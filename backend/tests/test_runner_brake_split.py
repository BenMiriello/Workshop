"""Starting a worker and permitting unattended work are two decisions.

`POST /api/queue/runner/start` used to do both and get both wrong:

    autonomy_override.clear()                        # moved the GLOBAL brake
    subprocess.Popen(["bash", str(QUEUE_WATCHDOG)])  # with no DIR argument

`queue_runner_watchdog.sh` reads `DIR="${1:-}"` and calls `assert_run_target
"$DIR"`, which refuses an empty target under ADR 0014 and exits 2. So the button
cleared the brake and started nothing at all, and the brake stayed cleared.

These tests pin the split (phase 0,
docs/platform-redesign/autonomy-implementation-2026-08-13.md section 2.2):

  1. start does not touch the brake, and passes the tree it was given;
  2. start with no tree named is rejected, because ADR 0014 has no default;
  3. a refused tree comes back 400 carrying the run-target gate's OWN words,
     because the refusal an operator reads must be the one cron logs.

Test 3 is the one that keeps the reason from being reimplemented in Python and
drifting away from `scripts/dev_mode_gate.sh`.
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes import autonomy as autonomy_routes  # noqa: E402
from services import autonomy_override as ao  # noqa: E402


@pytest.fixture
def spawned(monkeypatch):
    """Record what the endpoint would have spawned instead of spawning it.

    A SHIM over the module's `subprocess` reference, not a patch of
    `subprocess.Popen` itself: `subprocess.run` is built on Popen, and the gate
    check is a real `run`. Patching the global broke the gate along with the
    spawn and every refusal test passed for the wrong reason."""
    import subprocess as real_subprocess

    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            calls.append(list(argv))

    class Shim:
        run = staticmethod(real_subprocess.run)
        Popen = FakePopen
        DEVNULL = real_subprocess.DEVNULL
        STDOUT = real_subprocess.STDOUT

    monkeypatch.setattr(autonomy_routes, "subprocess", Shim)
    return calls


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The routes, with the brake pointed at a sandbox so the real one is never
    touched. Both switches are redirected: `is_engaged()` reads their union, so
    a stray real AUTONOMY_DISABLED would otherwise decide these assertions."""
    rt = tmp_path / ".runtime"
    rt.mkdir()
    monkeypatch.setattr(ao, "OVERRIDE_PATH", rt / "autonomy-override.json")
    monkeypatch.setattr(ao, "LEGACY_FLAG", rt / "AUTONOMY_DISABLED")
    app = FastAPI()
    app.include_router(autonomy_routes.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def tree(tmp_path):
    """An ordinary working tree: not the platform repo, so the gate permits it
    whatever developer mode says."""
    d = tmp_path / "some-project"
    d.mkdir()
    return str(d)


class TestStartDoesNotMoveTheBrake:
    def test_start_leaves_an_engaged_brake_engaged(self, client, spawned, tree):
        """The regression. Starting a runner is not a statement about policy."""
        ao.engage("operator paused everything")
        r = client.post("/api/queue/runner/start", json={"dir": tree})
        assert r.status_code == 200, r.text
        assert ao.is_engaged() is True
        assert ao.read()["reason"] == "operator paused everything"

    def test_start_leaves_a_clear_brake_clear(self, client, spawned, tree):
        ao.clear()
        assert client.post("/api/queue/runner/start", json={"dir": tree}).status_code == 200
        assert ao.is_engaged() is False

    def test_start_passes_the_tree_to_the_watchdog(self, client, spawned, tree):
        """The other half of the old bug: the watchdog was spawned bare, so it
        exited 2 on `assert_run_target ""` every single time."""
        assert client.post("/api/queue/runner/start", json={"dir": tree}).status_code == 200
        assert len(spawned) == 1
        argv = spawned[0]
        assert argv[0] == "bash"
        assert argv[1].endswith("queue_runner_watchdog.sh")
        assert argv[2] == tree

    def test_stop_leaves_a_clear_brake_clear(self, client, spawned, tree):
        """Stop kills the resident runner. It no longer engages the brake, so a
        Stop cannot leave a global policy flag set with nothing naming it."""
        ao.clear()
        r = client.post(f"/api/queue/runner/stop?dir={tree}")
        assert r.status_code == 200, r.text
        assert ao.is_engaged() is False


class TestATargetMustBeNamed:
    def test_start_with_no_dir_is_rejected(self, client, spawned):
        r = client.post("/api/queue/runner/start", json={})
        assert r.status_code == 422, r.text
        assert spawned == []

    def test_start_with_an_empty_dir_is_refused_by_the_gate(self, client, spawned):
        """An empty string reaches the gate rather than being second-guessed in
        Python, and comes back with the gate's own sentence."""
        r = client.post("/api/queue/runner/start", json={"dir": ""})
        assert r.status_code == 400, r.text
        assert "no working tree named" in r.json()["detail"]
        assert spawned == []


class TestARefusedTargetCarriesTheGatesReason:
    def test_the_platform_repo_is_refused_when_dev_mode_is_off(
        self, client, spawned, monkeypatch
    ):
        """ADR 0014's second rule. The detail must be the SHELL's text, not a
        paraphrase: `scripts/dev_mode_gate.sh` is the one place that sentence
        lives, and the UI and cron have to say the same thing.

        `WORKSHOP_DEV_MODE=0` rather than unset on purpose: `scripts/lib/
        instance.sh` fills in anything still empty from the deployment's
        instance.env, which on a developer box sets it to 1. A non-empty value
        already in the environment wins, so this pins the refusal whatever the
        host is configured for."""
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "0")
        r = client.post(
            "/api/queue/runner/start",
            json={"dir": str(autonomy_routes.WORKSHOP_ROOT)},
        )
        assert r.status_code == 400, r.text
        detail = r.json()["detail"]
        assert "Workshop's own source tree" in detail
        assert "WORKSHOP_DEV_MODE=1" in detail
        assert spawned == []

    def test_dev_mode_on_permits_the_platform_repo(self, client, spawned, monkeypatch):
        """The gate is a real gate, not a blanket refusal: with the flag on, the
        same target is accepted and the watchdog is spawned with it."""
        monkeypatch.setenv("WORKSHOP_DEV_MODE", "1")
        root = str(autonomy_routes.WORKSHOP_ROOT)
        r = client.post("/api/queue/runner/start", json={"dir": root})
        assert r.status_code == 200, r.text
        assert spawned and spawned[0][2] == root
