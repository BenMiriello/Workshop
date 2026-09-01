#!/usr/bin/env python3
"""Does the plan we already wrote actually exist in the tree?

WHY THIS EXISTS, and it is not a style preference.

On 2026-08-29 someone wrote `docs/platform-redesign/threads-branches-and-pages-
2026-08-29.md`, whose section 7 is a numbered, ordered, five-step plan. On
2026-08-31 and again on 2026-09-01 the operator reported the SAME defects that
plan closes, in the same words, and on 2026-09-01 he asked why nothing had
moved. Nothing had moved. Three sessions had read that document, agreed with
it, summarised it, and written further documents about it.

A plan in a markdown file has no failure mode. It cannot go red. A session can
read it, feel oriented, and do something else, and the tree is indistinguishable
afterwards from one where the session executed it. This file gives the plan the
one property it was missing: it can FAIL.

THE SECOND FAILURE, which is subtler and did more damage.

Section 7 of that document ends with a stated blocker: "Prerequisite for any of
it, and currently blocking: `development` is not an app row. The `dev_only`
migration was never applied and sits on one of six alembic heads."

By 2026-09-01 that was false. `alembic heads` printed exactly one head and the
live database was at it. But three sessions took the document's word, treated
the whole plan as blocked, and went to work on something else. A STALE BLOCKER
IN A PLAN IS A PERMANENT EXCUSE, and it is worse than no plan, because it
converts every future session's inaction into something that looks like
diligence.

So a pin here may declare `blocked_on_check`: a command whose SUCCESS means the
blocker is gone. When a pinned step's blocker has cleared, this checker does not
quietly keep the pin. It fails, loudly, with UNBLOCKED AND UNSTARTED. That is
the single most valuable line this file can print, because it is the exact
sentence nobody said out loud for three days.

HOW IT BEHAVES, following `check_fresh_clone.sh` and its baseline, which is the
idiom this repo already uses for "a rule whose violations may only go down":

  * Every step is a set of ASSERTIONS about the tree. Each is mechanical.
  * A step not yet done is PINNED in `scripts/plan_landed_baseline.json`, with
    `pinned`, `because`, and optionally `blocked_on` / `blocked_on_check`.
  * A pin prints `NOT YET`. It never prints PASS and it never makes the
    absolute run green: with no arguments this exits 1 while any pin remains.
  * `--quiet` exits 0 on pinned steps, so a reviewer does not reject a worker
    for debt the worker inherited. That is the ONLY thing a pin buys.
  * A pinned step that starts passing is a failure (PIN STALE): finish the
    bookkeeping and remove the pin.
  * A pinned step whose `blocked_on_check` now succeeds is a failure
    (UNBLOCKED AND UNSTARTED), even under --quiet. An excuse that has expired
    is not a reason, and this is the one condition serious enough to fail a
    review over, because it is how three days went missing.
  * Adding a pin grows the baseline, which `check_ratchets_not_loosened.py`
    reads as a loosened ratchet and rejects inside a review.

A `blocked_on_check` MUST BE A COMMAND THAT CAN ACTUALLY SUCCEED, and the
author has to establish that, because nothing here can. Measured 2026-09-01,
on this file's own first day: the pin for step 3 used

    ! grep -rqE '"/runs"|"/changes"|"/deploys"|...' frontend/src

as its unblock check, and `frontend/src/api/schema.gen.ts` carries "/changes"
and "/deploys" as BACKEND ENDPOINT PATHS in the generated OpenAPI client. It
matched before the work started and would have matched after it finished, so
the check was unsatisfiable on every possible tree and the UNBLOCKED AND
UNSTARTED alarm could never have fired for that step.

That is the exact failure this file exists to prevent, wearing the costume of
the mechanism meant to prevent it: a pin whose escape hatch cannot open is a
permanent excuse with a command attached, which reads more diligent than a
plain excuse and is worse. So: write the check against the thing that will
actually change, exclude generated files by path, and satisfy yourself it would
pass on the finished tree before you pin.

Usage:
  python3 scripts/check_plan_landed.py            # full report, exits 1 on any pin
  python3 scripts/check_plan_landed.py --quiet    # reviewer gate: pins tolerated
  python3 scripts/check_plan_landed.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "scripts" / "plan_landed.json"
BASELINE = ROOT / "scripts" / "plan_landed_baseline.json"


class AssertionFailed(Exception):
    pass


def _paths(pattern: str) -> list[Path]:
    """Glob relative to the repo root. A literal path is its own glob."""
    if any(ch in pattern for ch in "*?["):
        return sorted(ROOT.glob(pattern))
    p = ROOT / pattern
    return [p] if p.exists() else []


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise AssertionFailed(f"cannot read {path.relative_to(ROOT)}: {e}") from e


def _check_absent(a: dict) -> None:
    hits = _paths(a["path"])
    if hits:
        names = ", ".join(str(h.relative_to(ROOT)) for h in hits[:5])
        raise AssertionFailed(f"still present: {names}")


def _check_present(a: dict) -> None:
    if not _paths(a["path"]):
        raise AssertionFailed(f"does not exist: {a['path']}")


def _check_regex_absent(a: dict) -> None:
    rx = re.compile(a["regex"])
    hits: list[str] = []
    for p in _paths(a["path"]):
        if not p.is_file():
            continue
        for i, line in enumerate(_read(p).splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    if hits:
        raise AssertionFailed(
            f"{len(hits)} line(s) still match /{a['regex']}/: " + ", ".join(hits[:4]))


def _check_regex_present(a: dict) -> None:
    rx = re.compile(a["regex"])
    files = [p for p in _paths(a["path"]) if p.is_file()]
    if not files:
        raise AssertionFailed(f"no file at {a['path']} to search")
    for p in files:
        if rx.search(_read(p)):
            return
    raise AssertionFailed(f"nothing matches /{a['regex']}/ in {a['path']}")


def _check_count_at_most(a: dict) -> None:
    rx = re.compile(a["regex"])
    files = [p for p in _paths(a["path"]) if p.is_file()]
    if not files:
        raise AssertionFailed(f"no file at {a['path']} to count in")
    n = sum(len(rx.findall(_read(p))) for p in files)
    if n > a["max"]:
        raise AssertionFailed(f"{n} matches of /{a['regex']}/, the target is at most {a['max']}")


def _check_command_succeeds(a: dict) -> None:
    """An assertion the filesystem cannot answer.

    Used for the one class of claim that burned this repo: a plan that names a
    STATE ("the migration was never applied", "it sits on one of six heads") is
    making a claim about a running system, and nobody re-ran it for three days.
    A claim about state belongs in a command, not in prose.
    """
    try:
        r = subprocess.run(a["command"], shell=True, cwd=ROOT, capture_output=True,
                           text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        raise AssertionFailed(f"could not run: {e}") from e
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise AssertionFailed(
            f"exit {r.returncode}: " + (tail[-1] if tail else "no output"))


CHECKS = {
    "command_succeeds": _check_command_succeeds,
    "absent": _check_absent,
    "present": _check_present,
    "regex_absent": _check_regex_absent,
    "regex_present": _check_regex_present,
    "count_at_most": _check_count_at_most,
}


def run_step(step: dict) -> list[str]:
    """Every failing assertion in one step. Empty means the step has landed."""
    failures: list[str] = []
    for a in step.get("assertions", []):
        fn = CHECKS.get(a.get("kind", ""))
        if fn is None:
            failures.append(f"unknown assertion kind {a.get('kind')!r}")
            continue
        try:
            fn(a)
        except AssertionFailed as e:
            failures.append(f"{a.get('says', a.get('kind'))}: {e}")
    return failures


def blocker_cleared(pin: dict) -> bool | None:
    """True when the pin's stated blocker is verifiably GONE.

    None when the pin states no checkable blocker, which is not the same thing
    and must not read as one. A blocker nobody can check is a claim, and a claim
    is what this whole file exists to stop taking on faith.
    """
    cmd = pin.get("blocked_on_check")
    if not cmd:
        return None
    try:
        r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true",
                    help="exit 0 on pinned steps (the reviewer gate)")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args()

    plan = json.loads(PLAN.read_text())
    pins = json.loads(BASELINE.read_text()).get("not_yet", {})

    known = {s["id"] for s in plan["steps"]}
    unknown = sorted(set(pins) - known)
    if unknown:
        print(f"plan: baseline pins a step the plan does not define: {', '.join(unknown)}",
              file=sys.stderr)
        return 2

    results = []
    for step in plan["steps"]:
        failures = run_step(step)
        pin = pins.get(step["id"])
        cleared = blocker_cleared(pin) if pin else None
        if pin and not failures:
            state = "pin_stale"
        elif pin and cleared is True:
            state = "unblocked_and_unstarted"
        elif pin:
            state = "not_yet"
        elif failures:
            state = "fail"
        else:
            state = "pass"
        results.append({"id": step["id"], "title": step["title"], "state": state,
                        "failures": failures, "source": step.get("source", "")})

    if args.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        print(f"plan: {plan['source']}")
        print()
        for r in results:
            mark = {"pass": "ok     ", "not_yet": "NOT YET",
                    "fail": "FAIL   ", "pin_stale": "PIN STALE",
                    "unblocked_and_unstarted": "UNBLOCKED"}[r["state"]]
            print(f"  {mark}  {r['id']}  {r['title']}")
            if r["state"] == "pin_stale":
                print("           this step now passes. Remove its pin from "
                      "scripts/plan_landed_baseline.json.")
            if r["state"] == "unblocked_and_unstarted":
                pin = pins[r["id"]]
                print("           THE BLOCKER THIS PIN NAMES IS GONE, and the step is")
                print("           still not done. Stated blocker: "
                      f"{pin.get('blocked_on', 'unstated')}")
                print(f"           Re-checked by: {pin.get('blocked_on_check')}")
                print("           Do this step, or replace the pin with a blocker that")
                print("           is actually true. A stale blocker is how 2026-08-29 to")
                print("           2026-09-01 went missing.")
            for f in r["failures"]:
                print(f"           - {f}")
        print()
        n_pass = sum(1 for r in results if r["state"] == "pass")
        print(f"  {n_pass}/{len(results)} steps landed")

    hard = [r for r in results
            if r["state"] in ("fail", "pin_stale", "unblocked_and_unstarted")]
    if hard:
        return 1
    if args.quiet:
        return 0
    return 1 if any(r["state"] == "not_yet" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
