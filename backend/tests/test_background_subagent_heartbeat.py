"""A background subagent that is still working keeps its turn alive.

On 2026-10-11 a chat launched two background builders and then sat in a
ten-minute blocking sleep, because it had been told a text-only reply ends the
turn. The prompt now tells it to end its message and be woken instead. That is
only safe if the parent going quiet does not get the builders killed: their
steps nest into the agent's segment, so before the `task_progress` heartbeat
counted as activity, BACKGROUND_GRACE_S of parent silence retired the turn and
the builders with it, however busy they were.

These drive the real loop over a live journal with the grace shortened.
"""
import asyncio
import json

from services import build_streaming, chat_store, turn_end
from tests.test_turn_model_failures import FakeProc, thread  # noqa: F401

GRACE_S = 0.4


def _line(**ev) -> str:
    return json.dumps(ev) + "\n"


def _drive_live(thread_id: str, monkeypatch, *, heartbeat: bool):
    """A parent that launched a builder, spoke and went quiet. The builder
    reports progress every 0.1s when `heartbeat` is set. Returns the pids the
    loop retired."""
    monkeypatch.setattr(turn_end, "BACKGROUND_GRACE_S", GRACE_S)
    turn_id = "run-heartbeat"
    chat_store.begin_turn(thread_id, turn_id, model="m")
    turn = build_streaming.Turn(id=turn_id, thread_id=thread_id, agent_slug="a")
    turn.spawned = True
    build_streaming.TURNS[turn_id] = turn
    d = build_streaming._journal_dir(turn_id)
    d.mkdir(parents=True, exist_ok=True)
    journal = d / "stream.jsonl"
    journal.write_text(
        _line(type="system", subtype="task_started", task_id="builder")
        + _line(type="assistant", message={"content": [
            {"type": "text", "text": "The builder is running."}]})
        + _line(type="result", subtype="success", result=""))
    meta = {"turn_id": turn_id, "thread_id": thread_id, "agent_slug": "a",
            "cwd": "", "model": "m", "session_id": None, "resume": False,
            "speaker": None, "owner_id": 1, "prompt": "go", "timeout_s": 0,
            "pid": 4242, "status": "running", "started": 0.0}
    proc = FakeProc()
    turn.proc, turn.pid = proc, proc.pid
    retired = []

    async def retire(pid, p=None, **_kw):
        retired.append(pid)
        proc.returncode = -15
    monkeypatch.setattr(build_streaming, "_retire_turn_proc", retire)

    async def go():
        driving = asyncio.create_task(
            build_streaming._drive_turn(turn, meta, proc=proc))
        for _ in range(int(GRACE_S * 4 / 0.1)):
            await asyncio.sleep(0.1)
            if heartbeat and proc.returncode is None:
                with open(journal, "a") as fh:
                    fh.write(_line(type="system", subtype="task_progress",
                                   task_id="builder", description="Running"))
        if proc.returncode is None:
            proc.returncode = 0
        await asyncio.wait_for(driving, 5)
    asyncio.run(go())
    return retired


def test_a_builder_reporting_progress_is_not_retired(thread, monkeypatch):
    assert not _drive_live(thread["id"], monkeypatch, heartbeat=True)


def test_a_silent_builder_is_still_bounded(thread, monkeypatch):
    """The control: the grace still retires a task that died without
    reporting, so the heartbeat did not turn the bound off."""
    assert _drive_live(thread["id"], monkeypatch, heartbeat=False)
