"""The chat's context_key actually reaches the workshop MCP server as
WORKSHOP_QUERY_SCOPE, so a development chat can search development-scoped
content and a workshop chat cannot widen into it.

THE REGRESSION THIS CLOSES. `app_registry._platform_mcp_servers` used to read
`os.environ.get("WORKSHOP_QUERY_SCOPE", "workshop")` -- the BACKEND PROCESS's
own environment, which nothing anywhere ever set -- so every chat, on every
turn, searched as the literal string "workshop" regardless of which chat it
actually was. Since `scope_tree.reach("workshop")` deliberately excludes
`development`, the net effect was a DEVELOPMENT chat losing the ability to
search development-scoped content, which is most of the platform's own
history and exactly the content a development chat needs. The scope filter
(mcp/workshop_query_server.py `_run_search`) got narrower for the one scope
that is supposed to reach everything.

Mirrors test_workshop_query_owner.py's two-halves shape, and test_agentic_
turn_gets_search.py's `_build_cmd` harness: the spawning side
(`build_streaming._build_cmd`, via `app_registry.set_current_query_scope`)
hands the real chat's context_key down as an env var on the launch spec, and
the subprocess side (`workshop_query_server._run_search`, tested separately
in test_semantic_index.py / test_scope_tree.py) resolves it through
`scope_tree.reach`. This file is the propagation half: does the right value
actually leave this process on the actual turn-start path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import (agent_authority, auth_store,  # noqa: E402
                      build_streaming as bs, chat_store, llm_router,
                      ownership, paths)


def _meta(thread_id=None, **over) -> dict:
    m = {"thread_id": thread_id, "session_id": None, "resume": False,
         "cwd": str(paths.workshop_root()), "permission_mode": None,
         "allowed_tools": None, "tools_disabled": False, "mcp_selection": None,
         "model": None, "prompt": "hi"}
    m.update(over)
    return m


def _config_of(cmd: list[str]) -> dict | None:
    if "--mcp-config" not in cmd:
        return None
    return json.loads(cmd[cmd.index("--mcp-config") + 1])


@pytest.fixture(autouse=True)
def owner():
    """A turn has an owner pinned by auth (see test_agentic_turn_gets_search.py
    for why the server needs one to attach at all). Cleared after, along with
    the scope pin, so one test's context_key never leaks into the next."""
    oid = auth_store.operator_id()
    if oid is None:
        pytest.skip("no operator bootstrapped on this instance")
    ownership.set_current_owner(oid)
    yield oid
    ownership.clear_current_owner()
    # A fresh, current import rather than a module-level name: some other test
    # in this suite (test_app_registry_tolerant.py) pops and re-imports
    # `services.app_registry` to simulate a slimmed instance, which would
    # otherwise leave this cleanup call pinning a scope on an orphaned module
    # object nothing else reads.
    from services import app_registry
    app_registry.set_current_query_scope(None)


class TestTheRealTurnHandsDownTheRealChatsScope:
    """THE REAL DELIVERABLE. A future refactor that drops the propagation
    (e.g. reordering `_build_cmd` so the pin happens after the spec is built,
    or a new spawn path that forgets it) fails these loudly, instead of
    silently narrowing every search back to `workshop`."""

    def test_a_development_chats_context_key_rides_the_launch_env(self, monkeypatch):
        monkeypatch.setattr(chat_store, "get_thread",
                            lambda tid: {"context_key": "development"})
        doc = _config_of(bs._build_cmd(_meta("thread-1"))[0])
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert env.get("WORKSHOP_QUERY_SCOPE") == "development"

    def test_a_project_chats_context_key_rides_the_launch_env(self, monkeypatch):
        monkeypatch.setattr(chat_store, "get_thread",
                            lambda tid: {"context_key": "project:birds"})
        doc = _config_of(bs._build_cmd(_meta("thread-2"))[0])
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert env.get("WORKSHOP_QUERY_SCOPE") == "project:birds"

    def test_no_thread_omits_the_var_rather_than_guessing_workshop(self, monkeypatch):
        """No context_key known (a thread that vanished between claiming the
        run and building this argv, a malformed meta) must NOT silently become
        "workshop" HERE -- that confusion between "unknown" and "workshop" is
        the bug this closes. The var is omitted and the subprocess's OWN
        fallback (still "workshop", never "development") applies one layer
        down, exactly like an unpinned owner falling back to the operator."""
        monkeypatch.setattr(chat_store, "get_thread", lambda tid: None)
        doc = _config_of(bs._build_cmd(_meta(None))[0])
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert "WORKSHOP_QUERY_SCOPE" not in env

    def test_a_thread_lookup_that_raises_does_not_kill_the_turn(self, monkeypatch):
        monkeypatch.setattr(chat_store, "get_thread",
                            lambda tid: (_ for _ in ()).throw(RuntimeError("db down")))
        cmd, _ = bs._build_cmd(_meta("thread-3"))
        assert "--permission-mode" in cmd  # the turn is still runnable

    def test_the_narrow_tool_path_pins_it_too(self, monkeypatch):
        """The full-agentic branch is not the only spawner: a narrow
        (allowed_tools) turn reaches `_platform_mcp_servers` through
        `llm_router.tool_flags` -> `strict_mcp_flags` instead of
        `llm_router.mcp_config_json`, and must see the same pin -- it used to
        read the same broken env-var default. Checked the same black-box way
        as the full-agentic tests above (the JSON `_build_cmd` actually
        emits), not by peeking at `app_registry`'s own module state: that
        state lives on whatever module object happens to be in `sys.modules`
        at read time, and `test_app_registry_tolerant.py` (elsewhere in this
        suite) pops and re-imports `services.app_registry` to simulate a
        slimmed instance, which stale-binds any name imported before it ran.
        """
        monkeypatch.setattr(chat_store, "get_thread",
                            lambda tid: {"context_key": "development"})
        cmd, _ = bs._build_cmd(_meta(
            "thread-4", allowed_tools=[agent_authority.SEARCH_TOOL],
            mcp_servers=["workshop"]))
        doc = _config_of(cmd)
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert env.get("WORKSHOP_QUERY_SCOPE") == "development"


class TestTheBackendProcesssOwnEnvironmentIsNeverConsulted:
    def test_setting_the_old_var_by_hand_has_no_effect(self, monkeypatch):
        """THE EXACT REGRESSION, reproduced and then disproven. Before this
        fix, `WORKSHOP_QUERY_SCOPE` was read straight out of `os.environ` --
        this process's own environment, which no caller of `main.py` ever set
        -- so it always fell through to "workshop". Setting it by hand here
        must have NO effect on what actually gets pinned: only the thread's
        real context_key may decide it now."""
        monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "development")
        monkeypatch.setattr(chat_store, "get_thread",
                            lambda tid: {"context_key": "project:birds"})
        doc = _config_of(bs._build_cmd(_meta("thread-5"))[0])
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert env.get("WORKSHOP_QUERY_SCOPE") == "project:birds"


class TestABackgroundPassNeverDefaultsToDevelopment:
    def test_mcp_config_json_with_no_context_key_omits_the_var(self):
        """`scripts/ralph.sh` (and any other CLI use of `mcp-config`) calls
        `llm_router.mcp_config_json` with no chat behind it at all. It must
        fall through to the subprocess's own "workshop" default, never to
        "development": an unattended worker must not be handed the one scope
        that reaches everything just because nobody told it which chat it
        was."""
        doc = json.loads(llm_router.mcp_config_json(
            ["workshop"], owner_id=auth_store.operator_id()))
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert "WORKSHOP_QUERY_SCOPE" not in env

    def test_an_explicit_context_key_still_rides_through_mcp_config_json(self):
        """The seam `llm_router.py:930`'s comment names ("pin BEFORE building
        the specs") is reused, not duplicated: `_build_cmd`'s full-agentic
        branch calls this function WITH the thread's context_key, and it must
        carry through exactly like `owner_id` already does."""
        doc = json.loads(llm_router.mcp_config_json(
            ["workshop"], owner_id=auth_store.operator_id(),
            context_key="development"))
        env = doc["mcpServers"]["workshop"].get("env") or {}
        assert env.get("WORKSHOP_QUERY_SCOPE") == "development"
