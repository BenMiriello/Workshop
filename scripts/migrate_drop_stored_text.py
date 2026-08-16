#!/usr/bin/env python3
"""Drop the stored `text` duplicate from every ticket store.

A ticket has been a title and a body since 010a9a4; `text` is the join of the
two, re-derived by `tasks._normalize` on every read. It was ALSO written to disk
during the transition, so every record carried the same words twice and a hand
edit could put the copy out of step with the pair it came from. `text` is now in
`tasks._PROJECTED_KEYS`, so nothing writes it any more; this clears what the
transition already wrote.

WHY THIS EDITS THE JSON DIRECTLY rather than reading and re-persisting through
the store. Two roots are relational: reads come from Postgres and the file is a
mirror. A migration pass has no authenticated owner, so reading through the
store either falls back to the file (and then rewrites it from itself, which is
this, with extra steps) or reads Postgres and overwrites the mirror with it,
which is a different operation than the one being asked for. Stripping one key
touches exactly what it says it touches.

THE SAFETY RULE: a record's `text` is only deleted when it is exactly
reproducible from that record's own title and body. Anything else means the
duplicate is the sole copy of something, so it is REPORTED and left alone.

    python3 scripts/migrate_drop_stored_text.py            # dry run, reports only
    python3 scripts/migrate_drop_stored_text.py --apply    # rewrite

Idempotent: a second run reports 0 records carrying the duplicate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from services.tasks import join_text, split_text  # noqa: E402

SKIP_PARTS = {"node_modules", ".venv", ".git"}


def stores(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob(".tasks.json")
                  if not any(part in SKIP_PARTS for part in p.parts))


def reproducible(record: dict) -> bool:
    """Is the stored `text` exactly what the canonical pair would render?

    Compared through the same split/join pair the store uses, so a record whose
    title was migrated from an over-long line still matches.
    """
    stored = record.get("text")
    if not isinstance(stored, str):
        return True  # nothing meaningful to lose
    title = str(record.get("title") or "")
    body = str(record.get("body") or "")
    if not title and not body:
        return False  # the duplicate is the only copy of the words
    return join_text(title, body).strip() == join_text(*split_text(stored)).strip()


def _atomic_write(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tasks.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the stores (default is a dry run)")
    args = ap.parse_args()

    carried = 0
    kept: list[tuple[Path, str]] = []
    plan: list[tuple[Path, dict, int]] = []

    for path in stores(REPO):
        try:
            payload = json.loads(path.read_text())
        except Exception as exc:
            print(f"  unreadable, skipped: {path.relative_to(REPO)} ({exc})")
            continue
        records = payload.get("tasks") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            continue
        n = 0
        for rec in records:
            if not isinstance(rec, dict) or "text" not in rec:
                continue
            if not reproducible(rec):
                kept.append((path, str(rec.get("id"))))
                continue
            n += 1
        if n:
            carried += n
            plan.append((path, payload, n))
            print(f"  {n:4d} records  {path.relative_to(REPO)}")

    print(f"{len(plan)} stores, {carried} records carry a reproducible duplicate")
    for path, tid in kept:
        print(f"  KEPT (text is not reproducible from title+body): "
              f"{path.relative_to(REPO)} {tid}")

    if not args.apply:
        print("dry run; nothing written. Re-run with --apply")
        return 0

    for path, payload, _n in plan:
        records = payload.get("tasks") if isinstance(payload, dict) else payload
        for rec in records:
            if isinstance(rec, dict) and "text" in rec and reproducible(rec):
                rec.pop("text")
        _atomic_write(path, payload)

    # Verify by re-reading, not by trusting the write.
    left = 0
    for path, _payload, _n in plan:
        records = json.loads(path.read_text()).get("tasks") or []
        left += sum(1 for r in records if isinstance(r, dict) and "text" in r)
    print(f"rewrote {len(plan)} stores; {left} records still carry it")
    return 0 if left == len(kept) else 1


if __name__ == "__main__":
    raise SystemExit(main())
