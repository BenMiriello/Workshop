#!/usr/bin/env python3
"""check_no_raw_storage.py — localStorage / sessionStorage have exactly one caller.

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
excluding `*.test.ts*` (this repo's frontend test policy — pure logic modules,
no DOM harness — Read PLAN item 4) and the one allowed module itself.

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
_ROOTS = [_WORKSHOP / "frontend" / "src", _WORKSHOP / "mobile"]

# The one file allowed to call the native API. Relative to _WORKSHOP, posix form.
_ALLOWED = {"frontend/src/lib/storage.ts"}

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
    return files


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    rel = path.relative_to(_WORKSHOP).as_posix()
    if rel in _ALLOWED:
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

    violations = []
    for path in sorted(_iter_files()):
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
        print("check_no_raw_storage: clean — frontend/src/lib/storage.ts is the only caller "
              "of localStorage/sessionStorage")
        return 0

    print(f"check_no_raw_storage: {len(violations)} reference(s) to localStorage/sessionStorage "
          "outside frontend/src/lib/storage.ts (ADR 0025 clause 4)")
    for v in violations:
        print(f"    {v['file']}:{v['line']}  ({v['token']})  {v['snippet']}")
    print("\nRoute the access through userStorage.local / userStorage.session "
          "(frontend/src/lib/storage.ts) instead. If this key genuinely describes the "
          "device rather than the person, add it to DEVICE_SCOPED there, with a reason, "
          "and call it through the module anyway — the module is still the only caller.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
