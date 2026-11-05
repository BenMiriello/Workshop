"""A turn that resumes at a point gone back to FORKS the session there.

Measured on CLI 2.1.311: `--resume-session-at` without `--fork-session` moves
the session's own line to the point, and from then on the CLI cannot find any
point on the line it left ("No message found with message.uuid"), so switching
back to that line, or going back to a point on it, started over from the
visible text. It also cannot find a point further back than a compaction in
the file. The fork leaves the session whole, and the turn adopts the copy the
CLI reports in its `init` line.
"""
import asyncio
import json

import pytest

from services import build_agents, build_streaming, chat_store, ownership


def _entry(uid, parent, kind="assistant", **kw):
    return {"type": kind, "uuid": uid, "parentUuid": parent, **kw}


def _write(tmp_path, entries):
    path = tmp_path / "session.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(path)


def test_only_the_line_the_cli_resumes_is_reachable(tmp_path):
    path = _write(tmp_path, [
        _entry("u1", None, "user"), _entry("a1", "u1"),
        _entry("u2", "a1", "user"), _entry("a2", "u2"),
        _entry("u3", "a1", "user"), _entry("a3", "u3"),
    ])
    assert build_streaming.transcript_reaches(path, "a1")
    assert build_streaming.transcript_reaches(path, "a3")
    assert not build_streaming.transcript_reaches(path, "a2")
    assert not build_streaming.transcript_reaches(path, "nowhere")


def test_a_compaction_hides_all_but_its_own_parent(tmp_path):
    path = _write(tmp_path, [
        _entry("u1", None, "user"), _entry("a1", "u1"),
        _entry("u2", "a1", "user"), _entry("a2", "u2"),
        {"type": "system", "subtype": "compact_boundary", "uuid": "c1",
         "parentUuid": None, "logicalParentUuid": "a2"},
        _entry("u3", "a2", "user"), _entry("a3", "u3"),
    ])
    assert not build_streaming.transcript_reaches(path, "a1")
    assert build_streaming.transcript_reaches(path, "a2")
    assert build_streaming.transcript_reaches(path, "a3")


def test_a_subagent_entry_is_never_the_leaf(tmp_path):
    path = _write(tmp_path, [
        _entry("u1", None, "user"), _entry("a1", "u1"),
        _entry("s1", None, isSidechain=True),
    ])
    assert build_streaming.transcript_reaches(path, "a1")


def test_the_init_line_names_the_fork():
    init = json.dumps({"type": "system", "subtype": "init", "session_id": "s2"})
    assert build_streaming.forked_session(init) == "s2"
    other = json.dumps({"type": "system", "subtype": "status", "session_id": "s2"})
    assert build_streaming.forked_session(other) is None


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        t = chat_store.create_thread("general")
        chat_store.ensure_thread_session(t["id"])
        yield chat_store.get_thread(t["id"])
    finally:
        ownership.clear_current_owner()


def test_the_turn_adopts_the_fork_and_spends_the_rewind(thread, monkeypatch):
    monkeypatch.setattr(build_streaming, "_write_meta", lambda meta: None)
    tid, sid = thread["id"], thread["claude_session"]
    chat_store.set_rewind(tid, sid, "entry-7")
    turn = build_streaming.Turn(id="t1", thread_id=tid, agent_slug="general")
    meta = {"turn_id": "t1", "session_id": sid, "resume_at": "entry-7"}
    build_streaming._adopt_fork(turn, meta, "the-fork")
    assert chat_store.get_thread(tid)["claude_session"] == "the-fork"
    assert chat_store.rewind_for(tid, sid) is None
    assert meta["session_id"] == "the-fork" and meta["resume_at"] is None


def test_a_point_the_session_no_longer_reaches_starts_fresh(thread, monkeypatch):
    import routes.chat as rc
    tid, sid = thread["id"], thread["claude_session"]
    chat_store.set_rewind(tid, sid, "entry-7")
    monkeypatch.setattr(build_streaming, "session_reaches", lambda *_a: False)
    assert asyncio.run(rc._rewind_unreachable(tid, sid, False, "/tmp")) is True
    assert chat_store.rewind_for(tid, sid) is None
    chat_store.set_rewind(tid, sid, "entry-7")
    monkeypatch.setattr(build_streaming, "session_reaches", lambda *_a: True)
    assert asyncio.run(rc._rewind_unreachable(tid, sid, False, "/tmp")) is False
    assert chat_store.rewind_for(tid, sid) == "entry-7"


def test_an_agent_moves_only_from_the_session_it_is_on(tmp_path, monkeypatch):
    reg = tmp_path / "build_agents.json"
    reg.write_text(json.dumps({"build_agents": {"maker": {"claude_session": "s1"}}}))
    monkeypatch.setattr(build_agents, "_registry", lambda: reg)
    assert not build_agents.adopt_session("maker", "someone-else", "s2")
    assert build_agents.adopt_session("maker", "s1", "s2")
    assert build_agents.get_agent("maker")["claude_session"] == "s2"
