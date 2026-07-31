"""Blocked work becomes an inbox question.

A task carries `awaiting`: the general escalation primitive (services/tasks.py).
Whatever stopped the work — a permission, a missing fixture, an ambiguous
ticket, a contradiction — writes the same record. It has always been the shape
a run uses to ASK rather than fail, and until now nothing surfaced it: the
record sat in a task store and the operator had to go looking for it, on a page
that only shows one scope at a time.

The inbox is the surface built to receive exactly this (P4), so this is the
bridge between them.

WHY RECONCILE ON READ rather than raise at the moment of blocking. The producers
are shell sweeps (`project_ralph.sh`, `ralph.sh`) writing through the stdlib-only
`services.jobs` CLI shim, in a different process from the FastAPI app. Hooking
the write path would mean coupling that module to the inbox and breaking the
stdlib-only contract it documents. Reconciling is idempotent, works no matter
which process wrote the block, and self-heals if a raise is ever missed — a
"raise once at the transition" design gets permanently out of step the first
time anything writes a task store behind its back.

The dedupe key is `task:<scope>:<id>`, so re-running this is exactly-once while
an item is open.
"""
from __future__ import annotations

import logging
from pathlib import Path

from services import inbox, tasks

log = logging.getLogger(__name__)

# A blocked task is only worth the operator's attention in these states. `todo`
# with a stale `awaiting` is a record of a block that has already been cleared
# and re-queued, not a live question.
_ASKING_STATUSES = ("blocked", "in_review", "doing")


def _question_of(task: dict) -> str:
    aw = task.get("awaiting")
    if not isinstance(aw, dict):
        return ""
    return str(aw.get("ask") or aw.get("question") or "").strip()


def _title_of(task: dict) -> str:
    text = (task.get("text") or "").strip()
    return text.split("\n")[0][:120] or f"Task {task.get('id', '')[:8]}"


def sync(scope_roots: list[tuple[str, Path]]) -> dict:
    """Raise an inbox question for every blocked task, and close the ones whose
    task has since moved on. Returns a small tally for logging.

    Never raises into the caller: the inbox listing must not fail because one
    task store is unreadable.
    """
    raised = 0
    closed = 0
    live_keys: set[str] = set()
    # Snapshot what is already open, so "raised" counts genuinely new questions.
    # raise_item returns the EXISTING item when a dedupe key matches, so it
    # cannot itself tell us whether anything was created.
    open_items = inbox.list_items(state="open")
    known = {it.get("dedupe_key") for it in open_items if it.get("dedupe_key")}

    for skey, root in scope_roots:
        try:
            items = tasks.list_tasks(root, include_done=False)
        except Exception as e:  # a single bad store must not break the inbox
            log.warning("run_inbox: cannot read tasks for %s: %s", skey, e)
            continue
        for t in items:
            if t.get("status") not in _ASKING_STATUSES:
                continue
            ask = _question_of(t)
            if not ask:
                continue
            key = f"task:{skey}:{t.get('id')}"
            live_keys.add(key)
            aw = t.get("awaiting") or {}
            try:
                item = inbox.raise_item(
                    kind="question",
                    # The assignee is who is stuck. Falling back to the scope
                    # keeps the row attributable rather than blank.
                    agent=str(t.get("assignee") or t.get("owner") or skey),
                    scope=skey,
                    title=_title_of(t),
                    body=ask,
                    # A structured proposal (grant this uri/verb) is the
                    # machine-checkable half of the ask; offering it as options
                    # is what makes the answer one tap instead of prose.
                    options=["Approve", "Decline"] if aw.get("proposal") else [],
                    ticket_id=str(t.get("id") or "") or None,
                    run_id=(t.get("job_ids") or [None])[-1],
                    dedupe_key=key,
                )
                if key not in known and item.get("state") == "open":
                    raised += 1
                    known.add(key)
            except Exception as e:
                log.warning("run_inbox: cannot raise for %s: %s", key, e)

    # Close items whose task no longer asks. Without this the inbox accumulates
    # questions for work that was unblocked elsewhere (the Review tab, a worker
    # retry), which is the same "stops being read" failure dedupe exists to
    # prevent.
    for it in open_items:
        key = it.get("dedupe_key") or ""
        if key.startswith("task:") and key not in live_keys:
            inbox.resolve_item(it["id"], answer=None, dismissed=True)
            closed += 1

    return {"raised": raised, "closed": closed}
