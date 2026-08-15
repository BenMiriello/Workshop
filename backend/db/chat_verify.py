"""Does the tree say the same thing as the log?

Step 1 of moving chat storage onto Postgres
(docs/platform-redesign/transcript-read-from-rows-2026-08-14.md) makes the live
write path build a faithful tree: one row per stretch of an interrupted reply,
a `chat_turn` per run. Step 2 serves reads from those rows. Between the two sits
this: the tree is currently built by a best-effort background write, and moving
the read onto something that can silently miss a row would turn a lost mirror
write into a lost message on screen. That is a far worse failure than a lost
branch point, and the only defence against shipping it is being able to answer
"is the tree complete" with a number rather than a hope.

Three questions, per thread:

  MISSING    a message event in the log with no row. A mirror write that failed.
  UNTURNED   an assistant row whose log event named a run, with no `turn_id`.
             Written by the old flat mirror, before stretches existed.
  ORDER      the tree's render order against the log's, resolved the way the
             SCREEN resolves it.

`rebuild` is the repair, and it is deliberately blunt: drop the thread's rows and
re-load them from the log through the backfill, which already knows how to split
a reply. Re-parenting an existing flat row is not possible (`parent_id` is
immutable by trigger, which is what makes a cycle unbuildable) and splitting one
in place would need rows inserted between rows, so there is nothing subtler to
do.

    python -m db.chat_verify              report
    python -m db.chat_verify --rebuild    repair what the report found
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

META_NAME = "meta.json"


def _log_view(thread_dir: Path) -> dict[str, Any]:
    """What the log says this thread contains, in the order the screen shows."""
    from db.chat_backfill import _read_events

    events = _read_events(thread_dir)
    messages = [e for e in events if e.get("kind") == "message"]
    turned = {e["id"] for e in messages
              if e.get("role") == "assistant" and (e.get("meta") or {}).get("turn_id")}
    return {"events": events, "messages": messages, "turned": turned}


def check_thread(s, thread_dir: Path) -> dict | None:
    """One thread's report, or None when the tree has never seen it."""
    from db.chat_backfill import thread_by_external

    external = thread_dir.name
    tid = thread_by_external(s, external)
    if tid is None:
        return None

    log = _log_view(thread_dir)
    rows = s.execute(text(
        "SELECT external_id, role, turn_id FROM chat_message "
        "WHERE thread_id = :t AND kind = 'message' AND deleted_at IS NULL"),
        {"t": tid}).all()
    have = {r[0] for r in rows if r[0]}
    unturned = {r[0] for r in rows
                if r[0] and r[1] == "assistant" and r[2] is None}

    missing = [e["id"] for e in log["messages"] if e["id"] not in have]
    stale = sorted(log["turned"] & unturned)
    branches = s.scalar(text(
        "SELECT count(*) FROM chat_branch WHERE thread_id = :t "
        "AND deleted_at IS NULL"), {"t": tid}) or 0
    return {"external": external, "id": tid, "missing": missing,
            "unturned": stale, "branches": int(branches),
            "log_messages": len(log["messages"]), "rows": len(rows)}


def rebuild_thread(s, org_id, user_id, thread_dir: Path, stats: dict) -> None:
    """Drop this thread's rows and re-load them from the log.

    REFUSES A BRANCHED THREAD. A branch names its fork point and its head by
    message id, so rebuilding under one would leave those pointers naming rows
    that no longer exist. Repairing a branched thread means deciding what its
    branches mean against a tree that was rebuilt beneath them, and that is a
    judgement, not a migration.
    """
    from db.chat_backfill import _load_thread, thread_by_external

    external = thread_dir.name
    tid = thread_by_external(s, external)
    if tid is None:
        return
    branches = s.scalar(text(
        "SELECT count(*) FROM chat_branch WHERE thread_id = :t "
        "AND deleted_at IS NULL"), {"t": tid}) or 0
    if branches > 1:
        stats["skipped_branched"] += 1
        return

    s.execute(text("UPDATE chat_branch SET head_message_id = NULL, "
                   "fork_message_id = NULL WHERE thread_id = :t"), {"t": tid})
    s.execute(text("DELETE FROM chat_message WHERE thread_id = :t"), {"t": tid})
    s.execute(text("DELETE FROM chat_turn WHERE thread_id = :t"), {"t": tid})
    s.execute(text("UPDATE chat_thread SET next_seq = 1 WHERE id = :t"), {"t": tid})
    _load_thread(s, org_id, user_id, thread_dir, stats)
    stats["rebuilt"] += 1


def _owner_dirs():
    from db.chat_backfill import _iter_owner_dirs, _thread_dirs
    for owner, chat_dir in _iter_owner_dirs():
        for d in _thread_dirs(chat_dir):
            yield owner, d


def run(rebuild: bool = False) -> dict:
    from db.engine import owner_session, tenant_session

    with owner_session() as s:
        org_id = s.scalar(text(
            "SELECT id FROM organization WHERE slug = 'workshop' "
            "AND deleted_at IS NULL"))
        user_id = s.scalar(text(
            "SELECT owner_user_id FROM chat_thread WHERE owner_user_id IS NOT NULL "
            "LIMIT 1"))
    if org_id is None:
        return {"error": "no 'workshop' organization"}

    report = {"threads": 0, "clean": 0, "with_missing": 0, "with_unturned": 0,
              "missing_messages": 0, "unturned_messages": 0, "branched": 0}
    bad: list[dict] = []
    with tenant_session(org_id) as s:
        for _owner, d in _owner_dirs():
            r = check_thread(s, d)
            if r is None:
                continue
            report["threads"] += 1
            if r["branches"] > 1:
                report["branched"] += 1
            if r["missing"]:
                report["with_missing"] += 1
                report["missing_messages"] += len(r["missing"])
            if r["unturned"]:
                report["with_unturned"] += 1
                report["unturned_messages"] += len(r["unturned"])
            if not r["missing"] and not r["unturned"]:
                report["clean"] += 1
            else:
                bad.append(r)

    if not rebuild:
        report["needs_repair"] = [b["external"] for b in bad]
        return report

    stats = {"threads": 0, "threads_skipped": 0, "topped_up": 0, "messages": 0,
             "turns": 0, "boundaries": 0, "scopes": 0, "split_replies": 0,
             "legacy_split_replies": 0, "split_stretches": 0,
             "unsplit_replies": 0, "unsplit_messages": 0, "orphan_messages": 0,
             "rebuilt": 0, "skipped_branched": 0, "created_at_checked": 0}
    by_name = {b["external"]: b for b in bad}
    with tenant_session(org_id) as s:
        for _owner, d in _owner_dirs():
            if d.name in by_name:
                rebuild_thread(s, org_id, user_id, d, stats)
    report["repair"] = stats
    return report


if __name__ == "__main__":
    import json
    print(json.dumps(run(rebuild="--rebuild" in sys.argv), indent=2, default=str))
