"""Each user's saved app order. The dashboard's Apps card is arranged in it, and
the dock and the Apps page read it too, so an app sits in one place everywhere."""
from __future__ import annotations

import json

from services import auth_store


def _ensure() -> None:
    auth_store.init_db()
    with auth_store._connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS home_layout ("
            " user_id INTEGER PRIMARY KEY, items TEXT NOT NULL)"
        )


def order(user_id: int) -> list[str]:
    _ensure()
    with auth_store._connect() as conn:
        row = conn.execute("SELECT items FROM home_layout WHERE user_id = ?", (user_id,)).fetchone()
    return json.loads(row["items"]) if row else []


def set_order(user_id: int, visible: list[str]) -> list[str]:
    """Save a new order for the tiles the caller can see. Tiles it cannot see
    keep their slots, so arranging one view never scrambles another."""
    saved = order(user_id)
    seen = set(visible)
    slots = iter(visible)
    merged = [next(slots) if a in seen else a for a in saved]
    merged += [a for a in slots]
    merged = list(dict.fromkeys(merged))
    with auth_store._connect() as conn:
        conn.execute(
            "INSERT INTO home_layout (user_id, items) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET items = excluded.items",
            (user_id, json.dumps(merged)),
        )
    return merged
