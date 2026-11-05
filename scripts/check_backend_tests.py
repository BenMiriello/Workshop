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

`--since REV` makes the verdict about the CHANGE rather than about the tree. A
failure outside the baseline is only a regression if it does not also fail at
REV: the failing ids are re-run, only those, in a throwaway checkout of REV, and
the ones that fail there too are reported as already failing and do not gate. A
test that passes at REV and fails now still fails the check, so nothing a change
breaks is forgiven. The suite is still run whole against the tree: a change
anywhere can break a test anywhere, which is the point of running it.

WHY: on 2026-09-30 a one-line model-list change was blocked on 43 tests that
failed identically before it, because the pinned baseline had drifted behind
the tree and the check had no way to tell a failure the change caused from one
it merely stood next to.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _backend_tests_base import failing_at  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BASELINE = Path(__file__).resolve().parent / "backend_tests_baseline.json"

# `FAILED tests/test_x.py::test_y - AssertionError...` -- the id is everything
# before the first " - ", which is where pytest starts the message.
_FAILED = re.compile(r"^FAILED (\S+)")
_ERROR = re.compile(r"^ERROR (\S+)")
_SUMMARY = re.compile(r"(\d+) passed")
# `ISOLATION <what> <path>` -- tests/instance_isolation.py found the session
# wrote this installation's real state. The id is the path written.
_ISOLATION = re.compile(r"^ISOLATION (\S+) (\S+)")


def _suite_env() -> dict[str, str]:
    """The environment a suite run gets; the reasons for each choice follow."""
    # REENTRY SENTINEL, and it is not optional.
    #
    # `tests/test_change_gates.py` executes a REAL `reviewer.sh` (deliberately:
    # its whole point is that the gate's output is checked against a real run).
    # Wiring this check into reviewer.sh therefore closed a loop -- reviewer runs
    # the suite, the suite runs reviewer, that reviewer runs the suite -- and the
    # suite went from finishing in 198s to not finishing at all. Caught the same
    # day it was introduced, by the same "run it to the hang and read the last
    # test it started" method that found the previous one.
    #
    # The nested reviewer sees this variable and skips its backend-tests block,
    # so recursion stops at depth one and that test still exercises everything
    # else the gate does.
    env = dict(os.environ, WORKSHOP_BACKEND_TESTS_RUNNING="1")
    # THE SUITE GETS THE DATABASE CREDENTIALS, NOTHING ELSE OF THIS INSTALL.
    # A gate run from a plain shell had no WORKSHOP_DATABASE_URL, so every DB
    # test failed here and passed under the service. secrets.env is where that
    # URL lives. instance.env is deliberately NOT loaded: it pins this
    # installation's paths and flags, and tests like test_paths_are_portable
    # exist to prove the platform does not depend on any one layout. Nor is
    # db-admin.env, the owner credential the backend is never given. A value
    # already in the environment wins, as it does for db/script_env.
    sys.path.insert(0, str(BACKEND))
    from db import script_env
    # NOT LOADING IT IS NOT ENOUGH INSIDE A CHAT TURN. A turn inherits the
    # backend's environment, which already holds every instance.env value, so
    # run from a turn the suite saw this installation's paths pinned and 42
    # path and maker tests failed as "regressions" that were the same failures
    # at the base commit (measured 2026-10-02). Drop whatever it declares.
    try:
        pinned = script_env.parse_env_file(
            (script_env.config_dir() / "instance.env").read_text())
    except (OSError, UnicodeDecodeError):
        pinned = {}
    for key in pinned:
        env.pop(key, None)
    try:
        text = (script_env.config_dir() / "secrets.env").read_text()
    except (OSError, UnicodeDecodeError):
        text = ""
    for key, value in script_env.parse_env_file(text).items():
        env.setdefault(key, value)
    return env


def run_pytest(cwd: Path, targets: list[str], timeout: int) -> str:
    py = BACKEND / ".venv" / "bin" / "python3"
    if not py.exists():
        raise FileNotFoundError(py)
    proc = subprocess.run(
        [str(py), "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider"],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
        env=_suite_env(),
    )
    return proc.stdout + proc.stderr


def parse_results(out: str) -> tuple[set[str], set[str], int]:
    failed, errored = set(), set()
    for line in out.splitlines():
        m = _FAILED.match(line)
        if m:
            failed.add(m.group(1))
            continue
        m = _ERROR.match(line)
        if m:
            errored.add(m.group(1))
            continue
        m = _ISOLATION.match(line)
        if m:
            failed.add(f"instance_isolation::{m.group(2)}")
    passed = 0
    for m in _SUMMARY.finditer(out):
        passed = max(passed, int(m.group(1)))
    return failed, errored, passed


def run_suite(timeout: int) -> tuple[set[str], set[str], int, str]:
    """(failed ids, errored ids, passed count, raw tail)."""
    out = run_pytest(BACKEND, ["tests/"], timeout)
    failed, errored, passed = parse_results(out)
    return failed, errored, passed, out[-2000:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--since", default=None,
                    help="forgive new failures that also fail at this revision")
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
    inherited: list[str] = []
    if new and args.since:
        at_base = failing_at(args.since, new, ROOT, args.timeout,
                            run_pytest, parse_results)
        if at_base is not None:
            inherited = [t for t in new if t in at_base]
            new = [t for t in new if t not in at_base]

    if not args.quiet:
        print(f"backend_tests: {passed} passed, {len(failed)} failed "
              f"({len(known)} known), {len(errored)} errored (environmental)")
        for t in fixed:
            print(f"  FIXED (drop from baseline): {t}")
        for t in inherited:
            print(f"  ALREADY FAILING at {args.since} (not this change): {t}")
        for t in new:
            print(f"  NEW FAILURE: {t}")

    if new:
        print(f"backend_tests: {len(new)} test(s) newly failing. This is a "
              "regression, not debt.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
