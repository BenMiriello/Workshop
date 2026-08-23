"""Proposing tickets from a conversation, and remembering the proposals.

Replaces "squash the last 12 messages into one paragraph". A conversation does
not contain one ticket. It contains some number of them — often zero — mixed in
with things that were merely discussed, ruled out, or are still open. Collapsing
all of that into a single blob produced tickets with no scope, no acceptance,
and no way to tell a decision from a musing.

Two jobs here:

  1. **Propose**: read the conversation and return 0..N drafts, each of which
     must stand on its own.
  2. **Remember**: keep the drafts on the thread. Deriving them costs a model
     call and 30+ seconds, so closing the dialog and reopening it must not
     silently redo the work or lose edits.

SELF-CONTAINEDNESS IS THE HARD RULE. A worker sees the ticket, not the chat, and
so does a future reader with none of today's context. A draft that says "finish
P7" or "apply section 4.2" is worthless the moment it leaves the room it was
written in. References are fine; unexplained references are not.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from services import chat_store, llm_router, ticket_format

log = logging.getLogger(__name__)

# How much conversation to read. Enough to cover a working session, bounded so
# the call stays cheap and fast.
_WINDOW = 24
_MAX_DRAFTS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


PROMPT = """You are reading a conversation between an operator and their assistant, and
proposing the work items (tickets) it implies.

Propose ONLY work that was actually decided or clearly needed. This is the most
important instruction: a conversation often contains zero tickets. Ideas that
were floated and dropped, things already finished during the conversation,
questions that were answered, and topics still under discussion are NOT tickets.
If nothing was decided, return an empty list. Returning nothing is a correct and
common answer.

If several distinct pieces of work were decided, return several tickets. Split
by deliverable, not by paragraph. Do not invent scope to pad the list.

EVERY ticket must stand completely on its own. The person or agent who reads it
will NOT have this conversation, will not know today's shorthand, and may be
reading it months from now. Therefore:
  - Never use internal shorthand, phase labels, section numbers or identifiers
    that only mean something inside this conversation ("P7", "section 4.2",
    "the D5 rule", "as discussed above"). If the work came from such a thing,
    NAME the underlying thing in plain words instead.
  - You may reference a file, document or URL, but say what it is and what the
    reader is meant to find there, so the reference is a pointer and not a
    prerequisite.
  - State the goal so that someone who has never seen the conversation could
    act on it.
  - The TITLE is one imperative line under 80 characters saying what changes and
    where, in ordinary words. It has to be skimmable in a list without being
    opened: if the body is needed to know what the ticket is about, the title has
    failed. No metaphors for code structure ("seam", "spine", "surface") unless
    that is literally the name of the thing.

Return STRICT JSON, no prose, no code fence:

{"tickets": [
  {
    "title": "short imperative line, under 80 characters",
    "objective": "the concrete outcome, one sentence",
    "output": "what done looks like: the artifact, or how it would be judged",
    "entry_points": "the files, commands or symbols to read or run first",
    "boundaries": "what NOT to touch and what not to build",
    "confidence": "decided" | "likely" | "speculative"
  }
]}

`output` AND `boundaries` ARE REQUIRED. Never return either one empty. They are
the two fields tickets most often omit and they are the two a worker cannot
proceed without, because a worker sees the title and the body and NOTHING ELSE:
not this conversation, not the operator, not you.

  - `output` is how anyone tells the work is finished. Without it the worker
    picks its own finish line, and the run is judged against a different one.
    Write the observable thing: "the endpoint returns 200 with the new field and
    a test covers it", "the page renders the list on a cold load". Not "it
    works", not "the bug is fixed".
  - `boundaries` is what to leave alone. Without it a one-file change grows into
    a refactor nobody asked for, which is the single most expensive way a run
    goes wrong. Name the files, directories or systems that are off limits, and
    say what NOT to build. If the work genuinely has no boundary, write exactly
    "none: this may touch anything the objective requires". A stated "none" is a
    decision; an empty field is an omission.

`entry_points` is nearly as important, and the honest answer counts: "the
conversation named no files; start by grepping for <term>" is a real entry point
and is better than silence.

Do NOT invent a plausible-sounding file path or acceptance test. If the
conversation did not establish something, say what IS known and say plainly what
was not settled. A ticket that admits it does not know where to start is useful;
one that points confidently at a file nobody mentioned costs a wasted run. That
licence applies to how MUCH you can say in a field, never to leaving `output` or
`boundaries` blank.

`confidence` is your read of the conversation:
  decided      the operator explicitly asked for this or agreed to it
  likely       strongly implied by what was decided, but never stated outright
  speculative  it came up and might be wanted; the operator should judge

=== CONVERSATION ===
{convo}
=== END CONVERSATION ===

JSON:"""


def _conversation_text(thread: dict) -> str:
    out: list[str] = []
    for m in (thread.get("messages") or [])[-_WINDOW:]:
        body = (m.get("content") or "").strip()
        if not body:
            continue
        who = "Operator" if m.get("role") == "user" else (
            (m.get("meta") or {}).get("agent") or "Assistant")
        out.append(f"{who}: {body[:2500]}")
    return "\n\n".join(out)


def _parse(raw: str) -> list[dict]:
    """Pull the ticket list out of a model reply.

    Tolerant by design: a fenced block or a stray sentence around the JSON is a
    formatting slip, not a reason to lose the whole proposal and make the
    operator wait another 30 seconds.
    """
    text = (raw or "").strip()
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        brace = text.find("{")
        if brace < 0:
            return []
        text = text[brace:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    tickets = data.get("tickets") if isinstance(data, dict) else None
    if not isinstance(tickets, list):
        return []
    out: list[dict] = []
    for t in tickets[:_MAX_DRAFTS]:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip().replace("—", ", ")
        if not title:
            continue
        def field(name: str, limit: int = 600) -> str:
            return str(t.get(name) or "").strip().replace("—", ", ")[:limit]

        # The body is COMPOSED from the four fields through the one shared
        # formatter, so what the model returns and what `ticket_readiness` looks
        # for cannot drift. A model that ignored the field names and wrote prose
        # into `body` is not discarded: it rides along under the composed part.
        body = ticket_format.compose(
            objective=field("objective"),
            output=field("output"),
            entry_points=field("entry_points"),
            boundaries=field("boundaries"),
            extra=field("body", 1500),
        )
        out.append({
            "id": uuid.uuid4().hex[:12],
            "title": title[:140],
            "body": body[:2400],
            # Kept as its own field for the dialog's editor, and because a draft
            # filed before this change still carries one.
            "acceptance": field("output") or field("acceptance"),
            "confidence": (t.get("confidence") if t.get("confidence") in
                           ("decided", "likely", "speculative") else "likely"),
            "filed_task_id": None,
        })
    return out


async def propose(thread_id: str, root, scope: str) -> dict:
    """Scan the conversation and file each proposal as a DRAFT TICKET.

    A proposal used to be a thread-local record in its own little store, which
    made "draft" two different things: a row on a chat, and a `draft` ticket. One
    concept, one place — a proposal IS a draft ticket now, which means it gets
    three things the thread-local version never had:

      - dedup. `task_dedup` folds a re-scan into the existing ticket instead of
        proposing the same work twice, and links near-matches as related.
      - the readiness checklist, because it is a real ticket the checker can read.
      - visibility. It shows up in Work with everything else waiting on the
        operator, instead of only inside a dialog nobody has open.

    The thread keeps SCAN state only (when it was last scanned, and how long the
    transcript was then), so the dialog can still say "5 messages since these were
    drafted". That is bookkeeping about the scan, not a second copy of the work.
    """
    import asyncio

    from services import task_dedup

    t = chat_store.get_thread(thread_id)
    if t is None:
        raise KeyError(thread_id)
    convo = _conversation_text(t)
    if not convo.strip():
        return {"drafts": [], "generated_at": None, "message_count": 0,
                "messages_since": 0, "scope": scope}

    prompt = PROMPT.replace("{convo}", convo)
    try:
        res = await asyncio.to_thread(
            llm_router.call_cloud, prompt, model=llm_router.TIER_HAIKU)
        proposals = _parse(res.get("text") or "")
    except Exception as e:
        log.warning("ticket draft failed for %s: %s", thread_id, e)
        raise

    filed: list[dict] = []
    for prop in proposals:
        text = f"{prop['title']}\n{prop['body']}".strip()
        labels = ["proposed"]
        # `confidence` is the model's read of the conversation, and it is the one
        # thing the operator most wants to see before approving. It rides as a
        # label rather than a new schema field: labels are first-class, already
        # render, and already sync to a tracker.
        if prop.get("confidence") and prop["confidence"] != "decided":
            labels.append(f"confidence:{prop['confidence']}")
        try:
            out = task_dedup.dedup_add(
                root, text=text, labels=labels, status="draft",
                effort=prop.get("effort") or None,
                source={"kind": "chat", "ref": thread_id},
                scoped_by="agent:chat",
            )
        except Exception as e:  # one bad proposal must not lose the others
            log.warning("filing proposal failed for %s: %s", thread_id, e)
            continue
        task = out.get("task")
        if task:
            filed.append({**task, "scope": scope, "dedup": out.get("decision")})

    payload = {
        "generated_at": _now(),
        # The transcript length the proposals were derived from. The UI uses it to
        # say "5 messages since these were drafted" rather than making the
        # operator guess whether they are stale.
        "message_count": len(t.get("messages") or []),
    }
    _store(thread_id, payload)
    return {**payload, "drafts": filed, "messages_since": 0, "scope": scope}


def get(thread_id: str, root, scope: str) -> dict:
    """This thread's open draft tickets, with staleness against the transcript.

    Never scans; that is always an explicit act, because it costs a model call and
    tens of seconds. Reads the TICKET store rather than a thread-local copy, so a
    draft edited or approved anywhere else is reflected here with no syncing.
    """
    from services import tasks

    t = chat_store.get_thread(thread_id)
    if t is None:
        raise KeyError(thread_id)
    scan = dict(t.get("ticket_drafts") or {})
    drafts = [
        {**task, "scope": scope}
        for task in tasks.list_tasks(root, include_done=False)
        if task.get("status") == "draft"
        and (task.get("source") or {}).get("ref") == thread_id
    ]
    drafts.sort(key=lambda d: d.get("created") or "")
    return {
        "drafts": drafts,
        "scope": scope,
        "generated_at": scan.get("generated_at"),
        "message_count": int(scan.get("message_count") or 0),
        "messages_since": max(
            0, len(t.get("messages") or []) - int(scan.get("message_count") or 0)),
    }


def _store(thread_id: str, payload: dict) -> None:
    """Persist SCAN state on the thread. Deliberately not the drafts themselves:
    the tickets are the drafts, and a second copy here is what this refactor
    removed."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        return
    t["ticket_drafts"] = {"generated_at": payload.get("generated_at"),
                          "message_count": payload.get("message_count")}
    chat_store.save_thread(t)
