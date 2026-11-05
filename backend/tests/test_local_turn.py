"""A turn on a local model starts lean: its window is small."""
from __future__ import annotations

import json
import subprocess

import pytest

from services import app_scope_turn, build_streaming, llm_providers, local_turn


def meta(**kw) -> dict:
    base = {"turn_id": "t1", "model": "w", "session_id": None, "resume": False,
            "cwd": "/tmp", "permission_mode": "bypassPermissions"}
    base.update(kw)
    return base


@pytest.fixture()
def local(monkeypatch):
    monkeypatch.setattr(llm_providers, "redirects_cli", lambda m: m == "w")


def _after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_a_local_turn_loads_no_settings_and_offers_only_the_coding_tools(local):
    cmd, _ = build_streaming._build_cmd(meta())
    assert _after(cmd, "--setting-sources") == ""
    assert _after(cmd, "--tools") == ",".join(local_turn.BUILTIN_TOOLS)


def test_a_local_turn_that_chose_its_tools_keeps_them(local):
    cmd, _ = build_streaming._build_cmd(
        meta(allowed_tools=["WebSearch"], mcp_servers=[]))
    assert _after(cmd, "--setting-sources") == ""
    assert cmd.count("--tools") <= 1
    assert local_turn.BUILTIN_TOOLS[0] + "," not in " ".join(cmd)


def test_a_local_turn_loads_no_claude_md_and_no_memory(local):
    env = build_streaming._turn_env(meta(), {})
    assert env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_a_cloud_turn_is_unchanged(local, monkeypatch):
    for k in app_scope_turn.ISOLATION_ENV:
        monkeypatch.delenv(k, raising=False)
    cmd, _ = build_streaming._build_cmd(meta(model="claude-opus-5"))
    assert "--setting-sources" not in cmd and "--tools" not in cmd
    env = build_streaming._turn_env(meta(model="claude-opus-5"), {})
    assert not set(app_scope_turn.ISOLATION_ENV) & set(env)


def test_no_model_runs_the_clis_own_auto(local):
    from services import model_caps

    assert model_caps.mode_for_model("auto", "w") == "acceptEdits"
    assert model_caps.mode_for_model("auto", "claude-opus-5") == "acceptEdits"
    assert model_caps.mode_for_model("plan", "w") == "plan"
    cmd, _ = build_streaming._build_cmd(meta(permission_mode="auto"))
    assert _after(cmd, "--permission-mode") == "acceptEdits"


def test_every_model_is_offered_the_same_modes(local):
    from services import model_caps

    assert "modes" not in model_caps.caps_for("w", None, None, "auto")
    assert "modes" not in model_caps.caps_for("claude-opus-5")


def test_a_local_app_turn_keeps_its_tools_and_carries_the_guard(local):
    cmd, _ = build_streaming._build_cmd(meta(app_scoped=True, allowed_tools=["WebFetch"],
                                             mcp_servers=[], permission_mode="auto"))
    hooks = [h["hooks"][0]["command"] for h in
             json.loads(_after(cmd, "--settings"))["hooks"]["PreToolUse"]]
    assert any(local_turn.GUARD_ENV in h for h in hooks)
    assert ",".join(local_turn.BUILTIN_TOOLS) not in cmd
    env = build_streaming._turn_env(meta(app_scoped=True), {})
    assert env["MAX_MCP_OUTPUT_TOKENS"] == str(local_turn.MCP_OUTPUT_TOKENS)
    assert "REPEAT_LIMIT" in env[local_turn.GUARD_ENV]


def test_the_guard_runs_from_its_own_source(tmp_path):
    hook = json.loads(local_turn.settings(None))["hooks"]["PreToolUse"][0]["hooks"][0]
    event = json.dumps({"tool_name": "WebFetch", "tool_input": {"url": "x"}})
    env = {"WORKSHOP_GUARD_TURN": "guard-source-test", "PATH": "/usr/bin:/bin",
           "TMPDIR": str(tmp_path), local_turn.GUARD_ENV: local_turn.GUARD.read_text()}
    assert len(hook["command"]) < 60
    codes = [subprocess.run(hook["command"], shell=True, input=event, text=True,
                            env=env, capture_output=True).returncode for _ in range(3)]
    assert codes == [0, 0, 2]


def test_a_local_turn_is_not_offered_the_web_search_its_server_cannot_run():
    assert not set(local_turn.SERVER_SIDE_TOOLS) & set(local_turn.BUILTIN_TOOLS)
