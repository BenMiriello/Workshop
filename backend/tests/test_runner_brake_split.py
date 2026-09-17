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
    spawn and every refusal test passed for the wrong reason.

    BOTH modules are shimmed. The route still runs the developer-mode gate with
    a real `subprocess.run`, but the watchdog SPAWN now lives in
    services/work_driver, which owns the runner's process facts: a route module
    should not manage processes, and the reconciler needs the same code path.
    The assertions below are unchanged — only the seam moved."""
    import subprocess as real_subprocess

    from services import work_driver

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
    monkeypatch.setattr(work_driver, "subprocess", Shim)
    # `ensure` waits for the detached runner to take the lock before deciding
    # whether the start worked. Nothing real is spawned here, so skip the wait
    # and the backoff it would record.
    monkeypatch.setattr(work_driver.time, "sleep", lambda s: None)
    monkeypatch.setattr(work_driver, "_failed_at", {})
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
    # Start now ARMS the scope rather than only poking a process (the runner is
    # derived from the intent store, so a start that only spawned would be
    # undone by the next reconcile pass). That is a persistent write, so the
    # intent store is sandboxed here too or a test would arm a real scope.
    from services import scope_execution
    monkeypatch.setattr(scope_execution, "_store_path",
                        lambda: rt / "scope-execution.json")
    app = FastAPI()
    app.include_router(autonomy_routes.router, prefix="/api")
    return TestClient(app)


@pytest.fixture
def tree(tmp_path):
    """An ordinary working tree: not the platform repo, so the gate permits it
    whatever developer mode says.

    It carries a task store because the endpoint now requires the target to be
    one (or a git worktree) before it will start anything. A bare directory is
    refused: the first thing a sweep does is `tasks.next_ready(dir)`, which
    CREATES `.tasks.json` and `TODO.md` in whatever it is handed, so a typo used
    to start a sweep against an arbitrary path and leave two files in it."""
    d = tmp_path / "some-project"
    d.mkdir()
    (d / ".tasks.json").write_text('{"version": 2, "tasks": []}')
    return str(d)


@pytest.fixture
def one_maker(monkeypatch):
    """This scope resolves to exactly one live maker agent.

    Stubbed at `makers.makers_for_scope`, which is the seam `work_driver.worker_for`
    delegates to. It delegates deliberately: the first version of `worker_for`
    re-derived the answer itself by comparing `agents_registry`'s bare
    `project_slug` against a PREFIXED scope key, so it matched exactly one scope
    (`development`, whose key has no prefix) and refused every project. A test
    that stubbed the registry instead of this function would have passed over
    that bug, which is why the assertion in the sibling test is about the
    argv."""
    from services import makers, scope_execution

    # Both seams, so the whole of `worker_for` runs. A tmp_path tree resolves to
    # no scope at all, which is the EARLIER of the two refusals, and stubbing
    # only the maker lookup would leave the test failing for a different reason
    # than it appears to test.
    monkeypatch.setattr(scope_execution, "_scope_of", lambda root: "project:some-project")
    monkeypatch.setattr(
        makers, "makers_for_scope",
        lambda scope, **kw: [{"slug": "some-maker", "role": "maker"}])


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

    def test_start_passes_the_tree_to_the_watchdog(self, client, spawned, tree, one_maker):
        """The other half of the old bug: the watchdog was spawned bare, so it
        exited 2 on `assert_run_target ""` every single time.

        `one_maker` is required now, and that is not test scaffolding for its own
        sake: since dbdd324c the sweep is spawned with `--assignee`, because
        `tasks.next_ready(unattended=True)` selects only tickets assigned to the
        worker the sweep NAMES, so a sweep that names nobody selects nothing
        however full the queue is. `work_driver.ensure` therefore refuses to
        start a runner for a scope it cannot resolve a single live maker for.
        This test predates that and asserted the spawn happened regardless; it
        has been failing since, which is how a stale test reads as a broken
        feature. See the refusal test below."""
        assert client.post("/api/queue/runner/start", json={"dir": tree}).status_code == 200
        assert len(spawned) == 1
        argv = spawned[0]
        assert argv[0] == "bash"
        assert argv[1].endswith("queue_runner_watchdog.sh")
        assert argv[2] == tree
        # The assignee rides on the argv, because the flag being absent is what
        # made the whole loop a no-op: every sweep logged "unattended selection
        # refused: no worker was named".
        assert "--assignee" in argv
        assert argv[argv.index("--assignee") + 1] == "agent:some-maker"

    def test_start_refuses_a_scope_with_no_single_maker(self, client, spawned, tree):
        """A sweep that names no worker selects nothing, so starting one is a
        runner that reports itself healthy and does no work. That was the state
        the operator found on 2026-09-16 after releasing the brake, and the
        refusal is what replaced it. No maker, no runner."""
        assert client.post("/api/queue/runner/start", json={"dir": tree}).status_code == 200
        assert spawned == []

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


# --- the pidfile is per tree, like the lock ---------------------------------
# Start names a tree, so Stop has to as well. While the lock was per-tree and the
# pidfile was one shared file, two runners on two trees both ran but the second
# clobbered the first's pid: Stop killed whichever started last regardless of the
# tree it was asked about, and the other became unstoppable from the UI.

def test_pid_path_is_per_tree() -> None:
    a = autonomy_routes._queue_pid_path("/srv/tree-a")
    b = autonomy_routes._queue_pid_path("/srv/tree-b")
    assert a != b
    assert a.parent == b.parent


def test_pid_and_lock_share_one_tree_key() -> None:
    d = "/srv/tree-a"
    key = autonomy_routes._queue_key(d)
    assert key in autonomy_routes._queue_pid_path(d).name
    assert key in autonomy_routes._queue_lock_path(d).name


def test_the_key_matches_the_shell_derivation() -> None:
    """`printf '%s\\n' "$DIR" | md5sum | cut -c1-12`. If the two ever disagree,
    the API reports a runner that is not there and cannot stop the one that is."""
    import hashlib
    # Any path. Not this checkout's: check_no_hardcoding.sh fails the reviewer
    # gate on an absolute home path in tracked source, and the derivation is
    # what is under test, not the tree.
    d = "/srv/some/tree"
    assert autonomy_routes._queue_key(d) == hashlib.md5((d + "\n").encode()).hexdigest()[:12]


# --- the target has to be a tree ---------------------------------------------
# `assert_run_target` answers "is this tree PERMITTED" and correctly says nothing
# about whether the path is a working tree at all. But `dir` is free text from a
# form, and a sweep's first act is `tasks.next_ready(dir)`, which CREATES a task
# store in whatever directory it is handed. A typo therefore started a sweep
# against an arbitrary path and left `.tasks.json` and `TODO.md` behind in it.

class TestTheTargetMustBeATree:
    def test_a_bare_directory_is_refused(self, client, spawned, tmp_path):
        bare = tmp_path / "not-a-tree"
        bare.mkdir()
        r = client.post("/api/queue/runner/start", json={"dir": str(bare)})
        assert r.status_code == 400
        assert "task store" in r.text or "git worktree" in r.text
        assert not spawned, "refused, but a watchdog was spawned anyway"
        assert not (bare / ".tasks.json").exists(), "the refusal still created a store"

    def test_a_path_that_is_not_a_directory_is_refused(self, client, tmp_path):
        f = tmp_path / "a-file"
        f.write_text("x")
        r = client.post("/api/queue/runner/start", json={"dir": str(f)})
        assert r.status_code == 400
        assert "not a directory" in r.text

    def test_a_git_worktree_with_no_store_yet_is_allowed(self, client, spawned, tmp_path):
        """A fresh project bound to a repo has no `.tasks.json` until its first
        ticket. Refusing it would be wrong."""
        d = tmp_path / "fresh-repo"
        (d / ".git").mkdir(parents=True)
        r = client.post("/api/queue/runner/start", json={"dir": str(d)})
        assert r.status_code == 200, r.text


class TestStopNamesItsTree:
    def test_stop_without_a_dir_is_refused(self, client):
        """Runners are per tree. A missing `dir` used to resolve to the platform
        repo, so stopping a `projects/birds` runner without naming it killed the
        wrong one and reported success."""
        r = client.post("/api/queue/runner/stop")
        assert r.status_code == 400
        assert "per tree" in r.text

    def test_stop_with_a_dir_is_accepted(self, client, tree):
        assert client.post(f"/api/queue/runner/stop?dir={tree}").status_code == 200
