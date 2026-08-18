"""The thread change stream: clients learn about a change when it happens.

WHAT THIS REPLACES: a ten-second poll. Everything a client did not do itself
arrived up to ten seconds late on every other device, which the operator
reported as "it doesn't show up in the chat message in the other device until I
refresh".

The stream carries no content on purpose. It says a version changed; the client
then runs the same cursor-based sync it always had. Two owners of "what is in
this chat" is the shape of every reorder and eaten-message bug this system has
had, and a stream that carried the transcript would be a second one.
"""
import asyncio

import pytest

from services import chat_events, chat_log, chat_queue, chat_store, ownership


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def tid(owned):
    t = chat_store.create_thread("general")["id"]
    chat_events.forget(t)
    return t


def test_a_message_bumps_the_version(tid):
    before = chat_events.version(tid)
    chat_store.append_message(tid, "user", "hello")
    assert chat_events.version(tid) > before


def test_a_queued_message_bumps_it_too(tid):
    """A message the server took but has not sent is a change every open client
    has to see: it is the operator's text, waiting, and it renders."""
    before = chat_events.version(tid)
    chat_queue.enqueue(tid, text="typed during the command", client_id="c1")
    assert chat_events.version(tid) > before


def test_draining_the_queue_bumps_it(tid):
    chat_queue.enqueue(tid, text="waiting", client_id="c1")
    before = chat_events.version(tid)
    chat_queue.drop(tid, ["c1"])
    assert chat_events.version(tid) > before


def test_a_meta_write_bumps_it(tid):
    """The run in flight, the title, the cut and the summary all live in meta,
    and all of them change what a client renders."""
    before = chat_events.version(tid)
    chat_store.begin_turn(tid, "run-1")
    assert chat_events.version(tid) > before


def test_a_waiter_wakes_on_the_change(tid):
    async def go():
        waiter = asyncio.create_task(
            chat_events.wait(tid, chat_events.version(tid), timeout=5))
        await asyncio.sleep(0)          # let it start waiting
        chat_store.append_message(tid, "user", "hello")
        return await asyncio.wait_for(waiter, timeout=2)

    assert asyncio.run(go()) > 0


def test_a_client_that_is_already_behind_does_not_wait(tid):
    """A reconnect that missed a change wakes immediately rather than one
    keepalive behind."""
    chat_store.append_message(tid, "user", "hello")
    current = chat_events.version(tid)

    async def go():
        # A timeout of zero would still return the current version, because the
        # caller is behind and there is nothing to wait for.
        return await chat_events.wait(tid, current - 1, timeout=0.01)

    assert asyncio.run(go()) == current


def test_it_times_out_quietly_when_nothing_happens(tid):
    async def go():
        return await chat_events.wait(tid, chat_events.version(tid), timeout=0.05)

    assert asyncio.run(go()) == chat_events.version(tid)


def test_a_live_draft_does_NOT_bump_it(tid):
    """A running turn rewrites its draft hundreds of times and has its own
    stream. Bumping on it would wake every idle client on every token to tell it
    something it is already being told."""
    chat_store.begin_turn(tid, "run-1")
    before = chat_events.version(tid)
    chat_log.write_live_sync(tid, {"turn_id": "run-1", "content": "partial"})
    assert chat_events.version(tid) == before


def test_a_notification_failure_cannot_break_the_write(tid, monkeypatch):
    def boom(_):
        raise RuntimeError("no")

    monkeypatch.setattr(chat_events, "bump", boom)
    # The write is what matters; the notification is a latency optimization over
    # a poll that still runs behind it.
    chat_store.append_message(tid, "user", "hello")
    msgs = [e for e in chat_log.read_events(tid) if e.get("kind") == "message"]
    assert [m["content"] for m in msgs] == ["hello"]
