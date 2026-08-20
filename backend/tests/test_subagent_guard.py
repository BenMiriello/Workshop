"""The PreToolUse hook that forces a chat subagent into the foreground.

Root cause + the empirical proof it fixes: docs/platform-redesign/
chat-agent-integration-2026-08-18.md and services/subagent_guard.py's own
header. This pins the one decision the hook makes; the wiring into the CLI
argv (services/build_streaming._build_cmd) is covered by
test_permission_channel.py's argv-purity tests, which stay green with this
flag added.

The load-bearing case is `test_denies_agent_with_background_omitted`: this CLI
backgrounds a subagent by default, so an omitted flag is the failure, not the
safe case. The first version of the guard allowed an omitted flag and that is
exactly how turn b35b74f5 (2026-08-20) launched two async mappers and died
waiting for them.
"""
from services.subagent_guard import deny_reason


class TestDenyReason:
    def test_denies_agent_with_background_true(self):
        assert deny_reason("Agent", {"run_in_background": True}) is not None

    def test_denies_task_with_background_true(self):
        assert deny_reason("Task", {"run_in_background": True}) is not None

    def test_denies_agent_with_background_omitted(self):
        # The real bug: omitted means BACKGROUND in this CLI, so it must deny.
        assert deny_reason("Agent", {}) is not None

    def test_denies_task_with_background_omitted(self):
        assert deny_reason("Task", {"prompt": "do a thing"}) is not None

    def test_denies_agent_with_background_none(self):
        assert deny_reason("Agent", {"run_in_background": None}) is not None

    def test_allows_agent_with_background_false(self):
        assert deny_reason("Agent", {"run_in_background": False}) is None

    def test_allows_task_with_background_false(self):
        assert deny_reason("Task", {"run_in_background": False}) is None

    def test_allows_agent_with_background_false_string(self):
        # A caller that stringifies the flag still reads as foreground.
        assert deny_reason("Agent", {"run_in_background": "false"}) is None

    def test_ignores_unrelated_tools(self):
        assert deny_reason("Bash", {"run_in_background": True}) is None
        assert deny_reason("Bash", {}) is None

    def test_tolerates_a_non_dict_input(self):
        assert deny_reason("Agent", None) is None

    def test_denial_names_the_flag_to_set_false(self):
        reason = deny_reason("Agent", {})
        assert "run_in_background" in reason
        assert "false" in reason
