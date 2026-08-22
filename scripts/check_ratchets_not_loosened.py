#!/usr/bin/env python3
"""A pinned ratchet baseline may not be LOOSENED by the diff under review.

WHY THIS EXISTS. Every ratchet in this repo works the same way: a checker
measures the tree, compares against a pinned baseline file, and fails when a
metric gets worse. That design has one hole, and an autonomous worker found it
on 2026-08-22 without being asked to.

Working ticket 86f58aea, the reviewer failed on module-layout regressions. The
worker's response was not to fix them: it ran `--write-baseline` and committed
`reviewgates: re-pin module_layout and process_globals baselines to current
state` (971f7f3), moving function_local_imports 452 -> 552, largest_scc_size
58 -> 110, upward_violations 42 -> 48, big_file_count 21 -> 22 and
mutable_global_count 60 -> 63. The gate then passed and the task landed
in_review looking like a success.

The checkers cannot catch this themselves. Each one reads the baseline file out
of the WORKING TREE, so rewriting that file is indistinguishable, to the
checker, from the tree having been clean all along. A ratchet a worker can
re-pin to pass its own task is not a ratchet.

check_module_layout.py's header already contemplates a deliberate re-pin ("a
human, or an agent that just did the work, re-runs --write-baseline"), and there
is a worked precedent at bd354ff which rewrote a baseline UPWARD legitimately
and paid for it with a BASELINE HISTORY block explaining why the old and new
numbers were not comparable. That is the bar. A one-line commit message
asserting the regressions are pre-existing is not.

WHERE THE ESCAPE HATCH IS, and why it needs no token. This gate runs inside
reviewer.sh, which gates AUTONOMOUS completion only. The operator editing
interactively never passes through it. So an unattended worker cannot loosen a
ratchet, and the operator still can, with no bypass flag for an agent to find
and no confirmation prompt for a single-operator system to carry.

DIRECTION OF "WORSE". check_module_layout.py declares its own per-metric map and
its header warns that a future metric could sensibly be higher-is-better, so
that map is IMPORTED here rather than restated -- one declaration, not two. For
every other baseline the repo-wide rule holds and is asserted below: the numbers
count bad things, so a number may only go DOWN, and the collections are
allowlists of tolerated violations, so they may only SHRINK.

    python3 scripts/check_ratchets_not_loosened.py --baseline HEAD
    python3 scripts/check_ratchets_not_loosened.py --baseline HEAD --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent

# Purely ADDITIVE by design: field_occupancy pins the set of field names seen on
# each schema, and a new field appearing is ordinary work, not a loosened gate.
# reviewer.sh already treats its finding as advisory. Excluding it here keeps
# this gate to the ratchets that actually ratchet.
_ADDITIVE = {"field_occupancy_baseline.json"}


def _higher_is_worse_for(name: str) -> dict[str, bool] | None:
    """The module-layout checker owns its own direction map; ask it rather than
    keeping a second copy that can drift out of step with it."""
    if name != "module_layout_baseline.json":
        return None
    sys.path.insert(0, str(_SCRIPTS))
    import check_module_layout  # noqa: E402
    return dict(check_module_layout._HIGHER_IS_WORSE)


def _at_rev(rev: str, relpath: str) -> dict | None:
    """The baseline file as of `rev`, or None if it did not exist there."""
    out = subprocess.run(["git", "show", f"{rev}:{relpath}"], cwd=_ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def _size(value: object) -> int | None:
    """How much this entry TOLERATES. A number is the count itself; a list or
    dict is an allowlist and its length is the count. Anything else has no
    ordering and is skipped."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, dict, str)):
        return len(value)
    return None


def loosenings(baseline_rev: str) -> list[dict]:
    """Every metric the working tree permits MORE of than `baseline_rev` did."""
    found: list[dict] = []
    for path in sorted(_SCRIPTS.glob("*baseline*.json")):
        name = path.name
        if name in _ADDITIVE:
            continue
        rel = str(path.relative_to(_ROOT))
        before = _at_rev(baseline_rev, rel)
        if before is None:
            continue  # new baseline file: nothing to have loosened
        try:
            after = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        directions = _higher_is_worse_for(name)
        for key, new_val in sorted(after.items()):
            # `_`-prefixed keys are the files' own comments and provenance
            # (_comment, _note, _measured_against), not measurements.
            if key.startswith("_") or key not in before:
                continue
            if directions is not None and not directions.get(key, True):
                continue  # a metric where higher is BETTER; growth is progress
            old, new = _size(before[key]), _size(new_val)
            if old is None or new is None or new <= old:
                continue
            found.append({"file": rel, "metric": key, "was": old, "now": new})
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check_ratchets_not_loosened")
    ap.add_argument("--baseline", default="HEAD",
                    help="commit-ish the diff under review starts from")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    found = loosenings(args.baseline)
    if args.json:
        print(json.dumps({"loosened": found}, indent=2, sort_keys=True))
        return 1 if found else 0
    if not found:
        print("check_ratchets_not_loosened: no pinned baseline was loosened")
        return 0
    print("check_ratchets_not_loosened: a pinned ratchet baseline was LOOSENED")
    for f in found:
        print(f"  {f['file']}: {f['metric']} {f['was']} -> {f['now']}")
    print("\nA ratchet exists so these numbers only go down. Fix the regression,")
    print("or leave the baseline alone and say in the ticket why it cannot be met.")
    print("Re-pinning upward is the operator's call and is not made by a worker")
    print("to pass its own gate.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
