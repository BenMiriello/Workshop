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

import logging
import os
import threading
import time
from uuid import UUID

from services import ownership

log = logging.getLogger(__name__)

__all__ = ["TenantUnresolved", "available", "configured", "org_and_user",
           "current_org", "org_for_owner", "reset_cache"]

# The installation's single organization today. A tenant lookup that had to
# guess would be a bug; this is the slug the backfill seeds, and it is only used
# to find the org that the CURRENT owner belongs to, never to substitute for one.
DEFAULT_ORG_SLUG = os.environ.get("WORKSHOP_ORG_SLUG", "workshop")


class TenantUnresolved(RuntimeError):
    """No organization/user row for this app user."""


_lock = threading.Lock()
_cache: dict[int, tuple[UUID, UUID]] = {}
_db_ok: bool | None = None

# How long a FAILED probe stays cached before the database is asked again, and
# the two bounds that window moves between. It doubles on each consecutive
# failure, so a database that is genuinely gone costs about one connect attempt
# a minute rather than one per request, and a database that comes back is
# noticed within a minute rather than never.
#
# THE "NEVER" IS NOT HYPOTHETICAL. Until 2026-09-16 the first answer was cached
# for the whole process lifetime, and on that day the probe at startup failed
# once while the database was fine seconds later. Every relational read in the
# process -- apps, chat branches, tasks, leases, changes, worktree records,
# ticket sync -- degraded for eight hours and was fixed by a restart that
# changed no code. A cache with no expiry turns a blip into an outage that ends
# only when someone notices.
_RETRY_FLOOR_S = 1.0
_RETRY_CEIL_S = 60.0
_db_failed_at: float | None = None
_db_retry_after_s: float = _RETRY_FLOOR_S

# The file store this process started against. A deployment sets
# WORKSHOP_RUNTIME before import, so for a real one this never changes; only a
# test redirecting `ownership._RUNTIME` at a tmp_path makes it differ. See
# `_file_store_redirected`.
_RUNTIME_AT_IMPORT = ownership._RUNTIME


def _file_store_redirected() -> bool:
    """Has this process been pointed at a throwaway file store since import?

    THE TWO STORES MUST DESCRIBE ONE INSTALLATION, and this is what enforces it.

    Tests that touch a real owner redirect `ownership._RUNTIME` to a tmp_path so
    they do not write real chats, which is the correct and long-standing guard.
    It stopped being sufficient the moment a second store went in behind the
    same write: `org_and_user()` resolves the tenant from the OWNER contextvar
    and never looked at `_RUNTIME`, so a test with a redirected file store still
    mirrored into the real database. It went unnoticed because `available()`
    below assumed no test machine had Postgres, which was true until this one
    got one. Measured cost before the fix: 56 phantom threads, most of them
    titled "Voice", written by the voice suite on every full run.

    Checked on every call rather than cached with `_db_ok`, because the
    redirect happens after import and the point is to notice it.
    """
    return ownership._RUNTIME != _RUNTIME_AT_IMPORT


def reset_cache() -> None:
    global _db_ok, _db_failed_at, _db_retry_after_s
    with _lock:
        _cache.clear()
        _db_ok = None
        _db_failed_at = None
        _db_retry_after_s = _RETRY_FLOOR_S


def configured() -> bool:
    """Whether this INSTALLATION has a relational store at all, reachable or not.

    Not the same question as `available()`, and conflating the two is a data
    hazard on the read side. "No database on this box" is a legitimate
    file-backed checkout that must keep working. "There is a database and it did
    not answer this call" is an outage, and serving a retired file store through
    it is how `.tasks.json` came back from the dead on 2026-08-17 (four tickets
    re-minted with fresh ids out of `TODO.md`). Callers that must fail closed ask
    this to tell the two apart.

    Uncached and deliberately cheap: it reads the environment, never the
    database, so it stays answerable in the exact situation the database is not.

    A process whose file store has been redirected answers False, matching
    `available()` and for the same reason: it is no longer operating against
    this installation, so this installation's database is not its database.
    That is what keeps the fail-closed read from firing inside every test that
    points `ownership._RUNTIME` at a tmp_path.
    """
    if _file_store_redirected():
        return False
    return bool(os.environ.get("WORKSHOP_DATABASE_URL"))


def _failure_is_fresh() -> bool:
    """Is the cached failure recent enough to answer from without re-probing?"""
    if _db_ok is not False or _db_failed_at is None:
        return False
    return (time.monotonic() - _db_failed_at) < _db_retry_after_s


def available() -> bool:
    """Whether the relational store can be reached at all.

    The chat read path asks this before trying a branch-aware read, so a machine
    with no Postgres (every checkout that is not this one) keeps working on the
    files. A process whose file store has been redirected answers False too: see
    `_file_store_redirected`.

    TWO ANSWERS THAT LOOK THE SAME AND CACHE DIFFERENTLY, which is the whole
    shape of this function. "There is no database on this box" comes from
    `configured()`, is read off the environment, cannot change under a running
    process, and so is answered without ever connecting. "There is a database
    and it did not answer" is an OUTAGE, it is expected to end, and caching it
    forever is what made one failed probe outlast the failure by eight hours.
    Only the second kind expires, on the doubling window above.

    Success is still cached permanently. A reachable database that dies is
    noticed by the query that fails, not by this; re-probing before every read
    would double the round trips to answer a question that is almost always yes.
    """
    global _db_ok, _db_failed_at, _db_retry_after_s
    if _file_store_redirected():
        return False
    if _db_ok is True:
        return True
    if not configured():
        return False
    if _failure_is_fresh():
        return False
    with _lock:
        # Re-checked under the lock: concurrent readers arriving during an
        # outage must cost one connect attempt between them, not one each.
        if _db_ok is True:
            return True
        if _failure_is_fresh():
            return False
        was_down = _db_ok is False
        try:
            from sqlalchemy import text

            from db.engine import SessionLocal
            s = SessionLocal()
            try:
                s.execute(text("SELECT 1"))
            finally:
                s.close()
            _db_ok = True
            _db_failed_at = None
            _db_retry_after_s = _RETRY_FLOOR_S
            if was_down:
                log.warning("the relational store answered again; "
                            "relational reads are live")
        except Exception:
            _db_ok = False
            _db_failed_at = time.monotonic()
            # Logged once per failure EPISODE rather than per probe: the point
            # of the message is that reads started degrading, and repeating it
            # every minute for a box that has no database running buries that.
            if not was_down:
                log.warning(
                    "the relational store is configured but did not answer; "
                    "relational reads will degrade until it does",
                    exc_info=True)
            _db_retry_after_s = min(_db_retry_after_s * 2, _RETRY_CEIL_S)
    return bool(_db_ok)


def org_for_owner(owner_id: int) -> tuple[UUID, UUID]:
    """(org_id, user_id) for an app user. Raises if either is missing."""
    if _file_store_redirected():
        # Belt and braces with `available()`. Every caller today checks that
        # first, but this is the function that actually hands out the key to
        # the real database, so a caller that forgets must fail rather than
        # write. An owner id read out of a throwaway file store does not name
        # a real tenant.
        raise TenantUnresolved(
            "the file store is redirected (a test tmp_path), so this process "
            "must not resolve a real tenant")
    cached = _cache.get(int(owner_id))
    if cached is not None:
        return cached

    from sqlalchemy import text

    from db.engine import admin_session

    # THE APPLICATION CONNECTION, through a narrow SECURITY DEFINER function.
    #
    # This asked through `owner_session()` and that was a design error, not a
    # detail: the serving process DOES NOT HOLD the owner credential, on
    # purpose, so every resolution raised in the running backend while passing
    # under pytest (which had both DSNs exported). The chat branch control never
    # rendered because of it.
    #
    # It cannot be a plain SELECT either. `user_account`, `organization` and
    # `membership` are RLS-protected by policies keyed on `app.current_user` and
    # `app.current_org`, and this lookup is what PRODUCES those values, so with
    # neither set all three read as empty. `workshop_resolve_tenant` (migration
    # d4e17c9b3a55) answers exactly that one question and nothing else.
    with admin_session() as s:
        row = s.execute(
            text("SELECT org_id, user_id FROM workshop_resolve_tenant(:legacy)"),
            {"legacy": int(owner_id)}).one_or_none()
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
