"""The server-owned send queue.

These pin the contract that replaced a queue living in one browser's
localStorage — the arrangement that lost two of the operator's messages on
2026-08-12, when the tab that held them moved to another chat before the
command turn they were waiting on had finished.

What must be true:

  - a message the server accepted is on disk, under the thread, and readable by
    anything that opens that thread from anywhere;
  - it keeps the moment the operator hit enter, not the moment it was delivered;
  - accepting the same message twice (a retry, a second tab) queues it once;
  - a batch is formed the way a turn is formed: everything consecutive together,
    a slash command alone;
  - nothing leaves the queue until the turn carrying it has been recorded;
  - Stop holds the queue, and the operator's next send releases it.
"""
import pytest

from services import chat_log, chat_queue, chat_store, ownership


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


def _texts(entries):
    return [e["text"] for e in entries]


def test_enqueued_message_is_on_disk_not_in_a_browser(tid):
    chat_queue.enqueue(tid, text="the long one", client_id="c1")
    # Read through a fresh call: nothing is cached in the process either.
    assert _texts(chat_queue.read(tid)) == ["the long one"]
    assert chat_queue.queue_path(tid).exists()


def test_enqueue_is_idempotent_on_client_id(tid):
    chat_queue.enqueue(tid, text="hello", client_id="c1", ts="2026-08-12T17:44:00Z")
    # A retry of the same send: same id, and it must not become two messages.
    chat_queue.enqueue(tid, text="hello", client_id="c1", ts="2026-08-12T17:59:00Z")
    entries = chat_queue.read(tid)
    assert len(entries) == 1
    # ...and it keeps the send time it was FIRST queued with, because that is
    # when the operator actually hit enter.
    assert entries[0]["ts"] == "2026-08-12T17:44:00Z"


def test_order_is_when_the_operator_hit_enter(tid):
    # Queued out of order (a slow request for the earlier message), which is
    # exactly the case where arrival order is the wrong answer.
    chat_queue.enqueue(tid, text="second", client_id="c2", ts="2026-08-12T17:45:00Z")
    chat_queue.enqueue(tid, text="first", client_id="c1", ts="2026-08-12T17:44:00Z")
    assert _texts(chat_queue.read(tid)) == ["first", "second"]


def test_a_batch_is_everything_consecutive(tid):
    for i, t in enumerate(["one", "two", "three"]):
        chat_queue.enqueue(tid, text=t, client_id=f"c{i}", ts=f"2026-08-12T17:4{i}:00Z")
    assert _texts(chat_queue.next_batch(tid)) == ["one", "two", "three"]


def test_a_command_is_a_turn_by_itself(tid):
    chat_queue.enqueue(tid, text="/compact", client_id="c0", ts="2026-08-12T17:40:00Z")
    chat_queue.enqueue(tid, text="and then this", client_id="c1", ts="2026-08-12T17:41:00Z")
    # Joined to anything else a command stops being a command at all, so it goes
    # alone and the rest follows as the next turn.
    assert _texts(chat_queue.next_batch(tid)) == ["/compact"]
    chat_queue.drop(tid, ["c0"])
    assert _texts(chat_queue.next_batch(tid)) == ["and then this"]


def test_a_command_later_in_the_queue_breaks_the_batch(tid):
    chat_queue.enqueue(tid, text="one", client_id="c0", ts="2026-08-12T17:40:00Z")
    chat_queue.enqueue(tid, text="two", client_id="c1", ts="2026-08-12T17:41:00Z")
    chat_queue.enqueue(tid, text="/compact", client_id="c2", ts="2026-08-12T17:42:00Z")
    chat_queue.enqueue(tid, text="after", client_id="c3", ts="2026-08-12T17:43:00Z")
    assert _texts(chat_queue.next_batch(tid)) == ["one", "two"]


def test_stop_holds_and_the_next_send_releases(tid):
    chat_queue.enqueue(tid, text="typed ahead", client_id="c1")
    chat_queue.set_held(tid, True)
    # Stop hands the wheel back: nothing goes out on its own.
    assert chat_queue.next_batch(tid) == []
    assert chat_queue.state(tid)["queue_held"] is True
    # The operator sending again is them taking it back.
    chat_queue.enqueue(tid, text="ok go", client_id="c2")
    assert chat_queue.state(tid)["queue_held"] is False
    assert _texts(chat_queue.next_batch(tid)) == ["typed ahead", "ok go"]


def test_a_failed_drain_parks_the_queue_with_its_reason(tid):
    chat_queue.enqueue(tid, text="hello", client_id="c1")
    chat_queue.set_held(tid, True, error="developer mode is off")
    assert chat_queue.next_batch(tid) == []
    assert chat_queue.state(tid)["queue_error"] == "developer mode is off"
    # Nothing was thrown away: the text is still exactly where the operator can
    # see it, which is the whole difference between this and message loss.
    assert _texts(chat_queue.read(tid)) == ["hello"]
    # Releasing clears the diagnosis with the hold.
    chat_queue.set_held(tid, False)
    assert chat_queue.state(tid)["queue_error"] == ""
    assert _texts(chat_queue.next_batch(tid)) == ["hello"]


def test_cancelling_takes_one_message_back_out(tid):
    chat_queue.enqueue(tid, text="one", client_id="c1", ts="2026-08-12T17:40:00Z")
    chat_queue.enqueue(tid, text="two", client_id="c2", ts="2026-08-12T17:41:00Z")
    chat_queue.drop(tid, ["c1"])
    assert _texts(chat_queue.read(tid)) == ["two"]


def test_an_empty_queue_leaves_no_file(tid):
    chat_queue.enqueue(tid, text="one", client_id="c1")
    chat_queue.drop(tid, ["c1"])
    assert not chat_queue.queue_path(tid).exists()
    assert chat_queue.read(tid) == []


def test_a_thread_with_a_queue_is_findable_after_a_restart(tid):
    chat_queue.enqueue(tid, text="waiting", client_id="c1")
    # This is what the startup drain walks: it cannot depend on any client ever
    # opening the thread again.
    assert tid in chat_queue.threads_with_queues()


def test_attachments_and_settings_survive_the_wait(tid):
    chat_queue.enqueue(tid, text="look at this", client_id="c1",
                       attachments=[{"id": "a1", "name": "shot.png", "kind": "image"}],
                       model="claude-opus-5", effort="high")
    batch = chat_queue.next_batch(tid)
    assert chat_queue.attachments_of(batch) == [
        {"id": "a1", "name": "shot.png", "kind": "image"}]
    assert chat_queue.setting_of(batch, "model") == "claude-opus-5"
    assert chat_queue.setting_of(batch, "effort") == "high"


def test_the_last_explicit_setting_in_a_batch_wins(tid):
    chat_queue.enqueue(tid, text="one", client_id="c1", ts="2026-08-12T17:40:00Z",
                       model="claude-sonnet-5")
    chat_queue.enqueue(tid, text="two", client_id="c2", ts="2026-08-12T17:41:00Z",
                       model="claude-opus-5")
    # They changed model while messages waited; the turn runs as what they chose
    # most recently.
    assert chat_queue.setting_of(chat_queue.next_batch(tid), "model") == "claude-opus-5"


def test_joined_text_is_what_the_model_reads(tid):
    chat_queue.enqueue(tid, text="one", client_id="c1", ts="2026-08-12T17:40:00Z")
    chat_queue.enqueue(tid, text="two", client_id="c2", ts="2026-08-12T17:41:00Z")
    assert chat_queue.joined_text(chat_queue.next_batch(tid)) == "one\n\ntwo"


def test_a_corrupt_queue_file_does_not_wedge_the_chat(tid):
    chat_queue.enqueue(tid, text="one", client_id="c1")
    chat_queue.queue_path(tid).write_text("{not json")
    # Loud in the log, empty here: a queue that cannot be parsed must not block
    # every future send on the thread.
    assert chat_queue.read(tid) == []
    chat_queue.enqueue(tid, text="two", client_id="c2")
    assert _texts(chat_queue.read(tid)) == ["two"]


def test_forget_removes_everything_for_a_deleted_thread(tid):
    chat_queue.enqueue(tid, text="one", client_id="c1")
    chat_queue.forget(tid)
    assert not chat_queue.queue_path(tid).exists()


def test_is_command_matches_the_send_paths_rule():
    """The same rule as `_is_raw_command`, and it has to be.

    This decides what may be BATCHED, and the send path decides what is sent
    raw to the CLI. Anything the send path would send raw must be alone in its
    turn, so the two rules are one rule: a single line beginning with a slash.
    That includes "/compact the logs please" — the composer will not offer it as
    a command, but a message can arrive here from anywhere, and if it reaches
    the send path it goes raw, so it must not be glued to another message first.
    """
    assert chat_queue.is_command("/compact")
    assert chat_queue.is_command("  /clear  ")
    assert chat_queue.is_command("/compact the logs please")
    # Multi-line is not a command: joined to anything else it stops being one,
    # which is exactly the case batching must not create.
    assert not chat_queue.is_command("/compact\nand also this")
    assert not chat_queue.is_command("compact")


def test_the_queue_is_not_in_the_transcript(tid):
    """A queued message is not something that was said yet.

    It has to stay out of the log until it is actually delivered, or the model
    reads it once from the queue and again from history."""
    chat_queue.enqueue(tid, text="waiting", client_id="c1")
    events = chat_log.read_events(tid)
    assert not [e for e in events if e.get("kind") == "message"]
