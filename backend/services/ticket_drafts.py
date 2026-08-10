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

Return STRICT JSON, no prose, no code fence:

{"tickets": [
  {
    "title": "short imperative line, under 80 characters",
    "objective": "the concrete outcome, one sentence",
    "output": "what done looks like: the artifact, or how it would be judged",
    "entry_points": "the files, commands or symbols to read or run first",
    "boundaries": "what NOT to touch and what not to build; \"none\" if genuinely unbounded",
    "confidence": "decided" | "likely" | "speculative"
  }
]}

These four fields are what a worker needs and cannot get anywhere else, since it
never sees this conversation. Fill in every one you can support from what was
actually said. Do NOT invent a plausible-sounding file path or acceptance test:
if the conversation did not establish it, write what IS known and leave the rest
short. A ticket that admits it does not know where to start is useful; one that
points confidently at a file nobody mentioned costs a wasted run.

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


async def propose(thread_id: str) -> dict:
    """Draft tickets for a thread and store them on it.

    Stored, not returned-and-forgotten. Deriving costs a model call and tens of
    seconds; an operator who opens the dialog, reads the drafts, closes it and
    comes back must get their drafts (and any edits) rather than paying again.
    """
    import asyncio

    t = chat_store.get_thread(thread_id)
    if t is None:
        raise KeyError(thread_id)
    convo = _conversation_text(t)
    if not convo.strip():
        return {"drafts": [], "generated_at": None, "message_count": 0}

    prompt = PROMPT.replace("{convo}", convo)
    try:
        res = await asyncio.to_thread(
            llm_router.call_cloud, prompt, model=llm_router.TIER_HAIKU)
        drafts = _parse(res.get("text") or "")
    except Exception as e:
        log.warning("ticket draft failed for %s: %s", thread_id, e)
        raise

    payload = {
        "drafts": drafts,
        "generated_at": _now(),
        # The transcript length the drafts were derived from. The UI uses it to
        # say "5 messages since these were drafted" rather than making the
        # operator guess whether they are stale.
        "message_count": len(t.get("messages") or []),
    }
    _store(thread_id, payload)
    return payload


def get(thread_id: str) -> dict:
    """Stored drafts for a thread, with staleness measured against the live
    transcript. Never regenerates; that is always an explicit act."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise KeyError(thread_id)
    payload = dict(t.get("ticket_drafts") or {})
    payload.setdefault("drafts", [])
    payload.setdefault("generated_at", None)
    payload.setdefault("message_count", 0)
    payload["messages_since"] = max(
        0, len(t.get("messages") or []) - int(payload.get("message_count") or 0))
    return payload


def update_draft(thread_id: str, draft_id: str, patch: dict) -> dict:
    """Edit a stored draft in place (title / body / acceptance), or mark it
    filed. Edits persist for the same reason the drafts do."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise KeyError(thread_id)
    payload = dict(t.get("ticket_drafts") or {"drafts": []})
    for d in payload.get("drafts", []):
        if d.get("id") == draft_id:
            for k in ("title", "body", "acceptance", "filed_task_id"):
                if k in patch:
                    d[k] = patch[k]
            break
    _store(thread_id, payload)
    return payload


def discard(thread_id: str, draft_id: str) -> dict:
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise KeyError(thread_id)
    payload = dict(t.get("ticket_drafts") or {"drafts": []})
    payload["drafts"] = [d for d in payload.get("drafts", []) if d.get("id") != draft_id]
    _store(thread_id, payload)
    return payload


def _store(thread_id: str, payload: dict) -> None:
    t = chat_store.get_thread(thread_id)
    if t is None:
        return
    t["ticket_drafts"] = payload
    chat_store.save_thread(t)
