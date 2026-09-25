"""Which rules files a scope hands its agent as "Project-local agent rules".

The tracked file (AGENTS.md first, then its aliases) is the repo's; the local
file (AGENTS.local.md or CLAUDE.local.md) is one installation's and untracked.
Both reach the agent, local after tracked and marked as such.
"""
from __future__ import annotations

from services import chat_context as cc


def _reader(files: dict[str, str]):
    return lambda name: files.get(name, "")


def test_the_first_nonempty_tracked_file_wins():
    rules = cc._agent_rules(_reader({"AGENTS.md": "  ", "CLAUDE.md": "use tabs"}))
    assert rules == "use tabs"


def test_a_local_file_is_appended_after_the_tracked_rules():
    rules = cc._agent_rules(_reader({
        "AGENTS.md": "repo rule", "CLAUDE.local.md": "this box's rule"}))
    assert rules.index("repo rule") < rules.index(cc._LOCAL_RULES_HEADER)
    assert rules.index(cc._LOCAL_RULES_HEADER) < rules.index("this box's rule")


def test_agents_local_is_preferred_over_claude_local():
    rules = cc._agent_rules(_reader({
        "AGENTS.local.md": "agents local", "CLAUDE.local.md": "claude local"}))
    assert "agents local" in rules
    assert "claude local" not in rules


def test_a_local_file_alone_still_reaches_the_agent():
    rules = cc._agent_rules(_reader({"CLAUDE.local.md": "only local"}))
    assert rules.startswith(cc._LOCAL_RULES_HEADER)
    assert "only local" in rules


def test_no_local_file_leaves_the_tracked_rules_unchanged():
    assert cc._agent_rules(_reader({"AGENTS.md": "repo rule"})) == "repo rule"
    assert cc._agent_rules(_reader({})) == ""


def test_the_project_persona_carries_both(tmp_path):
    (tmp_path / "AGENTS.md").write_text("repo rule\n")
    (tmp_path / "CLAUDE.local.md").write_text("this box's rule\n")
    provider = object.__new__(cc.ProjectContextProvider)
    provider.slug = "example"
    provider.key = "project:example"
    provider.dir = tmp_path
    provider.is_self = False
    text = provider.persona()
    assert "repo rule" in text
    assert cc._LOCAL_RULES_HEADER in text
    assert "this box's rule" in text
