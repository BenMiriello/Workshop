"""The C7 columns apply and reverse, on a database nobody needs.

Step C7 of `docs/platform-redesign/changes-and-review-2026-08-22.md`: a change
can be pre-approved (`auto_merge`) and records who merged it (`merged_by`,
`merge_basis`).

Three claims, and only one of them needs a server:

  * THERE IS STILL EXACTLY ONE HEAD, and this revision is on it. A second head
    is not a failure anywhere else -- alembic makes one happily, `upgrade head`
    then fails with "Multiple head revisions are present", and the person who
    finds out is whoever next deploys.

  * THE DEFAULT IS OFF IN THE SCHEMA ITSELF. `auto_merge` defaults false at the
    database, so a row inserted by anything that has never heard of C7 is not
    armed. A default that lives only in Python is a default one INSERT can miss.

  * UPGRADE THEN DOWNGRADE LEAVES THE SCHEMA WHERE IT STARTED. Against a
    throwaway database created and dropped by the test, never the
    installation's own.

DOWNGRADE IS ADDRESSED BY REVISION, NOT BY `-1`, for the reason
`test_lease_migration.py` records: `-1` means "whatever landed most recently",
which stops being this migration the moment another one lands.
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
_REVISION = "d3f5a90c71b2"
_PARENT = "f2a6d0c31b78"

_PROBE_DB = "wstest_auto_merge_migration"

_NEW_COLUMNS = ("auto_merge", "merged_by", "merge_basis")


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


def test_the_model_and_the_migration_agree_on_the_columns():
    """Two hand-maintained lists, and no database needed to compare them."""
    from db.models import Change

    source = next(_MIGRATIONS.glob(f"versions/{_REVISION}_*.py")).read_text()
    model_columns = {c.name for c in Change.__table__.columns}
    for column in _NEW_COLUMNS:
        assert column in model_columns, f"{column} is not on the model"
        assert f'"{column}"' in source, f"{column} is not in the migration"


def test_the_merge_actor_vocabulary_is_the_one_the_model_declares():
    from db.models import MERGE_ACTORS

    source = next(_MIGRATIONS.glob(f"versions/{_REVISION}_*.py")).read_text()
    for actor in MERGE_ACTORS:
        assert f'"{actor}"' in source or f"'{actor}'" in source, (
            f"{actor} is a merge actor the check constraint would reject")


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


def _change_columns(url: str) -> set[str]:
    import psycopg

    with psycopg.connect(_psycopg_url(url)) as c:
        return {r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'change'").fetchall()}


@pytestmark_db
def test_the_migration_applies_and_reverses(probe_db, monkeypatch):
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    cfg = _config(probe_db)

    command.upgrade(cfg, "head")
    after_up = _change_columns(probe_db)
    for column in _NEW_COLUMNS:
        assert column in after_up, f"{column} missing after upgrade"

    command.downgrade(cfg, _PARENT)
    after_down = _change_columns(probe_db)
    for column in _NEW_COLUMNS:
        assert column not in after_down, f"{column} survived the downgrade"
    assert "head_sha" in after_down, "the downgrade took the table with it"

    # And it is not a one-way door: re-applying works on the reversed schema.
    command.upgrade(cfg, "head")
    assert _NEW_COLUMNS[0] in _change_columns(probe_db)


@pytestmark_db
def test_a_row_written_by_something_that_never_heard_of_c7_is_not_armed(
        probe_db, monkeypatch):
    """The fail-closed default, in the schema rather than only in Python."""
    import psycopg
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    command.upgrade(_config(probe_db), "head")

    with psycopg.connect(_psycopg_url(probe_db), autocommit=True) as c:
        c.execute("INSERT INTO organization (id, slug, name) "
                  "VALUES (gen_random_uuid(), 'wstest-probe', 'Probe')")
        org = c.execute(
            "SELECT id FROM organization WHERE slug = 'wstest-probe'"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO change (id, org_id, project_slug, branch_slug, "
            "base_ref, head_sha, title) VALUES (gen_random_uuid(), %s, "
            "'proj', 'br', 'main', %s, 'untouched by C7')", (org, "a" * 40))
        armed, actor, basis = c.execute(
            "SELECT auto_merge, merged_by, merge_basis FROM change").fetchone()
        assert armed is False, "a change nobody armed must never merge itself"
        assert actor is None and basis is None


@pytestmark_db
def test_an_unknown_merge_actor_is_a_constraint_violation(probe_db, monkeypatch):
    """`merged_by` is a closed vocabulary. A row saying something else would be
    a merge whose attribution nothing can interpret, which is the confusion the
    column exists to remove."""
    import psycopg
    from alembic import command

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", probe_db)
    command.upgrade(_config(probe_db), "head")

    with psycopg.connect(_psycopg_url(probe_db), autocommit=True) as c:
        c.execute("INSERT INTO organization (id, slug, name) "
                  "VALUES (gen_random_uuid(), 'wstest-probe', 'Probe')")
        org = c.execute(
            "SELECT id FROM organization WHERE slug = 'wstest-probe'"
        ).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "INSERT INTO change (id, org_id, project_slug, branch_slug, "
                "base_ref, head_sha, title, merged_by) VALUES "
                "(gen_random_uuid(), %s, 'proj', 'br', 'main', %s, 't', "
                "'somebody')", (org, "a" * 40))
