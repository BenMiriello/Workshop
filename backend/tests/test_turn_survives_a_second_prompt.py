"""A turn is not over while the CLI still owes us an answer.

Driver-level companion to tests/test_turn_end.py, which pins the rule itself.
This one runs the actual drive loop over a journal, because the bug was never in
the arithmetic: it was that `saw_result` went straight to SIGTERM.

The failure, from the operator's side: he types while a turn is working, the
reply lands, and his message is answered by an empty bubble reading "Ended
without a reply. Nothing ran this turn." The CLI had read the message and was
answering it when the driver killed the process. Six turns in the log, six
`interruptedByShutdown` records in the CLI's own session journals.
"""
import asyncio
import json
import os

import pytest

from services import build_streaming, chat_drain, chat_queue, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    from services import chat_naming
    monkeypatch.setattr(chat_naming, "schedule", lambda *a, **kw: None)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()


class FakeProc:
    def __init__(self, pid: int = 4243):
        self.pid = pid
        self.returncode = None


def _replay(text: str) -> str:
    """What `--replay-user-messages` emits when the CLI has READ a prompt."""
    return json.dumps({"type": "user", "isReplay": True,
                       "message": {"content": [{"type": "text", "text": text}]}})


def _assistant(text: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": text}]}})


def _result() -> str:
    """One per prompt the CLI reads, not one per turn. That is the whole point."""
    return json.dumps({"type": "result", "subtype": "success"})


@pytest.fixture()
def no_drain(monkeypatch):
    """Hold the drain still so a test can read the queue.

    `_finalize_turn` fires `drain_soon` the instant a turn ends, which is the
    behaviour that makes a requeued message actually go out. Here it would
    consume the entry before the assertion could see it."""
    fired: list[str] = []
    monkeypatch.setattr(chat_drain, "drain_soon",
                        lambda thread_id, reason="": fired.append(reason))
    return fired


class Driver:
    """The drive loop over a journal this test appends to as it goes."""

    def __init__(self, thread_id: str, monkeypatch, delivered: int = 1):
        self.retired = False
        run_id = "run-second-prompt"
        chat_store.begin_run(thread_id, run_id, model="m")
        self.turn = build_streaming.Turn(id=run_id, thread_id=thread_id, agent_slug="a")
        self.turn.spawned = True
        self.turn.delivered = delivered
        build_streaming.TURNS[run_id] = self.turn
        d = build_streaming._journal_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "stream.jsonl"
        self.path.write_text("")
        self.meta = {"turn_id": run_id, "thread_id": thread_id, "agent_slug": "a",
                     "cwd": "", "model": "m", "session_id": None, "resume": False,
                     "speaker": None, "owner_id": 1, "prompt": "do the thing",
                     "timeout_s": 0, "pid": 4243, "status": "running", "started": 0.0,
                     "allowed_tools": None, "disallowed_tools": None,
                     "mcp_servers": None, "tools_disabled": False}
        # A REAL input channel, opened the way the spawn opens it (O_RDWR, so
        # the fd is its own reader and a write never sees ENXIO). `inject` goes
        # through the actual write path in these tests, because the bookkeeping
        # under test lives in `write_to_turn`.
        self.fifo = build_streaming._stdin_fifo(run_id)
        os.mkfifo(self.fifo, 0o600)
        self.in_fd = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        self.proc = FakeProc()
        self.turn.proc, self.turn.pid = self.proc, self.proc.pid

        async def _retire(pid, proc=None):
            self.retired = True
        monkeypatch.setattr(build_streaming, "_retire_turn_proc", _retire)

    def write(self, *lines: str) -> None:
        with open(self.path, "a") as f:
            f.write("".join(l + "\n" for l in lines))

    async def settle(self, seconds: float = 0.6) -> None:
        await asyncio.sleep(seconds)

    def close(self) -> None:
        try:
            os.close(self.in_fd)
        except OSError:
            pass


class TestTheFirstResultIsNotAlwaysTheEnd:
    def test_an_ordinary_turn_is_retired_on_its_result(self, thread, monkeypatch):
        """The common path must not get slower. One prompt, read, answered."""
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=1)
            d.write(_replay("do the thing"), _assistant("here you go"), _result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await asyncio.wait_for(task, 5)
            return d
        d = asyncio.run(go())
        assert d.retired is True
        msgs = chat_store.get_thread(thread["id"])["messages"]
        assert msgs[-1]["content"] == "here you go"

    def test_a_message_delivered_mid_turn_is_answered_not_killed(self, thread, monkeypatch):
        """The reported failure. The turn's own prompt is answered (result #1)
        while a second message is already in the CLI's hands; the driver used to
        SIGTERM here, and the answer to that message never existed."""
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=2)
            d.write(_replay("do the thing"), _assistant("first answer"), _result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await d.settle()
            # The result landed and the process is STILL RUNNING, because the
            # second message has not been answered.
            assert d.retired is False, "killed the process with a message still in flight"
            d.write(_replay("and also this"), _assistant("second answer"), _result())
            await asyncio.wait_for(task, 5)
            return d
        d = asyncio.run(go())
        assert d.retired is True
        text = chat_store.get_thread(thread["id"])["messages"][-1]["content"]
        assert "first answer" in text and "second answer" in text

    def test_a_result_for_a_prompt_of_the_clis_own_does_not_end_the_turn(
            self, thread, monkeypatch):
        """The resume case. On `--resume` with orphaned background agents the
        CLI delivers a `<task-notification>` as a prompt of its own, ahead of
        ours, and it gets a result ~90ms after spawn. Nothing of ours had been
        answered yet, so that result must not end the turn."""
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=1)
            # Our prompt is read, then a result lands with nothing produced: it
            # belongs to the CLI's own prompt, not to ours.
            d.write(_replay("do the thing"), _result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await d.settle()
            assert d.retired is False, "killed the request that was about to answer"
            d.write(_assistant("the real answer"), _result())
            await asyncio.wait_for(task, 5)
            return d
        d = asyncio.run(go())
        assert d.retired is True
        assert chat_store.get_thread(thread["id"])["messages"][-1]["content"] == "the real answer"

class TestAMessageTheCliNeverReadGoesBackOnTheQueue:
    """A message is the system's responsibility from the moment the server
    accepts it. Writing it into a FIFO that nothing ever reads is not delivery,
    and it was the one remaining path where an accepted message could vanish:
    the write succeeded, the turn ended, and the text sat in the transcript with
    nothing coming for it."""

    def test_an_unread_injection_is_requeued_under_its_own_id(
            self, thread, monkeypatch, no_drain):
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=0)
            build_streaming.inject(d.turn.id, "and also check the logs",
                                   client_id="cid-1", sent_at="2026-08-12T20:00:00+00:00")
            d.write(_assistant("done"), _result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await asyncio.sleep(0.3)
            d.proc.returncode = 0        # the process is gone; it never read it
            await asyncio.wait_for(task, 5)
        # The turn's own prompt is not part of this: `delivered=0` starts it
        # with nothing outstanding, so the injection is the only write.
        asyncio.run(go())
        q = chat_queue.read(thread["id"])
        assert [e["text"] for e in q] == ["and also check the logs"]
        # Its OWN id and send time, so the drain re-sends it without writing a
        # second copy to the transcript and it keeps its place in the order.
        assert q[0]["client_id"] == "cid-1"
        assert q[0]["ts"] == "2026-08-12T20:00:00+00:00"
        assert no_drain, "the thread was never told to drain"

    def test_a_message_the_cli_did_read_is_not_requeued(
            self, thread, monkeypatch, no_drain):
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=0)
            build_streaming.inject(d.turn.id, "and also check the logs", client_id="cid-1")
            d.write(_replay("and also check the logs"), _assistant("checked"), _result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await asyncio.wait_for(task, 5)
            d.close()
        asyncio.run(go())
        assert chat_queue.read(thread["id"]) == []

    def test_a_stopped_turn_does_not_requeue(self, thread, monkeypatch, no_drain):
        """Stop is the operator taking the wheel back, and it holds the queue on
        purpose. Re-queueing would clear that hold and send, which is the
        opposite of what they just asked for."""
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=0)
            build_streaming.inject(d.turn.id, "and also check the logs", client_id="cid-1")
            d.turn.stop_requested = True
            d.write(_result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await asyncio.wait_for(task, 5)
            d.close()
        asyncio.run(go())
        assert chat_queue.read(thread["id"]) == []

    def test_a_send_with_no_client_id_is_left_alone(self, thread, monkeypatch, no_drain):
        """There would be no way to recognise it as the same message, so the
        drain would append a SECOND copy of it to the transcript. That is a
        worse failure than the one this fixes."""
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=0)
            build_streaming.inject(d.turn.id, "no id on this one")
            d.write(_result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await asyncio.sleep(0.3)
            d.proc.returncode = 0
            await asyncio.wait_for(task, 5)
            d.close()
        asyncio.run(go())
        assert chat_queue.read(thread["id"]) == []


class TestTheEmptyTurnItself:
    def test_a_message_the_cli_never_read_is_reported_as_such(self, thread, monkeypatch):
        """The turn ends (the process died) with a delivery never acknowledged.
        The reply must say the message did not reach the model rather than
        reporting it as the model having had nothing to say."""
        async def go():
            d = Driver(thread["id"], monkeypatch, delivered=1)
            d.write(_result())
            task = asyncio.create_task(build_streaming._drive_turn(d.turn, d.meta, proc=d.proc))
            await asyncio.sleep(0.3)
            d.proc.returncode = 0        # the process is gone; nothing more is coming
            await asyncio.wait_for(task, 5)
        asyncio.run(go())
        text = chat_store.get_thread(thread["id"])["messages"][-1]["content"]
        assert text == "Ended before this message reached the model. Nothing ran."


class TestWhatRidesWithThePrompt:
    """The sends a prompt was assembled from, in the shape the queue holds.

    The prompt itself is an assembled blob (preamble, knowledge index, recent
    conversation); putting THAT back on the queue would re-send the machinery
    rather than the message. What goes back is what the operator actually sent,
    under the ids the log already knows it by."""

    def test_a_single_message_carries_its_own_id(self):
        import routes.chat as rc
        assert rc._sends_behind("look at the logs", None, "cid-9", None) == [
            {"text": "look at the logs", "client_id": "cid-9", "ts": None,
             "attachments": []},
        ]

    def test_a_drained_queue_carries_every_part_at_its_own_time(self):
        """Each entry became its own log event at its own send time, so each
        goes back as itself. Attachments rode on the first part, exactly as
        `_record_user_turn` records them."""
        import routes.chat as rc
        parts = [rc.TurnPart(text="first", sent_at="2026-08-12T20:00:00+00:00", client_id="a"),
                 rc.TurnPart(text="second", sent_at="2026-08-12T20:01:00+00:00", client_id="b")]
        out = rc._sends_behind("first\n\nsecond", parts, "a", [{"id": "att-1"}])
        assert [e["client_id"] for e in out] == ["a", "b"]
        assert [e["ts"] for e in out] == ["2026-08-12T20:00:00+00:00",
                                          "2026-08-12T20:01:00+00:00"]
        assert out[0]["attachments"] == [{"id": "att-1"}] and out[1]["attachments"] == []
