#!/usr/bin/env python3
"""Fail when a component references a class name no stylesheet defines.

WHY THIS EXISTS. A `className` that no CSS rule matches renders UNSTYLED, and
nothing else in this repo notices: `tsc` sees a string, `check_design.sh` checks
for inline hex and emoji, and the test suite covers pure logic modules with no
DOM. So the defect is invisible to every gate and visible only on screen, which
is the worst combination available.

Measured 2026-08-22, the first time this ran: five live cases, including
`.ws-chat-card`, which `Chats.tsx` had always passed with a comment explaining
that it carries `min-width: 0` to stop a long snippet giving the page a
horizontal scroll on a phone. The rule was never written, so the class did
nothing and the bug its own comment described had been live the whole time.
Also `.ws-chip` on the Deploys page, which rendered five filter buttons with no
styling at all and no way to see which was active.

WHAT IT DELIBERATELY DOES NOT DO. It does not check the reverse direction (a
rule nobody uses). Dead CSS is cheap and removing it needs judgement about what
a page might set at runtime; an undefined class is always a defect.

Usage:
    python3 scripts/check_undefined_css.py          # gate at the pinned count
    python3 scripts/check_undefined_css.py --list   # show every offender
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "undefined_css_baseline.json"

# Source only. `dist/` holds built copies of the same rules and would mask a
# class whose SOURCE rule was deleted.
CSS_ROOTS = [ROOT / "frontend" / "src", ROOT / "packages" / "ui" / "src"]
TSX_ROOTS = [ROOT / "frontend" / "src", ROOT / "packages" / "ui" / "src"]

CLASS_RE = re.compile(r"\.([a-zA-Z][\w-]*)")
# `className="a b"` and `className={`a ${x}`}`. Anything more dynamic than that
# (a computed map, a cx() call with variables) is out of reach and is skipped
# rather than guessed at.
USE_RE = re.compile(r'className=(?:"([^"]*)"|\{`([^`]*)`\})')
INTERP_RE = re.compile(r"\$\{[^}]*\}")
NAME_RE = re.compile(r"[a-zA-Z][\w-]*$")


def defined_classes() -> set[str]:
    out: set[str] = set()
    for root in CSS_ROOTS:
        for f in root.rglob("*.css"):
            out |= set(CLASS_RE.findall(f.read_text(errors="ignore")))
    return out


def referenced_classes() -> dict[str, set[str]]:
    used: dict[str, set[str]] = defaultdict(set)
    for root in TSX_ROOTS:
        for f in root.rglob("*.tsx"):
            for m in USE_RE.finditer(f.read_text(errors="ignore")):
                raw = m.group(1) or m.group(2) or ""
                # A template hole leaves a FRAGMENT behind ("ws-x--" from
                # `ws-x--${kind}`). A fragment is not a class name and must not
                # be reported: it is the one false positive this check can
                # produce, and reporting it would train people to ignore it.
                had_hole = bool(INTERP_RE.search(raw))
                for cls in INTERP_RE.sub(" ", raw).split():
                    if not NAME_RE.fullmatch(cls):
                        continue
                    if had_hole and (cls.endswith("-") or cls.endswith("_")):
                        continue
                    used[cls].add(str(f.relative_to(ROOT)))
    return used


def main() -> int:
    defined = defined_classes()
    used = referenced_classes()
    missing = {c: sorted(v) for c, v in used.items() if c not in defined}

    if "--list" in sys.argv or missing:
        for cls in sorted(missing):
            print(f"  .{cls:34} {', '.join(missing[cls][:3])}")

    pinned = 0
    if BASELINE.exists():
        pinned = int(json.loads(BASELINE.read_text()).get("undefined", 0))

    n = len(missing)
    print(f"check_undefined_css: {n} referenced-but-undefined class(es) "
          f"({len(defined)} defined, {len(used)} referenced), pinned at {pinned}")
    if n > pinned:
        print("FAIL: a component references a class no stylesheet defines. It will "
              "render unstyled. Define the rule, or drop the class if it is dead.")
        return 1
    if n < pinned:
        print(f"note: down to {n}; lower the baseline in {BASELINE.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
