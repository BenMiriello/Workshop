#!/usr/bin/env python3
"""Every design doc, its declared status, and whether that status is a lie.

R1 in docs/platform-redesign/coherence-overhaul-2026-08-16.md: "A design doc
must record when it lands." The 2026-08-16 audit counted 62 docs in
docs/platform-redesign/, 22 with no status line at all, and three actively
misleading -- carrying a "NOT BUILT" banner for something that IS built
(primitive-roles-2026-07-18.md, resource-access-protocol-2026-07-16.md,
chat-scope-design-2026-07-18.md; a fourth, resource-access-interface-spec-
2026-07-18.md, made the same implicit claim by omission).

This script does two things:

  1. lists every doc under docs/platform-redesign/ with whatever status line
     it can find (a top-of-file "Status:" line, in any of the styles this repo
     actually uses -- plain, bulleted, or a bold blockquote), and flags the
     ones with none.
  2. for docs whose status paragraph claims "NOT BUILT" (or an equivalent --
     "no code has been written", "no broker exists"), checks a short table of
     concrete on-disk artifacts those specific claims dispute, and flags the
     ones where the artifact demonstrably exists.

(2) is deliberately NOT a generic "is this claim still true" engine -- that
is not a class of check that generalises without reading the doc, unlike
check_one_definition.py's enum scan, which is why THIS table names specific
docs and specific artifacts rather than trying to infer either. When a doc's
banner is corrected (this script's own first run drove four such fixes), it
stops matching the NOT-BUILT pattern and drops out of this report on its own
-- the table does not need to be edited to keep the report accurate.

Usage:
    python3 scripts/doc_status.py
    python3 scripts/doc_status.py --json
    python3 scripts/doc_status.py --no-status-only

Exit status is 0 always. Like field_occupancy.py, this measures; wiring a
threshold into a gate is reviewer.sh's decision, made separately.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable

_WORKSHOP = Path(__file__).resolve().parents[1]
_DOCS_DIR = _WORKSHOP / "docs" / "platform-redesign"

# Matches a status line in every style seen in this directory:
#   "Status: accepted"
#   "- Status: accepted"
#   "> **STATUS: DESIGN. NOT BUILT.**"
#   "**Status**: plan, being implemented."
# i.e. optional blockquote/bullet/emphasis markup, the word "status"
# (case-insensitive), optional closing emphasis markup, then a colon.
_STATUS_RE = re.compile(
    r"^\s*(?:[>\-*]\s*){0,2}\*{0,2}status\*{0,2}\s*:\s*(.*)$", re.IGNORECASE
)

# Phrases this repo's docs actually use to assert nothing has shipped yet.
_NOT_BUILT_RE = re.compile(
    r"not[\s-]*built|no code (?:has been|was) written|no broker exists",
    re.IGNORECASE,
)

_SCAN_LINES = 40  # the status line always lives in the opening block


def _grep(pattern: str, rel_path: str) -> bool:
    path = _WORKSHOP / rel_path
    try:
        return bool(re.search(pattern, path.read_text(errors="replace")))
    except OSError:
        return False


# Doc -> artifacts its NOT-BUILT-shaped banner disputes. Verified against the
# tree during the coherence-overhaul T6 pass (2026-08-17); see the banner
# correction notes added to each doc for the reasoning.
ARTIFACT_CHECKS: dict[str, list[tuple[str, Callable[[], bool]]]] = {
    "primitive-roles-2026-07-18.md": [
        ("role CHECK constraint (backend/db/models.py)",
         lambda: _grep(r'CheckConstraint\(.*role', "backend/db/models.py")),
        ("role write-set map (backend/services/agent_authority.py)",
         lambda: _grep(r"_ROLE_WRITES", "backend/services/agent_authority.py")),
    ],
    "resource-access-protocol-2026-07-16.md": [
        ("MediatedBroker (backend/services/broker.py)",
         lambda: _grep(r"class MediatedBroker", "backend/services/broker.py")),
        ("policy tiers (backend/services/policy.py)",
         lambda: _grep(r"^TIERS\s*=", "backend/services/policy.py")),
    ],
    "resource-access-interface-spec-2026-07-18.md": [
        ("MediatedBroker (backend/services/broker.py)",
         lambda: _grep(r"class MediatedBroker", "backend/services/broker.py")),
        ("policy tiers (backend/services/policy.py)",
         lambda: _grep(r"^TIERS\s*=", "backend/services/policy.py")),
    ],
    "chat-scope-design-2026-07-18.md": [
        ("chat_thread.scope column (backend/db/models.py)",
         lambda: _grep(r"scope:\s*Mapped\[str\]", "backend/db/models.py")),
        ("chat_thread.scope_locked column (backend/db/models.py)",
         lambda: _grep(r"scope_locked:\s*Mapped\[bool\]", "backend/db/models.py")),
    ],
}


def _status_block(text: str) -> tuple[str | None, str]:
    """(status text after the colon, or None; the paragraph it sits in)."""
    lines = text.splitlines()
    for i, line in enumerate(lines[:_SCAN_LINES]):
        m = _STATUS_RE.match(line)
        if not m:
            continue
        para = [line]
        j = i + 1
        while j < len(lines) and lines[j].strip():
            para.append(lines[j])
            j += 1
        return m.group(1).strip(), "\n".join(para)
    return None, ""


def scan() -> dict[str, dict]:
    report: dict[str, dict] = {}
    for path in sorted(_DOCS_DIR.glob("*.md")):
        text = path.read_text(errors="replace")
        status, para = _status_block(text)
        entry: dict = {
            "has_status": status is not None,
            "status": status,
        }
        if para and _NOT_BUILT_RE.search(para):
            entry["claims_not_built"] = True
            checks = ARTIFACT_CHECKS.get(path.name, [])
            built = [(label, fn()) for label, fn in checks]
            existing = [label for label, exists in built if exists]
            if existing:
                entry["misleading"] = True
                entry["artifacts_that_exist"] = existing
        report[path.name] = entry
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-status-only", action="store_true",
                     help="only list docs with no declared status")
    args = ap.parse_args()

    report = scan()
    no_status = sorted(n for n, e in report.items() if not e["has_status"])
    misleading = sorted(n for n, e in report.items() if e.get("misleading"))

    if args.json:
        print(json.dumps({
            "docs": report,
            "total": len(report),
            "no_status": no_status,
            "misleading": misleading,
        }, indent=2, sort_keys=True))
        return 0

    if not args.no_status_only:
        for name, entry in sorted(report.items()):
            if entry["has_status"]:
                shown = (entry["status"] or "")[:88]
                print(f"    {name:60} {shown}")
        print()

    print(f"=== {len(report)} docs in docs/platform-redesign/, "
          f"{len(no_status)} with no status line")
    if no_status:
        for name in no_status:
            print(f"    NO STATUS  {name}")

    if misleading:
        print(f"\n=== {len(misleading)} doc(s) claim NOT BUILT for something that IS built")
        for name in misleading:
            print(f"    MISLEADING  {name}")
            for label in report[name]["artifacts_that_exist"]:
                print(f"        exists: {label}")
    else:
        print("\n=== no doc's NOT-BUILT banner disputes a checked artifact that exists")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
