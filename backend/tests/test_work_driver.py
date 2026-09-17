"""The reconciler between execution intent and the resident worker.

The behaviour being pinned is the one that was missing entirely: arming a scope
must produce a runner, and it must keep producing one after the runner dies,
because a detached process dies with a reboot and nothing was bringing it back.
"""
from __future__ import annotations

import json
import os

import pytest

from services import scope_execution, work_driver


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the intent store at a throwaway root so the live armed set is
    untouched by these tests."""
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    work_driver._failed_at.clear()
    yield tmp_path
    work_driver._failed_at.clear()


def test_armed_set_is_empty_by_default(store):
    """Fail-closed: absent file => nothing armed => the driver starts nothing."""
    assert scope_execution.armed_scopes() == []
    assert scope_execution.armed_roots() == []


def test_armed_scopes_reports_mode_and_drops_disarmed(store):
    scope_execution.set_mode("project:a", "supervised")
    scope_execution.set_mode("project:b", "autonomous")
    assert dict(scope_execution.armed_scopes()) == {
        "project:a": "supervised", "project:b": "autonomous",
    }
    scope_execution.set_mode("project:a", "reactive")
    assert dict(scope_execution.armed_scopes()) == {"project:b": "autonomous"}


def test_armed_roots_resolves_each_scope_in_THIS_namespace(store, tmp_path, monkeypatch):
    """THE BUG THIS REPLACES. The intent used to be keyed by the scope's root
    path, but a confined chat turn sees its scope root at the mountpoint
    (/workspace) while the backend sees the real location. Arming `development`
    from a turn wrote "/workspace", and the runner the backend then started for
    it logged `project not found or no dir: /workspace`. The scope name is the
    stable identity; the root is resolved per namespace, here."""
    from services import task_scope

    here = tmp_path / "here"; here.mkdir()
    monkeypatch.setattr(task_scope, "root_for_context",
                        lambda k: here if k == "project:a" else None)
    scope_execution.set_mode("project:a", "supervised")
    scope_execution.set_mode("project:gone", "autonomous")

    # project:gone does not resolve HERE, so it is skipped rather than guessed at.
    assert scope_execution.armed_roots() == [(str(here), "supervised")]


def test_a_legacy_path_key_is_folded_onto_its_scope(store, monkeypatch):
    """The store on disk had "/workspace": the read-time migration maps it."""
    from services import task_scope

    monkeypatch.setattr(task_scope, "key_for_root",
                        lambda r: "development" if str(r) == "/workspace" else None)
    scope_execution._write({"/workspace": "supervised"})
    assert scope_execution.armed_scopes() == [("development", "supervised")]
    # And it is PERSISTED, so a reader in another namespace (where "/workspace"
    # resolves to nothing) still sees the scope armed instead of an empty set.
    # A read-only fold silently disarmed the scope for the backend; measured.
    assert json.loads(scope_execution._store_path().read_text()) == {
        "development": "supervised"}


def test_a_partial_fold_is_not_written_back(store, monkeypatch):
    """Writing away a key THIS namespace cannot translate would destroy the one
    another namespace still can. Fold in memory, leave the file alone."""
    from services import task_scope

    monkeypatch.setattr(task_scope, "key_for_root", lambda r: None)
    scope_execution._write({"/workspace": "supervised"})
    assert scope_execution.armed_scopes() == []
    assert json.loads(scope_execution._store_path().read_text()) == {
        "/workspace": "supervised"}


def test_supervision_flag_follows_the_stage_ceiling():
    """supervised stops at in_review (no flag, the runner's default);
    autonomous may reach done (--unsupervised). One mapping, not two."""
    assert work_driver._runner_flags("supervised") == []
    assert work_driver._runner_flags("autonomous") == ["--unsupervised"]


def test_reconcile_starts_a_runner_for_each_armed_scope(store, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.setattr(scope_execution, "armed_roots",
                        lambda: [(str(a), "supervised"), (str(b), "autonomous")])

    started: list[tuple[str, str]] = []
    monkeypatch.setattr(work_driver, "is_resident", lambda d: False)
    monkeypatch.setattr(work_driver, "ensure",
                        lambda root, mode="supervised": started.append(
                            (os.path.realpath(root), mode)) or True)
    monkeypatch.setattr(work_driver.autonomy_override, "is_engaged", lambda: False)

    out = work_driver.reconcile()
    assert out == {"armed": 2, "started": 2}
    assert dict(started) == {
        os.path.realpath(a): "supervised",
        os.path.realpath(b): "autonomous",
    }


def test_reconcile_is_idempotent_when_the_runner_is_already_resident(store, tmp_path,
                                                                    monkeypatch):
    a = tmp_path / "a"; a.mkdir()
    monkeypatch.setattr(scope_execution, "armed_roots", lambda: [(str(a), "supervised")])
    calls: list[str] = []
    monkeypatch.setattr(work_driver, "is_resident", lambda d: True)
    monkeypatch.setattr(work_driver, "ensure",
                        lambda root, mode="supervised": calls.append(root) or True)
    monkeypatch.setattr(work_driver.autonomy_override, "is_engaged", lambda: False)

    assert work_driver.reconcile() == {"armed": 1, "started": 0}
    assert calls == []


def test_the_global_brake_stops_the_driver_starting_anything(store, tmp_path,
                                                             monkeypatch):
    """The override is above every scope. It does not KILL a running loop:
    queue_runner.sh re-reads the brake each iteration and idles, so releasing it
    must not require a restart."""
    a = tmp_path / "a"; a.mkdir()
    monkeypatch.setattr(scope_execution, "armed_roots", lambda: [(str(a), "supervised")])
    calls: list[str] = []
    monkeypatch.setattr(work_driver, "is_resident", lambda d: False)
    monkeypatch.setattr(work_driver, "ensure",
                        lambda root, mode="supervised": calls.append(root) or True)
    monkeypatch.setattr(work_driver.autonomy_override, "is_engaged", lambda: True)

    out = work_driver.reconcile()
    assert out["started"] == 0
    assert out["skipped"] == "autonomy override engaged"
    assert calls == []


def test_reconcile_leaves_an_unarmed_tree_alone(store, tmp_path, monkeypatch):
    """Additive only: a runner nobody armed is either a hand-started debug sweep
    or a just-disarmed scope, and killing it here would fight the operator."""
    a = tmp_path / "a"; a.mkdir()
    monkeypatch.setattr(work_driver, "is_resident", lambda d: True)
    monkeypatch.setattr(work_driver.autonomy_override, "is_engaged", lambda: False)
    stopped: list[str] = []
    monkeypatch.setattr(work_driver, "stop", lambda root: stopped.append(root))

    assert work_driver.reconcile() == {"armed": 0, "started": 0}
    assert stopped == []


def test_a_start_that_does_not_take_backs_off(store, tmp_path, monkeypatch):
    """The dev-mode gate refuses some trees outright. Retrying every tick would
    write a refusal into queue-runner.log once a minute forever."""
    a = tmp_path / "a"; a.mkdir()
    spawns: list[list[str]] = []

    monkeypatch.setattr(work_driver, "is_resident", lambda d: False)
    monkeypatch.setattr(work_driver.QUEUE_WATCHDOG.__class__, "exists", lambda self: True)
    # A tmp_path tree is no scope and so has no maker agent. This test is about
    # the BACKOFF after a spawn that did not take, so the worker lookup is
    # stubbed to the answer a real armed scope gives; the refusal when there is
    # no worker is its own test below.
    monkeypatch.setattr(work_driver, "worker_for", lambda root: "agent:test-maker")
    monkeypatch.setattr(work_driver.subprocess, "Popen",
                        lambda cmd, **kw: spawns.append(cmd))
    monkeypatch.setattr(work_driver.time, "sleep", lambda s: None)

    assert work_driver.ensure(a, "supervised") is False
    assert len(spawns) == 1
    # Second call inside the backoff window does not spawn again.
    assert work_driver.ensure(a, "supervised") is False
    assert len(spawns) == 1


def test_the_runner_is_told_which_worker_to_run_as(store, tmp_path, monkeypatch):
    """The sweep must NAME a worker or it selects nothing, so `ensure` passes one.

    THE FAILURE THIS PINS ACTUALLY HAPPENED, 2026-09-16. `project_ralph.sh` has
    always taken `--assignee agent:<slug>` and `queue_runner.sh` had no such
    flag, so every resident sweep ran with assignee=None. `next_ready`'s
    unattended rule matches only tickets assigned to the worker the sweep names
    and treats an unnamed sweep as naming nobody, so it refused all 80 ready
    tickets, once every 72 seconds, while the loop reported itself healthy. The
    operator released the autonomy brake and watched nothing happen.
    """
    a = tmp_path / "a"; a.mkdir()
    spawns: list[list[str]] = []

    monkeypatch.setattr(work_driver, "is_resident", lambda d: False)
    monkeypatch.setattr(work_driver.QUEUE_WATCHDOG.__class__, "exists", lambda self: True)
    monkeypatch.setattr(work_driver, "worker_for", lambda root: "agent:demo-maker")
    monkeypatch.setattr(work_driver.subprocess, "Popen",
                        lambda cmd, **kw: spawns.append(cmd))
    monkeypatch.setattr(work_driver.time, "sleep", lambda s: None)

    work_driver.ensure(a, "supervised")
    assert len(spawns) == 1
    cmd = spawns[0]
    assert "--assignee" in cmd, "the sweep must be told who it runs as"
    assert cmd[cmd.index("--assignee") + 1] == "agent:demo-maker"


def test_a_scope_with_no_worker_starts_no_runner(store, tmp_path, monkeypatch):
    """No worker means every sweep would select nothing, so do not start one.

    A resident runner that can never claim a ticket is worse than no runner: it
    holds the tree's lock, reports itself healthy to the API, and writes its
    refusal only into a log. That combination is what made the original bug
    survive, so the absence of a worker has to be refused where it is visible
    rather than absorbed into a loop.
    """
    a = tmp_path / "a"; a.mkdir()
    spawns: list[list[str]] = []

    monkeypatch.setattr(work_driver, "is_resident", lambda d: False)
    monkeypatch.setattr(work_driver.QUEUE_WATCHDOG.__class__, "exists", lambda self: True)
    monkeypatch.setattr(work_driver, "worker_for", lambda root: None)
    monkeypatch.setattr(work_driver.subprocess, "Popen",
                        lambda cmd, **kw: spawns.append(cmd))
    monkeypatch.setattr(work_driver.time, "sleep", lambda s: None)

    assert work_driver.ensure(a, "supervised") is False
    assert spawns == []


def test_lock_and_pid_keys_match_the_shell_derivation(tmp_path):
    """The runner's flock is the ONLY authority on residency, so the Python and
    shell derivations must agree byte for byte or the API reports a loop that
    isn't there. Shell: md5 of `printf '%s\\n' "$DIR"`, first 12 hex chars.

    The lock MUST live under .runtime (bind-mounted into confined bwrap turns),
    not /tmp (private tmpfs per namespace). The shell derivation uses
    $WORKSHOP/.runtime/queue-runner-$KEY.lock; Python must match exactly."""
    import hashlib
    d = "/some/tree"
    expect = hashlib.md5((d + "\n").encode()).hexdigest()[:12]
    assert work_driver.queue_key(d) == expect
    lock = work_driver.lock_path(d)
    assert str(lock).endswith(f"queue-runner-{expect}.lock")
    assert lock.parent.name == ".runtime", "lock must be in .runtime, not /tmp"
    assert str(work_driver.pid_path(d)).endswith(f"queue-runner-{expect}.pid")


def test_is_resident_reads_runtime_lock(tmp_path, monkeypatch):
    """Lock in .runtime (bind-mounted into confined turns) is visible across
    bwrap namespace boundaries. /tmp is a private tmpfs — a runner started in
    one namespace and the backend in another would both acquire their own /tmp
    lock and both sweep. Moving to .runtime makes one holder block the other.

    Demonstrated: hold the lock here (simulating the backend runner), then call
    is_resident (simulating a chat turn in a different namespace) — it returns
    True, so the chat-turn driver would NOT start a duplicate."""
    import fcntl as _fcntl

    runtime = tmp_path / ".runtime"
    runtime.mkdir()
    monkeypatch.setattr(work_driver, "WORKSHOP_ROOT", tmp_path)

    d = str(tmp_path / "tree")
    lock = work_driver.lock_path(d)

    # Lock is under .runtime, visible across namespaces that share the mount.
    assert lock.parent == runtime
    assert lock.parent.name == ".runtime"

    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        # is_resident probes the same path; lock held here => returns True
        assert work_driver.is_resident(d)
    finally:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)

    # After release, available again.
    assert not work_driver.is_resident(d)
