"""The context meter measures THIS conversation, not a delegate's.

`context_tokens` is the size of the window the model actually read, taken from
the usage block of the latest assistant call. A subagent's assistant messages
arrive on the same stream, carrying `parent_tool_use_id`, and a delegate runs on
its own fresh context: tens of thousands of tokens against a parent's hundreds
of thousands.

Without a parent check, the last delegate to speak overwrote the thread's
reading. The operator watched the meter fall from 44% to 8% without the
conversation having lost a single message, and reasonably read it as the
counter being broken. It was measuring the wrong conversation.

Spend is the opposite case, and these tests pin that too: a delegate's tokens
are really spent, so they must keep accumulating.
"""
import asyncio
import json

import pytest

from services import build_streaming, chat_store, ownership


PARENT_CTX = 440_000
CHILD_CTX = 80_000
AGENT_ID = "toolu_child"


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

    def die(self, rc: int = 0) -> None:
        self.returncode = rc


def _usage(ctx: int, out: int = 10) -> dict:
    # Split across the three input buckets the way a real cached call reports,
    # so the test also pins that _ctx_tokens_from_usage sums all three.
    return {"input_tokens": ctx // 4,
            "cache_read_input_tokens": ctx - (ctx // 4),
            "cache_creation_input_tokens": 0,
            "output_tokens": out}


def _assistant(text: str, ctx: int, parent: str | None = None) -> str:
    ev = {"type": "assistant",
          "message": {"content": [{"type": "text", "text": text}], "usage": _usage(ctx)}}
    if parent:
        ev["parent_tool_use_id"] = parent
    return json.dumps(ev)


def _agent_launch() -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": AGENT_ID, "name": "Agent",
         "input": {"description": "Go and look", "prompt": "look"}}]}})


def _result() -> str:
    return json.dumps({"type": "result", "subtype": "success", "usage": _usage(1)})


def _journal(turn_id: str, lines: list[str], thread_id: str) -> dict:
    d = build_streaming._journal_dir(turn_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stream.jsonl").write_text("".join(l + "\n" for l in lines))
    return {"turn_id": turn_id, "thread_id": thread_id, "agent_slug": "a", "cwd": "",
            "model": "m", "session_id": None, "resume": False, "speaker": None,
            "owner_id": 1, "prompt": "do the thing", "timeout_s": 0,
            "pid": 4243, "status": "running", "started": 0.0,
            "allowed_tools": None, "disallowed_tools": None, "mcp_servers": None,
            "tools_disabled": False}


async def _run(thread_id: str, lines: list[str]) -> None:
    turn_id = "run-ctx"
    chat_store.begin_turn(thread_id, turn_id, model="m")
    turn = build_streaming.Turn(id=turn_id, thread_id=thread_id, agent_slug="a")
    turn.spawned = True
    build_streaming.TURNS[turn_id] = turn
    meta = _journal(turn_id, lines, thread_id)
    proc = FakeProc()
    turn.proc, turn.pid = proc, proc.pid
    driving = asyncio.create_task(build_streaming._drive_turn(turn, meta, proc=proc))
    await asyncio.sleep(0.05)
    proc.die()
    await asyncio.wait_for(driving, 5)


class TestTheMeterMeasuresThisConversation:
    def test_a_delegates_context_does_not_become_the_threads(self, thread):
        # The parent speaks, delegates, and the delegate speaks LAST. Before the
        # parent check, the delegate's 80k landed on the thread.
        asyncio.run(_run(thread["id"], [
            _assistant("planning", PARENT_CTX),
            _agent_launch(),
            _assistant("reading files", CHILD_CTX, parent=AGENT_ID),
            _result(),
        ]))
        t = chat_store.get_thread(thread["id"])
        assert t["context_tokens"] == PARENT_CTX

    def test_the_threads_own_latest_call_still_wins(self, thread):
        # Two of the parent's own calls: the LAST one is the window size.
        asyncio.run(_run(thread["id"], [
            _assistant("first", 100_000),
            _assistant("second", PARENT_CTX),
            _result(),
        ]))
        t = chat_store.get_thread(thread["id"])
        assert t["context_tokens"] == PARENT_CTX

    def test_a_turn_that_is_only_delegation_reports_no_context(self, thread):
        # Nothing the parent read was ever reported, so the thread must not
        # inherit a number that describes someone else's window.
        asyncio.run(_run(thread["id"], [
            _agent_launch(),
            _assistant("reading files", CHILD_CTX, parent=AGENT_ID),
            _result(),
        ]))
        t = chat_store.get_thread(thread["id"])
        assert t.get("context_tokens") != CHILD_CTX

    def test_a_delegate_is_still_billed(self, thread, monkeypatch):
        # The opposite rule, and the reason this is not simply "ignore a
        # delegate's usage": its tokens are really spent. Spend accumulates
        # across every call; only the WINDOW reading is parent-only.
        import services.usage_ledger as ul
        seen: list[int] = []
        # Called positionally as record(project, tokens, ...), and the call site
        # swallows exceptions, so a keyword-only stub would silently record
        # nothing and the test would pass for the wrong reason.
        monkeypatch.setattr(ul, "record", lambda project, tokens, **kw: seen.append(int(tokens)))
        asyncio.run(_run(thread["id"], [
            _assistant("planning", PARENT_CTX),
            _agent_launch(),
            _assistant("reading files", CHILD_CTX, parent=AGENT_ID),
            _result(),
        ]))
        # Whatever the ledger was told, it is more than the parent call alone,
        # so the delegate's spend was not dropped along with its window size.
        assert seen, "the turn recorded no spend at all"
        assert max(seen) > 0
