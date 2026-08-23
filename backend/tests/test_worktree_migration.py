"""The `worktree` migration applies and reverses, on a database nobody needs.

Two separate claims, and only one of them needs a server:

  * THERE IS STILL EXACTLY ONE HEAD. A second head is not a test failure
    anywhere else -- alembic happily creates one, `upgrade head` then fails
    with "Multiple head revisions are present", and the person who finds out is
    whoever next deploys. This needs no database and so runs everywhere.

  * UPGRADE THEN DOWNGRADE LEAVES THE SCHEMA WHERE IT STARTED. Run against a
    throwaway database created and dropped by the test, never against the
    installation's own: a migration test that ran against the live schema would
    be a migration test that could destroy it.

DOWNGRADE IS ADDRESSED BY REVISION, NOT BY `-1`. `test_change_migration.py`
takes `-1` from `head`, which meant its own revision only as long as it WAS the
head; the next migration silently turned it into "reverse whatever landed most
recently", and it has been failing ever since. Naming the parent keeps this test
about this migration for as long as the file exists.

The database half needs the OWNER credential, because migrations run as the
schema owner by design (`db/migrations/env.py`) and the application role
deliberately holds no DDL.
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
_REVISION = "e7d4c1b90a63"
_PARENT = "c8299a81457a"

# A name no installation would choose, carrying the same test prefix
# `tests/conftest.py` guards organizations with.
_PROBE_DB = "wstest_worktree_migration"

_TABLES = ("worktree", "worktree_event")


def _config(url: str | None = None):
    """An alembic Config built in memory rather than from `alembic.ini`.

    Deliberately leaves `config_file_name` unset: `env.py` calls `fileConfig()`
    when it is present, which would reconfigure logging for the whole pytest
    process.
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def test_there_is_exactly_one_head_and_this_revision_is_on_it():
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


def test_the_tenant_tables_tuple_names_both_new_tables():
    """The RLS-coverage guard reads `_TENANT_TABLES` out of the migration text
    (tests/test_db_rls_coverage.py). A table added without it is readable by
    every tenant, and nothing else catches that without a live database."""
    source = next(_MIGRATIONS.glob(f"versions/{_REVISION}_*.py")).read_text()
    for table in _TABLES:
        assert f'"{table}"' in source.split("_TENANT_TABLES")[1][:200]


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


def _facts(url: str) -> dict:
    import psycopg

    out: dict = {"tables": {}, "change_fk": False}
    with psycopg.connect(_psycopg_url(url)) as c:
        out["change_fk"] = c.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conname = 'change_worktree_id_org_fkey'").fetchone()[0] > 0
        for table in _TABLES:
            if c.execute("SELECT to_regclass(%s)", (f"public.{table}",)
                         ).fetchone()[0] is None:
                continue
            rls = c.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = %s", (table,)).fetchone()
            out["tables"][table] = {
                "enabled": rls[0],
                "forced": rls[1],
                "policies": sorted(
                    p[0] for p in c.execute(
                        "SELECT policyname FROM pg_policies WHERE tablename = %s",
                        (table,)).fetchall()),
                "columns": sorted(
                    col[0] for col in c.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = %s", (table,)).fetchall()),
            }
    return out


@pytestmark_db
def test_the_migration_applies_and_reverses(probe_db, monkeypatch):
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    cfg = _config(probe_db)

    command.upgrade(cfg, "head")
    up = _facts(probe_db)
    assert set(up["tables"]) == set(_TABLES), "upgrade did not create both tables"
    # RLS is not optional on a tenant-scoped table, and ENABLE without FORCE is
    # the most common way an RLS deployment is silently unprotected.
    for table in _TABLES:
        assert up["tables"][table]["enabled"] and up["tables"][table]["forced"]
        assert up["tables"][table]["policies"] == [f"{table}_org_isolation"]
    for column in ("org_id", "app_id", "project_slug", "scope", "branch_slug",
                   "branch", "path", "base_ref", "base_sha", "ticket_id",
                   "allocated_by", "status", "merged_sha", "removed_at",
                   "deleted_at"):
        assert column in up["tables"]["worktree"]["columns"], f"{column} missing"
    for column in ("org_id", "worktree_id", "kind", "actor", "sha", "detail",
                   "paths", "created_at"):
        assert column in up["tables"]["worktree_event"]["columns"], \
            f"{column} missing"
    # a7c3e10d9b64 left `change.worktree_id` without a foreign key and named
    # this migration as the one that owes it.
    assert up["change_fk"], "change.worktree_id was never given its reference"

    command.downgrade(cfg, _PARENT)
    down = _facts(probe_db)
    assert down["tables"] == {}, "downgrade left the worktree tables behind"
    assert not down["change_fk"], (
        "downgrade left change pointing at a table that is gone")

    # And it is not a one-way door: re-applying works on the reversed schema.
    command.upgrade(cfg, "head")
    assert set(_facts(probe_db)["tables"]) == set(_TABLES)


@pytestmark_db
def test_a_terminal_worktree_must_record_when_it_left_the_disk(probe_db,
                                                               monkeypatch):
    """`ck_worktree_terminal_is_removed`, exercised rather than asserted.

    Without it a row can claim `merged` while the tree it names is still on
    disk, which is precisely the record-versus-disk disagreement this table
    exists to close.
    """
    import psycopg
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    command.upgrade(_config(probe_db), "head")

    with psycopg.connect(_psycopg_url(probe_db), autocommit=True) as c:
        c.execute("INSERT INTO organization (id, slug, name) "
                  "VALUES (gen_random_uuid(), 'probe', 'Probe')")
        org = c.execute("SELECT id FROM organization WHERE slug = 'probe'"
                        ).fetchone()[0]
        insert = (
            "INSERT INTO worktree (id, org_id, project_slug, scope, "
            "branch_slug, branch, path, base_ref, status, removed_at) VALUES "
            "(gen_random_uuid(), %s, 'p', 'project:p', %s, %s, '/tmp/x', "
            "'main', %s, %s)")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(insert, (org, "bad", "wt/p/bad", "merged", None))
    with psycopg.connect(_psycopg_url(probe_db), autocommit=True) as c:
        org = c.execute("SELECT id FROM organization WHERE slug = 'probe'"
                        ).fetchone()[0]
        # The other direction: a live record must NOT carry a removal time.
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "INSERT INTO worktree (id, org_id, project_slug, scope, "
                "branch_slug, branch, path, base_ref, status, removed_at) "
                "VALUES (gen_random_uuid(), %s, 'p', 'project:p', 'live', "
                "'wt/p/live', '/tmp/x', 'main', 'allocated', now())", (org,))


@pytestmark_db
def test_the_models_describe_what_the_migration_builds(probe_db, monkeypatch):
    """No autogenerate drift for the new tables.

    The schema was just built from the migrations, so ANY difference against
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
