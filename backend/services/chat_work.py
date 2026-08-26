"""Everything a chat's work surfaces need, assembled once.

The chat has three things that show work: the Tickets badge, the Work panel, and
the ticket card sitting inside an answer. Before this they polled separately —
`/inbox` on one interval, `/tasks` on another, and a card would have added two
more per ticket — so the badge, a panel row and a card could all be looking at a
different copy of the same ticket and say different things about it.

This is the one read behind all three. Not an optimisation: it is what makes
"one canonical status" a property of the system rather than a rule everybody has
to remember. There is one copy of a ticket in the payload, so the three surfaces
cannot disagree about it.

What is deliberately NOT here:

  A run's latest tool call. It does not exist. Tool events belong to chat turns;
  a run has no step data and no SSE, so the run summary carries the fields a job
  record actually has and nothing invented on top of them.

  `job.chat_id`. It is declared and never written (0 of 2761 records measured
  2026-08-15). A run reaches its chat through its TICKET's `source`, which is why
  `filed` matches on `source.ref` and not on the run.
"""
from __future__ import annotations

import logging
from typing import Any

from services import chat_store, inbox, jobs, knowledge, scope_tree, task_scope, tasks

log = logging.getLogger(__name__)

# The panel is a popover, not a list page: it shows the head of the scope's
# active work and links to the board for the rest.
SCOPE_CAP = 8

# "Active" in the sense the board's Active view means: not backlog, not finished.
ACTIVE_STATE_TYPES = ("unstarted", "started")


# The scope KINDS with no task queue of their own. The workshop root has one
# (`shared/queue`) but a chat is not put in front of it just for being unbound:
# showing a general chat the root backlog would make every chat in the system
# look like it owned the same work. An app dock has no queue at all.
_QUEUELESS_SCOPES = ("workshop",)

# `("app:",)` used to blanket EVERY app scope here, unconditionally -- the
# state app-is-the-entity-2026-08-20.md's sec 2.2 measured
# (`chat_work._QUEUELESS_PREFIXES = ("app:",)`) and asked to be reconsidered.
# It is gone as a prefix rule, on purpose: an app can own tickets now
# (stage A/B), exactly when it owns a working tree. Whether THIS app does is
# decided per-call, below, by asking `task_scope.root_for_context` the same
# question every other scope answers already -- not by a second copy of the
# app<->project link rule. An installed-only app with no project (finance,
# health, wearables at design time) still gets no queue, because it still has
# no task-store root; that is the one case the old prefix rule was right
# about, and it now falls out of the resolution instead of being asserted.
#
# NOTE for the frontend: `taskScopeFor` in
# `frontend/src/components/chat/chatScope.ts` still blankets `app:` and has
# not been updated here (frontend is out of scope for this change) -- it
# needs the same fix or an app chat's Tickets badge will keep hiding itself
# for a linked app.


def task_scope_for(context_key: str | None) -> str | None:
    """The task-store scope a chat files into, or None when it has none.

    The SCOPE comes from `scope_tree.scope_for_context` like every other scope
    question in the system; the only thing decided here is which scopes a chat
    gets a queue for. It used to return the raw `context_key`, so an unknown key
    came out as itself rather than resolving to the root, and every key kind
    that was not already a scope kind came out as a string `scope_tree.parse`
    rejects and `reach()` can never yield.

    Mirrors `taskScopeFor` in `frontend/src/components/chat/chatScope.ts`.
    """
    scope = scope_tree.scope_for_context(context_key)
    if scope in _QUEUELESS_SCOPES:
        return None
    if scope.startswith("app:") and task_scope.root_for_context(scope) is None:
        return None
    return scope


def _run_summary(job: dict, names: dict[str, str]) -> dict:
    """The latest run of a ticket, in the fields a job record HAS.

    `files` and `commits` are COUNTS, and they are null unless the evidence is
    the structured receipt `project_ralph.sh` writes, where both are lists. Null
    rather than zero, and always present rather than sometimes absent: zero would
    claim the receipt said nothing changed, and a key that comes and goes makes
    every reader test for two spellings of the same absence.
    """
    from routes.jobs import _display_agent

    ev = job.get("evidence")
    receipt = ev if isinstance(ev, dict) and ev.get("kind") == "receipt" else {}
    files, commits = receipt.get("files"), receipt.get("commits")
    # WHY IT STOPPED, which the card had no way to say. A run refused by a guard
    # closes `cancelled` with nothing else on it, so from the chat it was
    # indistinguishable from a crash (measured 2026-08-23: the operator pressed
    # Run twice, got `cancelled 0s` twice, and the reason was only ever in
    # `.runtime/queue-runner.log`). `halted` is the sentence; `refusal` is what
    # makes it a refusal rather than a stop the sweep chose.
    halted = str((ev or {}).get("halted") or "").strip() if isinstance(ev, dict) else ""
    refusal = (ev or {}).get("refusal") if isinstance(ev, dict) else None
    out: dict[str, Any] = {
        "id": job.get("id"),
        "status": job.get("status"),
        "started_at": job.get("started_at"),
        "ended_at": job.get("ended_at"),
        "agent_name": _display_agent(job.get("agent_id"), names),
        "trigger": job.get("trigger"),
        "exit_code": job.get("exit_code"),
        "files": len(files) if isinstance(files, list) else None,
        "commits": len(commits) if isinstance(commits, list) else None,
        "halted": halted or None,
        "refusal": refusal if isinstance(refusal, dict) else None,
    }
    return out


def _latest_run(task_id: str, names: dict[str, str]) -> dict | None:
    """The most recently started run of one ticket, or None if it never ran.

    Filtered by `task_id` BEFORE the limit is applied, so a ticket whose only run
    is old is still found in a log that holds thousands of newer ones.

    PER TICKET ON PURPOSE, and an attempt to index it in one pass was reverted on
    2026-08-16. `jobs.read` copies and sorts the whole log per call, so indexing
    looks like an easy win, but the only way to get every ticket's runs from one
    call is to drop the `task_id` filter, and then `limit` cuts the log by
    recency before this ticket is looked for. That is exactly the bug the
    paragraph above describes: a ticket whose only run is old silently reads as
    never run. Reaching past `read` into the private `_read_all` avoids that and
    breaks the seam the tests stub. The scan is cached and the endpoint measured
    50ms for 11 tickets; revisit only with a real index on the job store.
    """
    try:
        found = jobs.read(task_id=task_id, limit=1)
    except Exception as e:
        log.warning("chat_work: cannot read runs for %s: %s", task_id, e)
        return None
    return _run_summary(found[0], names) if found else None


# ---------------------------------------------------------------------------
# "Needs you", read off the tickets themselves
# ---------------------------------------------------------------------------
#
# A ticket's STATUS is the fact, and there is no second copy of it anywhere
# (docs/platform-redesign/one-fact-one-place-2026-08-22.md §3.1):
#
#     in_review   finished, needs a decision   -> accept | send back
#     blocked     stuck, needs an answer       -> answer (+ grant / rule / cancel)
#
# Everything that wants the operator is one of those two, so the panel, the badge
# and the inline block all read tickets filtered by status. `services/run_inbox`
# used to COPY that status into an inbox row, which went stale the moment a
# ticket moved, which is why the inbox had to be rebuilt on every read to be
# believable. Deriving it here means there is nothing to keep in step.
#
# The ROW SHAPE stays the inbox item's, and that is deliberate rather than
# lazy: the panel, the question view and the inline decision block all read one
# list of "things waiting on you", and an ambient agent's notice is still one of
# those. Two shapes would mean two renderers for one column.

NEEDS_YOU_STATUSES = ("in_review", "blocked")

_ROW_PREFIX = "task:"


def needs_you_id(scope: str, task_id: str) -> str:
    """The row id for a waiting ticket. Derived, stable, and the only handle the
    client needs: `POST /inbox/{id}/resolve` reads the ticket straight back out
    of it, so answering never depends on a stored row existing."""
    return f"{_ROW_PREFIX}{scope}:{task_id}"


def parse_needs_you_id(item_id: str) -> tuple[str, str] | None:
    """`(scope, task_id)` for a derived row id, or None for a stored item.

    Split from the RIGHT: a scope key carries its own colon (`project:demo`,
    `agent:ralph`) and a ticket id never does.
    """
    if not str(item_id or "").startswith(_ROW_PREFIX):
        return None
    head, _, task_id = str(item_id).rpartition(":")
    scope = head[len(_ROW_PREFIX):]
    if not scope or not task_id:
        return None
    return scope, task_id


def needs_you_options(task: dict) -> list[str]:
    """The taps this ticket offers, named for what they do.

    `Approve`/`Decline` is reserved for a block carrying a structured proposal,
    because that is the pair `routes/inbox` mints a grant from. A finished ticket
    gets its own pair so the two are never confused: the words on the buttons are
    the verbs in `services/task_decision`.
    """
    if (task.get("awaiting") or {}).get("proposal"):
        return ["Approve", "Decline"]
    if task.get("status") == "in_review":
        return ["Accept", "Send back"]
    return []


# The sentence the sweep used to stamp onto EVERY finished ticket. It is dropped
# on read as well as no longer written (`project_ralph.sh question_for`), because
# the store already holds it on tickets that were finished before the writer was
# fixed, and a row is not worth a migration to look right. Matched on a distinctive
# prefix rather than the whole string so a reworded copy of the same non-fact does
# not slip back in unnoticed.
_BOILERPLATE_ASK = "the work on this ticket is finished and waiting on you"


def _is_boilerplate(ask: str) -> bool:
    return ask.strip().lower().startswith(_BOILERPLATE_ASK)


def needs_you_ask(task: dict) -> str:
    """What this ticket is ASKING, when it has something to say beyond its state.

    EMPTY IS THE NORMAL CASE FOR A FINISHED TICKET, on purpose. "The work is
    finished and waiting on you" is the definition of `in_review`, so every row
    would carry the identical sentence, and a sentence every instance repeats is
    not information (one-fact-one-place §3.3). With nothing here the row shows
    the ticket's TITLE, which is the part that differs.

    `blocked_reason` only counts on a ticket that is actually blocked: it is not
    cleared eagerly, so reading it on a finished one would quote a block that has
    already been dealt with.
    """
    aw = task.get("awaiting") or {}
    ask = str(aw.get("ask") or aw.get("question") or "").strip()
    if ask and not _is_boilerplate(ask):
        return ask
    if task.get("status") == "blocked":
        return str(task.get("blocked_reason") or "").strip()
    return ""


def _who_did_the_work(task: dict, run: dict | None, scope: str,
                      names: dict[str, str]) -> str:
    """WHOSE work this row is about: the agent of the run that produced the
    state, which is who the operator would be answering.

    NOT the assignee-or-owner the deleted `run_inbox` used. `tasks` defaults
    `owner` to `operator` for any ticket with no delegate, so an operator-filed
    ticket rendered as "operator asks" behind an `O` avatar: the surface telling
    the operator he was asking himself. The run knows who actually did it.
    """
    name = (run or {}).get("agent_name")
    if name:
        return str(name)
    assignee = str(task.get("assignee") or "").strip()
    if assignee and assignee != tasks.DEFAULT_OWNER:
        from routes.jobs import _display_agent
        return _display_agent(assignee, names) or assignee
    # A ticket worked by the unattended sweep has a run but NO agent identity:
    # nothing delegated it, the queue picked it up. Its trigger is the true
    # answer to "who did this", and it is the one the run feed already shows.
    from routes.jobs import _TRIGGER_LABELS
    trigger = str((run or {}).get("trigger") or "")
    if trigger:
        return f"{_TRIGGER_LABELS.get(trigger, trigger.capitalize())} run"
    # NOT the scope. "development asks" is the same non-fact as the "operator
    # asks" this function was written to remove, one word further out: it names
    # the queue the ticket sits in, not anybody who did anything. Empty, and the
    # row leads with its TITLE instead, which is the part that differs.
    return ""


def is_waiting(task: dict) -> bool:
    """Does this ticket want the operator.

    THE definition, and the only one. A block the operator DISMISSED in review
    stays a block; it just stops asking. That is the one thing that takes a
    waiting ticket out of this column without changing its status.

    Read by both scans: `thread_work` (one chat's column) and `needs_you` (the
    whole instance). A predicate rather than a line inside each loop, because
    two copies of this is how a badge and a list start disagreeing about what
    is waiting.
    """
    return (task.get("status") in NEEDS_YOU_STATUSES
            and not (task.get("awaiting") or {}).get("dismissed"))


def _needs_you_row(task: dict, scope: str, names: dict[str, str]) -> dict:
    """One waiting ticket as a row of the "needs you" column."""
    run = _latest_run(task["id"], names)
    aw = task.get("awaiting") or {}
    return {
        "id": needs_you_id(scope, task["id"]),
        "kind": "question",
        "state": "open",
        "agent": _who_did_the_work(task, run, scope, names),
        "scope": scope,
        "title": str(task.get("title") or task.get("text") or "").split("\n")[0][:120],
        "body": needs_you_ask(task),
        "options": needs_you_options(task),
        # Left null on purpose. Delivery is by SCOPE, exactly as it was when
        # these rows were stored: naming the originating chat here would pull a
        # row out of every sibling chat in the same scope that can see the
        # ticket today. Provenance decides the INLINE block, not the panel.
        "chat_id": None,
        "ticket_id": task["id"],
        "run_id": (run or {}).get("id"),
        "created_at": str(aw.get("since") or task.get("updated") or ""),
        "resolved_at": None,
        "answer": None,
        "snoozed_until": None,
    }


def needs_you(limit: int = 50) -> dict:
    """What wants the operator, across the whole instance.

    The same column `thread_work` builds for one chat, without the chat: every
    scope's waiting tickets plus whatever an ambient agent raised, merged and
    ordered by the same rule. The dashboard's "Needs you" card and the per-chat
    panel therefore cannot disagree about what is waiting or about which
    question is first.

    NOT A SECOND SCAN OF A DIFFERENT THING. `is_waiting` is the definition,
    `_needs_you_row` builds the row, `inbox.sort_items` orders it and the
    ticket-shadow rule below is the same one the chat applies: a stored row that
    NAMES a ticket does not surface, because the ticket is the fact and already
    has a derived row. `run_inbox` was deleted for being the second answer to
    one question; this must not become the third.

    `counts` is per kind, the same shape `inbox.counts()` returns, so a badge
    can say WHAT is waiting and not only how much.
    """
    from routes.jobs import _name_map
    from routes.tasks_all import scope_roots

    names = _name_map()
    rows: list[dict] = []
    for skey, root in scope_roots():
        try:
            recs = tasks.list_tasks(root)
        except Exception as e:  # one unreadable store must not empty the column
            log.warning("needs_you: cannot read tasks for %s: %s", skey, e)
            continue
        for r in recs:
            if is_waiting(r):
                rows.append(_needs_you_row(r, skey, names))

    stored = [row for row in inbox.list_items(limit=limit)
              if not row.get("ticket_id")]
    items = inbox.sort_items(rows + stored)[:limit]

    counts = {k: 0 for k in inbox.KINDS}
    for it in items:
        kind = it.get("kind", "notify")
        counts[kind] = counts.get(kind, 0) + 1
    return {"items": items, "counts": counts, "total": len(items)}


def thread_work(thread_id: str) -> dict | None:
    """The whole work picture for one chat. None when the thread does not exist.

    The ticket scan reads every scope's store. That is what a cross-scope
    `filed` requires (a chat files wherever it was scoped at the time, and can be
    rescoped afterwards), and `tasks.list_tasks` short-circuits its reconcile on
    an unchanged export hash, so a sweep across ~109 tickets does no work when
    nothing moved. No index and no new query parameter on `GET /tasks`: this is
    one caller with one shape, and a filter on the shared list route would be a
    second place for the chat edge to be defined.
    """
    t = chat_store.get_thread(thread_id)
    if t is None:
        return None
    context_key = t.get("context_key") or "general"
    scope_key = task_scope_for(context_key)

    chat_scope = knowledge.scope_for_context(context_key)

    from routes.tasks_all import _with_latitude, _with_triage, scope_roots

    by_scope: dict[str, dict[str, dict]] = {}
    index: dict[str, tuple[dict, str]] = {}
    filed_raw: list[tuple[dict, str]] = []
    scope_raw: list[tuple[dict, str]] = []
    waiting_raw: list[tuple[dict, str]] = []
    active_in_scope = 0

    for skey, root in scope_roots():
        try:
            rows = tasks.list_tasks(root)
        except Exception as e:  # one unreadable store must not empty the panel
            log.warning("chat_work: cannot read tasks for %s: %s", skey, e)
            continue
        by_scope[skey] = {r["id"]: r for r in rows}
        for r in rows:
            index.setdefault(r["id"], (r, skey))
        for r in rows:
            src = r.get("source") or {}
            filed_here = (src.get("kind") == "chat"
                          and str(src.get("ref") or "") == thread_id)
            if filed_here:
                filed_raw.append((r, skey))
            if skey == scope_key and \
                    tasks.state_type(r.get("status")) in ACTIVE_STATE_TYPES:
                active_in_scope += 1
                if not filed_here:
                    scope_raw.append((r, skey))
            if is_waiting(r):
                waiting_raw.append((r, skey))

    lat_cache: dict[str, str | None] = {}

    def project(rec: dict, skey: str) -> dict:
        out = dict(rec)
        out["scope"] = skey
        _with_latitude(out, skey, lat_cache)
        _with_triage(out, by_scope.get(skey, {}), index)
        return out

    # Newest first: a card is opened from the answer that filed it, and the panel
    # reads top-down, so the ticket this conversation just produced is the one at
    # the top.
    filed = tasks.sort_tasks([project(r, s) for r, s in filed_raw], "created")
    # Most recently touched first, matching the list route's default order, then
    # capped. The panel links to the board for anything past the cap.
    scope_rows = tasks.sort_tasks(
        [project(r, s) for r, s in scope_raw], "updated")[:SCOPE_CAP]

    from routes.jobs import _name_map
    names = _name_map()
    for rec in (*filed, *scope_rows):
        rec["run"] = _latest_run(rec["id"], names)

    # WHAT WANTS THE OPERATOR: the waiting tickets, plus whatever an ambient
    # agent raised that has no ticket behind it. Both go through
    # `surfaces_in_chat` unchanged, which is the rule that keeps this column
    # readable, and through one sort, so the two cannot interleave differently
    # in the panel and in the card that links to a row.
    # A STORED ROW THAT NAMES A TICKET DOES NOT SURFACE. The ticket is the fact
    # and it already has a derived row above; a stored copy is the second answer
    # to one question that `run_inbox` was deleted for being.
    #
    # This is what the paragraph above always claimed and did not enforce.
    # Deleting the PRODUCER left every row it had already written open in the
    # store, so nine tickets appeared TWICE in the panel: once derived, once
    # legacy. Measured on the live store 2026-08-22: 20 rows for 11 waiting
    # tickets, every duplicate a leftover uuid row beside its `task:` twin.
    stored = [row for row in inbox.list_items(chat_scope=chat_scope,
                                              chat_id=thread_id)
              if not row.get("ticket_id")]
    items = inbox.sort_items(
        [row for row in (_needs_you_row(r, s, names) for r, s in waiting_raw)
         if inbox.surfaces_in_chat(row, chat_scope, thread_id)]
        + stored)

    running = sum(1 for rec in (*filed, *scope_rows)
                  if (rec.get("run") or {}).get("status") == "running")
    return {
        "inbox": items,
        "filed": filed,
        "scope": scope_rows,
        "counts": {
            # The BADGE. An open-ticket count is ambient information wearing an
            # alarm's clothing, so only what actually wants the operator counts
            # here; the other three are chip counts.
            "needs_you": len(items),
            "running": running,
            "this_chat": len(filed),
            # Every active ticket in the scope, uncapped and including the ones
            # this chat filed: it is the size of the set the chip opens, not the
            # size of the page shown.
            "all": active_in_scope,
        },
        "scope_key": scope_key,
    }
