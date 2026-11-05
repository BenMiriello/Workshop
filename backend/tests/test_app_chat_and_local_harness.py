"""Two generic capabilities: a runner app's chat read from its manifest, and an
agentic turn on a local model run through the same CLI harness.

What is pinned. For the app chat: the `chat` block alone decides the persona,
the HTTP MCP server and the scope's declared model, and an app without the block
is untouched. For the harness: a provider that declares it can take an agentic
turn, the redirect covers every model alias the CLI uses, and no cloud
credential reaches a process pointed at a local server.
"""

from __future__ import annotations

import json
import types

import pytest

from services import (app_chat, app_chat_gateway, app_links, app_runner, chat_context,
                      llm_providers, llm_router, local_model_provider,
                      model_resolution, ownership)


def _provider(slug):
    decl = app_chat.declaration(slug)
    return chat_context.AppChatProvider(decl) if decl is not None else None

SLUG = "some-app"


@pytest.fixture()
def app_tree(tmp_path, monkeypatch):
    """A linked runner app whose manifest the test writes."""
    root = tmp_path / SLUG
    root.mkdir()
    monkeypatch.setattr(app_links, "linked_project_slug",
                        lambda slug: slug if slug == SLUG else None)
    monkeypatch.setattr(app_links, "load", lambda: types.SimpleNamespace(
        projects={SLUG: {"title": "Some App"}}))
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: root)
    monkeypatch.setattr(app_runner, "app_status", lambda slug: {"services": [
        {"name": "api", "port": 8765, "status": "running"}]})

    def write(chat: dict | None) -> None:
        manifest = {"name": SLUG, "services": [
            {"name": "api", "start": "true", "port": 8700}]}
        if chat is not None:
            manifest["chat"] = chat
        (root / "workshop.app.json").write_text(json.dumps(manifest))

    write.root = root
    return write


class TestTheGateway:
    """An app's chat may send its model requests through one of its services,
    which then queues them with the app's other work."""

    THREAD = {"id": "t-1", "scope": f"app:{SLUG}", "title": "Notes & ideas"}

    @pytest.fixture()
    def thread(self):
        return self.THREAD

    LOCAL = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8099", "X": "1"}

    def test_a_turn_on_a_model_server_goes_through_the_gateway(
            self, app_tree, thread):
        app_tree({"gateway": {"service": "api", "path": "/llm"}})
        env = app_chat_gateway.gateway_env(thread, dict(self.LOCAL))
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8765/llm"
        assert env["X"] == "1"
        assert env["CLAUDE_STREAM_IDLE_TIMEOUT_MS"] == str(app_chat_gateway.STREAM_IDLE_MS)
        assert env["ANTHROPIC_CUSTOM_HEADERS"].splitlines() == [
            "X-Workshop-Thread: t-1",
            "X-Workshop-Thread-Title: Notes%20%26%20ideas",
            f"X-Workshop-App: {SLUG}"]

    def test_the_gateway_is_told_which_turn_sent_it(self, app_tree, thread):
        app_tree({"gateway": {"service": "api", "path": "/llm"}})
        env = app_chat_gateway.gateway_env(thread, dict(self.LOCAL), "turn-7")
        assert env["ANTHROPIC_CUSTOM_HEADERS"].splitlines()[-1] \
            == "X-Workshop-Turn: turn-7"

    def test_a_turn_the_cli_serves_itself_is_untouched(self, app_tree, thread):
        app_tree({"gateway": {"service": "api"}})
        assert app_chat_gateway.gateway_env(thread, {"X": "1"}) == {"X": "1"}

    def test_an_app_without_a_gateway_goes_straight_to_the_server(
            self, app_tree, thread):
        app_tree({"model": "sonnet"})
        env = app_chat_gateway.gateway_env(thread, dict(self.LOCAL))
        assert env == {**self.LOCAL,
                       "ANTHROPIC_CUSTOM_HEADERS": f"X-Workshop-App: {SLUG}"}

    def test_the_gateway_is_told_the_apps_compaction_model(
            self, app_tree, thread, monkeypatch):
        app_tree({"gateway": {"service": "api", "path": "/llm"}})
        monkeypatch.setattr(
            app_chat_gateway.local_model_provider, "compaction_model_for",
            lambda scope: "big" if scope == f"app:{SLUG}" else None)
        env = app_chat_gateway.gateway_env(thread, dict(self.LOCAL))
        assert env["ANTHROPIC_CUSTOM_HEADERS"].splitlines()[-1] \
            == f"{app_chat_gateway.COMPACTION_HEADER}: big"
        monkeypatch.setattr(
            app_chat_gateway.local_model_provider, "compaction_model_for",
            lambda scope: None)
        env = app_chat_gateway.gateway_env(thread, dict(self.LOCAL))
        assert app_chat_gateway.COMPACTION_HEADER not in \
            env["ANTHROPIC_CUSTOM_HEADERS"]

    def test_another_scope_is_untouched(self, app_tree):
        app_tree({"gateway": {"service": "api"}})
        assert app_chat_gateway.gateway_env({"id": "t-1", "scope": "development"},
                                    dict(self.LOCAL)) == self.LOCAL


class TestTheChatBlock:
    def test_an_app_without_the_block_has_no_declared_chat(self, app_tree):
        app_tree(None)
        assert _provider(SLUG) is None

    def test_the_persona_is_the_declared_file_read_fresh(self, app_tree):
        (app_tree.root / "chat").mkdir()
        (app_tree.root / "chat" / "persona.txt").write_text("first")
        app_tree({"persona": "chat/persona.txt"})
        provider = _provider(SLUG)
        assert provider.persona() == "first"
        (app_tree.root / "chat" / "persona.txt").write_text("second")
        assert provider.persona() == "second"

    def test_a_persona_outside_the_tree_is_ignored(self, app_tree, tmp_path):
        (tmp_path / "outside.txt").write_text("escaped")
        app_tree({"persona": "../outside.txt"})
        assert "escaped" not in _provider(SLUG).persona()

    def test_the_mcp_server_is_http_at_the_running_port(self, app_tree):
        app_tree({"mcp": {"service": "api", "path": "/mcp"}})
        provider = _provider(SLUG)
        assert provider.mcp_specs() == {"some-app": {
            "type": "http", "url": "http://127.0.0.1:8765/mcp"}}
        assert provider.mcp_servers == ["some-app", "workshop"]
        assert "mcp__some-app__*" in provider.allowed_tools
        assert set(chat_context.WORKSHOP_TOOLKIT) <= set(provider.allowed_tools)

    def test_a_stopped_service_falls_back_to_its_pinned_port(
            self, app_tree, monkeypatch):
        monkeypatch.setattr(app_runner, "app_status", lambda slug: {"services": []})
        app_tree({"mcp": {"service": "api"}})
        spec = _provider(SLUG).mcp_specs()["some-app"]
        assert spec["url"] == "http://127.0.0.1:8700/mcp"

    def test_the_model_is_the_scopes_declared_default(self, app_tree):
        app_tree({"model": "sonnet"})
        provider = _provider(SLUG)
        assert provider.declared_model == "sonnet"
        # The scope's rung turns the tier name into the model it names.
        assert model_resolution._lookup(provider.declared_model).id == llm_router.TIER_SONNET

    def test_get_provider_answers_the_app_scope_with_it(self, app_tree):
        app_tree({"model": "sonnet"})
        provider = chat_context.get_provider(f"app:{SLUG}")
        assert isinstance(provider, chat_context.AppChatProvider)
        assert provider.key == f"app:{SLUG}"

    def test_the_full_provider_list_offers_the_app_scope(self, app_tree, monkeypatch):
        app_tree({"model": "sonnet"})
        monkeypatch.setattr(app_links, "all_app_slugs", lambda: [SLUG, "other"])
        keys = [f"app:{d.app_slug}" for d in app_chat.declarations()]
        assert keys == [f"app:{SLUG}"]

    def test_the_declared_server_reaches_the_cli_flags(self, app_tree):
        app_tree({"mcp": {"service": "api"}})
        provider = _provider(SLUG)
        flags, _env, blocks = llm_router.tool_flags(
            allowed_tools=chat_context.effective_allowed_tools(provider),
            mcp_servers=provider.mcp_servers, mcp_specs=provider.mcp_specs())
        config = json.loads(flags[flags.index("--mcp-config") + 1])
        assert config["mcpServers"]["some-app"]["type"] == "http"
        assert blocks


class _HarnessProvider:
    """A local server that drives the CLI harness."""

    id = "test_local"
    transport = llm_providers.TRANSPORT_HTTP
    wire = llm_providers.WIRE_ANTHROPIC
    base_url = "http://127.0.0.1:9"
    auth_mode = "none"
    cli_harness = True

    def models(self):
        return [llm_providers.ModelInfo(
            id="a-harnessed-weight", label="A", provider_id=self.id, wire=self.wire)]

    def reach(self):
        return llm_providers.Reach(llm_providers.REACH_READY)

    def cli_env(self, model, env):
        return {**env, "ANTHROPIC_BASE_URL": self.base_url}

    def run_turn(self, prompt, *, model, **kw):  # pragma: no cover
        raise AssertionError("not called")


@pytest.fixture()
def harnessed(tmp_path, monkeypatch):
    real = llm_providers.providers
    monkeypatch.setattr(llm_providers, "providers",
                        lambda: [*real(), _HarnessProvider()])
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


class TestTheLocalHarness:
    def test_an_agentic_turn_may_run_on_a_harness_provider(self, harnessed):
        choice = model_resolution.resolve_model(1, None, model_resolution.ModelRequest(
            model="a-harnessed-weight", agentic=True))
        assert choice.provider_id == "test_local"

    def test_the_turn_env_is_redirected_only_for_that_model(self, harnessed):
        assert llm_providers.cli_turn_env("a-harnessed-weight", {})[
            "ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
        assert llm_providers.cli_turn_env(llm_router.TIER_HAIKU, {}) == {}

    def test_the_local_provider_redirects_every_alias_and_drops_credentials(
            self, monkeypatch):
        monkeypatch.delenv("WORKSHOP_BACKEND_PORT", raising=False)
        env = local_model_provider.LocalHttpProvider("http://127.0.0.1:8099").cli_env(
            "w", {"CLAUDE_CODE_OAUTH_TOKEN": "secret", "ANTHROPIC_API_KEY": "k",
                  "PATH": "/bin"})
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8099"
        assert env["ANTHROPIC_AUTH_TOKEN"]
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
        for alias in ("ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                      "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                      "CLAUDE_CODE_SUBAGENT_MODEL", "ANTHROPIC_MODEL"):
            assert env[alias] == "w"
        assert env["CLAUDE_CODE_ATTRIBUTION_HEADER"] == "0"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
        assert env["PATH"] == "/bin"

    def test_the_local_provider_declares_the_harness(self):
        provider = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
        assert llm_providers.drives_cli_harness(provider)

    def test_a_local_turn_caps_its_reply_so_compaction_waits_for_the_window(
            self, monkeypatch):
        provider = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
        model = types.SimpleNamespace(
            id="w", capabilities=types.SimpleNamespace(context_tokens=65536))
        monkeypatch.setattr(provider, "models", lambda: [model])
        env = provider.cli_env("w", {})
        assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "65536"
        assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8000"
