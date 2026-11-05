"""What an app's `chat` block declares beyond where its chat lives: how long a
tool call may take, and what its gateway is asked to be told.

Pinned: `mcp.timeout_s` reaches the CLI's per-server `timeout` in
milliseconds, a missing or junk value leaves the spec as it was, and a turn sent
through a gateway asks the CLI for its request-class header and gets enough
retries to wait out a restart of the app, which the failure streak honours.
"""

from __future__ import annotations

from services import app_chat, app_chat_gateway, chat_context, model_failures
from tests.test_app_chat_and_local_harness import SLUG, app_tree  # noqa: F401

LOCAL = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8099"}
THREAD = {"id": "t-1", "scope": f"app:{SLUG}", "title": "x"}


def _specs():
    return chat_context.AppChatProvider(app_chat.declaration(SLUG)).mcp_specs()


def test_a_declared_tool_timeout_reaches_the_http_spec_in_ms(app_tree):
    app_tree({"mcp": {"service": "api", "timeout_s": 900}})
    assert app_chat.declaration(SLUG).tool_timeout_s == 900
    assert _specs()["some-app"]["timeout"] == 900000


def test_no_declared_timeout_leaves_the_cli_default(app_tree):
    app_tree({"mcp": {"service": "api"}})
    assert "timeout" not in _specs()["some-app"]


def test_a_timeout_that_is_not_a_positive_number_is_ignored(app_tree):
    for junk in ("soon", -5, 0, True, None):
        app_tree({"mcp": {"service": "api", "timeout_s": junk}})
        assert app_chat.declaration(SLUG).tool_timeout_s is None
        assert "timeout" not in _specs()["some-app"]


def test_a_gateway_turn_asks_for_the_request_class_header(app_tree):
    app_tree({"gateway": {"service": "api"}})
    env = app_chat_gateway.gateway_env(THREAD, dict(LOCAL), "turn-1")
    assert env["CLAUDE_CODE_GATEWAY_HINT_HEADERS"] == "1"


def test_without_a_gateway_the_hint_is_not_set(app_tree):
    app_tree({"model": "sonnet"})
    env = app_chat_gateway.gateway_env(THREAD, dict(LOCAL), "turn-1")
    assert "CLAUDE_CODE_GATEWAY_HINT_HEADERS" not in env


def _shortest_wait(retries: int) -> float:
    """The CLI's waits before retries 1..n at their shortest (CLI 2.1.287)."""
    return sum(min(32.0, 0.5 * 2 ** (n - 1)) for n in range(1, retries + 1))


def test_the_retry_budget_rides_out_an_app_restart():
    retries = app_chat_gateway.GATEWAY_RETRIES
    assert retries == 6
    assert _shortest_wait(retries) >= app_chat_gateway.RIDE_OUT_S
    assert _shortest_wait(retries - 1) < app_chat_gateway.RIDE_OUT_S
    assert app_chat_gateway.retries_to_ride_out(1e9) == 15


def test_a_gateway_turn_gets_the_budget_and_keeps_a_larger_one(app_tree):
    app_tree({"gateway": {"service": "api"}})
    local = {**LOCAL, "CLAUDE_CODE_MAX_RETRIES": "2"}
    env = app_chat_gateway.gateway_env(THREAD, local, "turn-1")
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "6"
    env = app_chat_gateway.gateway_env(
        THREAD, {**LOCAL, "CLAUDE_CODE_MAX_RETRIES": "9"}, "turn-1")
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "9"


def test_a_turn_without_a_gateway_keeps_its_retries(app_tree):
    app_tree({"model": "sonnet"})
    local = {**LOCAL, "CLAUDE_CODE_MAX_RETRIES": "2"}
    env = app_chat_gateway.gateway_env(THREAD, local, "turn-1")
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "2"
    assert env["ANTHROPIC_BASE_URL"] == local["ANTHROPIC_BASE_URL"]


def test_the_failure_streak_waits_out_the_clis_own_retries():
    assert model_failures.limit_for(None) == model_failures.MAX_IN_A_ROW
    assert model_failures.limit_for("2") == model_failures.MAX_IN_A_ROW
    limit = model_failures.limit_for(str(app_chat_gateway.GATEWAY_RETRIES))
    assert limit == app_chat_gateway.GATEWAY_RETRIES + 1
    streak = model_failures.Streak(limit=limit)
    for _ in range(app_chat_gateway.GATEWAY_RETRIES):
        streak.failed({"error": "connection refused"}, 0.0)
    assert streak.verdict(1.0) is None
    streak.failed({"error": "connection refused"}, 0.0)
    assert streak.verdict(1.0)
