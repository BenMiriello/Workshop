#!/usr/bin/env python3
"""No NEW raw operating-system path in agent-facing text. Layer 4 ratchet of
docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md
(§9 "Layer 4 — ratchet"): a file, note, doc or run is addressed by a managed
ref (workshop:<kind>/... , resolved to an in-app href), never by a raw absolute
path. This guards the surfaces where a path would reach a human or a model AS
TEXT, so the managed-address system cannot silently regrow.

WHAT IT SCANS. A curated set of agent-facing surfaces only — deliberately NOT the
whole tree, because ordinary path-handling code all over the backend legitimately
spells absolute paths, and a checker that fires on those gets disabled
(check_one_definition.py's header states the rule):

    backend/services/chat_context.py      persona + the runtime briefing
    backend/services/build_streaming.py   assistant-facing text it builds
    backend/services/refs.py              a ref value must never BE a raw path
    frontend/src/components/chat/**.{ts,tsx}   chat UI copy

WHAT COUNTS. A token that starts with an absolute OS path prefix
(/home /Users /root /etc /var /opt /tmp, or the expanded WORKSHOP_ROOT) appearing
in one of those files, excluding an import/require specifier. Line numbers are NOT
part of the identity — the matched path snippet is — so unrelated edits that shift
lines do not churn the baseline.

WHY A PINNED BASELINE, NOT ZERO. The count is not zero today ON PURPOSE:
chat_context.py's runtime briefing still writes `Working directory (cwd): <abs>`
into a build agent's prompt, and that only goes away when Layer 1 puts the agent
in a scope-mounted namespace so its cwd is already the managed path. Failing on it
now would fail the reviewer for every unrelated change while Layer 1 is still
open. So the current set is pinned in no_raw_paths_baseline.json and this fails
only on a NEW raw path. When Layer 1 lands and the briefing stops emitting the
cwd, re-run --write-baseline; the baseline shrinks toward zero and this becomes an
absolute gate.

Usage:
    python3 scripts/check_no_raw_paths.py                # exit 1 on a new raw path
    python3 scripts/check_no_raw_paths.py --json
    python3 scripts/check_no_raw_paths.py --write-baseline
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_WORKSHOP = Path(__file__).resolve().parents[1]
_BASELINE_PATH = Path(__file__).resolve().parent / "no_raw_paths_baseline.json"

# The agent-facing surfaces. Explicit backend files, plus a glob for chat UI copy.
_BACKEND_FILES = (
    "backend/services/chat_context.py",
    "backend/services/build_streaming.py",
    "backend/services/refs.py",
)
_FRONTEND_GLOBS = ("frontend/src/components/chat/**/*.ts",
                   "frontend/src/components/chat/**/*.tsx")

# Absolute OS path prefixes that name a real location on the host. WORKSHOP_ROOT
# is added at runtime so an in-tree literal is caught too, not only /home & co.
_PREFIXES = ["/home/", "/Users/", "/root/", "/etc/", "/var/", "/opt/", "/tmp/"]

# A path token: a prefix followed by more path, stopping at whitespace, a quote,
# or a closing bracket. This is the snippet recorded as the offense identity.
def _token_re() -> re.Pattern[str]:
    root = str(_WORKSHOP)
    prefixes = _PREFIXES + [root + "/"]
    alt = "|".join(re.escape(p) for p in prefixes)
    return re.compile(r"(?:" + alt + r")[^\s\"'`)\]<>]*")


# Lines that are an import/require specifier — a module path, not agent text.
_IMPORT_RE = re.compile(r"""^\s*(?:import\b|from\s+\S+\s+import\b|.*\brequire\s*\(|"""
                        r"""export\s+(?:\*|\{|default)\b)""")


def _scan_files() -> list[str]:
    """Every scanned file that exists, repo-relative posix."""
    out: list[str] = []
    for rel in _BACKEND_FILES:
        if (_WORKSHOP / rel).is_file():
            out.append(rel)
    for g in _FRONTEND_GLOBS:
        for p in sorted(_WORKSHOP.glob(g)):
            if not p.is_file():
                continue
            name = p.name
            # Test/spec/story files carry fixture paths (e.g. "/tmp/x") that are
            # source, not agent-facing copy — the same reason the state ratchet
            # excludes a tests/ tree.
            if any(s in name for s in (".test.", ".spec.", ".stories.")):
                continue
            out.append(p.relative_to(_WORKSHOP).as_posix())
    return sorted(set(out))


def _offenses() -> list[str]:
    """`<relfile> :: <path-snippet>` for every raw path token found in an
    agent-facing file, excluding import specifiers. De-duplicated and sorted, so
    the identity is (file, snippet) — never a line number."""
    tok = _token_re()
    found: set[str] = set()
    for rel in _scan_files():
        try:
            text = (_WORKSHOP / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if _IMPORT_RE.match(line):
                continue
            for m in tok.finditer(line):
                snippet = m.group(0).rstrip(".,;:!?)")
                found.add(f"{rel} :: {snippet}")
    return sorted(found)


def _load_baseline(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    allowed = data.get("allowed") if isinstance(data, dict) else None
    return sorted(allowed) if isinstance(allowed, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", default=str(_BASELINE_PATH))
    ap.add_argument("--write-baseline", action="store_true",
                    help="pin the current raw-path set as the allowlist and exit")
    args = ap.parse_args()

    baseline_path = Path(args.baseline)
    current = _offenses()

    if args.write_baseline:
        payload = {
            "allowed": current,
            "_note": ("raw OS paths in agent-facing text today; a NEW one not in "
                      "this list fails the ratchet. Shrinks toward zero once Layer "
                      "1 removes the runtime-briefing cwd. See "
                      "scripts/check_no_raw_paths.py."),
        }
        baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"check_no_raw_paths: wrote baseline to {baseline_path}: "
              f"{len(current)} entries")
        return 0

    allowed = _load_baseline(baseline_path)
    offenders = [p for p in current if p not in allowed]

    if args.json:
        print(json.dumps({
            "current": current, "allowed": allowed, "offenders": offenders,
        }, indent=2))
        return 1 if offenders else 0

    if offenders:
        print(f"check_no_raw_paths: {len(offenders)} new raw OS path(s) in "
              "agent-facing text:")
        for p in offenders:
            print(f"    {p}")
        print("\nA file/note/doc/run is addressed by a managed ref "
              "(workshop:<kind>/...), resolved to an in-app href, never a raw "
              "path. If this really must be a path literal in logic, it does not "
              "belong in an agent-facing surface; otherwise, deliberately re-pin "
              "with --write-baseline after review.")
        return 1

    print(f"check_no_raw_paths: no new raw path in agent-facing text "
          f"(baseline: {len(allowed)} allowlisted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
