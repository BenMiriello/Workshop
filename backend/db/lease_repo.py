"""Reads and writes for the `lease` table
(docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md, step B3).

Same shape as `db/worktree_repo.py`: every function takes a `Session` so a
caller can compose several operations in one transaction, and rows come back as
plain dicts straight off raw SQL.

THIS MODULE KNOWS NOTHING ABOUT TICKETS, TREES OR SESSIONS. It stores who holds
what, until when, and at which generation. Deciding that a `todo` ticket may be
claimed, that a chat may write to a directory, or that a presence has gone is
`services/leases.py`'s job one layer up.

EVERY TIME COMPARISON AND EVERY TIME COMPUTATION IS SERVER-SIDE. There is not a
single Python `datetime` in the parameters below, deliberately: `expires_at` is
always `now() + make_interval(...)` evaluated by Postgres and always compared
against Postgres's `now()`. Section 5 states the rule ("server-computed, never a
caller's clock") and it is also the entire answer to clock skew -- the sweeps,
the backend and a chat do not have to agree about the time, because none of them
is ever asked.

ACQUIRE IS ONE STATEMENT. Not a SELECT then an INSERT, not an advisory lock, not
a flock: a single `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE
lease.expires_at < now()`. Two processes running it concurrently are serialized
by Postgres on the unique index, the loser's `DO UPDATE` finds the row no longer
expired and updates nothing, and `RETURNING` gives it no row -- which is how it
learns it lost. See `services/leases.py`'s docstring for why this rather than
`SELECT ... FOR UPDATE SKIP LOCKED`.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.ids import uuid7
from db.models import LEASE_HOLDER_KINDS, LEASE_SUBJECT_KINDS

__all__ = [
    "acquire", "renew", "release", "release_for_holder", "fence",
    "get_lease", "list_leases",
]

# Every column, spelled once. `SELECT *` would be shorter, but the acquire
# statement below has to name its RETURNING list anyway and two lists that must
# agree is how a column silently stops being read.
_COLUMNS = (
    "id, org_id, subject_kind, subject_id, holder_kind, holder_id, "
    "principal_user_id, generation, expires_at, acquired_at, last_renewed_at, "
    "last_work_at, created_at, updated_at"
)

# `expires_at > now()` is the whole definition of "held" (section 6: two states,
# not three). Spelled once so no caller can invent a slightly different one.
_HELD = "expires_at > now()"

# What a release WRITES, and it is not a bare `now()`. Postgres's `now()` is
# `transaction_timestamp()`, which is constant for the whole transaction, so a
# row expiring at exactly `now()` is NOT expired to the `expires_at < now()`
# comparison the acquire makes -- and `services/leases.acquire` releases a dead
# interactive holder and re-acquires in ONE transaction, which is precisely that
# case. Backdating by a microsecond makes "released" mean expired to every
# reader, including one that shares the transaction. Found by the test for
# section 6's passive presence rule, which failed for exactly this reason.
_EXPIRED_NOW = "now() - interval '1 microsecond'"


def _row(r: Any) -> dict | None:
    return None if r is None else dict(r._mapping)


def _validate(subject_kind: str | None = None,
              holder_kind: str | None = None) -> None:
    """Refuse a kind outside its closed set before it reaches SQL.

    Both are also CHECK constraints, and that is the authority; this is here so
    a typo comes back as the name of the field that was wrong rather than as a
    `CheckViolation` with a constraint name in it.
    """
    if subject_kind is not None and subject_kind not in LEASE_SUBJECT_KINDS:
        raise ValueError(f"unknown lease subject kind: {subject_kind!r}")
    if holder_kind is not None and holder_kind not in LEASE_HOLDER_KINDS:
        raise ValueError(f"unknown lease holder kind: {holder_kind!r}")


def acquire(s: Session, org_id: UUID | str, *, subject_kind: str,
            subject_id: str, holder_kind: str, holder_id: str,
            ttl_sec: int, principal_user_id: UUID | str | None = None,
            min_generation: int = 1) -> dict | None:
    """Take the lease on this subject. Returns the row, or None if someone else
    holds it and their lease has not expired.

    THE ONE ATOMIC STATEMENT of section 5, verbatim in shape:

        INSERT INTO lease (...) VALUES (...)
        ON CONFLICT (subject_kind, subject_id) DO UPDATE
           SET holder_id = EXCLUDED.holder_id,
               generation = lease.generation + 1,
               expires_at = now() + :ttl
         WHERE lease.expires_at < now()

    with two additions that do not change its shape:

    `GREATEST(lease.generation + 1, EXCLUDED.generation)` rather than a bare
    `+ 1`, so a caller that already holds a higher fencing generation for this
    subject can carry it in as a floor. That is what lets the ticket lease take
    over `tasks.claim()`'s existing `claim_token` without the generation
    appearing to go BACKWARDS on a ticket that has already been claimed five
    times -- a fence that goes backwards is a fence that can be replayed.

    `org_id` IS NOT IN THE `SET` LIST, and that is load-bearing rather than an
    omission. Assigning it would let an acquire move a subject from one tenant
    to another; leaving it means a conflicting row belonging to another tenant
    is invisible under RLS, the upsert cannot see it to update it, and Postgres
    raises a unique violation -- reported here as "not acquired". Fail closed.
    """
    _validate(subject_kind, holder_kind)
    ttl = int(ttl_sec)
    if ttl <= 0:
        raise ValueError(f"ttl_sec must be positive, got {ttl_sec!r}")
    params = {
        "id": uuid7(), "org": str(org_id), "sk": subject_kind,
        "sid": str(subject_id), "hk": holder_kind, "hid": str(holder_id),
        "principal": None if principal_user_id is None else str(principal_user_id),
        "gen": max(1, int(min_generation)), "ttl": ttl,
    }
    stmt = text(
        "INSERT INTO lease (id, org_id, subject_kind, subject_id, holder_kind, "
        "holder_id, principal_user_id, generation, expires_at, acquired_at, "
        "last_renewed_at, last_work_at) VALUES (:id, :org, :sk, :sid, :hk, "
        ":hid, :principal, :gen, now() + make_interval(secs => :ttl), now(), "
        "now(), now()) "
        "ON CONFLICT ON CONSTRAINT uq_lease_subject DO UPDATE SET "
        "holder_kind = EXCLUDED.holder_kind, "
        "holder_id = EXCLUDED.holder_id, "
        "principal_user_id = EXCLUDED.principal_user_id, "
        "generation = GREATEST(lease.generation + 1, EXCLUDED.generation), "
        "expires_at = now() + make_interval(secs => :ttl), "
        "acquired_at = now(), last_renewed_at = now(), last_work_at = now() "
        "WHERE lease.expires_at < now() "
        f"RETURNING {_COLUMNS}")
    try:
        # A SAVEPOINT, so the unique violation described above does not poison
        # the caller's transaction. Without it a losing cross-tenant acquire
        # would abort everything the caller had already done.
        with s.begin_nested():
            return _row(s.execute(stmt, params).one_or_none())
    except IntegrityError:
        return None


def renew(s: Session, org_id: UUID | str, *, subject_kind: str, subject_id: str,
          generation: int, ttl_sec: int, work: bool = False) -> dict | None:
    """Extend this lease. Returns the row, or None if the generation is stale.

    `WHERE generation = :g` IS THE FENCE, exactly as section 5 spells it. A
    holder that was displaced -- by an operator edit, by a liveness reclaim, by
    a preempt -- presents a generation the row no longer carries and renews
    nothing, which is how it finds out.

    NOT ALSO `AND expires_at > now()`. A holder whose lease lapsed while nobody
    was waiting for it is still the recorded holder at the recorded generation,
    and refusing its renewal would strand the work for no benefit. The moment
    anyone else takes the subject the generation moves and the fence bites.

    `work` IS WHAT SEPARATES "the process is up" FROM "the work is moving"
    (section 6). A plain heartbeat every 30s keeps a 90s lease alive; only a
    real work event pushes `last_work_at`, which is what the 10-minute idle
    window is measured against.
    """
    _validate(subject_kind)
    ttl = int(ttl_sec)
    if ttl <= 0:
        raise ValueError(f"ttl_sec must be positive, got {ttl_sec!r}")
    work_set = ", last_work_at = now()" if work else ""
    return _row(s.execute(text(
        "UPDATE lease SET expires_at = now() + make_interval(secs => :ttl), "
        f"last_renewed_at = now(){work_set} "
        "WHERE org_id = :org AND subject_kind = :sk AND subject_id = :sid "
        "AND generation = :gen "
        f"RETURNING {_COLUMNS}"),
        {"org": str(org_id), "sk": subject_kind, "sid": str(subject_id),
         "gen": int(generation), "ttl": ttl}).one_or_none())


def release(s: Session, org_id: UUID | str, *, subject_kind: str,
            subject_id: str, generation: int) -> dict | None:
    """Give the lease up now rather than in TTL seconds. Fenced by generation.

    EXPIRES THE ROW; NEVER DELETES IT. `generation` is a fencing token and is
    worth something only while it never goes backwards for a subject, so the row
    stays as that subject's permanent counter. Deleting it would restart the
    count at 1 and let a resurrected holder's stale token match a live one.
    """
    _validate(subject_kind)
    return _row(s.execute(text(
        f"UPDATE lease SET expires_at = {_EXPIRED_NOW}, "
        "last_renewed_at = now() "
        "WHERE org_id = :org AND subject_kind = :sk AND subject_id = :sid "
        "AND generation = :gen "
        f"RETURNING {_COLUMNS}"),
        {"org": str(org_id), "sk": subject_kind, "sid": str(subject_id),
         "gen": int(generation)}).one_or_none())


def release_for_holder(s: Session, org_id: UUID | str, *, holder_id: str,
                       holder_kind: str | None = None) -> list[dict]:
    """Expire every lease this session holds. Returns the rows it released.

    NOT FENCED BY GENERATION, and it does not need to be: the fence answers "are
    you still the holder I think you are", and the identity here IS the holder.
    Restricted to leases that are currently held so a re-run reports nothing
    rather than re-releasing history.

    This is the write half of section 6's join rule -- presence expiry for an
    interactive holder releases its lease immediately -- and it is why closing a
    chat tab costs nothing instead of orphaning a tree for a full TTL.
    """
    where = ["org_id = :org", "holder_id = :hid", _HELD]
    params: dict[str, Any] = {"org": str(org_id), "hid": str(holder_id)}
    if holder_kind is not None:
        _validate(holder_kind=holder_kind)
        where.append("holder_kind = :hk")
        params["hk"] = holder_kind
    rows = s.execute(text(
        f"UPDATE lease SET expires_at = {_EXPIRED_NOW}, "
        f"last_renewed_at = now() "
        f"WHERE {' AND '.join(where)} RETURNING {_COLUMNS}"), params).all()
    return [dict(r._mapping) for r in rows]


def fence(s: Session, org_id: UUID | str, *, subject_kind: str,
          subject_id: str) -> dict | None:
    """Invalidate whoever holds this subject: bump the generation and expire it.

    DELIBERATELY UNFENCED, because it is what a caller with more authority than
    the holder does. `tasks.update()` already has exactly this operation: an
    operator edit to a ticket a worker holds bumps `claim_token` so the worker's
    next write is refused. That is one mechanism, and this is where it now
    lives for the relational half.

    NOT PREEMPT. Preempt is step B7 and is a verb with a grace window, a
    snapshot and a timeline entry. This is the storage primitive underneath it.
    """
    _validate(subject_kind)
    return _row(s.execute(text(
        f"UPDATE lease SET generation = generation + 1, "
        f"expires_at = {_EXPIRED_NOW}, last_renewed_at = now() "
        "WHERE org_id = :org AND subject_kind = :sk AND subject_id = :sid "
        f"RETURNING {_COLUMNS}"),
        {"org": str(org_id), "sk": subject_kind,
         "sid": str(subject_id)}).one_or_none())


def get_lease(s: Session, org_id: UUID | str, *, subject_kind: str,
              subject_id: str) -> dict | None:
    """This subject's row whether or not it has expired.

    An expired row is returned, not hidden: "nobody holds this, and the last
    holder was X at generation N" is a different and more useful answer than
    silence, and it is what the directory renders.
    """
    _validate(subject_kind)
    return _row(s.execute(text(
        f"SELECT {_COLUMNS} FROM lease WHERE org_id = :org "
        "AND subject_kind = :sk AND subject_id = :sid"),
        {"org": str(org_id), "sk": subject_kind,
         "sid": str(subject_id)}).one_or_none())


def list_leases(s: Session, org_id: UUID | str, *, subject_kind: str | None = None,
                holder_kind: str | None = None, holder_id: str | None = None,
                held_only: bool = True, limit: int = 200) -> list[dict]:
    """Leases for this org, most recently renewed first."""
    where = ["org_id = :org"]
    params: dict[str, Any] = {"org": str(org_id),
                              "limit": max(1, min(limit, 1000))}
    if held_only:
        where.append(_HELD)
    if subject_kind:
        _validate(subject_kind)
        where.append("subject_kind = :sk")
        params["sk"] = subject_kind
    if holder_kind:
        _validate(holder_kind=holder_kind)
        where.append("holder_kind = :hk")
        params["hk"] = holder_kind
    if holder_id:
        where.append("holder_id = :hid")
        params["hid"] = str(holder_id)
    rows = s.execute(text(
        f"SELECT {_COLUMNS} FROM lease WHERE {' AND '.join(where)} "
        "ORDER BY last_renewed_at DESC LIMIT :limit"), params).all()
    return [dict(r._mapping) for r in rows]
