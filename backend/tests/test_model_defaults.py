"""The operator's model defaults: a default per scope above what the scope
declares, the global default below both, and the Settings routes that edit
them alongside the compaction model.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import chat as chat_routes
from services import (chat_context, chat_store, llm_contract, llm_router,
                      local_model_provider as lmp, model_resolution, ownership)

OPUS = llm_router.TIER_OPUS_5
SONNET = llm_router.TIER_SONNET
HAIKU = llm_router.TIER_HAIKU


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setenv("WORKSHOP_LOCAL_MODEL_CONFIG", str(tmp_path / "local.json"))
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


def _resolve(request, scope=None):
    return model_resolution.resolve_model(1, scope, request)


class TestTheStore:
    def test_nothing_is_set_at_first(self, owned):
        assert chat_store.get_scope_models() == {}
        assert chat_store.get_scope_model("project:x") is None

    def test_a_value_sticks_and_clears(self, owned):
        assert chat_store.set_scope_model("project:x", OPUS) == OPUS
        assert chat_store.get_scope_model("project:x") == OPUS
        assert chat_store.set_scope_model("project:x", "") is None
        assert chat_store.get_scope_models() == {}

    def test_scopes_are_kept_apart(self, owned):
        chat_store.set_scope_model("project:x", OPUS)
        chat_store.set_scope_model("app:y", HAIKU)
        assert chat_store.get_scope_models() == {"project:x": OPUS, "app:y": HAIKU}

    def test_an_unknown_model_is_refused_and_nothing_is_written(self, owned):
        with pytest.raises(ValueError):
            chat_store.set_scope_model("project:x", "no-such-model")
        assert chat_store.get_scope_models() == {}

    def test_junk_on_disk_reads_as_nothing(self, owned):
        chat_store._write_pref("scope_models", {"project:x": 3, "": "  "})
        assert chat_store.get_scope_models() == {}


class TestPrecedence:
    def test_the_operator_value_beats_the_declared_one(self, owned):
        chat_store.set_scope_model("project:x", OPUS)
        choice = _resolve(model_resolution.ModelRequest(), scope="project:x")
        assert choice.model_id == OPUS
        assert choice.source == model_resolution.SOURCE_SCOPE

    def test_the_declared_value_answers_without_one(self, owned):
        chat_store.set_default_model(HAIKU)
        choice = _resolve(model_resolution.ModelRequest(scope_model=SONNET))
        assert choice.model_id == SONNET

    def test_the_global_default_stays_below_both(self, owned):
        chat_store.set_default_model(HAIKU)
        both = model_resolution.ModelRequest(operator_scope_model=OPUS,
                                             scope_model=SONNET)
        assert _resolve(both).model_id == OPUS
        assert model_resolution.default_for(None, SONNET) == (
            SONNET, model_resolution.SOURCE_SCOPE)
        assert model_resolution.default_for(None, None) == (
            HAIKU, model_resolution.SOURCE_PRINCIPAL)

    def test_the_global_default_answers_a_scope_with_neither(self, owned):
        chat_store.set_default_model(HAIKU)
        choice = _resolve(model_resolution.ModelRequest(), scope="general")
        assert choice.model_id == HAIKU
        assert choice.source == model_resolution.SOURCE_PRINCIPAL

    def test_a_pin_still_beats_the_operator_scope_value(self, owned):
        chat_store.set_scope_model("general", OPUS)
        choice = _resolve(model_resolution.ModelRequest(pinned_model=HAIKU),
                          scope="general")
        assert choice.model_id == HAIKU

    def test_a_vanished_operator_value_falls_to_the_declared_one(self, owned):
        chat_store._write_pref("scope_models", {"general": "gone-model"})
        request = model_resolution.ModelRequest(
            operator_scope_model="gone-model", scope_model=SONNET)
        choice = _resolve(request)
        assert choice.model_id == SONNET
        assert "gone-model" in choice.notes[0]

    def test_an_agent_turn_keeps_the_chats_settled_model(self, owned):
        chat_store.set_scope_model("general", OPUS)
        choice = _resolve(model_resolution.ModelRequest.for_agent(
            "a", {}, chat_model=HAIKU), scope="general")
        assert choice.model_id == HAIKU

    def test_the_composer_preselects_the_operator_value(self, owned):
        chat_store.set_scope_model("general", OPUS)
        entry = chat_context._provider_entry(chat_context.get_provider("general"))
        assert entry["default_model"] == OPUS


def _local(monkeypatch, *models: tuple[str, int]):
    infos = tuple(llm_contract.ModelInfo(
        id=m, label=m, provider_id=lmp.PROVIDER_ID, wire=llm_contract.WIRE_ANTHROPIC,
        capabilities=llm_contract.Capabilities(context_tokens=w)) for m, w in models)
    monkeypatch.setattr(lmp, "probe", lambda force=False: lmp.Probe(
        llm_contract.Reach(llm_contract.REACH_READY, ""), infos, 0.0))


class TestTheCompactionModelStore:
    def test_it_is_kept_beside_the_server_url(self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304))
        lmp.set_base_url("http://127.0.0.1:9")
        assert lmp.set_compaction_model("big") == "big"
        assert lmp.compaction_model() == "big"
        assert lmp.base_url() == "http://127.0.0.1:9"
        lmp.set_base_url("http://127.0.0.1:10")
        assert lmp.compaction_model() == "big"

    def test_only_a_listed_model_with_a_window_is_taken(self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("blind", 0))
        for bad in ("blind", "elsewhere"):
            with pytest.raises(ValueError):
                lmp.set_compaction_model(bad)
        assert lmp.compaction_model() is None

    def test_none_clears_it(self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304))
        lmp.set_compaction_model("big")
        assert lmp.set_compaction_model("") is None
        assert lmp.compaction_model() is None


class TestCompactionPerScope:
    def test_the_scopes_choice_beats_the_global_one(self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("huge", 131072))
        lmp.set_compaction_model("big")
        lmp.set_scope_compaction_model("app:pics", "huge")
        assert lmp.compaction_model_for("app:pics") == "huge"
        assert lmp.compaction_model_for("app:other") == "big"
        assert lmp.compaction_model_for(None) == "big"

    def test_nothing_set_keeps_the_chats_model(self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304))
        assert lmp.compaction_model_for("app:pics") is None
        assert lmp.compaction_model_for(None) is None

    def test_clearing_a_scope_falls_back_to_the_global_one(
            self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("huge", 131072))
        lmp.set_compaction_model("big")
        lmp.set_scope_compaction_model("app:pics", "huge")
        assert lmp.set_scope_compaction_model("app:pics", "") is None
        assert lmp.compaction_model_for("app:pics") == "big"
        assert lmp.scope_compaction_models() == {}

    def test_only_a_listed_model_with_a_window_is_taken(
            self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("blind", 0))
        for bad in ("blind", "elsewhere"):
            with pytest.raises(ValueError):
                lmp.set_scope_compaction_model("app:pics", bad)
        assert lmp.scope_compaction_models() == {}

    def test_it_is_kept_beside_the_global_one_and_the_url(
            self, owned, monkeypatch):
        _local(monkeypatch, ("big", 98304))
        lmp.set_base_url("http://127.0.0.1:9")
        lmp.set_compaction_model("big")
        lmp.set_scope_compaction_model("app:pics", "big")
        lmp.set_scope_compaction_model("app:pics", "")
        assert lmp.compaction_model() == "big"
        assert lmp.base_url() == "http://127.0.0.1:9"

    def test_junk_on_disk_reads_as_nothing(self, owned):
        lmp._write_config("scope_compaction_models",
                          {"app:a": 3, "": "big", "app:b": "  "})
        assert lmp.scope_compaction_models() == {}
        lmp._write_config("scope_compaction_models", ["app:a"])
        assert lmp.scope_compaction_models() == {}


class _Place:
    def __init__(self, key, label="Place", declared_model=None):
        self.key, self.label, self.declared_model = key, label, declared_model


@pytest.fixture
def client(owned, monkeypatch):
    monkeypatch.setattr(chat_context, "place_providers", lambda: [
        _Place("general"), _Place("project:demo", "Build"),
        _Place("app:pics", "Pictures", SONNET), _Place("agent:helper")])
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/chat")
    return TestClient(app)


class TestTheRoutes:
    def test_the_choices_come_with_the_values(self, client, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("blind", 0))
        got = client.get("/chat/model-defaults").json()
        assert got["default_model"] is None
        assert got["compaction_model"] is None
        assert [m["id"] for m in got["compaction_models"]] == ["big"]
        assert OPUS in [m["id"] for m in got["models"]]
        assert got["scopes"] == [
            {"key": "app:pics", "label": "Pictures", "model": None,
             "fallback_model": SONNET,
             "fallback_source": model_resolution.SOURCE_SCOPE,
             "compaction_model": None, "compaction_fallback": None},
            {"key": "project:demo", "label": "demo (build)", "model": None,
             "fallback_model": got["deployment_model"],
             "fallback_source": model_resolution.SOURCE_DEPLOYMENT,
             "compaction_model": None, "compaction_fallback": None}]

    def test_writing_one_leaves_the_others(self, client, monkeypatch):
        _local(monkeypatch, ("big", 98304))
        assert client.post("/chat/model-defaults",
                           json={"default_model": HAIKU}).status_code == 200
        client.post("/chat/model-defaults",
                    json={"scope_models": {"app:pics": OPUS}})
        got = client.post("/chat/model-defaults",
                          json={"compaction_model": "big"}).json()
        assert got["default_model"] == HAIKU
        assert got["compaction_model"] == "big"
        pics, demo = got["scopes"]
        assert pics["model"] == OPUS
        assert demo["model"] is None
        assert demo["fallback_model"] == HAIKU

    def test_a_scopes_compaction_model_is_written_and_served(
            self, client, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("huge", 131072))
        client.post("/chat/model-defaults", json={"compaction_model": "big"})
        got = client.post("/chat/model-defaults", json={
            "scope_compaction_models": {"app:pics": "huge"}}).json()
        pics, demo = got["scopes"]
        assert (pics["compaction_model"], pics["compaction_fallback"]) \
            == ("huge", "big")
        assert (demo["compaction_model"], demo["compaction_fallback"]) \
            == (None, "big")
        got = client.post("/chat/model-defaults", json={
            "scope_compaction_models": {"app:pics": ""}}).json()
        assert got["scopes"][0]["compaction_model"] is None
        assert got["compaction_model"] == "big"

    def test_a_bad_scope_compaction_model_is_a_400_and_writes_nothing(
            self, client, monkeypatch):
        _local(monkeypatch, ("big", 98304), ("blind", 0))
        for body in ({"scope_compaction_models": {"app:nope": "big"}},
                     {"scope_compaction_models": {"agent:helper": "big"}},
                     {"scope_compaction_models": {"app:pics": "blind"}},
                     {"scope_compaction_models": {"app:pics": "elsewhere"}},
                     {"scope_compaction_models": {"app:pics": "big",
                                                  "project:x": "big"}}):
            assert client.post("/chat/model-defaults", json=body).status_code == 400
        assert lmp.scope_compaction_models() == {}

    def test_an_unknown_scope_or_model_is_a_400_and_writes_nothing(self, client):
        for body in ({"scope_models": {"project:no-such": OPUS}},
                     {"scope_models": {"agent:helper": OPUS}},
                     {"scope_models": {"app:pics": "no-such-model"}},
                     {"default_model": "no-such-model"},
                     {"compaction_model": "no-such-model"}):
            assert client.post("/chat/model-defaults", json=body).status_code == 400
        assert chat_store.get_scope_models() == {}
        assert chat_store.get_default_model() is None
