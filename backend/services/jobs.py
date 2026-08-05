"""Job store — one record for every bounded execution.

A Job is the single record type for anything that runs to completion under the
platform: a cron worker tick, a per-project Ralph sweep, an event-driven agent
reaction, a manual "run now", or a supervisor heartbeat cycle. Interactive chat
turns are NOT Jobs (they are open-ended conversation, not a bounded execution).

Record shape (all keys optional except id/trigger/status/started_at):

    {
      "id":            "job_<epoch_ms>_<rand>",
      "agent_id":      "agent-...",        # who ran (Letta id or logical name)
      "project_slug":  "my-project",       # project binding, if any
      "chat_id":       "discord:...",      # chat binding, if any
      "trigger":       "manual|cron|event|loop",
      "status":        "queued|running|awaiting_input|done|failed|cancelled",
      "started_at":    "<iso8601>",
      "ended_at":      "<iso8601>",
      "exit_code":     0,
      "tasks_done":    3,
      "tasks_failed":  0,
      "evidence":      {...} | "text",     # freeform proof of work
      "budget":        {...}               # loop caps that bounded the run
    }

Backing store: append-only JSONL at `.runtime/jobs.jsonl`. `start()` appends the
opening record; `finish()` appends a second line carrying the same `id` plus the
terminal fields. Readers collapse lines by id (later lines override earlier), so
the log stays append-only and safe for concurrent shell + backend writers.

This module is pure stdlib so it is importable both as `services.jobs` (inside
the FastAPI app, cwd=backend/) and via the CLI shim `python -m backend.services
.jobs start|finish ...` (from the repo root, for shell writers like
cron_agent.sh / project_ralph.sh / ralph.sh).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

STATUSES = ("queued", "running", "awaiting_input", "done", "failed", "cancelled")
TERMINAL_STATUSES = ("done", "failed", "cancelled")
TRIGGERS = ("manual", "cron", "event", "loop")
# `kind` classifies what the Job is doing so the feed can filter by role
# (ralph sweep, deploy attempt, categorizer, ...). Open-set: unknown values are
# preserved but the enum below is what the deploy/dispatch/UI code understands.
KINDS = ("ralph", "sweep", "deploy", "deploy-rollback", "categorize", "reason")

JOBS_LOG = Path(os.environ.get(
    "WORKSHOP_JOBS_LOG",
    "/home/clawd-a/workshop/.runtime/jobs.jsonl",
))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"job_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{random.randint(0x1000, 0xffff):04x}"


def _append(record: dict) -> None:
    JOBS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with JOBS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _clean(**fields: Any) -> dict:
    """Drop keys whose value is None so append lines stay compact and a later
    partial line never clobbers an earlier value with a null."""
    return {k: v for k, v in fields.items() if v is not None}


# ---------------------------------------------------------------------------
# Write API
# ---------------------------------------------------------------------------

def start(
    *,
    agent_id: str | None = None,
    project_slug: str | None = None,
    chat_id: str | None = None,
    trigger: str = "manual",
    status: str = "running",
    budget: dict | None = None,
    job_id: str | None = None,
    task_id: str | None = None,
    parent_id: str | None = None,
    pid: int | None = None,
    kind: str | None = None,
    binding: dict | None = None,
) -> dict:
    """Open a Job and append its record. Returns the full record (with `id`).
    `task_id` links a child Job to the Task it worked; `parent_id` links it to
    the coordinator sweep Job, so the Jobs feed + task history form one graph.
    `pid` records the launching process so the liveness watchdog can tell a
    running Job whose process is dead from one that is genuinely in flight (2d)."""
    if trigger not in TRIGGERS:
        trigger = "manual"
    if status not in STATUSES:
        status = "running"
    record = _clean(
        id=job_id or _new_id(),
        agent_id=agent_id,
        project_slug=project_slug,
        chat_id=chat_id,
        trigger=trigger,
        status=status,
        started_at=_now_iso(),
        budget=budget,
        task_id=task_id,
        parent_id=parent_id,
        pid=pid,
        kind=kind,
        binding=binding,
    )
    _append(record)
    return record


def finish(
    job_id: str,
    *,
    status: str = "done",
    exit_code: int | None = None,
    tasks_done: int | None = None,
    tasks_failed: int | None = None,
    evidence: Any | None = None,
    budget: dict | None = None,
) -> dict:
    """Close a Job by appending a terminal line carrying the same id. Reads
    collapse it over the opening line. Returns the appended line."""
    if status not in STATUSES:
        status = "done"
    line = _clean(
        id=job_id,
        status=status,
        ended_at=_now_iso(),
        exit_code=exit_code,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
        evidence=evidence,
        budget=budget,
    )
    _append(line)
    # Tell the conversation that asked for this work that it is over. Here, at
    # the one terminal point every job passes through, rather than in each
    # runner — a runner added later would otherwise finish silently and nobody
    # would notice the report was missing. Best-effort by construction: see
    # services/run_report.
    try:
        from services import run_report
        run_report.post_for_job(job_id, status)
    except Exception:
        pass
    return line


# ---------------------------------------------------------------------------
# Read / index API
# ---------------------------------------------------------------------------

def _collapse(lines: Iterable[dict]) -> dict[str, dict]:
    """Merge append-only lines into one record per id (later overrides)."""
    by_id: dict[str, dict] = {}
    for rec in lines:
        jid = rec.get("id")
        if not jid:
            continue
        if jid in by_id:
            by_id[jid].update(rec)
        else:
            by_id[jid] = dict(rec)
    return by_id


def _read_all() -> dict[str, dict]:
    if not JOBS_LOG.exists():
        return {}
    parsed: list[dict] = []
    for line in JOBS_LOG.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _collapse(parsed)


def read(
    *,
    agent_id: str | None = None,
    project_slug: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    parent_id: str | None = None,
    kind: str | None = None,
    binding_key: str | None = None,
    binding_value: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Most recent Jobs first, filtered by agent/project/status/task/parent/kind.
    binding_key+binding_value filters on a specific field of the binding dict
    (e.g. read(kind="deploy", binding_key="deploy_id", binding_value="dep_x"))."""
    limit = max(1, min(int(limit), 1000))
    records = list(_read_all().values())

    def keep(r: dict) -> bool:
        if agent_id is not None and r.get("agent_id") != agent_id:
            return False
        if project_slug is not None and r.get("project_slug") != project_slug:
            return False
        if status is not None and r.get("status") != status:
            return False
        if task_id is not None and r.get("task_id") != task_id:
            return False
        if parent_id is not None and r.get("parent_id") != parent_id:
            return False
        if kind is not None and r.get("kind") != kind:
            return False
        if binding_key is not None:
            b = r.get("binding") or {}
            if not isinstance(b, dict) or str(b.get(binding_key)) != str(binding_value):
                return False
        return True

    records = [r for r in records if keep(r)]
    records.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return records[:limit]


def get(job_id: str) -> dict | None:
    return _read_all().get(job_id)


# ---------------------------------------------------------------------------
# Liveness reconcile (self-healing 2d): fail Jobs stuck `running` after death
# ---------------------------------------------------------------------------

def _pid_alive(pid) -> bool:
    """True if a process with this pid is alive (signal 0 probe)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _age_seconds(rec: dict, now: datetime | None = None) -> float | None:
    started = rec.get("started_at")
    if not started:
        return None
    try:
        dt = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds()


def reconcile_stuck(*, min_age_sec: int = 120, max_age_sec: int = 7200,
                    now: datetime | None = None) -> list[dict]:
    """Find Jobs stuck in `running` whose launching process is dead and close
    them as `failed`, so the Jobs feed tells the truth and a crashed sweep does
    not linger as a phantom "still running" record forever (2d).

    A running Job is considered dead when it is older than ``min_age_sec`` (to
    avoid racing a just-opened Job) AND either its recorded ``pid`` is no longer
    alive, or it has no pid and is older than ``max_age_sec`` (a hard age ceiling
    for legacy records that predate pid recording). Returns the list of Jobs it
    failed. Reclaiming the associated Task is the task store's job (tasks.reclaim);
    this only fixes the Job record."""
    now = now or datetime.now(timezone.utc)
    failed: list[dict] = []
    for rec in read(status="running", limit=1000):
        age = _age_seconds(rec, now)
        if age is None or age < min_age_sec:
            continue
        pid = rec.get("pid")
        if pid is not None:
            dead = not _pid_alive(pid)
        else:
            dead = age >= max_age_sec
        if not dead:
            continue
        finish(
            rec["id"],
            status="failed",
            evidence="liveness watchdog: launching process dead while running",
        )
        failed.append(rec)
    return failed


# ---------------------------------------------------------------------------
# CLI shim — for shell writers (cron_agent.sh / project_ralph.sh / ralph.sh)
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="jobs", description="Workshop Job store")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="open a Job; prints the job id")
    p_start.add_argument("--agent-id", default=None)
    p_start.add_argument("--project-slug", default=None)
    p_start.add_argument("--chat-id", default=None)
    p_start.add_argument("--trigger", default="manual", choices=list(TRIGGERS))
    p_start.add_argument("--status", default="running", choices=list(STATUSES))
    p_start.add_argument("--budget", default=None, help="JSON object")
    p_start.add_argument("--id", dest="job_id", default=None)
    p_start.add_argument("--task-id", default=None)
    p_start.add_argument("--parent-id", default=None)
    p_start.add_argument("--pid", type=int, default=None)

    p_finish = sub.add_parser("finish", help="close a Job")
    p_finish.add_argument("--id", dest="job_id", required=True)
    p_finish.add_argument("--status", default="done", choices=list(STATUSES))
    p_finish.add_argument("--exit-code", type=int, default=None)
    p_finish.add_argument("--tasks-done", type=int, default=None)
    p_finish.add_argument("--tasks-failed", type=int, default=None)
    p_finish.add_argument("--evidence", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "start":
        budget = None
        if args.budget:
            try:
                budget = json.loads(args.budget)
            except json.JSONDecodeError:
                budget = None
        rec = start(
            agent_id=args.agent_id,
            project_slug=args.project_slug,
            chat_id=args.chat_id,
            trigger=args.trigger,
            status=args.status,
            budget=budget,
            job_id=args.job_id,
            task_id=args.task_id,
            parent_id=args.parent_id,
            pid=args.pid,
        )
        print(rec["id"])
        return 0

    if args.cmd == "finish":
        evidence = args.evidence
        if evidence is not None:
            # Mirror --budget: a structured evidence payload (the receipt object,
            # the awaiting-input question list) arrives as a JSON string on the
            # CLI. Parse it so it is stored as a real object, not a string. A
            # plain-text legacy evidence string falls through unchanged.
            try:
                evidence = json.loads(evidence)
            except json.JSONDecodeError:
                pass
        finish(
            args.job_id,
            status=args.status,
            exit_code=args.exit_code,
            tasks_done=args.tasks_done,
            tasks_failed=args.tasks_failed,
            evidence=evidence,
        )
        print("ok")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
