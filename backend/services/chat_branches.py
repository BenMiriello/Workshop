"""Branching, from the chat routes' point of view.

`db/chat_repo.py` speaks in tree nodes and uuids. The chat surface speaks in
FILE EVENT IDS (the ULID on each `log.jsonl` line), because that is what the
transcript on screen is built from and what the client can point at. This module
is the translation, plus the async boundary: `db/engine.py` is a synchronous
SQLAlchemy stack and every chat route is `async def`, so a session opened on the
event loop would block it. Everything here hops to the threadpool.

WHY THE READ PATH CAN TRUST THIS. `visible_event_ids` returns None -- meaning
"do not filter anything" -- for a thread that has never been branched, which is
every thread until the operator uses the feature. So this changes exactly
nothing for a conversation nobody has forked, and there is no flag to get wrong.

Everything here is best-effort against the database being reachable. A chat must
not fail to render because Postgres is down; it falls back to the flat file
order, which is what it did before any of this existed.
"""
from __future__ import annotations

import logging

from starlette.concurrency import run_in_threadpool

from services import tenancy

log = logging.getLogger(__name__)

__all__ = ["list_branches", "fork", "switch", "rename", "visible_event_ids",
           "visible_event_ids_sync", "record_message", "keep_visible"]


def keep_visible(events: list[dict], visible: set[str] | None) -> list[dict]:
    """Drop the events that are not on the current branch.

    `None` means show everything, which is the answer for every thread nobody
    has branched.

    AN EVENT WITH NO ROW IS KEPT. That covers the mirror-write having failed,
    and the window between an append and its mirror landing. Keeping it shows a
    message on a branch it may not belong to; dropping it makes a message the
    operator just sent vanish. Only one of those is recoverable by looking
    again, so the bias is deliberate.
    """
    if visible is None:
        return events
    out = []
    for ev in events:
        eid = ev.get("id")
        if not eid or eid in visible or ev.get("kind") in ("run", "title"):
            out.append(ev)
    return out


def _sync_list(org_id, thread_ref: str) -> list[dict]:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            return []
        return chat_repo.list_branches(s, t["id"])


async def list_branches(thread_ref: str) -> list[dict]:
    if not tenancy.available():
        return []
    try:
        org_id, _ = tenancy.org_and_user()
        return await run_in_threadpool(_sync_list, org_id, thread_ref)
    except Exception:
        log.warning("branch list failed for %s", thread_ref, exc_info=True)
        return []


def _resolve_anchor(s, thread_id: str, ref: str) -> str | None:
    """A client-supplied anchor to a message id.

    Accepts either a message uuid or the ULID the file log gave the event. The
    ULID is the interesting case: an interrupted reply became SEVERAL rows that
    all carry it, and "branch from this reply" means from the END of it, so the
    last one wins. Anchoring on the first stretch would silently drop the rest
    of a reply the operator was pointing AT, not before.
    """
    from sqlalchemy import text
    row = s.execute(text(
        "SELECT id FROM chat_message "
        " WHERE thread_id = :t AND deleted_at IS NULL "
        "   AND (external_id = :ref OR CAST(id AS text) = :ref) "
        " ORDER BY seq DESC LIMIT 1"),
        {"t": str(thread_id), "ref": str(ref)}).one_or_none()
    return None if row is None else str(row._mapping["id"])


def _sync_fork(org_id, thread_ref: str, anchor: str) -> dict:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            raise chat_repo.BranchError(f"no such thread: {thread_ref}")
        msg_id = _resolve_anchor(s, t["id"], anchor)
        if msg_id is None:
            raise chat_repo.MessageNotFound(
                f"no message {anchor} in thread {thread_ref}")
        branch = chat_repo.branch_from(s, t["id"], msg_id)
        return {"branch": branch, "branches": chat_repo.list_branches(s, t["id"])}


async def fork(thread_ref: str, anchor: str) -> dict:
    org_id, _ = tenancy.org_and_user()
    return await run_in_threadpool(_sync_fork, org_id, thread_ref, anchor)


def _sync_switch(org_id, thread_ref: str, branch_id: str) -> dict:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            raise chat_repo.BranchError(f"no such thread: {thread_ref}")
        branch = chat_repo.switch_branch(s, t["id"], branch_id)
        return {"branch": branch, "branches": chat_repo.list_branches(s, t["id"])}


async def switch(thread_ref: str, branch_id: str) -> dict:
    org_id, _ = tenancy.org_and_user()
    return await run_in_threadpool(_sync_switch, org_id, thread_ref, branch_id)


def _sync_rename(org_id, thread_ref: str, branch_id: str, name: str) -> dict:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            raise chat_repo.BranchError(f"no such thread: {thread_ref}")
        chat_repo.rename_branch(s, t["id"], branch_id, name)
        return {"branches": chat_repo.list_branches(s, t["id"])}


async def rename(thread_ref: str, branch_id: str, name: str) -> dict:
    org_id, _ = tenancy.org_and_user()
    return await run_in_threadpool(_sync_rename, org_id, thread_ref, branch_id, name)


def _sync_state(org_id, thread_ref: str) -> dict:
    """Which branch is current, and which file events are on it.

    One function because the sync endpoint needs both on every poll and they
    come from the same two rows. Returns `visible=None` for "show everything".
    """
    from sqlalchemy import text

    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            return {"branch": None, "count": 0, "visible": None}
        row = s.execute(text(
            "SELECT count(*) AS n, "
            "       max(CASE WHEN b.id = th.current_branch_id THEN b.name END) AS nm "
            "  FROM chat_branch b JOIN chat_thread th ON th.id = b.thread_id "
            " WHERE b.thread_id = :t AND b.deleted_at IS NULL"),
            {"t": t["id"]}).one()
        n = int(row._mapping["n"] or 0)
        branch = ({"id": t["current_branch_id"], "name": row._mapping["nm"]}
                  if t["current_branch_id"] else None)
        # NEVER BRANCHED means never filtered. One branch is the state every
        # thread starts in and stays in until the operator uses the feature, so
        # this is the answer for almost every read, and it makes the whole
        # mechanism inert until it is asked for.
        if n < 2:
            return {"branch": branch, "count": n, "visible": None}
        ids = chat_repo.path_ids(s, t["id"])
        if not ids:
            return {"branch": branch, "count": n, "visible": set()}
        rows = s.execute(text(
            "SELECT external_id FROM chat_message WHERE thread_id = :t "
            "AND external_id IS NOT NULL AND id = ANY(CAST(:ids AS uuid[]))"),
            {"t": t["id"], "ids": "{" + ",".join(ids) + "}"}).all()
        return {"branch": branch, "count": n, "visible": {r[0] for r in rows}}


def _sync_visible(org_id, thread_ref: str) -> set[str] | None:
    return _sync_state(org_id, thread_ref)["visible"]


def branch_state_sync(thread_ref: str) -> dict:
    """`{branch, count, visible}` for the sync endpoint, or all-None when the
    relational store is unreachable."""
    empty = {"branch": None, "count": 0, "visible": None}
    if not tenancy.available():
        return empty
    try:
        org_id, _ = tenancy.org_and_user()
        return _sync_state(org_id, thread_ref)
    except Exception:
        log.warning("branch state failed for %s", thread_ref, exc_info=True)
        return empty


def visible_event_ids_sync(thread_ref: str) -> set[str] | None:
    """Synchronous twin, for the read paths that are already synchronous.

    Blocking the event loop is the obvious objection and it does not survive
    contact with what these callers already do: `chat_store._fold` reads and
    parses the whole log file, which on the operator's largest thread is 4.5 MB,
    on the same thread. Two indexed queries are cheaper than that by orders of
    magnitude. Making them async would mean making the fold async, which is a
    much larger change than this one is worth.
    """
    if not tenancy.available():
        return None
    try:
        org_id, _ = tenancy.org_and_user()
        return _sync_visible(org_id, thread_ref)
    except Exception:
        log.warning("branch filter failed for %s", thread_ref, exc_info=True)
        return None


async def visible_event_ids(thread_ref: str) -> set[str] | None:
    """The file-event ids on the current branch, or None for "show everything".

    None is returned for a thread that has never been branched AND for any
    failure, which are deliberately the same answer: both mean the file's own
    order is the whole truth, and that is what the transcript did before this.
    """
    if not tenancy.available():
        return None
    try:
        org_id, _ = tenancy.org_and_user()
        return await run_in_threadpool(_sync_visible, org_id, thread_ref)
    except Exception:
        log.warning("branch filter failed for %s", thread_ref, exc_info=True)
        return None


def _sync_record(org_id, user_id, thread_ref: str, ev: dict, meta: dict) -> None:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            t = chat_repo.create_thread(
                s, org_id, user_id, external_id=thread_ref,
                context_key=meta.get("context_key") or "general",
                scope=meta.get("scope") or "workshop",
                title=meta.get("title"))
        kind = ev.get("kind")
        if kind == "boundary":
            chat_repo.append_message(s, t["id"], role="assistant",
                                     kind="boundary", content="",
                                     boundary_mode=ev.get("mode"),
                                     external_id=ev.get("id"))
        elif kind == "scope":
            chat_repo.append_message(s, t["id"], role="assistant", kind="scope",
                                     content="", scope_from=ev.get("from"),
                                     scope_to=ev.get("to"),
                                     external_id=ev.get("id"))
        elif kind == "message":
            m = dict(ev.get("meta") or {})
            segments = m.pop("segments", None)
            for k in ("cut_id", "cut_head", "at_unit", "at_segment",
                      "during_run", "during_turn", "injected", "streaming"):
                m.pop(k, None)
            chat_repo.append_message(
                s, t["id"], role=ev.get("role") or "user",
                content=ev.get("content") or "", segments=segments,
                meta=m or None, client_id=ev.get("client_id"),
                external_id=ev.get("id"))


async def record_message(thread_ref: str, ev: dict, meta: dict | None = None
                         ) -> None:
    """Mirror one appended log event into the tree.

    Best-effort by design: the file log is still the durable write, and a chat
    must not fail to send because the database is unreachable. What is lost when
    this fails is a message's PLACE in the tree, and the backfill is re-runnable
    for exactly that reason.
    """
    if not tenancy.available():
        return
    try:
        org_id, user_id = tenancy.org_and_user()
        await run_in_threadpool(_sync_record, org_id, user_id, thread_ref, ev,
                                meta or {})
    except Exception:
        log.warning("mirror-write failed for %s", thread_ref, exc_info=True)
