"""An app assistant is what its definition file declares.

App personas were one of the ways an agent got defined in this system without
using the agent format: a ContextProvider subclass with the system prompt as a
concatenated string literal, the tool list as a class attribute, and the model as
a constant. These tests pin the migration — that the declaration is now the file,
that the provider reads it, and that a missing file FAILS instead of quietly
producing an assistant with no instructions and no tools that still answers.
"""
import pytest

from services import agents_registry as reg
from services import app_registry
from services import llm_router as _llm


# DISCOVERED, not listed. These four classes used to live in one platform module
# (`services/app_personas.py`) beside 520 lines of the same four apps' business
# logic, and this file imported them by name. They are each beside their own app
# now and reach the chat surface through `AppSpec.chat_provider`, so the honest
# way to ask "is every app assistant declared" is to ask the catalog -- which
# also means a new app is covered the day it is added, rather than the day
# someone remembers to add a line here.
PROVIDERS = [f for f in app_registry.installed_chat_providers()]


class TestEveryProviderIsDeclared:
    @pytest.mark.parametrize("cls", PROVIDERS, ids=lambda c: c.key)
    def test_provider_has_a_definition_file(self, cls):
        d = reg.app_persona(cls.key)
        assert d["persona"].strip()
        assert d["name"]

    @pytest.mark.parametrize("cls", PROVIDERS, ids=lambda c: c.key)
    def test_provider_reads_its_declaration(self, cls):
        d = reg.app_persona(cls.key)
        p = cls()
        assert p.persona() == d["persona"]
        assert p.label == d["name"]
        # Every declared tool reaches the assistant. It is a SUPERSET, not an
        # equality: the base class adds the workshop toolkit (orientation, and
        # reach to the chat's own uploads) on top of the declaration, so no app
        # assistant arrives unable to see what it has access to.
        assert set(d["tools"]) <= set(p.allowed_tools)
        assert set(d["mcp_servers"]) <= set(p.mcp_servers)
        assert "workshop" in p.mcp_servers

    def test_no_orphan_definitions(self):
        """A file with no provider is an assistant nobody can reach."""
        assert set(reg.app_persona_keys()) == {c.key for c in PROVIDERS}


# The per-app capability assertions that used to sit here -- "this assistant
# still declares exactly these two integration tools", "this marker is still in
# that persona's text", "this one still resolves to that model tier" -- were
# always APP tests, and said so in their own docstring: they named four apps and
# were the only thing in this file that named any. They left with their apps on
# 2026-09-04 and are each app's `tests/test_persona_declaration.py` now. What
# stays here is the contract every app assistant must satisfy, asserted over
# whatever the catalog discovered.


class TestFailureIsLoud:
    def test_a_missing_definition_raises(self):
        with pytest.raises(reg.AppPersonaMissing):
            reg.app_persona("no-such-app")

    def test_an_empty_persona_raises(self, tmp_path, monkeypatch):
        (tmp_path / "hollow.md").write_text("---\nname: Hollow\n---\n\n")
        monkeypatch.setattr(reg, "APP_AGENTS_DIR", tmp_path)
        with pytest.raises(reg.AppPersonaMissing):
            reg.app_persona("hollow")


class TestModelTier:
    def test_short_names_resolve(self):
        assert _llm.tier("sonnet") == _llm.TIER_SONNET
        assert _llm.tier("opus-5") == _llm.TIER_OPUS_5

    def test_a_full_handle_passes_through(self):
        assert _llm.tier("claude-opus-5") == "claude-opus-5"

    def test_unknown_and_absent_fall_back(self):
        """A typo in a declaration must not reach the CLI as a model name."""
        assert _llm.tier("sonnnet", _llm.TIER_HAIKU) == _llm.TIER_HAIKU
        assert _llm.tier(None, _llm.TIER_HAIKU) == _llm.TIER_HAIKU
        assert _llm.tier("", _llm.TIER_HAIKU) == _llm.TIER_HAIKU


class TestAppAgentsAreNotRosterAgents:
    def test_the_roster_does_not_glob_the_apps_dir(self):
        """`agents/apps/` sits under the agents dir; the roster's glob is
        non-recursive, and an app assistant appearing as a standing agent with
        triggers would be exactly the identity confusion this removes."""
        slugs = {r["slug"] for r in reg.list_agents(include_retired=True)}
        assert not (slugs & set(reg.app_persona_keys()))
