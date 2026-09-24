"""A thread's stored session still resumes after the cwd its turns run in moves.

The CLI files a session under `~/.claude/projects/<cwd-slug>/<id>.jsonl`, keyed
by the cwd IT sees, and `--resume` only looks in the current cwd's dir. Turns
moved twice (from the home dir to a per-user chat dir, then into the scoped
sandbox at /workspace), and each move left every existing thread pointing at a
session the CLI could no longer find. `session_alive` now copies such a session
into the dir the CLI will look in, so an upgraded install keeps its
conversations instead of silently starting each one over.
"""
import os

import pytest

from services import build_streaming, scoped_exec

SID = "0b6c3d0e-5a7f-4c56-9d1e-2f8a9b7c6d5e"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("WORKSHOP_TURN_USER", raising=False)
    return tmp_path


def _write(home, slug: str, body: str = '{"type":"user"}\n'):
    d = home / ".claude" / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{SID}.jsonl").write_text(body)
    return d


def test_scoped_turn_adopts_a_session_written_under_the_old_cwd(home, monkeypatch):
    monkeypatch.setattr(scoped_exec, "enabled", lambda: True)
    old = _write(home, "-home-someone", '{"old":true}\n')
    (old / SID / "subagents").mkdir(parents=True)
    (old / SID / "subagents" / "a.jsonl").write_text("x\n")

    assert build_streaming.session_alive(SID, "/some/chat/cwd")

    target = home / ".claude" / "projects" / "-workspace"
    assert (target / f"{SID}.jsonl").read_text() == '{"old":true}\n'
    assert (target / SID / "subagents" / "a.jsonl").exists()
    assert (old / f"{SID}.jsonl").exists(), "the original is left in place"


def test_unscoped_turn_adopts_into_its_own_cwd_dir(home, monkeypatch):
    monkeypatch.setattr(scoped_exec, "enabled", lambda: False)
    _write(home, "-home-someone")

    assert build_streaming.session_alive(SID, "/srv/chat")
    assert (home / ".claude" / "projects" / "-srv-chat" / f"{SID}.jsonl").exists()


def test_a_session_already_in_place_is_left_alone(home, monkeypatch):
    monkeypatch.setattr(scoped_exec, "enabled", lambda: True)
    here = _write(home, "-workspace", "current\n")
    _write(home, "-home-someone", "stale\n")

    assert build_streaming.session_alive(SID, "/any")
    assert (here / f"{SID}.jsonl").read_text() == "current\n"


def test_a_session_nowhere_on_disk_is_still_gone(home, monkeypatch):
    monkeypatch.setattr(scoped_exec, "enabled", lambda: True)
    assert not build_streaming.session_alive(SID, "/any")
    assert not os.path.exists(home / ".claude" / "projects" / "-workspace" / f"{SID}.jsonl")
