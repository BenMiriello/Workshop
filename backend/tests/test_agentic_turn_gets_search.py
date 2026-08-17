"""A full-agentic turn can search the workshop's own writing.

THE HOLE THIS CLOSES. `_build_cmd`'s full-agentic branch sends no MCP flags at
all and inherits whatever the CLI's own configuration provides. The workshop
server is in neither `~/.claude.json` nor a `<cwd>/.mcp.json`, because we spawn
it rather than install it. So a project or build chat — the turn with the WIDEST
tool surface in the system — was the only one that could not read the notes,
tickets and chats it is working from.

Two details here are load-bearing and both have bitten before:

  * ADDITIVE, never `--strict-mcp-config`. Strict would silently drop every
    other server the tree legitimately configures.
  * A FLAG must follow the JSON. `--mcp-config` is variadic and keeps eating
    following non-flag tokens; on 2026-08-10 `--mcp-config '{...}' hi` sent the
    CLI looking for a config file named `hi`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import auth_store, build_streaming as bs, ownership  # noqa: E402
from wsplatform import paths  # noqa: E402


@pytest.fixture(autouse=True)
def owner():
    """A turn has an owner pinned by auth. Without one the server is
    deliberately NOT attached (see test_no_owner_gets_no_server), so every
    other case here has to establish one."""
    oid = auth_store.operator_id()
    if oid is None:
        pytest.skip("no operator bootstrapped on this instance")
    ownership.set_current_owner(oid)
    yield oid
    ownership.clear_current_owner()


def _meta(**over) -> dict:
    # Derived, never written out: check_no_hardcoding.sh fails the reviewer
    # gate on an absolute home path in tracked source.
    m = {"session_id": None, "resume": False, "cwd": str(paths.workshop_root()),
         "permission_mode": None, "allowed_tools": None, "tools_disabled": False,
         "mcp_selection": None, "model": None, "prompt": "hi"}
    m.update(over)
    return m


def _config_of(cmd: list[str]) -> dict | None:
    if "--mcp-config" not in cmd:
        return None
    return json.loads(cmd[cmd.index("--mcp-config") + 1])


def test_a_full_agentic_turn_gets_the_workshop_server() -> None:
    cmd, _ = bs._build_cmd(_meta())
    doc = _config_of(cmd)
    assert doc is not None, "the full-agentic branch emitted no MCP config"
    assert "workshop" in doc["mcpServers"]


def test_it_is_additive_and_not_strict() -> None:
    cmd, _ = bs._build_cmd(_meta())
    assert "--strict-mcp-config" not in cmd


def test_a_flag_follows_the_json_so_the_variadic_group_terminates() -> None:
    cmd, _ = bs._build_cmd(_meta())
    i = cmd.index("--mcp-config")
    assert len(cmd) > i + 2, "the json is last; a following flag is what ends it"
    assert cmd[i + 2].startswith("--"), f"a bare token follows the json: {cmd[i + 2]!r}"


def test_the_owner_is_pinned() -> None:
    """The server is owner-scoped. Unpinned, the turn searches an EMPTY index
    and every smoke test still passes, which is the way this ships broken."""
    doc = _config_of(bs._build_cmd(_meta())[0])
    env = (doc["mcpServers"]["workshop"].get("env") or {})
    assert env.get("WORKSHOP_QUERY_OWNER"), "no owner pinned on the workshop server"


def test_a_narrow_tool_turn_is_untouched() -> None:
    """Narrow turns get their MCP flags from tool_flags. This branch must not
    also emit one, or the turn carries two configs."""
    cmd, _ = bs._build_cmd(_meta(allowed_tools=["Read"]))
    assert cmd.count("--mcp-config") <= 1


def test_a_chat_that_chose_its_servers_is_untouched() -> None:
    """An explicit mcp_selection routes through tool_flags' strict path. Adding
    workshop here would override a choice the operator made."""
    cmd, _ = bs._build_cmd(_meta(mcp_selection=[]))
    assert cmd.count("--mcp-config") <= 1


def test_an_unavailable_server_does_not_kill_the_turn(monkeypatch) -> None:
    """A missing launch spec means this turn runs without search, exactly as it
    did before. It must never mean the turn fails to start."""
    from services import llm_router
    monkeypatch.setattr(llm_router, "mcp_config_json",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("workshop")))
    cmd, _ = bs._build_cmd(_meta())
    assert "--mcp-config" not in cmd
    assert "--permission-mode" in cmd


def test_no_owner_gets_no_server() -> None:
    """THE CROSS-ACCOUNT GUARD. The server is owner-scoped, and the config
    builder's default owner is the OPERATOR. An unowned turn that fell back to
    that default would be handed the operator's notes, tickets and chats, and
    would silently pin the operator for the rest of the context. A turn with no
    owner gets no search tool instead."""
    ownership.clear_current_owner()
    cmd, _ = bs._build_cmd(_meta())
    assert "--mcp-config" not in cmd
    assert "--permission-mode" in cmd, "the turn must still be runnable"


def test_the_pinned_owner_is_the_caller(owner: int) -> None:
    doc = _config_of(bs._build_cmd(_meta())[0])
    env = (doc["mcpServers"]["workshop"].get("env") or {})
    assert env.get("WORKSHOP_QUERY_OWNER") == str(owner)
