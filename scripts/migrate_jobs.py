#!/usr/bin/env python3
"""One-time migration: fold the old run ledgers into the unified Job store.

Run ONCE, with autonomy paused (nothing else appending), from the repo root:

    python3 scripts/migrate_jobs.py            # dry run (default)
    python3 scripts/migrate_jobs.py --apply    # perform the migration

What it does:
  * `.runtime/agent-runs-completed.jsonl` — event-consumer records; each carries
    an `agent_id`. Imported as Jobs (trigger=event). Renamed `.migrated` after.
  * `.runtime/agent-runs.jsonl` — a per-tick AGGREGATE with no `agent_id`; it
    cannot be represented as a Job. NOT imported; renamed `.migrated` for history.
  * `.runtime/agent-runs-pending.jsonl` — the live event QUEUE the cron consumes.
    LEFT ALONE.
  * Per-project `<dir>/.runtime/ralph-run.json` — the old sweep run-state. Imported
    as a `running` loop Job ONLY if its pid is genuinely alive; otherwise skipped.
    The file is removed afterward either way.
  * `.runtime/heartbeats/*.json` — liveness files, not Jobs. LEFT ALONE.

Timestamps from the source records are preserved. Records are written directly as
collapsed single-line Jobs (reads collapse by id, so one full line per Job is
correct). Safe to run with autonomy paused.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from services import jobs  # noqa: E402  (after sys.path setup)

RUNTIME = Path(os.environ.get("WORKSHOP_RUNTIME", str(REPO / ".runtime")))
COMPLETED = RUNTIME / "agent-runs-completed.jsonl"
AGGREGATE = RUNTIME / "agent-runs.jsonl"
PENDING = RUNTIME / "agent-runs-pending.jsonl"  # never touched

# Old event-consumer status -> Job status.
_EVENT_STATUS = {
    "consumed": "done",
    "timeout": "failed",
    "failed": "failed",
    "throttled": "cancelled",
    "skipped": "cancelled",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_job(record: dict) -> None:
    jobs.JOBS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with jobs.JOBS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in record.items() if v is not None},
                           separators=(",", ":")) + "\n")


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _project_dirs() -> list[Path]:
    """Best-effort discovery of project dirs that may hold a ralph-run.json."""
    dirs: set[Path] = set()
    for reg in (RUNTIME / "projects.json", REPO / "projects.json"):
        try:
            data = json.loads(reg.read_text())
        except (OSError, ValueError):
            continue
        for p in (data.get("projects") or {}).values():
            d = p.get("dir")
            if d:
                dirs.add(Path(d))
    # Also glob the conventional project tree.
    for cand in REPO.glob("projects/*/.runtime/ralph-run.json"):
        dirs.add(cand.parent.parent)
    return sorted(dirs)


def migrate_completed(apply: bool) -> int:
    if not COMPLETED.exists():
        print(f"  completed: {COMPLETED.name} absent, skip")
        return 0
    n = 0
    for line in COMPLETED.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        agent_id = rec.get("agent_id")
        if not agent_id:
            continue  # malformed/skipped rows without an agent are not Jobs
        ts = rec.get("ts") or _now_iso()
        status = _EVENT_STATUS.get(rec.get("status"), "done")
        job = {
            "id": jobs._new_id(),
            "agent_id": agent_id,
            "trigger": "event",
            "status": status,
            "started_at": ts,
            "ended_at": ts,
            "exit_code": rec.get("exit_code"),
            "evidence": {"source": rec.get("source"), "relay_route": rec.get("relay_route")},
        }
        if apply:
            _append_job(job)
        n += 1
    print(f"  completed: {n} event Job(s) {'imported' if apply else 'to import'}")
    if apply:
        COMPLETED.rename(COMPLETED.with_suffix(COMPLETED.suffix + ".migrated"))
    return n


def migrate_aggregate(apply: bool) -> None:
    if not AGGREGATE.exists():
        print(f"  aggregate: {AGGREGATE.name} absent, skip")
        return
    print(f"  aggregate: {AGGREGATE.name} is a per-tick aggregate (no agent_id), "
          f"not imported, {'renamed .migrated' if apply else 'would rename .migrated'}")
    if apply:
        AGGREGATE.rename(AGGREGATE.with_suffix(AGGREGATE.suffix + ".migrated"))


def migrate_ralph_runs(apply: bool) -> int:
    n = 0
    for d in _project_dirs():
        f = d / ".runtime" / "ralph-run.json"
        if not f.exists():
            continue
        try:
            rec = json.loads(f.read_text())
        except (OSError, ValueError):
            rec = None
        if rec and rec.get("status") == "running" and _pid_alive(rec.get("pid")):
            slug = rec.get("slug") or d.name
            started = rec.get("started") or rec.get("updated") or _now_iso()
            job = {
                "id": jobs._new_id(),
                "project_slug": slug,
                "trigger": "loop",
                "status": "running",
                "started_at": started,
            }
            if apply:
                _append_job(job)
            n += 1
            print(f"  ralph-run: {slug} is live (pid {rec.get('pid')}), "
                  f"{'imported' if apply else 'would import'} as running loop Job")
        else:
            print(f"  ralph-run: {d.name} not live, skipping import")
        if apply:
            f.unlink()
    if not n:
        print("  ralph-run: no live sweeps to import")
    return n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Migrate old run ledgers into the Job store")
    ap.add_argument("--apply", action="store_true", help="perform the migration (default: dry run)")
    args = ap.parse_args(argv)

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"migrate_jobs [{mode}] -> {jobs.JOBS_LOG}")
    print(f"  (pending queue {PENDING.name} is left untouched; heartbeats/ left untouched)")
    migrate_completed(args.apply)
    migrate_aggregate(args.apply)
    migrate_ralph_runs(args.apply)
    print("done" + ("" if args.apply else " (dry run. Re-run with --apply to write)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
