"""`db.backfill --apply` imports without `--force` when the database is empty.

The guard exists because, once a scope's tickets live in Postgres, its
`.tasks.json` is a frozen backup and applying it would revert every ticket
written since. That is only true when the database HOLDS those tickets. An
installation importing for the first time has an empty database and the file
is the only copy, and it was refused all the same.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("sqlalchemy")

from db import backfill  # noqa: E402
from services import tasks  # noqa: E402


@pytest.fixture()
def run_main(monkeypatch):
    applied = []
    monkeypatch.setattr(tasks, "is_relational_scope", lambda root: True)
    monkeypatch.setattr(backfill, "apply", lambda: applied.append(1) or {})

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["db.backfill", *argv])
        return backfill.main(), bool(applied)
    return run


def test_an_empty_database_is_imported_without_force(run_main, monkeypatch):
    monkeypatch.setattr(backfill, "_scopes_holding_tickets", lambda slugs: [])
    assert run_main("--apply") == (0, True)


def test_a_database_holding_the_scope_is_still_refused(run_main, monkeypatch):
    monkeypatch.setattr(backfill, "_scopes_holding_tickets", lambda slugs: ["development"])
    assert run_main("--apply") == (2, False)
    assert run_main("--apply", "--force") == (0, True)


# --- the query itself, against a throwaway database --------------------------

_ADMIN = os.environ.get("WORKSHOP_DATABASE_ADMIN_URL", "").strip()
_PROBE_DB = "wstest_backfill_first_import"


@pytest.fixture()
def probe_db(monkeypatch):
    if not _ADMIN:
        pytest.skip("no WORKSHOP_DATABASE_ADMIN_URL; the query runs as the owner")
    import psycopg
    from alembic import command
    from alembic.config import Config

    u = urlsplit(_ADMIN)
    maintenance = urlunsplit(("postgresql", u.netloc, "/postgres", "", ""))
    with psycopg.connect(maintenance, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{_PROBE_DB}"')
        c.execute(f'CREATE DATABASE "{_PROBE_DB}"')
    url = urlunsplit((u.scheme, u.netloc, f"/{_PROBE_DB}", "", ""))
    monkeypatch.setenv("WORKSHOP_DATABASE_ADMIN_URL", url)
    cfg = Config()
    cfg.set_main_option("script_location",
                        str(Path(backfill.__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")
    try:
        yield url
    finally:
        with psycopg.connect(maintenance, autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{_PROBE_DB}"')


def test_the_query_sees_tickets_only_where_they_exist(probe_db):
    from db.engine import owner_session
    from db.ids import uuid7
    from db.models import Organization, Project, Ticket, WorkflowState

    assert backfill._scopes_holding_tickets(["development", "workshop"]) == []

    with owner_session() as s:
        org = Organization(id=uuid7(), slug="probe", name="probe")
        s.add(org)
        s.flush()
        projects = {}
        for slug, key in (("development", "DEV"), ("workshop", "WS")):
            projects[slug] = Project(id=uuid7(), org_id=org.id, slug=slug, name=slug, key=key)
            s.add(projects[slug])
        s.flush()
        dev = projects["development"]
        st = WorkflowState(id=uuid7(), org_id=org.id, project_id=dev.id, name="Todo",
                           category="unstarted", position=0, is_default=True)
        s.add(st)
        s.flush()
        s.add(Ticket(id=uuid7(), org_id=org.id, project_id=dev.id, number=1,
                     title="t", body="", state_id=st.id))
        s.commit()

    assert backfill._scopes_holding_tickets(["development", "workshop"]) == ["development"]
