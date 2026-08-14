"""Create the least-privilege application role.

WHY THIS EXISTS, and it is not a nicety.

`ALTER TABLE ... FORCE ROW LEVEL SECURITY` makes a table's OWNER subject to its
policies. It does nothing about a SUPERUSER or a role holding BYPASSRLS: those
bypass row security unconditionally, and no table-level setting can stop them.

The official Postgres image creates `POSTGRES_USER` as a superuser. So the
obvious setup, where the application connects with the credentials the container
was initialised with, has RLS **enabled, forced, policied and completely
inert**. It was verified inert here: with policies in place and `verify_rls()`
reporting no problems, one tenant read another tenant's ticket by primary key
and `SELECT count(*) FROM ticket` returned every row in the database.

That is the worst possible failure shape, because every signal says it is
working. Hence two roles:

  workshop      owner + superuser. Migrations only. Never serves a request.
  workshop_app  NOSUPERUSER NOBYPASSRLS. What the application connects as.

`verify_rls()` now also asserts the connecting role is not privileged, because
that is the check that would have caught this immediately.

Run after `alembic upgrade`. The owner DSN lives in its own file, which the
backend service deliberately does not load, so the serving process never holds
a superuser credential:

    set -a; . ~/.config/workshop/db-admin.env; set +a
    .venv/bin/python -m db.bootstrap
"""
from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text

APP_ROLE = "workshop_app"


def _admin_url() -> str:
    url = os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip()
    if not url:
        raise SystemExit(
            "WORKSHOP_DATABASE_ADMIN_URL is not set (the OWNER connection, used "
            "for migrations and this bootstrap). It lives in "
            "~/.config/workshop/db-admin.env, which the backend service does not "
            "load, so the serving process never holds the superuser DSN."
        )
    return url


def bootstrap(password: str) -> None:
    engine = create_engine(_admin_url(), future=True)
    with engine.begin() as conn:
        dbname = conn.execute(text("SELECT current_database()")).scalar()
        exists = conn.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        ).scalar()

        # CREATE/ALTER ROLE are utility statements: Postgres does not accept a
        # bind parameter for the password, so it has to be a literal. Composed
        # with psycopg.sql rather than f-stringed, so quoting is the driver's
        # job and a password containing a quote cannot end the statement early.
        from psycopg import sql

        raw = conn.connection.driver_connection
        stmt = (sql.SQL("ALTER ROLE {} WITH PASSWORD {}") if exists
                else sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}"))
        with raw.cursor() as cur:
            cur.execute(stmt.format(sql.Identifier(APP_ROLE), sql.Literal(password)))

        # Belt and braces: assert the negative privileges explicitly rather than
        # relying on CREATE ROLE defaults, so a role that was created by hand
        # with the wrong flags is corrected rather than trusted.
        conn.execute(text(f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"))

        conn.execute(text(f'GRANT CONNECT ON DATABASE "{dbname}" TO {APP_ROLE}'))
        conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
        # DML only. No DDL: the application must not be able to drop a policy.
        conn.execute(text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        conn.execute(text(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"))
        # And for tables a future migration adds, so a new table is not silently
        # unreadable by the application until someone remembers to grant it.
        conn.execute(text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"))
        conn.execute(text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}"))

        # Tables the application may read and append to but never rewrite.
        #
        # `ticket_event` is the audit log, and models.py states outright that
        # "an audit row that can be edited or removed is not an audit row".
        # `GRANT ... ON ALL TABLES` had swept it up, and a reviewer confirmed
        # the application role could run `UPDATE ticket_event SET new_value =
        # 'rewritten history'` and then `DELETE FROM ticket_event`. The property
        # was asserted in a docstring and enforced by nothing.
        conn.execute(text(f"REVOKE UPDATE, DELETE ON ticket_event FROM {APP_ROLE}"))

        # Provisioning and the migration stamp are admin-only (see migration
        # 7bf9ae20fe0d). Re-applied here so a re-run of bootstrap after a fresh
        # GRANT does not silently hand them back.
        for table in ("organization", "user_account", "alembic_version"):
            conn.execute(text(
                f"REVOKE INSERT, UPDATE, DELETE ON {table} FROM {APP_ROLE}"))

        row = conn.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
            {"r": APP_ROLE}).one()
        if row[0] or row[1]:
            raise SystemExit(
                f"{APP_ROLE} still has rolsuper={row[0]} rolbypassrls={row[1]}; "
                "RLS would not apply to it. Refusing to report success."
            )
    print(f"{APP_ROLE}: created/updated, NOSUPERUSER NOBYPASSRLS, DML granted")


if __name__ == "__main__":
    pw = os.environ.get("WORKSHOP_DB_APP_PASSWORD", "").strip()
    if not pw:
        print("WORKSHOP_DB_APP_PASSWORD is not set", file=sys.stderr)
        raise SystemExit(2)
    bootstrap(pw)
