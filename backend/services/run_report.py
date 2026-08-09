"""When a run finishes, tell the chat that asked for it.

THE GAP THIS CLOSES. Work leaves a conversation as a ticket, a worker picks it
up somewhere else, and the conversation never hears back. The operator is left
holding the thread of what they asked for and having to go looking for whether
it happened — which is most of what "juggling chats" actually is.

WHY A SUMMARY AND NOT A TRANSCRIPT. The sub-agent pattern this system is an
instance of works precisely because the delegated context does NOT come back:
the worker burns its own window and returns a short report. So this posts a
condensed result (what ran, what it did, whether it passed), never the run's
output. If you want the detail it is one click away in the run.

WHY THE MODEL IS NOT TOLD DIRECTLY. Appending here puts the report in the
TRANSCRIPT, which is what the operator reads. The live CLI session does not see
it, and deliberately so: injecting into a running session mid-turn is how you
corrupt a conversation. The model finds out the honest way — the ticket write
bumped the scope counter (services/scope_version), so the next turn in that
scope is told shared work changed and can read it if it matters.

Owner resolution is a scan rather than a schema change. Threads live under
`.runtime/users/<owner>/chat/`, a background worker has no owner pinned, and
tickets filed before this existed carry no owner either. Globbing for the id
handles both without a migration, and costs one directory listing per report.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("workshop.run_report")

WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path.home() / "workshop")))
_RUNTIME = Path(os.environ.get("WORKSHOP_RUNTIME", str(WORKSHOP_ROOT / ".runtime")))

# A report is a courtesy, not a result. Nothing here may raise into the caller:
# a job that did its work must never be marked failed because telling someone
# about it went wrong.


def owner_of_thread(thread_id: str) -> int | None:
    """Which owner's store holds this thread, or None if no store has it.

    Public because the question path needs the same answer: a ticket that stopped
    to ask reaches its chat exactly the way a finished run does."""
    if not thread_id:
        return None
    try:
        for d in (_RUNTIME / "users").iterdir():
            if (d / "chat" / f"{thread_id}.json").is_file():
                try:
                    return int(d.name)
                except ValueError:
                    continue
    except OSError:
        return None
    return None


def _summarize(job: dict, tickets: list[dict], status: str) -> str:
    """The report body. Short by design — see the module docstring."""
    jid = job.get("id") or "?"
    verb = {"done": "finished", "failed": "failed", "cancelled": "was cancelled"}.get(status, status)
    lines = [f"Run `{jid}` {verb}."]

    done = [t for t in tickets if t.get("status") == "done"]
    other = [t for t in tickets if t.get("status") != "done"]
    for t in done[:5]:
        lines.append(f"  - done: {(t.get('text') or '').splitlines()[0][:90]}")
    for t in other[:5]:
        lines.append(f"  - {t.get('status') or 'open'}: {(t.get('text') or '').splitlines()[0][:90]}")
    extra = len(tickets) - len(done[:5]) - len(other[:5])
    if extra > 0:
        lines.append(f"  - (+{extra} more)")

    ev = job.get("evidence")
    if isinstance(ev, dict):
        gate = ev.get("reviewer") or ev.get("gate")
        if gate:
            lines.append(f"Reviewer gate: {gate}.")
    if status != "done":
        lines.append("Nothing here is verified.")
    return "\n".join(lines)


def post_for_job(job_id: str, status: str = "done") -> int:
    """Post one report into every chat that filed a ticket this job worked.

    Returns how many chats were told (0 is the normal case for a run nobody
    started from a conversation)."""
    try:
        from services import jobs, tasks, task_scope

        job = (jobs.get(job_id) if hasattr(jobs, "get") else None) or {}
        if not job:
            job = {"id": job_id}
        # root_for_job resolves a job BOUND to a project or an agent. A job with
        # neither works the workshop root queue, which is the common case for
        # anything filed from the workshop chat — so fall back to it here rather
        # than widening root_for_job, whose other callers rely on None meaning
        # "not bound".
        root = task_scope.root_for_job(job) or task_scope.WORKSHOP_ROOT
        # `job_ids` (plural) is the stored field: a ticket can be worked by more
        # than one run across retries, so membership is the test, not equality.
        worked = [t for t in tasks._load(root) if job_id in (t.get("job_ids") or [])]
        if not worked:
            return 0

        # Group by originating conversation: one report per chat, not per ticket.
        by_thread: dict[str, list[dict]] = {}
        for t in worked:
            src = t.get("source") or {}
            if src.get("kind") != "chat":
                continue
            ref = src.get("ref")
            if ref:
                by_thread.setdefault(str(ref), []).append(t)
        if not by_thread:
            return 0

        from services import chat_store, ownership

        posted = 0
        previous = ownership.owner_or_none()
        for thread_id, tickets in by_thread.items():
            owner = owner_of_thread(thread_id)
            if owner is None:
                continue
            try:
                ownership.set_current_owner(owner)
                chat_store.append_message(
                    thread_id, "assistant", _summarize(job, tickets, status),
                    meta={"kind": "run_report", "job": job_id, "status": status,
                          "tickets": [t.get("id") for t in tickets]},
                )
                posted += 1
            except Exception as e:
                log.debug("run report to thread %s failed: %s", thread_id, e)
            finally:
                if previous is None:
                    ownership.clear_current_owner()
                else:
                    ownership.set_current_owner(previous)
        return posted
    except Exception as e:
        log.debug("run report for job %s skipped: %s", job_id, e)
        return 0
