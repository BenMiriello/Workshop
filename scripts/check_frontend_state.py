#!/usr/bin/env python3
"""One state mechanism for server reads. The old ones only go down. Measured, ratcheted.

Rule: a new server read in `frontend/src/` goes through the query layer
(`frontend/src/lib/queryClient.ts`). `usePoll` and hand-rolled polling
`setInterval`s are the LEGACY mechanisms; their counts may only fall.

WHY THIS EXISTS. `@tanstack/react-query` was adopted for six chat reads
(docs/decisions/0025-one-owner-per-server-entity.md). Days later another agent
built the dashboard and wrote FOUR BRAND-NEW `usePoll` call sites
(`components/dashboard/NeedsYouCard.tsx`, `RunningCard.tsx` x2,
`useThreads.ts`), because both mechanisms were live and nothing made the old
one un-choosable. That is the whole failure this file exists to stop: not the
43 inherited sites, but the 44th. The operator's instruction was "don't keep a
deprecated or forgotten old state system"; a ratchet is how this repo makes an
old path un-choosable while the existing debt is frozen rather than fixed.

THE SPEC. This implements clause 3 of "The check" in ADR 0025 -- "A polling
`setInterval` outside `usePoll`" -- generalised to cover `usePoll` ITSELF, which
the ADR could not do because at the time it was written `usePoll` was the
destination. It is not any more. The ADR's closing rationale is the design of
this file verbatim: "the check must be addable to the gate on day one, with the
existing violations frozen rather than fixed, or it will not be added at all."
`check_one_definition.py` went from 4 to 0 in a day because it could fail
immediately. This one cannot, so it ratchets.

WHAT IS COUNTED, and why exactly two numbers:

  usepoll_sites       call sites of `usePoll(` under frontend/src. The hook
                      itself lives in `packages/ui/src/usePoll.ts` and is NOT
                      counted: the kit is a general-purpose library and
                      retiring the export is a separate breaking MINOR bump.
                      What this number gates is Workshop CHOOSING it.

  raw_poll_intervals  `setInterval(` / `window.setInterval(` under frontend/src
                      whose timer reaches the network, i.e. every site not in
                      `_TIMER_ALLOW` below. Four today.

TIMERS ARE NOT ALL POLLS, so there is one allowlist. ADR 0025 clause 3 says a
`setInterval` trips the rule only when its callback "transitively reaches the
generated client", and names the exempt kinds: retry, audio meter, debounce,
one-shot, clock tick. A gate that failed a component for re-rendering a
relative timestamp once a second would fire constantly and then get disabled --
the failure mode `check_one_definition.py`'s header warns about. So each
non-poll timer is an entry in `_TIMER_ALLOW` with a one-line reason, which
makes an exemption a diff someone reviews (the same idiom as
`check_no_raw_storage.py`'s allowed-callers list).

NO TRANSITIVE ANALYSIS, deliberately. Classification is by hand, recorded in
the allowlist, not inferred by chasing callbacks through a TS import graph. The
sixteen sites were read individually and split 4/12 (see the reasons below). An
approximate reachability walk would be wrong in BOTH directions on this tree --
`AgentBuild.tsx` passes a `useCallback` defined 12 lines up, `Aviary.tsx` calls
a local `tick` that awaits two fetches, `capture.ts`'s watchdog calls a method
that touches only a `MediaStreamTrack` -- and a number that is wrong upward
gets disbelieved and then ignored, which is how the last gate died.

SO A NEW NON-POLL TIMER ALSO FAILS, until it is allowlisted. That is intended
and it is the cost of not guessing: the author of a new clock tick adds three
lines saying why it is not a poll. The FAIL message says so explicitly.

WHAT IT DOES NOT CATCH. A recursive `setTimeout` that re-arms itself is the
same defect and ADR 0025 clause 3 names it; it is not counted here because the
tree has none today and a pattern with nothing to match is a pattern nobody
maintains. Add it when one appears -- with a baseline bump and a reason.

PIN FROM A COMMITTED TREE. The counts come from `git ls-files`, so pinning with
work in progress records whatever the index happened to hold. Commit, then
--write-baseline. (Same trap that mis-pinned `no_app_names_baseline.json`.)

THE BASELINE DOES NOT SCAN ITSELF, and here it does not have to: the scan root
is `frontend/src/` and both this script and its baseline live under `scripts/`.
`check_no_app_names.py` needs an explicit self-exclusion because it scans
`scripts/`; noting the difference so nobody "fixes" the missing guard by
widening the root.

DELTA MODE (`--since <ref>`) is the same mechanism as `check_no_app_names.py`
and exists for the same reason: used as a review gate, a whole-tree count
against a static pin judges a worker on debt the worker inherited, which is
what stalled 2,066 loop jobs (see `scripts/_ratchet_delta.py`). With `--since`
the script runs the SAME whole-tree scan on a `git archive` extract of the base
ref AND on the working tree, with the SAME allowlist, and fails only on a count
that got worse between those two points. It still scans EVERY tracked file on
both sides: delta mode changes WHAT IS COMPARED, not what is measured, so
moving a `usePoll` into a file this diff never touched is still caught.

Usage:
    python3 scripts/check_frontend_state.py                  # report + gate on baseline
    python3 scripts/check_frontend_state.py --json
    python3 scripts/check_frontend_state.py --since HEAD     # gate mode: only THIS run's regression
    python3 scripts/check_frontend_state.py --write-baseline
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "frontend_state_baseline.json"

# The one scan root. `packages/ui` is deliberately outside it: that is where
# `usePoll` is DEFINED, and counting the definition would mean the number could
# never reach zero without a breaking change to a shared kit.
SCAN_PREFIX = "frontend/src/"

# The destination every new server read goes to. Named here so the FAIL message
# can point at it rather than at a doc the reader has to go find.
QUERY_LAYER = "frontend/src/lib/queryClient.ts"

_USEPOLL = re.compile(r"\busePoll\s*\(")
_SETINTERVAL = re.compile(r"\b(?:window\.)?setInterval\s*\(")

# Timers that are NOT polls: nothing in the callback reaches the network.
#
# TWO LISTS, and the split is a rule this repo enforces elsewhere. A platform
# file may be named here. An APP file may not: "no platform source file may
# name a specific app" (CLAUDE.md), measured by check_no_app_names.py, which
# gates the same reviewer run this checker does. Naming five app paths here
# raised that gate's literal count and failed the branch that added them.
#
# So an app's timer is exempted by a pattern that PROVES it is local -- a state
# setter that cannot reach the network -- with no path attached. That is the
# stronger form anyway: it says WHY the timer is not a poll instead of who owns
# it. `_TIMER_ALLOW` keeps (path, pattern) for platform files, where the path
# genuinely narrows.
#
# Both are matched against a WINDOW (the setInterval line plus the two after
# it), not that line alone, because the callback body that proves the point is
# usually on the next line: `timerRef.current = window.setInterval(() => {`
# says nothing by itself.
#
# Keyed by a line pattern rather than a line NUMBER on purpose -- a line number
# goes stale on the next edit above it and then silently exempts the wrong
# timer. `recorder.tsx` is why a pattern is needed at all: it holds one real
# poll (the 800ms live publish) and one clock, so a file-level exemption there
# would hide a poll.
_TIMER_ALLOW: tuple[tuple[str, str, str], ...] = (
    (
        "frontend/src/components/Chat.tsx",
        r"setNowMs\(Date\.now\(\)\)",
        "clock tick: re-renders the turn's elapsed-time indicator, already gated on visibility",
    ),
    (
        "frontend/src/components/VoiceSettings.tsx",
        r"setInterval\(tick, 500\)",
        "reads session.diagnostics(), an in-memory snapshot of the local voice client",
    ),
    (
        "frontend/src/components/chat/ContextModal.tsx",
        r"setNow\(Date\.now\(\)\)",
        "clock tick: ages the context-window timestamps in place",
    ),
    (
        "frontend/src/components/chat/work/TicketCard.tsx",
        r"setNow\(Date\.now\(\)\)",
        "clock tick: relative age on the ticket card",
    ),
    (
        "frontend/src/components/chat/work/WorkPanel.tsx",
        r"setNow\(Date\.now\(\)\)",
        "clock tick: relative age in the work panel",
    ),
    (
        "frontend/src/lib/voice/capture.ts",
        r"this\.checkDevice\(\)",
        "audio watchdog: checkDevice() inspects the local MediaStreamTrack only",
    ),
)

# Exempt ANYWHERE, because the pattern itself proves the callback is local
# state and cannot reach the network. No path, so an app's own timer needs no
# platform file to name it. Keep these narrow: each one names a specific state
# setter, not a shape like `window.setInterval`.
_TIMER_ALLOW_ANYWHERE: tuple[tuple[str, str], ...] = (
    (r"setTick\(\(x\) => x \+ 1\)", "re-render tick for a relative-time label"),
    (r"setElapsed\(", "elapsed-seconds counter; pure Date/number arithmetic"),
    (r"toLocaleTimeString", "wall-clock label"),
)

# The setInterval line plus the two after it: the callback body that proves a
# timer is local is usually not on the line that opens it.
_WINDOW = 3


def _allow_rules() -> list[tuple[str | None, re.Pattern[str], str]]:
    return (
        [(path, re.compile(pat), why) for path, pat, why in _TIMER_ALLOW]
        + [(None, re.compile(pat), why) for pat, why in _TIMER_ALLOW_ANYWHERE]
    )


def tracked_files(root: Path = ROOT) -> list[str]:
    """Repo-relative paths of every tracked file under `root`.

    For this checkout that is `git ls-files`. For a `git archive` EXTRACT of a
    base ref (delta mode) there is no `.git`, so the walk IS the answer: an
    archive holds exactly the tracked tree at that ref and nothing else. The two
    paths yield the same kind of list, which is what lets one `scan()` serve
    both sides. Note `.git` is a FILE in a worktree, so `.exists()` (not
    `.is_dir()`) is what makes this work from `branches/*`."""
    if (root / ".git").exists():
        out = subprocess.run(
            ["git", "ls-files", "--", SCAN_PREFIX],
            cwd=root, capture_output=True, text=True, check=True,
        ).stdout
        return [ln for ln in out.splitlines() if ln]
    return sorted(
        p.relative_to(root).as_posix()
        for p in (root / SCAN_PREFIX).rglob("*")
        if p.is_file()
    )


def _is_source(rel: str) -> bool:
    """A scanned file: TS/TSX under the scan root, tests excluded.

    Tests are excluded for the same reason `check_no_raw_storage.py` excludes
    them: this repo's frontend test policy is pure logic modules with no DOM
    harness, so a `usePoll(` in a test would be describing the mechanism, not
    choosing it."""
    if not rel.startswith(SCAN_PREFIX):
        return False
    if not rel.endswith((".ts", ".tsx")):
        return False
    return ".test." not in Path(rel).name


def scan(root: Path = ROOT) -> dict:
    """Count both legacy mechanisms over the tree under `root`.

    The allowlist is compiled from THIS file, once, and used for both sides of a
    delta -- same reason `check_no_app_names.py` passes one roster to both
    scans. Re-deriving it per tree would let the exemption set shrink underneath
    the base scan and report a regression that is really the allowlist moving."""
    rules = _allow_rules()
    polls: list[dict] = []
    intervals: list[dict] = []
    exempt: list[dict] = []

    for rel in tracked_files(root):
        if not _is_source(rel):
            continue
        try:
            body = (root / rel).read_text(errors="replace")
        except OSError:
            continue
        lines = body.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # A comment is prose, and prose is not a mechanism. Several files
            # here explain in `//` comments why the timer beside them is NOT a
            # poll; failing the build on the explanation is how a check earns
            # its own suppression.
            if stripped.startswith(("//", "*", "/*")):
                continue
            if _USEPOLL.search(line):
                polls.append({"file": rel, "line": i, "text": stripped})
            if _SETINTERVAL.search(line):
                window = "\n".join(lines[i - 1 : i - 1 + _WINDOW])
                why = next(
                    (
                        w for path, pat, w in rules
                        if (path is None or path == rel) and pat.search(window)
                    ),
                    None,
                )
                (exempt if why else intervals).append(
                    {"file": rel, "line": i, "text": stripped, **({"why": why} if why else {})}
                )

    # A stale exemption is REPORTED, never failed. Failing it would mean that
    # deleting an allowlisted timer -- unambiguously progress -- breaks the
    # build, which is the ratchet pointing backwards.
    matched = {(e["file"], e["why"]) for e in exempt}
    stale = [
        {"file": path or "(any)", "pattern": pat, "why": why}
        for path, pat, why in (
            [(p, pt, w) for p, pt, w in _TIMER_ALLOW]
            + [(None, pt, w) for pt, w in _TIMER_ALLOW_ANYWHERE]
        )
        if not any((path is None or f == path) and w == why for f, w in matched)
    ]

    return {
        "usepoll_sites": len(polls),
        "raw_poll_intervals": len(intervals),
        "usepoll": polls,
        "intervals": intervals,
        "exempt_timers": exempt,
        "stale_allowlist_entries": stale,
    }


def _new_sites(now: list[dict], before: list[dict]) -> list[dict]:
    """Sites present now and not at the base ref, matched by (file, source line).

    By TEXT and not by line number: a site that moved down the file because
    something was inserted above it is the same site, and naming it as "new"
    in a FAIL message sends the reader to fix code that did not change."""
    seen: dict[tuple[str, str], int] = {}
    for s in before:
        seen[(s["file"], s["text"])] = seen.get((s["file"], s["text"]), 0) + 1
    out = []
    for s in now:
        key = (s["file"], s["text"])
        if seen.get(key):
            seen[key] -= 1
        else:
            out.append(s)
    return out


_METRICS = ("usepoll_sites", "raw_poll_intervals")
_SITES = {"usepoll_sites": "usepoll", "raw_poll_intervals": "intervals"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument(
        "--since",
        metavar="REV",
        default=None,
        help="DELTA MODE: scan REV's tree as well, and fail only on a count that "
             "got worse between REV and the working tree. The pinned baseline is "
             "still reported. See this script's header.",
    )
    ap.add_argument(
        "--because",
        default="",
        help="Required when a pin RAISES a count. Recorded in the baseline and "
             "printed on every subsequent run, so an upward pin cannot be quiet.",
    )
    args = ap.parse_args()

    base = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    result = scan(ROOT)

    since_counts = None
    if args.since is not None:
        try:
            with extracted_tree(args.since, ROOT) as base_tree:
                since_counts = scan(base_tree)
        except (subprocess.CalledProcessError, OSError) as exc:
            print(f"check_frontend_state: cannot read ref {args.since!r}: {exc}",
                  file=sys.stderr)
            return 2

    if args.write_baseline:
        # AN UPWARD PIN MUST SAY WHY. A ratchet you re-pin whenever it fails is
        # not a ratchet. There is no legitimate upward move for either metric
        # here -- both are legacy mechanisms with a destination -- but the
        # escape hatch exists so that a genuine one (say, a kit change that
        # renames the hook and momentarily doubles the count) is recorded in
        # the baseline and reprinted on every run until it is paid off, rather
        # than being done quietly with a text editor.
        raised = {
            k: (base.get(k), result[k])
            for k in _METRICS
            if base.get(k) is not None and result[k] > base[k]
        }
        if raised and not args.because:
            for k, (was, now) in sorted(raised.items()):
                print(f"pin would RAISE {k}: {was} -> {now}")
            print("\nRefusing. Re-pin an increase with --because '<why>'.")
            return 1
        payload = {
            "_comment": (
                "Ratchet baseline for check_frontend_state.py. These counts may only "
                f"go DOWN. A new server read in frontend/src goes through the query "
                f"layer at {QUERY_LAYER}; `usePoll` and hand-rolled polling "
                "setInterval timers are the LEGACY mechanisms and both are on their "
                "way to zero. usepoll_sites counts call sites under frontend/src only "
                "(the hook's definition in packages/ui/src/usePoll.ts is a separate, "
                "breaking, MINOR-bump ticket). raw_poll_intervals counts setInterval "
                "timers that reach the network -- the non-poll timers (clock ticks, "
                "audio meters, debounces, watchdogs) are exempted one by one, with a "
                "reason each, in _TIMER_ALLOW inside the checker."
            ),
            "usepoll_sites": result["usepoll_sites"],
            "raw_poll_intervals": result["raw_poll_intervals"],
        }
        if raised:
            payload["raised"] = {
                k: {"from": was, "to": now, "because": args.because}
                for k, (was, now) in sorted(raised.items())
            }
        elif base.get("raised"):
            keep = {
                k: v for k, v in base["raised"].items()
                if result.get(k, 0) > v.get("from", 0)
            }
            if keep:
                payload["raised"] = keep
        BASELINE.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"baseline written: {BASELINE.relative_to(ROOT)}")
        for k, v in sorted(payload.get("raised", {}).items()):
            print(f"  RAISED {k} {v['from']} -> {v['to']}: {v['because']}")
        return 0

    if args.json:
        payload = dict(result)
        if since_counts is not None:
            payload["since_ref"] = args.since
            payload["since"] = {k: since_counts[k] for k in _METRICS}
        print(json.dumps(payload, indent=2))
        return 0

    print(f"frontend-state: legacy server-read mechanisms under {SCAN_PREFIX}")
    print(f"  usepoll_sites      {result['usepoll_sites']:>4}  "
          f"call sites of usePoll(")
    print(f"  raw_poll_intervals {result['raw_poll_intervals']:>4}  "
          f"setInterval timers that reach the network")
    print(f"  (exempt            {len(result['exempt_timers']):>4}  "
          f"non-poll timers, allowlisted in the checker)")

    for s in result["stale_allowlist_entries"]:
        print(f"  stale allowlist entry, nothing matches it any more: "
              f"{s['file']} /{s['pattern']}/ — {s['why']}")

    if not base:
        print("\nno baseline; run --write-baseline to pin one")
        return 0

    for key, info in sorted((base.get("raised") or {}).items()):
        print(f"  note {key} was RAISED {info['from']} -> {info['to']}: {info['because']}")

    judge_against = since_counts if since_counts is not None else base
    against = f"the base ref {args.since}" if since_counts is not None else "the pinned baseline"

    failed = False
    for key in _METRICS:
        was, now = judge_against.get(key), result[key]
        if was is None:
            continue
        if now > was:
            print(f"\nFAIL {key}: {was} -> {now} against {against}. "
                  f"The rule is that this only goes down.")
            if since_counts is not None:
                for s in _new_sites(result[_SITES[key]], since_counts[_SITES[key]]):
                    print(f"       new: {s['file']}:{s['line']}  {s['text'][:100]}")
            if key == "usepoll_sites":
                print(f"       A new server read goes through the query layer "
                      f"({QUERY_LAYER}), not usePoll.")
            else:
                print(f"       If this timer does NOT reach the network (clock tick, "
                      f"audio meter, debounce, watchdog), add it to _TIMER_ALLOW in "
                      f"scripts/check_frontend_state.py with a one-line reason. If it "
                      f"does, it is a server read: use the query layer ({QUERY_LAYER}).")
            failed = True
        elif now < was:
            print(f"  ok  {key} improved {was} -> {now} against {against}")

    if since_counts is not None and base:
        stale = [
            f"{k}: pinned {base[k]} -> now {result[k]}"
            for k in _METRICS
            if base.get(k) is not None and result[k] > base[k]
        ]
        if stale:
            print(f"\n  FYI {len(stale)} count(s) still past the PINNED baseline, "
                  f"inherited and not caused by this change (not gating):")
            for line in stale:
                print(f"      {line}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
