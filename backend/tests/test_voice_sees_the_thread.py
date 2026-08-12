"""Voice is another interface to ONE conversation, not a second conversation.

The operator asked directly, mid-call: "are you able to see the past messages
before this particular audio session?" The answer was no. A conversational voice
worker spawns its own `claude -p` on its own session id and was fed only the
live-context block plus the utterance, so the model answering out loud had never
seen a typed message in the thread. Every one of the nine threads in the store
with spoken turns took that path.

Two defects, both "the model you are talking to has amnesia":
  1. a fresh voice session was never seeded with the thread;
  2. the session id lived only in an in-memory dict, so a backend restart lost it
     and the next utterance minted a new one, forgetting the spoken turns too.
"""
import pytest

from services import chat_store, ownership, voice_worker


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()


class TestTheSeed:
    def test_a_fresh_session_is_handed_what_was_typed(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "the analyser sidecar is on 8013")
        chat_store.append_message(tid, "assistant", "noted, 8013 it is")

        seed = voice_worker._thread_seed(tid)

        assert "the analyser sidecar is on 8013" in seed
        assert "noted, 8013 it is" in seed
        # It has to say it is a continuation, or the model opens by greeting
        # someone it is already mid-conversation with.
        assert "already in progress" in seed

    def test_speakers_are_named_so_the_model_knows_whose_words_are_whose(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "my line")
        chat_store.append_message(tid, "assistant", "my reply")
        seed = voice_worker._thread_seed(tid)
        assert "Operator: my line" in seed
        assert "You: my reply" in seed

    def test_an_empty_thread_seeds_nothing(self, thread):
        assert voice_worker._thread_seed(thread["id"]) == ""

    def test_a_half_written_reply_is_not_seeded_as_history(self, thread):
        # A run in flight renders as a trailing assistant message that is not in
        # the record yet. Seeding it hands the model its own unfinished sentence.
        tid = thread["id"]
        chat_store.append_message(tid, "user", "real question")
        chat_store.append_message(tid, "assistant", "half a thou",
                                  meta={"streaming": True})
        seed = voice_worker._thread_seed(tid)
        assert "real question" in seed
        assert "half a thou" not in seed

    def test_a_clear_is_honoured(self, thread):
        # /clear means none of this has been said. Voice must not be the one path
        # that quietly remembers it anyway.
        tid = thread["id"]
        chat_store.append_message(tid, "user", "before the clear")
        chat_store.clear_thread_context(tid)
        chat_store.append_message(tid, "user", "after the clear")

        seed = voice_worker._thread_seed(tid)
        assert "before the clear" not in seed
        assert "after the clear" in seed


class TestTheSessionSurvivesARestart:
    def test_clearing_the_thread_drops_the_voice_session(self, thread):
        tid = thread["id"]
        chat_store.append_message(tid, "user", "something")
        chat_store.update_thread(tid, voice_session="sess-abc")
        assert chat_store.get_thread(tid).get("voice_session") == "sess-abc"

        chat_store.clear_thread_context(tid)

        # Left behind, a /clear would spare the spoken half of the conversation:
        # talking after a clear would resume a model that still remembered
        # everything the clear was meant to drop.
        assert not chat_store.get_thread(tid).get("voice_session")

    def test_the_session_id_round_trips_through_the_store(self, thread):
        # WORKERS is in-memory; the store is the only copy that survives a
        # restart, which is the whole point of persisting it.
        tid = thread["id"]
        chat_store.update_thread(tid, voice_session="sess-xyz")
        assert chat_store.get_thread(tid).get("voice_session") == "sess-xyz"
