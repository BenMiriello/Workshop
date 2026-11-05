"""The sandbox a chat turn's shell commands run in (services/turn_sandbox)."""
import json

from services import build_streaming, local_turn, tool_policy, turn_sandbox


def meta(**kw) -> dict:
    base = {"turn_id": "t1", "model": "claude-opus-5", "session_id": None,
            "resume": False, "cwd": "/tmp"}
    base.update(kw)
    return base


def _settings(cmd: list[str]) -> dict:
    return json.loads(cmd[cmd.index("--settings") + 1])


def test_it_is_off_unless_the_installation_turns_it_on(monkeypatch):
    monkeypatch.delenv(turn_sandbox.ENABLE_ENV, raising=False)
    assert turn_sandbox.settings(tool_policy.OPEN) is None
    assert "sandbox" not in _settings(build_streaming._build_cmd(meta(permission_mode="auto"))[0])


def test_an_auto_chat_runs_its_shell_in_the_sandbox(monkeypatch):
    monkeypatch.setenv(turn_sandbox.ENABLE_ENV, "on")
    box = _settings(build_streaming._build_cmd(meta(permission_mode="auto"))[0])["sandbox"]
    assert box["enabled"] and box["failIfUnavailable"]
    assert box["allowUnsandboxedCommands"]
    assert "systemctl *" in box["excludedCommands"]


def test_a_chosen_mode_keeps_its_asks_so_gets_no_sandbox(monkeypatch):
    monkeypatch.setenv(turn_sandbox.ENABLE_ENV, "on")
    for mode in ("default", "acceptEdits"):
        assert "sandbox" not in _settings(build_streaming._build_cmd(meta(permission_mode=mode))[0])


def test_the_installation_declares_its_own_exclusions(monkeypatch):
    monkeypatch.setenv(turn_sandbox.ENABLE_ENV, "on")
    monkeypatch.setenv("WORKSHOP_SANDBOX_EXCLUDED_COMMANDS", "bash scripts/apply.sh *")
    monkeypatch.setenv("WORKSHOP_SANDBOX_WRITE_PATHS", "~/.config/x")
    box = turn_sandbox.settings(tool_policy.OPEN)
    assert "bash scripts/apply.sh *" in box["excludedCommands"]
    assert "~/.config/x" in box["filesystem"]["allowWrite"]


def test_a_local_turn_gets_it_too(monkeypatch):
    monkeypatch.setenv(turn_sandbox.ENABLE_ENV, "on")
    assert "sandbox" in json.loads(local_turn.settings(tool_policy.OPEN))
    assert "sandbox" not in json.loads(local_turn.settings(tool_policy.GUARD))
