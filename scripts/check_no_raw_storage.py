#!/usr/bin/env python3
"""check_no_raw_storage.py ,  localStorage / sessionStorage have exactly one caller.

ADR 0025 (docs/decisions/0025-one-owner-per-server-entity.md) clause 4 + "The
check" item 2: every browser storage key is namespaced by the signed-in user
through `frontend/src/lib/storage.ts`, and that module is enforced as the ONLY
place in the app allowed to touch the native `localStorage` / `sessionStorage`
API. Anywhere else, a reference is a defect already measured once (19-22
un-namespaced keys, all global to the browser profile, so a second signed-in
user on one machine could read and clobber the first user's drafts, outbox,
scroll positions and device tokens) and would silently reopen it.

WHY THIS IS NOT A PLAIN GREP. This codebase narrates itself in comments more
than most, and several of those comments quote real code, backticks and all --
`useLogScroll.ts` has "`sessionStorage.setItem` is synchronous main-thread I/O"
inside a `//` comment, `Settings.tsx` has a comment containing the literal text
`localStorage["ws-theme"]`. A grep for the API name fails the build on prose
that explains the rule, which is exactly the kind of check that "fires
constantly, gets disabled" (the failure mode `check_one_definition.py`'s own
header warns about). So this strips comments and string/template-literal
contents before matching -- approximately, with a small hand-rolled scanner
rather than a real TS parser (the same tradeoff `check_no_hardcoding.sh` makes
with plain `git grep`), and then looks for the identifier in what's left.

WHAT IT SCANS. `frontend/src/**/*.{ts,tsx}` and `mobile/**/*.{ts,tsx}`,
excluding `*.test.ts*` (this repo's frontend test policy ,  pure logic modules,
no DOM harness ,  Read PLAN item 4) and the one allowed module itself.

Usage:
    python3 scripts/check_no_raw_storage.py            # human-readable
    python3 scripts/check_no_raw_storage.py --json      # machine-readable

Exit 0 = clean, 1 = violation(s).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
# Scan roots. `packages/ui/src` and `frontend/index.html` were ADDED 2026-08-17
# after a review found this check printing "clean" while eight raw calls sat
# outside its reach.
#
# WHY THAT MATTERED MORE THAN A MISSED FILE. This script does not merely stay
# silent on what it cannot see, it ASSERTS a fact: "storage.ts is the only
# caller of localStorage/sessionStorage". That statement was false of the tree.
# A gate that reports a clean it did not verify is worse than no gate, because
# a reader stops looking.
#
# `packages/ui` is not a side project: `frontend/package.json` depends on
# `@workshop/ui` and the kit compiles INTO the bundle, so its storage calls run
# in exactly the same browser profile as the app's.
#
# This is the same failure shape as the `job -> run` rename recorded in 97e0584:
# the SOURCE of the centralisation was measured and its DESTINATION was not.
_ROOTS = [
    _WORKSHOP / "frontend" / "src",
    _WORKSHOP / "mobile",
    _WORKSHOP / "packages" / "ui" / "src",
]

# Single files outside any root that still ship storage access.
_EXTRA_FILES = [_WORKSHOP / "frontend" / "index.html"]

# The files allowed to call the native API. Relative to _WORKSHOP, posix form.
#
# THERE ARE TWO, because the engine MOVED. `packages/ui/src/storage.ts` says so
# in its own first line: "Originated as Workshop's lib/storage.ts ... promoted
# here because the mechanism itself ... is general". The namespacing engine (the
# per-user prefix, the legacy-key migration, the lazy globalThis lookup) now
# lives in the kit; `frontend/src/lib/storage.ts` is the Workshop-side wrapper
# that registers the device-scoped exemptions. A module that namespaces browser
# storage has to touch browser storage, which is exactly why the old home was
# allowed, and the allowlist was simply never updated to follow the move.
#
# That is the failure shape this file's own header names: "the SOURCE of the
# centralisation was measured and its DESTINATION was not."
#
# MEASURED COST OF NOT FIXING IT, 2026-08-22: all 8 remaining violations were
# these implementation lines, so the check failed on every diff touching any
# frontend .tsx. Three unattended tickets did their work, committed it, and were
# then PARKED by the reviewer for a violation in a file they never opened
# (86f58aea, d73229b4, d2012fdc). A repo-wide gate that no ticket can ever pass
# does not protect the invariant, it just stops the queue.
_ALLOWED = {
    "frontend/src/lib/storage.ts",
    "packages/ui/src/storage.ts",
}

# ADR 0025 clause 4: "Keys that genuinely describe the device rather than the
# person (a station label on a shared kiosk, A THEME PREFERENCE ...) may stay
# global, but only by being listed in the namespacing module with a reason."
#
# For these two files that module cannot be the list. `packages/ui` is the
# platform kit and must not import out of `frontend/src`, and an inline
# bootstrap script in `index.html` cannot import a module at all. So the list
# lives here, which is the only place that can hold it, and every entry carries
# its reason so an exemption is a reviewed line rather than a silent hole.
_DEVICE_SCOPED_FILES = {
    "packages/ui/src/theme.ts":
        "ws-theme and ws-palette. Appearance of THIS browser, not of a person, "
        "and the ADR names a theme preference as the legitimate case. The kit "
        "is platform-owned and cannot import frontend/src/lib/storage.ts.",
    "frontend/index.html":
        "The pre-React bootstrap reads ws-theme/ws-palette to paint before "
        "first render (a namespaced read is impossible there: no user is known "
        "yet), and sessionStorage __sw_killed__ is a one-shot service-worker "
        "kill flag, which is device state for one tab.",
}

_TOKEN_RE = re.compile(r"\b(localStorage|sessionStorage)\b")


def _strip_comments_and_strings(text: str) -> str:
    """Replace comment and string/template-literal contents with spaces,
    preserving every newline and the overall length so line/column numbers in
    the output still point at the real source. Not a full TS lexer: template
    interpolations (`${...}`) are treated as inert like the rest of the
    literal, same simplification `check_no_hardcoding.sh` accepts elsewhere in
    this repo for a plain-text scan over source it does not fully parse."""
    out = []
    i, n = 0, len(text)
    state = "code"  # code | line_comment | block_comment | sq | dq | tpl
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if c == "/" and nxt == "/":
                state = "line_comment"
                out.append("  ")
                i += 2
                continue
            if c == "/" and nxt == "*":
                state = "block_comment"
                out.append("  ")
                i += 2
                continue
            if c == "'":
                state = "sq"
                out.append(" ")
                i += 1
                continue
            if c == '"':
                state = "dq"
                out.append(" ")
                i += 1
                continue
            if c == "`":
                state = "tpl"
                out.append(" ")
                i += 1
                continue
            out.append(c)
            i += 1
            continue
        if state == "line_comment":
            if c == "\n":
                state = "code"
                out.append(c)
            else:
                out.append(" ")
            i += 1
            continue
        if state == "block_comment":
            if c == "*" and nxt == "/":
                state = "code"
                out.append("  ")
                i += 2
                continue
            out.append(c if c == "\n" else " ")
            i += 1
            continue
        if state in ("sq", "dq", "tpl"):
            close = {"sq": "'", "dq": '"', "tpl": "`"}[state]
            if c == "\\" and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            if c == close:
                state = "code"
                out.append(" ")
                i += 1
                continue
            out.append(c if c == "\n" else " ")
            i += 1
            continue
    return "".join(out)


def _iter_files() -> list[Path]:
    files: list[Path] = []
    for root in _ROOTS:
        if not root.is_dir():
            continue
        for ext in ("*.ts", "*.tsx"):
            for p in root.rglob(ext):
                if "node_modules" in p.parts:
                    continue
                name = p.name
                if ".test." in name or name.endswith(".test.ts") or name.endswith(".test.tsx"):
                    continue
                files.append(p)
    for f in _EXTRA_FILES:
        if f.is_file():
            files.append(f)
    return files


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    rel = path.relative_to(_WORKSHOP).as_posix()
    if rel in _ALLOWED or rel in _DEVICE_SCOPED_FILES:
        return []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    stripped = _strip_comments_and_strings(text)
    hits: list[tuple[int, str, str]] = []
    for m in _TOKEN_RE.finditer(stripped):
        line_no = stripped.count("\n", 0, m.start()) + 1
        line_start = stripped.rfind("\n", 0, m.start()) + 1
        line_end = stripped.find("\n", m.start())
        if line_end == -1:
            line_end = len(text)
        original_line = text[line_start:line_end].strip()
        hits.append((line_no, m.group(1), original_line))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    scanned = sorted(_iter_files())
    violations = []
    for path in scanned:
        for line_no, token, snippet in scan_file(path):
            violations.append({
                "file": path.relative_to(_WORKSHOP).as_posix(),
                "line": line_no,
                "token": token,
                "snippet": snippet,
            })

    if args.json:
        print(json.dumps({
            "status": "violation" if violations else "ok",
            "violations": violations,
        }, indent=2))
        return 1 if violations else 0

    if not violations:
        # Say exactly what was checked. The previous wording claimed storage.ts
        # was "the only caller", which was false of the tree and is the reason
        # the scan roots were widened: a gate that overstates its own result
        # stops anyone looking further.
        print(f"check_no_raw_storage: clean over {len(scanned)} file(s) in "
              "frontend/src, mobile, packages/ui/src and frontend/index.html")
        # Printed from _ALLOWED rather than hardcoded: this script's whole
        # argument is that a gate must not assert a fact it has not checked,
        # and naming one owner while enforcing two was how the list and the
        # claim drifted apart in the first place.
        for _owner in sorted(_ALLOWED):
            print(f"    namespaced caller: {_owner}")
        for rel, why in sorted(_DEVICE_SCOPED_FILES.items()):
            print(f"    device-scoped, exempt: {rel}")
            print(f"        {why}")
        return 0

    print(f"check_no_raw_storage: {len(violations)} reference(s) to localStorage/sessionStorage "
          "outside frontend/src/lib/storage.ts (ADR 0025 clause 4)")
    for v in violations:
        print(f"    {v['file']}:{v['line']}  ({v['token']})  {v['snippet']}")
    print("\nRoute the access through userStorage.local / userStorage.session "
          "(frontend/src/lib/storage.ts) instead. If this key genuinely describes the "
          "device rather than the person, add it to DEVICE_SCOPED there, with a reason, "
          "and call it through the module anyway, since the module is still the only caller.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
