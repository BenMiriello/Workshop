"""The queue drains itself, with no browser involved.

This is the other half of `test_chat_queue`: that one pins what the queue
stores, this one pins that something actually sends it.

THE INCIDENT THESE REPLAY (2026-08-12): `/compact` ran as a turn that reads no
input, the operator typed two long messages during it, and the only copy lived
in the tab's localStorage. The tab moved to another chat before the command
finished; the turn ended with nothing bound to that thread; the next page load
came from another device. Both messages were gone, and the server had never
heard of them.

Every test below would have failed on that build.
"""
import asyncio

import pytest

from services import chat_drain, chat_log, chat_queue, chat_store, ownership


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
    return chat_store.create_thread("general")["id"]


@pytest.fixture()
def starter(monkeypatch):
    """Stands in for the send path. Records the batches it was handed."""
    calls: list[list[dict]] = []

    async def fake(thread_id: str, batch: list[dict]) -> bool:
        calls.append(batch)
        return True

    monkeypatch.setattr(chat_drain, "_starter", fake)
    monkeypatch.setattr(chat_drain, "_locks", {})
    return calls


def test_the_queue_drains_with_no_client_anywhere(tid, starter):
    chat_queue.enqueue(tid, text="the first long one", client_id="c1",
                       ts="2026-08-12T17:44:20Z")
    chat_queue.enqueue(tid, text="the second long one", client_id="c2",
                       ts="2026-08-12T17:44:52Z")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is True
    # One turn, both messages, in the order they were typed.
    assert [e["text"] for e in starter[0]] == ["the first long one", "the second long one"]
    # ...and they are out of the queue now that a turn carries them.
    assert chat_queue.read(tid) == []


def test_nothing_drains_while_a_run_is_in_flight(tid, starter):
    chat_queue.enqueue(tid, text="typed during the command", client_id="c1")
    chat_store.begin_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is False
    assert starter == []
    # Still queued, still on disk, still visible to every device.
    assert [e["text"] for e in chat_queue.read(tid)] == ["typed during the command"]


def test_it_drains_once_that_run_ends(tid, starter):
    chat_queue.enqueue(tid, text="typed during the command", client_id="c1")
    chat_store.begin_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is False
    chat_store.release_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is True
    assert [e["text"] for e in starter[0]] == ["typed during the command"]


def test_a_held_queue_does_not_fire_unattended(tid, starter):
    """Stop means stop.

    Stopping ends the run, and the drain fires on every run ending — so without
    the hold, Stop would immediately send everything typed ahead as a turn
    nobody asked for."""
    chat_queue.enqueue(tid, text="typed ahead", client_id="c1")
    chat_queue.set_held(tid, True)
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is False
    assert starter == []


def test_a_failed_start_leaves_the_messages_queued(tid, monkeypatch):
    async def boom(thread_id: str, batch: list[dict]) -> bool:
        raise RuntimeError("developer mode is off")

    monkeypatch.setattr(chat_drain, "_starter", boom)
    monkeypatch.setattr(chat_drain, "_locks", {})
    chat_queue.enqueue(tid, text="hello", client_id="c1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is False
    # NOT LOST. Parked on the reason, where the operator can see both the text
    # and why it has not gone.
    assert [e["text"] for e in chat_queue.read(tid)] == ["hello"]
    assert chat_queue.state(tid)["queue_error"] == "developer mode is off"


def test_a_starter_that_declines_keeps_the_batch(tid, monkeypatch):
    """A run claimed between the check and the call is not an error."""
    async def declined(thread_id: str, batch: list[dict]) -> bool:
        return False

    monkeypatch.setattr(chat_drain, "_starter", declined)
    monkeypatch.setattr(chat_drain, "_locks", {})
    chat_queue.enqueue(tid, text="hello", client_id="c1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is False
    assert [e["text"] for e in chat_queue.read(tid)] == ["hello"]


def test_startup_delivers_what_a_restart_interrupted(owned, starter):
    """A backend that died mid-turn comes back with the turn gone and the queue
    still there. Nothing else would ever notice: the browser that queued those
    messages may never open that chat again."""
    a = chat_store.create_thread("general")["id"]
    b = chat_store.create_thread("general")["id"]
    chat_queue.enqueue(a, text="waiting on a", client_id="a1")
    chat_queue.enqueue(b, text="waiting on b", client_id="b1")
    assert asyncio.run(chat_drain.drain_all(reason="test-startup")) == 2
    assert {batch[0]["text"] for batch in starter} == {"waiting on a", "waiting on b"}


def test_a_command_goes_out_alone_then_the_rest_follow(tid, starter):
    chat_queue.enqueue(tid, text="/compact", client_id="c1", ts="2026-08-12T17:40:00Z")
    chat_queue.enqueue(tid, text="and then this", client_id="c2", ts="2026-08-12T17:41:00Z")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is True
    assert [e["text"] for e in starter[0]] == ["/compact"]
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is True
    assert [e["text"] for e in starter[1]] == ["and then this"]


def test_a_deleted_thread_does_not_hold_a_queue_forever(owned, starter):
    tid = chat_store.create_thread("general")["id"]
    chat_queue.enqueue(tid, text="orphan", client_id="c1")
    chat_store.delete_thread(tid)
    # Nothing can carry it, and it must not be retried forever either.
    from routes import chat as chat_routes
    assert asyncio.run(chat_routes._start_queued_batch(tid, chat_queue.read(tid))) is False
    assert chat_queue.read(tid) == []


def test_a_drained_message_keeps_the_time_it_was_typed(tid, monkeypatch):
    """A message that waited is recorded at the moment the operator hit enter,
    not at the moment it went out.

    The transcript the operator reads has to agree with the queue they were
    watching: text written at 17:44 and delivered at 17:46 that reads as 17:46
    is the record disagreeing with what they saw."""
    from routes import chat as chat_routes

    started: list[str] = []

    async def fake_run(thread_id, t, content, model, parts, **kw):
        chat_routes._record_user_turn(thread_id, content, parts,
                                      client_id=kw.get("client_id"))
        started.append(content)
        return None

    monkeypatch.setattr(chat_routes, "_turn_for_message", fake_run)
    chat_queue.enqueue(tid, text="typed while it compacted", client_id="c1",
                       ts="2026-08-12T17:44:20+00:00")
    assert asyncio.run(chat_routes._start_queued_batch(tid, chat_queue.read(tid))) is True
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "user"]
    assert len(msgs) == 1
    assert msgs[0]["ts"].startswith("2026-08-12T17:44:20")
    # ...and under the id the browser has been rendering it with, so the bubble
    # on screen is reconciled instead of drawn a second time.
    assert msgs[0]["client_id"] == "c1"


def test_nothing_overtakes_something_already_waiting(tid, starter):
    """The window between a command turn ending and the drain firing.

    A message sent in it would find the thread idle and start a turn of its own,
    ahead of messages the operator typed EARLIER and that are still queued. The
    transcript would then record the two in the wrong order, permanently."""
    import asyncio as _a
    from routes import chat as chat_routes

    chat_queue.enqueue(tid, text="typed first", client_id="c1",
                       ts="2026-08-12T17:44:00Z")

    async def go():
        # Nothing is running: the thread looks idle to a fresh send.
        out = await chat_routes._send(tid, "typed second", client_id="c2",
                                      sent_at="2026-08-12T17:45:00Z")
        # It JOINED the queue rather than starting a turn of its own...
        assert out["queued"] is True
        # ...and the drain the enqueue kicked off sends them together, in the
        # order they were typed, as ONE turn.
        await chat_drain.drain(tid, reason="test")

    _a.run(go())
    assert [e["text"] for e in starter[0]] == ["typed first", "typed second"]
    assert chat_queue.read(tid) == []


def test_a_batch_already_committed_is_not_sent_twice(tid, starter):
    """A crash between recording the turn and clearing the queue.

    The entries leave the queue only after the turn carrying them is recorded,
    so a crash in between leaves them queued AND committed. The log dedupes on
    client_id so the transcript is safe, but the model would be handed the
    message a second time."""
    import asyncio as _a
    from routes import chat as chat_routes

    chat_queue.enqueue(tid, text="already went out", client_id="c1")
    chat_store.append_message(tid, "user", "already went out", client_id="c1")
    assert _a.run(chat_routes._start_queued_batch(tid, chat_queue.read(tid))) is False
    assert chat_queue.read(tid) == []


def test_a_continuation_seed_starts_a_turn_attributed_to_no_one(tid, monkeypatch):
    """Fire-and-continue. A finished background run leaves an auto seed; the
    drain must start a turn from it WITHOUT recording an operator message, so the
    chat continues on its own with nothing put in the operator's mouth."""
    import asyncio as _a
    from routes import chat as chat_routes

    seen: list[dict] = []

    async def fake_run(thread_id, t, content, model, parts, **kw):
        seen.append({"content": content, "already_recorded": kw.get("already_recorded"),
                     "parts": parts})
        # A real turn with already_recorded=True records nothing here.
        return None

    monkeypatch.setattr(chat_routes, "_turn_for_message", fake_run)
    chat_drain.request_continuation(tid, seed="read the result above and continue",
                                    dedupe_id="job1-" + tid)
    # The starter is what decides HOW a batch becomes a turn; the drop-after is
    # drain()'s job and is covered by the batching tests. Here we pin the routing:
    # a lone auto seed starts a turn with already_recorded=True and no user turn.
    assert _a.run(chat_routes._start_queued_batch(tid, chat_queue.read(tid))) is True
    assert len(seen) == 1
    assert seen[0]["content"] == "read the result above and continue"
    assert seen[0]["already_recorded"] is True
    assert seen[0]["parts"] is None
    # No operator (user) message was written to the log.
    users = [e for e in chat_log.read_events(tid)
             if e.get("kind") == "message" and e.get("role") == "user"]
    assert users == []


def test_a_continuation_seed_is_idempotent_on_the_job(tid):
    """Re-finishing the same run (a retry, a restart replaying the close) must not
    stack continuations: the seed dedupes on the job+thread id."""
    chat_drain.request_continuation(tid, seed="continue once", dedupe_id="jobX-" + tid)
    chat_drain.request_continuation(tid, seed="continue once", dedupe_id="jobX-" + tid)
    seeds = [e for e in chat_queue.read(tid) if e.get("auto")]
    assert len(seeds) == 1


def test_a_continuation_waits_behind_operator_messages(tid, starter):
    """An operator message typed before a run finished goes out first; the seed
    is its own turn afterward, never batched into the operator's."""
    chat_queue.enqueue(tid, text="what the operator typed", client_id="c1",
                       ts="2026-08-20T10:00:00Z")
    chat_drain.request_continuation(tid, seed="now continue", dedupe_id="jobY-" + tid)
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is True
    assert [e["text"] for e in starter[0]] == ["what the operator typed"]
    assert not any(e.get("auto") for e in starter[0])


def test_a_continuation_does_not_fire_while_a_turn_runs(tid, starter):
    """The seed waits for the thread to be genuinely idle, same as any queued
    message: a continuation must never start a rival to a live turn."""
    chat_drain.request_continuation(tid, seed="continue", dedupe_id="jobZ-" + tid)
    chat_store.begin_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is False
    assert starter == []
    chat_store.release_turn(tid, "run-1")
    assert asyncio.run(chat_drain.drain(tid, reason="test")) is True
    assert starter[0][0].get("auto") is True


def test_an_archived_chat_does_not_fire_its_queue(tid, starter):
    """Archiving puts a conversation away. A queue firing afterwards would start
    a turn in a chat the operator has closed. The messages are not dropped: they
    wait, and restoring the chat restores them with it."""
    import asyncio as _a
    from routes import chat as chat_routes

    chat_queue.enqueue(tid, text="typed before it was archived", client_id="c1")
    chat_store.archive_thread(tid)
    assert _a.run(chat_routes._start_queued_batch(tid, chat_queue.read(tid))) is False
    assert [e["text"] for e in chat_queue.read(tid)] == ["typed before it was archived"]
    st = chat_queue.state(tid)
    assert st["queue_held"] is True and "archived" in st["queue_error"]
