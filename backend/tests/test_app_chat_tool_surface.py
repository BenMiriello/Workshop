"""An app's own assistant sees exactly its tools, and its search stays home.

What is pinned: the CLI's `--allowedTools` gates a call but still LISTS every
tool a server offers, so an app turn was shown `query`, `spawn_background` and
the rest (49 tools at init) and told by the orientation map that it could use
them. The turn now confines the workshop server to the app toolkit, `search`
included, and that search and `open_ref` reach only the chat's own scope's
notes and chats. Every other turn is unconfined, as before.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mcp import workshop_query_server as wqs
from services import (app_chat, app_registry, build_streaming, chat_context,
                      orientation, semantic_index)
from services import tool_registry as tr


@pytest.fixture()
def app_provider(tmp_path):
    decl = app_chat.ChatDeclaration(
        app_slug="some-app", project="some-app", root=Path(tmp_path),
        label="Some App", persona_file=None, mcp_service=None,
        mcp_path="/mcp", model=None)
    return chat_context.AppChatProvider(decl)


@pytest.fixture()
def unconfined():
    app_registry.set_current_query_confinement(None)
    yield
    app_registry.set_current_query_confinement(None)


def _bare(ids) -> set[str]:
    return {t.split("__", 2)[2] for t in ids if t.startswith("mcp__workshop__")}


class TestTheGrant:
    def test_an_app_chat_is_granted_search_and_not_query_or_spawn(self, app_provider):
        allowed = chat_context.effective_allowed_tools(app_provider)
        assert tr.WORKSHOP_SEARCH_TOOL_ID in allowed
        assert tr.WORKSHOP_QUERY_TOOL_ID not in allowed
        assert tr.SPAWN_TOOL_ID not in allowed
        assert tr.SUBAGENT_TOOL_ID not in allowed

    def test_the_confinement_is_the_allowlist_bare(self, app_provider):
        names = app_registry.confined_workshop_tools(app_provider.allowed_tools)
        assert set(names) == _bare(tr.APP_TOOLKIT_IDS)

    def test_a_whole_server_grant_is_not_a_confinement(self):
        assert app_registry.confined_workshop_tools(["mcp__workshop__*"]) is None
        assert app_registry.confined_workshop_tools(None) is None


class TestTheTurnPassesIt:
    NARROW = {"turn_id": "t1", "model": "claude-opus-5", "session_id": None,
              "resume": False, "cwd": "/tmp",
              "permission_mode": "bypassPermissions",
              "allowed_tools": ["mcp__workshop__search", "mcp__workshop__open_ref"],
              "mcp_servers": ["workshop"]}

    def _workshop_env(self) -> dict:
        return app_registry.spec_context(app_registry._platform_mcp_servers()["workshop"])

    def test_an_app_turn_confines_the_server(self, unconfined):
        build_streaming._build_cmd({**self.NARROW, "app_scoped": True})
        env = self._workshop_env()
        assert env["WORKSHOP_QUERY_CONFINED"] == "1"
        assert set(env["WORKSHOP_QUERY_TOOLS"].split(",")) == {"search", "open_ref"}

    def test_any_other_turn_is_unconfined(self, unconfined):
        build_streaming._build_cmd({**self.NARROW, "app_scoped": True})
        build_streaming._build_cmd(self.NARROW)
        env = self._workshop_env()
        assert "WORKSHOP_QUERY_CONFINED" not in env
        assert "WORKSHOP_QUERY_TOOLS" not in env


@pytest.fixture()
def confined(monkeypatch):
    monkeypatch.setenv("WORKSHOP_QUERY_SCOPE", "app:some-app")
    monkeypatch.setenv("WORKSHOP_QUERY_THREAD", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("WORKSHOP_QUERY_CONFINED", "1")
    monkeypatch.setenv("WORKSHOP_QUERY_TOOLS", ",".join(sorted(_bare(tr.APP_TOOLKIT_IDS))))
    monkeypatch.setattr(wqs, "_pin_owner", lambda: None)


class TestTheServer:
    def test_a_confined_server_lists_only_the_granted_tools(self, confined):
        names = {t["name"] for t in wqs._tool_list()}
        assert names <= _bare(tr.APP_TOOLKIT_IDS)
        assert "search" in names
        assert not {"query", "spawn_background"} & names

    def test_an_unconfined_server_still_lists_query_and_spawn(self, confined, monkeypatch):
        monkeypatch.delenv("WORKSHOP_QUERY_CONFINED")
        names = {t["name"] for t in wqs._tool_list()}
        assert {"query", "spawn_background"} <= names

    def test_a_call_outside_the_confinement_is_unknown(self, confined):
        resp = wqs._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "query", "arguments": {}}})
        assert "unknown tool" in resp["error"]["message"]

    def test_search_reaches_only_its_own_notes_and_chats(self, confined, monkeypatch):
        seen = {}

        def fake_search(query, **kw):
            seen.update(kw)
            return {"hits": [], "mode": "semantic", "rebuilding": False,
                    "searched": 0, "corpus": 1}
        monkeypatch.setattr(semantic_index, "search", fake_search)
        monkeypatch.setattr(semantic_index, "status",
                            lambda: {"chunks": 1, "embedder_error": None})
        wqs._run_search({"query": "the outline", "kinds": ["note", "doc", "chat", "ticket"]})
        assert seen["scope"] == {"app:some-app"}
        assert set(seen["kinds"]) == {"note", "chat"}
        assert seen["confined"] is True

    def test_asking_only_for_docs_is_told_what_it_reaches(self, confined):
        out = wqs._run_search({"query": "x", "kinds": ["doc"]})
        assert "only its own notes and past chats" in out


class TestTheIndexFilter:
    META = [
        {"kind": "doc", "scope": "", "ref": "docs/a.md"},
        {"kind": "note", "scope": "app:some-app", "ref": "n.md"},
        {"kind": "chat", "scope": "app:other", "ref": "t2"},
    ]

    def test_confined_drops_placeless_and_other_scopes(self, monkeypatch):
        monkeypatch.setattr(semantic_index, "_resolve_chunk_scope", lambda k, raw: raw)
        mask = semantic_index._keep_mask(self.META, None, {"app:some-app"}, None, True)
        assert mask.tolist() == [False, True, False]

    def test_unconfined_keeps_placeless(self, monkeypatch):
        monkeypatch.setattr(semantic_index, "_resolve_chunk_scope", lambda k, raw: raw)
        mask = semantic_index._keep_mask(self.META, None, {"app:some-app"}, None)
        assert isinstance(mask, np.ndarray)
        assert mask.tolist() == [True, True, False]

    def test_confined_reachable_item_refuses_a_doc(self):
        assert not semantic_index.reachable_item(
            "doc", "", "workshop:doc/a.md", scopes={"app:some-app"}, confined=True)


class TestTheMapSaysOnlyWhatIsTrue:
    def test_the_app_map_names_search_and_no_query_or_subagent(self, app_provider):
        out = chat_context.orientation_manifest(app_provider)
        assert "`search`" in out
        assert "`query`" not in out
        assert "Agent tool" not in out
        assert "`spawn_background`" not in out
        assert "workshop:doc/" not in out

    def test_a_chat_without_search_is_not_told_about_it(self):
        out = orientation.manifest_text("workshop", search=False, query=False,
                                        subagent=False, background=False)
        assert "`search`" not in out and "`query`" not in out
        assert "Agent tool" not in out

    def test_the_default_map_is_unchanged_in_what_it_names(self):
        out = orientation.manifest_text("workshop")
        for words in ("`search`", "`query`", "Agent tool", "`spawn_background`",
                      "workshop:ticket/<id>"):
            assert words in out, words

    def test_where_is_on_a_confined_server_matches(self, confined, monkeypatch):
        monkeypatch.setattr(wqs, "_corpus_lines", lambda scope: "")
        out = wqs._run_where_is()
        assert "`query`" not in out and "Agent tool" not in out
        assert "`search`" in out
