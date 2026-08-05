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
from services import app_personas as ap
from services import llm_router as _llm


PROVIDERS = [
    ap.BudgetContextProvider,
    ap.FinanceContextProvider,
    ap.CommuteContextProvider,
    ap.LocationContextProvider,
]


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
        assert p.allowed_tools == d["tools"]
        assert p.mcp_servers == d["mcp_servers"]

    def test_no_orphan_definitions(self):
        """A file with no provider is an assistant nobody can reach."""
        assert set(reg.app_persona_keys()) == {c.key for c in PROVIDERS}


class TestDeclarationsSayWhatTheyUsedTo:
    """The migration must not have changed any assistant's capabilities."""

    def test_budget_keeps_its_narrow_plaid_pair(self):
        p = ap.BudgetContextProvider()
        assert p.allowed_tools == [
            "mcp__plaid__search_transactions",
            "mcp__plaid__list_transactions",
        ]
        assert p.mcp_servers == ["plaid"]
        assert p.default_model == _llm.TIER_SONNET

    def test_budget_still_carries_the_patch_protocol(self):
        assert "plan-patch" in ap.BudgetContextProvider().persona()

    def test_finance_reads_investments_too(self):
        p = ap.FinanceContextProvider()
        assert "mcp__plaid__get_holdings" in p.allowed_tools
        assert "mcp__plaid__list_investment_transactions" in p.allowed_tools

    def test_the_plaid_lookup_method_survived_the_move(self):
        """It was a shared Python constant appended to two personas; it is now
        written into both files, and losing it from either would silently
        degrade transaction attribution."""
        for cls in (ap.BudgetContextProvider, ap.FinanceContextProvider):
            body = cls().persona()
            assert "TRANSFER ATTRIBUTION" in body
            assert "CUMULATIVE LOOKBACK" in body

    @pytest.mark.parametrize("cls", [ap.CommuteContextProvider, ap.LocationContextProvider],
                             ids=lambda c: c.key)
    def test_read_only_assistants_get_no_tools(self, cls):
        p = cls()
        assert p.allowed_tools == []
        assert p.mcp_servers == []


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
