"""Stop stops the turn — and keeps what the turn had already produced.

The button used to close this tab's SSE socket and nothing else. The turn kept
running and kept spending; the UI looked idle because a local flag said so. When
the rebuild made the indicator read liveness from the server's `active_turn`, the
lie stopped working and the chat correctly said "Still running" after Stop, which
is how it was reported.

So `POST /chat/turns/{id}/stop` ends the run for real: it signals the process
GROUP (the CLI owns MCP servers and tool subprocesses), and then lets the drive
loop finish the turn through the ONE finalize pipeline — final drain, segments
folded, `chat_store.commit_turn` once at the tail — so the partial output is the
reply rather than something thrown away. Everything here pins one half of that.
"""
import asyncio
import signal

import pytest

from services import build_streaming, chat_log, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    # A stopped turn is not an error, so the finalize would schedule the
    # auto-namer (a model call). Nothing in this file is about naming.
    from services import chat_naming
    monkeypatch.setattr(chat_naming, "schedule", lambda *a, **kw: None)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


class FakeProc:
    """A spawned turn's process, as the drive loop uses it: a `returncode` that
    is None until the thing dies. Death is driven by the signal, so the test
    observes the real order — signal first, then the loop noticing."""

    def __init__(self, pid: int = 4242):
        self.pid = pid
        self.returncode = None

    def die(self, rc: int = -signal.SIGTERM) -> None:
        self.returncode = rc


def _journal(turn_id: str, lines: list[str]) -> dict:
    """A turn mid-flight: its journal on disk with `lines` already written."""
    d = build_streaming._journal_dir(turn_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stream.jsonl").write_text("".join(line + "\n" for line in lines))
    return {"turn_id": turn_id, "thread_id": "", "agent_slug": "a", "cwd": "",
            "model": "m", "session_id": None, "resume": False, "speaker": None,
            "owner_id": 1, "prompt": "do the thing", "timeout_s": 0,
            "pid": 4242, "status": "running", "started": 0.0,
            "allowed_tools": None, "disallowed_tools": None, "mcp_servers": None,
            "tools_disabled": False}


def _text_line(s: str) -> str:
    import json
    return json.dumps({"type": "assistant",
                       "message": {"content": [{"type": "text", "text": s}]}})


def _tool_line(name: str) -> str:
    import json
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": {}, "id": "t1"}]}})


def _running_turn(thread_id: str, lines: list[str]) -> tuple[build_streaming.Turn, dict, FakeProc]:
    turn_id = "run-stop"
    chat_store.begin_turn(thread_id, turn_id, model="m")
    turn = build_streaming.Turn(id=turn_id, thread_id=thread_id, agent_slug="a")
    turn.spawned = True
    build_streaming.TURNS[turn_id] = turn
    meta = _journal(turn_id, lines)
    meta["thread_id"] = thread_id
    proc = FakeProc()
    turn.proc, turn.pid = proc, proc.pid
    return turn, meta, proc


async def _drive_and_stop(turn, meta, proc, *, killer=None, stops: int = 1):
    """Run the real drive loop against the fake process and stop it. The killer
    stands in for the signal: it records, then kills the fake process the way
    SIGTERM kills a real one. `stops` presses land CONCURRENTLY, which is the
    race worth testing — two of them in the same tick."""
    async def kill(pid, p=None, **_kwargs):
        if killer is not None:
            killer(pid, p)
        proc.die()
    original = build_streaming._retire_turn_proc
    driving = asyncio.create_task(build_streaming._drive_turn(turn, meta, proc=proc))
    await asyncio.sleep(0.05)          # let it tail what is already written
    try:
        build_streaming._retire_turn_proc = kill  # type: ignore[assignment]
        outs = await asyncio.gather(
            *(build_streaming.stop_turn(turn.id) for _ in range(stops)))
        await asyncio.wait_for(driving, 5)
    finally:
        build_streaming._retire_turn_proc = original  # type: ignore[assignment]
    return list(outs)


class TestAStopKeepsWhatTheTurnProduced:
    """The whole point. A stop that discarded the partial answer would make Stop
    a destructive button, and the operator presses it precisely when the turn has
    already said something useful and is going the wrong way with it."""

    def test_the_partial_output_is_committed_as_the_reply(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("half an answer")])

        asyncio.run(_drive_and_stop(turn, meta, proc))

        last = chat_store.get_thread(tid)["messages"][-1]
        assert last["role"] == "assistant"
        assert last["content"] == "half an answer"
        assert last["meta"]["stopped"] == chat_store.STOPPED_BY_OPERATOR
        # Not an error and not lost: it is a turn that ended early, on purpose.
        assert not last["meta"].get("error")
        assert not last["meta"].get("timeout")

    def test_the_tool_steps_it_got_through_are_kept_too(self, thread):
        """A stop with no text is still a record of work: the chips are what the
        operator reads to see how far it got."""
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_tool_line("Read"), _tool_line("Edit")])

        asyncio.run(_drive_and_stop(turn, meta, proc))

        last = chat_store.get_thread(tid)["messages"][-1]
        assert [s["name"] for s in last["meta"]["segments"]] == ["Read", "Edit"]
        # No body. The tag says who ended it; a sentence repeating that is the
        # same thing said twice.
        assert last["content"] == ""
        assert last["meta"]["stopped"] == chat_store.STOPPED_BY_OPERATOR

    def test_output_written_between_the_signal_and_the_exit_is_not_lost(self, thread):
        """The final drain happens after the process is gone, so bytes the CLI
        flushed on its way out still make the reply."""
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("first half ")])

        def flush_more(pid, p):
            with open(build_streaming._journal_dir(turn.id) / "stream.jsonl", "a") as f:
                f.write(_text_line("and the rest") + "\n")

        asyncio.run(_drive_and_stop(turn, meta, proc, killer=flush_more))

        assert chat_store.get_thread(tid)["messages"][-1]["content"] == \
            "first half and the rest"

    def test_exactly_one_message_is_appended(self, thread):
        """One commit pipeline. A second finalize path for stops would show the
        turn twice, which is how the old two-pipeline arrangement failed."""
        tid = thread["id"]
        chat_store.append_message(tid, "user", "go")
        turn, meta, proc = _running_turn(tid, [_text_line("partial")])

        asyncio.run(_drive_and_stop(turn, meta, proc))

        bodies = [m["content"] for m in chat_store.get_thread(tid)["messages"]]
        assert bodies == ["go", "partial"]


class TestTheProcessGroupIsSignalled:
    """The CLI owns children — MCP servers, tool subprocesses. Signalling only
    the leader leaves them running, and the run's spend with them."""

    def test_stop_signals_the_group_once(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("x")])
        seen: list[tuple] = []

        asyncio.run(_drive_and_stop(turn, meta, proc,
                                    killer=lambda pid, p: seen.append((pid, p))))

        assert [pid for pid, _p in seen] == [proc.pid]

    def test_the_real_killer_sends_sigterm_to_the_group(self, monkeypatch):
        """_retire_turn_proc itself, unmocked: the group, not the pid. The turn
        is spawned start_new_session=True, so it owns that group."""
        signals: list[tuple[int, int]] = []
        monkeypatch.setattr(build_streaming.os, "getpgid", lambda pid: 777)
        monkeypatch.setattr(build_streaming.os, "killpg",
                            lambda pgid, sig: signals.append((pgid, sig)))
        proc = FakeProc(pid=555)
        proc.die()      # already gone, so the helper returns on its first check

        asyncio.run(build_streaming._retire_turn_proc(proc.pid, proc))

        assert signals == [(777, signal.SIGTERM)]


class TestStoppingIsIdempotent:
    """A stop that raises, or that commits twice, is worse than a stop that does
    nothing: the operator pressed a button, not started a transaction."""

    def test_two_presses_in_the_same_tick_commit_once(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("partial")])
        seen: list[tuple] = []

        outs = asyncio.run(_drive_and_stop(
            turn, meta, proc, killer=lambda pid, p: seen.append(pid), stops=2))

        # Both are told the turn is stopped, because it is. Neither is an error.
        assert [o["stopped"] for o in outs] == [True, True]
        assert len(seen) == 1, "the group is signalled once, not once per press"
        replies = [m for m in chat_store.get_thread(tid)["messages"]
                   if m["role"] == "assistant"]
        assert len(replies) == 1

    def test_stopping_an_already_committed_run_appends_nothing(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("done")])
        asyncio.run(_drive_and_stop(turn, meta, proc))
        before = chat_log.read_events(tid)

        out = asyncio.run(build_streaming.stop_turn(turn.id))

        assert out["stopped"] is False
        assert chat_log.read_events(tid) == before

    def test_stopping_a_run_this_process_never_held_is_a_no_op(self, thread):
        """A restart orphan. The startup reconcile owns it; a Stop must not
        invent a second way to close it out, and must not 500."""
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-from-a-previous-boot")
        before = chat_log.read_events(tid)

        out = asyncio.run(build_streaming.stop_turn("run-from-a-previous-boot"))

        assert out == {"turn_id": "run-from-a-previous-boot", "stopped": False,
                       "reason": "no turn by that id is going here"}
        assert chat_log.read_events(tid) == before

    def test_the_endpoint_answers_an_unknown_turn_without_raising(self, thread):
        import routes.chat as rc
        out = asyncio.run(rc.stop_turn("no-such-run"))
        assert out["stopped"] is False


class TestTheThreadIsLeftConsistent:
    """A stop that left `active_turn_id` set would keep the indicator saying
    "Still running" — the exact complaint — and would make the next startup
    re-finalize the run as lost on top of the reply it already has."""

    def test_the_active_run_is_cleared(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("partial")])
        assert chat_store.active_turn_id(tid) == turn.id

        asyncio.run(_drive_and_stop(turn, meta, proc))

        assert chat_store.active_turn_id(tid) is None
        assert chat_log.active_turn(tid) is None
        assert build_streaming.turn_is_live(turn.id) is False

    def test_the_run_marker_says_finished_not_lost(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("partial")])

        asyncio.run(_drive_and_stop(turn, meta, proc))

        states = [e["state"] for e in chat_log.read_events(tid) if e["kind"] == "turn"]
        assert states == ["started", "finished"]

    def test_reconcile_does_not_re_finalize_a_stopped_run(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("partial")])
        asyncio.run(_drive_and_stop(turn, meta, proc))
        before = chat_log.read_events(tid)

        build_streaming.TURNS.clear()        # as if the backend had restarted
        build_streaming.reconcile_threads()

        assert chat_log.read_events(tid) == before
        assert not [m for m in chat_store.get_thread(tid)["messages"]
                    if m["content"] == chat_store.LOST_TURN_TEXT]

    def test_the_journal_is_cleaned_up(self, thread):
        tid = thread["id"]
        turn, meta, proc = _running_turn(tid, [_text_line("partial")])
        asyncio.run(_drive_and_stop(turn, meta, proc))
        assert not build_streaming._journal_dir(turn.id).exists()


class TestALostRunIsWordedDifferently:
    """Two endings, two values. "You stopped this" and "a restart took it" are
    not the same event and the transcript must not blur them."""

    def test_a_restart_orphan_is_marked_interrupted(self, thread):
        tid = thread["id"]
        chat_store.begin_turn(tid, "run-gone")

        build_streaming.reconcile_threads()

        last = chat_store.get_thread(tid)["messages"][-1]
        assert last["meta"]["stopped"] == chat_store.STOPPED_INTERRUPTED
        assert last["content"] == chat_store.LOST_TURN_TEXT

    def test_the_two_values_are_distinct(self):
        assert chat_store.STOPPED_BY_OPERATOR != chat_store.STOPPED_INTERRUPTED


def test_a_stop_before_the_process_exists_still_ends_the_run(thread):
    """The claim window: a run owns the thread from before its prompt is
    assembled, so Stop can land when there is no pid to signal. The drive loop
    picks the signal up on its first pass instead of the run continuing."""
    tid = thread["id"]
    turn, meta, proc = _running_turn(tid, [_text_line("late")])
    turn.pid, turn.proc = None, None      # claimed, not yet spawned
    seen: list = []

    async def go():
        async def kill(pid, p=None, **_kwargs):
            seen.append(pid)
            proc.die()
        stopping = asyncio.create_task(build_streaming.stop_turn(turn.id))
        await asyncio.sleep(0)
        orig = build_streaming._retire_turn_proc
        build_streaming._retire_turn_proc = kill  # type: ignore[assignment]
        try:
            await build_streaming._drive_turn(turn, meta, proc=proc)
            return await stopping
        finally:
            build_streaming._retire_turn_proc = orig

    out = asyncio.run(go())
    assert out["stopped"] is True
    assert seen == [meta["pid"]], "the loop signals what stop_turn could not"
    assert chat_store.active_turn_id(tid) is None
    assert chat_store.get_thread(tid)["messages"][-1]["content"] == "late"


def test_os_kill_is_not_called_on_a_pid_of_none(monkeypatch):
    """Guard on the claim-window path: `_retire_turn_proc(None, None)` must not
    signal pid 0, which on Linux is the whole process group of the backend."""
    monkeypatch.setattr(build_streaming.os, "killpg",
                        lambda *a: pytest.fail("signalled with no pid"))
    monkeypatch.setattr(build_streaming.os, "getpgid",
                        lambda *a: pytest.fail("looked up a group with no pid"))
    asyncio.run(build_streaming._retire_turn_proc(None, None))


def test_stop_returns_before_hanging_forever(thread, monkeypatch):
    """A run whose commit never lands must not hold the request open. The stop is
    real either way — the loop owns the commit and always runs it."""
    tid = thread["id"]
    turn, meta, proc = _running_turn(tid, [_text_line("x")])
    monkeypatch.setattr(build_streaming, "STOP_WAIT_S", 0.05)

    async def kill(pid, p=None, **_kwargs):
        return None       # the process ignores it; nothing finishes
    monkeypatch.setattr(build_streaming, "_retire_turn_proc", kill)

    out = asyncio.run(build_streaming.stop_turn(turn.id))
    assert out["stopped"] is True and out["committed"] is False
