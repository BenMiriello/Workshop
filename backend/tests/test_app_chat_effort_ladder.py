"""An app gateway's declared effort ladder, per scope.

Pinned: in the app's scope, on a model the gateway serves, the declared levels
are the ladder the composer is offered and the level a turn is resolved to,
and a turn there never launches without `--effort`: the declared
`effort_default`, else the ladder's middle rung, when nothing was picked
(the CLI otherwise sends a level of its own). Another scope, a model the CLI
serves itself, and an app without the declaration keep the model's own ladder.
"""

from __future__ import annotations

import pytest

from services import (app_chat, app_chat_gateway, build_streaming, chat_context, chat_store,
                      llm_router, model_caps, model_resolution)
from tests.test_app_chat_and_local_harness import (  # noqa: F401
    SLUG, app_tree, harnessed)

LOCAL = "a-harnessed-weight"
SCOPE = f"app:{SLUG}"


@pytest.fixture()
def declared(app_tree, harnessed):
    app_tree({"gateway": {"service": "api",
                          "efforts": ["High", "low", "medium", "ludicrous"]}})


def _resolve(scope, **kw):
    return model_resolution.resolve_model(
        1, scope, model_resolution.ModelRequest(model=kw.pop("model", LOCAL), **kw))


def test_the_declaration_is_read_as_written(declared):
    assert app_chat_gateway.declared_efforts(SCOPE) == (
        ("high", "low", "medium", "ludicrous"), None)
    assert app_chat_gateway.declared_efforts("general") == ((), None)


def test_the_scope_ladder_is_spellable_levels_in_order(declared):
    assert model_caps.efforts_for(LOCAL, SCOPE) == ("low", "medium", "high")
    assert model_caps.efforts_for(LOCAL) == ()
    assert model_caps.efforts_for(LOCAL, "general") == ()


def test_a_model_the_cli_serves_keeps_its_own_ladder(declared):
    assert model_caps.efforts_for(llm_router.TIER_HAIKU, SCOPE) == ()
    assert model_caps.efforts_for(llm_router.TIER_SONNET, SCOPE) == \
        model_caps.EFFORT_BY_MODEL[llm_router.TIER_SONNET]


def test_without_the_declaration_nothing_changes(app_tree, harnessed):
    app_tree({"gateway": {"service": "api"}})
    assert model_caps.efforts_for(LOCAL, SCOPE) == ()
    assert _resolve(SCOPE).effort is None


def test_a_gateway_less_app_does_not_offer_a_ladder(app_tree, harnessed):
    app_tree({"model": "sonnet", "efforts": ["low"]})
    assert model_caps.efforts_for(LOCAL, SCOPE) == ()


def test_the_composer_is_offered_the_scope_ladder(declared, monkeypatch):
    monkeypatch.setattr(chat_context, "pickable_models", lambda: [LOCAL])
    entry = chat_context._provider_entry(
        chat_context.AppChatProvider(app_chat.declaration(SLUG)))
    assert entry["model_caps"][LOCAL]["efforts"] == {
        "levels": ["low", "medium", "high"], "default": "medium"}


def test_a_picked_level_on_the_ladder_is_sent(declared):
    assert _resolve(SCOPE, effort="low").effort == "low"


def test_a_level_off_the_ladder_falls_to_the_ladders_default(declared):
    assert _resolve(SCOPE, effort="max").effort == "medium"


def test_nothing_picked_sends_the_middle_rung_explicitly(declared):
    assert _resolve(SCOPE).effort == "medium"
    assert _resolve("general").effort is None


def test_a_declared_default_wins_over_the_middle_rung(app_tree, harnessed):
    app_tree({"gateway": {"service": "api", "efforts": ["low", "medium", "high"],
                          "effort_default": "Low"}})
    assert _resolve(SCOPE).effort == "low"
    assert model_caps.caps_for(LOCAL, None, SCOPE)["efforts"]["default"] == "low"


def test_a_declared_default_off_the_ladder_is_ignored(app_tree, harnessed):
    app_tree({"gateway": {"service": "api", "efforts": ["low", "high"],
                          "effort_default": "max"}})
    assert _resolve(SCOPE).effort == "high"


def test_the_operators_own_default_still_wins_when_on_the_ladder(
        declared, monkeypatch):
    monkeypatch.setattr(chat_store, "get_default_effort", lambda: "low")
    assert _resolve(SCOPE).effort == "low"
    assert model_caps.caps_for(LOCAL, "low", SCOPE)["efforts"]["default"] == "low"


def test_a_declared_ladder_scope_never_launches_without_effort(
        declared, monkeypatch):
    monkeypatch.setattr(chat_store, "get_thread",
                        lambda tid: {"id": tid, "context_key": SCOPE})
    meta = {"turn_id": "t1", "thread_id": "th", "model": LOCAL,
            "session_id": None, "resume": False, "cwd": "/tmp"}
    for effort in (None, "", "low"):
        cmd, _ = build_streaming._build_cmd({**meta, "effort": effort})
        assert cmd[cmd.index("--effort") + 1] == (effort or "medium")
    monkeypatch.setattr(chat_store, "get_thread",
                        lambda tid: {"id": tid, "context_key": "general"})
    cmd, _ = build_streaming._build_cmd(meta)
    assert "--effort" not in cmd
