"""A compaction the CLI does on its own writes the same working-state note the
Compact button writes.

The note is what a chat carries across compactions and into its next turn.
Only the button used to write it, so a chat whose CLI compacted mid-turn kept
nothing but the CLI's own summary. These drive a recorded stream carrying the
CLI's `compact_boundary` through `_drive_turn`, the path a live turn takes, and
read the note back from the store.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from services import (app_chat, build_streaming, chat_context, chat_store, knowledge,
                      ownership, working_state)

STRUCTURED = """## Objective
- Plan the first page.

## Decisions and why
- Four panels, because the scene has four beats.

## Done
- None.

## Not done
- The layout.

## Shortcuts taken and what they owe
- None.

## Open questions
- None.
"""

# The shape the CLI emits, as recorded in its stream journal.
AUTO_BOUNDARY = json.dumps({
    "type": "system", "subtype": "compact_boundary", "session_id": "s1",
    "compact_metadata": {"trigger": "auto", "pre_tokens": 61_000,
                         "post_tokens": 3_000, "duration_ms": 5_000}})
COMPACTING = json.dumps({"type": "system", "subtype": "status",
                         "status": "compacting", "session_id": "s1"})
REPLY = json.dumps({"type": "assistant", "message": {
    "content": [{"type": "text", "text": "The layout is planned."}],
    "usage": {"input_tokens": 3_000, "output_tokens": 10}}})
RESULT = json.dumps({"type": "result", "subtype": "success",
                     "usage": {"input_tokens": 1, "output_tokens": 1}})


@pytest.fixture()
def scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSHOP_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSHOP_RUNTIME", str(tmp_path / ".runtime"))
    monkeypatch.setenv("WORKSHOP_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    shared = tmp_path / "shared" / "knowledge"
    shared.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(knowledge, "_root", lambda: tmp_path)
    monkeypatch.setattr(knowledge, "_runtime", lambda: tmp_path / ".runtime")
    monkeypatch.setattr(knowledge, "_shared_knowledge_root", lambda: shared)
    from wsplatform import paths
    monkeypatch.setattr(paths, "workshop_root", lambda: tmp_path)
    monkeypatch.setattr(paths, "projects_root", lambda: tmp_path / "projects")
    from services import chat_naming
    monkeypatch.setattr(chat_naming, "schedule", lambda *a, **kw: None)
    decl = app_chat.ChatDeclaration(
        app_slug="some-app", project="some-app", root=Path(tmp_path),
        label="Some App", persona_file=None, mcp_service=None,
        mcp_path="/mcp", model=None)
    provider = chat_context.AppChatProvider(decl)
    monkeypatch.setattr(chat_context, "get_provider", lambda key: provider)
    ownership.set_current_owner(4242)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


@pytest.fixture()
def summarised(monkeypatch):
    """Record each summarising call instead of reaching a model."""
    calls: list[dict] = []

    def fake(prev, msgs, t, *, per_thread):
        calls.append({"thread": t["id"], "msgs": msgs, "per_thread": per_thread})
        return STRUCTURED, "local-model-x"
    monkeypatch.setattr(working_state, "_summarize_for", fake)
    return calls


def _chatty() -> str:
    tid = chat_store.create_thread("general")["id"]
    for i in range(4):
        chat_store.append_message(tid, "user", f"message {i}")
        chat_store.append_message(tid, "assistant", f"reply {i}")
    return tid


async def _drive(tid: str, lines: list[str], prompt: str = "plan the page") -> None:
    turn_id = "turn-auto"
    chat_store.begin_turn(tid, turn_id, model="m")
    turn = build_streaming.Turn(id=turn_id, thread_id=tid, agent_slug="a")
    turn.spawned = True
    build_streaming.TURNS[turn_id] = turn
    d = build_streaming._journal_dir(turn_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stream.jsonl").write_text("".join(line + "\n" for line in lines))
    meta = {"turn_id": turn_id, "thread_id": tid, "agent_slug": "a", "cwd": "",
            "model": "m", "session_id": None, "resume": False, "speaker": None,
            "owner_id": 4242, "prompt": prompt, "timeout_s": 0, "pid": 4243,
            "status": "running", "started": 0.0, "allowed_tools": None,
            "disallowed_tools": None, "mcp_servers": None, "tools_disabled": False}

    class Proc:
        pid, returncode = 4243, None
    proc = Proc()
    turn.proc, turn.pid = proc, proc.pid
    driving = asyncio.create_task(build_streaming._drive_turn(turn, meta, proc=proc))
    await asyncio.sleep(0.05)
    proc.returncode = 0
    await asyncio.wait_for(driving, 5)
    await _write_finished(tid)


async def _write_finished(tid: str) -> None:
    """Wait until no write holds this thread's lock."""
    for _ in range(100):
        fd = working_state._try_lock(tid)
        if fd is not None:
            os.close(fd)
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"the working-state write for {tid} never finished")


def _note(tid: str) -> str:
    return knowledge.read_working_state(knowledge.scope_for_context("general"), tid)[0]


class TestAnAutoCompactionWritesTheNote:
    def test_the_threads_own_note_is_written_by_the_chats_model(self, scoped, summarised):
        tid = _chatty()
        asyncio.run(_drive(tid, [COMPACTING, AUTO_BOUNDARY, REPLY, RESULT]))
        assert len(summarised) == 1
        assert summarised[0]["thread"] == tid and summarised[0]["per_thread"] is True
        assert _note(tid).startswith("## Objective")
        raw = (knowledge.scope_dir(knowledge.scope_for_context("general"))
               / f"{knowledge.working_state_note_id(tid)}.md").read_text()
        assert "derived:local-model-x" in raw

    def test_the_note_includes_the_reply_of_the_turn_that_compacted(
            self, scoped, summarised):
        tid = _chatty()
        asyncio.run(_drive(tid, [COMPACTING, AUTO_BOUNDARY, REPLY, RESULT]))
        assert any("The layout is planned." in (m.get("content") or "")
                   for m in summarised[0]["msgs"])

    def test_a_turn_that_did_not_compact_writes_nothing(self, scoped, summarised):
        tid = _chatty()
        asyncio.run(_drive(tid, [REPLY, RESULT]))
        assert summarised == [] and _note(tid) == ""

    def test_the_explicit_compact_turn_leaves_it_to_the_button(self, scoped, summarised):
        tid = _chatty()
        boundary = AUTO_BOUNDARY.replace('"auto"', '"manual"')
        asyncio.run(_drive(tid, [COMPACTING, boundary, RESULT], prompt="/compact"))
        assert summarised == []


class TestOneWriteAtATime:
    def test_a_second_trigger_while_one_runs_is_dropped(self, scoped, monkeypatch):
        gate, calls = threading.Event(), []

        def slow(thread_id, t):
            calls.append(thread_id)
            gate.wait(5)
        monkeypatch.setattr(working_state, "write_now", slow)

        async def go():
            first = working_state.spawn("t1", {})
            assert first is not None
            assert working_state.spawn("t1", {}) is None
            other = working_state.spawn("t2", {})
            assert other is not None
            gate.set()
            await asyncio.gather(first, other)
            again = working_state.spawn("t1", {})
            assert again is not None
            await again
        asyncio.run(go())
        assert sorted(calls) == ["t1", "t1", "t2"]

    def test_a_write_held_by_another_process_is_seen(self, scoped, monkeypatch):
        """The lock is what another backend worker would hold, not a registry
        inside this process."""
        monkeypatch.setattr(working_state, "write_now", lambda *a: None)
        lock = working_state._lock_path("t1")
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, os, sys; fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR);"
             "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); sys.stdin.read()",
             str(lock)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "held"

            async def held():
                return working_state.spawn("t1", {})
            assert asyncio.run(held()) is None
        finally:
            holder.stdin.close()
            holder.wait(5)

        async def free():
            await working_state.spawn("t1", {})
        asyncio.run(free())

    def test_a_write_that_raises_releases_the_lock(self, scoped, monkeypatch):
        def boom(thread_id, t):
            raise RuntimeError("summariser fell over")
        monkeypatch.setattr(working_state, "write_now", boom)

        async def go():
            with pytest.raises(RuntimeError):
                await working_state.spawn("t1", {})
            again = working_state.spawn("t1", {})
            assert again is not None
            with pytest.raises(RuntimeError):
                await again
        asyncio.run(go())
