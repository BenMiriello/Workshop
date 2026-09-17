"""Layer 1: bwrap scoped execution. Proves the sandbox confines a command to its
scope root mounted at /workspace, and that OFF is a pure passthrough.

These run the real bwrap when it is present and skip cleanly otherwise, so the
suite is green on a host without user namespaces."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from services import scoped_exec

_HAS_BWRAP = shutil.which("bwrap") is not None


def test_zero_forces_off_passthrough(monkeypatch):
    # The escape hatch: `=0` forces the sandbox off even where it would work.
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")
    assert scoped_exec.wrap(["echo", "hi"], "/tmp") == ["echo", "hi"]


def test_default_is_on_where_functional(monkeypatch):
    # Confined by default: unset means on wherever the sandbox works. On a host
    # without user namespaces functional() is False and it degrades to passthrough,
    # so the assertion tracks the probe rather than a fixed answer.
    monkeypatch.delenv("WORKSHOP_SCOPED_EXEC", raising=False)
    wrapped = scoped_exec.wrap(["echo", "hi"], "/tmp")
    if scoped_exec.functional():
        assert wrapped[0].endswith("bwrap")
        assert "--chdir" in wrapped and scoped_exec.MOUNTPOINT in wrapped
    else:
        assert wrapped == ["echo", "hi"]


def test_empty_workdir_is_passthrough(monkeypatch):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    assert scoped_exec.wrap(["echo", "hi"], "") == ["echo", "hi"]


def test_no_die_with_parent(monkeypatch):
    # A turn is spawned detached to survive a backend restart; --die-with-parent
    # would kill the sandbox on restart and defeat that guarantee.
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    if not _HAS_BWRAP:
        pytest.skip("bwrap not installed")
    assert "--die-with-parent" not in scoped_exec.wrap(["echo", "hi"], "/tmp")


def _skip_if_sandbox_cannot_mount(r):
    """Skip when bwrap itself could not build the namespace here.

    These two tests EXECUTE bwrap, so they assert scoped_exec's behaviour only
    when the host can actually run it. That intent is already in the file --
    `skipif(not _HAS_BWRAP)` plus a skip on "namespace" in stderr -- but the
    stderr match was too narrow to cover the other way a container refuses.

    Measured 2026-09-01: both failed with

        bwrap: Can't bind mount /oldroot/<home>/.claude.json on
        /newroot/<home>/.claude.json: No such file or directory

    which is bwrap failing to SET UP, not scoped_exec computing the wrong argv.
    Proved environmental by checking out the pre-session scoped_exec.py and
    getting the identical failure, and by the source existing at wrap() time:
    the CLI rewrites that file by atomic rename, so a bind whose source is
    checked and then mounted can lose the race whenever the CLI is busy. The
    argv these tests really care about is asserted without executing anything by
    the tests around them, which still run here.

    A bwrap SETUP failure is skipped. A non-zero exit from the command INSIDE
    the sandbox is not, because that is the thing under test.
    """
    err = (r.stderr or "").lower()
    if r.returncode != 0 and ("namespace" in err or "bwrap:" in err):
        pytest.skip(f"bwrap cannot build a namespace here: {(r.stderr or '').strip()}")


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap not installed")
def test_scope_is_presented_at_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "inside.txt").write_text("in")

    cmd = scoped_exec.wrap(["sh", "-c", "pwd; cat /workspace/inside.txt"], str(scope))
    r = subprocess.run(cmd, cwd=str(scope), capture_output=True, text=True)
    _skip_if_sandbox_cannot_mount(r)
    assert r.returncode == 0, r.stderr
    assert "/workspace" in r.stdout
    assert "in" in r.stdout


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap not installed")
def test_scope_realpath_in_argv_is_remapped(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    (scope / "inside.txt").write_text("in")

    # A path under the scope root that appears in the LAUNCH argv is remapped to
    # the mountpoint, so the turn's own command resolves inside the sandbox (this
    # is what makes the MCP server's absolute host paths work). The real path is
    # not in the namespace; the remap is why referencing it still reads.
    real = scoped_exec.wrap(["cat", str(scope / "inside.txt")], str(scope))
    assert "/workspace/inside.txt" in real  # the arg was rewritten
    rr = subprocess.run(real, cwd=str(scope), capture_output=True, text=True)
    _skip_if_sandbox_cannot_mount(rr)
    assert rr.returncode == 0, rr.stderr
    assert "in" in rr.stdout


def test_outside_scope_is_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("out")

    # A sibling path outside the scope is not mounted and not under the root, so
    # it is neither remapped nor readable.
    out = scoped_exec.wrap(["cat", str(outside)], str(scope))
    assert str(outside) in out  # untouched: outside the root
    ro = subprocess.run(out, cwd=str(scope), capture_output=True, text=True)
    if "namespace" in (ro.stderr or "").lower():
        pytest.skip("user namespaces unavailable here")
    assert ro.returncode != 0


# --- The uid layer ([SEC-A1]): all argv-construction, no root and no real sudo ---


def _fake_account(monkeypatch, tmp_path, name="workshop-turn"):
    """A pwd.getpwnam() double plus the wrapper/sudo existence checks
    turn_user() makes, so these tests exercise real validation logic without
    a real system account -- which nothing running as this suite can create."""
    import pwd as _pwd

    home = tmp_path / "turn-home"
    home.mkdir()

    class _Entry:
        pw_name = name
        pw_dir = str(home)

    def fake_getpwnam(n):
        if n == name:
            return _Entry()
        raise KeyError(n)

    real_exists = os.path.exists
    real_which = shutil.which

    monkeypatch.setattr(_pwd, "getpwnam", fake_getpwnam)
    monkeypatch.setattr(scoped_exec.os.path, "exists",
                         lambda p: True if p == scoped_exec.TURN_WRAPPER else real_exists(p))
    monkeypatch.setattr(scoped_exec.shutil, "which",
                         lambda b: "/usr/bin/sudo" if b == "sudo" else real_which(b))
    monkeypatch.setattr(scoped_exec, "functional", lambda: True)
    return home


def test_turn_user_off_by_default(monkeypatch):
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    assert scoped_exec.turn_user() is None


def test_turn_user_missing_account_raises_rather_than_falls_back(monkeypatch):
    # Fail closed: this flag's whole point is that a broken precondition ends
    # the turn rather than silently spawning it as the host user.
    monkeypatch.setenv("WORKSHOP_TURN_USER", "no-such-workshop-turn-account")
    with pytest.raises(scoped_exec.TurnUserUnavailable):
        scoped_exec.turn_user()


def test_turn_user_valid_account_returns_the_name(monkeypatch, tmp_path):
    _fake_account(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKSHOP_TURN_USER", "workshop-turn")
    assert scoped_exec.turn_user() == "workshop-turn"


def test_turn_user_self_scope_carve_out(monkeypatch, tmp_path):
    """A turn scoped to the platform's own repo is NOT put under the uid
    layer even with a fully valid account configured: systemctl --user
    (which apply.sh needs) cannot cross a uid boundary."""
    _fake_account(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKSHOP_TURN_USER", "workshop-turn")
    from wsplatform import paths

    root = str(paths.workshop_root())
    assert scoped_exec.turn_user(root) is None
    # A sibling scope, not the repo root itself, is not exempt.
    assert scoped_exec.turn_user(str(tmp_path)) == "workshop-turn"


def test_spawn_argv_refuses_when_confinement_off(monkeypatch):
    # FAIL-CLOSED. With the sandbox forced off, a turn is REFUSED, never run
    # unconfined. spawn_argv is the turn-spawn boundary: the walls dropping is a
    # refusal, not a passthrough. (`wrap()` itself stays a no-op primitive -- see
    # the wrap passthrough tests above -- but no turn is spawned through it.)
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")
    with pytest.raises(scoped_exec.ConfinementUnavailable):
        scoped_exec.spawn_argv(["echo", "hi"], "/tmp")


def test_spawn_argv_refuses_on_empty_scope_root(monkeypatch):
    # No scope root means nothing to confine to, so the turn is refused rather
    # than run against the host filesystem.
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    with pytest.raises(scoped_exec.ConfinementUnavailable):
        scoped_exec.spawn_argv(["echo", "hi"], "")


def test_spawn_argv_wraps_in_sudo_with_the_bwrap_tail_unchanged(monkeypatch, tmp_path):
    _fake_account(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKSHOP_TURN_USER", "workshop-turn")
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    if not _HAS_BWRAP:
        pytest.skip("bwrap not installed")
    scope = tmp_path / "scope"
    scope.mkdir()

    plain = scoped_exec.wrap(["echo", "hi"], str(scope))
    wrapped = scoped_exec.spawn_argv(["echo", "hi"], str(scope))

    assert wrapped[:6] == ["/usr/bin/sudo", "-n", "-E", "-u", "workshop-turn", "--"]
    assert wrapped[6] == scoped_exec.TURN_WRAPPER
    assert wrapped[7] == "run"
    # The bwrap invocation itself is untouched by the uid layer: same argv
    # `wrap()` alone would have produced, just prefixed.
    assert wrapped[8:] == plain


def test_spawn_argv_raises_rather_than_silently_dropping_isolation(monkeypatch, tmp_path):
    # With bwrap absent the filesystem layer refuses first (ConfinementUnavailable),
    # which is also fail-closed; this test targets the uid layer's refusal, so it
    # needs the filesystem layer to actually confine.
    if not _HAS_BWRAP:
        pytest.skip("bwrap not installed; ConfinementUnavailable fires before the uid layer")
    monkeypatch.setenv("WORKSHOP_TURN_USER", "no-such-account")
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    with pytest.raises(scoped_exec.TurnUserUnavailable):
        scoped_exec.spawn_argv(["echo", "hi"], str(tmp_path))


def test_turn_env_swaps_home_and_drops_xdg(monkeypatch, tmp_path):
    _fake_account(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKSHOP_TURN_USER", "workshop-turn")
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    scope = tmp_path / "scope"
    scope.mkdir()

    base = {
        "HOME": os.path.expanduser("~"),
        "PATH": "/usr/bin:/bin",
        "XDG_RUNTIME_DIR": "/run/user/1003",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1003/bus",
        "CLAUDE_CODE_OAUTH_TOKEN": "keep-me",
    }
    out = scoped_exec.turn_env(dict(base), str(scope))
    assert out["HOME"] == str(tmp_path / "turn-home")
    assert "XDG_RUNTIME_DIR" not in out
    assert "DBUS_SESSION_BUS_ADDRESS" not in out
    assert out["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me"  # -E must carry this
    assert out["WORKSHOP_TURN_PATH"] == base["PATH"]  # secure_path workaround


def test_turn_env_noop_when_unconfined_and_uid_off(monkeypatch):
    # Neither layer active: env passes through untouched (same object).
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")  # confinement forced off
    env = {"HOME": os.path.expanduser("~"), "PATH": "/usr/bin"}
    assert scoped_exec.turn_env(env, "/tmp") is env


def test_turn_env_remaps_scope_paths_when_confined(monkeypatch):
    # Confined, uid layer off: env values pointing under the scope root are
    # rewritten to the mountpoint, so paths.workshop_root() and everything derived
    # from it resolve INSIDE the sandbox instead of at the absent host path. This
    # is the fix for the confined triager reading zero notes.
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")  # confinement forced on
    env = {"WORKSHOP_ROOT": "/scope/root",
           "WORKSHOP_NOTIFY_FILE": "/scope/root/.runtime/n.txt",
           "OUTSIDE": "/var/run/outside/.claude"}
    out = scoped_exec.turn_env(env, "/scope/root")
    assert out["WORKSHOP_ROOT"] == scoped_exec.MOUNTPOINT
    assert out["WORKSHOP_NOTIFY_FILE"] == scoped_exec.MOUNTPOINT + "/.runtime/n.txt"
    assert out["OUTSIDE"] == "/var/run/outside/.claude"  # outside the root, unchanged


def test_grants_under_the_uid_layer_never_bind_the_real_claude_dir(monkeypatch, tmp_path):
    """Only the two world-readable policy files may cross into the turn-user's
    home; the credentials-bearing directory itself must never appear as a
    grant once a different uid is going to run inside the sandbox."""
    _fake_account(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKSHOP_TURN_USER", "workshop-turn")
    scope = tmp_path / "scope"
    scope.mkdir()

    grants = scoped_exec._grants(str(scope))
    real_claude = os.path.join(os.path.expanduser("~"), ".claude")
    for mode, src, dest in grants:
        if src == real_claude:
            pytest.fail("the whole ~/.claude directory must not be a grant under the uid layer")
        if src.startswith(real_claude + "/"):
            assert src.rsplit("/", 1)[-1] in ("CLAUDE.md", "settings.json"), src


def test_grants_no_longer_bind_the_whole_xdg_runtime_dir(monkeypatch):
    """Regression for the live gap the review found: the old grant bound all
    of $XDG_RUNTIME_DIR read-write, which also contains docker.sock -- exactly
    what Layer 1's own acceptance criterion says a turn must not reach."""
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    grants = scoped_exec._grants(None)
    xdg = os.environ.get("XDG_RUNTIME_DIR") or "/run/user/0"
    for mode, src, dest in grants:
        assert src != xdg, "the bare XDG runtime dir must not be a whole-directory grant"


# --- linked git worktrees -------------------------------------------------
#
# A worktree confined alone is not a git repository: its `.git` is a FILE
# pointing at an absolute path in the parent repo. Measured 2026-08-22, every
# git command inside such a sandbox failed with "not a git repository", which
# would have left an unattended worker unable to commit its own work.

def _make_repo_with_worktree(tmp_path):
    """A real parent repo plus a linked worktree, or None if git is unusable."""
    if shutil.which("git") is None:
        return None
    parent = tmp_path / "parent"
    parent.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    run = lambda *a: subprocess.run(a, cwd=str(parent), env=env, capture_output=True)
    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (parent / "f.txt").write_text("hello\n")
    run("git", "add", "f.txt")
    run("git", "commit", "-qm", "init")
    wt = tmp_path / "wt"
    if run("git", "worktree", "add", "-q", str(wt), "-b", "side").returncode != 0:
        return None
    return parent, wt


def test_a_plain_repo_needs_no_extra_git_binds(tmp_path):
    """A normal checkout keeps its .git INSIDE the scope, so it comes along with
    the mountpoint bind and must not produce a redundant bind."""
    made = _make_repo_with_worktree(tmp_path)
    if made is None:
        pytest.skip("git unavailable")
    parent, _ = made
    assert scoped_exec._git_worktree_binds(str(parent)) == []


def test_a_linked_worktree_binds_the_parent_git_directory(tmp_path):
    made = _make_repo_with_worktree(tmp_path)
    if made is None:
        pytest.skip("git unavailable")
    parent, wt = made
    binds = scoped_exec._git_worktree_binds(str(wt))
    assert binds, "a linked worktree must bind the parent .git or it is not a repo"
    srcs = [os.path.realpath(s) for s, _ in binds]
    assert os.path.realpath(str(parent / ".git")) in srcs
    # Bound at its real path, so the absolute `gitdir:` pointer resolves.
    for src, dest in binds:
        assert src == dest


def test_a_non_repository_directory_gets_no_git_binds(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert scoped_exec._git_worktree_binds(str(plain)) == []


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap unavailable")
def test_git_works_inside_a_confined_linked_worktree(tmp_path, monkeypatch):
    """The behaviour the binds exist for: read HEAD and commit, confined."""
    made = _make_repo_with_worktree(tmp_path)
    if made is None:
        pytest.skip("git unavailable")
    _, wt = made
    monkeypatch.delenv("WORKSHOP_SCOPED_EXEC", raising=False)
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    if not scoped_exec.enabled():
        pytest.skip("scoped exec disabled on this host")
    argv = scoped_exec.wrap(
        ["sh", "-c",
         "git rev-parse --abbrev-ref HEAD && touch n && git add n && "
         "git -c user.email=t@t -c user.name=t commit -qm probe && echo COMMITTED"],
        str(wt),
    )
    out = subprocess.run(argv, cwd=str(wt), capture_output=True, text=True)
    if out.returncode != 0 and "bwrap" in (out.stderr or ""):
        pytest.skip(f"sandbox unavailable here: {out.stderr.strip()[:120]}")
    assert "side" in out.stdout, out.stderr
    assert "COMMITTED" in out.stdout, out.stderr


@pytest.mark.skipif(not _HAS_BWRAP, reason="bwrap unavailable")
def test_the_git_binds_come_after_the_mountpoint_bind(tmp_path):
    """Ordering is load-bearing: bwrap applies mounts in order, so a git bind
    emitted before the mountpoint bind is shadowed by it."""
    made = _make_repo_with_worktree(tmp_path)
    if made is None:
        pytest.skip("git unavailable")
    _, wt = made
    argv = scoped_exec.wrap(["true"], str(wt))
    if scoped_exec.MOUNTPOINT not in argv:
        pytest.skip("sandbox disabled on this host")
    mount_at = argv.index(scoped_exec.MOUNTPOINT)
    binds = scoped_exec._git_worktree_binds(str(wt))
    assert binds
    for src, _ in binds:
        assert argv.index(src) > mount_at


# ---- what a confined turn can SEE, without binding anything ----------------
# `host_path_visible` is the read-only complement of wrap(): it answers "would
# this host path still exist, at this spelling, inside a turn scoped here?".
# Written because the workshop MCP server is launched from the BACKEND's own
# interpreter and script, so a turn scoped to a project cannot exec it and the
# CLI drops it silently -- see services/chat_context._grants_workshop_tool.


def test_a_path_under_the_scope_root_is_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    root = tmp_path / "scope"
    (root / "sub").mkdir(parents=True)
    assert scoped_exec.host_path_visible(str(root / "sub"), str(root))
    assert scoped_exec.host_path_visible(str(root), str(root))


def test_a_path_outside_the_scope_root_is_not(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    root = tmp_path / "scope"
    root.mkdir()
    sibling = tmp_path / "elsewhere"
    sibling.mkdir()
    assert not scoped_exec.host_path_visible(str(sibling), str(root))
    # The near miss that a prefix compare gets wrong: a sibling whose name
    # STARTS with the root's.
    twin = tmp_path / "scope-2"
    twin.mkdir()
    assert not scoped_exec.host_path_visible(str(twin), str(root))


def test_the_system_binds_are_visible_because_wrap_binds_them(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    root = tmp_path / "scope"
    root.mkdir()
    assert scoped_exec.host_path_visible("/usr/bin/env", str(root))


def test_no_scope_root_means_nothing_is_visible(monkeypatch):
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "1")
    assert not scoped_exec.host_path_visible("/usr/bin/env", None)
    assert not scoped_exec.host_path_visible("", "/tmp")


def test_off_means_the_real_filesystem(monkeypatch, tmp_path):
    # An unconfined turn genuinely sees the host, so the answer is just "does
    # it exist" -- and a missing path is still missing.
    monkeypatch.setenv("WORKSHOP_SCOPED_EXEC", "0")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert scoped_exec.host_path_visible(str(outside), str(tmp_path / "scope"))
    assert not scoped_exec.host_path_visible(str(tmp_path / "nope"), str(tmp_path))
