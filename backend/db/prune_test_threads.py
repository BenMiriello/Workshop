"""Remove chat threads that only tests ever created.

WHY THIS EXISTS. The chat mirror writes every message into Postgres as well as
into `log.jsonl`. Tests that touch a real owner redirect `ownership._RUNTIME` at
a tmp_path so they do not write real chats, but the mirror resolved its tenant
from the owner contextvar, which that redirect never touched. So the file half
of every such write went to a tmp_path and the database half went to the real
database. `services/tenancy.py` closes the leak; this removes what leaked before
it was closed.

WHAT MAKES A THREAD SAFE TO DELETE. Two independent conditions, both required:

  1. No directory on disk. `log.jsonl` is still the durable store, so a thread
     the file store has never heard of cannot be a conversation anyone can read.
  2. It matches a test signature: a known fixture title AND at most
     `_MAX_FIXTURE_MESSAGES` messages.

Condition 1 alone is NOT enough and that is the whole care here: a chat the
operator deleted also loses its directory. Condition 2 is what separates "test
junk" from "a real conversation whose files are gone", and anything meeting 1
but not 2 is REPORTED AND KEPT, never deleted. Deleting a real transcript to
tidy up a test problem would be a far worse outcome than leaving junk.

Dry run unless `--apply`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Titles the test suites hardcode. `Voice` comes from test_voice_stream_route
# and test_voice_records_what_was_said; `New chat` is the chat-store default a
# branch test leaves untouched.
_FIXTURE_TITLES = {"Voice", "New chat"}

# Every leaked thread measured carried 1 message; the branch fixture carried 4.
# A real conversation that somehow lost its directory is very unlikely to be
# this short, and one that is gets kept and reported rather than removed.
_MAX_FIXTURE_MESSAGES = 5


def _runtime_root() -> Path:
    return Path(os.environ.get(
        "WORKSHOP_RUNTIME",
        str(Path(__file__).resolve().parents[2] / ".runtime")))


def _thread_dirs_on_disk() -> set[str]:
    users = _runtime_root() / "users"
    if not users.is_dir():
        return set()
    return {d.name for u in users.iterdir() if u.is_dir()
            for d in (u / "chat").iterdir() if (u / "chat").is_dir() and d.is_dir()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="delete; without it nothing is written")
    args = ap.parse_args()

    from sqlalchemy import text

    from db.engine import owner_session

    on_disk = _thread_dirs_on_disk()
    if not on_disk:
        print("refusing to run: no chat directories found on disk, so every "
              "thread would look orphaned. Check WORKSHOP_RUNTIME.")
        return 2
    print(f"chat directories on disk: {len(on_disk)}")

    with owner_session() as s:
        rows = s.execute(text("""
            SELECT t.id, t.external_id, t.title,
                   (SELECT count(*) FROM chat_message m
                     WHERE m.thread_id = t.id) AS msgs
              FROM chat_thread t
        """)).all()

        orphans = [r for r in rows if r._mapping["external_id"] not in on_disk]
        fixtures, keep = [], []
        for r in orphans:
            m = r._mapping
            if m["title"] in _FIXTURE_TITLES and m["msgs"] <= _MAX_FIXTURE_MESSAGES:
                fixtures.append(m)
            else:
                keep.append(m)

        print(f"threads in database: {len(rows)}")
        print(f"  with no directory on disk: {len(orphans)}")
        print(f"  ...matching a test signature (deletable): {len(fixtures)}")
        print(f"  ...NOT matching, kept for review: {len(keep)}")
        for m in keep:
            print(f"      KEEP {m['external_id']}  {m['title']!r}  "
                  f"{m['msgs']} msgs")

        if not fixtures:
            print("nothing to do")
            return 0
        if not args.apply:
            print("\ndry run; pass --apply to delete the deletable set")
            return 0

        ids = [m["id"] for m in fixtures]
        deleted = {}
        for table in ("chat_message", "chat_turn", "chat_branch"):
            r = s.execute(text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"),
                          {"ids": ids})
            deleted[table] = r.rowcount
        r = s.execute(text("DELETE FROM chat_thread WHERE id = ANY(:ids)"),
                      {"ids": ids})
        deleted["chat_thread"] = r.rowcount
        s.commit()
        for k, v in deleted.items():
            print(f"deleted {v:5} from {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
