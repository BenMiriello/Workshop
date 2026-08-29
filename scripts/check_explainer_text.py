#!/usr/bin/env python3
"""The application is not a place to leave notes for the operator.

THE RULE. No screen explains its own mechanism in prose. The interface is not
documentation and it is not a changelog: a paragraph under a control describing
how a feature ranks, a parenthetical teaching a file format, a help line
restating the label above it and then explaining what the value does downstream
-- all of it is developer commentary shipped as product copy.

WHY IT NEEDED A GATE. The operator has reported this at least three times. It
was the drift banner deleted on 2026-08-28; it was "no app copy addressed to a
developer"; and on 2026-08-28 it was two live strings on the Memory page, one
telling him how pinned notes rank and one telling him to write in Markdown. His
words: "the application itself is not a place for you to leave notes to me! how
many times and how many ways do i have to tell you to never ever do this
again?" Three reports and three one-off deletions is a rule nobody is enforcing,
which is the same position `check_prompt_layers.py` was written for, and this is
modelled on it deliberately.

WHAT IS NOT AN EXPLAINER, and the distinction the whole check turns on:

  an EMPTY STATE names an ACTION       "Drop a file into any chat to see it here"
  an ERROR says what just happened     "That didn't save"
  a STATE line reports a value         "Showing the first 100 of 340"
  a LABEL names its own control        "Hook", "Goes in"

  an EXPLAINER describes a MECHANISM   "Pinned notes, then preferences and
                                        decisions, rank first in what gets read
                                        into every chat in this scope"

Design rule 9 REQUIRES an instructional hint on an empty state, so a gate that
caught those would be arguing with the design system. It does not: the exempt
list in the declaration is the props the kit passes an empty state's hint
through, and interpolated text is skipped entirely because a string carrying a
value is reporting state rather than teaching.

THE DECLARATION IS THE FACTS. Surfaces, patterns and exemptions live in
explainer_rules.json; the floor lives in explainer_baseline.json, separately, so
that check_ratchets_not_loosened.py (which globs *baseline*.json) can see the
floor being raised. This script holds no fact about the tree.

Usage:
    python3 scripts/check_explainer_text.py             # exit 1 above the floor
    python3 scripts/check_explainer_text.py --json
    python3 scripts/check_explainer_text.py --list
    python3 scripts/check_explainer_text.py --since REV # fail only on a NEW one
    python3 scripts/check_explainer_text.py --write-baseline --because "..."
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ratchet_delta import extracted_tree  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "scripts" / "explainer_rules.json"
BASELINE = ROOT / "scripts" / "explainer_baseline.json"

# A JSX text run: everything between `>` and the next `<`. Interpolation is
# detected rather than parsed -- a run containing `{` is state, not prose.
_P_OPEN = re.compile(r"<p\b[^>]*>", re.S)
_ATTR = re.compile(r'\b(?P<name>[a-zA-Z-]+)\s*=\s*"(?P<value>[^"]*)"')


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _text_after(src: str, at: int) -> str:
    """The literal text run following an opening tag, or "" if it is not one."""
    end = src.find("<", at)
    return src[at:end] if end > at else ""


def _is_prose(text: str, min_chars: int) -> bool:
    """A static sentence long enough to be an explanation.

    `{` anywhere disqualifies it: that is interpolation, and an interpolated
    string is reporting a value. A run with no letters is markup whitespace.
    """
    if "{" in text or "}" in text:
        return False
    flat = " ".join(text.split())
    if len(flat) < min_chars:
        return False
    # Needs a sentence's worth of words, and a terminator, or it is a label that
    # happens to be long.
    return flat.count(" ") >= 8 and flat.rstrip().endswith((".", "?", "!"))


def _exempt_line(line: str, exempt: dict) -> bool:
    """A line that is an empty state's hint, an error's detail, or a label."""
    if any(f"{a}=" in line for a in exempt.get("attrs", ())):
        return True
    return any(c in line for c in exempt.get("components", ()))


def scan(root: Path = ROOT) -> list[dict]:
    rules = _load(RULES)
    pats = rules.get("patterns", {})
    exempt = rules.get("exempt", {})
    files: list[Path] = []
    for surface in rules.get("surfaces", []):
        base = root / surface["path"]
        if base.is_dir():
            files.extend(sorted(base.rglob("*.tsx")))
    hits: list[dict] = []
    for f in files:
        try:
            src = f.read_text()
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        # Skip a test file: its strings are fixtures, not copy.
        if ".test." in f.name:
            continue

        para = pats.get("explainer-paragraph")
        if para:
            for m in _P_OPEN.finditer(src):
                run = _text_after(src, m.end())
                if not _is_prose(run, para.get("min_chars", 80)):
                    continue
                line_no = src[: m.start()].count("\n") + 1
                # The opening tag's own line carries the props that would exempt
                # it (a Notice's detail, a StateBlock's hint).
                whole_line = src.splitlines()[line_no - 1]
                if _exempt_line(whole_line, exempt):
                    continue
                hits.append({"file": rel, "line": line_no,
                             "pattern": "explainer-paragraph",
                             "text": " ".join(run.split())[:120]})

        for name, rule in pats.items():
            attr = rule.get("attr")
            if not attr:
                continue
            for m in _ATTR.finditer(src):
                if m.group("name") != attr:
                    continue
                value = m.group("value")
                line_no = src[: m.start()].count("\n") + 1
                if rule.get("regex"):
                    if not re.search(rule["regex"], value, re.I):
                        continue
                else:
                    if not _is_prose(value, rule.get("min_chars", 60)):
                        continue
                hits.append({"file": rel, "line": line_no, "pattern": name,
                             "text": value[:120]})
    hits.sort(key=lambda h: (h["file"], h["line"], h["pattern"]))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--since", metavar="REV",
                    help="fail only on offenses this working tree ADDED over REV")
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--because", default="")
    args = ap.parse_args()

    hits = scan()
    base = _load(BASELINE)
    floor = int(base.get("allowed", 0))

    if args.write_baseline:
        if not args.because.strip():
            print("refusing to pin a baseline with no --because", file=sys.stderr)
            return 2
        BASELINE.write_text(json.dumps({
            "_note": base.get("_note", ""),
            "allowed": len(hits),
            "because": args.because.strip(),
        }, indent=2) + "\n")
        print(f"baseline written: {len(hits)}")
        return 0

    if args.json:
        print(json.dumps({"count": len(hits), "allowed": floor, "hits": hits}, indent=1))
        return 0 if len(hits) <= floor else 1

    print(f"explainer-text: {len(hits)} explainer string(s), floor {floor}")
    if args.list or len(hits) > floor:
        for h in hits:
            print(f"  {h['file']}:{h['line']}  [{h['pattern']}]  {h['text']}")

    if args.since:
        with extracted_tree(args.since, ROOT) as base_root:
            before = len(scan(base_root))
        if len(hits) > before:
            print(f"  FAIL added {len(hits) - before} explainer string(s) over {args.since}")
            return 1
        print(f"  ok  {before} -> {len(hits)} against {args.since}")
        return 0

    if len(hits) > floor:
        print(f"  FAIL {len(hits) - floor} over the floor. The application is not a "
              f"place to leave notes for the operator.")
        return 1
    if len(hits) < floor:
        print(f"  ok  improved {floor} -> {len(hits)}; re-pin with --write-baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
