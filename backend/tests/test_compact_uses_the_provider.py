"""Compaction is the provider's own `/compact`, sent as a turn.

THIS FILE USED TO BE `test_compact_detaches.py` AND IT HUNG THE SUITE.

It pinned the lever Workshop pulled when it did its own folding: detach the
live CLI session, drop the stale context reading, leave the transcript alone.
`5be52599` deleted that fold in favour of sending the CLI's own `/compact`, so
`compact_thread` stopped resetting the session and started sending a turn --
and these tests kept calling it for real. Five of the six asserted a
`session_reset` key the response no longer has, and every one of them blocked
on a live CLI spawn that never returns under pytest.

The cost was not five red tests. `pytest tests/` stopped at 32% and hung there,
so the whole backend suite has been unrunnable, which is the reason
`reviewer.sh` still gates on mypy alone and no backend test has guarded a merge
since. Found 2026-08-31 by running the suite to the hang and reading the last
test it started.

So the seam is `_send`, and it is stubbed. What is worth pinning here is the
DECISION -- that Workshop asks the provider to compact rather than summarising
anything itself -- and the transcript being untouched by it.
"""
import asyncio

import pytest

from services import chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()


def _routes():
    import routes.chat as rc
    return rc


@pytest.fixture()
def sent(monkeypatch):
    """Records what `compact_thread` sends, and sends nothing.

    `_send` spawns a real CLI turn. Calling it under pytest is what hung the
    suite, so no test in this file may reach it.
    """
    calls: list[tuple[str, str]] = []

    async def fake_send(thread_id, content, *a, **kw):
        calls.append((thread_id, content))
        return {"turn_id": "t-1"}

    rc = _routes()
    monkeypatch.setattr(rc, "_send", fake_send)
    # The working-state note is fired and not awaited, and it reads the notes
    # store. It is not what these tests are about.
    monkeypatch.setattr(rc, "_spawn_working_state", lambda *a, **kw: None)
    return calls


def test_it_sends_the_providers_own_compact_command(thread, sent):
    """The whole decision, in one assertion.

    Workshop does not summarise: it says `/compact` and the CLI answers the
    question "what should a compaction keep". A single-line message starting
    with "/" is sent verbatim (`_is_raw_command`), which is what makes the CLI
    register a command rather than read the text.
    """
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")

    asyncio.run(rc.compact_thread(tid))

    assert sent == [(tid, "/compact")]


def test_it_returns_the_turn_that_is_now_running(thread, sent):
    """It does not wait for the boundary. The divider and the new meter reading
    arrive on the change stream like any other turn's output."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")

    r = asyncio.run(rc.compact_thread(tid))

    assert r == {"turn_id": "t-1"}


def test_queued_is_an_acceptance_not_a_refusal(thread, monkeypatch):
    """A command cannot be spoken into a running turn, so it waits in the
    thread's queue and the drain sends it when the turn ends. Reporting that as
    "busy, nothing happened" would be false."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")

    async def queued(*a, **kw):
        return {"queued": True}

    monkeypatch.setattr(rc, "_send", queued)
    monkeypatch.setattr(rc, "_spawn_working_state", lambda *a, **kw: None)

    assert asyncio.run(rc.compact_thread(tid)) == {"queued": True}


def test_a_failure_to_start_is_reported_not_a_500(thread, monkeypatch):
    """The button reports what went wrong instead of throwing."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")

    async def boom(*a, **kw):
        raise RuntimeError("no session")

    monkeypatch.setattr(rc, "_send", boom)
    monkeypatch.setattr(rc, "_spawn_working_state", lambda *a, **kw: None)

    assert asyncio.run(rc.compact_thread(tid)) == {"error": "no session"}


def test_it_does_not_cut_the_transcript(thread, sent):
    """The difference between /compact and /clear, pinned. Compaction shrinks
    what the MODEL is handed, never what the operator can read, so the messages
    stay and the cut does not move."""
    rc = _routes()
    tid = thread["id"]
    for i in range(6):
        chat_store.append_message(tid, "user", f"m{i}")
    before = chat_store.get_thread(tid)

    asyncio.run(rc.compact_thread(tid))
    after = chat_store.get_thread(tid)

    assert len(after["messages"]) == len(before["messages"]) == 6
    assert after.get("cleared_through", 0) == before.get("cleared_through", 0) == 0


def test_unknown_thread_404s(thread):
    from fastapi import HTTPException
    rc = _routes()
    with pytest.raises(HTTPException) as e:
        asyncio.run(rc.compact_thread("0" * 32))
    assert e.value.status_code == 404
