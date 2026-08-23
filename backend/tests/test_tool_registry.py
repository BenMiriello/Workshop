"""A tool is a declared thing with an owner, and a base one cannot be edited.

Stage 1 + 2 of `docs/platform-redesign/tools-are-declared-2026-08-23.md`. What
these pin, in the doc's own terms:

  * THE PLATFORM TUPLE IS COMPLETE, measured against the live grants rather
    than against a copy of itself. `chat_context` and the workshop MCP server
    are imported and their tool names asserted to be IN the tuple, so the day
    someone grants a sixth workshop tool or a third web tool, this fails.
  * A USER TOOL ROUND-TRIPS through `.runtime/tools.json`, and a missing file
    reads as empty rather than as an error (a fresh clone ships none).
  * A USER TOOL MAY NOT TAKE A PLATFORM TOOL'S ID -- refused at write time with
    the id named, and dropped on read when written into the file by hand. This
    is the one deliberate divergence from `app_registry`, which lets a
    discovered app shadow a built-in; a platform tool a user record could
    redefine would be a default, not a base tool.
  * A MALFORMED RECORD IS NORMALIZED, never fatal (the `services/manifests`
    discipline), because tools.json is an ordinary file people edit.
  * THE API REFUSES to edit or delete a platform tool, with a reason.

What they deliberately do NOT pin: that any of this grants anything. It does
not. `services/llm_router.tool_flags` is still the only enforcement point, and
stage 3 is what makes the four declaration sources read from this table.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from services import tool_registry as tr


@pytest.fixture()
def tools_file(tmp_path, monkeypatch):
    """Point the user store at a tmp path. Module-global lookup, exactly as
    `services/installed_apps` is monkeypatched: no test may write the real
    `.runtime/tools.json`."""
    path = tmp_path / "tools.json"
    monkeypatch.setattr(tr, "REGISTRY_PATH", path)
    return path


@pytest.fixture()
def no_app_tools(monkeypatch):
    """Isolate from THIS instance's installed apps, which are `.runtime` state:
    a test about user tools must not depend on which apps happen to be
    installed on the box running it."""
    monkeypatch.setattr(tr, "app_tools", lambda: [])


# ---- the platform tuple ----------------------------------------------------

def test_platform_tuple_covers_every_tool_the_live_grants_name():
    """Complete, measured against the two places that actually grant these."""
    from services import chat_context

    ids = {t.id for t in tr.PLATFORM_TOOLS}
    for name in (*chat_context.WEB_TOOLS, chat_context.SUBAGENT_TOOL):
        assert name in ids, f"{name} is granted by chat_context but is not declared"
    for name in chat_context.WORKSHOP_TOOLKIT:
        assert name in ids, f"{name} is in WORKSHOP_TOOLKIT but is not declared"
    # The general provider's own allowlist, which is where query/search live.
    for name in chat_context._PLATFORM_PROVIDERS["general"].allowed_tools:
        assert name in ids, f"{name} is on the general allowlist but is not declared"


def test_platform_tuple_covers_every_tool_the_workshop_server_exposes():
    from mcp import workshop_query_server as wqs

    ids = {t.id for t in tr.PLATFORM_TOOLS}
    for name in (wqs.TOOL_NAME, wqs.SEARCH_TOOL_NAME, wqs.WHERE_IS_TOOL_NAME,
                 wqs.LIST_ATTACHMENTS_TOOL_NAME, wqs.GET_FILE_TOOL_NAME,
                 wqs.SPAWN_TOOL_NAME):
        assert f"mcp__{wqs.SERVER_NAME}__{name}" in ids


def test_platform_records_are_whole_and_reuse_the_servers_own_prose():
    from mcp import workshop_query_server as wqs

    for tool in tr.PLATFORM_TOOLS:
        assert tool.provenance == "platform"
        assert tool.kind in tr.KINDS
        assert tool.title and tool.description
        assert tool.ceiling == (tool.id,), "a builtin/mcp record resolves to itself"
        assert tool.editable is False
    search = next(t for t in tr.PLATFORM_TOOLS if t.id == "mcp__workshop__search")
    assert search.description == wqs.SEARCH_TOOL_DESC, "descriptions have one source"
    assert search.spec == {"server": "workshop", "tool": "search"}


def test_spawn_background_is_offered_only_to_a_chat():
    """The server adds it to tools/list only when a chat is bound; that is a
    fact about the tool, so it is in `scopes` rather than left implicit."""
    spawn = next(t for t in tr.PLATFORM_TOOLS if t.id.endswith("spawn_background"))
    assert spawn.scopes == ("chat",)


# ---- the user store --------------------------------------------------------

def test_missing_tools_json_is_empty_not_an_error(tools_file):
    assert not tools_file.exists()
    assert tr.user_tools() == []


def test_unreadable_tools_json_is_empty_not_an_error(tools_file):
    tools_file.write_text("{not json at all", encoding="utf-8")
    assert tr.user_tools() == []


def test_a_user_tool_round_trips_through_tools_json(tools_file, no_app_tools):
    created = tr.create_user_tool("shipit", {
        "title": "Ship it",
        "description": "Build and publish the bundle.",
        "kind": "command",
        "spec": {"command": ["bash", "scripts/apply.sh"],
                 "params": [{"name": "target", "required": True}]},
    })
    assert created.provenance == "user" and created.editable is True

    doc = json.loads(tools_file.read_text(encoding="utf-8"))
    assert [t["id"] for t in doc["tools"]] == ["shipit"]
    assert "provenance" not in doc["tools"][0], \
        "provenance belongs to the source that loaded the record, not to the file"

    back = tr.user_tools()
    assert len(back) == 1
    assert back[0] == created
    assert back[0].spec["command"] == ["bash", "scripts/apply.sh"]
    assert back[0].spec["params"] == [
        {"name": "target", "type": "string", "description": "", "required": True}]
    assert back[0].ceiling == (), "declaring a command tool grants nothing by default"

    assert tr.get("shipit") == created
    tr.update_user_tool("shipit", {"title": "Ship it (v2)", "kind": "command"})
    assert tr.get("shipit").title == "Ship it (v2)"
    assert tr.delete_user_tool("shipit") is True
    assert tr.user_tools() == []


def test_a_malformed_record_is_normalized_rather_than_fatal(tools_file):
    tools_file.write_text(json.dumps({"tools": [
        {"id": "wonky", "kind": "teleport", "scopes": "chat",
         "title": 17, "spec": ["not", "a", "dict"]},
        {"title": "no id at all"},          # unaddressable: dropped, not fatal
        "bare-string-id",                   # a bare id is a minimal declaration
        None,                               # dropped
    ]}), encoding="utf-8")

    tools = {t.id: t for t in tr.user_tools()}
    assert set(tools) == {"wonky", "bare-string-id"}
    wonky = tools["wonky"]
    assert wonky.kind == "builtin", "an unreadable kind lands on the one that implements nothing"
    assert wonky.title == "wonky", "a non-string title falls back to the id"
    assert wonky.scopes == ("chat",), "a lone string where a list was meant still reads"
    assert wonky.spec == {}
    assert wonky.provenance == "user"


def test_a_record_cannot_declare_itself_platform_owned(tools_file):
    tools_file.write_text(json.dumps({"tools": [
        {"id": "sneaky", "provenance": "platform"},
    ]}), encoding="utf-8")
    assert tr.user_tools()[0].provenance == "user"


# ---- the collision refusal -------------------------------------------------

def test_a_user_tool_may_not_take_a_platform_tools_id(tools_file, no_app_tools):
    with pytest.raises(tr.ToolConflict) as e:
        tr.create_user_tool("WebFetch", {"title": "mine now"})
    assert "WebFetch" in str(e.value), "the refusal must name the conflicting id"
    assert "platform" in str(e.value)
    assert not tools_file.exists(), "a refused write leaves no file behind"


def test_a_platform_id_written_into_the_file_by_hand_still_loses(tools_file):
    """The refusal is a property of the system, not a UI nicety: tools.json is
    an ordinary file an agent or a person can edit directly."""
    tools_file.write_text(json.dumps({"tools": [
        {"id": "WebSearch", "title": "redefined", "description": "not the real one"},
    ]}), encoding="utf-8")
    assert tr.user_tools() == []
    assert tr.get("WebSearch").provenance == "platform"
    assert tr.get("WebSearch").title == "Web search"


def test_an_unusable_id_is_refused_with_the_rule(tools_file, no_app_tools):
    with pytest.raises(ValueError) as e:
        tr.create_user_tool("has spaces && rm -rf", {})
    assert "tool id" in str(e.value)


def test_editing_or_deleting_a_platform_tool_is_refused(tools_file, no_app_tools):
    with pytest.raises(tr.ToolNotEditable) as e:
        tr.update_user_tool("Agent", {"title": "mine"})
    assert "Agent" in str(e.value)
    with pytest.raises(tr.ToolNotEditable):
        tr.delete_user_tool("Agent")


# ---- app provenance --------------------------------------------------------

def test_an_installed_apps_manifest_declares_its_tools(monkeypatch, tools_file):
    from services import installed_apps, manifests

    monkeypatch.setattr(installed_apps, "installed_app_ids", lambda: ["demo"])
    monkeypatch.setattr(manifests, "load_manifests", lambda: {
        "demo": manifests._normalize_entry("demo", {
            "tools": [
                {"id": "demo.sync", "title": "Sync", "kind": "command",
                 "spec": {"command": ["demo-sync"]}},
                {"no": "id"},          # normalized away, never fatal
                "demo.bare",
            ],
        }),
    })

    tools = {t.id: t for t in tr.app_tools()}
    assert set(tools) == {"demo.sync", "demo.bare"}
    assert tools["demo.sync"].provenance == "app"
    assert tools["demo.sync"].spec["app"] == "demo", "a tool leaves with its app"
    assert tools["demo.sync"].editable is False


def test_an_uninstalled_apps_tools_are_not_in_the_catalog(monkeypatch, tools_file):
    from services import installed_apps, manifests

    monkeypatch.setattr(installed_apps, "installed_app_ids", lambda: [])
    monkeypatch.setattr(manifests, "load_manifests", lambda: {
        "demo": manifests._normalize_entry("demo", {"tools": [{"id": "demo.sync"}]}),
    })
    assert tr.app_tools() == []


def test_a_manifest_with_no_tools_key_is_simply_empty():
    from services import manifests

    assert manifests._normalize_entry("demo", {})["tools"] == []
    assert manifests._normalize_entry("demo", {"tools": "nonsense"})["tools"] == []


# ---- the API ---------------------------------------------------------------

@pytest.fixture()
def client():
    """The two routers on a bare app.

    Mounted without `main`'s auth dependencies, exactly as `test_apps_api`'s
    client fixture does: what is under test is the tools contract, not the
    operator gate, and the gate has its own tests. That the real app MOUNTS
    these is a separate assertion below -- an unwired route that 404s is not
    done, and a bare-app test cannot see that.
    """
    from fastapi import FastAPI

    from routes import agents as agents_routes
    from routes import tools as tools_routes

    app = FastAPI()
    app.include_router(tools_routes.router)
    app.include_router(agents_routes.router, prefix="/agents")
    return TestClient(app)


def test_the_tools_router_is_wired_into_the_app():
    import main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/tools" in paths and "/tools/{tool_id}" in paths
    assert "/agents/tools" in paths


def test_the_route_serves_the_registry_and_agents_tools_serves_the_same(client, tools_file):
    for path in ("/tools", "/agents/tools"):
        r = client.get(path)
        assert r.status_code == 200, r.text
        rows = r.json()["tools"]
        by_id = {t["id"]: t for t in rows}
        for tool in tr.PLATFORM_TOOLS:
            assert by_id[tool.id]["provenance"] == "platform"
            assert by_id[tool.id]["editable"] is False
            assert by_id[tool.id]["description"]
        # The deleted vocabulary: not CLI tool names, not MCP server names,
        # enforced by nothing (doc sec 1).
        for dead in ("filesystem", "google-docs", "playwright", "ollama", "web"):
            assert dead not in by_id


def test_the_api_creates_updates_and_deletes_a_user_tool(client, tools_file, no_app_tools):
    r = client.post("/tools", json={"id": "preview.mine", "title": "My preview",
                                    "kind": "command",
                                    "spec": {"command": ["echo", "hi"]}})
    assert r.status_code == 200, r.text
    assert r.json()["provenance"] == "user" and r.json()["editable"] is True

    assert client.post("/tools", json={"id": "preview.mine"}).status_code == 409

    r = client.put("/tools/preview.mine", json={"title": "Renamed", "kind": "command"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"

    assert client.delete("/tools/preview.mine").status_code == 200
    assert client.delete("/tools/preview.mine").status_code == 404
    assert client.put("/tools/preview.mine", json={"title": "x"}).status_code == 404


def test_the_api_refuses_to_create_edit_or_delete_a_platform_tool(client, tools_file, no_app_tools):
    r = client.post("/tools", json={"id": "WebSearch", "title": "mine now"})
    assert r.status_code == 409
    assert "WebSearch" in r.json()["detail"]

    r = client.put("/tools/WebSearch", json={"title": "mine now"})
    assert r.status_code == 409
    assert "WebSearch" in r.json()["detail"] and "not edited" in r.json()["detail"]

    r = client.delete("/tools/WebSearch")
    assert r.status_code == 409
    assert "WebSearch" in r.json()["detail"]

    assert not tools_file.exists(), "no refused write reached the store"


# ---- stage 3: declaration sources read the registry (not private lists) ----

class TestDeclarationSourcesUseTheRegistry:
    """Every name any of the four declaration sources can produce is in
    PLATFORM_TOOLS, so a fifth private list cannot be added silently.

    doc §5 step 3: `agent_authority`, `chat_context`, `agents_registry`, and
    `llm_router` are the four sources. This class pins that invariant.
    """

    def test_group_constants_are_subsets_of_platform_tools(self):
        """The named group constants in tool_registry are the one definition of
        each tool name; each must name a tool that actually exists."""
        ids = tr.PLATFORM_TOOL_IDS
        groups: list[tuple[str, tuple[str, ...]]] = [
            ("WEB_TOOL_IDS", tr.WEB_TOOL_IDS),
            ("SUBAGENT_TOOL_ID", (tr.SUBAGENT_TOOL_ID,)),
            ("WORKSHOP_SEARCH_TOOL_ID", (tr.WORKSHOP_SEARCH_TOOL_ID,)),
            ("WORKSHOP_TOOLKIT_IDS", tr.WORKSHOP_TOOLKIT_IDS),
            ("GENERAL_ALLOWLIST_IDS", tr.GENERAL_ALLOWLIST_IDS),
            ("READ_TOOL_IDS", tr.READ_TOOL_IDS),
            ("TREE_WRITE_IDS", tr.TREE_WRITE_IDS),
            ("PERSONA_CEILING_IDS", tuple(tr.PERSONA_CEILING_IDS)),
            ("BASELINE_DISALLOW_IDS", tr.BASELINE_DISALLOW_IDS),
        ]
        for name, group in groups:
            for tid in group:
                assert tid in ids, (
                    f"{tid!r} is in tool_registry.{name} but has no PLATFORM_TOOLS entry; "
                    "add it to PLATFORM_TOOLS before putting it in a group constant"
                )

    def test_agent_authority_produces_only_registered_names(self):
        """tools_for_role yields only names the registry knows, for every role."""
        from services import agent_authority as aa
        ids = tr.PLATFORM_TOOL_IDS
        for role in ("maker", "reviewer", "triager", "keeper", "broker", None):
            for tid in aa.tools_for_role(role):
                assert tid in ids, (
                    f"agent_authority.tools_for_role({role!r}) yields {tid!r}, "
                    "which has no PLATFORM_TOOLS entry"
                )

    def test_persona_ceiling_is_fully_registered(self):
        """Every tool the persona ceiling blocks must be in the registry."""
        from services import agents_registry
        ids = tr.PLATFORM_TOOL_IDS
        for tid in agents_registry._PERSONA_TOOL_CEILING:
            assert tid in ids, (
                f"agents_registry._PERSONA_TOOL_CEILING includes {tid!r}, "
                "which has no PLATFORM_TOOLS entry"
            )

    def test_baseline_disallow_is_registered(self):
        """The one tool always denied in async turns must be in the registry."""
        from services import llm_router
        ids = tr.PLATFORM_TOOL_IDS
        for tid in llm_router.BASELINE_DISALLOWED_TOOLS:
            assert tid in ids, (
                f"llm_router.BASELINE_DISALLOWED_TOOLS includes {tid!r}, "
                "which has no PLATFORM_TOOLS entry"
            )


# ---- stage 4: command deny-list is applied at registration -----------------

def test_registration_refuses_a_command_with_a_denied_pattern(tools_file, no_app_tools):
    """A user tool whose command matches IRREVERSIBLE_PATTERNS is refused at
    create time, with the matching pattern named; the store file is not written.

    doc §5 step 4: "the deny list applied at registration and at run time."
    Checked at registration rather than only at run time so that a pattern added
    after a tool was declared cannot silently enable it.
    """
    with pytest.raises(ValueError) as exc_info:
        tr.create_user_tool("force-push", {
            "title": "Force push",
            "kind": "command",
            "spec": {"command": ["git", "push", "--force"]},
        })
    msg = str(exc_info.value)
    assert "Bash(git push:*)" in msg, (
        f"refusal must name the matching pattern; got: {msg!r}"
    )
    assert not tools_file.exists(), "a refused write leaves no file behind"
