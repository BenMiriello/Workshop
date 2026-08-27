"""`mcp__workshop__query` used to hard-pin every caller to the operator's
identity ("Single-operator system"), so its digest of open tasks/jobs/notes
leaked to whoever was actually chatting. The server is a separate stdio
subprocess spawned fresh per turn — it has no access to the backend's
request-scoped `ownership` contextvar on its own — so fixing this is two
halves: the spawning side (app_registry._platform_mcp_servers) hands the real
caller down as an env var, and the subprocess side
(mcp.workshop_query_server._pin_owner) reads it instead of always asking
auth_store for the operator. See
the 2026-08-11 scope-separation security audit section 3.
"""
from __future__ import annotations

import pytest

from services import app_registry, auth_store, ownership
from mcp import workshop_query_server as wqs


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    try:
        yield
    finally:
        ownership.clear_current_owner()


class TestSpawnSideHandsDownTheRealCaller:
    def test_the_pinned_owner_rides_in_the_launch_env(self, owned):
        ownership.set_current_owner(42)
        spec = app_registry._platform_mcp_servers()["workshop"]
        assert spec["env"]["WORKSHOP_QUERY_OWNER"] == "42"

    def test_no_owner_pinned_omits_the_var_rather_than_guessing(self, owned):
        # No set_current_owner() call: nothing pinned. A background pass with
        # no per-request caller falls through to the subprocess's own
        # operator fallback (see below), which is correct there.
        spec = app_registry._platform_mcp_servers()["workshop"]
        assert "WORKSHOP_QUERY_OWNER" not in spec["env"]


class TestSubprocessSidePinsWhoeverWasHandedDown:
    def test_the_env_var_wins_over_the_operator(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
        auth_store.init_db()
        op = auth_store.create_user("operator@example.com", "password1")
        member = auth_store.create_user("member@example.com", "password2")
        auth_store.set_operator(op["id"])
        monkeypatch.setenv("WORKSHOP_QUERY_OWNER", str(member["id"]))
        try:
            wqs._pin_owner()
            assert ownership.current_owner() == member["id"]
            assert ownership.current_owner() != op["id"]
        finally:
            ownership.clear_current_owner()

    def test_falls_back_to_the_operator_when_nothing_was_handed_down(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth.db")
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
        auth_store.init_db()
        op = auth_store.create_user("operator@example.com", "password1")
        auth_store.set_operator(op["id"])
        monkeypatch.delenv("WORKSHOP_QUERY_OWNER", raising=False)
        try:
            wqs._pin_owner()
            assert ownership.current_owner() == op["id"]
        finally:
            ownership.clear_current_owner()

    def test_never_raises_when_nothing_can_be_resolved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth-empty.db")
        monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime-empty")
        auth_store.init_db()  # no operator bootstrapped
        monkeypatch.delenv("WORKSHOP_QUERY_OWNER", raising=False)
        wqs._pin_owner()  # must not raise
        with pytest.raises(ownership.OwnerUnset):
            ownership.current_owner()
