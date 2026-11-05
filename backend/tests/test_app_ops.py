"""The operation verbs: who may operate what, and that a restart reports what
actually happened (it waits for health, refuses unsaved work, runs the app's
own preflight and reload)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from mcp import ops_tools, turn_context  # noqa: E402
from services import app_ops, app_runner  # noqa: E402


@pytest.mark.parametrize("scope,slug,ok", [
    ("development", "widgets", True),
    ("project:widgets", "widgets", True),
    ("project:gadgets", "widgets", False),
    ("app:widgets", "widgets", False),
    ("workshop", "widgets", False),
    ("", "widgets", False),
])
def test_authority(scope, slug, ok):
    if ok:
        app_ops.authority(scope, slug)
    else:
        with pytest.raises(app_ops.Refused):
            app_ops.authority(scope, slug)


class FakeRunner:
    def __init__(self, tmp_path, spec):
        self.dir = tmp_path
        self.spec = spec
        self.pid = 100
        self.restarts = 0

    def install(self, mp):
        mp.setattr(app_runner, "is_self_project", lambda slug: False)
        mp.setattr(app_runner, "resolve_manifest",
                   lambda slug: app_runner.AppManifest(name=slug, services=[self.spec]))
        mp.setattr(app_runner, "project_dir", lambda slug: self.dir)
        mp.setattr(app_runner, "command_env", lambda slug, spec: {"PATH": "/usr/bin:/bin"})
        mp.setattr(app_runner, "app_status", lambda slug: {"phase": "up", "services": [
            {"name": self.spec.name, "status": "running", "pid": self.pid, "port": 9,
             "healthy": True, "error": None}]})
        mp.setattr(app_runner, "app_logs", lambda slug, svc, max_bytes=0: "log tail")
        mp.setattr(app_runner, "restart_service", self.restart)
        mp.setattr(app_ops.time, "sleep", lambda s: None)

    def restart(self, slug, name):
        self.restarts += 1
        self.pid += 1
        return {}


def test_a_restart_reports_the_pid_change(tmp_path, monkeypatch):
    fake = FakeRunner(tmp_path, app_runner.ServiceSpec(name="api", start="x", health="/h"))
    fake.install(monkeypatch)
    out = app_ops.restart("demo", "api")
    assert "pid 100 -> 101" in out and fake.restarts == 1


def test_a_failed_preflight_restarts_nothing(tmp_path, monkeypatch):
    fake = FakeRunner(tmp_path, app_runner.ServiceSpec(name="api", start="x", preflight="echo broken; exit 3"))
    fake.install(monkeypatch)
    with pytest.raises(app_ops.Refused, match="broken"):
        app_ops.restart("demo", "api")
    assert fake.restarts == 0


def test_a_declared_reload_runs_instead_of_a_respawn(tmp_path, monkeypatch):
    marker = tmp_path / "reloaded"
    fake = FakeRunner(tmp_path, app_runner.ServiceSpec(name="api", start="x", reload=f"touch {marker}"))
    fake.install(monkeypatch)
    out = app_ops.restart("demo", "api")
    assert marker.exists() and fake.restarts == 0 and "swapped in new code" in out


def test_uncommitted_work_is_refused_unless_claimed(tmp_path, monkeypatch):
    fake = FakeRunner(tmp_path, app_runner.ServiceSpec(name="api", start="x"))
    fake.install(monkeypatch)
    monkeypatch.setattr(app_ops, "uncommitted", lambda slug: ["server/half_done.py"])
    with pytest.raises(app_ops.Refused, match="half_done.py"):
        app_ops.restart("demo", "api")
    assert fake.restarts == 0
    assert "pid" in app_ops.restart("demo", "api", include_uncommitted=True)


def test_a_service_that_does_not_come_back_says_so_with_its_log(tmp_path, monkeypatch):
    fake = FakeRunner(tmp_path, app_runner.ServiceSpec(name="api", start="x", ready_timeout=0))
    fake.install(monkeypatch)
    monkeypatch.setattr(app_runner, "restart_service", lambda slug, name: {})
    with pytest.raises(RuntimeError, match="log tail"):
        app_ops.restart("demo", "api")


def test_the_tools_are_offered_only_where_the_runner_lives():
    assert ops_tools.offered() is False
    with turn_context.bound({"WORKSHOP_QUERY_SCOPE": "project:demo"}):
        assert ops_tools.offered() is True


def test_an_app_chat_defaults_to_its_own_app_and_cannot_reach_another(monkeypatch):
    seen = {}
    monkeypatch.setattr(app_ops, "status", lambda slug: seen.setdefault("slug", slug) or "ok")
    with turn_context.bound({"WORKSHOP_QUERY_SCOPE": "project:demo"}):
        text, failed = ops_tools.run(ops_tools.STATUS_NAME, {})
        assert not failed and seen["slug"] == "demo"
        text, failed = ops_tools.run(ops_tools.STATUS_NAME, {"app": "other"})
        assert failed and text.startswith("Refused")


def test_the_platforms_own_state_files_are_not_unsaved_work(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "state.json").write_text("{}")
    (tmp_path / ".tasks.json").write_text("{}")
    (tmp_path / "half_done.py").write_text("x = ")
    monkeypatch.setattr(app_runner, "project_dir", lambda slug: tmp_path)
    assert app_ops.uncommitted("demo") == ["half_done.py"]
