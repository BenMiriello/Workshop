"""A recorded pid is only trusted while it still names the process we spawned.

After a reboot the kernel reissues low pids, and /proc/<n>/ resolves for thread
ids as well as process ids. On 2026-09-21 one app's api pid came back as a thread
of the Letta container; liveness read it as a running process and the monitor
left the api dead until someone restarted it by hand.
"""
from __future__ import annotations

import json
import os
import threading

from services import app_runner, port_registry


def test_thread_id_is_not_a_live_process():
    started = threading.Event()
    release = threading.Event()

    def hold():
        started.set()
        release.wait()

    t = threading.Thread(target=hold)
    t.start()
    started.wait()
    try:
        tid = t.native_id
        assert os.path.exists(f"/proc/{tid}/stat")
        assert port_registry.process_alive(tid) is False
        assert port_registry.process_alive(os.getpid()) is True
    finally:
        release.set()
        t.join()


def _persisted(tmp_path, monkeypatch, boot_stamp: str | None):
    store = tmp_path / "app-deploys.json"
    store.write_text(json.dumps({"widgets": {"slug": "widgets", "desired": "running",
                                            "services": {"api": {"name": "api", "status": "running",
                                                                 "pid": os.getpid(), "port": 8700}}}}))
    stamp = tmp_path / "app-deploys.boot"
    if boot_stamp is not None:
        stamp.write_text(boot_stamp)
    monkeypatch.setattr(app_runner, "STATE_STORE", store)
    monkeypatch.setattr(app_runner, "BOOT_STAMP", stamp)
    monkeypatch.setattr(app_runner, "_boot_id", lambda: "boot-now")
    state: dict = {}
    monkeypatch.setattr(app_runner, "_STATE", state)
    app_runner._load_persisted()
    return state, stamp


def test_state_from_a_previous_boot_drops_pids(tmp_path, monkeypatch):
    state, stamp = _persisted(tmp_path, monkeypatch, "boot-before")
    svc = state["widgets"]["services"]["api"]
    assert svc["pid"] is None
    assert svc["status"] == "failed"
    assert stamp.read_text().strip() == "boot-now"


def test_state_from_this_boot_keeps_pids(tmp_path, monkeypatch):
    state, _ = _persisted(tmp_path, monkeypatch, "boot-now")
    svc = state["widgets"]["services"]["api"]
    assert svc["pid"] == os.getpid()
    assert svc["status"] == "running"


def test_missing_stamp_is_trusted_and_written(tmp_path, monkeypatch):
    state, stamp = _persisted(tmp_path, monkeypatch, None)
    assert state["widgets"]["services"]["api"]["status"] == "running"
    assert stamp.read_text().strip() == "boot-now"
