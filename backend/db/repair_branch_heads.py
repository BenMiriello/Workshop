"""Point each thread's current branch at the tip the file log actually ends on.

WHY THIS EXISTS. `chat_branch.head_message_id` defines a branch: the transcript
is the ancestor walk UP from that head (`chat_repo._PATH`). Measured on the real
store 2026-08-15, twelve of sixty-eight live threads have a head sitting on a
chain that the file log's newest message is NOT on, so the walk terminates on an
old message and everything since is unreachable. On 596c8d5c the head was a
`continue` from hours earlier while the reply after it sat in `chat_message`,
correctly parented, invisible.

This is NOT the same fault as a head that has simply fallen behind: only ONE
thread has a head with children (a scratch test row). The other eleven have
DIVERGENT chains, which is the signature of the concurrent mirror writes fixed
on 2026-08-14. This repairs the rows that race left behind.

WHY IT MATTERS BEYOND THE THREAD LIST. A stale head is not a display bug. The
head is what a fork anchors against and what `chat_repo.transcript` renders, so
anything that moves the transcript read onto the rows inherits the wrong
conversation. The chat list is just where it became visible (the sidebar preview
showed a days-old `continue` instead of the last reply).

WHAT IT DOES, and the safety rule. For each thread it finds the `chat_message`
row carrying the file log's LAST message event id, walks DOWN to that chain's
leaf (an interrupted reply's later stretches are children of the row holding
the source event id, so the leaf is the true tip), and points the branch there.

It only ever touches a thread whose rendered last message already disagrees
with the file fold, and it never invents a row: if the file's last event has no
row at all, that thread is REPORTED AND SKIPPED, because the missing row is a
mirror gap and moving the head would not fix it. The file log remains the
source of truth throughout; nothing here writes to it, and no message row is
created, altered or deleted. Only `chat_branch.head_message_id` moves.

Dry run unless `--apply`. Re-runnable: a thread already agreeing is skipped.

  PYTHONPATH=backend backend/.venv/bin/python backend/db/repair_branch_heads.py
  PYTHONPATH=backend backend/.venv/bin/python backend/db/repair_branch_heads.py --apply

Verify after with db/chat_list_parity.py, which is the gate on
WORKSHOP_CHAT_LIST_PG (see routes/chat.py threads()).
"""
from __future__ import annotations

import argparse

# How far down one chain to look for the leaf. A reply split by interruptions
# is a handful of stretches, never hundreds; a bound this loose only exists so
# a cycle introduced by some future bug cannot spin forever.
_MAX_DESCEND = 200


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually move the heads (default: report only)")
    ap.add_argument("--owner", type=int, default=6,
                    help="app user id whose chats to repair (default 6)")
    args = ap.parse_args()

    from sqlalchemy import text

    from services import ownership, tenancy, chat_log, chat_store
    ownership._owner_ctx.set(args.owner)
    from db import chat_repo
    from db.engine import tenant_session

    org_id, _ = tenancy.org_and_user()
    file_rows = {r["id"]: r for r in chat_store.list_threads()}

    moved: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    with tenant_session(org_id) as s:
        for tid, fr in file_rows.items():
            t = chat_repo.thread_by_ref(s, tid)
            if t is None:
                continue
            nodes = [n for n in chat_repo.transcript(s, t["id"])
                     if n.get("kind") == "message"]
            rendered = (nodes[-1].get("content") or "")[:120] if nodes else ""
            if rendered == fr["last_message"]:
                continue  # already agrees; nothing to do

            events = [e for e in chat_log.read_events(tid)
                      if e.get("kind") == "message"]
            if not events:
                skipped.append((tid, "no message events in the file log"))
                continue

            last_ext = events[-1]["id"]
            row = s.execute(text(
                "SELECT id FROM chat_message "
                " WHERE thread_id = :t AND external_id = :e AND deleted_at IS NULL "
                " ORDER BY created_at DESC LIMIT 1"),
                {"t": t["id"], "e": last_ext}).first()
            if row is None:
                # A mirror GAP, not a head problem. Moving the head would hide
                # it rather than fix it, so this is surfaced and left alone.
                skipped.append((tid, f"file's last event {last_ext} has no row"))
                continue

            tip = row[0]
            for _ in range(_MAX_DESCEND):
                nxt = s.execute(text(
                    "SELECT id FROM chat_message "
                    " WHERE parent_id = :p AND deleted_at IS NULL "
                    " ORDER BY created_at DESC LIMIT 1"), {"p": tip}).first()
                if nxt is None:
                    break
                tip = nxt[0]

            moved.append((tid, str(tip)))
            if args.apply:
                s.execute(text(
                    "UPDATE chat_branch SET head_message_id = :m WHERE id = :b"),
                    {"m": tip, "b": t["current_branch_id"]})
        if args.apply:
            s.commit()

    print(f"threads whose rendered tail disagrees with the file: "
          f"{len(moved) + len(skipped)}")
    for tid, tip in moved:
        print(f"  {'moved ' if args.apply else 'would move'} {tid[:12]} -> {tip}")
    for tid, why in skipped:
        print(f"  SKIPPED {tid[:12]}: {why}")
    if not args.apply and moved:
        print("\ndry run. re-run with --apply to move them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
