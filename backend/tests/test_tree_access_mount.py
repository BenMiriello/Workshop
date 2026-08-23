"""[B1] The mount follows the lease.

`docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md` §4:
holding the worktree lease means the tree is mounted read-write; not holding it
means the SAME tree is mounted read-only.

The first class here is the one that matters most and it is not about the
feature at all: `services/scoped_exec.py` is what every live turn spawns inside,
including the operator's own chat, so the flag-off path has to be provably
unchanged rather than argued to be. It asserts on the constructed argv and env,
which is the thing that actually reaches `execve`.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from services import scoped_exec, tree_access

FLAG = "WORKSHOP_TREE_LEASE_MOUNT"

CMD = ["claude", "-p", "--output-format", "stream-json", "--model", "opus"]
ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": os.path.expanduser("~"),
    "WORKSHOP_ROOT": "/srv/scope",
    "WORKSHOP_RUNTIME": "/srv/scope/.runtime",
    "CLAUDE_CODE_OAUTH_TOKEN": "redacted",
}

needs_bwrap = pytest.mark.skipif(
    not scoped_exec.functional(),
    reason="bwrap cannot create a user namespace on this host")


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    """Every test states its own flag value. Nothing inherits the host's."""
    monkeypatch.delenv(FLAG, raising=False)


def _lease(holder_id: str, *, state: str = "held", kind: str = "chat") -> dict:
    return {"id": "l1", "subject_kind": "worktree", "subject_id": "s1",
            "holder_kind": kind, "holder_id": holder_id, "generation": 3,
            "state": state, "expires_at": None}


@pytest.fixture
def no_records(monkeypatch):
    """A reachable worktree record store that has no record for this tree, so
    the subject id is the tree's realpath. Patched rather than skipped: the
    suite must answer the mount question without a database."""
    from services import worktree_records
    monkeypatch.setattr(worktree_records, "list_records", lambda **kw: [])


class TestTheFlagOffPathIsUnchanged:
    """With the flag off the spawn path must be BYTE-EQUIVALENT to today's."""

    def test_argv_is_identical_with_and_without_a_session_id(self, tmp_path):
        # Passing `session_id` is what build_streaming now does on every turn.
        # While the flag is off it must make no difference whatsoever.
        base = scoped_exec.wrap(list(CMD), str(tmp_path))
        with_session = scoped_exec.wrap(list(CMD), str(tmp_path),
                                        session_id="chat-1", holder_kind="chat")
        assert base == with_session

    def test_the_scope_is_bound_read_write(self, tmp_path):
        argv = scoped_exec.wrap(list(CMD), str(tmp_path), session_id="chat-1")
        i = argv.index(scoped_exec.MOUNTPOINT)
        assert argv[i - 2] == "--bind"
        assert argv[i - 1] == os.path.realpath(str(tmp_path))

    def test_explicit_zero_is_also_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv(FLAG, "0")
        argv = scoped_exec.wrap(list(CMD), str(tmp_path), session_id="chat-1")
        assert "--ro-bind" not in argv[argv.index(scoped_exec.MOUNTPOINT) - 2]

    def test_a_typo_in_the_flag_is_off(self, tmp_path, monkeypatch):
        # The failure mode of a misread flag is every turn in the system losing
        # write access at once, so anything that is not an explicit yes is no.
        monkeypatch.setenv(FLAG, "yes-please")
        assert scoped_exec.tree_lease_mounts() is False

    def test_the_lease_layer_is_not_even_consulted(self, tmp_path, monkeypatch):
        """No import, no database, no lease read on the default path."""
        def explode(*a, **kw):  # pragma: no cover -- the point is it never runs
            raise AssertionError("the lease layer was consulted with the flag off")

        monkeypatch.setattr(tree_access, "decide", explode)
        argv = scoped_exec.wrap(list(CMD), str(tmp_path), session_id="chat-1")
        # Not just "it did not raise": the seam catches exceptions and turns
        # them into a read-only mount, so a swallowed AssertionError would look
        # like a pass. The scope still being read-WRITE is what says the lease
        # layer was never reached.
        assert argv[argv.index(scoped_exec.MOUNTPOINT) - 2] == "--bind"

    def test_turn_env_is_unchanged(self, tmp_path):
        env = scoped_exec.turn_env(dict(ENV), str(tmp_path))
        assert env["WORKSHOP_ROOT"] == "/srv/scope"  # not under the scope root

    def test_decide_says_the_flag_is_off(self, tmp_path):
        d = tree_access.decide(str(tmp_path), session_id="chat-1")
        assert d.flag_on is False and d.mode == "rw"


class TestTheMountFollowsTheLease:
    def test_no_lease_is_read_only(self, tmp_path, monkeypatch, no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        monkeypatch.setattr(leases, "get", lambda *a, **kw: None)
        argv = scoped_exec.wrap(list(CMD), str(tmp_path), session_id="chat-1")
        i = argv.index(scoped_exec.MOUNTPOINT)
        assert argv[i - 2] == "--ro-bind"

    def test_the_holder_gets_read_write(self, tmp_path, monkeypatch, no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        monkeypatch.setattr(leases, "get", lambda *a, **kw: _lease("chat-1"))
        argv = scoped_exec.wrap(list(CMD), str(tmp_path), session_id="chat-1")
        i = argv.index(scoped_exec.MOUNTPOINT)
        assert argv[i - 2] == "--bind"

    def test_someone_elses_lease_is_read_only(self, tmp_path, monkeypatch,
                                              no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        monkeypatch.setattr(leases, "get", lambda *a, **kw: _lease("chat-2"))
        d = tree_access.decide(str(tmp_path), session_id="chat-1")
        assert d.read_only and "another session" in d.reason

    def test_an_expired_lease_is_read_only(self, tmp_path, monkeypatch,
                                           no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        monkeypatch.setattr(leases, "get",
                            lambda *a, **kw: _lease("chat-1", state="expired"))
        assert tree_access.decide(str(tmp_path), session_id="chat-1").read_only

    def test_a_turn_with_no_session_id_reads(self, tmp_path, monkeypatch,
                                             no_records):
        monkeypatch.setenv(FLAG, "1")
        d = tree_access.decide(str(tmp_path), session_id=None)
        assert d.read_only

    def test_the_worktree_record_id_is_the_subject_when_there_is_one(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(FLAG, "1")
        from services import leases, worktree_records
        monkeypatch.setattr(
            worktree_records, "list_records",
            lambda **kw: [{"id": "wt-uuid", "path": str(tmp_path)}])
        seen = {}

        def fake_get(kind, sid):
            seen["kind"], seen["sid"] = kind, sid
            return _lease("chat-1")

        monkeypatch.setattr(leases, "get", fake_get)
        assert tree_access.decide(str(tmp_path), session_id="chat-1").mode == "rw"
        assert seen == {"kind": "worktree", "sid": "wt-uuid"}

    def test_the_realpath_is_the_subject_when_there_is_no_record(
            self, tmp_path, monkeypatch, no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        seen = {}

        def fake_get(kind, sid):
            seen["sid"] = sid
            return None

        monkeypatch.setattr(leases, "get", fake_get)
        tree_access.decide(str(tmp_path), session_id="chat-1")
        assert seen["sid"] == os.path.realpath(str(tmp_path))


class TestABrokenPreconditionFailsClosed:
    """§4's enforcement is worth nothing if "I could not tell" means read-write.
    There is no path below that produces `rw` without an affirmative answer."""

    def test_an_unreadable_lease_store_is_read_only(self, tmp_path, monkeypatch,
                                                    no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases

        def unreadable(*a, **kw):
            raise leases.LeasesUnavailable("no relational store")

        monkeypatch.setattr(leases, "get", unreadable)
        d = tree_access.decide(str(tmp_path), session_id="chat-1")
        assert d.mode == "ro" and "lease store" in d.reason

    def test_an_unreadable_record_store_is_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv(FLAG, "1")
        from services import worktree_records

        def unreadable(**kw):
            raise worktree_records.WorktreesUnavailable("no relational store")

        monkeypatch.setattr(worktree_records, "list_records", unreadable)
        d = tree_access.decide(str(tmp_path), session_id="chat-1")
        assert d.mode == "ro"

    def test_an_unexpected_exception_is_read_only_not_a_crash(
            self, tmp_path, monkeypatch, no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases

        def boom(*a, **kw):
            raise RuntimeError("something nobody predicted")

        monkeypatch.setattr(leases, "get", boom)
        assert tree_access.decide(str(tmp_path), session_id="chat-1").mode == "ro"

    def test_the_wrap_seam_swallows_an_import_failure_as_read_only(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(FLAG, "1")

        def boom(*a, **kw):
            raise ImportError("no lease layer on this checkout")

        monkeypatch.setattr(tree_access, "decide", boom)
        argv = scoped_exec.wrap(list(CMD), str(tmp_path), session_id="chat-1")
        i = argv.index(scoped_exec.MOUNTPOINT)
        assert argv[i - 2] == "--ro-bind"

    def test_no_reachable_input_ever_yields_read_write_without_a_lease(
            self, tmp_path, monkeypatch, no_records):
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        for lease in (None, _lease("chat-2"), _lease("chat-1", state="expired"),
                      {"state": "held", "holder_id": None},
                      {"holder_id": "chat-1"}):
            monkeypatch.setattr(leases, "get", lambda *a, **kw: lease)
            assert tree_access.decide(str(tmp_path),
                                      session_id="chat-1").mode == "ro"


class TestTheGitMetadataFollowsTheMount:
    """A worktree's objects and refs live in the parent `.git`. Leaving that
    read-write under a read-only checkout would leave a reader able to commit
    and move refs, which is not the exclusion §4 describes."""

    def _linked_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "i"],
                       cwd=repo, check=True, env=env)
        wt = tmp_path / "wt"
        subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "b"],
                       cwd=repo, check=True, env=env)
        return repo, wt

    def test_read_only_binds_the_common_dir_read_only(self, tmp_path,
                                                      monkeypatch, no_records):
        repo, wt = self._linked_worktree(tmp_path)
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        monkeypatch.setattr(leases, "get", lambda *a, **kw: None)
        argv = scoped_exec.wrap(list(CMD), str(wt), session_id="chat-1")
        gitdir = os.path.realpath(str(repo / ".git"))
        assert gitdir in argv
        assert argv[argv.index(gitdir) - 1] == "--ro-bind"

    def test_the_holder_gets_it_read_write(self, tmp_path, monkeypatch,
                                           no_records):
        repo, wt = self._linked_worktree(tmp_path)
        monkeypatch.setenv(FLAG, "1")
        from services import leases
        monkeypatch.setattr(leases, "get", lambda *a, **kw: _lease("chat-1"))
        argv = scoped_exec.wrap(list(CMD), str(wt), session_id="chat-1")
        gitdir = os.path.realpath(str(repo / ".git"))
        assert argv[argv.index(gitdir) - 1] == "--bind"


@needs_bwrap
class TestTheKernelActuallyEnforcesIt:
    """The argv assertions above say what we asked for. These say what the
    kernel did with it, which is the only thing §4's "enforced by the OS rather
    than by cooperation" can mean."""

    def _run(self, tmp_path, monkeypatch, lease):
        monkeypatch.setenv(FLAG, "1")
        from services import leases, worktree_records
        monkeypatch.setattr(worktree_records, "list_records", lambda **kw: [])
        monkeypatch.setattr(leases, "get", lambda *a, **kw: lease)
        # NO GRANTS. The grant set is `~/.claude`, the systemd socket and the
        # CLI root, none of which this probe needs and any of which can be
        # unbindable on a host that is itself already inside a namespace (a bind
        # mount cannot be re-bound from within a nested sandbox, which is how
        # this suite is often run). Dropping them keeps the thing under test --
        # the mountpoint bind that `wrap()` chose -- exactly as it is.
        monkeypatch.setattr(scoped_exec, "_grants", lambda root=None: [])
        argv = scoped_exec.wrap(
            ["/bin/sh", "-c", "echo written > /workspace/probe.txt"],
            str(tmp_path), session_id="chat-1")
        return subprocess.run(argv, capture_output=True, text=True, timeout=60)

    def test_a_write_without_the_lease_fails_erofs(self, tmp_path, monkeypatch):
        proc = self._run(tmp_path, monkeypatch, None)
        assert proc.returncode != 0
        assert tree_access.detect_erofs(proc.stderr), proc.stderr
        assert not (tmp_path / "probe.txt").exists()

    def test_a_write_with_the_lease_succeeds(self, tmp_path, monkeypatch):
        proc = self._run(tmp_path, monkeypatch, _lease("chat-1"))
        assert proc.returncode == 0, proc.stderr
        assert (tmp_path / "probe.txt").read_text().strip() == "written"


class TestTheErofsEventBecomesAnOffer:
    """§4.2 and §10.3: the failed write is caught and turned into an OFFER whose
    default is a new branch. Never a crash, and never an automatic join."""

    def test_it_recognises_the_message(self):
        assert tree_access.detect_erofs(
            "sh: 1: cannot create /workspace/x: Read-only file system")
        assert tree_access.detect_erofs("OSError: [Errno 30] Read-only file system")
        assert not tree_access.detect_erofs("File not found")
        assert not tree_access.detect_erofs(None)

    def test_it_finds_the_blocked_path(self):
        assert tree_access.erofs_path(
            "cannot create /workspace/backend/x.py: Read-only file system"
        ) == "/workspace/backend/x.py"

    def test_the_default_is_a_new_branch_even_with_branches_to_join(self):
        offer = tree_access.first_write_offer(
            "Read-only file system", current_ref="main",
            existing_branches=["wt/a/fix", "wt/a/other"])
        assert offer["default"]["action"] == "new_branch"
        assert offer["default"]["base_ref"] == "main"
        assert [o["branch"] for o in offer["options"]] == ["wt/a/fix", "wt/a/other"]
        assert offer["automatic"] is False
        assert offer["joined_automatically"] is False

    def test_a_non_erofs_error_produces_nothing(self):
        assert tree_access.first_write_offer("Tool call failed",
                                             current_ref="main") is None

    def test_it_carries_the_path_that_was_blocked(self):
        offer = tree_access.first_write_offer(
            "cannot create /workspace/a.txt: Read-only file system",
            current_ref="main")
        assert offer["blocked_path"] == "/workspace/a.txt"


class TestTheTurnLoopOffersRatherThanCrashes:
    """The seam in `build_streaming`: an errored tool result carrying EROFS
    pushes ONE `write_blocked` event and never fails the turn."""

    def _turn(self):
        from services import build_streaming
        return build_streaming.Turn(id="t1", thread_id="chat-1",
                                    agent_slug="maker")

    def test_nothing_happens_with_the_flag_off(self, monkeypatch):
        from services import build_streaming
        turn = self._turn()
        build_streaming._maybe_write_blocked(
            turn, {}, {"text": "Read-only file system", "is_error": True})
        assert turn.write_offer is None
        assert not [e for e in turn.events if e.kind == "write_blocked"]

    def test_one_offer_per_turn(self, monkeypatch):
        from services import build_streaming
        monkeypatch.setenv(FLAG, "1")
        turn = self._turn()
        payload = {"text": "cannot create /workspace/a: Read-only file system",
                   "is_error": True}
        build_streaming._maybe_write_blocked(turn, {"branch": "main"}, payload)
        build_streaming._maybe_write_blocked(turn, {"branch": "main"}, payload)
        events = [e for e in turn.events if e.kind == "write_blocked"]
        assert len(events) == 1
        assert events[0].payload["default"]["action"] == "new_branch"
        assert turn.write_offer["blocked_path"] == "/workspace/a"

    def test_a_broken_offer_does_not_break_the_turn(self, monkeypatch):
        from services import build_streaming
        monkeypatch.setenv(FLAG, "1")

        def boom(*a, **kw):
            raise RuntimeError("no")

        monkeypatch.setattr(tree_access, "first_write_offer", boom)
        turn = self._turn()
        build_streaming._maybe_write_blocked(
            turn, {}, {"text": "Read-only file system", "is_error": True})
        assert turn.write_offer is None


def test_the_cli_takes_a_session_id(monkeypatch, tmp_path):
    """`scripts/ralph.sh` reaches this seam from bash, so the lease layer has to
    be addressable from there too."""
    monkeypatch.delenv(FLAG, raising=False)
    captured = {}

    def fake_exec(path, argv, env):
        captured["argv"] = argv
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_exec)
    with pytest.raises(SystemExit):
        scoped_exec._exec_main(["exec", "--workdir", str(tmp_path),
                                "--session-id", "run-9", "--", "/bin/true"])
    assert captured["argv"][0].endswith("bwrap")
    # Flag off: the scope is still bound read-write.
    assert "--ro-bind" != captured["argv"][
        captured["argv"].index(scoped_exec.MOUNTPOINT) - 2]


def test_the_module_does_not_claim_the_token_protects_the_tree():
    """§4 is explicit that the lease is bookkeeping and the mount is
    enforcement, and that getting it backwards is the error the section exists
    to correct. This is the one comment claim worth asserting on."""
    src = open(tree_access.__file__, encoding="utf-8").read()
    assert "THE MOUNT IS ENFORCEMENT" in src
    assert "Nothing in `services/leases.py` protects a tree" in src
