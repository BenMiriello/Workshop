"""A build prompt names the directory the AGENT is in, not the one the backend is.

Measured 2026-09-16: a project agent told the operator it was working "on the
host at <abs path>" and carried on in host paths. It did not discover them. Two
places in `chat_context` handed them over -- `instruction_contract` passed
`provider.dir` into the capabilities block, and `_task_cli` built a command from
`paths.workshop_root()` -- while `services/scoped_exec` guarantees the opposite:
a turn is confined to its scope, bind-mounted at MOUNTPOINT, and "the real
/home/... path does not exist inside the sandbox for the scope, so it cannot be
echoed".

`scripts/check_prompt_layers.py` is the gate for this and it renders the whole
prompt; these are the two specific claims, asserted directly, so a revert is
caught by a one-second test as well as by the review gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services import chat_context, scoped_exec


@pytest.fixture
def confined(monkeypatch):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")


@pytest.fixture
def unconfined(monkeypatch):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")


class TestWhereTheAgentIsToldItIs:
    def test_a_confined_build_turn_is_told_the_mountpoint(self, confined):
        assert chat_context.agent_cwd("/somewhere/else/project") == scoped_exec.MOUNTPOINT

    def test_a_turn_rendered_with_confinement_off_is_still_told_the_mountpoint(
            self, unconfined):
        # spawn_argv refuses a turn it cannot confine, so there is no
        # unconfined turn whose prompt could truthfully name the host path.
        assert chat_context.agent_cwd("/somewhere/else/project") == scoped_exec.MOUNTPOINT


def _project_provider(host_dir):
    provider = object.__new__(chat_context.ProjectContextProvider)
    provider.slug = "example"
    provider.key = "project:example"
    provider.dir = host_dir
    provider.is_self = False
    return provider


class TestTheWholeContractNamesOnlyTheMountpoint:
    @pytest.mark.parametrize("flag", ["1", "0"])
    def test_a_build_contract_names_the_mountpoint_not_the_host_dir(
            self, monkeypatch, tmp_path, flag):
        monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", flag)
        host = tmp_path / "host-side" / "example"
        host.mkdir(parents=True)
        text = chat_context.instruction_contract(
            _project_provider(host), enable_chat_tools=True)
        assert f"operating in {scoped_exec.MOUNTPOINT}" in text
        assert str(host) not in text
        assert str(tmp_path) not in text

    def test_an_agent_is_told_its_workspace_is_the_mountpoint(self, confined):
        text = chat_context.agent_self_preamble(
            name="Scout", slug="scout", role="maker", scope="project:example",
            cwd="/host/side/example", tools=["Read"])
        assert f"WORKSPACE: {scoped_exec.MOUNTPOINT}" in text
        assert "/host/side" not in text


class TestTheTaskCommandIsOneTheAgentCanRun:
    def test_a_scope_that_holds_the_cli_gets_it_relative_to_itself(self, monkeypatch):
        root = Path(chat_context.paths.workshop_root())
        assert chat_context._task_cli(root) == "python3 scripts/project_tasks.py"

    def test_a_scope_outside_the_platform_tree_is_offered_no_command(self, tmp_path):
        assert chat_context._task_cli(tmp_path) is None

    def test_the_rule_falls_back_to_the_server_side_block(self, tmp_path):
        rule = chat_context._task_edit_rule(tmp_path)
        assert "```task-patch" in rule
        assert "project_tasks.py" not in rule

    def test_no_platform_root_reaches_a_project_persona(self, confined, tmp_path):
        provider = object.__new__(chat_context.ProjectContextProvider)
        provider.slug = "example"
        provider.key = "project:example"
        provider.dir = tmp_path
        provider.is_self = False
        text = provider.persona()
        # The host path this process knows must not appear, and the managed
        # mountpoint must. Not asserted against `workshop_root()`: in a confined
        # checkout that root IS the mountpoint, which makes the assertion pass
        # for the wrong reason.
        assert str(tmp_path) not in text
        assert scoped_exec.MOUNTPOINT in text
        assert "project_tasks.py" not in text
