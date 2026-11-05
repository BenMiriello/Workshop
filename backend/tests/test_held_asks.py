"""A call whose card went unanswered runs without a card once the operator's
next message has come in (services/held_asks, kept by chat_store)."""
from __future__ import annotations

import pytest

from services import build_streaming, chat_store, ownership

RM = ("Bash", {"command": "rm -rf /srv/models/old"})


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    chat_store._SUMMARY_CACHE.clear()
    try:
        yield chat_store.create_thread("general")["id"]
    finally:
        ownership.clear_current_owner()
        chat_store._SUMMARY_CACHE.clear()


def test_the_next_operator_message_approves_the_held_call_once(thread):
    chat_store.hold_ask(thread, *RM)
    assert not chat_store.take_approved_ask(thread, *RM)
    chat_store.append_message(thread, "user", "yes, delete them")
    assert not chat_store.take_approved_ask(thread, "Bash", {"command": "rm -rf /srv"})
    assert chat_store.take_approved_ask(thread, *RM)
    assert not chat_store.take_approved_ask(thread, *RM)


def test_a_message_from_something_else_approves_nothing(thread):
    chat_store.hold_ask(thread, *RM)
    chat_store.append_message(thread, "user", "wake up", meta={"sender": "schedule"})
    assert not chat_store.take_approved_ask(thread, *RM)


def test_an_unused_approval_lasts_one_message(thread):
    chat_store.hold_ask(thread, *RM)
    chat_store.append_message_during_turn(thread, "user", "yes")
    chat_store.append_message_during_turn(thread, "user", "actually, something else")
    assert not chat_store.take_approved_ask(thread, *RM)


def test_a_retried_send_does_not_spend_the_approval(thread):
    chat_store.hold_ask(thread, *RM)
    chat_store.append_message(thread, "user", "yes", client_id="c1")
    chat_store.append_message(thread, "user", "yes", client_id="c1")
    assert chat_store.take_approved_ask(thread, *RM)


def test_the_refusal_tells_the_turn_the_next_message_approves():
    assert "next message" in build_streaming.UNANSWERED_MESSAGE
    assert "list each action" not in build_streaming.UNANSWERED_MESSAGE
