"""A service that is already serving is adopted, never reported down or respawned.

The runner's record is a cache and the socket is the truth. Each test runs a
real server from the service's own directory, then leaves the record saying
the service is dead, which is what a reboot restore overlapping the monitor, or
two starts from two tabs, left behind in practice.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from services import app_runner


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """An app `a` with one service `web` whose directory is tmp/web, a private
    runner state, and a registry that records what it is told."""
    (tmp_path / "web").mkdir()
    port = _free_port()
    manifest = app_runner.AppManifest(name="a", services=[app_runner.ServiceSpec(
        name="web", start="serve", cwd="web", port=port)])
    adopted: dict = {}
    monkeypatch.setattr(app_runner, "resolve_manifest", lambda slug: manifest)
    monkeypatch.setattr(app_runner, "_project_dir", lambda slug: tmp_path)
    monkeypatch.setattr(app_runner, "is_self_project", lambda slug: False)
    monkeypatch.setattr(app_runner, "_persist_state", lambda: None)
    monkeypatch.setattr(app_runner, "_LOCKS", {})
    monkeypatch.setattr(app_runner.port_registry, "adopt",
                        lambda owner, kind, p, pid: adopted.update({owner: pid}) or True)
    monkeypatch.setattr(app_runner.port_registry, "release", lambda owner: None)
    monkeypatch.setattr(app_runner, "_STATE", {"a": {
        "slug": "a", "desired": "running", "phase": "up", "backend": "subprocess",
        "services": {"web": {"name": "web", "status": "failed", "pid": None,
                             "port": port, "error": "host rebooted"}}}})
    procs: list[subprocess.Popen] = []

    def serve(cwd) -> subprocess.Popen:
        p = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        procs.append(p)
        for _ in range(100):
            if app_runner._service_healthy(port, None, timeout=0.2):
                return p
            time.sleep(0.05)
        raise RuntimeError("test server did not come up")

    yield {"serve": serve, "adopted": adopted, "dir": tmp_path, "port": port}
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()


def _web(slug: str = "a") -> dict:
    return next(s for s in app_runner.app_status(slug)["services"] if s["name"] == "web")


def test_a_serving_service_recorded_failed_reads_running(world):
    p = world["serve"](world["dir"] / "web")
    web = _web()
    assert web["status"] == "running"
    assert web["pid"] == p.pid
    assert world["adopted"] == {"app:a/web": p.pid}
    assert app_runner.app_status("a")["phase"] == "up"


def test_a_process_from_another_directory_on_the_port_is_not_adopted(world, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    world["serve"](tmp_path / "elsewhere")
    assert _web()["status"] == "failed"
    assert world["adopted"] == {}


def test_nothing_on_the_port_stays_failed(world):
    assert _web()["status"] == "failed"
    assert app_runner.app_status("a")["phase"] == "stopped"


def test_a_bring_up_in_progress_owns_its_services(world):
    world["serve"](world["dir"] / "web")
    app_runner._STATE["a"]["phase"] = "starting"
    assert _web()["status"] == "failed"


def test_a_status_read_during_a_bring_up_keeps_its_port_claim(world, monkeypatch):
    released: list[str] = []
    monkeypatch.setattr(app_runner.port_registry, "release", released.append)
    reaped = subprocess.Popen([sys.executable, "-c", "pass"])
    reaped.wait()
    app_runner._STATE["a"]["phase"] = "starting"
    app_runner._STATE["a"]["services"]["web"].update(status="running", pid=reaped.pid)
    app_runner.app_status("a")
    assert released == []
    app_runner._STATE["a"]["phase"] = "up"
    app_runner.app_status("a")
    assert released == ["app:a/web"]


def test_opening_a_serving_app_does_not_start_it_again(world, monkeypatch):
    world["serve"](world["dir"] / "web")
    monkeypatch.setattr(app_runner, "start_app",
                        lambda slug: pytest.fail("a serving app was started again"))
    assert app_runner.restore_desired_running() == []


def test_two_starts_at_once_run_one_bring_up(world, monkeypatch):
    runs: list[str] = []
    gate = threading.Event()

    def bring_up(slug, manifest):
        with app_runner._slug_lock(slug):
            runs.append(slug)
            gate.wait(2)
        app_runner._STATE[slug]["phase"] = "up"

    monkeypatch.setattr(app_runner, "_bring_up", bring_up)
    monkeypatch.setattr(app_runner, "_use_containers", lambda: False)
    app_runner._STATE["a"]["phase"] = "stopped"
    app_runner.start_app("a")
    app_runner.start_app("a")
    gate.set()
    time.sleep(0.2)
    assert runs == ["a"]


def test_a_bring_up_cut_off_by_a_restart_is_not_left_in_progress(tmp_path, monkeypatch):
    store = tmp_path / "app-deploys.json"
    store.write_text('{"a": {"phase": "starting", "services": '
                     '{"web": {"status": "building"}, "api": {"status": "running", "pid": 5}}}}')
    monkeypatch.setattr(app_runner, "STATE_STORE", store)
    monkeypatch.setattr(app_runner, "BOOT_STAMP", tmp_path / "boot")
    monkeypatch.setattr(app_runner, "_STATE", {})
    app_runner._load_persisted()
    a = app_runner._STATE["a"]
    assert a["phase"] == "failed"
    assert a["services"]["web"]["status"] == "failed"
    assert a["services"]["api"]["status"] == "running"
