"""The thread list, served from Postgres.

`chat_store.list_threads` (the file fold) is correct and stays that way: it
is the fallback. THIS module is what the `/threads` route tries FIRST, and it
is allowed to fail — on any exception, or when the relational store is simply
unreachable, it returns `None` and the route falls back to the fold. A
Postgres outage must cost speed, never the chat list.

`db.chat_repo.list_threads` does the one query (a recursive walk of every
thread's CURRENT branch path, aggregated -- see its own docstring for why a
plain `SELECT * FROM chat_message` would be wrong). Everything below that is
business logic chat_store already has and this reuses rather than
reimplements: the title-from-seed fallback, the scope derivation from
`context_key`, the timestamp format the file store writes.
Two independent readers computing the SAME rule in two languages is exactly
how a "byte-identical" requirement quietly stops being true six months later.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from services import tenancy

log = logging.getLogger("workshop.chat_list_pg")

__all__ = ["list_threads"]


def _iso(value: datetime | None) -> str | None:
    """Match `chat_log.now_iso()` / `chat_store._now_iso()` exactly: seconds
    precision, not the microseconds Postgres actually stores. Established by
    measurement (2026-08-15): the file's `2026-08-15T01:55:02+00:00` and
    Postgres's `...02.470688+00:00` name the same instant, so truncating is a
    formatting choice, not a data loss -- and it is the ONE formatting choice
    that reproduces today's file-fold output byte for byte."""
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _shape_row(row: dict) -> dict:
    """One `db.chat_repo.list_threads` row, in the exact shape
    `chat_store._summary_row` hands the `/threads` route.

    Imports `chat_store` lazily and only for its pure helpers: the title
    fallback and the mode default. Using the SAME functions the file fold uses
    is what keeps the two paths from drifting apart the moment one of those
    rules changes.
    """
    from services import context_window, scope_tree
    from services.chat_store import DEFAULT_CHAT_MODE

    ck = row.get("context_key") or "general"

    title = row.get("title") or "New chat"
    if title in (None, "", "New chat") and row.get("seed_title"):
        title = row["seed_title"]

    state = row.get("state") or {}

    stored_ctx = row.get("context_tokens")
    ctx_tokens = (int(stored_ctx)
                  if isinstance(stored_ctx, (int, float)) and stored_ctx > 0 else 0)

    return {
        "id": row["external_id"],
        "context_key": ck,
        # DERIVED, not the column, exactly as `chat_store._summary_row` derives
        # it -- so a stale mirror row cannot make the PG path and the file path
        # disagree about where a chat lives.
        "scope": scope_tree.scope_for_context(ck),
        "mode": row.get("mode") or DEFAULT_CHAT_MODE,
        # WHO ANSWERS, when that is an agent -- the same field
        # `chat_store._summary_row` hands back, off the same value.
        "agent_id": row.get("agent_slug"),
        "model": row.get("model"),
        "title": title,
        "created_at": _iso(row.get("created_at")),
        # NOT the column. `chat_thread.updated_at` is maintained by the
        # `chat_thread_set_updated_at` BEFORE UPDATE trigger, so it says when
        # the ROW was last written, which is "whenever the mirror last ran".
        # The file's `updated_at` is what every reader actually wants (the
        # chat list sorts on it and Home renders it as an age), and it is
        # mirrored verbatim into `state` by chat_repo.sync_thread_fields.
        # The column is the fallback for a thread whose meta has not been
        # mirrored since this field existed.
        "updated_at": (state.get("meta_updated_at")
                       if isinstance(state, dict) and state.get("meta_updated_at")
                       else _iso(row.get("updated_at"))),
        "archived_at": _iso(row.get("archived_at")),
        "last_user_at": _iso(row.get("last_user_at")),
        "message_count": int(row.get("message_count") or 0),
        "last_message": (row.get("last_message") or "")[:300],
        # HOW FULL THE WINDOW IS. `context_tokens` is a real mirrored column
        # (db.models.App -> chat_thread), so this path has the same fact the
        # file fold reads out of meta, and both resolve the denominator through
        # the one table in `services/context_window`.
        #
        # `running` is NOT here: it is stamped over the finished rows by
        # `chat_store.stamp_running`, so the two list sources cannot derive it
        # differently. See that function.
        "context_tokens": ctx_tokens or None,
        "context_window": (context_window.window_for(row.get("model"), ctx_tokens)
                           if ctx_tokens else None),
        "branch_slug": (state or {}).get("branch_slug"),
    }


def _sync_list(org_id, owner_id, context_key: str | None, archived: bool
              ) -> list[dict]:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        rows = chat_repo.list_threads(s, org_id, owner_id,
                                      context_key=context_key, archived=archived)
    return [_shape_row(r) for r in rows]


# Logged once per failure EPISODE, not per request. `tenancy.available()` is
# itself cached (services.tenancy._db_ok) after the first check, so a
# database that is down from the start never reaches the `except` below at
# all; this covers the other case, where startup found Postgres reachable and
# it later stops answering -- without this flag every poll during that window
# (a page load, and `refreshThreads()` after every turn, from every open tab)
# would log its own warning, which is exactly the spam the task calls out.
_fallback_warned = False


async def list_threads(context_key: str | None = None, archived: bool = False
                       ) -> list[dict] | None:
    """The PG-backed thread list, or `None` when the caller must fall back to
    `chat_store.list_threads` (the file fold)."""
    global _fallback_warned
    if not tenancy.available():
        return None
    try:
        org_id, owner_id = tenancy.org_and_user()
        rows = await run_in_threadpool(_sync_list, org_id, owner_id,
                                       context_key, archived)
    except Exception:
        if not _fallback_warned:
            log.warning("PG thread list failed; falling back to the file "
                        "fold until it recovers", exc_info=True)
            _fallback_warned = True
        return None
    _fallback_warned = False
    return rows
