#!/usr/bin/env python3
"""One-time: rescope any chat threads still pointing at the removed `new-project`
provider to `general`, so no thread references a deleted context.

Phase 3 of the platform redesign deletes NewProjectContextProvider. Threads are
per-owner JSON at .runtime/users/<owner_id>/chat/<id>.json. This walks every
owner's chat dir and rewrites context_key new-project -> general. Idempotent and
safe to run with autonomy live (a rewrite only touches the context_key field).

Usage: python3 scripts/migrate_newproject_threads.py [--apply]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT / ".runtime" / "users"
APPLY = "--apply" in sys.argv


def main() -> None:
    if not USERS.exists():
        print("no .runtime/users; nothing to do")
        return
    changed = 0
    for chat_dir in USERS.glob("*/chat"):
        for p in chat_dir.glob("*.json"):
            try:
                t = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if t.get("context_key") == "new-project":
                changed += 1
                if APPLY:
                    t["context_key"] = "general"
                    p.write_text(json.dumps(t, indent=2))
                print(("rescoped " if APPLY else "would rescope ") + str(p))
    verb = "rescoped" if APPLY else "would rescope"
    print(f"done: {changed} thread(s) {verb} new-project -> general"
          + ("" if APPLY else " (dry run; re-run with --apply)"))


if __name__ == "__main__":
    main()
