#!/usr/bin/env python3
"""One-time: retire the `participants` key from every chat thread's meta.json.

`participants` was a list of `agent:<slug>` strings written by
`chat_store._promotion_patch` and read by nothing that answers a turn. Thread T2
of docs/platform-redesign/coherence-overhaul-2026-08-16.md deletes it, and this
is the pass that takes it off disk so no meta.json carries a key with no reader.

THE 15 THREADS THAT CARRY ONE ARE NOT LEFT EMPTY-HANDED. They are the casualties
of the original P5 promotion: each was a chat with a build agent, and the
promotion re-keyed it to `project:<slug>` and recorded who it had been with in
this field. The slug moves to the thread's `agent_id`, which is a field that
already exists on every thread, is already mirrored into the `chat_thread.
agent_slug` column, and is now read by the chat list (`lib/chat.threadAgent`).
Nothing is dropped.

THEY ARE NOT RESTORED TO `agent:<slug>`, and that is a decision, not an
omission. Every one of those slugs names a retired or deleted build agent, so
`scope_tree` does not list it as a concrete scope: `reach()` cannot mint
`agent:budget-builder-1` any more than it could mint `app:development`, which is
the exact address that dropped 27 threads out of Memory.tsx's filter and was
repaired one commit ago. Re-keying them would recreate that bug and would also
pull the transcripts out of the project subtree where their work lives. The
transcripts stay where their work is; who they were with is preserved beside
them.

Idempotent. Only touches `participants` and `agent_id`.

Usage: python3 scripts/migrate_chat_participants.py [--apply]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT / ".runtime" / "users"
APPLY = "--apply" in sys.argv


def _slug_of(participants: object) -> str | None:
    """The single agent slug a participants list names, or None.

    A list with more than one entry has no single answer, so it does not set
    `agent_id`; the key is still dropped and the loss is reported rather than
    made silently. No live thread is in that shape (all 15 carry exactly one).
    """
    if not isinstance(participants, list) or len(participants) != 1:
        return None
    raw = participants[0]
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    return value.split(":", 1)[1] if value.startswith("agent:") else value


def main() -> None:
    if not USERS.exists():
        print("no .runtime/users; nothing to do")
        return
    dropped = 0
    carried = 0
    unnamed = 0
    for meta_path in sorted(USERS.glob("*/chat/*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if "participants" not in meta:
            continue
        participants = meta.pop("participants")
        dropped += 1
        slug = _slug_of(participants)
        note = ""
        if slug and not meta.get("agent_id"):
            meta["agent_id"] = slug
            carried += 1
            note = f" (agent_id <- {slug})"
        elif not slug and participants:
            unnamed += 1
            note = f" (no single agent in {participants!r}; not carried)"
        if APPLY:
            meta_path.write_text(json.dumps(meta, indent=2))
        verb = "dropped" if APPLY else "would drop"
        print(f"{verb} participants from {meta_path.parent.name}"
              f" [{meta.get('context_key')}]{note}")
    verb = "dropped" if APPLY else "would drop"
    print(f"done: {verb} participants from {dropped} thread(s), "
          f"{carried} carried to agent_id, {unnamed} with no single agent"
          + ("" if APPLY else " (dry run; re-run with --apply)"))


if __name__ == "__main__":
    main()
