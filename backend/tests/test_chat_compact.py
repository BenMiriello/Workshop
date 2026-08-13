"""Compaction actually compacts, and a cut actually cuts.

THE BUG THESE PIN, measured 2026-08-12: `get_thread` hands over every message
the thread has ever held plus a `cleared_through` COUNT, and trimming is the
caller's job. The caller that builds what the model reads never did it. So
`/clear` appended a boundary, dropped the CLI session, drew a divider, and the
next turn re-sent the whole history anyway. The context meter DID subtract the
cut, so the meter fell while nothing else did.

`/compact` was the same, one step worse: it detached the session and folded
nothing at all, because folding had been removed after its summarizer spent
months failing with E2BIG (the prompt went in argv, which Linux caps at 128KB
per argument, so it died on exactly the threads long enough to need it) and the
failure was swallowed as best-effort while the chat said "Compacted."

So: the cut is applied where the prompt is built, and /compact folds for real.
Neither touches the transcript — the operator keeps every message either way.
"""
import asyncio

import pytest

from services import chat_context, chat_log, chat_store, ownership


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def chatty(owned):
    """A thread with a real amount of conversation in it."""
    tid = chat_store.create_thread("general")["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"OLD-{i} " + ("filler " * 50))
        chat_store.append_message(tid, "assistant", f"reply-{i} " + ("filler " * 50))
    chat_store.append_message(tid, "user", "RECENT the latest thing")
    return tid


def _prompt(tid: str) -> str:
    from routes import chat as chat_routes
    t = chat_store.get_thread(tid)
    provider = chat_context.get_provider(t.get("context_key"))
    prompt, _ = asyncio.run(chat_routes._assemble_full_prompt(t, provider, "next?"))
    return prompt


def _conversation(prompt: str) -> str:
    """Just the verbatim-history block, which is what a cut is supposed to move."""
    marker = "=== CONVERSATION SO FAR ==="
    return prompt.split(marker)[1] if marker in prompt else ""


class TestAClearActuallyClears:
    def test_the_prompt_loses_the_messages_behind_the_cut(self, chatty):
        before = _conversation(_prompt(chatty))
        assert "OLD-0" in before
        chat_store.clear_thread_context(chatty)
        after = _conversation(_prompt(chatty))
        # Nothing before the cut is sent. This was the whole failure: the count
        # was recorded and nothing acted on it.
        assert after == ""

    def test_the_transcript_keeps_every_message(self, chatty):
        chat_store.clear_thread_context(chatty)
        msgs = [e for e in chat_log.read_events(chatty) if e.get("kind") == "message"]
        assert len(msgs) == 21

    def test_a_message_sent_after_the_clear_is_still_sent(self, chatty):
        chat_store.clear_thread_context(chatty)
        chat_store.append_message(chatty, "user", "AFTER-THE-CLEAR")
        assert "AFTER-THE-CLEAR" in _conversation(_prompt(chatty))


class TestCompactionFolds:
    """These call the fold with a stubbed summarizer: what is under test is the
    bookkeeping around the model call, not the model."""

    @pytest.fixture()
    def stub_summary(self, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync",
                            lambda prev, msgs: f"SUMMARY of {len(msgs)} messages")
        return chat_routes

    def test_the_prompt_shrinks_and_keeps_the_recent_tail(self, chatty, stub_summary):
        before = _conversation(_prompt(chatty))
        folded, err = asyncio.run(
            stub_summary._fold_thread(chatty, chat_store.get_thread(chatty)))
        assert err == ""
        assert folded == 15          # 21 messages, 6 kept verbatim
        after = _conversation(_prompt(chatty))
        assert len(after) < len(before) / 2
        # The tail is what the conversation is ABOUT right now, and summarizing
        # it is what makes a compacted chat unable to continue.
        assert "RECENT the latest thing" in after
        assert "OLD-0" not in after

    def test_the_summary_replaces_them_in_the_prompt(self, chatty, stub_summary):
        asyncio.run(stub_summary._fold_thread(chatty, chat_store.get_thread(chatty)))
        prompt = _prompt(chatty)
        assert "EARLIER IN THIS CONVERSATION (summary)" in prompt
        assert "SUMMARY of 15 messages" in prompt

    def test_the_transcript_is_untouched(self, chatty, stub_summary):
        asyncio.run(stub_summary._fold_thread(chatty, chat_store.get_thread(chatty)))
        msgs = [e for e in chat_log.read_events(chatty) if e.get("kind") == "message"]
        assert len(msgs) == 21
        assert any("OLD-0" in (m.get("content") or "") for m in msgs)

    def test_it_leaves_a_boundary_the_transcript_can_read(self, chatty, stub_summary):
        asyncio.run(stub_summary._fold_thread(chatty, chat_store.get_thread(chatty)))
        cuts = [e for e in chat_log.read_events(chatty) if e.get("kind") == "boundary"]
        assert [c["mode"] for c in cuts] == ["compact"]
        # WHERE it cut, on the event, so the divider is read rather than guessed
        # and says the same thing on every device.
        assert cuts[0]["at_seq"] > 0

    def test_folding_twice_folds_what_arrived_since(self, chatty, stub_summary):
        asyncio.run(stub_summary._fold_thread(chatty, chat_store.get_thread(chatty)))
        for i in range(8):
            chat_store.append_message(chatty, "user", f"NEW-{i}")
        folded, err = asyncio.run(
            stub_summary._fold_thread(chatty, chat_store.get_thread(chatty)))
        assert err == ""
        assert folded == 8   # 6 old tail + 8 new = 14, keep 6, fold 8
        assert "NEW-7" in _conversation(_prompt(chatty))

    def test_a_short_chat_folds_nothing(self, owned, stub_summary):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hello")
        folded, err = asyncio.run(stub_summary._fold_thread(tid, chat_store.get_thread(tid)))
        assert (folded, err) == (0, "")
        assert not chat_store.get_thread(tid).get("summary")


class TestAFailedFoldChangesNothingAndSaysSo:
    def test_a_summarizer_that_raises_is_reported(self, chatty, monkeypatch):
        from routes import chat as chat_routes

        def boom(prev, msgs):
            raise RuntimeError("Argument list too long")

        monkeypatch.setattr(chat_routes, "_summarize_sync", boom)
        before = _conversation(_prompt(chatty))
        folded, err = asyncio.run(
            chat_routes._fold_thread(chatty, chat_store.get_thread(chatty)))
        assert folded == 0
        assert "Argument list too long" in err
        # NOTHING MOVED. This is the case that answered 200 for months while the
        # chat said the earlier messages were now a summary.
        assert _conversation(_prompt(chatty)) == before
        assert not chat_store.get_thread(chatty).get("summary")

    def test_an_empty_summary_is_a_failure_not_a_fold(self, chatty, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync", lambda prev, msgs: "   ")
        folded, err = asyncio.run(
            chat_routes._fold_thread(chatty, chat_store.get_thread(chatty)))
        assert folded == 0
        assert err
        assert not chat_store.get_thread(chatty).get("summary")


class TestLegacyBoundariesStayInert:
    def test_a_compact_boundary_with_no_summary_hides_nothing(self, chatty):
        """Folding was removed and restored on the same day. A thread carrying a
        boundary from the broken era has nothing behind it, and honouring it
        would hide real messages behind a summary that was never written."""
        chat_log.append_sync(chatty, {"kind": "boundary", "mode": "compact", "at_seq": 10})
        assert "OLD-0" in _conversation(_prompt(chatty))
        assert not chat_store.get_thread(chatty).get("cleared_through")


class TestTheCutOnTheWire:
    """`cut_state` is what the transcript's divider is drawn from, and it is read
    on every poll from every open tab. It must agree with what the PROMPT does
    and cost a meta read to answer."""

    def test_it_agrees_with_the_prompt_after_a_clear(self, chatty):
        chat_store.clear_thread_context(chatty)
        assert chat_store.cut_state(chatty) == {
            "cleared_through": chat_store.get_thread(chatty)["cleared_through"],
            "cut_mode": "clear",
        }

    def test_it_agrees_with_the_prompt_after_a_fold(self, chatty, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync", lambda prev, msgs: "S")
        asyncio.run(chat_routes._fold_thread(chatty, chat_store.get_thread(chatty)))
        st = chat_store.cut_state(chatty)
        assert st["cut_mode"] == "compact"
        assert st["cleared_through"] == chat_store.get_thread(chatty)["cleared_through"]

    def test_no_cut_reads_as_no_cut(self, chatty):
        assert chat_store.cut_state(chatty) == {"cleared_through": 0, "cut_mode": ""}

    def test_a_cut_made_before_the_count_was_cached_is_backfilled(self, chatty):
        """Every thread cleared before 2026-08-12 has `boundary_seq` and no
        cached count. Reporting no cut for those would take the divider off them
        while the prompt kept honouring it."""
        chat_store.clear_thread_context(chatty)
        expected = chat_store.get_thread(chatty)["cleared_through"]
        chat_log.patch_meta_sync(chatty, cut_through=None, cut_mode=None)
        assert chat_store.cut_state(chatty) == {
            "cleared_through": expected, "cut_mode": "clear"}
        # ...and it is written down, so the next read is a meta read.
        assert chat_log.read_meta(chatty).get("cut_through") == expected

    def test_a_stale_compact_boundary_reads_as_no_cut(self, chatty):
        chat_log.append_sync(chatty, {"kind": "boundary", "mode": "compact", "at_seq": 10})
        chat_log.patch_meta_sync(chatty, boundary_seq=10, cut_through=5, cut_mode="compact")
        # No summary behind it, so neither the prompt nor the divider honours it.
        assert chat_store.cut_state(chatty) == {"cleared_through": 0, "cut_mode": ""}


    def test_it_matches_the_prompt_for_a_boundary_written_any_other_way(self, chatty):
        """THERE MUST NOT BE TWO ANSWERS.

        `_document` derives the cut from the log for the prompt; `cut_state`
        reports it for the divider. A boundary appended by a path that did not
        also update meta would make one honour it and the other not, which is a
        divider missing from a cut that is really being applied."""
        chat_store.clear_thread_context(chatty)
        expected = chat_store.get_thread(chatty)["cleared_through"]
        # Drop the cache the way a thread from before it exists would be, and
        # let the read path resolve it again.
        chat_log.patch_meta_sync(chatty, cut_through=None, cut_mode=None)
        assert chat_store.cut_state(chatty)["cleared_through"] == expected
        # ...and the startup backfill reaches the same answer without a poll.
        chat_log.patch_meta_sync(chatty, cut_through=None, cut_mode=None)
        assert chat_store.backfill_cuts() >= 1
        assert chat_log.read_meta(chatty)["cut_through"] == expected


class TestTheStopRuleSurvivesACompaction:
    """A `/compact` drops the CLI session, so the turn after it re-assembles the
    whole prompt instead of resuming. That path did not carry the turn contract.

    The contract (`chat_context.turn_contract`) is the block that tells an agent
    a text-only message ENDS its turn. `_resumed_prompt` has opened with it every
    turn since a7faffa; `_assemble_full_prompt` never added it, so the rule was
    present on every turn except the first one after a cut, which is where a long
    piece of work is most likely to be mid-flight.

    Measured on thread 65341b40 (2026-08-12): `/compact` at 23:05:58Z, then a
    322,142-character prompt at 23:10:39Z with no contract in it. That turn ended
    "Building it now." with `stop_reason: end_turn` and the work never happened.
    The resumed prompts either side of it carry the contract and did not stop.
    """

    def test_the_full_assembly_carries_it(self, chatty):
        assert "=== HOW THIS TURN ENDS" in _prompt(chatty)

    def test_it_is_still_there_on_the_turn_after_a_compact(self, chatty):
        chat_store.clear_thread_context(chatty)
        assert "=== HOW THIS TURN ENDS" in _prompt(chatty)

    def test_it_sits_near_the_operators_message_not_behind_the_transcript(self, chatty):
        """Position is the point on this path. The resumed prompt is small and
        leads with the rule; this one can run to hundreds of thousands of
        characters, and a rule stated before all of that is a rule stated
        nowhere."""
        p = _prompt(chatty)
        assert p.index("=== HOW THIS TURN ENDS") > p.index("=== CONVERSATION SO FAR ===")

    def test_the_meter_and_the_turn_measure_the_same_prompt(self, chatty):
        """`_context_estimate` has prepended the contract to its own copy of the
        preamble since a7faffa, with a comment saying the first turn must not
        differ from a resumed one. The meter measured a prompt containing the
        rule while the turn ran without it; the two have to agree."""
        from routes import chat as chat_routes
        report = asyncio.run(chat_routes.thread_context(chatty))
        assert report["source"] == "estimated" and report["used_tokens"] > 0
        # Same content, so the same size: the estimate is not inflated by a block
        # the real prompt lacks.
        assert abs(report["used_tokens"] - round(len(_prompt(chatty)) / 4)) < 200
