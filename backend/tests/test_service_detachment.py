"""A supervised app must survive a restart of the supervisor.

Stopping a systemd unit kills its whole CGROUP. Services were spawned as plain
children of the backend, so they lived in the backend's cgroup, so
`systemctl restart control-panel@workshop-backend` killed every app the runner
was supervising. On 2026-08-17 that took one app down three times in one hour
while the operator was using it - and each time it looked like the app had
crashed, because from the outside that is exactly what it looks like.

`start_new_session=True` does NOT help: a new session and process group are not
a new cgroup, and systemd kills by cgroup.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

from services import app_runner


def _cgroup_of(pid: int) -> str:
    with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
        return f.read().strip()


needs_systemd_run = pytest.mark.skipif(
    not shutil.which("systemd-run") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
    reason="needs a systemd user session")


@pytest.fixture(autouse=True)
def reset_probe():
    """The capability probe is cached per process; each test states its own world."""
    app_runner._DETACH_OK = None
    yield
    app_runner._DETACH_OK = None


def _bus_env() -> dict:
    env = {"PATH": os.environ.get("PATH", "")}
    for key in app_runner._BUS_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


@needs_systemd_run
def test_the_start_command_is_wrapped_in_its_own_scope():
    argv = app_runner._detached_argv(["/bin/bash", "-c", "run me"], _bus_env())
    assert argv[:3] == ["systemd-run", "--user", "--scope"]
    assert argv[-3:] == ["/bin/bash", "-c", "run me"]


def test_wrapping_can_be_turned_off(monkeypatch):
    """A box without a systemd user session still has to run apps."""
    monkeypatch.setattr(app_runner, "DETACH_SERVICES", False)
    assert app_runner._detached_argv(["/bin/bash", "-c", "x"], _bus_env()) == ["/bin/bash", "-c", "x"]


def test_wrapping_is_skipped_when_systemd_run_is_absent(monkeypatch):
    monkeypatch.setattr(app_runner.shutil, "which", lambda _: None)
    assert app_runner._detached_argv(["/bin/echo", "hi"], _bus_env()) == ["/bin/echo", "hi"]


def test_a_service_env_without_a_bus_is_never_wrapped():
    """The regression that took every app down.

    `_service_env` is an allowlist, so a spawned service has no
    DBUS_SESSION_BUS_ADDRESS. Wrapping anyway gave "Failed to connect to bus" and
    an instant exit for EVERY service the runner started.
    """
    assert app_runner._detached_argv(
        ["/bin/bash", "-c", "x"], {"PATH": "/usr/bin"}) == ["/bin/bash", "-c", "x"]


@needs_systemd_run
def test_a_detached_service_lands_outside_the_callers_cgroup():
    """The property that matters, checked against the kernel rather than assumed."""
    mine = _cgroup_of(os.getpid())
    proc = subprocess.Popen(
        app_runner._detached_argv(["/bin/bash", "-c", "sleep 10"], _bus_env()),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        for _ in range(40):
            time.sleep(0.1)
            try:
                theirs = _cgroup_of(proc.pid)
            except FileNotFoundError:
                pytest.fail("the wrapped process died immediately")
            if theirs != mine:
                break
        else:
            pytest.fail(f"never left the caller's cgroup ({mine})")
        assert ".scope" in theirs
    finally:
        proc.kill()
        proc.wait(timeout=10)


@needs_systemd_run
def test_the_pid_is_preserved_so_liveness_and_signals_still_work():
    """Everything downstream keys off this pid.

    `--scope` execs in place rather than forking, so the pid Popen returns IS
    the service. If that stopped being true, pid liveness, `killpg` and
    `port_registry.set_pid` would all silently track a process that had already
    gone, and a crashed service would look healthy forever.
    """
    proc = subprocess.Popen(
        app_runner._detached_argv(
            ["/bin/bash", "-c", "echo $$; exec sleep 10"], _bus_env()),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True, text=True)
    try:
        reported = int(proc.stdout.readline().strip())
        assert reported == proc.pid
        # And the process group is intact, which is how _stop_service signals.
        assert os.getpgid(proc.pid) == proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=10)
