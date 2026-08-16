"""The shell CLI must write to the store the app reads.

The two migrated scopes read their tickets from Postgres. `tasks._from_relational`
needs a pinned owner to resolve the tenant, and with none pinned it logs and
falls back to the file. In a CLI that is a silent wrong answer: reproduced
2026-08-15, `project_tasks.py set-status <id> done` printed the updated record
and exited 0, the file said `done`, Postgres still said `todo`, and the app
showed `todo`.

Two set-points had to be right for the documented invocation (`python3
scripts/project_tasks.py ...`) to reach the right store: the interpreter, which
needs sqlalchemy, and the owner.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[2] / "scripts" / "project_tasks.py"


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("project_tasks_cli", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_it_pins_the_owner_it_looks_up(cli, monkeypatch):
    from services import auth_store, ownership

    ownership.clear_current_owner()
    monkeypatch.setattr(auth_store, "operator_id", lambda: 41)
    cli._pin_owner()
    assert ownership.current_owner() == 41


def test_the_owner_is_looked_up_and_never_hardcoded(cli, monkeypatch):
    """A fresh install has a different id, and a machine-specific constant in
    source is what check_no_hardcoding.sh exists to catch."""
    from services import auth_store, ownership

    ownership.clear_current_owner()
    monkeypatch.setattr(auth_store, "operator_id", lambda: 7)
    cli._pin_owner()
    assert ownership.current_owner() == 7


def test_no_auth_store_leaves_file_backed_scopes_working(cli, monkeypatch):
    """A checkout with no database must still drive its own file store."""
    from services import auth_store, ownership

    ownership.clear_current_owner()

    def boom():
        raise RuntimeError("no auth store here")

    monkeypatch.setattr(auth_store, "operator_id", boom)
    cli._pin_owner()  # must not raise
    assert ownership.owner_or_none() is None


def test_the_cli_pins_before_it_does_anything(cli):
    """The pin has to happen inside main, not at a call site someone can forget."""
    import inspect

    src = inspect.getsource(cli.main)
    body = src.split("\n", 1)[1]
    assert "_pin_owner()" in body.split("argparse.ArgumentParser")[0]
