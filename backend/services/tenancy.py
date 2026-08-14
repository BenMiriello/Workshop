"""Which organization and user a request is acting as.

THE BRIDGE, and it is a small one on purpose. `services/ownership.py` pins an
app-user id (a small int, from `.runtime/auth.db`) in a contextvar for the life
of a request. The relational schema keys everything on `organization.id` and
`user_account.id`, both uuids. This maps one to the other and caches the result,
because it is a lookup of a row that changes approximately never.

FAILS CLOSED, matching `ownership.current_owner()` and `db.engine.tenant_session`
on either side of it. An unresolvable owner raises; it never falls back to "the
first organization", which is how a single-tenant convenience becomes a
cross-tenant read the day there are two.

This is the FIRST request-path consumer of the database (ADR 0019 P3 has not
landed), so it also carries the "is the database even reachable" question:
`available()` answers it without raising, and the chat paths use it to degrade
to the file store rather than fail a request.
"""
from __future__ import annotations

import os
import threading
from uuid import UUID

from services import ownership

__all__ = ["TenantUnresolved", "available", "org_and_user", "current_org",
           "org_for_owner", "reset_cache"]

# The installation's single organization today. A tenant lookup that had to
# guess would be a bug; this is the slug the backfill seeds, and it is only used
# to find the org that the CURRENT owner belongs to, never to substitute for one.
DEFAULT_ORG_SLUG = os.environ.get("WORKSHOP_ORG_SLUG", "workshop")


class TenantUnresolved(RuntimeError):
    """No organization/user row for this app user."""


_lock = threading.Lock()
_cache: dict[int, tuple[UUID, UUID]] = {}
_db_ok: bool | None = None


def reset_cache() -> None:
    global _db_ok
    with _lock:
        _cache.clear()
        _db_ok = None


def available() -> bool:
    """Whether the relational store can be reached at all.

    Cached after the first answer. The chat read path asks this before trying a
    branch-aware read, so a machine with no Postgres (every checkout that is not
    this one, and the whole test suite) keeps working on the files.
    """
    global _db_ok
    if _db_ok is not None:
        return _db_ok
    with _lock:
        if _db_ok is not None:
            return _db_ok
        if not os.environ.get("WORKSHOP_DATABASE_URL"):
            _db_ok = False
            return _db_ok
        try:
            from sqlalchemy import text

            from db.engine import SessionLocal
            s = SessionLocal()
            try:
                s.execute(text("SELECT 1"))
            finally:
                s.close()
            _db_ok = True
        except Exception:
            _db_ok = False
    return _db_ok


def org_for_owner(owner_id: int) -> tuple[UUID, UUID]:
    """(org_id, user_id) for an app user. Raises if either is missing."""
    cached = _cache.get(int(owner_id))
    if cached is not None:
        return cached

    from sqlalchemy import text

    from db.engine import owner_session

    # The owner connection, because `user_account` and `organization` are
    # RLS-protected with policies keyed on the tenant we are trying to FIND.
    # This is the same bootstrap question `db.engine.user_session` exists for,
    # asked before there is a user_account id to ask it with.
    with owner_session() as s:
        row = s.execute(text("""
            SELECT u.id AS user_id, m.org_id AS org_id
              FROM user_account u
              JOIN membership m ON m.user_id = u.id
              JOIN organization o ON o.id = m.org_id
             WHERE u.legacy_user_id = :legacy
               AND u.deleted_at IS NULL AND o.deleted_at IS NULL
             ORDER BY (o.slug = :slug) DESC, m.created_at ASC
             LIMIT 1
        """), {"legacy": int(owner_id), "slug": DEFAULT_ORG_SLUG}).one_or_none()
    if row is None:
        raise TenantUnresolved(
            f"app user {owner_id} has no user_account/membership. Run "
            f"backend/db/backfill.py, which seeds them from .runtime/auth.db.")
    pair = (row._mapping["org_id"], row._mapping["user_id"])
    with _lock:
        _cache[int(owner_id)] = pair
    return pair


def org_and_user() -> tuple[UUID, UUID]:
    """The acting tenant, from the ownership contextvar."""
    return org_for_owner(ownership.current_owner())


def current_org() -> UUID:
    return org_and_user()[0]
