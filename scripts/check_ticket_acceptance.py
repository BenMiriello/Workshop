#!/usr/bin/env python3
"""Open tickets with no EXECUTABLE acceptance, ratcheted. The number may only go down.

THE FINDING THIS PROTECTS. Measured 2026-09-01 across every task store this
installation sweeps: 208 open tickets, and the `acceptance` field was null on all
208. The field has existed on the schema since the store did. Nothing had ever
filled it. So every ticket's finish line was a sentence somewhere in a 337-word
body, a worker handed one formed its own view of "done" and reported success
against that view, and the reviewer had nothing to run.

`services/ticket_format.acceptance_refusal` is the rule and the gate: filing a
ticket now requires a command, or criteria the gate executes. This script is the
other half. The gate stops the debt growing; this counts what is already there,
so it shrinks visibly instead of being declared fixed.

WHY IT IS NOT A RETROACTIVE FIX. Writing acceptance criteria onto 208 existing
tickets would be inventing 208 finish lines from bodies that never stated one,
which is guessing at what someone meant weeks ago and recording the guess as
fact. The debt is counted, not fabricated.

WHAT IT COUNTS. Every ticket in a NON-TERMINAL status (`tasks.TERMINAL` is done
and cancelled) in every scope `routes.tasks_all.scope_roots` sweeps -- the same
set the Tickets page shows -- whose acceptance is not executable. A done ticket
is not counted: its finish line is in the past and cannot be supplied now, so
counting it would make the number fall for a reason that is not progress.

PER SCOPE, NOT ONE TOTAL, and that is not tidiness. The scopes a process can
see depend on where its project trees are mounted: a sandboxed agent and the
host service legitimately sweep different sets, so a single total would swing by
six for a reason that has nothing to do with anyone's work. A scope absent from
the baseline is therefore not a regression by itself (the same rule
`check_response_model.py` applies to a new route file); it enters the ratchet on
the next deliberate re-pin. What fails the run is a scope the baseline KNOWS
holding more unchecked tickets than it did.

WHY THIS RATCHET HAS NO `--since`. Every other ratchet here measures the TREE,
so it can measure the same tree at the run's base ref and blame only what this
run changed. Tickets are not in the tree: they are rows in a live store that any
process can write at any moment. There is nothing to check out. So this compares
the store as it is against a pinned number, and the pinned number may only move
down. Filing an acceptance-less ticket is what makes it go up, and both filing
paths refuse to do that, so a rise means something got in around the gate --
which is exactly what a reviewer wants to hear about.

Usage:
    python3 scripts/check_ticket_acceptance.py            # measure, compare, exit 1 on regression
    python3 scripts/check_ticket_acceptance.py --json
    python3 scripts/check_ticket_acceptance.py --list     # name the offenders
    python3 scripts/check_ticket_acceptance.py --write-baseline
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_WORKSHOP = _SCRIPTS.parent
_BACKEND = _WORKSHOP / "backend"
_BASELINE_PATH = _SCRIPTS / "ticket_acceptance_baseline.json"

sys.path.insert(0, str(_BACKEND))

# RUN UNDER THE BACKEND'S INTERPRETER, for the same reason `project_tasks.py`
# does: the two migrated scopes read their tickets from Postgres through
# sqlalchemy, and under an interpreter without it the store falls back to the
# file silently. A ratchet reading a stale file would report a number nobody
# else can see. A checkout with no venv still runs, file-backed.
if (importlib.util.find_spec("sqlalchemy") is None
        and not os.environ.get("WORKSHOP_TICKET_RATCHET_REEXEC")):
    _VENV_PY = _BACKEND / ".venv" / "bin" / "python3"
    if _VENV_PY.exists():
        os.environ["WORKSHOP_TICKET_RATCHET_REEXEC"] = "1"
        os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])


def _measure() -> dict:
    from services import tasks, ticket_format
    # The SAME scope sweep the list route uses, imported rather than restated:
    # a second copy of "which scopes have tickets" would drift, and this number
    # has to be the one the operator sees on the Tickets page.
    from routes.tasks_all import scope_roots
    try:
        from services import ownership
        ownership.pin_operator()
    except Exception:
        pass

    per_scope: dict[str, int] = {}
    offenders: list[dict] = []
    open_total = 0
    for skey, root in scope_roots():
        try:
            items = tasks.list_tasks(root)
        except Exception:
            continue
        for t in items:
            if t.get("status") in tasks.TERMINAL:
                continue
            open_total += 1
            if ticket_format.executable_acceptance(t.get("acceptance")):
                continue
            per_scope[skey] = per_scope.get(skey, 0) + 1
            offenders.append({
                "scope": skey,
                "id": str(t.get("id") or ""),
                "title": str(t.get("title") or "")[:80],
                "status": str(t.get("status") or ""),
                "source": tasks.source_kind(t),
            })
    return {
        "open_tickets": open_total,
        "without_executable_acceptance": len(offenders),
        "per_scope_without_executable_acceptance": per_scope,
        "offenders": offenders,
    }


def _regressions(current: dict, baseline: dict) -> list[tuple[str, int, int]]:
    """(scope, pinned, now) for every scope holding MORE unchecked tickets than
    the baseline allows. A scope the baseline has never heard of is skipped: see
    the header on why the visible scope set is not stable across processes."""
    base = baseline.get("per_scope_without_executable_acceptance") or {}
    out = []
    for skey, n in current["per_scope_without_executable_acceptance"].items():
        pinned = base.get(skey)
        if pinned is not None and n > pinned:
            out.append((skey, pinned, n))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="name every open ticket that states no runnable check")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    current = _measure()
    baseline_path = Path(args.baseline)

    if args.write_baseline:
        pinned = {
            # `_`-prefixed: context, not a gate metric. The number of OPEN
            # tickets is not a thing this ratchet governs (a busy week raises it
            # honestly), and check_ratchets_not_loosened.py skips `_` keys, so
            # naming it this way is what stops a re-pin reading as a loosening.
            "_open_tickets": current["open_tickets"],
            "without_executable_acceptance": current["without_executable_acceptance"],
            "per_scope_without_executable_acceptance":
                current["per_scope_without_executable_acceptance"],
        }
        baseline_path.write_text(json.dumps(pinned, indent=2, sort_keys=True) + "\n")
        print(f"check_ticket_acceptance: wrote baseline to {baseline_path} "
              f"({current['without_executable_acceptance']} of "
              f"{current['open_tickets']} open tickets state no runnable check)")
        return 0

    try:
        baseline = json.loads(baseline_path.read_text())
    except (OSError, json.JSONDecodeError):
        baseline = {}

    pinned_total = baseline.get("without_executable_acceptance")
    now = current["without_executable_acceptance"]
    regressions = _regressions(current, baseline)

    if args.json:
        print(json.dumps({"current": current, "baseline": baseline,
                          "regressions": [{"scope": s, "baseline": b, "current": c}
                                          for s, b, c in regressions]}, indent=2))
        return 1 if regressions else 0

    print(f"check_ticket_acceptance: {current['open_tickets']} open ticket(s) in "
          f"{len(current['per_scope_without_executable_acceptance']) or 0} scope(s) "
          "with debt")
    print(f"    with an executable finish line: {current['open_tickets'] - now}")
    print(f"    without one:                    {now}"
          + (f"  (pinned at {pinned_total})" if pinned_total is not None else ""))
    if args.list:
        for o in current["offenders"]:
            print(f"      {o['scope']}/{o['id'][:8]}  [{o['source']}] {o['title']}")

    if regressions:
        print()
        print("check_ticket_acceptance: a scope holds MORE unchecked tickets than it did.")
        for skey, before, after in regressions:
            print(f"    {skey}: {before} -> {after}")
        print("A ticket is filed with a command, or with criteria the gate runs.")
        print("Both filing paths refuse one without: POST /api/tasks and")
        print("`scripts/project_tasks.py add --acceptance-cmd ...`. If a ticket got")
        print("in around them, give it a check rather than re-pinning this file.")
        return 1
    if pinned_total is not None and now < pinned_total:
        print()
        print(f"check_ticket_acceptance: down from {pinned_total} to {now}. Re-pin "
              "with --write-baseline when you mean it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
