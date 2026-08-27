"""An app persona cannot declare its way to a shell.

`agents/apps/<key>.md` is ordinary markdown — edited by agents as well as by
people — and its `tools:` list was passed through to the turn verbatim
(`app_persona.AppPersona.__init__` -> `allowed_tools`). App assistants have no
working directory of their own: `routes/chat.py` runs them at the shared
non-build fallback (`WORKSHOP_CHAT_CWD`, or the account's home when unset), the
same fallback `general` ran at when it could read and edit the platform's own
source. So `Bash` in one of these files is an unscoped shell in the operator's
home, and nothing but the current contents of the directory said otherwise.

See docs/masini/security-audit-2026-08-11-scope-separation.md section 4.
"""
from __future__ import annotations

import pytest

from services import agents_registry


@pytest.fixture
def persona_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(agents_registry, "APP_AGENTS_DIR", tmp_path)
    return tmp_path


def _write(d, key: str, tools: list[str]) -> None:
    lines = ["---", "name: Test", "purpose: t", "tools:"]
    lines += [f"  - {t}" for t in tools] or ["  []"]
    lines += ["mcp_servers: []", "---", "", "You are a test assistant."]
    (d / f"{key}.md").write_text("\n".join(lines), encoding="utf-8")


class TestCeiling:
    def test_filesystem_and_exec_tools_are_dropped(self, persona_dir):
        _write(persona_dir, "rogue",
               ["Bash", "Write", "Edit", "Read", "mcp__plaid__get_balances"])
        assert agents_registry.app_persona("rogue")["tools"] == [
            "mcp__plaid__get_balances"]

    def test_a_rogue_declaration_does_not_take_the_app_down(self, persona_dir):
        # Dropped, logged, app still loads: one bad line must not 500 the app.
        _write(persona_dir, "rogue", ["Bash"])
        d = agents_registry.app_persona("rogue")
        assert d["tools"] == [] and d["persona"]

    def test_the_delegation_tool_is_capped_too(self, persona_dir):
        # Task/Agent spawn a subagent that inherits the full default toolset —
        # a ceiling that stops Bash but allows Task stops nothing.
        _write(persona_dir, "rogue", ["Task", "Agent"])
        assert agents_registry.app_persona("rogue")["tools"] == []

    def test_mcp_tools_pass_through_untouched(self, persona_dir):
        tools = ["mcp__plaid__search_transactions", "mcp__plaid__list_transactions"]
        _write(persona_dir, "budgetish", tools)
        assert agents_registry.app_persona("budgetish")["tools"] == tools


class TestShippedPersonasAreUnaffected:
    def test_every_real_app_persona_survives_the_ceiling(self):
        # The ceiling is a guard against future edits, not a change to today's
        # behavior: if it alters a shipped persona, that persona was already
        # holding something it should not.
        for key in agents_registry.app_persona_keys():
            d = agents_registry.app_persona(key)
            assert all(t.startswith("mcp__") for t in d["tools"]), (
                f"app persona {key!r} declares a non-MCP tool")
