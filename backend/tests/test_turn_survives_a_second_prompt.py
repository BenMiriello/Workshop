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

import pytest

from services import build_streaming, chat_store, ownership


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
