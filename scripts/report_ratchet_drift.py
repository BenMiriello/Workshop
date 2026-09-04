#!/usr/bin/env python3
"""Run the ratchets BARE and report any that the tree has drifted past.

WHY THIS EXISTS. Every ratchet here is a checker plus a pinned baseline whose
stated rule is that the number only goes down. `reviewer.sh` runs each one in
DELTA mode on purpose (`_delta_check`, and the argument at reviewer.sh:545-552):
a run is not blamed for debt that existed at its own base commit, or inherited
debt would block every unrelated change. That is correct and is not touched here.

The hole it leaves is that reviewer.sh only runs on AUTONOMOUS work, so an
attended commit is measured by nothing, and the ABSOLUTE count is read only when
a person chooses to run the bare script. On 2026-08-25 commit b9fa2078 took
check_no_app_names from 302 to 303 against a baseline that may only go down; the
bare gate failed from that moment and it was found by an audit a day later
rather than by any gate. Two more silent upward drifts landed in one day on
2026-09-04.

WHAT IT DOES NOT DO. It never fails a commit and nothing in a commit path calls
it. It exits 0 whether or not it finds drift (`--exit-code` opts into a non-zero
status for a caller that wants one), it re-pins nothing, and it is not a git
hook. It writes a receipt and prints a summary; the receipt is the surface.

WHICH CHECKERS. The roster is READ OUT OF reviewer.sh -- every `_delta_check`
call with a baseline file. One declaration, so a checker wired into the reviewer
is scheduled here without anyone remembering to add it twice, and a checker
removed there stops being reported here.

WHEN THE DRIFT STARTED. A checker that fails says how much debt there is now,
not when it appeared. So each run carries forward the previous receipt's
`first_seen` for a checker that was already failing, and stamps the current
commit for one that has just started. Run on a schedule, that names the drifting
commit to within one interval, which is the whole point.

    python3 scripts/report_ratchet_drift.py            # run, write the receipt
    python3 scripts/report_ratchet_drift.py --json     # the receipt on stdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent

# `_delta_check VARNAME "script" "baseline"` — the reviewer's own wiring.
_DELTA_CALL = re.compile(
    r'^\s*_delta_check\s+\w+\s+"([^"]+)"(?:\s+"([^"]+)")?', re.M)

# EXCLUDED FOR COST, not because it does not matter. check_fresh_clone.sh clones
# HEAD into a temp dir, deletes the app source and runs a frontend build: minutes
# of CPU and disk, where every other checker here is under ten seconds. A pass
# that expensive would not be run often enough to catch a drift within a day,
# which is the requirement. It stays a deliberate, on-demand command.
_TOO_EXPENSIVE = {"check_fresh_clone.sh"}

# Where the receipt lands. `.runtime` is instance state and is not tracked, which
# is right for a measurement of one checkout at one moment.
RECEIPT = Path(os.environ.get("WORKSHOP_RUNTIME", str(_ROOT / ".runtime"))) / "ratchet-drift.json"

# A drifted checker's own words are the report. Each one already prints its
# metric and its old and new counts in its own vocabulary, so quoting it beats
# re-deriving the numbers from the baseline file in a second format that would
# then have to be kept in step with twelve checkers.
_OUTPUT_TAIL_LINES = 12


def roster() -> list[tuple[str, str]]:
    """(script, baseline) for every ratchet the reviewer delta-checks."""
    try:
        src = (_SCRIPTS / "reviewer.sh").read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    for script, baseline in _DELTA_CALL.findall(src):
        if not baseline or script in _TOO_EXPENSIVE:
            continue
        if not (_SCRIPTS / script).exists() or not (_SCRIPTS / baseline).exists():
            continue
        if (script, baseline) not in out:
            out.append((script, baseline))
    return out


def _commit() -> dict:
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=_ROOT, capture_output=True,
                                  text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"sha": git("rev-parse", "--short", "HEAD"),
            "subject": git("log", "-1", "--format=%s"),
            "at": git("log", "-1", "--format=%cI")}


def _run_bare(script: str, *, timeout: float) -> tuple[bool, str]:
    """Run one checker with NO --since, i.e. against the pinned baseline for the
    whole tree. Returns (passed, the tail of what it said)."""
    runner = "bash" if script.endswith(".sh") else sys.executable
    try:
        proc = subprocess.run([runner, str(_SCRIPTS / script)], cwd=_ROOT,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"{script} did not finish within {timeout}s"
    except OSError as e:
        return False, f"{script} could not be started: {e}"
    said = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    return proc.returncode == 0, "\n".join(said[-_OUTPUT_TAIL_LINES:])


def _receipt_age() -> float | None:
    """How many seconds since the receipt was written, or None if there is none.

    Read from the receipt's own `generated_at` rather than the file's mtime: the
    receipt is what a reader trusts, and a copy or a checkout would carry a
    stat time that has nothing to do with when the tree was measured."""
    stamp = _previous().get("generated_at")
    try:
        written = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - written).total_seconds()


def _previous() -> dict:
    try:
        return json.loads(RECEIPT.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def measure(*, timeout: float = 300.0) -> dict:
    """Run every scheduled ratchet bare and build the receipt."""
    commit = _commit()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    was = {c.get("script"): c for c in (_previous().get("checks") or [])}
    checks: list[dict] = []
    for script, baseline in roster():
        ok, said = _run_bare(script, timeout=timeout)
        entry = {"script": script, "baseline": baseline, "ok": ok, "said": said}
        if not ok:
            prior = was.get(script) or {}
            # Already failing last time: keep the commit where it started, which
            # is the one an operator actually wants named. Newly failing: it
            # started somewhere in the window since the previous run, and HEAD is
            # the closest honest answer to that.
            entry["first_seen"] = (prior.get("first_seen")
                                   if not prior.get("ok", True) else None) or commit
        checks.append(entry)
    drifted = [c for c in checks if not c["ok"]]
    return {"generated_at": now, "commit": commit, "checks": checks,
            "drifted": [c["script"] for c in drifted]}


def write_receipt(receipt: dict) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECEIPT.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    tmp.replace(RECEIPT)


def summary(receipt: dict) -> str:
    checks = receipt.get("checks") or []
    if not checks:
        return "report_ratchet_drift: no delta-checked ratchet found in reviewer.sh"
    drifted = [c for c in checks if not c.get("ok")]
    if not drifted:
        return (f"report_ratchet_drift: all {len(checks)} ratchets are at or "
                f"below their pinned baseline")
    lines = [f"report_ratchet_drift: {len(drifted)} of {len(checks)} ratchets "
             f"have DRIFTED past their pinned baseline"]
    for c in drifted:
        seen = c.get("first_seen") or {}
        lines.append(f"\n  {c['script']} (pinned in {c['baseline']})")
        lines.append(f"    first seen at {seen.get('sha') or '?'} "
                     f"{seen.get('subject') or ''}".rstrip())
        for line in (c.get("said") or "").splitlines():
            lines.append(f"    | {line}")
    lines.append(f"\nReceipt: {RECEIPT}")
    lines.append("A ratchet baseline only goes down. Fix the regression; do not "
                 "re-pin it to pass.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="report_ratchet_drift")
    ap.add_argument("--json", action="store_true", help="print the receipt")
    ap.add_argument("--timeout", type=float, default=300.0,
                    help="per-checker wall limit in seconds")
    ap.add_argument("--exit-code", action="store_true",
                    help="exit 1 when a ratchet has drifted (default: always 0, "
                         "because this must never fail an attended commit)")
    ap.add_argument("--if-older-than", type=float, default=0.0, metavar="SECONDS",
                    help="do nothing when the existing receipt is younger than "
                         "this. What the backend's scheduled pass passes, so the "
                         "several restarts this tree sees in a day re-read the "
                         "receipt instead of re-measuring the whole tree.")
    args = ap.parse_args(argv)

    if args.if_older_than > 0:
        fresh = _receipt_age()
        if fresh is not None and fresh < args.if_older_than:
            print(f"report_ratchet_drift: receipt is {int(fresh)}s old "
                  f"(< {int(args.if_older_than)}s); nothing measured")
            return 0

    receipt = measure(timeout=args.timeout)
    write_receipt(receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True) if args.json
          else summary(receipt))
    return 1 if (args.exit_code and receipt["drifted"]) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
