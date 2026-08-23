"""[B8] §11: telling a reader the tree moved under it.

The design is explicit about what this is NOT -- no invalidation protocol (the
stale read is already in the context window and cannot be recalled) and no new
channel (it is "a presence-driven steer"). So the tests below check two things
mostly: that the count is exact or absent, and that delivery goes through
`services/control.steer` and nothing else.
"""
from __future__ import annotations

import subprocess

import pytest

from services import reader_staleness as rs


def _repo(tmp_path):
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_path)}
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(a, cwd=repo, check=True, env=env,
                                    capture_output=True)
    run("git", "init", "-q", ".")
    (repo / "a.txt").write_text("one\n")
    run("git", "add", "-A")
    run("git", "commit", "-q", "-m", "one")
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=env,
                           capture_output=True, text=True).stdout.strip()
    return repo, first, run


class TestTheCount:
    def test_it_counts_the_files_that_changed(self, tmp_path):
        repo, first, run = _repo(tmp_path)
        (repo / "a.txt").write_text("two\n")
        (repo / "b.txt").write_text("new\n")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", "two")
        assert rs.files_changed_since(str(repo), first) == 2

    def test_an_unmoved_tree_counts_zero(self, tmp_path):
        repo, first, _ = _repo(tmp_path)
        assert rs.files_changed_since(str(repo), first) == 0

    def test_a_sha_this_tree_never_had_is_unknown_not_zero(self, tmp_path):
        # "Cannot say" and "nothing changed" are different answers and a
        # staleness message whose number is a guess teaches the model to
        # discount the next one.
        repo, _, _ = _repo(tmp_path)
        assert rs.files_changed_since(str(repo), "0" * 40) is None

    def test_no_sha_is_unknown(self, tmp_path):
        repo, _, _ = _repo(tmp_path)
        assert rs.files_changed_since(str(repo), None) is None

    def test_a_directory_that_is_not_a_repo_is_unknown(self, tmp_path):
        assert rs.files_changed_since(str(tmp_path), "0" * 40) is None


class TestTheMessage:
    def test_it_states_one_fact(self):
        m = rs.message(3, branch="wt/app/fix")
        assert "3 files changed" in m
        assert "wt/app/fix" in m

    def test_one_file_is_singular(self):
        assert "1 file changed" in rs.message(1)

    def test_it_does_not_instruct(self):
        # The harness's read-before-write check enforces the part that can be
        # enforced; this module does not invent policy for a turn it knows
        # nothing about.
        m = rs.message(2).lower()
        for word in ("you must", "please re-read", "you should"):
            assert word not in m


class TestDelivery:
    def test_it_steers_through_the_control_channel(self, tmp_path, monkeypatch):
        repo, first, run = _repo(tmp_path)
        (repo / "a.txt").write_text("two\n")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", "two")
        from services import control
        sent = {}

        def fake_steer(holder_id, text, *, actor=None, **kw):
            sent.update(holder_id=holder_id, text=text, actor=actor)
            return {"delivered": True}

        monkeypatch.setattr(control, "steer", fake_steer)
        out = rs.notify("chat-7", repo_path=str(repo), since_sha=first)
        assert out == {"delivered": True}
        assert sent["holder_id"] == "chat-7"
        assert "1 file changed" in sent["text"]

    def test_a_reader_that_is_not_stale_is_told_nothing(self, tmp_path,
                                                        monkeypatch):
        repo, first, _ = _repo(tmp_path)
        from services import control

        def explode(*a, **kw):  # pragma: no cover -- the point is it never runs
            raise AssertionError("steered a reader that was up to date")

        monkeypatch.setattr(control, "steer", explode)
        assert rs.notify("chat-7", repo_path=str(repo), since_sha=first) is None

    def test_an_unknown_diff_is_told_nothing(self, tmp_path, monkeypatch):
        repo, _, _ = _repo(tmp_path)
        from services import control
        monkeypatch.setattr(control, "steer",
                            lambda *a, **kw: pytest.fail("steered on a guess"))
        assert rs.notify("chat-7", repo_path=str(repo),
                         since_sha="0" * 40) is None

    def test_one_unreachable_reader_does_not_stop_the_rest(self, tmp_path,
                                                           monkeypatch):
        repo, first, run = _repo(tmp_path)
        (repo / "a.txt").write_text("two\n")
        run("git", "add", "-A")
        run("git", "commit", "-q", "-m", "two")
        from services import control

        def flaky(holder_id, text, **kw):
            if holder_id == "gone":
                raise RuntimeError("that session went away")
            return {"delivered": True, "holder_id": holder_id}

        monkeypatch.setattr(control, "steer", flaky)
        out = rs.notify_readers(
            [{"session_id": "gone", "since_sha": first},
             {"session_id": "chat-8", "since_sha": first},
             {"session_id": None, "since_sha": first}],
            repo_path=str(repo))
        assert [r["holder_id"] for r in out] == ["chat-8"]


def test_it_invents_no_protocol_of_its_own():
    """§11: the remedy is a message into the session, not an invalidation
    protocol and not a new channel. `control.steer` is the only delivery."""
    src = open(rs.__file__, encoding="utf-8").read()
    assert "control.steer" in src
    assert "NO INVALIDATION PROTOCOL" in src
