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

    def test_the_title_describes_the_OPENING_exchange(self, store, named):
        """Naming reads the chat FROM THE FRONT, so the title says what the chat
        is about rather than what it drifted onto.

        This is the operator's "the name changed to something irrelevant or
        tangential" bug. It used to read the LATEST exchange: a chat that opened
        on subagents and later touched the palette generator got permanently
        named after the palette generator."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "How do subagents work here?", "They run in an isolated context.")
        _exchange(tid, "Now fix the palette generator", "Regenerated the tokens.")
        chat_naming.generate_title(tid)
        prompt = named["prompt"]
        assert "How do subagents work here?" in prompt
        assert "palette generator" not in prompt

    def test_a_late_tangent_can_never_become_the_title(self, store, monkeypatch):
        """Even after many turns, naming is anchored to the start. A chat that
        goes twenty turns deep and ends on a side question is still named from
        its opening."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "Design the ticket dependency model", "Here is the edge model.")
        for _ in range(20):
            _exchange(tid, "quick aside about the bird mic buzz", "It is a ground loop.")
        seen = {}

        def capture(prompt, **kw):
            seen["prompt"] = prompt
            return {"text": "Ticket Dependency Model"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", capture)
        chat_naming.generate_title(tid)
        assert "ticket dependency model" in seen["prompt"].lower()
        assert "bird mic buzz" not in seen["prompt"]
        assert chat_store.get_thread(tid)["title"] == "Ticket Dependency Model"

    def test_a_named_chat_is_left_alone(self, store, named):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        chat_store.rename_thread(tid, "Operator's Own Name")
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Operator's Own Name"
        assert "prompt" not in named   # not even a model call

    def test_a_chat_is_named_from_the_opening_message_alone(self, store, named):
        """WITHOUT WAITING FOR THE REPLY, which is the whole timing fix.

        Requiring the reply meant the earliest a name could exist was the moment
        the first turn committed, so the title inherited that turn's length:
        measured across the real threads, a median of 9.4 minutes from the
        opening message, ranging 1.9 to 38.8. That spread is what the operator
        reported as the naming being inconsistent and sometimes not running.

        The subject of a conversation is almost always already in the message
        that opened it, so there is nothing to wait for."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "How do subagents work here?")
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"
        # And the reply is not fabricated into the prompt when there is not one.
        assert "Assistant:" not in named["prompt"]

    def test_a_topicless_opener_still_waits(self, store, monkeypatch):
        """Naming early must not mean naming badly. A greeting has no subject,
        so the model answers NONE and the chat keeps waiting, exactly as it did
        when a full exchange was required."""
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda prompt, **kw: {"text": "NONE"})
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hey")
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"

    def test_the_reply_is_used_when_there_is_one(self, store, named):
        """Not required, but not ignored either: a reply that has landed is still
        context for what the chat turned out to be about."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        chat_naming.generate_title(tid)
        assert "They run in an isolated context." in named["prompt"]

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
        """A rejection burns that exchange, so the retry reads the NEXT one."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid)
        monkeypatch.setattr(
            chat_naming.llm_router, "call_cloud",
            lambda *a, **k: {"text": "I'm sorry, I can't help with that"},
        )
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"
        _exchange(tid, "Split /clear from /new", "Done.")
        monkeypatch.setattr(
            chat_naming.llm_router, "call_cloud",
            lambda *a, **k: {"text": "Subagent Delegation In Chat"},
        )
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"


class TestNoTopicAndTheAttemptCap:
    """When the exchange has no discernible subject the namer answers NONE, and
    that is treated like a rejection: skip now, try again on the next reply. A
    chat that is genuinely unnameable stops asking after `_MAX_NAME_ATTEMPTS`
    content rejections, so it never pays a naming call every turn forever — it
    keeps its seed / "New chat" fallback. This is the "Compact -> Ready For Next
    Task" bug: a thin first exchange used to be named permanently anyway."""

    def test_a_none_response_is_not_saved(self, store, monkeypatch):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "Compact", "Ready. What's next?")
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: {"text": "NONE"})
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"

    def test_a_substantive_later_turn_names_after_a_none(self, store, monkeypatch):
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "Compact", "Ready.")
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: {"text": "NONE"})
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"
        _exchange(tid, "Split /clear from /new in the composer", "Done.")
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: {"text": "Clear Command Split"})
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Clear Command Split"

    def test_it_gives_up_after_the_cap_and_makes_no_more_calls(self, store, monkeypatch):
        """One call per early exchange, then it stops asking forever."""
        tid = chat_store.create_thread("general")["id"]
        for _ in range(chat_naming._MAX_NAME_ATTEMPTS + 4):
            _exchange(tid, "ok", "Ready.")
        calls = {"n": 0}

        def none_call(*a, **k):
            calls["n"] += 1
            return {"text": "NONE"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", none_call)
        for _ in range(chat_naming._MAX_NAME_ATTEMPTS + 4):
            chat_naming.generate_title(tid)
        assert calls["n"] == chat_naming._MAX_NAME_ATTEMPTS
        assert chat_store.get_thread(tid)["title"] == "New chat"

    def test_waiting_for_the_next_exchange_does_not_burn_the_budget(self, store, monkeypatch):
        """A young chat whose next exchange has not happened yet must not spend
        an attempt on it. Otherwise a chat that opens with one thin turn would
        exhaust the cap while sitting idle and could never be named."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "ok", "Ready.")
        calls = {"n": 0}

        def none_call(*a, **k):
            calls["n"] += 1
            return {"text": "NONE"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", none_call)
        for _ in range(5):
            chat_naming.generate_title(tid)
        assert calls["n"] == 1   # only exchange 0 existed to try
        _exchange(tid, "Design the latitude model", "Here it is.")
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: {"text": "Latitude Model Design"})
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Latitude Model Design"

    def test_a_pending_exchanges_none_is_retried_once_the_reply_lands(self, store, monkeypatch):
        """The real bug this file was extended for: naming runs at send time, so
        a single-exchange chat can get judged on the user's message ALONE before
        any reply exists. If that pending look comes back NONE, the exchange
        pointer must not move past it — the same exchange is asked again once
        the reply lands, not skipped in favor of an exchange 1 that will never
        exist for a one-and-done chat.

        Before this fix: the send-time NONE consumed exchange 0's only shot,
        the pointer moved to index 1, and a real, clear request stayed on its
        raw first-message seed forever even though its reply was informative."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "Add a keyboard shortcut to jump to search")
        calls = {"n": 0}

        def none_call(*a, **k):
            calls["n"] += 1
            return {"text": "NONE"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", none_call)
        chat_naming.generate_title(tid)   # the send-time look: user message only
        assert calls["n"] == 1
        assert chat_store.get_thread(tid)["title"] == "New chat"
        # The pending rejection must not have spent the budget or moved the
        # pointer forward.
        assert chat_store.get_thread(tid).get("_name_attempts", 0) == 0

        chat_store.append_message(tid, "assistant", "Bound Ctrl+K to focus the search field.")

        def title_call(*a, **k):
            calls["n"] += 1
            return {"text": "Search Shortcut Binding"}

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", title_call)
        chat_naming.generate_title(tid)   # the commit-time look: same exchange, now complete
        assert calls["n"] == 2
        assert chat_store.get_thread(tid)["title"] == "Search Shortcut Binding"

    def test_transient_failures_do_not_count_toward_the_cap(self, store, monkeypatch):
        """A rate limit is not a verdict that a chat is unnameable. Five failed
        calls, then a good one, still names it — the original retry property."""
        tid = chat_store.create_thread("general")["id"]
        _exchange(tid, "How do subagents work here?", "They run isolated.")
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limited")))
        for _ in range(5):
            chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "New chat"
        monkeypatch.setattr(chat_naming.llm_router, "call_cloud",
                            lambda *a, **k: {"text": "Subagent Delegation In Chat"})
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


class TestTheTriggerIsWired:
    """WHERE NAMING IS CALLED FROM, which is the part that kept regressing.

    Every test above drives the namer directly, so all of them passed through
    seven rounds of fixes in which the actual defects were in the wiring: a call
    site deleted during an unrelated refactor, a second one routed through a
    function that no longer existed, and a guard that made the two ways a turn
    can fail behave oppositely. None of that is visible from the namer's side.
    """

    def test_sending_a_message_schedules_naming(self, store, monkeypatch):
        """The primary trigger. If this stops firing the chat is named only when
        the turn commits, which is the slow, variable behaviour that was
        reported."""
        import asyncio

        called = []
        monkeypatch.setattr(chat_naming, "schedule", called.append)
        tid = chat_store.create_thread("general")["id"]

        async def main():
            chat_store.append_message(tid, "user", "How do subagents work here?")

        asyncio.run(main())
        assert called == [tid]

    def test_the_assistants_own_reply_does_not_schedule_naming(self, store, monkeypatch):
        """Only the operator's message brings a new subject. Scheduling on the
        reply too would double every chat's naming cost for nothing."""
        import asyncio

        called = []
        monkeypatch.setattr(chat_naming, "schedule", called.append)
        tid = chat_store.create_thread("general")["id"]

        async def main():
            chat_store.append_message(tid, "assistant", "They run in an isolated context.")

        asyncio.run(main())
        assert called == []

    def test_appending_off_the_loop_never_blocks_on_a_model_call(self, store, monkeypatch):
        """`schedule` runs the model call INLINE when there is no running loop,
        which is right for a script that asks for it and wrong for a store write
        that merely happens to be off the loop. Appending a message must never
        become a blocking model call because of where it was called from."""
        def explode(*a, **kw):
            raise AssertionError("a store write must not make a model call")

        monkeypatch.setattr(chat_naming.llm_router, "call_cloud", explode)
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "How do subagents work here?")
        assert chat_store.get_thread(tid)["title"] == "New chat"

    def test_a_failed_turn_still_gets_its_chat_named(self, store, named):
        """There used to be an `if not is_error` guard at the commit call site,
        so a chat whose turns errored was never named. It also disagreed with
        itself: a turn the operator STOPPED commits with is_error False and DID
        name. The operator's message is present and perfectly nameable either
        way, and a chat whose first turn failed is exactly the one being
        reopened."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "How do subagents work here?")
        chat_store.append_message(tid, "assistant", "Rate limited.", meta={"error": True})
        chat_naming.generate_title(tid)
        assert chat_store.get_thread(tid)["title"] == "Subagent Delegation In Chat"


class TestOneNamingTaskPerThread:
    """A burst of messages used to schedule a naming task per message: each read
    the same attempt counter, each wrote N+1 (undercounting the budget), and
    each spent a model call on a question the first answer had settled."""

    def test_a_burst_schedules_one_task(self, store, monkeypatch):
        import asyncio
        tid = chat_store.create_thread("general")["id"]
        started = []

        async def slow_name(thread_id):
            started.append(thread_id)
            await asyncio.sleep(0.05)

        monkeypatch.setattr(chat_naming, "name_thread", slow_name)

        async def main():
            for _ in range(3):
                chat_naming.schedule(tid)
            # One task for the burst, plus ONE re-run for what landed during
            # its flight: never a task per message.
            await asyncio.sleep(0.2)
            # After everything settles, a NEW schedule may run again.
            chat_naming.schedule(tid)
            await asyncio.sleep(0.1)

        asyncio.run(main())
        assert started == [tid, tid, tid]

    def test_a_schedule_refused_in_flight_rearms_one_rerun(self, store, monkeypatch):
        """The in-flight task read the thread BEFORE the refused schedule's
        message landed, so the refusal is new information, not a duplicate.
        One re-run on completion; a named thread makes it a cheap no-op."""
        import asyncio
        tid = chat_store.create_thread("general")["id"]
        started = []

        async def slow_name(thread_id):
            started.append(thread_id)
            await asyncio.sleep(0.05)

        monkeypatch.setattr(chat_naming, "name_thread", slow_name)

        async def main():
            chat_naming.schedule(tid)
            await asyncio.sleep(0.01)
            chat_naming.schedule(tid)   # refused: in flight. Re-arms.
            await asyncio.sleep(0.2)

        asyncio.run(main())
        assert started == [tid, tid]


class TestSeedTitleLength:
    """The stand-in title is a preview of the first message, so it may not be
    shorter than the row that shows it.

    It was capped at 60 characters, cut mid-word, with no ellipsis, while every
    surface that renders a title already clips its own overflow. On a 1440px
    chat list that left roughly six hundred pixels of empty row to the right of
    "...Then write one sentence abo". Operator: "the chat text/message preview
    itself in the chat card in chats is still truncated unnecessarily. can use
    its full row if its long enough."
    """

    def test_a_sentence_a_row_can_hold_is_not_cut(self):
        from services.chat_store import seed_title_from
        line = ("Run 'sleep 25; echo hi' in bash. Then write one sentence about "
                "it and stop, nothing else at all")
        assert seed_title_from(line) == line

    def test_only_the_first_line_stands_in_for_a_name(self):
        from services.chat_store import seed_title_from
        assert seed_title_from("the ask\n\nand a page of detail after it") == "the ask"

    def test_a_very_long_line_is_cut_at_a_word_boundary(self):
        from services.chat_store import seed_title_from, SEED_TITLE_MAX
        out = seed_title_from("word " * 200)
        assert len(out) <= SEED_TITLE_MAX
        assert out.endswith("word")

    def test_one_unbroken_word_still_gets_cut(self):
        from services.chat_store import seed_title_from, SEED_TITLE_MAX
        assert seed_title_from("x" * 400) == "x" * SEED_TITLE_MAX

    def test_an_empty_message_names_the_chat_rather_than_nothing(self):
        from services.chat_store import seed_title_from
        assert seed_title_from("   \n  ") == "New chat"
