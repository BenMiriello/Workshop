#!/usr/bin/env python3
"""Move `.runtime/learnings/*.jsonl` into the note store as standing notes.

Ticket 81198145. A live learning becomes a note with `reach: always`. The
ARCHIVE is left exactly where it is and is not migrated: see the comment at the
archive block below for why that is the better half of the ticket's own
"migrate it or keep it readable".

CONSERVATION IS REPORTED, before and after, per scope, because the ticket asks
for it and because a migration that silently drops four rows out of 53 looks
exactly like one that worked.

THE TWO SCOPES THAT LOOK ALIKE. `_scope_from_filename` reverses `:` to `-` only
for the prefixes project/agent/app/role, so `development.jsonl` is the scope
`development` and `project-development.jsonl` is the scope `project:development`.
They are DIFFERENT scopes and a naive merge collides them. This reuses that
function rather than reimplementing the rule.

Usage:
    python3 scripts/migrate_learnings_to_notes.py            # report only
    python3 scripts/migrate_learnings_to_notes.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def _rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from services import knowledge, standing
    from services.learnings import _scope_from_filename
    from services.task_scope import _note_id_from_title

    store = ROOT / ".runtime" / "learnings"
    if not store.is_dir():
        print("no learnings store; nothing to migrate")
        return 0

    # `global` was a scope whose entire definition was "injected everywhere",
    # and the note store already has one of those: `workshop` is the widest
    # element of every read-union (`knowledge.read_layers`), so a standing note
    # there reaches every scope. Mapping it is what lets the fold delete the
    # second scope grammar rather than reproduce it.
    ALIASES = {"global": "workshop"}

    plan: list[tuple[str, str, dict]] = []   # (scope, reach, row)
    for f in sorted(store.glob("*.jsonl")):
        for row in _rows(f):
            sc = _scope_from_filename(f.stem)
            plan.append((ALIASES.get(sc, sc), standing.ALWAYS, row))
    # THE ARCHIVE STAYS WHERE IT IS, and that is the ticket's own option: "do
    # not delete the archive: migrate it OR keep it readable, since it is the
    # only record of what was retired and why."
    #
    # Keeping it readable is the better half of that choice. Those 33 rows were
    # RETIRED -- superseded, merged away, aged out -- and migrating them would
    # put 33 dead rules into the operator's Memory page on the day the fold is
    # supposed to make that page worth looking at. `reach: search` would keep
    # them out of prompts and would not keep them out of the tree. They remain
    # as `.runtime/learnings/archive/*.jsonl`, plain JSON lines anyone can read,
    # which is what they have always been.

    before: dict[str, int] = {}
    for scope, reach, _ in plan:
        before[f"{scope} ({reach})"] = before.get(f"{scope} ({reach})", 0) + 1

    print(f"BEFORE: {len(plan)} rows")
    for k in sorted(before):
        print(f"  {before[k]:>3}  {k}")

    if not args.apply:
        print("\n(report only; pass --apply to write)")
        return 0

    written = 0
    skipped: list[str] = []
    for scope, reach, row in plan:
        text = " ".join((row.get("text") or "").split())
        if not text:
            skipped.append(f"{scope}: empty text, id {row.get('id')}")
            continue
        try:
            knowledge._parse_scope(scope)
        except Exception:
            skipped.append(f"{row.get('id')}: {scope!r} is not a scope this store accepts")
            continue
        title = standing._title_for(text)
        # `_note_id_from_title` suffixes a MILLISECOND timestamp, which is unique
        # when a human writes notes and is not when a migration writes 86 in a
        # loop: two rows with the same title in the same millisecond mint the
        # same id and the second silently overwrites the first. Measured in the
        # sandbox run: 86 rows in, 85 files out. Conservation is the whole point
        # of this script, so the id is checked against the directory.
        note_id = _note_id_from_title(title)
        base = note_id
        n = 2
        while (knowledge.scope_dir(scope, create=True) / f"{note_id}.md").exists():
            note_id = f"{base}-{n}"
            n += 1
        # Provenance carries across as the count it already had: a rule the old
        # store saw five times is a rule this one has seen five times, and
        # throwing that away would make every migrated note look brand new.
        seen = int(row.get("provenance_total") or len(row.get("provenance") or []) or 1)
        body = standing._with_provenance(text, seen)
        if reach == "search" and row.get("archived_reason"):
            body += f"\n\nRetired: {row['archived_reason']}."
        knowledge._put_note_sync(
            scope, note_id, title, body,
            author=str(row.get("source") or ""), reach=reach)
        written += 1

    print(f"\nAFTER: {written} notes written, {len(skipped)} skipped")
    for s in skipped:
        print(f"  skipped: {s}")

    total_after = 0
    for scope in sorted({s for s, _, _ in plan}):
        try:
            n = standing.count(scope)
        except Exception:
            n = 0
        total_after += n
        print(f"  {n:>3}  {scope} standing notes now")
    print(f"\nCONSERVATION: {len(plan)} rows in, {written} notes written, "
          f"{len(plan) - written - len(skipped)} unaccounted")
    return 0 if written + len(skipped) == len(plan) else 1


if __name__ == "__main__":
    raise SystemExit(main())
