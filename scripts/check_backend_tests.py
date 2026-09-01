#!/usr/bin/env python3
"""The backend test suite, as a ratchet: known failures may only go down.

WHY THIS DID NOT EXIST UNTIL NOW. `reviewer.sh` has run mypy and never pytest,
and that was defensible for one concrete reason: the suite could not finish.
`pytest tests/` stopped at 32% and hung there, because a test called the real
`compact_thread`, which spawns a CLI turn that never returns under pytest. So
"wire pytest into the gate" was blocked on a bug nobody had located.

It is located and fixed (see tests/test_compact_uses_the_provider.py), the suite
completes in about three and a half minutes, and the reason not to gate on it is
gone. The cost of not gating was measured the same day: a response-shape rename
on 2026-08-17 left seven frontend call sites reading dead keys, and the Tickets
page showed "No tickets yet" over 188 open tickets for two weeks with nothing in
the repo able to notice.

A RATCHET, NOT A CLEAN BAR. There are real failures on the day this lands, and
blocking every future commit behind them would mean this check gets disabled
within a week -- which is how a gate becomes decoration. So the known set is
pinned by test id in `backend_tests_baseline.json`, and the rule is:

  - a failure NOT in the baseline fails this check. That is a regression.
  - a baseline failure that now passes is reported, and the baseline should be
    trimmed. `--update` does that.
  - the count may never be raised except deliberately, the same contract every
    other ratchet in scripts/ carries.

ERRORS ARE NOT FAILURES HERE. Roughly a hundred tests error at setup with
`WORKSHOP_DATABASE_ADMIN_URL is not set` -- that variable lives in a file the
backend service deliberately does not load, so those tests are environmental and
say nothing about the code. They are counted and reported, never gating. If the
admin URL IS present they run for real and any failure among them is an ordinary
failure like the rest.

Usage:
    check_backend_tests.py            run and compare against the baseline
    check_backend_tests.py --update   rewrite the baseline from this run
    check_backend_tests.py --quiet    exit code only

`--since REV` is accepted and ignored, so `_delta_check` in reviewer.sh can
call this the same way it calls every other checker. The suite is not a diff:
a change anywhere can break a test anywhere, which is the point of running it.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BASELINE = Path(__file__).resolve().parent / "backend_tests_baseline.json"

# `FAILED tests/test_x.py::test_y - AssertionError...` -- the id is everything
# before the first " - ", which is where pytest starts the message.
_FAILED = re.compile(r"^FAILED (\S+)")
_ERROR = re.compile(r"^ERROR (\S+)")
_SUMMARY = re.compile(r"(\d+) passed")


def run_suite(timeout: int) -> tuple[set[str], set[str], int, str]:
    """(failed ids, errored ids, passed count, raw tail)."""
    py = BACKEND / ".venv" / "bin" / "python3"
    if not py.exists():
        raise FileNotFoundError(py)
    proc = subprocess.run(
        [str(py), "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
        cwd=BACKEND, capture_output=True, text=True, timeout=timeout,
    )
    out = proc.stdout + proc.stderr
    failed, errored = set(), set()
    for line in out.splitlines():
        m = _FAILED.match(line)
        if m:
            failed.add(m.group(1))
            continue
        m = _ERROR.match(line)
        if m:
            errored.add(m.group(1))
    passed = 0
    for m in _SUMMARY.finditer(out):
        passed = max(passed, int(m.group(1)))
    return failed, errored, passed, out[-2000:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--since", default=None, help="accepted and ignored")
    args = ap.parse_args()

    try:
        failed, errored, passed, tail = run_suite(args.timeout)
    except FileNotFoundError as e:
        print(f"backend_tests: no interpreter at {e}; check unavailable", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("backend_tests: the suite did not finish. That is the failure this "
              "check exists to catch; find the hanging test with `-v` and read "
              "the last one it started.", file=sys.stderr)
        return 1

    if passed == 0:
        print("backend_tests: no tests reported as passing, which means the run "
              f"did not work rather than that everything failed.\n{tail}",
              file=sys.stderr)
        return 2

    if args.update:
        BASELINE.write_text(json.dumps(
            {"_comment": "Known-failing backend tests. A ratchet: entries may be "
                         "removed, never added without a deliberate decision. "
                         "See check_backend_tests.py.",
             "failures": sorted(failed)}, indent=2) + "\n")
        print(f"backend_tests: baseline written with {len(failed)} known failure(s)")
        return 0

    if not BASELINE.exists():
        print(f"backend_tests: no baseline at {BASELINE}; run with --update",
              file=sys.stderr)
        return 2

    known = set(json.loads(BASELINE.read_text()).get("failures") or [])
    new = sorted(failed - known)
    fixed = sorted(known - failed)

    if not args.quiet:
        print(f"backend_tests: {passed} passed, {len(failed)} failed "
              f"({len(known)} known), {len(errored)} errored (environmental)")
        for t in fixed:
            print(f"  FIXED (drop from baseline): {t}")
        for t in new:
            print(f"  NEW FAILURE: {t}")

    if new:
        print(f"backend_tests: {len(new)} test(s) newly failing. This is a "
              "regression, not debt.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
