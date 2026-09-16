"""Branching, from the chat routes' point of view.

`db/chat_repo.py` speaks in tree nodes and uuids. The chat surface speaks in
FILE EVENT IDS (the ULID on each `log.jsonl` line), because that is what the
transcript on screen is built from and what the client can point at. This module
is the translation, plus the async boundary: `db/engine.py` is a synchronous
SQLAlchemy stack and every chat route is `async def`, so a session opened on the
event loop would block it. Everything here hops to the threadpool.

WHY THE READ PATH CAN TRUST THIS. `hidden_event_ids` returns None -- meaning
"hide nothing" -- for a thread that has never been branched, which is
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

__all__ = ["list_branches", "fork", "switch", "rename", "hidden_event_ids",
           "hidden_event_ids_sync", "record_message", "record_meta",
           "keep_visible"]


def keep_visible(events: list[dict], hidden: set[str] | None) -> list[dict]:
    """Drop the events that are on a branch this thread is not showing.

    THE ARGUMENT IS WHAT TO HIDE, NOT WHAT TO SHOW, and that is the whole
    correctness argument rather than a preference. An event the tree has never
    heard of -- the mirror-write failed, or it landed in the window between the
    log append and the mirror -- is not in the hidden set, so it renders. Filter
    by an allow-list instead and that same event disappears, which is a message
    the operator just sent vanishing from their own chat. One of those two
    failures is recoverable by looking again and the other is not.

    `None` means hide nothing: no branches, or the database was unreachable.
    """
    if not hidden:
        return events
    return [ev for ev in events
            if ev.get("id") not in hidden or ev.get("kind") in ("turn", "title")]


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


def _resolve_anchor(s, thread_id: str, ref: str, mode: str = "after"
                    ) -> str | None:
    """A client-supplied anchor to the message to carry on FROM.

    Accepts either a message uuid or the ULID the file log gave the event. The
    ULID is the interesting case: an interrupted reply became SEVERAL rows that
    all carry it, and "branch from this reply" means from the END of it, so the
    last one wins. Anchoring on the first stretch would silently drop the rest
    of a reply the operator was pointing AT, not before.

    TWO MODES, because a reply and your own message mean different things when
    you point at them.

      after   keep this message, drop what follows. What a reply means: "this
              answer was fine, the conversation after it was not."
      before  drop this message too. What YOUR message means: "take back what I
              said here", so the anchor is its PARENT and the conversation ends
              just before you typed. Pointing at your own message and keeping it
              would leave you re-sending into your own last word.

    `before` on the first message of a thread has no parent and returns the
    empty-string sentinel, which the caller reports rather than silently
    treating as "the whole conversation".
    """
    from sqlalchemy import text
    row = s.execute(text(
        "SELECT id, parent_id FROM chat_message "
        " WHERE thread_id = :t AND deleted_at IS NULL "
        "   AND (external_id = :ref OR CAST(id AS text) = :ref) "
        " ORDER BY seq {} LIMIT 1".format("ASC" if mode == "before" else "DESC")),
        {"t": str(thread_id), "ref": str(ref)}).one_or_none()
    if row is None:
        return None
    if mode == "before":
        parent = row._mapping["parent_id"]
        return "" if parent is None else str(parent)
    return str(row._mapping["id"])


def _sync_fork(org_id, thread_ref: str, anchor: str, mode: str = "after") -> dict:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            raise chat_repo.BranchError(f"no such thread: {thread_ref}")
        msg_id = _resolve_anchor(s, t["id"], anchor, mode)
        if msg_id is None:
            raise chat_repo.MessageNotFound(
                f"no message {anchor} in thread {thread_ref}")
        if msg_id == "":
            raise chat_repo.BranchError(
                "this is the first message in the chat, so there is no point "
                "before it to go back to")
        branch = chat_repo.branch_from(s, t["id"], msg_id)
        return {"branch": branch, "branches": chat_repo.list_branches(s, t["id"])}


async def fork(thread_ref: str, anchor: str, mode: str = "after") -> dict:
    org_id, _ = tenancy.org_and_user()
    return await run_in_threadpool(_sync_fork, org_id, thread_ref, anchor, mode)


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
    come from the same two rows. Returns `hidden=None` for "show everything".
    """
    from sqlalchemy import text

    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            return {"branch": None, "count": 0, "hidden": None}
        row = s.execute(text(
            "SELECT count(*) AS n, "
            "       max(CASE WHEN b.id = th.current_branch_id THEN b.name END) AS nm, "
            # The head's FILE event id, because that is the id the transcript
            # renders with and therefore the one the client can compare against.
            # It is what lets the menu grey out an action that would be a no-op
            # instead of accepting the click and appearing to do nothing.
            "       max(CASE WHEN b.id = th.current_branch_id "
            "                THEN hm.external_id END) AS head "
            "  FROM chat_branch b "
            "  JOIN chat_thread th ON th.id = b.thread_id "
            "  LEFT JOIN chat_message hm ON hm.id = b.head_message_id "
            " WHERE b.thread_id = :t AND b.deleted_at IS NULL"),
            {"t": t["id"]}).one()
        n = int(row._mapping["n"] or 0)
        branch = ({"id": t["current_branch_id"], "name": row._mapping["nm"],
                   "head": row._mapping["head"]}
                  if t["current_branch_id"] else None)
        # NEVER BRANCHED means never filtered. One branch is the state every
        # thread starts in and stays in until the operator uses the feature, so
        # this is the answer for almost every read, and it makes the whole
        # mechanism inert until it is asked for.
        if n < 2:
            return {"branch": branch, "count": n, "hidden": None}
        ids = chat_repo.path_ids(s, t["id"])
        # Everything in this conversation that is NOT on the current path. Asked
        # this way round because it is the set the filter needs and because it
        # is the smaller one: a thread is mostly the line you are on.
        rows = s.execute(text(
            "SELECT external_id FROM chat_message "
            " WHERE thread_id = :t AND external_id IS NOT NULL "
            "   AND deleted_at IS NULL "
            "   AND NOT (id = ANY(CAST(:ids AS uuid[])))"),
            {"t": t["id"], "ids": "{" + ",".join(ids) + "}"}).all()
        return {"branch": branch, "count": n, "hidden": {r[0] for r in rows}}


def _sync_hidden(org_id, thread_ref: str) -> set[str] | None:
    return _sync_state(org_id, thread_ref)["hidden"]


def branch_state_sync(thread_ref: str) -> dict:
    """`{branch, count, hidden}` for the sync endpoint, or all-None when the
    relational store is unreachable."""
    empty = {"branch": None, "count": 0, "hidden": None}
    if not tenancy.available():
        return empty
    try:
        org_id, _ = tenancy.org_and_user()
        return _sync_state(org_id, thread_ref)
    except Exception:
        log.warning("branch state failed for %s", thread_ref, exc_info=True)
        return empty


def hidden_event_ids_sync(thread_ref: str) -> set[str] | None:
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
        return _sync_hidden(org_id, thread_ref)
    except Exception:
        log.warning("branch filter failed for %s", thread_ref, exc_info=True)
        return None


async def hidden_event_ids(thread_ref: str) -> set[str] | None:
    """The file-event ids on the current branch, or None for "show everything".

    None is returned for a thread that has never been branched AND for any
    failure, which are deliberately the same answer: both mean the file's own
    order is the whole truth, and that is what the transcript did before this.
    """
    if not tenancy.available():
        return None
    try:
        org_id, _ = tenancy.org_and_user()
        return await run_in_threadpool(_sync_hidden, org_id, thread_ref)
    except Exception:
        log.warning("branch filter failed for %s", thread_ref, exc_info=True)
        return None


# Keys that say WHERE a message landed inside a reply. Structure replaces them,
# so they are not carried into a row's `meta`. Shared with the backfill so the
# live path and the reconstruction cannot drift apart.
_POSITIONAL = ("cut_id", "cut_head", "at_unit", "at_segment", "during_turn",
               "during_turn", "injected", "streaming", "segments")
# Keys that live on `chat_turn`, not on a message.
_TURN_LEVEL = ("turn_id", "model", "agent", "started_at", "ended_at", "error",
               "timeout", "stopped")


def _message_meta(ev: dict) -> dict | None:
    m = {k: v for k, v in (ev.get("meta") or {}).items()
         if k not in _POSITIONAL and k not in _TURN_LEVEL}
    return m or None


def stretches(segments: list[dict]) -> list[list[dict]]:
    """A reply's segments, cut into one list per stretch at the positions the
    server recorded while it was writing.

    The count is the contract: one stretch per recorded `cut_after` ref, plus
    the final one. That is exactly the number of stretch rows that were opened
    while the turn ran (see `open_stretch`), because both come from the same
    events: one `cut_here` call with text already emitted produces one ref and
    one row.

    `cut_head` refs are deliberately NOT counted. They mark messages that beat
    the reply's first word, so there is no stretch before them to open a row
    for; those messages parent to the point the turn started from, which is the
    "the turn's own start is the floor" rule from docs/decisions/0018.

    Two refs on one segment is two messages sent with no output between them,
    which yields an empty stretch between them. That is correct and it is why
    the flush is per ref rather than per segment.
    """
    out: list[list[dict]] = []
    buf: list[dict] = []
    for s in segments:
        if not isinstance(s, dict):
            continue
        buf.append(s)
        for _ in (s.get("cut_after") or ()):
            out.append(buf)
            buf = []
    out.append(buf)
    return out


def _text_of(segs: list[dict]) -> str:
    """What a stretch's bubble says: its top-level prose, the same join the
    client's `textOf` does and the same one the reply's own `content` is."""
    return "".join(s.get("text") or "" for s in segs if s.get("t") == "text")


def _close_turn(s, thread_uuid, ev: dict, ev_meta: dict) -> None:
    """Finish the turn this reply belongs to: fill the stretch rows that were
    opened while it ran, then append the last stretch.

    STRUCTURE WAS DECIDED WHILE IT RAN; only content is decided here. A row's
    `parent_id` is immutable by trigger and nothing here touches it.
    """
    from db import chat_repo

    turn_id = ev_meta.get("turn_id")
    turn_uuid = chat_repo.upsert_turn(
        s, thread_uuid, turn_id,
        status=("error" if ev_meta.get("error") else
                "stopped" if ev_meta.get("stopped") else "finished"),
        model=ev_meta.get("model"), agent_slug=ev_meta.get("agent"),
        started_at=_dt(ev_meta.get("started_at")),
        ended_at=_dt(ev_meta.get("ended_at")),
        error=bool(ev_meta.get("error")), timeout=bool(ev_meta.get("timeout")),
        stopped=ev_meta.get("stopped"))

    opened = chat_repo.turn_stretches(s, turn_uuid)
    # SPLIT THE COMMITTED LIST, never the one that was in memory at seal time.
    # Two paths rewrite a turn's segments at commit: the `/compact` text strip
    # and the trailing-text replacement, which walks back over EVERY trailing
    # text segment including sealed ones and replaces them with the cleaned
    # answer. `carry_cuts` keeps the refs, so the positions survive, but the
    # prose of an earlier stretch can genuinely change. Reading the final list is
    # what makes that a non-event here.
    pieces = stretches([x for x in (ev_meta.get("segments") or [])
                        if isinstance(x, dict)])

    # DEGRADE, DO NOT DROP. The counts match whenever every stretch-open landed,
    # which is the normal case; a database blip mid-turn can lose one, and then
    # the only wrong answer is to lose the text with it. Whatever cannot be
    # placed in an opened row goes into the final one, so the split comes out
    # coarser than it should and nothing is missing.
    for i, row in enumerate(opened):
        piece = pieces[i] if i < len(pieces) - 1 else []
        chat_repo.fill_message(s, row, content=_text_of(piece), segments=piece)
    rest = pieces[len(opened):] if len(opened) < len(pieces) else [pieces[-1]]
    tail = [seg for piece in rest for seg in piece]
    if len(opened) > len(pieces) - 1:
        log.warning("turn %s: %d stretch rows for %d stretches; last one holds "
                    "the remainder", turn_id, len(opened), len(pieces))

    # A REPLY NOBODY INTERRUPTED KEEPS ITS OWN `content`, byte for byte. The
    # stored text and the join of the segments disagree on a handful of replies
    # (ADR 0020 measured five), the screen renders `content`, and a turn that was
    # never split has no reason to be re-derived. Only a SPLIT reply takes its
    # text from the pieces, because there is no per-stretch `content` to take.
    chat_repo.append_message(
        s, thread_uuid, role="assistant",
        content=(_text_of(tail) if opened else (ev.get("content") or "")),
        segments=tail or None, meta=_message_meta(ev), turn_id=turn_uuid,
        client_id=ev.get("client_id"), external_id=ev.get("id"),
        created_at=ev.get("ts"))


def _dt(value):
    from datetime import datetime
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _sync_open_stretch(org_id, thread_ref: str, turn_id: str) -> None:
    """Open a row for the stretch of a reply that just ended, so the message
    that ended it has something true to hang from.

    Called the instant `build_streaming.cut_here` seals the open text, which is
    the server stating "this stretch is finished and a message goes next". The
    row's PLACE is knowable at that moment and its TEXT is not, so the place is
    written now and `_close_turn` writes the text when the turn ends.

    NOT the reverted `revises:` slot. That claimed a row for the reply at turn
    START, before any output existed, which is what forced every mid-turn
    message below the whole reply and took five rounds of corrections to undo
    (see the chat_log module docstring). This claims one at a point that is
    already true: text has demonstrably been emitted, and a message
    demonstrably follows it.
    """
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            return
        turn_uuid = chat_repo.upsert_turn(s, t["id"], turn_id, status="running")
        chat_repo.append_message(s, t["id"], role="assistant", content="",
                                 turn_id=turn_uuid)


async def open_stretch(thread_ref: str, turn_id: str) -> None:
    if not tenancy.available():
        return
    try:
        org_id, _ = tenancy.org_and_user()
        await run_in_threadpool(_sync_open_stretch, org_id, thread_ref, turn_id)
    except Exception:
        log.warning("stretch-open failed for %s", thread_ref, exc_info=True)


def _sync_record(org_id, user_id, thread_ref: str, ev: dict, meta: dict) -> None:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            # THE FULL FIELD SET, not a 3-column stand-in. This used to pass
            # only context_key/scope/title, so mode, model, scope_locked,
            # agent_slug and everything else silently took the column
            # defaults (the mode column's server default was "orchestrated"
            # then, so a "direct" chat mirrored as orchestrated) and NOTHING
            # ever corrected them afterward -- the thread
            # list now reads these columns, so a row created this thin would
            # misreport itself for its whole life. `chat_repo._thread_fields`
            # is the same mapping the backfill uses at creation.
            t = chat_repo.create_thread(
                s, org_id, user_id, external_id=thread_ref,
                **chat_repo._thread_fields(meta, s, org_id))
            # `state.meta_updated_at` isn't a plain column create_thread can
            # take a kwarg for; one extra UPDATE, once per thread, at creation.
            chat_repo.sync_thread_fields(s, t["id"], meta, org_id=org_id)
        kind = ev.get("kind")
        if kind == "boundary":
            chat_repo.append_message(s, t["id"], role="assistant",
                                     kind="boundary", content="",
                                     boundary_mode=ev.get("mode"),
                                     external_id=ev.get("id"),
                                     created_at=ev.get("ts"))
        elif kind == "scope":
            chat_repo.append_message(s, t["id"], role="assistant", kind="scope",
                                     content="", scope_from=ev.get("from"),
                                     scope_to=ev.get("to"),
                                     external_id=ev.get("id"),
                                     created_at=ev.get("ts"))
        elif kind == "message":
            ev_meta = ev.get("meta") or {}
            # A REPLY IS A TURN, and a turn that was interrupted is several rows
            # whose places were already fixed while it ran. Recognised by
            # `meta.turn_id`, which only a committed turn carries, so an ordinary
            # message still takes the plain path below and the common case (a
            # turn nobody interrupted) still writes exactly one row.
            if ev.get("role") == "assistant" and ev_meta.get("turn_id"):
                _close_turn(s, t["id"], ev, ev_meta)
                return
            chat_repo.append_message(
                s, t["id"], role=ev.get("role") or "user",
                content=ev.get("content") or "",
                segments=ev_meta.get("segments"), meta=_message_meta(ev),
                client_id=ev.get("client_id"), external_id=ev.get("id"),
                created_at=ev.get("ts"))


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


def _sync_record_meta(org_id, thread_ref: str, meta: dict) -> None:
    from db import chat_repo
    from db.engine import tenant_session
    with tenant_session(org_id) as s:
        t = chat_repo.thread_by_ref(s, thread_ref)
        if t is None:
            # Not mirrored yet -- a later message creates it with the full
            # field set (_sync_record above, via chat_repo._thread_fields).
            # There is no row here to patch.
            return
        chat_repo.sync_thread_fields(s, t["id"], meta, org_id=org_id)


async def record_meta(thread_ref: str, meta: dict) -> None:
    """Mirror a META-ONLY change into the tree: archive/restore, rename, a
    mode/model/scope change.

    `record_message` above is the funnel for a LOGGED event; this is the
    funnel for everything that only ever touched meta.json. Before this,
    none of it reached Postgres at all except by riding along on the next
    message through `chat_repo.create_thread`'s one-time field set -- so an
    archived chat kept reading `archived_at: NULL` and a renamed chat kept
    its creation-time title, both indefinitely, until something else
    happened to send a message on that thread. Measured on the live store
    2026-08-15: three actually-archived threads, all still live in Postgres.

    Best-effort, same as `record_message`: the file's meta.json is still the
    durable write, and a patch must not fail because Postgres is unreachable.
    `services.chat_log.patch_meta_sync` calls this ONLY when the patch
    touches a field the thread list (or a future reader) actually cares
    about -- see `_LIST_RELEVANT_META_FIELDS` there -- so the high-frequency
    bookkeeping patches a live turn makes (active_turn_id, live_message,
    turn_marks, context_tokens) never reach here at all.
    """
    if not tenancy.available():
        return
    try:
        org_id, _ = tenancy.org_and_user()
        await run_in_threadpool(_sync_record_meta, org_id, thread_ref, meta)
    except Exception:
        log.warning("meta mirror-write failed for %s", thread_ref, exc_info=True)
