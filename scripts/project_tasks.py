#!/usr/bin/env python3
"""Thin CLI over backend/services/tasks.py so shell (project_ralph.sh) and a
builder chat's Bash can drive a scope's structured task store WITHOUT desyncing
it from TODO.md.

The store (<scope>/.tasks.json) is authoritative; TODO.md is its regenerated
export plus a healed inbox. NEVER sed-edit TODO.md directly; a stray edit is
reconciled back in, but editing state that way is not the contract. Change state
through this CLI (shell) or the task API (chat/UI).

Verbs (all take a scope directory as the first positional):
  next-open <dir>                     first ready task as JSON (alias: next-ready)
  next-ready <dir> [--assignee A]     dependency-aware next ready task
  list-json <dir> [--assignee A] [--include-done]
  add <dir> "text" [--priority N] [--depends-on id,id] [--assignee A]
      [--touches glob,glob] [--acceptance-cmd CMD] [--max-iterations N]
      [--effort s|m|l] [--discovered-from id] [--provenance KEY] [--source KIND]
  set-status <dir> <id> <status> [--job-id J] [--reason R] [--expect-claim-token T]
  claim <dir> <id> <job_id> [--assignee A] [--pid PID]
  note <dir> <id> "evidence text" [--kind KIND] [--ref REF]
  block <dir> <id> "reason" [--expect-claim-token T]
  set-awaiting <dir> <id> "question"  set/clear the operator-input payload

Every command prints JSON on stdout (a task object, a list, or {} / {"error":...}).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# backend/ on path so `from services import tasks` resolves the SAME store the API uses.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(_BACKEND))

from services import tasks  # noqa: E402


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _slim(t: dict | None) -> dict:
    """Compact projection for the loop (keeps next-open's old shape + new fields)."""
    if not t:
        return {}
    return {
        "id": t["id"], "text": t.get("text", ""), "owner": t.get("owner", "operator"),
        "assignee": t.get("assignee"), "status": t.get("status"),
        "priority": t.get("priority", 0), "depends_on": t.get("depends_on", []),
        "acceptance": t.get("acceptance"), "max_iterations": t.get("max_iterations"),
        "attempts": t.get("attempts", 0), "awaiting": t.get("awaiting"),
        "blocked_reason": t.get("blocked_reason"),
        "touches": t.get("touches", []),
        "claim_token": t.get("claim_token"),
        "lease_until": t.get("lease_until"),
        "claim_pid": t.get("claim_pid"),
    }


def _opt_int(v):
    """An optional int CLI arg: empty string / None -> None (no fence / no pid)."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="project_tasks.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scope(sp):
        sp.add_argument("dir", type=Path)

    sp = sub.add_parser("next-open"); scope(sp); sp.add_argument("--assignee", default=None)
    sp = sub.add_parser("next-ready"); scope(sp); sp.add_argument("--assignee", default=None)
    sp = sub.add_parser("list-json"); scope(sp)
    sp.add_argument("--assignee", default=None); sp.add_argument("--include-done", action="store_true")

    sp = sub.add_parser("add"); scope(sp); sp.add_argument("text")
    sp.add_argument("--priority", type=int, default=0)
    sp.add_argument("--depends-on", default=None)
    sp.add_argument("--assignee", default=None)
    sp.add_argument("--touches", default=None)
    sp.add_argument("--acceptance-cmd", default=None)
    sp.add_argument("--max-iterations", type=int, default=None)
    sp.add_argument("--effort", default=None)
    sp.add_argument("--discovered-from", default=None)
    sp.add_argument("--provenance", default=None)
    sp.add_argument("--source", default="agent")

    sp = sub.add_parser("set-status"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("status")
    sp.add_argument("--job-id", default=None); sp.add_argument("--reason", default=None)
    sp.add_argument("--expect-claim-token", default=None)
    sp.add_argument("--restore-attempt", action="store_true",
                    help="give back the attempt the claim consumed (env failure, not task failure)")

    sp = sub.add_parser("claim"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("job_id"); sp.add_argument("--assignee", default=None)
    sp.add_argument("--pid", default=None)

    sp = sub.add_parser("note"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("text")
    sp.add_argument("--kind", default="note"); sp.add_argument("--ref", default=None)

    sp = sub.add_parser("block"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("reason")
    sp.add_argument("--expect-claim-token", default=None)

    sp = sub.add_parser("set-awaiting"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("question")

    a = p.parse_args(argv[1:])
    root: Path = a.dir

    try:
        if a.cmd in ("next-open", "next-ready"):
            print(json.dumps(_slim(tasks.next_ready(root, assignee=a.assignee))))
            return 0
        if a.cmd == "list-json":
            print(json.dumps(tasks.list_tasks(root, include_done=a.include_done, owner=a.assignee)))
            return 0
        if a.cmd == "add":
            acceptance = {"kind": "command", "spec": a.acceptance_cmd} if a.acceptance_cmd else None
            t = tasks.add(
                root, text=a.text, priority=a.priority, depends_on=_csv(a.depends_on),
                assignee=a.assignee, touches=_csv(a.touches), acceptance=acceptance,
                max_iterations=a.max_iterations, effort=a.effort,
                discovered_from=a.discovered_from, provenance=a.provenance,
                source={"kind": a.source, "ref": None},
            )
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "set-status":
            t = tasks.update(root, a.task_id, status=a.status, job_id=a.job_id,
                             blocked_reason=a.reason,
                             restore_attempt=bool(getattr(a, "restore_attempt", False)),
                             expect_claim_token=_opt_int(a.expect_claim_token))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "claim":
            t = tasks.claim(root, a.task_id, a.job_id, assignee=a.assignee,
                            pid=_opt_int(a.pid))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "note":
            t = tasks.update(root, a.task_id, evidence={"kind": a.kind, "ref": a.ref or a.text})
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "block":
            t = tasks.update(root, a.task_id, status="blocked", blocked_reason=a.reason,
                             expect_claim_token=_opt_int(a.expect_claim_token))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "set-awaiting":
            t = tasks.update(root, a.task_id, awaiting={"question": a.question})
            print(json.dumps(_slim(t)))
            return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps({"error": f"unknown command: {a.cmd}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
