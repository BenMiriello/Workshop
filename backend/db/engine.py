"""Engine, session factory, and the tenant set-point.

THE TENANT SET-POINT IS THE WHOLE SECURITY MODEL, so it lives in one place.

Row-Level Security in Postgres filters on a runtime variable, and this module
is the only thing that sets it. Two rules are load-bearing and both have a
documented failure mode behind them:

1. `SET LOCAL`, never plain `SET`. A plain `SET` outlives the transaction and
   the next request served by that pooled connection inherits the previous
   tenant's context. That converts a connection pool into a cross-tenant read.
   `SET LOCAL` is scoped to the transaction, so it cannot outlive the request.

2. Every tenant-scoped table is created with `FORCE ROW LEVEL SECURITY`, not
   just `ENABLE`. A table's owner bypasses its policies by default, the
   application usually connects as the owner, and forgetting `FORCE` is the
   single most common way an RLS deployment is silently unprotected. The
   `FORCE` lives in the migration; `verify_rls()` below asserts it at runtime so
   a table added later without it fails loudly instead of quietly.

The application-level `WHERE org_id = ...` stays as the first layer. RLS is the
second one, and it is the one that holds when a query forgets.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["ENGINE_URL", "engine", "SessionLocal", "tenant_session", "user_session",
           "admin_session", "verify_rls", "TenantUnset"]

# The runtime variables the RLS policies read. Defined once; the migration and
# this module must agree or every policy silently matches nothing.
TENANT_SETTING = "app.current_org"

# The acting user. Needed for exactly one bootstrap question that cannot be
# answered inside a tenant: "which organizations do I belong to?" A login has a
# user but does not yet have an org, so a `membership` policy keyed only on
# org_id would make the org list unreadable and lock every user out. `membership`
# therefore carries two policies, OR'd by Postgres: one for rows in the current
# org, one for rows belonging to the current user.
USER_SETTING = "app.current_user"


class TenantUnset(RuntimeError):
    """Raised when a tenant-scoped session is opened with no organization.

    Fails closed, deliberately, mirroring `services/ownership.py`: an unset
    tenant is a programming error, never a licence to read everything.
    """


def _url() -> str:
    url = os.environ.get("WORKSHOP_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "WORKSHOP_DATABASE_URL is not set. It lives in "
            "~/.config/workshop/secrets.env and is loaded by the backend unit; "
            "see infra/postgres/docker-compose.yml for the server itself."
        )
    return url


ENGINE_URL = _url  # callable, so importing this module never requires the env

_engine = None
_SessionLocal = None


def engine():
    """The process-wide Engine, built on first use.

    Lazy on purpose: importing this module must not require a database, so the
    existing file-backed code paths and the test suite keep working on a machine
    with no Postgres.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            _url(),
            pool_pre_ping=True,   # a recycled connection must not fail a request
            future=True,
        )
    return _engine


def SessionLocal() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=engine(), expire_on_commit=False,
                                     future=True)
    return _SessionLocal()


def _set_local(session: Session, setting: str, value: str) -> None:
    """`SET LOCAL <setting> = <value>`, transaction-scoped.

    `set_config(..., true)` is the function form, and the `true` is the whole
    point: it makes the setting local to the transaction. The value is bound as
    a parameter; the setting NAME is interpolated because Postgres does not
    accept a parameter there, and both names are module constants rather than
    anything a caller supplies.
    """
    session.execute(text(f"SELECT set_config('{setting}', :v, true)"), {"v": value})


@contextmanager
def tenant_session(org_id: UUID | str | None,
                   user_id: UUID | str | None = None) -> Iterator[Session]:
    """A session bound to one organization for the life of one transaction.

    Everything the application does with tenant data goes through here. The
    `SET LOCAL` is issued inside the transaction the caller's work runs in, so
    the setting is discarded when that transaction ends, whether it commits or
    rolls back, and cannot follow a pooled connection into the next request.
    """
    if org_id is None:
        raise TenantUnset("tenant_session() requires an organization id")
    org = str(UUID(str(org_id)))  # reject anything that is not a uuid

    session = SessionLocal()
    try:
        session.begin()
        _set_local(session, TENANT_SETTING, org)
        if user_id is not None:
            _set_local(session, USER_SETTING, str(UUID(str(user_id))))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def user_session(user_id: UUID | str) -> Iterator[Session]:
    """A session with a user but no organization, for the bootstrap lookup.

    The only thing this can read is the caller's own `membership` rows, which is
    how the application answers "which organizations do I belong to?" at login,
    before any tenant is chosen. Every other tenant table has no matching policy
    without an org, so it reads as empty.
    """
    session = SessionLocal()
    try:
        session.begin()
        _set_local(session, USER_SETTING, str(UUID(str(user_id))))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def admin_session() -> Iterator[Session]:
    """A session with NO tenant set. Reads nothing from tenant-scoped tables.

    For migrations, the backfill, and cross-tenant administration. It is not a
    back door: with no `app.current_org` set, every RLS policy evaluates against
    a missing setting and returns no rows, so this session sees tenant tables as
    empty rather than as everything.
    """
    session = SessionLocal()
    try:
        session.begin()
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def verify_rls(session: Session) -> list[str]:
    """Everything that would stop RLS from actually isolating tenants.

    Returns a list of problems, empty when correct.

    THE FIRST CHECK IS THE IMPORTANT ONE. A SUPERUSER or a role with BYPASSRLS
    ignores row security entirely, and no amount of ENABLE/FORCE on the table
    changes that. The Postgres docker image creates its `POSTGRES_USER` as a
    superuser, so connecting with the credentials the container was initialised
    with produces a database where the policies exist, the flags read correctly,
    and one tenant can still read another's rows by primary key. That was
    verified here before `db/bootstrap.py` existed. A per-table audit alone
    reports that state as healthy, which is why the role is checked first.

    Then, per table: correct only when RLS is both ENABLED and FORCED and there
    is at least one policy. Enabled-without-forced is the owner-bypass case;
    forced-with-no-policy denies everything, which is loud rather than dangerous
    but still wrong.
    """
    problems: list[str] = []

    role = session.execute(text(
        "SELECT current_user, rolsuper, rolbypassrls "
        "FROM pg_roles WHERE rolname = current_user")).one_or_none()
    if role is not None:
        name, is_super, bypass = tuple(role)
        if is_super:
            problems.append(
                f"connected as {name}, which is a SUPERUSER: row security does "
                f"not apply to it and every policy below is inert")
        elif bypass:
            problems.append(
                f"connected as {name}, which holds BYPASSRLS: every policy "
                f"below is inert")

    rows = session.execute(text("""
        SELECT c.relname,
               c.relrowsecurity   AS enabled,
               c.relforcerowsecurity AS forced,
               (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'org_id'
                        AND NOT a.attisdropped)
        ORDER BY c.relname
    """)).all()

    for name, enabled, forced, policies in rows:
        if not enabled:
            problems.append(f"{name}: RLS not enabled")
        elif not forced:
            problems.append(f"{name}: RLS enabled but not FORCED (owner bypasses it)")
        elif not policies:
            problems.append(f"{name}: RLS forced but no policy (denies everything)")
    return problems
