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
      [--touches glob,glob] [--acceptance-cmd CMD] [--criterion 'desc::cmd::expect']
      [--max-attempts N] [--effort s|m|l] [--latitude strict|standard|open]
      [--discovered-from id] [--provenance KEY] [--source KIND]
      # --latitude is what the worker may do where THIS ticket does not say.
      # Omit it to INHERIT the scope's setting (that is not the same as
      # `--latitude standard`, which pins the default onto the ticket).
      # --criterion is repeatable and attaches a machine-checkable assertion the
      # gate EXECUTES (exit0 by default, or an expected stdout substring). The
      # 'desc::' and '::expect' segments are optional: 'cmd' alone => exit0 gate.
  set-status <dir> <id> <status> [--job-id J] [--reason R] [--expect-claim-token T]
  claim <dir> <id> <job_id> [--assignee A] [--pid PID]
  heartbeat <dir> <id> <claim_token>  renew a claimed task's lease (fenced by token)
  note <dir> <id> "evidence text" [--kind KIND] [--ref REF]
  block <dir> <id> "reason" [--expect-claim-token T]
      [--kind K] [--ask "..."] [--unblocks-if "..."] [--dedupe-key KEY]
      [--proposal-uri URI --proposal-verb read|write|append] [--raised-by REF]
      # The structured request the Review tab answers. `reason` alone still works
      # (a plain block). --kind is an open slug (permission|resource|input|
      # decision|missing-info|dependency|environment|other, or a new one): the set
      # is a seed, not a boundary. A --proposal-uri + --proposal-verb pair makes
      # the ask machine-checkable, so the review can offer a prefilled grant
      # instead of asking the operator to author one. --dedupe-key groups a
      # mass-block so ten tasks stopped by one rule are answered once.
  set-awaiting <dir> <id> "question"  set/clear the operator-input payload
  check-touches <dir> <id> [--expect-claim-token T]
      read a newline-separated file list on STDIN and hold the run to the task's
      declared `touches` globs: prints {"violations": [...], "blocked": bool}. A
      task with no `touches` is unrestricted, so this prints an empty list.

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
from services.latitude import LATITUDES as _LATITUDES  # noqa: E402


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _parse_criterion(raw: str) -> dict | None:
    """Parse a `desc::cmd::expect` criterion string into {desc, cmd, expect}.

    Segments are optional: `cmd` alone => {desc: cmd, cmd, expect: exit0};
    `desc::cmd` => expect defaults to exit0; `desc::cmd::expect` sets all three.
    When 3+ segments are present the FIRST is desc and the LAST is expect, so a
    cmd containing `::` still parses. A blank cmd yields None (dropped)."""
    parts = [p for p in (raw or "").split("::")]
    if len(parts) >= 3:
        desc, expect = parts[0].strip(), parts[-1].strip()
        cmd = "::".join(parts[1:-1]).strip()
    elif len(parts) == 2:
        desc, cmd, expect = parts[0].strip(), parts[1].strip(), ""
    else:
        cmd = (parts[0] if parts else "").strip()
        desc, expect = "", ""
    if not cmd:
        return None
    return {"desc": desc or cmd, "cmd": cmd, "expect": expect or "exit0"}


def _slim(t: dict | None) -> dict:
    """Compact projection for the loop (keeps next-open's old shape + new fields)."""
    if not t:
        return {}
    return {
        "id": t["id"], "text": t.get("text", ""), "owner": t.get("owner", "operator"),
        "assignee": t.get("assignee"), "status": t.get("status"),
        "priority": t.get("priority", 0), "depends_on": t.get("depends_on", []),
        "acceptance": t.get("acceptance"), "max_attempts": t.get("max_attempts"),
        "attempts": t.get("attempts", 0), "awaiting": t.get("awaiting"),
        "blocked_reason": t.get("blocked_reason"),
        "touches": t.get("touches", []),
        "effort": t.get("effort"),
        # null = inherit from the scope; ralph.sh resolves the cascade itself.
        "latitude": t.get("latitude"),
        "claim_token": t.get("claim_token"),
        "lease_until": t.get("lease_until"),
        "last_heartbeat": t.get("last_heartbeat"),
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
    sp.add_argument("--criterion", action="append", default=None,
                    help="repeatable machine-checkable assertion 'desc::cmd::expect' "
                         "(expect defaults to exit0; a bare 'cmd' is allowed)")
    sp.add_argument("--max-attempts", type=int, default=None)
    sp.add_argument("--effort", default=None)
    sp.add_argument("--latitude", default=None, choices=(None, *_LATITUDES))
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

    sp = sub.add_parser("heartbeat"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("claim_token")
    sp.add_argument("--assignee", default=None)

    sp = sub.add_parser("note"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("text")
    sp.add_argument("--kind", default="note"); sp.add_argument("--ref", default=None)

    sp = sub.add_parser("block"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("reason")
    sp.add_argument("--expect-claim-token", default=None)
    sp.add_argument("--kind", default=None)
    sp.add_argument("--ask", default=None)
    sp.add_argument("--unblocks-if", default=None)
    sp.add_argument("--proposal-uri", default=None)
    sp.add_argument("--proposal-verb", default=None, choices=(None, "read", "write", "append"))
    sp.add_argument("--dedupe-key", default=None)
    sp.add_argument("--raised-by", default=None)

    sp = sub.add_parser("set-awaiting"); scope(sp)
    sp.add_argument("task_id"); sp.add_argument("question")

    sp = sub.add_parser("check-touches"); scope(sp)
    sp.add_argument("task_id")
    sp.add_argument("--job-id", default=None)
    sp.add_argument("--expect-claim-token", default=None)

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
            criteria = [c for c in (_parse_criterion(r) for r in (a.criterion or [])) if c]
            if criteria:
                # A criteria-carrying task with no explicit command acceptance stays
                # reviewer-kind: the assertions run alongside the reviewer's diff gate.
                acceptance = acceptance or {"kind": "reviewer", "spec": None}
                acceptance["criteria"] = criteria
            t = tasks.add(
                root, text=a.text, priority=a.priority, depends_on=_csv(a.depends_on),
                assignee=a.assignee, touches=_csv(a.touches), acceptance=acceptance,
                max_attempts=a.max_attempts, effort=a.effort,
                latitude=a.latitude,
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
        if a.cmd == "heartbeat":
            t = tasks.heartbeat(root, a.task_id, _opt_int(a.claim_token),
                                assignee=a.assignee)
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "note":
            t = tasks.update(root, a.task_id, evidence={"kind": a.kind, "ref": a.ref or a.text})
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "block":
            # `reason` is the human line and stays exactly as it was. --ask (and
            # friends) additionally attach the STRUCTURED request the Review tab
            # acts on: what would unblock this, and — when the ask names a uri and
            # a verb — a grant the operator can approve in one click. Omit them and
            # the block is what it always was.
            request = None
            if a.ask or a.kind:
                request = {
                    "kind": a.kind or "other",
                    "ask": a.ask or a.reason,
                    "question": a.ask or a.reason,
                    "why": a.reason,
                    "unblocks_if": a.unblocks_if,
                    "dedupe_key": a.dedupe_key,
                    "raised_by": a.raised_by,
                }
                if a.proposal_uri and a.proposal_verb:
                    request["proposal"] = {"uri": a.proposal_uri, "verb": a.proposal_verb}
            t = tasks.update(root, a.task_id, status="blocked", blocked_reason=a.reason,
                             blocked_request=request,
                             expect_claim_token=_opt_int(a.expect_claim_token))
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "set-awaiting":
            t = tasks.update(root, a.task_id, awaiting={"question": a.question})
            print(json.dumps(_slim(t)))
            return 0
        if a.cmd == "check-touches":
            files = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
            bad = tasks.enforce_touches(
                root, a.task_id, files, job_id=a.job_id,
                expect_claim_token=_opt_int(a.expect_claim_token))
            print(json.dumps({"violations": bad, "blocked": bool(bad)}))
            return 0
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps({"error": f"unknown command: {a.cmd}"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
