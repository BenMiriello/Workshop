"""A chat names itself, and keeps trying until it does.

The operator's report: "the chat cards show the last message I sent as their
title". Two defects made that one symptom.

  1. Naming fired only when EXACTLY ONE assistant reply existed. That is a single
     attempt in a single moment. Anything transient landing there — a rate limit,
     a restart mid-call, a turn that ended in error — missed the window, and the
     count could never be 1 again, so the chat stayed "New chat" for its whole
     life.
  2. The fallback the list shows when a chat has no name (`_seed_title`) was
     REWRITTEN on every user message, so it tracked the latest thing said instead
     of standing in for a subject.

Together: a title that was always the operator's most recent message.
"""
import pytest

from services import chat_naming, chat_store, ownership


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def named(monkeypatch):
    """Capture the naming prompt without calling a model."""
    seen = {}

    def fake_call(prompt, **kw):
        seen["prompt"] = prompt
        seen["kwargs"] = kw
        return {"text": "Subagent Delegation In Chat"}

    monkeypatch.setattr(chat_naming.llm_router, "call_cloud", fake_call)
    return seen


def _exchange(tid, user="How do subagents work here?", assistant="They run in an isolated context."):
    chat_store.append_message(tid, "user", user)
    chat_store.append_message(tid, "assistant", assistant)


class TestNaming:
    def test_a_chat_is_named_after_its_first_exchange(self, store, named):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"

    def test_a_missed_first_turn_is_still_named_later(self, store, named):
        """The whole bug. Three exchanges deep and still unnamed, it must name
        itself on the next turn rather than being locked out forever."""
        tid = chat_store.create_thread("general")["id"]
        for _ in range(3):
            _exchange(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"

    def test_the_title_describes_the_first_exchange_not_the_latest(self, store, named):
        """A chat's name is what it is ABOUT. Naming from the newest turn would
        make the title wander every time the conversation moved on."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "How do subagents work here?", "They run in an isolated context.")
        _exchange(tid, "Now fix the palette generator", "Regenerated the tokens.")
        chat_naming.generate_title(tid)
        prompt = named["prompt"]
        assert "How do subagents work here?" in prompt
        assert "palette generator" not in prompt

    def test_a_named_chat_is_left_alone(self, store, named):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        chat_store.rename_thread(tid, "Operator's Own Name")
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Operator's Own Name"
        assert "prompt" not in named   # not even a model call

    def test_a_chat_with_no_reply_yet_is_not_named(self, store, named):
        """One user message is not enough to categorize a chat by."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hey")
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"

    def test_naming_costs_one_cheap_tools_off_call(self, store, named):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        chat_naming.generate_title(tid)
        assert named["kwargs"]["tools_disabled"] is True
        assert named["kwargs"]["max_turns"] == 1

    def test_a_failed_call_leaves_the_chat_nameable(self, store, monkeypatch):
        """A failure must not poison the title; the next turn tries again."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limited")))
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"


class TestRejectsNonTitleOutput:
    """Asked for "output ONLY the title", the model occasionally answers the
    conversation instead. Observed on a live thread: the permanent title ended
    up "I don't have tools available in" — the start of a refusal, saved
    verbatim because nothing checked it before persisting. This must be
    treated the same as a failed call: skip, and let the next reply retry."""

    def test_a_refusal_is_not_saved_as_a_title(self, store, monkeypatch):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        monkeypatch.setattr(
            chat_naming.llm_router, "call_cloud",
            lambda *a, **k: {"text": "I don't have tools available in this context"},
        )
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"

    def test_a_later_retry_can_still_succeed(self, store, monkeypatch):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        monkeypatch.setattr(
            chat_naming.llm_router, "call_cloud",
            lambda *a, **k: {"text": "I'm sorry, I can't help with that"},
        )
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"
        monkeypatch.setattr(
            chat_naming.llm_router, "call_cloud",
            lambda *a, **k: {"text": "Subagent Delegation In Chat"},
        )
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"


class TestSeedTitle:
    def test_the_seed_is_the_first_message_and_stays_that_way(self, store):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "How do subagents work here?")
        chat_store.append_message(tid, "assistant", "...")
        chat_store.append_message(tid, "user", "Restart again")
        assert chat_store.get_thread(tid)["_seed_title"] == "How do subagents work here?"

    def test_the_list_shows_the_seed_only_until_a_name_lands(self, store, named):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "How do subagents work here?")
        assert chat_store.list_threads()[0]["title"] == "How do subagents work here?"
        chat_naming.generate_title(tid)
        assert chat_store.list_threads()[0]["title"] == "Subagent Delegation In Chat"


class TestTheNamerDoesNotRaceTheConversation:
    """Naming used to run WHOLE inside `asyncio.to_thread`: it read the thread,
    made a ~2s model call, and wrote the thread back — from off the event loop,
    with the read and the write straddling the call. Anything the operator sent
    in that window was in the gap. The model call still runs in a worker thread,
    because it blocks; the write comes back to the loop.
    """

    def test_the_model_call_runs_off_the_loop_and_the_write_runs_on_it(
            self, store, monkeypatch):
        import asyncio
        import threading

        loop_thread = threading.current_thread()
        where = {}

        def fake_call(prompt, **kw):
            where["model_call"] = threading.current_thread()
            return {"text": "Subagent Delegation In Chat"}

        real_rename = chat_store.rename_thread

        def watched_rename(*a, **kw):
            where["write"] = threading.current_thread()
            return real_rename(*a, **kw)

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", fake_call)
        monkeypatch.setattr(chat_naming.chat_store, "rename_thread", watched_rename)

        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        asyncio.run(chat_naming.name_thread(tid))

        assert where["model_call"] is not loop_thread
        assert where["write"] is loop_thread
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"

    def test_a_message_sent_during_the_naming_call_survives(self, store, monkeypatch):
        """The race, run for real: the operator types while the namer is in its
        model call. Both the name and the message have to be there afterwards."""
        import asyncio

        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)

        def slow_call(prompt, **kw):
            import time
            time.sleep(0.05)          # the window the message lands in
            return {"text": "Subagent Delegation In Chat"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", slow_call)

        async def main():
            async def types():
                await asyncio.sleep(0.01)
                chat_store.append_message(tid, "user", "WAIT one more thing")
            await asyncio.gather(chat_naming.name_thread(tid), types())

        asyncio.run(main())
        t = chat_store.get_thread(tid)
        assert t["title"] == "Subagent Delegation In Chat"
        assert [m["content"] for m in t["messages"]][-1] == "WAIT one more thing"
        assert len(t["messages"]) == 3

    def test_a_name_typed_during_the_call_is_not_overwritten(self, store, monkeypatch):
        import asyncio

        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)

        def slow_call(prompt, **kw):
            import time
            time.sleep(0.05)
            return {"text": "Subagent Delegation In Chat"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", slow_call)

        async def main():
            async def renames():
                await asyncio.sleep(0.01)
                chat_store.rename_thread(tid, "What I Called It")
            await asyncio.gather(chat_naming.name_thread(tid), renames())

        asyncio.run(main())
        assert chat_store.get_thread(tid)["title"] == "What I Called It"
