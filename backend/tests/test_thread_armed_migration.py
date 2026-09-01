"""The `armed` / `role` migration applies and reverses, on a database nobody needs.

Two claims, and only one of them needs a server:

  * THERE IS STILL EXACTLY ONE HEAD, and this revision is on it. A second head
    is not a failure anywhere else -- alembic makes one happily, `upgrade head`
    then fails with "Multiple head revisions are present", and the person who
    finds out is whoever next deploys.

  * UPGRADE THEN DOWNGRADE LEAVES THE SCHEMA WHERE IT STARTED, against a
    throwaway database created and dropped by the test. Never the
    installation's own: a migration test that ran against the live schema would
    be a migration test that could destroy it.

DOWNGRADE IS ADDRESSED BY REVISION, NOT BY `-1`, for the reason
`test_worktree_migration.py` spells out: `-1` silently becomes "reverse whatever
landed most recently" as soon as another migration lands on top.

The check constraint is EXERCISED, not asserted. Reading `ck_chat_thread_armed`
out of `pg_constraint` proves a constraint exists; inserting `continuous` proves
it is the one that matters, and that is the value a reader who remembers the old
project-autonomy vocabulary would reach for.

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
_REVISION = "c5e1a8f24b03"
_PARENT = "b9f3e2d07a16"

_PROBE_DB = "wstest_thread_armed_migration"


def _config(url: str | None = None):
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


def test_the_constraint_lists_exactly_the_armed_modes_the_store_offers():
    """One list, not two. `chat_store.ARMED_MODES` is what the API clamps to and
    what the column admits; a value in one and not the other is a write that
    passes validation and then fails at the database."""
    from services.chat_store import ARMED_MODES

    source = next(_MIGRATIONS.glob(f"versions/{_REVISION}_*.py")).read_text()
    declared = source.split("_ARMED = (")[1].split(")")[0]
    assert sorted(v.strip().strip("\"'") for v in declared.split(",") if v.strip()) \
        == sorted(ARMED_MODES)


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


def _columns(url: str) -> dict[str, tuple]:
    import psycopg

    with psycopg.connect(_psycopg_url(url)) as c:
        rows = c.execute(
            "SELECT column_name, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = 'chat_thread' "
            "AND column_name IN ('armed', 'role')").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


@pytestmark_db
def test_the_migration_applies_and_reverses(probe_db, monkeypatch):
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    cfg = _config(probe_db)

    command.upgrade(cfg, "head")
    cols = _columns(probe_db)
    assert set(cols) == {"armed", "role"}
    # NOT NULL with a default: "nothing advances it" is a real answer and it is
    # every existing row's answer, so there is nothing to back-fill and no
    # nullable third state to interpret.
    assert cols["armed"][0] == "NO"
    assert "off" in (cols["armed"][1] or "")
    # `role` is genuinely optional: an armed thread with no role continues on
    # its own context, which is the ordinary case.
    assert cols["role"][0] == "YES"

    command.downgrade(cfg, _PARENT)
    assert _columns(probe_db) == {}, "downgrade left the columns behind"

    # And it is not a one-way door.
    command.upgrade(cfg, "head")
    assert set(_columns(probe_db)) == {"armed", "role"}


@pytestmark_db
def test_the_column_refuses_an_armed_value_the_store_does_not_offer(probe_db,
                                                                    monkeypatch):
    """`continuous` is the value the retired project-autonomy vocabulary used,
    so it is the one a reader would reach for by habit."""
    import psycopg
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    command.upgrade(_config(probe_db), "head")

    with psycopg.connect(_psycopg_url(probe_db), autocommit=True) as c:
        # Ids are allocated by the application (uuid7), not by a column
        # default, so the probe has to supply them.
        org = c.execute(
            "INSERT INTO organization (id, name, slug) "
            "VALUES (gen_random_uuid(), 't', 't') RETURNING id").fetchone()[0]
        user = c.execute(
            "INSERT INTO user_account (id, email) "
            "VALUES (gen_random_uuid(), 'a@b.c') RETURNING id").fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "INSERT INTO chat_thread "
                "  (id, org_id, owner_user_id, context_key, scope, mode, armed) "
                "VALUES (gen_random_uuid(), %s, %s, 'general', 'workshop', "
                "        'direct', 'continuous')",
                (org, user))
