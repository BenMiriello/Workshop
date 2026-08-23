"""The `change` migration applies and reverses, on a database nobody needs.

Two separate claims, and only one of them needs a server:

  * THERE IS STILL EXACTLY ONE HEAD. A second head is not a test failure
    anywhere else -- alembic happily creates one, `upgrade head` then fails
    with "Multiple head revisions are present", and the person who finds out is
    whoever next deploys. This needs no database and so runs everywhere.

  * UPGRADE THEN DOWNGRADE LEAVES THE SCHEMA WHERE IT STARTED. Run against a
    throwaway database created and dropped by the test, never against the
    installation's own: a migration test that ran against the live schema would
    be a migration test that could destroy it.

The second needs the OWNER credential, because migrations run as the schema
owner by design (`db/migrations/env.py`) and the application role deliberately
holds no DDL.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))

pytest.importorskip("alembic")
pytest.importorskip("sqlalchemy")

_MIGRATIONS = _BACKEND / "db" / "migrations"
_REVISION = "a7c3e10d9b64"
_PARENT = "f3b8d2a17e94"

# A name no installation would choose, carrying the same test prefix
# `tests/conftest.py` guards organizations with.
_PROBE_DB = "wstest_change_migration"


def _config(url: str | None = None):
    """An alembic Config built in memory rather than from `alembic.ini`.

    Deliberately leaves `config_file_name` unset: `env.py` calls
    `fileConfig()` when it is present, which would reconfigure logging for the
    whole pytest process and quietly change what every later test in the run
    sees on its handlers.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def test_there_is_exactly_one_head_and_this_revision_is_on_it():
    """Not "the head IS this revision" -- the next migration would break that,
    and a test that has to be edited by every later change stops being read.
    The invariant is that the graph stays linear and this revision is on it."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_config())
    heads = list(script.get_heads())
    assert len(heads) == 1, (
        f"expected a single alembic head; got {heads}. Two heads mean "
        f"`alembic upgrade head` fails for whoever deploys next."
    )
    assert script.get_revision(_REVISION).down_revision == _PARENT
    ancestry = {r.revision for r in script.walk_revisions("base", heads[0])}
    assert _REVISION in ancestry


# --- the database half -------------------------------------------------------

_ADMIN = os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip()

pytestmark_db = pytest.mark.skipif(
    not _ADMIN,
    reason="no WORKSHOP_DATABASE_ADMIN_URL; migrations run as the schema owner",
)


def _url_for(dbname: str) -> str:
    u = urlsplit(_ADMIN)
    return urlunsplit((u.scheme, u.netloc, f"/{dbname}", "", ""))


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg", "postgresql")


@pytest.fixture()
def probe_db():
    """An empty database, created and dropped around one test."""
    import psycopg

    maintenance = _psycopg_url(_url_for("postgres"))
    with psycopg.connect(maintenance, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{_PROBE_DB}"')
        c.execute(f'CREATE DATABASE "{_PROBE_DB}"')
    try:
        yield _url_for(_PROBE_DB)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{_PROBE_DB}"')


def _table_facts(url: str) -> dict:
    import psycopg

    with psycopg.connect(_psycopg_url(url)) as c:
        exists = c.execute(
            "SELECT to_regclass('public.change')").fetchone()[0]
        if exists is None:
            return {"exists": False}
        rls = c.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'change'").fetchone()
        policies = c.execute(
            "SELECT policyname FROM pg_policies WHERE tablename = 'change'"
        ).fetchall()
        columns = c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'change'").fetchall()
        return {
            "exists": True,
            "enabled": rls[0],
            "forced": rls[1],
            "policies": sorted(p[0] for p in policies),
            "columns": sorted(col[0] for col in columns),
        }


@pytestmark_db
def test_the_migration_applies_and_reverses(probe_db, monkeypatch):
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    cfg = _config(probe_db)

    command.upgrade(cfg, "head")
    after_up = _table_facts(probe_db)
    assert after_up["exists"], "upgrade did not create the change table"
    # RLS is not optional on a tenant-scoped table, and ENABLE without FORCE is
    # the most common way an RLS deployment is silently unprotected.
    assert after_up["enabled"] and after_up["forced"]
    assert after_up["policies"] == ["change_org_isolation"]
    for column in ("org_id", "app_id", "worktree_id", "project_slug",
                   "branch_slug", "base_ref", "head_sha", "ticket_ids",
                   "status", "gates", "remote", "merged_sha", "merged_at",
                   "closed_reason"):
        assert column in after_up["columns"], f"{column} missing"

    command.downgrade(cfg, "-1")
    assert _table_facts(probe_db) == {"exists": False}, (
        "downgrade left the change table behind")

    # And it is not a one-way door: re-applying works on the reversed schema.
    command.upgrade(cfg, "head")
    assert _table_facts(probe_db)["exists"]


@pytestmark_db
def test_the_models_describe_what_the_migration_builds(probe_db, monkeypatch):
    """No autogenerate drift for the new table.

    The stronger version of `test_db_schema_drift.py`, which can only assert
    the removal direction against a live schema it does not control. Here the
    schema was just built from the migrations, so ANY difference against
    `db.models` is a real disagreement between the two.
    """
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from db.models import Base

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    command.upgrade(_config(probe_db), "head")

    engine = create_engine(probe_db, future=True)
    try:
        with engine.connect() as conn:
            diffs = compare_metadata(MigrationContext.configure(conn),
                                     Base.metadata)
    finally:
        engine.dispose()
    assert diffs == [], f"models and migrations disagree: {diffs}"
