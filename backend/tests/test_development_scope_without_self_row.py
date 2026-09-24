"""The Development scope works on an installation with no `self: true` row.

Nothing writes that row: an installation that predates it, or that never
registered a project, has none. The chat provider used to derive its tree from
the row alone, so without it the scope pointed at `projects/development`, which
does not exist, and rescoping a chat to Development was refused as an unknown
project. The scope means this checkout either way, which is what `task_scope`
already answered for it.
"""
import asyncio
import json

import pytest

from routes import chat as chat_route
from services import chat_context, chat_store, ownership, task_scope
from wsplatform import dev_mode, paths


@pytest.fixture()
def no_self_row(tmp_path, monkeypatch):
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": {"other": {"dir": str(tmp_path / "other")}}}))
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(reg))
    monkeypatch.setenv(dev_mode.DEV_MODE_ENV, "1")
    return tmp_path


def test_the_provider_resolves_to_this_checkout(no_self_row):
    p = chat_context.get_provider("development")
    assert isinstance(p, chat_context.DevelopmentContextProvider)
    assert p.key == "development"
    assert p.dir == paths.workshop_root()
    assert p.is_self
    assert p.dir == task_scope.root_for_context("development")


def test_a_missing_registry_resolves_the_same(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_PROJECTS_REGISTRY", str(tmp_path / "absent.json"))
    monkeypatch.setenv(dev_mode.DEV_MODE_ENV, "1")
    assert chat_context.get_provider("development").dir == paths.workshop_root()


def test_a_chat_can_be_rescoped_to_development(no_self_row, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", no_self_row)
    ownership.set_current_owner(1)
    try:
        t = chat_store.create_thread("general")
        out = asyncio.run(chat_route.rescope_thread(
            t["id"], chat_route.RescopeThread(context_key="development")))
        assert out["context_key"] == "development"
    finally:
        ownership.clear_current_owner()
