"""Fields an agent definition declares, and whether anything reads them.

The rule this file exists to hold: a field the operator can set is either
honored or it is not offered. An agent record used to carry six declared fields
that changed nothing — `tools`, `model`, `capabilities`, `limits`, `depth`,
`fanout` — while the UI and the prompts described them as if they did.

`tools` and `model` are now parsed and used. `capabilities` is declaration only
and is no longer asserted to the model. The tests below pin both halves,
including the ones that pin a REMOVAL: a claim that was deleted has to stay
deleted, or it grows back the next time someone reads the frontmatter and
assumes it must mean something.
"""
import pytest

from services import agent_authority as aa
from services import agents_registry as reg


def _write(d, slug, fm_lines, body="You are a probe."):
    (d / f"{slug}.md").write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n")


@pytest.fixture
def rec_for(tmp_path):
    def build(fm_lines, slug="probe"):
        _write(tmp_path, slug, fm_lines)
        return reg._record_from_def(tmp_path / f"{slug}.md", "workshop")
    return build


class TestDeclaredTools:
    """`narrow_tools(role, rec["tools"])` was called at two sites, both
    commented as the way a definition asks for less than its role allows. The
    record never had a `tools` key, so the argument was always None and the
    function always returned the role's FULL set. The feature was described in
    prose and absent from the parser."""

    def test_a_declared_tool_list_is_parsed(self, rec_for):
        rec = rec_for(["name: probe", "role: maker", "tools: ['Read', 'Grep']"])
        assert rec["tools"] == ["Read", "Grep"]

    def test_a_declared_list_actually_narrows_the_role(self, rec_for):
        rec = rec_for(["name: probe", "role: maker", "tools: ['Read', 'Grep']"])
        got = aa.narrow_tools(rec["role"], rec["tools"])
        assert got == ["Read", "Grep"]
        for widened in ("Write", "Edit", "Bash"):
            assert widened not in got

    def test_no_declaration_means_the_full_role_set(self, rec_for):
        """None and [] are different, and collapsing them would disarm every
        agent that simply did not mention tools."""
        rec = rec_for(["name: probe", "role: maker"])
        assert rec["tools"] is None
        assert aa.narrow_tools(rec["role"], rec["tools"]) == aa.tools_for_role("maker")

    def test_an_empty_declaration_is_a_request_for_nothing(self, rec_for):
        rec = rec_for(["name: probe", "role: maker", "tools: []"])
        assert rec["tools"] == []
        assert aa.narrow_tools(rec["role"], rec["tools"]) == aa.tools_for_role("maker")

    def test_a_declaration_can_never_widen_the_role(self, rec_for):
        """A keeper asking for Bash is a mistake in the definition, reduced
        rather than honored."""
        rec = rec_for(["name: probe", "role: keeper", "tools: ['Read', 'Bash', 'Edit']"])
        assert aa.narrow_tools(rec["role"], rec["tools"]) == ["Read"]

    def test_junk_is_ignored_rather_than_half_read(self, rec_for):
        rec = rec_for(["name: probe", "role: maker", "tools: not-a-list"])
        assert rec["tools"] is None


class TestDeclaredModel:
    """An @mentioned agent ran on whatever the composer's dropdown was set to."""

    def test_a_declared_model_is_parsed(self, rec_for):
        rec = rec_for(["name: probe", "role: maker", "model: haiku"])
        assert rec["model"] == "haiku"

    def test_no_declaration_means_inherit(self, rec_for):
        assert rec_for(["name: probe", "role: maker"])["model"] is None

    def test_a_blank_declaration_means_inherit(self, rec_for):
        assert rec_for(["name: probe", 'model: "  "'])["model"] is None

    def test_a_declared_tier_resolves_to_a_pickable_model(self, rec_for):
        """What the mention path does with it: a tier name has to survive
        normalization, or the field is parsed and then discarded."""
        from services import chat_context

        rec = rec_for(["name: probe", "model: haiku"])
        assert chat_context.normalize_model(rec["model"]) in chat_context.pickable_models()


class TestCapabilitiesAreDeclarationOnly:
    """REMOVAL test. `capabilities` was rendered into the agent's own preamble
    under a header reading "this is the enforced truth", while the caller passed
    `mcp_servers=[]` hardcoded. A bridge agent declares discord, plaid and google-docs;
    only `plaid` is an installed workshop MCP server at all. The agent was being
    told it had three things it had no tool for."""

    def test_capabilities_are_still_read_as_description(self, rec_for):
        rec = rec_for(["name: probe", "capabilities: ['discord', 'plaid']"])
        assert rec["capabilities"] == ["discord", "plaid"]

    def test_they_are_not_a_parameter_of_the_agents_self_preamble(self):
        import inspect

        from services import chat_context

        params = inspect.signature(chat_context.agent_self_preamble).parameters
        assert "capabilities" not in params

    def test_nothing_turns_a_capability_into_an_mcp_grant(self):
        """If this ever becomes possible, the name has to match a real server.
        Two of those three do not exist as servers, which is why the honest
        move was to stop asserting them rather than to wire them up."""
        from services import app_registry

        servers = set(app_registry.installed_mcp_servers())
        assert "discord" not in servers
        assert "google-docs" not in servers
