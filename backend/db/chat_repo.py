"""Reads and writes of the chat transcript tree (ADR 0020).

THE ONE IDEA: the rendered transcript is the path from a branch's head up to the
root, and nothing else computes an order. `parent_id` is the order. `seq` is an
insertion counter and a sync cursor, and it is deliberately a different number,
because in the file log those two were the same and that is exactly why a
message sent while a reply was streaming could not be placed without a second
mechanism bolted on beside it.

THE ONE OPERATION: `branch_from(message)`. Going back to an earlier point and
carrying on is the same act as branching from it, because nothing is deleted to
do either. What separates the two in other products is only whether the
abandoned continuation is still reachable, and here it always is: it keeps its
ref. So there is one verb, not two, and the confirmation says what actually
happens rather than warning about a loss that cannot occur.

Sessions are synchronous, matching `db/engine.py`. FastAPI's chat routes are all
`async def`, so they must call these through `run_in_threadpool` (see
`db.aio.run_db`) rather than blocking the event loop.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.engine import tenant_session
from db.ids import uuid7

__all__ = [
    "MAX_PATH", "BranchError", "MessageNotFound",
    "transcript", "path_ids", "list_branches", "branch_from", "switch_branch",
    "rename_branch", "current_branch", "append_message", "thread_by_ref",
    "create_thread", "read_transcript", "read_branches",
]

# A conversation is not allowed to be deeper than this. It is a runaway guard,
# not a product limit: the deepest real thread on this installation is ~250
# messages, and `parent_id` is immutable by trigger so a cycle cannot be built
# in the first place. If this ever trips, something has corrupted the tree and
# looping forever is the worse failure.
MAX_PATH = 100_000


class BranchError(RuntimeError):
    """A branch operation that cannot be performed as asked."""


class MessageNotFound(BranchError):
    """The anchor message is not in this thread (or is deleted)."""


# The path walk. Anchored on the branch head, climbing `parent_id`.
#
# `depth DESC` returns it root-first, which is render order. Doing the ordering
# here rather than reversing in Python matters only for readability, but the
# depth guard does not: an unbounded recursive CTE over a cyclic graph does not
# error, it runs until the machine gives out.
_PATH = """
WITH RECURSIVE walk AS (
    SELECT m.id, m.parent_id, 0 AS depth
      FROM chat_message m
     WHERE m.id = :head AND m.deleted_at IS NULL
    UNION ALL
    SELECT m.id, m.parent_id, w.depth + 1
      FROM chat_message m
      JOIN walk w ON m.id = w.parent_id
     WHERE m.deleted_at IS NULL AND w.depth < :max_path
)
"""

_TRANSCRIPT = _PATH + """
SELECT m.id, m.parent_id, m.turn_id, m.role, m.kind, m.content, m.segments,
       m.meta, m.boundary_mode, m.scope_from, m.scope_to, m.seq, m.client_id,
       m.external_id, m.created_at,
       t.run_id, t.model AS turn_model, t.status AS turn_status,
       t.started_at AS turn_started_at, t.ended_at AS turn_ended_at,
       t.error AS turn_error, t.timeout AS turn_timeout,
       t.stopped AS turn_stopped, t.agent_slug AS turn_agent,
       w.depth
  FROM walk w
  JOIN chat_message m ON m.id = w.id
  LEFT JOIN chat_turn t ON t.id = m.turn_id
 ORDER BY w.depth DESC
"""


def _row(r: Any, is_turn_tail: bool = True) -> dict:
    """One transcript node, in the shape the chat API already hands the client.

    `meta` is rebuilt rather than returned raw: the turn-level facts (model,
    timings, how it ended) live on `chat_turn` now, but the client reads them
    from `msg.meta`, and there is no reason to make it learn a new shape for
    facts that did not change. Same compatibility argument as `db/repo.py`.

    `is_turn_tail` is why this is not a plain row-to-dict. A turn interrupted
    four times is four messages, and the turn's OUTCOME belongs to the last one
    only. Put `error` on all four and a failed turn renders as four red bubbles;
    put `ended_at` on all four and each stretch claims the whole turn's timing.
    The client used to strip those itself while unpacking one big event; now the
    rows are real, so the answer is recorded here instead of re-derived there.
    """
    m = dict(r._mapping)
    meta: dict[str, Any] = dict(m.pop("meta") or {})
    segments = m.pop("segments")
    if segments is not None:
        meta["segments"] = segments

    turn_id = m.pop("turn_id")
    run_id = m.pop("run_id")
    if turn_id is not None and not is_turn_tail:
        # Every stretch still says which turn it belongs to, so the UI can group
        # them; it just does not claim the turn's ending.
        meta["turn_id"] = run_id
        meta["streaming"] = False
        if m.get("turn_model") is not None:
            meta["model"] = m["turn_model"]
    elif turn_id is not None:
        # `turn_id` on the wire has always been the RUN uuid, not a row id.
        meta["turn_id"] = run_id
        meta["streaming"] = False
        for src, dst in (("turn_model", "model"), ("turn_agent", "agent"),
                         ("turn_stopped", "stopped")):
            if m.get(src) is not None:
                meta[dst] = m[src]
        for src, dst in (("turn_started_at", "started_at"),
                         ("turn_ended_at", "ended_at")):
            if m.get(src) is not None:
                meta[dst] = m[src].isoformat()
        for src, dst in (("turn_error", "error"), ("turn_timeout", "timeout")):
            if m.get(src):
                meta[dst] = True
    for k in ("turn_model", "turn_agent", "turn_stopped", "turn_started_at",
              "turn_ended_at", "turn_error", "turn_timeout", "turn_status"):
        m.pop(k, None)

    depth = m.pop("depth", None)
    out = {
        "id": str(m["id"]),
        "parent_id": str(m["parent_id"]) if m["parent_id"] else None,
        "role": m["role"],
        "kind": m["kind"],
        "content": m["content"],
        "seq": m["seq"],
        "ts": m["created_at"].isoformat(),
        "client_id": m["client_id"],
        "external_id": m["external_id"],
        "depth": depth,
    }
    if m["kind"] == "boundary":
        out["mode"] = m["boundary_mode"]
    elif m["kind"] == "scope":
        out["from"] = m["scope_from"]
        out["to"] = m["scope_to"]
    if meta:
        out["meta"] = meta
    return out


def thread_by_ref(s: Session, ref: str) -> dict | None:
    """Resolve a thread by new uuid or by the 32-hex id everything already uses.

    Both are accepted for the whole life of the migration: every existing URL,
    bookmark and `claude_session` mapping names a thread by the old one.
    """
    # external_id FIRST, and that order is load-bearing rather than a
    # preference. A legacy thread id is 32 hex characters, which `UUID()` parses
    # perfectly happily, so a "is it a uuid?" test says yes for every id this
    # installation has ever minted and the lookup would never reach the legacy
    # column at all. Ask the cheap, exact question first, then fall back.
    cols = ("id, external_id, current_branch_id, next_seq, owner_user_id, "
            "title, scope, context_key, mode")
    row = s.execute(text(
        f"SELECT {cols} FROM chat_thread "
        f"WHERE external_id = :ref AND deleted_at IS NULL"), {"ref": str(ref)}
    ).one_or_none()
    if row is None:
        try:
            UUID(str(ref))
        except (ValueError, AttributeError, TypeError):
            return None
        # CAST(...), not `:ref::uuid`: the `::` cast operator collides with the
        # named-parameter marker and psycopg rejects the statement outright.
        row = s.execute(text(
            f"SELECT {cols} FROM chat_thread "
            f"WHERE id = CAST(:ref AS uuid) AND deleted_at IS NULL"),
            {"ref": str(ref)}).one_or_none()
    if row is None:
        return None
    d = dict(row._mapping)
    d["id"] = str(d["id"])
    d["current_branch_id"] = (str(d["current_branch_id"])
                              if d["current_branch_id"] else None)
    d["owner_user_id"] = str(d["owner_user_id"])
    return d


def _head_of(s: Session, thread_id: str | UUID, branch_id: str | UUID | None
             ) -> UUID | None:
    if branch_id is None:
        return None
    return s.scalar(text(
        "SELECT head_message_id FROM chat_branch "
        "WHERE id = :b AND thread_id = :t AND deleted_at IS NULL"),
        {"b": str(branch_id), "t": str(thread_id)})


def current_branch(s: Session, thread_id: str | UUID) -> dict | None:
    row = s.execute(text(
        "SELECT b.id, b.name, b.ordinal, b.fork_message_id, b.head_message_id, "
        "       b.parent_branch_id, b.created_at "
        "  FROM chat_thread t JOIN chat_branch b ON b.id = t.current_branch_id "
        " WHERE t.id = :t AND b.deleted_at IS NULL"), {"t": str(thread_id)}
    ).one_or_none()
    return None if row is None else {
        k: (str(v) if isinstance(v, UUID) else v)
        for k, v in dict(row._mapping).items()}


def path_ids(s: Session, thread_id: str | UUID,
             branch_id: str | UUID | None = None) -> list[str]:
    """The ids on a branch, root first. The cheap form of `transcript()`, for
    callers that only need to know what is visible."""
    if branch_id is None:
        cur = current_branch(s, thread_id)
        branch_id = cur["id"] if cur else None
    head = _head_of(s, thread_id, branch_id)
    if head is None:
        return []
    rows = s.execute(text(_PATH + "SELECT w.id FROM walk w ORDER BY w.depth DESC"),
                     {"head": str(head), "max_path": MAX_PATH}).all()
    return [str(r[0]) for r in rows]


def transcript(s: Session, thread_id: str | UUID,
               branch_id: str | UUID | None = None) -> list[dict]:
    """Every visible node on a branch, in render order.

    Defaults to the thread's current branch. Passing `branch_id` renders another
    branch without switching to it, which is what a preview would need.
    """
    if branch_id is None:
        t = thread_by_ref(s, str(thread_id))
        if t is None:
            return []
        branch_id = t["current_branch_id"]
    head = _head_of(s, thread_id, branch_id)
    if head is None:
        return []
    rows = s.execute(text(_TRANSCRIPT),
                     {"head": str(head), "max_path": MAX_PATH}).all()
    # Which row is the last one of its turn. Computed over the PATH rather than
    # over the whole table on purpose: an abandoned branch can hold later
    # stretches of the same turn, and they are not on this transcript, so the
    # tail here is the last one the reader can actually see.
    last_of_turn: dict[Any, int] = {}
    for i, r in enumerate(rows):
        tid = r._mapping["turn_id"]
        if tid is not None:
            last_of_turn[tid] = i
    return [
        _row(r, is_turn_tail=(r._mapping["turn_id"] is None
                              or last_of_turn.get(r._mapping["turn_id"]) == i))
        for i, r in enumerate(rows)
    ]


# The picker. One row per branch, with what a reader needs to choose between
# them: where it split off, and what was last said on it.
_BRANCHES = """
SELECT b.id, b.name, b.ordinal, b.created_at,
       b.fork_message_id, b.head_message_id, b.parent_branch_id,
       (b.id = t.current_branch_id)          AS is_current,
       fm.content                            AS fork_content,
       fm.role                               AS fork_role,
       fm.created_at                         AS fork_at,
       hm.content                            AS head_content,
       hm.created_at                         AS head_at
  FROM chat_branch b
  JOIN chat_thread t             ON t.id = b.thread_id
  LEFT JOIN chat_message fm      ON fm.id = b.fork_message_id
  LEFT JOIN chat_message hm      ON hm.id = b.head_message_id
 WHERE b.thread_id = :t AND b.deleted_at IS NULL
 ORDER BY COALESCE(hm.created_at, b.created_at) DESC, b.ordinal DESC
"""


def _snip(value: str | None, n: int = 90) -> str:
    s = " ".join((value or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def list_branches(s: Session, thread_id: str | UUID) -> list[dict]:
    """Most recently active first, so the picker's top row is the useful one.

    The current branch is included and flagged rather than filtered out: a list
    that silently omits where you are makes the other entries unreadable.
    """
    out = []
    for r in s.execute(text(_BRANCHES), {"t": str(thread_id)}).all():
        m = dict(r._mapping)
        out.append({
            "id": str(m["id"]),
            "name": m["name"],
            "ordinal": m["ordinal"],
            "is_current": bool(m["is_current"]),
            "is_root": m["parent_branch_id"] is None,
            "created_at": m["created_at"].isoformat(),
            "fork_message_id": (str(m["fork_message_id"])
                                if m["fork_message_id"] else None),
            "forked_from": _snip(m["fork_content"]) or None,
            "forked_from_role": m["fork_role"],
            "forked_at": m["fork_at"].isoformat() if m["fork_at"] else None,
            "preview": _snip(m["head_content"]) or None,
            "last_at": (m["head_at"] or m["created_at"]).isoformat(),
        })
    return out


def _message_in_thread(s: Session, thread_id: str | UUID, message_id: str | UUID
                       ) -> dict:
    row = s.execute(text(
        "SELECT id, role, content, created_at FROM chat_message "
        "WHERE id = :m AND thread_id = :t AND deleted_at IS NULL"),
        {"m": str(message_id), "t": str(thread_id)}).one_or_none()
    if row is None:
        raise MessageNotFound(
            f"message {message_id} is not in thread {thread_id}")
    return dict(row._mapping)


def branch_from(s: Session, thread_id: str | UUID, message_id: str | UUID,
                *, name: str | None = None) -> dict:
    """Go back to `message_id` and carry on from there.

    This is the whole feature. It creates a ref at that point and makes it
    current; the branch that was current keeps its own head, so everything after
    the anchor stays reachable and listed. Nothing is deleted, ever.

    Two no-ops, both of which a real operator will hit:

    - forking from the point you are already at (the head) changes nothing, so
      it returns the current branch rather than growing a chain of identical
      refs;
    - forking twice from the same anchor without having said anything in
      between reuses the empty branch, for the same reason.
    """
    # The thread row is the lock for everything in this conversation: the
    # ordinal allocation and the current-branch swap have to be one atomic act,
    # or two clicks land two branches with the same number.
    locked = s.execute(text(
        "SELECT id, org_id, current_branch_id FROM chat_thread "
        "WHERE id = :t AND deleted_at IS NULL FOR UPDATE"),
        {"t": str(thread_id)}).one_or_none()
    if locked is None:
        raise BranchError(f"no such thread: {thread_id}")
    org_id = locked._mapping["org_id"]

    anchor = _message_in_thread(s, thread_id, message_id)
    cur = current_branch(s, thread_id)

    if cur is not None:
        if str(cur["head_message_id"]) == str(anchor["id"]):
            return cur
        if (cur["fork_message_id"] is not None
                and str(cur["fork_message_id"]) == str(anchor["id"])
                and str(cur["head_message_id"]) == str(anchor["id"])):
            return cur

    ordinal = (s.scalar(text(
        "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM chat_branch "
        "WHERE thread_id = :t"), {"t": str(thread_id)}) or 1)
    new_id = uuid7()
    s.execute(text(
        "INSERT INTO chat_branch "
        "  (id, org_id, thread_id, parent_branch_id, fork_message_id, "
        "   head_message_id, ordinal, name) "
        "VALUES (:id, :org, :t, :parent, :fork, :fork, :ord, :name)"),
        {"id": new_id, "org": org_id, "t": str(thread_id),
         "parent": cur["id"] if cur else None,
         "fork": str(anchor["id"]), "ord": ordinal,
         "name": name or f"Branch {ordinal}"})
    s.execute(text("UPDATE chat_thread SET current_branch_id = :b WHERE id = :t"),
              {"b": new_id, "t": str(thread_id)})
    return current_branch(s, thread_id) or {}


def switch_branch(s: Session, thread_id: str | UUID, branch_id: str | UUID
                  ) -> dict:
    """Make another branch current. One UPDATE, and nothing else moves."""
    owned = s.scalar(text(
        "SELECT 1 FROM chat_branch WHERE id = :b AND thread_id = :t "
        "AND deleted_at IS NULL"),
        {"b": str(branch_id), "t": str(thread_id)})
    if not owned:
        raise BranchError(f"branch {branch_id} is not in thread {thread_id}")
    s.execute(text("UPDATE chat_thread SET current_branch_id = :b WHERE id = :t"),
              {"b": str(branch_id), "t": str(thread_id)})
    return current_branch(s, thread_id) or {}


def rename_branch(s: Session, thread_id: str | UUID, branch_id: str | UUID,
                  name: str) -> dict:
    clean = " ".join((name or "").split())[:120]
    if not clean:
        raise BranchError("a branch name cannot be empty")
    n = s.execute(text(
        "UPDATE chat_branch SET name = :n WHERE id = :b AND thread_id = :t "
        "AND deleted_at IS NULL"),
        {"n": clean, "b": str(branch_id), "t": str(thread_id)}).rowcount
    if not n:
        raise BranchError(f"branch {branch_id} is not in thread {thread_id}")
    return current_branch(s, thread_id) or {}


def append_message(s: Session, thread_id: str | UUID, *, role: str,
                   content: str = "", kind: str = "message",
                   segments: list | None = None, meta: dict | None = None,
                   turn_id: str | UUID | None = None,
                   client_id: str | None = None,
                   external_id: str | None = None,
                   boundary_mode: str | None = None,
                   scope_from: str | None = None, scope_to: str | None = None,
                   parent_id: str | UUID | None = None) -> dict:
    """Append to the current branch and advance its head.

    `parent_id` defaults to the branch head, which is what makes "send a message
    after going back" land on the branch instead of after the abandoned tail.
    Passing it explicitly is for the backfill, which is rebuilding a known tree
    rather than extending a live one.
    """
    locked = s.execute(text(
        "SELECT id, org_id, current_branch_id, next_seq FROM chat_thread "
        "WHERE id = :t AND deleted_at IS NULL FOR UPDATE"),
        {"t": str(thread_id)}).one_or_none()
    if locked is None:
        raise BranchError(f"no such thread: {thread_id}")
    lm = locked._mapping
    org_id, branch_id, seq = lm["org_id"], lm["current_branch_id"], lm["next_seq"]

    if client_id:
        existing = s.execute(text(
            "SELECT id FROM chat_message WHERE thread_id = :t "
            "AND client_id = :c AND deleted_at IS NULL"),
            {"t": str(thread_id), "c": client_id}).one_or_none()
        if existing is not None:
            # Idempotent resend. Was a linear scan of the log; now the unique
            # index makes the check and the insert race-free together.
            return {"id": str(existing._mapping["id"]), "seq": None,
                    "duplicate": True}

    if parent_id is None:
        parent_id = _head_of(s, thread_id, branch_id)

    msg_id = uuid7()
    s.execute(text(
        "INSERT INTO chat_message "
        "  (id, org_id, thread_id, parent_id, turn_id, role, kind, content, "
        "   segments, meta, boundary_mode, scope_from, scope_to, seq, "
        "   client_id, external_id) "
        "VALUES (:id, :org, :t, :parent, :turn, :role, :kind, :content, "
        "        :segments, :meta, :bmode, :sfrom, :sto, :seq, :cid, :ext)"),
        {"id": msg_id, "org": org_id, "t": str(thread_id),
         "parent": str(parent_id) if parent_id else None,
         "turn": str(turn_id) if turn_id else None,
         "role": role, "kind": kind, "content": content or "",
         "segments": _json(segments), "meta": _json(meta),
         "bmode": boundary_mode, "sfrom": scope_from, "sto": scope_to,
         "seq": seq, "cid": client_id, "ext": external_id})
    s.execute(text("UPDATE chat_thread SET next_seq = :n WHERE id = :t"),
              {"n": seq + 1, "t": str(thread_id)})
    if branch_id is not None:
        s.execute(text("UPDATE chat_branch SET head_message_id = :m WHERE id = :b"),
                  {"m": msg_id, "b": str(branch_id)})
    return {"id": str(msg_id), "seq": seq, "duplicate": False}


_TURN_FIELDS = ("status", "model", "agent_slug", "started_at", "ended_at",
                "error", "timeout", "stopped")


def upsert_turn(s: Session, thread_id: str | UUID, run_id: str, **fields) -> UUID:
    """The `chat_turn` row for this run, created if it is new, updated if it is
    not. Returns its id.

    `run_id` is globally unique, so it is an exact "have I seen this run" test
    and the insert can be conflict-tolerant without a prior read. That matters
    here more than it did in the backfill: a turn is written TWICE on the live
    path, once when it starts producing structure and once when it ends, and the
    second write is the one that carries how it went.

    Only fields that were passed are written. A turn opened mid-flight knows its
    model and start; it does not yet know whether it errored, and a later update
    must not blank what an earlier one recorded.
    """
    org = s.scalar(text("SELECT org_id FROM chat_thread WHERE id = :t"),
                   {"t": str(thread_id)})
    if org is None:
        raise BranchError(f"no such thread: {thread_id}")
    given = {k: v for k, v in fields.items() if k in _TURN_FIELDS and v is not None}
    given.setdefault("status", "running")
    cols = {"id": uuid7(), "org": str(org), "t": str(thread_id), "run": run_id, **given}
    names = ", ".join(["id", "org_id", "thread_id", "run_id", *given])
    binds = ", ".join([":id", ":org", ":t", ":run", *(f":{k}" for k in given)])
    updates = ", ".join(f"{k} = EXCLUDED.{k}" for k in given)
    s.execute(text(
        f"INSERT INTO chat_turn ({names}) VALUES ({binds}) "
        f"ON CONFLICT (run_id) DO UPDATE SET {updates}"), cols)
    return s.scalar(text("SELECT id FROM chat_turn WHERE run_id = :r"), {"r": run_id})


def turn_stretches(s: Session, turn_id: str | UUID) -> list[UUID]:
    """The rows already opened for this turn, in the order they were opened.

    Ordered by `seq`, the per-thread insertion counter, which is exactly what it
    is for: these rows were inserted one per interruption as the turn ran, so
    insertion order IS stretch order. Render order (the parent chain) would give
    the same answer here and is more work to ask for.
    """
    rows = s.execute(text(
        "SELECT id FROM chat_message WHERE turn_id = :t AND deleted_at IS NULL "
        "ORDER BY seq"), {"t": str(turn_id)}).all()
    return [r[0] for r in rows]


def fill_message(s: Session, message_id: str | UUID, *, content: str | None = None,
                 segments: list | None = None, meta: dict | None = None,
                 external_id: str | None = None) -> None:
    """Write the CONTENT of a row whose place in the tree was already fixed.

    This is the half of a stretch that is not known when the stretch is opened.
    A reply that gets interrupted has its structure decided the moment it is
    interrupted (`build_streaming.seal_open_text` says so) and its text decided
    when the turn ends; the row is created at the first moment and completed at
    the second.

    It updates content, never `parent_id`. Parentage is immutable by trigger and
    must stay that way: it is what makes a cycle unbuildable rather than merely
    detectable. Nothing here needs to move a row, only to finish one.
    """
    sets, params = [], {"id": str(message_id)}
    for name, value in (("content", content), ("segments", _json(segments)),
                        ("meta", _json(meta)), ("external_id", external_id)):
        if value is not None:
            sets.append(f"{name} = :{name}")
            params[name] = value
    if not sets:
        return
    s.execute(text(f"UPDATE chat_message SET {', '.join(sets)} WHERE id = :id"),
              params)


def create_thread(s: Session, org_id: UUID | str, owner_user_id: UUID | str, *,
                  context_key: str = "general", scope: str = "workshop",
                  external_id: str | None = None, title: str | None = None,
                  **fields) -> dict:
    """A new conversation, with its root branch.

    The root branch is created here and not lazily, so `current_branch_id` is
    never null on a live thread and no read path needs a "what if there is no
    branch yet" case. It is named "Main" because that is what it is: the line
    the conversation started on.
    """
    thread_id = uuid7()
    cols = {"id": thread_id, "org_id": str(org_id),
            "owner_user_id": str(owner_user_id), "context_key": context_key,
            "scope": scope, "external_id": external_id, "title": title}
    allowed = ("mode", "model", "effort", "permission_mode", "seed_title",
               "title_source", "agent_slug", "scope_locked", "summary",
               "claude_session", "voice_session", "context_tokens",
               "archived_at", "created_at", "updated_at")
    for k, v in fields.items():
        if k in allowed and v is not None:
            cols[k] = v
    names = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    s.execute(text(f"INSERT INTO chat_thread ({names}) VALUES ({binds})"), cols)

    branch_id = uuid7()
    s.execute(text(
        "INSERT INTO chat_branch (id, org_id, thread_id, ordinal, name) "
        "VALUES (:id, :org, :t, 1, 'Main')"),
        {"id": branch_id, "org": str(org_id), "t": thread_id})
    s.execute(text("UPDATE chat_thread SET current_branch_id = :b WHERE id = :t"),
              {"b": branch_id, "t": thread_id})
    return {"id": str(thread_id), "current_branch_id": str(branch_id),
            "external_id": external_id}


def _json(value):
    """psycopg needs JSONB bound explicitly; None stays SQL NULL."""
    if value is None:
        return None
    return Jsonb(json.loads(json.dumps(value, default=str)))


# --- the org-scoped entry points --------------------------------------------
# Everything above takes a Session so a caller can compose several operations in
# ONE transaction, which the branch operations need. These are the convenience
# wrappers for the single-shot case.

def read_transcript(org_id: UUID | str, thread_ref: str) -> list[dict]:
    with tenant_session(org_id) as s:
        t = thread_by_ref(s, thread_ref)
        return [] if t is None else transcript(s, t["id"])


def read_branches(org_id: UUID | str, thread_ref: str) -> list[dict]:
    with tenant_session(org_id) as s:
        t = thread_by_ref(s, thread_ref)
        return [] if t is None else list_branches(s, t["id"])
