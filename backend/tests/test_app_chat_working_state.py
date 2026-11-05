"""An app chat's working state is its own thread's, written by its own model.

What is pinned: compaction in an app's scope used to write ONE note per scope,
so whichever chat compacted last overwrote it and every new chat in the scope
started inside that chat's work. It was also written by the cloud tier even
for a chat on a local model. Now an app chat's handoff is per thread (a new
chat starts with none), and a chat whose model a provider other than the CLI
serves is summarised by that model, through the app's gateway when there is
one. Every other chat keeps the one scope note, written as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import (app_chat, app_chat_gateway, chat_context, chat_store, knowledge,
                      llm_router, local_model_provider, own_model, ownership,
                      working_state)

STRUCTURED = """## Objective
- Plan the first page.

## Decisions and why
- Four panels, because the scene has four beats.

## Done
- None.

## Not done
- The layout.

## Shortcuts taken and what they owe
- None.

## Open questions
- None.
"""

SCOPE = "app:some-app"


@pytest.fixture()
def scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from wsplatform import paths
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    ownership.set_current_owner(4242)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def app_provider(tmp_path):
    decl = app_chat.ChatDeclaration(
        app_slug="some-app", project="some-app", root=Path(tmp_path),
        label="Some App", persona_file=None, mcp_service=None,
        mcp_path="/mcp", model=None)
    return chat_context.AppChatProvider(decl)


A = "a" * 32
B = "b" * 32


class TestTheNoteIsPerThread:
    def test_a_thread_reads_back_its_own_and_the_scope_sees_none(self, scoped):
        knowledge.write_working_state(SCOPE, STRUCTURED, author="derived:x",
                                      chat_id=A, thread_id=A)
        text, chat = knowledge.read_working_state(SCOPE, A)
        assert "Four panels" in text and chat == A
        assert knowledge.read_working_state(SCOPE, B) == ("", "")
        assert knowledge.read_working_state(SCOPE) == ("", "")

    def test_it_stays_out_of_the_scope_index(self, scoped):
        knowledge.write_working_state(SCOPE, STRUCTURED, thread_id=A)
        assert knowledge.working_state_note_id(A) not in knowledge.index_block(SCOPE)

    def test_the_scope_note_is_not_listed_either(self, scoped):
        knowledge.write_working_state(SCOPE, STRUCTURED)
        assert "(working-state)" not in knowledge.index_block(SCOPE)

    def test_it_neither_counts_against_nor_is_demoted_by_the_cap(self, scoped):
        for tid in (A, B):
            knowledge.write_working_state(SCOPE, STRUCTURED, thread_id=tid)
        out = knowledge.enforce_index_cap(SCOPE, max_lines=3)
        assert out["demoted"] == 0
        assert knowledge.read_working_state(SCOPE, A)[0]


class TestThePromptGetsOnlyItsOwn:
    def test_a_new_app_chat_starts_with_none(self, scoped, app_provider):
        knowledge.write_working_state(SCOPE, STRUCTURED, thread_id=A)
        assert chat_context.working_state_block(app_provider, thread_id=B) == ""
        assert chat_context.working_state_block(app_provider) == ""

    def test_a_compacted_app_chat_reads_back_its_own(self, scoped, app_provider):
        knowledge.write_working_state(SCOPE, STRUCTURED, thread_id=A)
        out = chat_context.working_state_block(app_provider, thread_id=A)
        assert out.startswith("=== WORKING STATE (this chat")
        assert "Four panels" in out

    def test_an_app_chat_never_reads_the_old_scope_note(self, scoped, app_provider):
        knowledge.write_working_state(SCOPE, STRUCTURED)
        assert chat_context.working_state_block(app_provider, thread_id=A) == ""


def _chatty(context_key: str) -> str:
    tid = chat_store.create_thread(context_key)["id"]
    for i in range(4):
        chat_store.append_message(tid, "user", f"message {i} " + "words " * 200)
        chat_store.append_message(tid, "assistant", f"reply {i} " + "words " * 200)
    return tid


class TestCompactionWritesIt:
    def _compact(self, tid):
        working_state.write_now(tid, chat_store.get_thread(tid))

    def test_an_app_chat_writes_its_own_thread_note(self, scoped, app_provider,
                                                    monkeypatch):
        monkeypatch.setattr(chat_context, "get_provider", lambda key: app_provider)
        monkeypatch.setattr(own_model, "serves", lambda model: False)
        seen = {}

        def fake_sync(prev, msgs, *, per_thread=False):
            seen["per_thread"] = per_thread
            return STRUCTURED
        monkeypatch.setattr(working_state, "_summarize_sync", fake_sync)
        tid = _chatty("general")
        self._compact(tid)
        assert seen["per_thread"] is True
        scope = knowledge.scope_for_context("general")
        assert knowledge.read_working_state(scope, tid)[0]
        assert knowledge.read_working_state(scope) == ("", "")

    def test_a_chat_on_a_served_model_is_summarised_by_it(self, scoped, monkeypatch):
        monkeypatch.setattr(working_state, "_resolve_model", lambda t: "local-model-x")
        monkeypatch.setattr(own_model, "serves", lambda model: model == "local-model-x")
        monkeypatch.setattr(own_model, "window_tokens", lambda model: 12_000)

        def no_cloud(*a, **k):
            raise AssertionError("the cloud tier was called")
        monkeypatch.setattr(llm_router, "call_cloud", no_cloud)
        calls = []

        def fake_call(prompt, *, model_id, thread=None, **kw):
            calls.append({"prompt": prompt, "model": model_id, "thread": thread})
            return {"text": STRUCTURED, "model": model_id}
        monkeypatch.setattr(own_model, "call", fake_call)
        tid = _chatty("general")
        self._compact(tid)
        assert calls and calls[0]["model"] == "local-model-x"
        assert calls[0]["thread"]["id"] == tid
        budget = (12_000 - working_state._OWN_MODEL_SUMMARY_TOKENS
                  - working_state._OWN_MODEL_HEADROOM_TOKENS) * 3
        assert len(calls[0]["prompt"]) <= budget
        assert "earlier messages left out" in calls[0]["prompt"]
        raw = (knowledge.scope_dir(knowledge.scope_for_context("general"))
               / f"{knowledge.WORKING_STATE_NOTE_ID}.md").read_text()
        assert raw.startswith("<!-- author: derived:local-model-x")

    def test_a_served_model_that_fails_writes_nothing_and_never_goes_to_the_cloud(
            self, scoped, monkeypatch):
        monkeypatch.setattr(working_state, "_resolve_model", lambda t: "local-model-x")
        monkeypatch.setattr(own_model, "serves", lambda model: True)
        monkeypatch.setattr(own_model, "window_tokens", lambda model: 0)

        def no_cloud(*a, **k):
            raise AssertionError("the cloud tier was called")

        def down(*a, **k):
            raise RuntimeError("server down")
        monkeypatch.setattr(llm_router, "call_cloud", no_cloud)
        monkeypatch.setattr(own_model, "call", down)
        tid = _chatty("general")
        self._compact(tid)
        assert knowledge.read_working_state(
            knowledge.scope_for_context("general")) == ("", "")


class TestTheCallGoesThroughTheGateway:
    def test_own_model_posts_to_the_gateway_with_the_thread(self, monkeypatch):
        posted = {}

        class Resp:
            status_code = 200

            def json(self):
                return {"content": [{"type": "text", "text": "ok"}], "model": "m"}

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, json=None, headers=None):
                posted.update(url=url, headers=headers, payload=json)
                return Resp()

        monkeypatch.setattr(local_model_provider, "_client", lambda timeout: Client())
        provider = local_model_provider.LocalHttpProvider("http://127.0.0.1:1")
        monkeypatch.setattr(own_model, "_provider", lambda model: provider)
        monkeypatch.setattr(app_chat_gateway, "gateway_route", lambda thread: (
            "http://127.0.0.1:2/llm", {"X-Workshop-Thread": thread["id"]}))
        out = own_model.call("hi", model_id="m", thread={"id": A}, max_tokens=99)
        assert out["text"] == "ok"
        assert posted["url"] == "http://127.0.0.1:2/llm/v1/messages"
        assert posted["headers"]["X-Workshop-Thread"] == A
        assert posted["headers"]["anthropic-version"]
        assert posted["payload"]["max_tokens"] == 99

    def test_a_cli_model_is_refused(self):
        with pytest.raises(ValueError):
            own_model.call("hi", model_id="no-such-model")

    def test_the_gateway_env_is_unchanged_by_the_split(self, monkeypatch):
        monkeypatch.setattr(app_chat_gateway, "gateway_route", lambda thread, turn_id="": (
            "http://127.0.0.1:2/llm", {"X-Workshop-Thread": "t", "X-Workshop-Turn": turn_id}))
        env = app_chat_gateway.gateway_env({"id": "t", "scope": SCOPE},
                                   {"ANTHROPIC_BASE_URL": "http://x",
                                    "ANTHROPIC_CUSTOM_HEADERS": "A: b"}, "u")
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:2/llm"
        assert env["ANTHROPIC_CUSTOM_HEADERS"].splitlines() == [
            "A: b", "X-Workshop-Thread: t", "X-Workshop-Turn: u"]
        assert app_chat_gateway.gateway_env({"id": "t"}, {}, "u") == {}
