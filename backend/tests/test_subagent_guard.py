"""The PreToolUse hook that denies a backgrounded chat subagent.

Root cause + the empirical proof it fixes: docs/platform-redesign/
chat-agent-integration-2026-08-18.md and services/subagent_guard.py's own
header. This pins the one decision the hook makes; the wiring into the CLI
argv (services/build_streaming._build_cmd) is covered by
test_permission_channel.py's argv-purity tests, which stay green with this
flag added.
"""
from services.subagent_guard import deny_reason


class TestDenyReason:
    def test_denies_agent_with_background_true(self):
        assert deny_reason("Agent", {"run_in_background": True}) is not None

    def test_denies_task_with_background_true(self):
        assert deny_reason("Task", {"run_in_background": True}) is not None

    def test_allows_agent_with_background_false(self):
        assert deny_reason("Agent", {"run_in_background": False}) is None

    def test_allows_agent_with_background_omitted(self):
        assert deny_reason("Agent", {}) is None

    def test_ignores_unrelated_tools(self):
        assert deny_reason("Bash", {"run_in_background": True}) is None

    def test_tolerates_a_non_dict_input(self):
        assert deny_reason("Agent", None) is None

    def test_denial_names_the_flag_to_retry_without(self):
        reason = deny_reason("Agent", {"run_in_background": True})
        assert "run_in_background" in reason
