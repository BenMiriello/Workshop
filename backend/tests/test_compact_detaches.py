"""/compact reclaims the LIVE window, and that is all it does.

It used to be sent to the CLI as a raw turn for the CLI's own `/compact` to run,
and the turn at 2026-08-12T05:10:18Z came back in two seconds having recorded

    user    <command-name>/compact</command-name>
    system  local_command
            <local-command-caveat> ... DO NOT respond to these messages

with no `compact_boundary` after it and a meter still reading what it read
before. That was read as "the command does not execute in headless mode", and
on 2026-08-27 that reading was measured false: those records are what a working
`/compact` writes too, and the boundary lands at the END of an operation taking
12 to 190 seconds, so a two-second read sees the start of a compaction and
calls it a no-op. See `routes.chat.compact_thread`.

What these tests pin is unaffected, because it is about OUR side: detaching the
session is a lever that works from here regardless. The next turn mints a fresh
one and re-sends the transcript, so the window drops because the session holding
it is gone.

The line these tests hold is that /compact is NOT /clear. It frees the window
without moving the cut, so nothing the operator can see goes away.
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


def test_it_drops_the_live_session(thread):
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")
    chat_store.ensure_thread_session(tid)

    r = asyncio.run(rc.compact_thread(tid))

    assert r["session_reset"] is True
    assert not chat_store.get_thread(tid).get("claude_session")


def test_the_next_turn_mints_a_fresh_session(thread):
    """The reclaim is only real if the CLI is not handed the old window back."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")
    before, _ = chat_store.ensure_thread_session(tid)

    asyncio.run(rc.compact_thread(tid))
    after, is_new = chat_store.ensure_thread_session(tid)

    assert is_new is True
    assert after != before


def test_the_stale_context_reading_goes_with_it(thread):
    """142k on a four-message chat: the number that made /compact look dead.

    It measured the window of the session being dropped, so it cannot outlive
    it. Absent the key the meter re-estimates from the prompt, which is right."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")
    chat_store.ensure_thread_session(tid)
    chat_store.set_thread_context_tokens(tid, 142_000)

    asyncio.run(rc.compact_thread(tid))

    assert not chat_store.get_thread(tid).get("context_tokens")


def test_it_does_not_cut_the_transcript(thread):
    """The difference between /compact and /clear, pinned. /compact drops the
    CLI's private copy of a conversation that the transcript can rebuild;
    nothing the operator can read is hidden by it."""
    rc = _routes()
    tid = thread["id"]
    for i in range(6):
        chat_store.append_message(tid, "user", f"m{i}")
    chat_store.ensure_thread_session(tid)
    before = chat_store.get_thread(tid)

    asyncio.run(rc.compact_thread(tid))
    after = chat_store.get_thread(tid)

    assert len(after["messages"]) == len(before["messages"]) == 6
    # And the cut does not move. /compact reclaims the live session's window;
    # only /clear takes a message out of the prompt.
    assert after.get("cleared_through", 0) == before.get("cleared_through", 0) == 0


def test_nothing_to_reclaim_says_so(thread):
    """So the client can report "nothing happened" only when nothing did."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "m0")

    r = asyncio.run(rc.compact_thread(tid))

    assert r["session_reset"] is False


def test_unknown_thread_404s(thread):
    from fastapi import HTTPException
    rc = _routes()
    with pytest.raises(HTTPException) as e:
        asyncio.run(rc.compact_thread("0" * 32))
    assert e.value.status_code == 404
