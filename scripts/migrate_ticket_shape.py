#!/usr/bin/env python3
"""Materialize `title`/`body` on every ticket store, and repair elided titles.

The store normalizes on READ, so nothing is broken before this runs: a legacy
record loads correctly and gains its title/body pair the next time its store
persists. This pass forces that write everywhere at once so the repair is
visible in one diff instead of trickling in over weeks.

Idempotent by construction: `split_text` is a fixed point, so a second run
reports zero changes. Dry by default.

    python3 scripts/migrate_ticket_shape.py            # report only
    python3 scripts/migrate_ticket_shape.py --write     # persist

Design: docs/platform-redesign/ticket-model-and-external-trackers-2026-08-07.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services import tasks  # noqa: E402


def stores(root: Path) -> list[Path]:
    skip = {"node_modules", ".venv", ".git"}
    out = []
    for p in root.rglob(tasks.STORE_NAME):
        if any(part in skip for part in p.parts):
            continue
        out.append(p.parent)
    return sorted(out, key=lambda p: str(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist (default: report only)")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args()

    total = repaired = missing_pair = 0
    export_changed: list[str] = []

    for store_root in stores(Path(args.root)):
        raw = json.loads((store_root / tasks.STORE_NAME).read_text()).get("tasks") or []
        export = store_root / tasks.EXPORT_NAME
        before = export.read_text() if export.exists() else None

        loaded = [tasks._normalize(t) for t in raw if isinstance(t, dict)]
        for old, new in zip(raw, loaded):
            total += 1
            if "title" not in old:
                missing_pair += 1
            # The stored title is either the `title` field or, for a record that
            # never migrated, the first line of `text`. Comparing against
            # whichever it is keeps the repair count honest on a re-run.
            old_title = old.get("title")
            if old_title is None:
                old_title = (old.get("text") or "").split("\n")[0]
            if old_title.rstrip().endswith("…") and new["title"] != old_title:
                repaired += 1
                rel = store_root.relative_to(Path(args.root))
                print(f"  repair {rel or '.'}: {old_title[:56]}")
                print(f"      -> {new['title'][:100]}")

        after = tasks.render_markdown(loaded)
        if before is not None and before != after:
            export_changed.append(str(store_root.relative_to(Path(args.root)) or "."))

        if args.write and loaded:
            with tasks._locked(store_root):
                tasks._persist(store_root, loaded)

    print()
    print(f"stores          {len(stores(Path(args.root)))}")
    print(f"tickets         {total}")
    print(f"gaining a pair  {missing_pair}")
    print(f"titles repaired {repaired}")
    print(f"exports changed {len(export_changed)}"
          + (f" ({', '.join(export_changed)})" if export_changed else ""))
    print("WROTE" if args.write else "dry run. Pass --write to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
