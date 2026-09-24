"""A brand-new Postgres server migrates with `upgrade head`, then `db.bootstrap`.

The application role `workshop_app` is created by `db.bootstrap`, and bootstrap
needs `ticket_event`, a later revision than 7bf9ae20fe0d, which REVOKEs from
that role. So on a server where the role had never existed, `upgrade head`
failed at 7bf9ae20fe0d and bootstrap could not run first: the only way through
was to upgrade to the revision before it, bootstrap, upgrade again and
bootstrap again. The migration now creates the role bare when it is missing.

The role is CLUSTER-wide, so this can only be proved on a server that has never
had one. On any other (an installation's own, or a shared test server) it skips
rather than dropping a role something may be connecting as. Point
WORKSHOP_DATABASE_ADMIN_URL at a throwaway server to run it.
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
psycopg = pytest.importorskip("psycopg")

_MIGRATIONS = _BACKEND / "db" / "migrations"
_PROBE_DB = "wstest_fresh_database_migrates"
_ADMIN = os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not _ADMIN,
    reason="no WORKSHOP_DATABASE_ADMIN_URL; migrations run as the schema owner",
)


def _url_for(dbname: str) -> str:
    u = urlsplit(_ADMIN)
    return urlunsplit((u.scheme, u.netloc, f"/{dbname}", "", ""))


def _plain(url: str) -> str:
    return url.replace("postgresql+psycopg", "postgresql")


def _config(url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture()
def fresh_server():
    from db import bootstrap

    maintenance = _plain(_url_for("postgres"))
    with psycopg.connect(maintenance, autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s",
                     (bootstrap.APP_ROLE,)).fetchone():
            pytest.skip(f"{bootstrap.APP_ROLE} already exists on this server; "
                        "this needs one that has never had it")
        c.execute(f'DROP DATABASE IF EXISTS "{_PROBE_DB}"')
        c.execute(f'CREATE DATABASE "{_PROBE_DB}"')
    try:
        yield _url_for(_PROBE_DB)
    finally:
        with psycopg.connect(maintenance, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{_PROBE_DB}"')
            c.execute(f"DROP ROLE IF EXISTS {bootstrap.APP_ROLE}")


def test_upgrade_head_then_bootstrap_on_a_server_with_no_app_role(fresh_server, monkeypatch):
    from alembic import command
    from alembic.script import ScriptDirectory

    from db import bootstrap

    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", fresh_server)
    cfg = _config(fresh_server)
    command.upgrade(cfg, "head")
    bootstrap.bootstrap("probe-password")

    head = ScriptDirectory.from_config(cfg).get_current_head()
    u = urlsplit(fresh_server)
    app_url = urlunsplit(("postgresql", f"{bootstrap.APP_ROLE}:probe-password@{u.hostname}"
                          f":{u.port or 5432}", u.path, "", ""))
    with psycopg.connect(_plain(fresh_server)) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head
    # The role the migration created bare can log in once bootstrap has run,
    # holds its DML, and still cannot provision.
    with psycopg.connect(app_url) as c:
        assert c.execute("SELECT count(*) FROM ticket").fetchone()[0] == 0
        c.execute("SELECT * FROM workshop_resolve_tenant(1)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO organization (id, name) VALUES (gen_random_uuid(), 'x')")
