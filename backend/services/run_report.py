"""When a run finishes, tell the chat that asked for it — and the tracker it came
from.

TWO AUDIENCES, ONE EVENT. A run ends once, and the two places that were left
holding the thread are the conversation that asked for the work
(`post_for_job`) and the external issue the ticket is linked to
(`post_to_tracker`). Both hang off the same terminal point in `services/jobs`
and both resolve "which tickets did this job work" the same way, which is why
they live together. Neither can fail the other: they are separate calls with
separate guards.

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
from services import paths

log = logging.getLogger("workshop.run_report")

WORKSHOP_ROOT = paths.workshop_root()
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
            # A thread is a directory of events (chat_log); the flat document is
            # the pre-2026-08-09 layout, still checked so an owner whose threads
            # have not been migrated yet is still found.
            if (d / "chat" / thread_id / "meta.json").is_file() \
                    or (d / "chat" / f"{thread_id}.json").is_file():
                try:
                    return int(d.name)
                except ValueError:
                    continue
    except OSError:
        return None
    return None


_VERB = {"done": "finished", "failed": "failed", "cancelled": "was cancelled"}


def _outcome_lines(job: dict, status: str) -> list[str]:
    """What the run's own evidence says about how it ended.

    Shared by the chat report and the tracker comment: a run that did not
    complete cleanly must not read like one that did, in either place.
    """
    lines: list[str] = []
    ev = job.get("evidence")
    if isinstance(ev, dict):
        gate = ev.get("reviewer") or ev.get("gate")
        if gate:
            lines.append(f"Reviewer gate: {gate}.")
        # A run that stopped before doing any work carries WHY it stopped. Say
        # it, or the report reads as an unexplained non-result.
        halted = ev.get("halted")
        if halted:
            lines.append(f"Stopped before working: {halted}.")
    elif isinstance(ev, str) and ev and status != "done":
        lines.append(ev if ev.endswith(".") else f"{ev}.")
    if status != "done":
        lines.append("Nothing here is verified.")
    return lines


def _summarize(job: dict, tickets: list[dict], status: str) -> str:
    """The report body. Short by design — see the module docstring."""
    jid = job.get("id") or "?"
    verb = _VERB.get(status, status)
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

    lines.extend(_outcome_lines(job, status))
    return "\n".join(lines)


def _run_comment(job: dict, task: dict, status: str) -> str:
    """The one comment a run posts on the linked issue (lifecycle doc §9.2:
    "one run-summary comment per run", not full comment sync).

    Written for the person reading the ISSUE, who does not have the run in front
    of them: what ran, where the ticket ended up, and no claim of success a
    failed run has not earned.
    """
    jid = job.get("id") or "?"
    verb = _VERB.get(status, status)
    parts = [f"Workshop run `{jid}` {verb}.",
             f"This ticket is now **{task.get('status') or 'todo'}**."]
    parts.extend(_outcome_lines(job, status))
    return "\n\n".join(parts)


def _worked_for_job(job_id: str) -> tuple[dict, Path, list[dict]]:
    """(job, task-store root, the tickets this run worked).

    One resolution for both audiences. `job_ids` (plural) is the stored field: a
    ticket can be worked by more than one run across retries, so membership is
    the test, not equality.
    """
    from services import jobs, tasks, task_scope

    job = (jobs.get(job_id) if hasattr(jobs, "get") else None) or {}
    if not job:
        job = {"id": job_id}
    # root_for_job resolves a job BOUND to a project or an agent. A job with
    # neither works a root queue, which is the common case for anything filed
    # from the workshop chat — so fall back to it here rather than widening
    # root_for_job, whose other callers rely on None meaning "not bound".
    #
    # There are two root queues since ADR 0014, and an unbound job can belong to
    # either: the workshop inbox is where such a job lands now, and the
    # development queue is where every one of them landed before the rename
    # (it is the same file, under its true name). So both are candidates, and
    # the one that actually holds this job's tickets wins.
    root = task_scope.root_for_job(job)
    candidates = [root] if root is not None else [
        c for c in (task_scope.root_for_context("workshop"),
                    task_scope.root_for_context("development"))
        if c is not None
    ] or [task_scope.WORKSHOP_SCOPE_DIR]
    root = candidates[0]
    all_tasks = tasks._load(root)
    worked = [t for t in all_tasks if job_id in (t.get("job_ids") or [])]
    for candidate in candidates[1:]:
        if worked:
            break
        found = tasks._load(candidate)
        hits = [t for t in found if job_id in (t.get("job_ids") or [])]
        if hits:
            root, all_tasks, worked = candidate, found, hits
    if not worked:
        # `job_ids` is written by the CLAIM, so a run that stalled before
        # claiming (budget refused, tree busy, ticket not claimable) matches
        # nothing here and used to post nothing at all, leaving the chat's
        # last word "run started" forever. The Job's own `task_id` says which
        # ticket it was launched for, which is exactly the case a pre-claim
        # failure needs to report on.
        own = job.get("task_id")
        if own:
            worked = [t for t in all_tasks if t.get("id") == own]
    return job, root, worked


def _any_tracker_configured() -> bool:
    """Is there a connection record on this box at all?

    A cheap file test that costs no database, because `jobs.finish` runs on every
    job in the system and most boxes (and every test run) have no tracker
    connected. Resolving the operator account first would open the auth database
    on every finished job to learn nothing.
    """
    from services import ownership, tracker_bindings
    try:
        return any((d / "trackers" / tracker_bindings.STORE_NAME).is_file()
                   for d in (ownership._RUNTIME / "users").iterdir())
    except OSError:
        return False


def post_to_tracker(job_id: str, status: str = "done") -> int:
    """Tell the external tracker a run ended on its issue.

    Two writes, in the order the lifecycle doc splits ownership (§4): the status
    transition, because workshop runs the state machine and the tracker only
    learns the result, and then ONE run-summary comment (§9.2). `push_status` is
    a no-op unless the binding is in `sync` mode; the comment is the single write
    `import` mode makes, because a run summary is exactly what the person who
    filed the issue wants.

    Returns how many linked tickets were told (0 is the normal case: most
    tickets are local). Best-effort, like the chat report — a run that did its
    work must never be marked failed because telling github about it went wrong.
    """
    from services import auth_store, ownership, task_scope, tasks, tracker_sync

    # The connection record lives in the operator's partition, and a sweep
    # finishing in a background process has nobody pinned. Without this the whole
    # path silently reads "no tracker configured" in exactly the case it exists
    # for. Single operator, so `operator_id` is the whole resolution.
    previous = ownership.owner_or_none()
    pinned = False
    try:
        if not _any_tracker_configured():
            return 0
        if previous is None:
            oid = auth_store.operator_id()
            if oid is None:
                return 0
            ownership.set_current_owner(oid)
            pinned = True

        job, root, worked = _worked_for_job(job_id)
        # Which binding applies is a property of the STORE the ticket lives in,
        # not of whoever ran the job — the same rule `key_for_root` states.
        scope = task_scope.key_for_root(root)
        if not worked or not scope:
            return 0
        told = 0
        for t in worked:
            if not (t.get("sync") or {}).get("remote_id"):
                continue
            tracker_sync.push_status(scope, root, t["id"])
            # Re-read after the transition so the comment quotes the status the
            # issue was actually just moved to.
            fresh = tasks.get_task(root, t["id"]) or t
            if tracker_sync.comment(scope, root, t["id"],
                                    _run_comment(job, fresh, status)):
                told += 1
        return told
    except Exception as e:
        log.debug("tracker report for job %s skipped: %s", job_id, e)
        return 0
    finally:
        if pinned:
            ownership.clear_current_owner()


def post_for_job(job_id: str, status: str = "done") -> int:
    """Post one report into every chat that filed a ticket this job worked.

    Returns how many chats were told (0 is the normal case for a run nobody
    started from a conversation)."""
    try:
        job, _root, worked = _worked_for_job(job_id)
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
