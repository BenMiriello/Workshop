"""Compare chat_list_pg.list_threads() against chat_store.list_threads()

Run: PYTHONPATH=backend backend/.venv/bin/python backend/db/chat_list_parity.py
This is the gate on WORKSHOP_CHAT_LIST_PG (see routes/chat.py threads()).
element-by-element, for owner 6, live and archived. Ad hoc verification for
the PG-backed thread list; not a pytest (this environment's DB tests are
gated off — see the task notes), run directly against the live store.
"""
import asyncio
import json

from services import ownership, chat_store, chat_list_pg


async def main():
    ownership.set_current_owner(6)

    results = {}
    for archived in (False, True):
        file_rows = chat_store.list_threads(archived=archived)
        pg_rows = await chat_list_pg.list_threads(archived=archived)
        assert pg_rows is not None, "PG list returned None (fell back) -- unexpected here"

        file_by_id = {r["id"]: r for r in file_rows}
        pg_by_id = {r["id"]: r for r in pg_rows}

        only_file = set(file_by_id) - set(pg_by_id)
        only_pg = set(pg_by_id) - set(file_by_id)

        field_diffs = []
        for tid in sorted(set(file_by_id) & set(pg_by_id)):
            f, p = file_by_id[tid], pg_by_id[tid]
            for k in sorted(set(f) | set(p)):
                if f.get(k) != p.get(k):
                    field_diffs.append((tid, k, f.get(k), p.get(k)))

        order_file = [r["id"] for r in file_rows]
        order_pg = [r["id"] for r in pg_rows]
        order_match = order_file == order_pg

        results[f"archived={archived}"] = {
            "file_count": len(file_rows),
            "pg_count": len(pg_rows),
            "only_in_file": sorted(only_file),
            "only_in_pg": sorted(only_pg),
            "field_diffs": field_diffs,
            "order_match": order_match,
            "order_file": order_file if not order_match else None,
            "order_pg": order_pg if not order_match else None,
        }

    print(json.dumps(results, indent=2, default=str))


asyncio.run(main())
