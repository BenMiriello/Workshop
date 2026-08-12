"""Where a mid-turn message lands, and which turns may be spoken to at all.

Two symptoms the operator reported repeatedly, both settled against his own
thread log (d899901fe4fe) rather than against a hypothesis:

  1. "my messages still sit BELOW your last message", "my message shows ABOVE
     the last system block". The cut coordinate counted TOP-LEVEL segments, and a
     subagent's steps nest inside one of those, so the coordinate froze for the
     whole delegation. seq32 of that thread: 128 top-level segments, 672 nested
     steps, one chip holding 175. seq37 and seq38 were sent minutes apart and
     both recorded index 42.

  2. "I sent the slash compact message then sent that last message ... and I had
     to send it again." seq52 `/compact` at 06:28:19, seq53 his message at
     06:29:00 filed against the compact run, seq54 the compact reply at 06:30:23,
     and nothing between. A command turn never reads its input, so the message
     was handed to the one turn guaranteed to ignore it.
"""
import asyncio

import pytest

from services import build_streaming, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


def _turn(thread_id: str = "t") -> build_streaming.Turn:
    return build_streaming.Turn(id="run-1", thread_id=thread_id, agent_slug="a")


def _launch_agent(turn: build_streaming.Turn, agent_id: str = "toolu_1") -> None:
    build_streaming._seg_agent(turn, {"agent": "Explore", "input": "look", "id": agent_id})


class TestUnitCount:
    def test_a_plain_reply_counts_its_segments(self):
        turn = _turn()
        build_streaming._seg_text(turn, "hello")
        build_streaming._seg_tool(turn, "Read", "a.py")
        assert build_streaming.unit_count(turn.segments) == 2

    def test_a_delegates_steps_each_count(self):
        """The whole bug in one assertion. Before this, a chip of any size was
        one unit and the coordinate stood still while the delegate worked."""
        turn = _turn()
        _launch_agent(turn)
        for i in range(5):
            build_streaming._agent_step(turn, "toolu_1", {"t": "tool", "name": "Read", "input": str(i)})
        # One top-level agent segment + five steps.
        assert len(turn.segments) == 1
        assert build_streaming.unit_count(turn.segments) == 6

    def test_the_coordinate_advances_during_a_delegation(self):
        """seq37 and seq38 both recorded 42. Two cuts taken at different points
        of one delegation must differ."""
        turn = _turn()
        _launch_agent(turn)
        build_streaming._agent_step(turn, "toolu_1", {"t": "tool", "name": "Read", "input": "a"})
        first = build_streaming.cut_here(turn)
        for _ in range(3):
            build_streaming._agent_step(turn, "toolu_1", {"t": "tool", "name": "Read", "input": "b"})
        second = build_streaming.cut_here(turn)
        assert first == 2
        assert second == 5
        assert second > first

    def test_an_empty_reply_is_zero(self):
        assert build_streaming.unit_count([]) == 0


class TestSealing:
    def test_text_written_after_a_cut_starts_a_new_segment(self):
        """Counting alone does not place a message. Streamed text merges into the
        trailing segment, so without a seal "A" then a message then "B" is one
        segment "AB" by commit time, and the message renders below both."""
        turn = _turn()
        build_streaming._seg_text(turn, "A")
        at = build_streaming.cut_here(turn)
        build_streaming._seg_text(turn, "B")
        assert at == 1
        assert [s["text"] for s in turn.segments] == ["A", "B"]

    def test_a_delegates_text_seals_the_same_way(self):
        turn = _turn()
        _launch_agent(turn)
        build_streaming._agent_step(turn, "toolu_1", {"t": "text", "text": "A"})
        build_streaming.cut_here(turn)
        build_streaming._agent_step(turn, "toolu_1", {"t": "text", "text": "B"})
        steps = turn.segments[0]["steps"]
        assert [s["text"] for s in steps] == ["A", "B"]

    def test_text_still_merges_when_nothing_interrupted_it(self):
        """The ordinary case must be untouched: a turn nobody interrupted still
        streams into ONE text segment, or every reply becomes a hundred blocks."""
        turn = _turn()
        for c in "abc":
            build_streaming._seg_text(turn, c)
        assert len(turn.segments) == 1
        assert turn.segments[0]["text"] == "abc"

    def test_sealing_an_empty_turn_is_harmless(self):
        turn = _turn()
        assert build_streaming.cut_here(turn) == 0
        build_streaming._seg_text(turn, "later")
        assert turn.segments[0]["text"] == "later"


class TestACommandTurnCannotBeSpokenTo:
    def test_inject_refuses_a_command_turn(self):
        turn = _turn()
        turn.accepts_input = False
        turn.spawned = True
        build_streaming.TURNS[turn.id] = turn
        assert build_streaming.inject(turn.id, "and also do this") is False

    def test_inject_still_reaches_an_ordinary_turn(self, monkeypatch):
        """The refusal must be about COMMANDS, not about injection. Without this
        the fix for a swallowed message silently disables mid-turn delivery."""
        turn = _turn()
        turn.spawned = True
        build_streaming.TURNS[turn.id] = turn
        wrote: list[str] = []
        monkeypatch.setattr(build_streaming, "write_to_turn",
                            lambda tid, text: wrote.append(text) or True)
        assert build_streaming.inject(turn.id, "and also do this") is True
        assert wrote == ["and also do this"]

    def test_a_finished_command_no_longer_blocks_delivery(self):
        turn = _turn()
        turn.accepts_input = False
        turn.complete = True
        build_streaming.TURNS[turn.id] = turn
        assert build_streaming.run_accepts_input(turn.id) is True

    def test_the_claim_marks_a_command_run(self, thread):
        async def go():
            return await build_streaming.claim_run(
                thread["id"], "run-cmd", model="m", accepts_input=False)
        assert asyncio.run(go()) is None
        assert build_streaming.run_accepts_input("run-cmd") is False


class TestSendDuringACommandTurn:
    """The 06:28-06:30 sequence, end to end.

    The message must not be recorded against the command run — a message in the
    log that nothing will ever read is worse than one still waiting — and it
    must not be left to the client either. On 2026-08-12 it was: the server
    answered `deferred` and stored nothing, the browser held the only copy, and
    two of the operator's messages ceased to exist when that tab moved on.
    It is TAKEN, onto the thread's own queue."""

    def test_the_message_is_queued_on_the_thread_not_left_to_the_browser(self, thread):
        from routes import chat as chat_routes
        from services import chat_queue

        tid = thread["id"]

        async def go():
            await build_streaming.claim_run(tid, "run-cmd", model="m", accepts_input=False)
            return await chat_routes._send(tid, "and now do the other thing")

        out = asyncio.run(go())
        assert out["queued"] is True
        assert out["injected"] is False
        # Not in the transcript: it has not been said yet, and a command turn
        # will never read it.
        contents = [m.get("content") for m in chat_store.get_thread(tid)["messages"]]
        assert "and now do the other thing" not in contents
        # ...but it IS on the thread, on disk, where any device can see it and
        # where the server will find it when this run ends.
        assert [e["text"] for e in chat_queue.read(tid)] == ["and now do the other thing"]

    def test_a_command_typed_during_any_turn_is_queued_too(self, thread):
        """A command cannot be injected either: the CLI's command layer only
        runs BETWEEN turns, so one sent mid-turn is swallowed with no user
        message, no command and no error."""
        from routes import chat as chat_routes
        from services import chat_queue

        tid = thread["id"]

        async def go():
            await build_streaming.claim_run(tid, "run-1", model="m", accepts_input=True)
            return await chat_routes._send(tid, "/compact")

        out = asyncio.run(go())
        assert out["queued"] is True
        assert [e["text"] for e in chat_queue.read(tid)] == ["/compact"]
