"""Re-parent a thread's messages so the chain follows the file log's order.

WHY THIS EXISTS. `chat_message.parent_id` is the transcript: `chat_repo._PATH`
walks it, so the chain IS the order. Measured on the real store 2026-08-15, ten
of sixty-eight live threads have ordinary messages chained out of the order the
append-only log recorded them in. On 596c8d5c the log ends with the assistant's
reply and the chain ends with the operator's `continue`, two adjacent messages
inverted. This is residue of the concurrent mirror writes fixed on 2026-08-14.

WHAT IT WILL NOT TOUCH, and why that restriction is the whole point.

An interrupted reply is deliberately NOT in log order: ADR 0018 splits it into
stretches threaded around the messages that interrupted it, so the tree is
richer than the flat log rather than a copy of it. The log appends the reply as
ONE event when the turn ends, so a message sent DURING that turn is recorded
before it; in the tree that message correctly sits between two stretches. Walk
the chain against log positions and that legitimate placement looks exactly
like the corruption above.

So this repairs only threads with NO split replies, where every message is one
row and log order is therefore unambiguously the right chain. Measured: the
split stretches themselves never step backwards on any affected thread (0 of
12), so nothing about splits is broken; it is only that their presence makes
"correct order" a question this tool cannot answer from the log alone. A thread
carrying splits is REPORTED AND SKIPPED, and rebuilding those needs the
splitting logic in db/chat_backfill.py, not a re-parent.

WHAT IT DOES. UPDATE of `parent_id` only, plus the branch head. No row is
created, altered in content, or deleted, and the file log is never written; the
log is the source of truth and this makes the mirror agree with it. Ordering is
taken from the log, so re-running on an already-correct thread is a no-op.

Dry run unless `--apply`.

  PYTHONPATH=backend backend/.venv/bin/python backend/db/repair_chain_order.py
  PYTHONPATH=backend backend/.venv/bin/python backend/db/repair_chain_order.py --apply

Verify with db/chat_list_parity.py, the gate on WORKSHOP_CHAT_LIST_PG.
"""
from __future__ import annotations

import argparse
import collections


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually re-parent (default: report only)")
    ap.add_argument("--owner", type=int, default=6)
    args = ap.parse_args()

    from db.script_env import load_config_env
    load_config_env()

    from sqlalchemy import text

    from services import ownership, tenancy, chat_log
    ownership._owner_ctx.set(args.owner)
    from db import chat_repo
    from db.engine import tenant_session

    org_id, _ = tenancy.org_and_user()
    repaired: list[tuple[str, int]] = []
    skipped: list[tuple[str, str]] = []
    already = 0

    with tenant_session(org_id) as s:
        for tid in chat_log.list_thread_ids():
            t = chat_repo.thread_by_ref(s, tid)
            if t is None:
                continue
            path = chat_repo.transcript(s, t["id"])
            if not path:
                continue

            counts = collections.Counter(n.get("external_id") for n in path)
            if any(v > 1 for v in counts.values()):
                # Has split replies: log order cannot express where the
                # stretches belong. See the module docstring.
                order = [n.get("external_id") for n in path]
                events = [e["id"] for e in chat_log.read_events(tid)
                          if e and e.get("kind") == "message"]
                pos = {e: i for i, e in enumerate(events)}
                prev, backwards = -1, 0
                for e in order:
                    p = pos.get(e)
                    if p is None:
                        continue
                    if p < prev:
                        backwards += 1
                    prev = max(prev, p)
                if backwards:
                    skipped.append(
                        (tid, f"has split replies; {backwards} backwards steps "
                              "need the splitting logic, not a re-parent"))
                continue

            events = [e["id"] for e in chat_log.read_events(tid)
                      if e and e.get("kind") == "message"]
            by_ext = {n.get("external_id"): n for n in path}
            # Only the messages that are actually mirrored, in log order. A log
            # event with no row is a mirror GAP; re-parenting cannot invent it,
            # so it is left out of the chain rather than papered over.
            want = [by_ext[e] for e in events if e in by_ext]
            if len(want) != len(path):
                skipped.append((tid, f"{len(path) - len(want)} row(s) not in the log"))
                continue
            if [n.get("external_id") for n in path] == [n.get("external_id") for n in want]:
                already += 1
                continue

            moved = 0
            parent = None
            for node in want:
                s.execute(text(
                    "UPDATE chat_message SET parent_id = :p WHERE id = :i"),
                    {"p": parent, "i": node["id"]}) if args.apply else None
                if str(node.get("parent_id") or "") != str(parent or ""):
                    moved += 1
                parent = node["id"]
            if args.apply and parent is not None:
                s.execute(text(
                    "UPDATE chat_branch SET head_message_id = :m WHERE id = :b"),
                    {"m": parent, "b": t["current_branch_id"]})
            repaired.append((tid, moved))
        if args.apply:
            s.commit()

    print(f"threads already in log order: {already}")
    print(f"threads {'re-parented' if args.apply else 'to re-parent'}: {len(repaired)}")
    for tid, moved in repaired:
        print(f"  {tid[:12]}  {moved} parent link(s) change")
    print(f"threads skipped: {len(skipped)}")
    for tid, why in skipped:
        print(f"  SKIP {tid[:12]}: {why}")
    if not args.apply and repaired:
        print("\ndry run. re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
