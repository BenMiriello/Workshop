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

import json
import logging
from typing import Any

from services import chat_store, inbox, jobs, knowledge, scope_tree, tasks

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
_QUEUELESS_PREFIXES = ("app:",)


def task_scope_for(context_key: str | None) -> str | None:
    """The task-store scope a chat files into, or None when it has none.

    The SCOPE comes from `scope_tree.scope_for_context` like every other scope
    question in the system; the only thing decided here is which scopes a chat
    gets a queue for. It used to return the raw `context_key`, which meant
    `build:<slug>` came out as `build:<slug>` -- a string `scope_tree.parse`
    rejects and `reach()` can never yield -- and an unknown key came out as
    itself rather than resolving to the root.

    Mirrors `taskScopeFor` in `frontend/src/components/chat/chatScope.ts`.
    """
    scope = scope_tree.scope_for_context(context_key)
    if scope in _QUEUELESS_SCOPES or scope.startswith(_QUEUELESS_PREFIXES):
        return None
    return scope


def _evidence_dict(ev: Any) -> dict | None:
    """A job's `evidence` as a dict, whichever way it was written.

    Runs are closed by shell workers through the stdlib-only `services.jobs` CLI
    shim, and some of them pass the receipt as a JSON STRING rather than as an
    object. The frontend already has to cope with both, so this does too; a
    freeform string that is not JSON is simply not a receipt.
    """
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except (ValueError, TypeError):
            return None
    return ev if isinstance(ev, dict) else None


def _run_summary(job: dict, names: dict[str, str]) -> dict:
    """The latest run of a ticket, in the fields a job record HAS.

    `files` and `commits` are COUNTS, and they are null unless the evidence is
    the structured receipt `project_ralph.sh` writes, where both are lists. Null
    rather than zero, and always present rather than sometimes absent: zero would
    claim the receipt said nothing changed, and a key that comes and goes makes
    every reader test for two spellings of the same absence.
    """
    from routes.jobs import _display_agent

    ev = _evidence_dict(job.get("evidence"))
    receipt = ev if ev and ev.get("kind") == "receipt" else {}
    files, commits = receipt.get("files"), receipt.get("commits")
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

    # The same producers, in the same order, that `GET /inbox` runs before an
    # open read. Called rather than copied: two surfaces reconciling differently
    # is how a badge and a panel start counting different things.
    inbox.reconcile()
    items = inbox.list_items(
        chat_scope=knowledge.scope_for_context(context_key), chat_id=thread_id)

    from routes.tasks_all import _with_latitude, _with_triage, scope_roots

    by_scope: dict[str, dict[str, dict]] = {}
    index: dict[str, tuple[dict, str]] = {}
    filed_raw: list[tuple[dict, str]] = []
    scope_raw: list[tuple[dict, str]] = []
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
