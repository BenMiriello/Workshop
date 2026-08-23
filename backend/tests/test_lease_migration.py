"""The `lease` migration applies and reverses, on a database nobody needs.

Three claims, and only one of them needs a server:

  * THERE IS STILL EXACTLY ONE HEAD. A second head is not a test failure
    anywhere else -- alembic happily creates one, `upgrade head` then fails with
    "Multiple head revisions are present", and the person who finds out is
    whoever next deploys. Needs no database, runs everywhere.

  * `_TENANT_TABLES` NAMES THE NEW TABLE. `tests/test_db_rls_coverage.py` reads
    that tuple out of the migration text; a tenant-scoped table missing from it
    is readable by every tenant, and nothing else catches that without a live
    database.

  * UPGRADE THEN DOWNGRADE LEAVES THE SCHEMA WHERE IT STARTED. Run against a
    throwaway database created and dropped by the test, never against the
    installation's own: a migration test that ran against the live schema would
    be a migration test that could destroy it.

DOWNGRADE IS ADDRESSED BY REVISION, NOT BY `-1`. `test_change_migration.py` took
`-1` from `head`, which meant its own revision only as long as it WAS the head;
the next migration silently turned it into "reverse whatever landed most
recently", and it broke. Naming the parent keeps this test about this migration
for as long as the file exists.

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
_REVISION = "f2a6d0c31b78"
_PARENT = "e7d4c1b90a63"

# A name no installation would choose, carrying the same test prefix
# `tests/conftest.py` guards organizations with.
_PROBE_DB = "wstest_lease_migration"

_TABLES = ("lease",)


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
        f"`alembic upgrade head` fails for whoever deploys next.")
    assert script.get_revision(_REVISION).down_revision == _PARENT
    ancestry = {r.revision for r in script.walk_revisions("base", heads[0])}
    assert _REVISION in ancestry


def test_the_tenant_tables_tuple_names_the_lease_table():
    source = next(_MIGRATIONS.glob(f"versions/{_REVISION}_*.py")).read_text()
    for table in _TABLES:
        assert f'"{table}"' in source.split("_TENANT_TABLES")[1][:200]


def test_the_model_and_the_migration_agree_on_the_columns():
    """Two hand-maintained lists of one table's columns. Needs no database, and
    catches the drift `alembic revision --autogenerate` would otherwise emit as
    a spurious migration on someone else's unrelated change."""
    from db.models import Lease

    source = next(_MIGRATIONS.glob(f"versions/{_REVISION}_*.py")).read_text()
    for column in (c.name for c in Lease.__table__.columns):
        assert f'"{column}"' in source, (
            f"{column} is on the model and not in the migration")


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

    out: dict = {"tables": {}}
    with psycopg.connect(_psycopg_url(url)) as c:
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
                "constraints": sorted(
                    r[0] for r in c.execute(
                        "SELECT conname FROM pg_constraint WHERE conrelid = "
                        "%s::regclass", (table,)).fetchall()),
            }
    return out


@pytestmark_db
def test_the_migration_applies_and_reverses(probe_db, monkeypatch):
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    cfg = _config(probe_db)

    command.upgrade(cfg, "head")
    up = _facts(probe_db)
    assert set(up["tables"]) == set(_TABLES), "upgrade did not create the table"
    # RLS is not optional on a tenant-scoped table, and ENABLE without FORCE is
    # the most common way an RLS deployment is silently unprotected.
    for table in _TABLES:
        assert up["tables"][table]["enabled"] and up["tables"][table]["forced"]
        assert up["tables"][table]["policies"] == [f"{table}_org_isolation"]
    for column in ("org_id", "subject_kind", "subject_id", "holder_kind",
                   "holder_id", "principal_user_id", "generation", "expires_at",
                   "acquired_at", "last_renewed_at", "last_work_at"):
        assert column in up["tables"]["lease"]["columns"], f"{column} missing"
    # THE EXCLUSION ITSELF. Without this constraint the acquire's ON CONFLICT
    # has no target and two holders of one subject is not a violation, which is
    # the entire property the table exists to have.
    assert "uq_lease_subject" in up["tables"]["lease"]["constraints"]
    # And no `deleted_at`: releasing must not be able to destroy the fencing
    # counter (see the migration's own docstring).
    assert "deleted_at" not in up["tables"]["lease"]["columns"]

    command.downgrade(cfg, _PARENT)
    assert _facts(probe_db)["tables"] == {}, "downgrade left the lease table"

    # And it is not a one-way door: re-applying works on the reversed schema.
    command.upgrade(cfg, "head")
    assert set(_facts(probe_db)["tables"]) == set(_TABLES)


@pytestmark_db
def test_one_subject_cannot_have_two_leases(probe_db, monkeypatch):
    """`uq_lease_subject`, exercised rather than asserted.

    Two rows for one subject is the collision the whole design exists to
    prevent, and a unique constraint that is merely declared in a migration
    nobody ran is not one.
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
        # A SECOND org, because the uniqueness is deliberately NOT org-scoped:
        # the subject is one physical thing and one holder has to mean one.
        c.execute("INSERT INTO organization (id, slug, name) "
                  "VALUES (gen_random_uuid(), 'probe2', 'Probe 2')")
        org2 = c.execute("SELECT id FROM organization WHERE slug = 'probe2'"
                         ).fetchone()[0]
        insert = (
            "INSERT INTO lease (id, org_id, subject_kind, subject_id, "
            "holder_kind, holder_id, expires_at) VALUES (gen_random_uuid(), "
            "%s, %s, 'the-subject', 'run', %s, now() + interval '90 seconds')")
        c.execute(insert, (org, "worktree", "run-a"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(insert, (org2, "worktree", "run-b"))

    # A different SUBJECT KIND with the same id is a different subject, and the
    # ticket lease and the worktree lease must be able to coexist (section 5).
    with psycopg.connect(_psycopg_url(probe_db), autocommit=True) as c:
        c.execute(
            "INSERT INTO lease (id, org_id, subject_kind, subject_id, "
            "holder_kind, holder_id, expires_at) VALUES (gen_random_uuid(), "
            "%s, 'ticket', 'the-subject', 'run', 'run-c', "
            "now() + interval '90 seconds')", (org,))
        assert c.execute("SELECT count(*) FROM lease").fetchone()[0] == 2


@pytestmark_db
def test_the_vocabularies_are_check_constraints(probe_db, monkeypatch):
    """A subject kind or holder kind outside the closed set is a violation, not
    a row nothing can interpret."""
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
            "INSERT INTO lease (id, org_id, subject_kind, subject_id, "
            "holder_kind, holder_id, generation, expires_at) VALUES "
            "(gen_random_uuid(), %s, %s, %s, %s, 'h', %s, now())")
        for kind, holder, generation in (("branch", "run", 1),
                                         ("worktree", "sweep", 1),
                                         ("worktree", "run", 0)):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(insert, (org, kind, f"s-{kind}-{holder}-{generation}",
                                   holder, generation))
