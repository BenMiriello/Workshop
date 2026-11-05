"""What the write boundary needs before it can be enforced: an app's outside
folders declared by the app, heavy commands capped without the systemd socket,
and app work handed to the app's own agent instead of written from the
platform chat."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mcp import ops_tools, turn_context  # noqa: E402
from services import scoped_exec  # noqa: E402


def test_an_app_declares_its_outside_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_DATA", "/srv/demo-data")
    (tmp_path / "workshop.app.json").write_text(json.dumps(
        {"folders": ["rw:${DEMO_DATA}", "/opt/models", "${UNSET_FOR_THIS_TEST}/x", "relative"]}))
    monkeypatch.delenv("UNSET_FOR_THIS_TEST", raising=False)
    assert scoped_exec.declared_folders(str(tmp_path)) == [
        ("rw", "/srv/demo-data", "/srv/demo-data"), ("ro", "/opt/models", "/opt/models")]


def test_a_tree_without_a_manifest_declares_nothing(tmp_path):
    assert scoped_exec.declared_folders(str(tmp_path)) == []


def test_a_capped_run_refuses_what_the_deny_list_forbids(tmp_path):
    with turn_context.bound({"WORKSHOP_QUERY_ROOT": str(tmp_path)}):
        text, failed = ops_tools.run(ops_tools.RUN_CAPPED_NAME, {"command": "git push origin main"})
    assert failed and "deny list" in text


def test_a_capped_run_runs_inside_the_callers_own_view(tmp_path, monkeypatch):
    seen = {}

    def fake_capped(slug, cmd, *, cwd, env=None, args=None):
        seen.update(cwd=cwd, args=args, memory=cmd.memory)
        return True, "out", 0.1

    from services import app_commands
    monkeypatch.setattr(app_commands, "run_capped", fake_capped)
    monkeypatch.setattr(scoped_exec, "spawn_argv", lambda cmd, root, **k: ["bwrap", root, *cmd])
    with turn_context.bound({"WORKSHOP_QUERY_ROOT": str(tmp_path)}):
        text, failed = ops_tools.run(ops_tools.RUN_CAPPED_NAME, {"command": "make big", "memory": "2G"})
    assert not failed and seen["args"][:2] == ["bwrap", str(tmp_path)]
    assert seen["memory"] == "2G"


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    (tmp_path / "workshop.app.json").write_text("{}")
    from services import app_runner
    monkeypatch.setattr(app_runner, "project_dir", lambda slug: tmp_path)
    return tmp_path


def test_app_work_is_handed_to_the_apps_own_agent(app_dir, monkeypatch):
    launched = {}
    from services import background_runs
    monkeypatch.setattr(background_runs, "launch", lambda **kw: launched.update(kw) or "job_1")
    claims = {"WORKSHOP_QUERY_SCOPE": "development", "WORKSHOP_QUERY_THREAD": "t1",
              "WORKSHOP_QUERY_OWNER": "3"}
    with turn_context.bound(claims):
        text, failed = ops_tools.run(ops_tools.AGENT_NAME, {"app": "demo", "task": "fix the header"})
    assert not failed and "job_1" in text
    assert launched["scope"] == "project:demo" and launched["thread_id"] == "t1"
    assert "fix the header" in launched["prompt"]


def test_only_the_development_chat_hands_work_over(app_dir):
    with turn_context.bound({"WORKSHOP_QUERY_SCOPE": "project:demo", "WORKSHOP_QUERY_THREAD": "t1",
                             "WORKSHOP_QUERY_OWNER": "3"}):
        text, failed = ops_tools.run(ops_tools.AGENT_NAME, {"app": "demo", "task": "x"})
    assert failed and "development chat" in text
