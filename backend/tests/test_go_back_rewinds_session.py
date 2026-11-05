"""Going back to a point the CLI session recorded RESUMES the session there.

Measured 2026-10-28: a cancelled send went back one message and the chat's
context meter fell from about 40% to 5%, because going back detached the session
and the next turn started over from the visible text. Every tool result, file
read and the prompt cache went with it. A reply now records where the session
stood when it ended (`meta.cli`), and going back to it is
`--resume-session-at <that entry>`: same session, nothing after the point.
"""
import asyncio
import json

import pytest

from routes.chat import ForkThread
from services import build_streaming, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        t = chat_store.create_thread("general")
        chat_store.append_message(t["id"], "user", "the plan")
        chat_store.ensure_thread_session(t["id"])
        yield chat_store.get_thread(t["id"])
    finally:
        ownership.clear_current_owner()


async def _async(value):
    return value


def _fork(monkeypatch, tid, rewind):
    import routes.chat as rc
    from services import chat_branches
    monkeypatch.setattr(chat_branches, "fork",
                        lambda *_a, **_k: _async({"branch": {"id": "b1"}, "rewind": rewind}))
    return asyncio.run(rc.fork_thread(tid, ForkThread(anchor="x", mode="after")))


def test_going_back_to_a_recorded_point_keeps_the_session_and_rewinds_it(thread, monkeypatch):
    tid, sid = thread["id"], thread["claude_session"]
    chat_store.set_thread_context_tokens(tid, 400_000)
    out = _fork(monkeypatch, tid, {"session": sid, "at": "entry-7"})
    assert out["rewound"] is True
    assert chat_store.get_thread(tid)["claude_session"] == sid
    assert chat_store.rewind_for(tid, sid) == "entry-7"
    assert not chat_store.get_thread(tid).get("context_tokens")


def test_a_point_in_an_earlier_session_adopts_that_session(thread, monkeypatch):
    """A point before a /clear, a /compact or an earlier detach is in the
    session that was live then, which the CLI still has."""
    tid = thread["id"]
    out = _fork(monkeypatch, tid, {"session": "an-older-session", "at": "entry-7"})
    assert out["rewound"] is True
    assert chat_store.get_thread(tid)["claude_session"] == "an-older-session"
    assert chat_store.rewind_for(tid, "an-older-session") == "entry-7"


def test_a_point_with_no_recorded_entry_detaches(thread, monkeypatch):
    tid = thread["id"]
    out = _fork(monkeypatch, tid, None)
    assert out["rewound"] is False
    assert not chat_store.get_thread(tid).get("claude_session")


def test_the_next_turn_forks_the_session_at_the_point(monkeypatch):
    meta = {"turn_id": "t1", "model": "claude-opus-5", "session_id": "s1", "resume": True,
            "resume_at": "entry-7", "cwd": "/tmp", "permission_mode": "bypassPermissions"}
    cmd, _ = build_streaming._build_cmd(meta)
    assert cmd[cmd.index("--resume") + 1] == "s1"
    assert cmd[cmd.index("--resume-session-at") + 1] == "entry-7"
    assert "--fork-session" in cmd
    cmd, _ = build_streaming._build_cmd({**meta, "resume_at": None})
    assert "--resume-session-at" not in cmd
    assert "--fork-session" not in cmd


def test_a_rewind_is_only_read_for_its_own_session(thread):
    tid, sid = thread["id"], thread["claude_session"]
    chat_store.set_rewind(tid, sid, "entry-7")
    assert chat_store.rewind_for(tid, "someone-else") is None
    chat_store.clear_rewind(tid)
    assert chat_store.rewind_for(tid, sid) is None


def test_session_points_are_top_level_assistant_entries():
    line = lambda **kw: json.dumps({"type": "assistant", "message": {"content": []}, **kw})
    assert build_streaming.session_point(line(uuid="u1")) == "u1"
    assert build_streaming.session_point(line(uuid="u2", parent_tool_use_id="tool-1")) is None
    assert build_streaming.session_point(json.dumps({"type": "user", "uuid": "u3"})) is None


def test_a_cut_stamps_the_stretch_with_the_point_reached():
    turn = build_streaming.Turn(id="t1", thread_id="th", agent_slug="a")
    turn.segments = [{"t": "text", "text": "reading"}]
    turn.cli_leaf = "entry-3"
    build_streaming.cut_here(turn, "ref-1")
    assert turn.segments[-1]["cli_at"] == "entry-3"
