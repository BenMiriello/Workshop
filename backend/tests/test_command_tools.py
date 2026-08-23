"""A declared `command` tool actually runs -- confined, typed, and denied.

Stage 4 of `docs/platform-redesign/tools-are-declared-2026-08-23.md`. What these
pin, in the doc's own terms:

  * HOW AN AGENT REACHES ONE. A command tool is listed on the workshop MCP
    server the turn already has and dispatched through its ordinary
    `tools/call`, so its CLI name is `mcp__workshop__<id>` and it arrives on the
    argv `llm_router.tool_flags` builds. No second server and no second
    enforcement point: declaring a tool still puts nothing on any allowlist.
  * THE DENY LIST APPLIES TWICE. At registration, naming the pattern, so a bad
    declaration is refused while a person is there to read why. And again on the
    RESOLVED argv at run time, which is the load-bearing one: it catches a tool
    declared before the pattern existed, and a forbidden command line assembled
    out of a parameter value.
  * TYPED INPUTS ARE CHECKED BEFORE ANYTHING RUNS. A missing required parameter
    or a value of the wrong type is a call that spawns no process at all.
  * IT GOES THROUGH `scoped_exec`, NOT BESIDE IT. The spawned argv is exactly
    what `spawn_argv` returns, and when confinement cannot be built the call
    fails rather than running with host filesystem access.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from services import agent_authority, command_tools, scoped_exec
from services import tool_registry as tr

_HAS_BWRAP = shutil.which("bwrap") is not None


@pytest.fixture()
def tools_file(tmp_path, monkeypatch):
    """Point the user store at a tmp path, exactly as test_tool_registry does:
    no test may write the real `.runtime/tools.json`."""
    path = tmp_path / "tools.json"
    monkeypatch.setattr(tr, "REGISTRY_PATH", path)
    return path


@pytest.fixture()
def no_app_tools(monkeypatch):
    """Isolate from whichever apps happen to be installed on this box."""
    monkeypatch.setattr(tr, "app_tools", lambda: [])


class _Subprocess:
    """A stand-in for the `subprocess` module, swapped into `command_tools`
    ALONE. Patching `subprocess.run` itself would also catch `scoped_exec`'s own
    bwrap probe, which is a spawn this file has no opinion about -- what is
    under test is what the RUNNER spawns."""

    TimeoutExpired = subprocess.TimeoutExpired
    CompletedProcess = subprocess.CompletedProcess

    def __init__(self, run):
        self.run = run


@pytest.fixture()
def never_spawns(monkeypatch):
    """Fail loudly if the runner spawns anything. Every refusal in this file is
    a refusal to RUN, so "it raised" is only half the assertion -- the other
    half is that no process was started before it raised."""
    def _boom(*a, **kw):  # noqa: ANN002, ANN003
        raise AssertionError(f"nothing should have been spawned, got {a!r}")
    monkeypatch.setattr(command_tools, "subprocess", _Subprocess(_boom))


@pytest.fixture()
def spawned(monkeypatch):
    """Record the argv the runner would have spawned, and answer as a command
    that succeeded."""
    calls: list[dict] = []

    def _fake(argv, **kw):  # noqa: ANN001, ANN003
        calls.append({"argv": argv, **kw})
        return subprocess.CompletedProcess(argv, 0, stdout="ran\n", stderr="")

    monkeypatch.setattr(command_tools, "subprocess", _Subprocess(_fake))
    return calls


@pytest.fixture()
def client():
    """The tools router on a bare app, exactly as test_tool_registry mounts it:
    what is under test is the tools contract, not the operator gate."""
    from fastapi import FastAPI

    from routes import tools as tools_routes

    app = FastAPI()
    app.include_router(tools_routes.router)
    return TestClient(app)


def _declare(tool_id: str, command: list[str], params: list | None = None) -> tr.Tool:
    return tr.create_user_tool(tool_id, {
        "title": tool_id,
        "description": f"{tool_id}, for a test",
        "kind": "command",
        "spec": {"command": command, "params": params or []},
    })


# --------------------------------------------------------------------------- #
# Registration: the good error message.
# --------------------------------------------------------------------------- #

class TestRegistrationRefusesAForbiddenCommand:

    def test_the_matching_pattern_is_named(self, tools_file, no_app_tools):
        with pytest.raises(ValueError) as e:
            _declare("release_it", ["git", "push", "origin", "main"])
        # NAMING the pattern is the requirement, not merely refusing: "that is
        # not allowed" leaves the author with nothing to change.
        assert "Bash(git push:*)" in str(e.value)
        assert not tools_file.exists(), "a refused declaration must write nothing"

    def test_a_command_hidden_inside_a_shell_string_is_still_seen(self, tools_file, no_app_tools):
        # The real command is the third argv element, after a `&&`. A check that
        # only read argv[0] would call this one `bash` and let it through.
        with pytest.raises(ValueError) as e:
            _declare("sneaky", ["bash", "-c", "cd /tmp && sudo rm -rf /var"])
        assert "Bash(sudo:*)" in str(e.value)

    def test_an_absolute_path_to_the_same_command_is_the_same_command(
            self, tools_file, no_app_tools):
        with pytest.raises(ValueError) as e:
            _declare("elevated", ["/usr/bin/sudo", "systemctl", "restart", "x"])
        assert "Bash(sudo:*)" in str(e.value)

    def test_an_ordinary_tool_is_accepted(self, tools_file, no_app_tools):
        tool = _declare("say_hello", ["echo", "hello"])
        assert tool.kind == "command"
        assert [t.id for t in tr.user_tools()] == ["say_hello"]

    def test_an_edit_is_checked_too(self, tools_file, no_app_tools):
        _declare("say_hello", ["echo", "hello"])
        with pytest.raises(ValueError) as e:
            tr.update_user_tool("say_hello", {
                "kind": "command", "spec": {"command": ["gh", "release", "create", "v1"]}})
        assert "Bash(gh release:*)" in str(e.value)
        # The stored record is untouched by the refused edit.
        assert tr.get("say_hello").spec["command"] == ["echo", "hello"]

    def test_the_api_refuses_it_with_the_pattern_in_the_message(
            self, tools_file, no_app_tools, client):
        r = client.post("/tools", json={
            "id": "release_it", "title": "Release", "kind": "command",
            "spec": {"command": ["git", "push"]}})
        assert r.status_code == 400
        assert "Bash(git push:*)" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Run time: the load-bearing check.
# --------------------------------------------------------------------------- #

class TestTheRunTimeCheckIsTheOneThatBinds:

    def test_a_tool_declared_before_the_pattern_existed_is_refused_when_called(
            self, tools_file, no_app_tools, monkeypatch, never_spawns):
        # Declared on a day when the deny list did not mention `git push`...
        monkeypatch.setattr(agent_authority, "IRREVERSIBLE_PATTERNS", ("Bash(shutdown:*)",))
        tool = _declare("release_it", ["git", "push", "origin", "main"])
        # ...and called on a day when it does. Registration cannot be re-run for
        # every record every time the list grows, so the run-time check is what
        # makes the list retroactive.
        monkeypatch.setattr(agent_authority, "IRREVERSIBLE_PATTERNS",
                            ("Bash(shutdown:*)", "Bash(git push:*)"))
        with pytest.raises(command_tools.ToolForbidden) as e:
            command_tools.run(tool, {}, workdir=str(tools_file.parent))
        assert "Bash(git push:*)" in str(e.value)

    def test_a_parameter_value_cannot_assemble_a_forbidden_line(
            self, tools_file, no_app_tools, never_spawns):
        # Legal at registration: the template says `git {sub}`, which matches no
        # pattern. The run-time check reads the RESOLVED argv, which does.
        tool = _declare("git_runner", ["bash", "-c", "git {sub}"],
                        [{"name": "sub", "type": "string", "required": True}])
        with pytest.raises(command_tools.ToolForbidden) as e:
            command_tools.run(tool, {"sub": "push origin main"},
                              workdir=str(tools_file.parent))
        assert "Bash(git push:*)" in str(e.value)

    def test_a_harmless_value_for_the_same_template_still_runs(
            self, tools_file, no_app_tools, spawned):
        # The other half of the pair above: the template is legal and stays
        # legal, so only the forbidden VALUE is refused.
        tool = _declare("git_runner", ["bash", "-c", "git {sub}"],
                        [{"name": "sub", "type": "string", "required": True}])
        command_tools.run(tool, {"sub": "status"}, workdir=str(tools_file.parent))
        assert spawned[0]["argv"][-1] == "git status"

    def test_a_record_written_into_the_file_by_hand_is_refused_at_run_time(
            self, tools_file, no_app_tools, never_spawns):
        # tools.json is an ordinary file. Nothing about writing it goes through
        # the API's refusal, so the run-time check is the only thing between a
        # hand-written record and the command it names.
        tools_file.write_text(
            '{"tools": [{"id": "handmade", "kind": "command",'
            ' "spec": {"command": ["npm", "publish"]}}]}\n', encoding="utf-8")
        tool = command_tools.get("handmade")
        assert tool is not None, "the record is readable; it is running it that is refused"
        with pytest.raises(command_tools.ToolForbidden) as e:
            command_tools.run(tool, {}, workdir=str(tools_file.parent))
        assert "Bash(npm publish:*)" in str(e.value)


# --------------------------------------------------------------------------- #
# Typed inputs, checked before anything runs.
# --------------------------------------------------------------------------- #

class TestTypedInputsAreValidatedBeforeAnythingRuns:

    @pytest.fixture()
    def greet(self, tools_file, no_app_tools):
        return _declare("greet", ["echo", "{greeting}", "{times}"], [
            {"name": "greeting", "type": "string", "required": True},
            {"name": "times", "type": "integer"},
        ])

    def test_a_missing_required_parameter_never_reaches_a_process(
            self, greet, tools_file, never_spawns):
        with pytest.raises(command_tools.ToolInputError) as e:
            command_tools.run(greet, {"times": 2}, workdir=str(tools_file.parent))
        assert "greeting" in str(e.value)

    def test_a_value_of_the_wrong_type_never_reaches_a_process(
            self, greet, tools_file, never_spawns):
        with pytest.raises(command_tools.ToolInputError) as e:
            command_tools.run(greet, {"greeting": "hi", "times": "lots"},
                              workdir=str(tools_file.parent))
        assert "times" in str(e.value) and "integer" in str(e.value)

    def test_a_parameter_the_tool_never_declared_is_refused(
            self, greet, tools_file, never_spawns):
        # Not silently dropped: a misspelled parameter would otherwise run the
        # command with a default nobody asked for and no way to see why.
        with pytest.raises(command_tools.ToolInputError) as e:
            command_tools.run(greet, {"greeting": "hi", "greetings": "hi"},
                              workdir=str(tools_file.parent))
        assert "greetings" in str(e.value)

    def test_a_boolean_is_not_an_integer(self, tools_file, no_app_tools, never_spawns):
        tool = _declare("counter", ["echo", "{n}"],
                        [{"name": "n", "type": "integer", "required": True}])
        with pytest.raises(command_tools.ToolInputError):
            command_tools.run(tool, {"n": True}, workdir=str(tools_file.parent))

    def test_an_unsupplied_optional_leaves_no_empty_argument(self, greet, spawned, tools_file):
        command_tools.run(greet, {"greeting": "hi"}, workdir=str(tools_file.parent))
        assert spawned[0]["argv"][-2:] == ["echo", "hi"]

    def test_the_schema_a_model_sees_is_generated_from_the_declared_params(self, greet):
        schema = command_tools.input_schema(greet)
        assert schema["required"] == ["greeting"]
        assert schema["properties"]["times"]["type"] == "integer"
        # additionalProperties mirrors the refusal above, so the model is told
        # the same rule the runner enforces.
        assert schema["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# Confinement: through scoped_exec, never beside it.
# --------------------------------------------------------------------------- #

class TestItRunsThroughTheExistingConfinement:

    def test_the_spawned_argv_is_exactly_what_scoped_exec_built(
            self, tools_file, no_app_tools, spawned, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
        if not _HAS_BWRAP:
            pytest.skip("bwrap not installed")
        scope = tmp_path / "scope"
        scope.mkdir()
        tool = _declare("say_hello", ["echo", "hello"])
        command_tools.run(tool, {}, workdir=str(scope))

        argv = spawned[0]["argv"]
        assert argv == scoped_exec.spawn_argv(["echo", "hello"], str(scope))
        assert argv[0].endswith("bwrap"), "a command tool is not spawned directly"
        assert argv[-2:] == ["echo", "hello"]
        assert "--chdir" in argv and scoped_exec.MOUNTPOINT in argv

    def test_the_env_is_the_remapped_turn_env(
            self, tools_file, no_app_tools, spawned, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
        if not _HAS_BWRAP:
            pytest.skip("bwrap not installed")
        scope = tmp_path / "scope"
        scope.mkdir()
        # A host path under the scope root does not exist inside the sandbox;
        # turn_env is what rewrites it to the mountpoint.
        monkeypatch.setenv("WORKSHOP_ROOT", str(scope))
        tool = _declare("say_hello", ["echo", "hello"])
        command_tools.run(tool, {}, workdir=str(scope))
        assert spawned[0]["env"]["WORKSHOP_ROOT"] == scoped_exec.MOUNTPOINT

    def test_it_refuses_rather_than_running_unconfined(
            self, tools_file, no_app_tools, never_spawns, monkeypatch, tmp_path):
        # `WORKSHOP_SCOPED_EXEC=0` is scoped_exec's own escape hatch. A command
        # tool inherits its fail-closed posture instead of noticing the sandbox
        # is off and running anyway.
        monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")
        tool = _declare("say_hello", ["echo", "hello"])
        with pytest.raises(scoped_exec.ConfinementUnavailable):
            command_tools.run(tool, {}, workdir=str(tmp_path))

    @pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap not installed")
    def test_a_declared_tool_really_runs_and_answers(
            self, tools_file, no_app_tools, monkeypatch, tmp_path):
        monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
        # An empty HOME so `_grants` finds none of the host user's dotfiles to
        # bind: this asserts the runner, not this box's ~/.claude.json.
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        scope = tmp_path / "scope"
        scope.mkdir()
        tool = _declare("say_hello", ["sh", "-c", "echo hello {who}; pwd"],
                        [{"name": "who", "type": "string", "required": True}])
        out = command_tools.run(tool, {"who": "workshop"}, workdir=str(scope))
        if "bwrap:" in out:
            pytest.skip(f"bwrap cannot build a sandbox here: {out}")
        assert "hello workshop" in out
        # The scope is presented at the mountpoint, so what the tool sees of the
        # filesystem is what the turn sees -- not the host path.
        assert scoped_exec.MOUNTPOINT in out
        assert "exit 0" in out


# --------------------------------------------------------------------------- #
# Reaching it: the workshop server, and the one enforcement point.
# --------------------------------------------------------------------------- #

class TestAnAgentReachesItThroughTheWorkshopServer:

    def test_it_is_listed_beside_the_servers_own_tools(self, tools_file, no_app_tools):
        from mcp import workshop_query_server as wqs

        _declare("say_hello", ["echo", "hello"],
                 [{"name": "who", "type": "string", "required": True}])
        listed = {t["name"]: t for t in wqs._tool_list()}
        assert wqs.SEARCH_TOOL_NAME in listed, "the platform tools are still there"
        assert "say_hello" in listed
        assert listed["say_hello"]["inputSchema"]["required"] == ["who"]
        assert listed["say_hello"]["description"]

    def test_calling_it_over_the_servers_own_jsonrpc_runs_it(
            self, tools_file, no_app_tools, spawned):
        from mcp import workshop_query_server as wqs

        _declare("say_hello", ["echo", "hello"])
        resp = wqs._handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                            "params": {"name": "say_hello", "arguments": {}}})
        assert resp["result"]["isError"] is False
        assert "exit 0" in resp["result"]["content"][0]["text"]

    def test_a_refusal_comes_back_as_a_failed_tool_call(self, tools_file, no_app_tools,
                                                        never_spawns):
        from mcp import workshop_query_server as wqs

        _declare("greet", ["echo", "{who}"],
                 [{"name": "who", "type": "string", "required": True}])
        resp = wqs._handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                            "params": {"name": "greet", "arguments": {}}})
        assert resp["result"]["isError"] is True
        assert "who" in resp["result"]["content"][0]["text"]

    def test_an_undeclared_name_is_still_an_unknown_tool(self, tools_file, no_app_tools):
        from mcp import workshop_query_server as wqs

        resp = wqs._handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                            "params": {"name": "no_such_tool", "arguments": {}}})
        assert resp["error"]["code"] == -32602

    def test_a_declared_tool_cannot_take_a_server_tools_name(self, tools_file, no_app_tools):
        from mcp import workshop_query_server as wqs

        # tools.json is an ordinary file, so this record can exist. What it must
        # not do is become the server's own `search`.
        tools_file.write_text(
            '{"tools": [{"id": "search", "kind": "command",'
            ' "spec": {"command": ["echo", "not-search"]}}]}\n', encoding="utf-8")
        names = [t["name"] for t in wqs._tool_list()]
        assert names.count(wqs.SEARCH_TOOL_NAME) == 1
        assert wqs._tool_list()[1]["description"] == wqs.SEARCH_TOOL_DESC


class TestDeclaringStillGrantsNothing:

    def test_the_grant_arrives_through_tool_flags_and_nowhere_else(self):
        """The registry is an enumeration; `tool_flags` is the argv. A command
        tool reaches an agent as an ordinary `mcp__workshop__<id>` name on the
        allowlist that builder writes -- there is no second path."""
        from services import llm_router

        name = "mcp__workshop__say_hello"
        flags, _env, _block = llm_router.tool_flags(
            allowed_tools=[name], mcp_servers=["workshop"])
        assert name in flags[flags.index("--allowedTools"):]

    def test_a_declared_tool_is_not_added_to_any_allowlist_by_declaring_it(
            self, tools_file, no_app_tools):
        from services import chat_context

        _declare("say_hello", ["echo", "hello"])
        granted = chat_context.effective_allowed_tools(chat_context._general)
        assert not any("say_hello" in t for t in granted)

    def test_a_role_is_not_widened_by_a_user_tool(self, tools_file, no_app_tools):
        _declare("say_hello", ["echo", "hello"])
        # narrow-never-widen: an agent definition asking for it under a role
        # that was not granted it still does not get it.
        assert agent_authority.narrow_tools(
            "keeper", ["mcp__workshop__say_hello"]) == []
