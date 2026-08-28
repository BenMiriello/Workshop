"""A message put into a thread by something other than the operator says so.

The mailbox has always been the only door into a thread, and it has always been
able to carry a message from something that is not a person: a finished run, a
trigger, another thread. What it could not do was say so. `control.queue` took
an `actor`, passed it to `run_control.submit` on the sweep branch, and DROPPED it
on the chat branch, one line before it would have been stored.

That single omission is why a thread cannot address a thread. The endpoint
exists (`POST /control/{id}/requests`), the mailbox exists (`chat_queue`), the
drain exists. The sender was the missing field, and the only signal in
production was `meta.autonomous`, set on 2 of 2,723 messages by three producers
each guessing at write time.

Recorded in `meta` rather than a new column deliberately: `meta` is already
persisted and mirrored, absence already means the operator, and a column needs a
migration against the live database that an agent cannot apply. The column is a
follow-up, not a thing this skipped.
"""
from __future__ import annotations

import pytest

from services import chat_queue

# Thread ids are uuid4 hex; chat_log._check_id refuses anything else so a thread
# id can never address a path outside the owner's chat dir.
T1 = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _queue_in_tmp(tmp_path, monkeypatch):
    """The queue is a file store under the owner's runtime dir, so it needs both
    a redirected path and a pinned owner."""
    from services import ownership

    ownership.pin_operator()
    monkeypatch.setattr(chat_queue, "queue_path",
                        lambda tid: tmp_path / f"{tid}.json")


class TestTheQueueCarriesIt:
    def test_a_sender_is_stored_on_the_entry(self):
        chat_queue.enqueue(T1, text="hello", client_id="c1",
                           sender="thread:abc")
        assert chat_queue.next_batch(T1)[0]["sender"] == "thread:abc"

    def test_no_sender_means_the_operator_and_stores_nothing(self):
        """Absence keeps its existing meaning, so no message already in the log
        becomes wrong and no backfill is owed."""
        chat_queue.enqueue(T1, text="hello", client_id="c1")
        assert "sender" not in chat_queue.next_batch(T1)[0]

    def test_it_survives_the_idempotent_re_enqueue(self):
        """A retry arrives under the id the message was first sent with. It must
        not lose the sender, or a retried agent send becomes an operator one."""
        chat_queue.enqueue(T1, text="hello", client_id="c1", sender="run:7")
        chat_queue.enqueue(T1, text="hello", client_id="c1", sender="run:7")
        batch = chat_queue.next_batch(T1)
        assert len(batch) == 1 and batch[0]["sender"] == "run:7"


class TestControlStopsDroppingTheActor:
    def test_a_queued_chat_request_records_its_actor(self, monkeypatch):
        """The regression itself: control.queue passed actor to the sweep branch
        and dropped it on the chat branch."""
        from services import control

        seen = {}

        def fake_enqueue(thread_id, **kw):
            seen.update(kw)
            seen["thread_id"] = thread_id
            return {}

        monkeypatch.setattr(control.chat_queue, "enqueue", fake_enqueue)
        monkeypatch.setattr(control.chat_drain, "drain_soon",
                            lambda *a, **k: None)
        monkeypatch.setattr(control, "_resolved_kind", lambda *a, **k: "chat")
        monkeypatch.setattr(control, "pending", lambda *a, **k: [])

        control.queue(T1, "do the thing", actor="thread:other")
        assert seen.get("sender") == "thread:other", (
            "control.queue dropped the actor on the chat branch")

    def test_an_operator_queue_still_records_no_sender(self, monkeypatch):
        from services import control

        seen = {}

        def fake_enqueue(thread_id, **kw):
            seen.update(kw)
            return {}

        monkeypatch.setattr(control.chat_queue, "enqueue", fake_enqueue)
        monkeypatch.setattr(control.chat_drain, "drain_soon",
                            lambda *a, **k: None)
        monkeypatch.setattr(control, "_resolved_kind", lambda *a, **k: "chat")
        monkeypatch.setattr(control, "pending", lambda *a, **k: [])

        control.queue(T1, "do the thing")
        assert seen.get("sender") is None


class TestItReachesTheStoredMessage:
    """The half that matters: a sender that stops at the queue is not a sender."""

    def _record(self, monkeypatch, parts):
        from routes import chat as chat_routes

        written = []

        def cap(*a, **kw):
            written.append(kw)
            return None

        monkeypatch.setattr(chat_routes.chat_store,
                            "append_message_during_turn", cap)
        monkeypatch.setattr(chat_routes.chat_store, "append_message", cap)
        chat_routes._record_user_turn(T1, "joined", parts)
        return written

    def test_a_single_queued_part_carries_it_into_meta(self, monkeypatch):
        from routes.chat import TurnPart

        written = self._record(monkeypatch, [
            TurnPart(text="hello", sent_at="2026-08-27T00:00:00Z",
                     client_id="c1", sender="thread:abc")])
        assert written[0]["meta"]["sender"] == "thread:abc"
        assert written[0]["meta"]["queued"] is True

    def test_an_operator_part_records_no_sender_key(self, monkeypatch):
        from routes.chat import TurnPart

        written = self._record(monkeypatch, [
            TurnPart(text="hello", sent_at="2026-08-27T00:00:00Z",
                     client_id="c1")])
        assert "sender" not in written[0]["meta"]

    def test_each_part_of_a_batch_keeps_its_own_sender(self, monkeypatch):
        """Per part, like the send time beside it. Copying one sender onto every
        part is the same class of error as dating them all at the drain."""
        from routes.chat import TurnPart

        written = self._record(monkeypatch, [
            TurnPart(text="one", sent_at="2026-08-27T00:00:01Z", client_id="c1",
                     sender="thread:abc"),
            TurnPart(text="two", sent_at="2026-08-27T00:00:02Z", client_id="c2"),
        ])
        assert written[0]["meta"]["sender"] == "thread:abc"
        assert "sender" not in written[1]["meta"]
