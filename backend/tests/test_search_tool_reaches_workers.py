"""The search tool actually reaches the three surfaces that were missing it.

`mcp__workshop__search` existed and only the general chat could call it. The
@mentioned agent was granted no MCP tool and was passed `mcp_servers=[]`
literally; the unattended worker (`scripts/ralph.sh`) spawned `claude -p` with no
MCP flags at all. Both surfaces "worked" the whole time, because a missing search
tool produces no error anywhere: the model simply never searches.

That is the reason these are structural assertions rather than a smoke test. A
smoke test of any of this passes while it is broken.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from services import agent_authority, llm_router

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent


class TestTheRoleDerivationGrantsIt:
    def test_a_role_that_writes_something_can_search(self):
        for role in ("maker", "reviewer", "triager", "keeper"):
            assert agent_authority.SEARCH_TOOL in agent_authority.tools_for_role(role), role

    def test_broker_cannot(self):
        """The one role defined by NOT reading the data it adjudicates. An earlier
        draft of the spec said "all five roles", which would have handed exactly
        this role a search tool over the whole corpus."""
        assert agent_authority.tools_for_role("broker") == []

    def test_an_unroled_agent_cannot(self):
        assert agent_authority.tools_for_role(None) == []
        assert agent_authority.tools_for_role("") == []
        assert agent_authority.tools_for_role("not-a-role") == []

    def test_a_definition_may_still_narrow_it_away(self):
        """`tools:` on a definition narrows, never widens. An agent that asks for
        Read only does not get search back."""
        assert agent_authority.narrow_tools("maker", ["Read"]) == ["Read"]

    def test_a_definition_cannot_widen_into_it(self):
        assert agent_authority.narrow_tools("broker", [agent_authority.SEARCH_TOOL]) == []


class TestTheServerIsSpawnedWithTheTool:
    """A granted tool whose server is absent is filtered straight back out by
    `llm_router.tool_flags`, so the grant and the spawn have to agree."""

    def test_the_servers_are_derived_from_the_tool_names(self):
        assert agent_authority.mcp_servers_for(
            ["Read", agent_authority.SEARCH_TOOL]) == ["workshop"]

    def test_a_toolset_with_no_mcp_tool_needs_no_server(self):
        assert agent_authority.mcp_servers_for(["Read", "Bash"]) == []
        assert agent_authority.mcp_servers_for(None) == []

    def test_every_role_that_gets_the_tool_also_gets_its_server(self):
        for role in ("maker", "reviewer", "triager", "keeper", "broker", None):
            tools = agent_authority.tools_for_role(role)
            want = "workshop" in agent_authority.mcp_servers_for(tools)
            assert want == (agent_authority.SEARCH_TOOL in tools), role

    def test_the_mention_path_no_longer_passes_an_empty_server_list(self):
        """Pinned structurally: `mcp_servers=[]` was a literal at both spawn
        sites, which is what made the grant unusable."""
        src = (BACKEND / "routes" / "chat.py").read_text()
        assert "mcp_servers=[], tools_disabled=not tools_active" not in src
        assert src.count("mcp_servers=agent_mcp, tools_disabled=not tools_active") == 2
        assert "agent_authority.mcp_servers_for(agent_tools)" in src


class TestTheFlagsTheCliActuallyGets:
    def test_a_granted_search_tool_survives_into_the_allowlist(self, monkeypatch):
        spec = {"type": "stdio", "command": "python", "args": ["s.py"]}
        monkeypatch.setattr(llm_router, "_mcp_server_specs", lambda: {"workshop": spec})
        tools = agent_authority.tools_for_role("reviewer")
        flags, env, block = llm_router.tool_flags(
            allowed_tools=tools,
            mcp_servers=agent_authority.mcp_servers_for(tools),
        )
        assert agent_authority.SEARCH_TOOL in flags
        cfg = json.loads(flags[flags.index("--mcp-config") + 1])
        assert "workshop" in cfg["mcpServers"]
        # The handshake has to complete before the first model turn or the model
        # is told it has no tools.
        assert block is True
        assert env.get("MCP_CONNECTION_NONBLOCKING") == "false"

    def test_without_the_server_the_tool_is_stripped_out_again(self, monkeypatch):
        """The failure this replaces, asserted directly: the same allowlist with
        `mcp_servers=[]` names a tool that cannot exist."""
        spec = {"type": "stdio", "command": "python", "args": ["s.py"]}
        monkeypatch.setattr(llm_router, "_mcp_server_specs", lambda: {"workshop": spec})
        tools = agent_authority.tools_for_role("reviewer")
        flags, _env, block = llm_router.tool_flags(allowed_tools=tools, mcp_servers=[])
        cfg = json.loads(flags[flags.index("--mcp-config") + 1])
        assert cfg["mcpServers"] == {}
        assert block is False

    def test_a_chat_that_deselected_the_server_loses_the_tool(self, monkeypatch):
        """The per-thread MCP picker still governs. Search is not exempt from it."""
        spec = {"type": "stdio", "command": "python", "args": ["s.py"]}
        monkeypatch.setattr(llm_router, "_mcp_server_specs", lambda: {"workshop": spec})
        tools = agent_authority.tools_for_role("maker")
        flags, _env, _b = llm_router.tool_flags(
            allowed_tools=tools, mcp_servers=["workshop"], mcp_selection=[])
        assert agent_authority.SEARCH_TOOL not in flags
        assert "Bash" in flags


class TestTheEmitterPinsTheOwner:
    """THE WAY THIS SHIPS BROKEN AND PASSES A SMOKE TEST.

    The workshop MCP server is owner-scoped: the semantic index, the chat logs
    and the knowledge root all resolve under `.runtime/users/<owner>/`. A worker
    with no owner pinned searches an EMPTY index, which is not an error — the
    tool answers "nothing has been written about it" and the run succeeds.
    """

    def test_the_owner_rides_in_the_spec_env(self):
        cfg = json.loads(llm_router.mcp_config_json(["workshop"], owner_id=4242))
        env = cfg["mcpServers"]["workshop"]["env"]
        assert env["WORKSHOP_QUERY_OWNER"] == "4242"

    def test_it_defaults_to_the_operator_not_to_nothing(self):
        from services import auth_store
        cfg = json.loads(llm_router.mcp_config_json(["workshop"]))
        env = cfg["mcpServers"]["workshop"]["env"]
        assert env.get("WORKSHOP_QUERY_OWNER") == str(auth_store.operator_id())

    def test_the_spec_can_actually_be_launched(self):
        cfg = json.loads(llm_router.mcp_config_json(["workshop"]))
        spec = cfg["mcpServers"]["workshop"]
        assert Path(spec["command"]).exists(), spec["command"]
        assert Path(spec["args"][0]).exists(), spec["args"][0]
        assert spec["env"]["PYTHONPATH"] == str(BACKEND)

    def test_an_unknown_server_raises_rather_than_emitting_an_empty_config(self):
        """Silence here is the whole hazard: an empty config spawns a worker with
        no search and no complaint."""
        with pytest.raises(KeyError):
            llm_router.mcp_config_json(["no-such-server"])

    def test_the_cli_prints_a_path_to_a_real_config(self, tmp_path):
        out = subprocess.run(
            [sys.executable, "-m", "services.llm_router",
             "mcp-config", "--server", "workshop"],
            cwd=str(BACKEND), capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr
        path = Path(out.stdout.strip())
        try:
            assert path.is_file(), out.stdout
            cfg = json.loads(path.read_text())
            assert "workshop" in cfg["mcpServers"]
            assert cfg["mcpServers"]["workshop"]["env"]["WORKSHOP_QUERY_OWNER"]
        finally:
            path.unlink(missing_ok=True)


class TestRalphPassesIt:
    """`scripts/ralph.sh` spawns `claude -p` itself, so the flag has to be in the
    script. Read as text: there is no way to assert on an argv the loop builds
    without running a real sweep."""

    SRC = (ROOT / "scripts" / "ralph.sh").read_text()

    def test_it_asks_python_for_the_config(self):
        assert "services.llm_router \\\n  mcp-config --server workshop" in self.SRC \
            or "mcp-config --server workshop" in self.SRC

    def test_the_flag_reaches_the_claude_invocation(self):
        assert '"${MCP_FLAGS[@]}"' in self.SRC
        # ... in the actual spawn, not only in the assignment.
        spawn = self.SRC[self.SRC.index('timeout "$PER_ITER_TIMEOUT"'):][:600]
        assert '"${MCP_FLAGS[@]}"' in spawn

    def test_it_is_additive_and_not_strict(self):
        """The worker is full-agentic by design. Strict mode would silently drop
        every other server the tree legitimately configures. Comment lines are
        skipped: the script SAYS why it is not strict, which is not the same as
        being strict."""
        code = [ln for ln in self.SRC.splitlines() if not ln.lstrip().startswith("#")]
        assert not [ln for ln in code if "--strict-mcp-config" in ln]

    def test_a_failed_emit_leaves_the_run_unflagged_rather_than_broken(self):
        assert "MCP_FLAGS=()" in self.SRC
        assert "NOT APPLIED (llm_router emitted no mcp-config)" in self.SRC

    def test_the_temp_config_is_cleaned_up(self):
        assert re.search(r"trap\s+'rm -f \"\$MCP_CFG\"'\s+EXIT", self.SRC)

    def test_the_prompt_only_describes_the_tool_when_it_is_there(self):
        """A prompt describing a tool the CLI was never given costs a turn and
        teaches the model to distrust the rest of the prompt."""
        enabled = self.SRC.index('log "workshop search tool: enabled')
        disabled = self.SRC.index('log "workshop search tool: NOT APPLIED')
        described = self.SRC.index("mcp__workshop__search", enabled)
        assert enabled < described < disabled
        assert "The workshop's own memory" in self.SRC[enabled:disabled]
