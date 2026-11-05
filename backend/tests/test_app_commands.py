"""The work verbs run what an app declares, capped, and never turn an agent's
arguments into shell text."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from services import app_commands, app_ops, app_runner  # noqa: E402
from services.app_runner import AppCommand  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "workshop.app.json").write_text(json.dumps({
        "name": "demo",
        "services": [{"name": "web", "start": "true", "build": "echo built > built.txt"}],
        "test": 'echo "args:$#:$1"',
        "checks": [{"name": "ok", "command": "true"}, {"name": "bad", "command": "echo nope; exit 1"}],
        "env_requirements": [{"key": "DEMO_KEY", "required": True, "secret": True}],
    }))
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: root)
    monkeypatch.setattr(app_runner, "project_dir", lambda slug: root)
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "command_env", lambda slug, spec: None)
    return root


def test_the_manifest_reads_the_new_declarations(app):
    m = app_runner.resolve_manifest("demo")
    assert m.test.command.startswith("echo")
    assert [c.name for c in m.checks] == ["ok", "bad"]


def test_arguments_are_arguments_not_shell(app, tmp_path):
    marker = tmp_path / "pwned"
    text, failed = app_commands.test("demo", [f"; touch {marker}"])
    assert not failed
    assert "args:1:; touch" in text
    assert not marker.exists()


def test_checks_report_each_and_fail_together(app):
    text, failed = app_commands.check("demo")
    assert failed
    assert "ok: passed" in text and "bad: FAILED" in text and "nope" in text


def test_build_runs_the_declared_build(app):
    text, failed = app_commands.build("demo")
    assert not failed and (app / "built.txt").exists()


def test_only_declared_settings_can_be_saved(app):
    with pytest.raises(app_ops.Refused, match="UNDECLARED"):
        app_commands.env("demo", {"UNDECLARED": "x"})


def test_a_command_that_runs_too_long_is_stopped(app):
    ok, out, _ = app_commands.run_capped("demo", AppCommand(command="sleep 5", timeout_s=1), cwd=app)
    assert not ok and "still running" in out


def test_the_platform_is_its_own_declaration():
    assert app_commands.is_platform("workshop")
    with pytest.raises(app_ops.Refused, match="app_apply"):
        app_ops.status("workshop")
    with pytest.raises(app_ops.Refused):
        app_ops.authority("project:demo", "workshop")
    app_ops.authority("development", "workshop")
