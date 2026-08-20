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
    """A thread with a real amount of conversation in it.

    Sized in TOKENS, not records, to exercise the token-budget fold
    (`_COMPACT_KEEP_TAIL_TOKENS`, ~4000): each OLD/reply message here is
    ``len("OLD-{i} " + "filler " * 400) / 4`` ~= 702 estimated tokens, so the
    21-message tail totals well past the budget and the walk from the newest
    message settles on keeping the last 6 verbatim (~5 * 702 + 6 = 3516,
    within budget; a 7th would push it over) and folding the other 15 -- the
    same split the old fixed "last 6 records" constant produced, but now
    because of size, not count.

    The messages alternate user/assistant (OLD-i, reply-i), so that raw
    6-message cut lands on an ASSISTANT message (reply-7): the tail-alignment
    step (see `_fold_summary`) pulls one more message (OLD-7, a user turn)
    into the kept tail so the verbatim portion opens on a prompt rather than
    a stranded reply, settling the real split at keep 7 / fold 14.
    """
    tid = chat_store.create_thread("general")["id"]
    for i in range(10):
        chat_store.append_message(tid, "user", f"OLD-{i} " + ("filler " * 400))
        chat_store.append_message(tid, "assistant", f"reply-{i} " + ("filler " * 400))
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


async def _fold_now(tid: str):
    """Compute + land in one step, as the route does on a quiet thread. The
    split (compute while the thread lives, land when quiet) is the 2026-08-14
    serialization change; these tests exercise the fold's semantics, not the
    scheduling, so they land immediately."""
    from routes import chat as chat_routes
    fold_n, summary, through, err = await chat_routes._fold_summary(
        tid, chat_store.get_thread(tid))
    if not err and fold_n > 0:
        chat_routes._land_fold(tid, summary, through)
    return fold_n, err


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
            _fold_now(chatty))
        assert err == ""
        # 21 messages; the token budget alone would keep the last 6, but that
        # lands on an assistant reply, so alignment pulls in one more (the
        # user turn ahead of it) and the real split is keep 7 / fold 14.
        assert folded == 14
        after = _conversation(_prompt(chatty))
        assert len(after) < len(before) / 2
        # The tail is what the conversation is ABOUT right now, and summarizing
        # it is what makes a compacted chat unable to continue.
        assert "RECENT the latest thing" in after
        assert "OLD-0" not in after

    def test_the_summary_replaces_them_in_the_prompt(self, chatty, stub_summary):
        asyncio.run(_fold_now(chatty))
        prompt = _prompt(chatty)
        assert "EARLIER IN THIS CONVERSATION (summary)" in prompt
        assert "SUMMARY of 14 messages" in prompt

    def test_the_transcript_is_untouched(self, chatty, stub_summary):
        asyncio.run(_fold_now(chatty))
        msgs = [e for e in chat_log.read_events(chatty) if e.get("kind") == "message"]
        assert len(msgs) == 21
        assert any("OLD-0" in (m.get("content") or "") for m in msgs)

    def test_it_leaves_a_boundary_the_transcript_can_read(self, chatty, stub_summary):
        asyncio.run(_fold_now(chatty))
        cuts = [e for e in chat_log.read_events(chatty) if e.get("kind") == "boundary"]
        assert [c["mode"] for c in cuts] == ["compact"]
        # WHERE it cut, on the event, so the divider is read rather than guessed
        # and says the same thing on every device.
        assert cuts[0]["at_seq"] > 0

    def test_folding_twice_folds_what_arrived_since(self, chatty, stub_summary):
        asyncio.run(_fold_now(chatty))
        # Sized like `chatty` itself: big enough that the second fold's tail
        # (the 7 messages kept by the first fold, plus these 8) clears the
        # token budget again, so this exercises the count-from-the-front
        # invariant on a real second fold rather than a no-op one.
        for i in range(8):
            chat_store.append_message(chatty, "user", f"NEW-{i} " + ("filler " * 350))
        folded, err = asyncio.run(
            _fold_now(chatty))
        assert err == ""
        assert folded == 9   # 7 old tail + 8 new = 15, keep 6, fold 9
        assert "NEW-7" in _conversation(_prompt(chatty))

    def test_a_short_chat_folds_nothing(self, owned, stub_summary):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hello")
        folded, err = asyncio.run(_fold_now(tid))
        assert (folded, err) == (0, "")
        assert not chat_store.get_thread(tid).get("summary")


class TestTheTokenBudgetReplacesTheRecordCount:
    """The bug the token-aware walk fixes: a record COUNT says nothing about
    size. A thread with few records but a huge message used to fold nothing
    (the old `_COMPACT_KEEP_TAIL = 6` never even looked at content length),
    which is exactly backwards -- that thread is the one that most needs it."""

    @pytest.fixture()
    def stub_summary(self, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync",
                            lambda prev, msgs: f"SUMMARY of {len(msgs)} messages")
        return chat_routes

    def test_few_records_but_one_huge_message_still_folds(self, owned, stub_summary):
        """4 records total -- well under the old fixed keep of 6, so the old
        heuristic would have folded nothing here at all -- but two of them are
        each bigger than the whole keep budget on their own."""
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "HUGE-0 " + ("x " * 10000))   # ~5000 tok
        chat_store.append_message(tid, "assistant", "HUGE-1 " + ("x " * 10000))  # ~5000 tok
        chat_store.append_message(tid, "user", "small-2")
        chat_store.append_message(tid, "assistant", "small-3 RECENT")
        folded, err = asyncio.run(_fold_now(tid))
        assert err == ""
        assert folded == 2           # the two huge messages, not the two small ones
        after = _conversation(_prompt(tid))
        assert "HUGE-0" not in after and "HUGE-1" not in after
        assert "small-2" in after and "RECENT" in after

    def test_the_last_two_messages_survive_even_over_budget(self, owned, stub_summary):
        """The guardrail: the newest message here is bigger than the entire
        keep budget by itself, so the walk blows the budget on the very first
        (mandatory) message it looks at. It must still keep the last 2
        verbatim rather than folding everything, or the exchange in progress
        gets summarized out from under itself."""
        tid = chat_store.create_thread("general")["id"]
        for i in range(6):
            chat_store.append_message(tid, "user", f"OLD-{i} " + ("filler " * 400))
        chat_store.append_message(tid, "user", "SECOND-TO-LAST " + ("z " * 10000))
        chat_store.append_message(tid, "assistant", "RECENT-LAST")
        folded, err = asyncio.run(_fold_now(tid))
        assert err == ""
        assert folded == 6           # only the 6 old records; the guardrail held
        after = _conversation(_prompt(tid))
        assert "SECOND-TO-LAST" in after
        assert "RECENT-LAST" in after
        assert "OLD-0" not in after

    def test_folded_through_is_still_a_count_from_the_front(self, chatty, stub_summary):
        """The cut stays `cleared_through + fold_n` regardless of the new
        retention math -- the append-at-back invariant the rest of the fold
        (and `_land_fold`'s landing) depends on is untouched by this change."""
        from routes import chat as chat_routes
        # A prior /clear moves `cleared_through` off zero, so this also checks
        # the walk is relative to the tail, not the whole message list.
        chat_store.clear_thread_context(chatty)
        ct_before = chat_store.get_thread(chatty)["cleared_through"]
        assert ct_before > 0
        for i in range(12):
            chat_store.append_message(chatty, "user", f"AFTER-CLEAR-{i} " + ("filler " * 400))
        fold_n, summary, folded_through, err = asyncio.run(
            chat_routes._fold_summary(chatty, chat_store.get_thread(chatty)))
        assert err == ""
        assert fold_n > 0
        assert folded_through == ct_before + fold_n


class TestTheFoldAlignsToAUserTurn:
    """`_fold_summary`'s token-budget walk stops purely on cost, and can land
    the cut between an exchange's two halves, keeping an assistant reply
    verbatim while folding the user prompt it answered. The alignment step
    keeps the verbatim tail starting at a user turn so a reply is never
    stranded from its prompt -- see `_fold_summary` and the `chatty` fixture's
    own docstring for the exact split this produces on it."""

    @pytest.fixture()
    def stub_summary(self, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync",
                            lambda prev, msgs: f"SUMMARY of {len(msgs)} messages")
        return chat_routes

    def test_the_kept_tail_opens_on_a_user_message_not_a_stranded_reply(self, chatty, stub_summary):
        asyncio.run(_fold_now(chatty))
        after = _conversation(_prompt(chatty))
        # The raw token-budget cut (keep 6) would open on "reply-7", the
        # assistant's answer to "OLD-7". Alignment pulls OLD-7 in too, so the
        # verbatim tail never opens mid-exchange.
        assert "OLD-7" in after
        assert "reply-7" in after
        assert "OLD-6" not in after

    def test_alignment_declines_when_it_would_break_the_meaningful_fold_guardrail(self, owned, stub_summary):
        """No user turn sits anywhere in this tail, so walking back to one would
        run off the front and leave nothing folded -- well under the "fold at
        least 2 messages" guardrail. Aligning must be declined and the
        token-budget answer (fold the first 4 of these 6) kept as-is."""
        tid = chat_store.create_thread("general")["id"]
        for i in range(6):
            chat_store.append_message(tid, "assistant", f"A{i} " + ("filler " * 1200))
        folded, err = asyncio.run(_fold_now(tid))
        assert err == ""
        assert folded == 4


class TestTheSummarizerMetersItsSpend:
    """`_summarize_sync` is a real `call_cloud` call and was going entirely
    unrecorded in `usage_ledger`, so a thread that compacted often looked
    cheaper than it actually was. `call_cloud` returns no token counts, so
    the entry is an ESTIMATE from the same chars/4 proxy `_est_tokens` uses,
    recorded report-only: it must never gate or block a compaction."""

    @pytest.fixture()
    def stub_summary(self, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync",
                            lambda prev, msgs: f"SUMMARY of {len(msgs)} messages")
        return chat_routes

    @pytest.fixture()
    def ledger(self, tmp_path, monkeypatch):
        from services import usage_ledger
        monkeypatch.setattr(usage_ledger, "LEDGER_PATH", tmp_path / "usage.jsonl")
        return usage_ledger

    def test_a_successful_fold_records_an_estimate(self, chatty, stub_summary, ledger):
        asyncio.run(_fold_now(chatty))
        entries = ledger._read_entries()
        compaction = [e for e in entries if e.get("role") == "compaction"]
        assert len(compaction) == 1
        assert compaction[0]["chat"] == chatty
        assert compaction[0]["tokens"] > 0

    def test_a_fold_with_nothing_to_do_records_nothing(self, owned, stub_summary, ledger):
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hello")
        asyncio.run(_fold_now(tid))
        assert ledger._read_entries() == []

    def test_a_failed_summarizer_records_nothing(self, chatty, ledger, monkeypatch):
        from routes import chat as chat_routes

        def boom(prev, msgs):
            raise RuntimeError("boom")

        monkeypatch.setattr(chat_routes, "_summarize_sync", boom)
        asyncio.run(_fold_now(chatty))
        assert ledger._read_entries() == []


class TestAFailedFoldChangesNothingAndSaysSo:
    def test_a_summarizer_that_raises_is_reported(self, chatty, monkeypatch):
        from routes import chat as chat_routes

        def boom(prev, msgs):
            raise RuntimeError("Argument list too long")

        monkeypatch.setattr(chat_routes, "_summarize_sync", boom)
        before = _conversation(_prompt(chatty))
        folded, err = asyncio.run(
            _fold_now(chatty))
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
            _fold_now(chatty))
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


class TestNoteAutoCompactMarksTheTranscriptWithoutMovingTheCut:
    """`chat_store.note_auto_compact` records that the CLI compacted its own
    window mid-turn, unasked. Per the 2026-08-20 operator decision it draws
    the SAME divider a manual compact draws (`MessageList.ContextCut`), but
    it must not itself change what the next turn sends the model: it carries
    no cut position and no summary, so `_fold`'s cut-resolution loop ignores
    it exactly the way it ignores "reset"."""

    def test_it_writes_an_auto_boundary_event(self, chatty):
        chat_store.note_auto_compact(chatty)
        boundaries = [e for e in chat_log.read_events(chatty) if e.get("kind") == "boundary"]
        assert [b["mode"] for b in boundaries] == ["auto"]
        assert boundaries[0]["at_seq"] > 0

    def test_it_does_not_move_the_cut(self, chatty):
        before = _conversation(_prompt(chatty))
        chat_store.note_auto_compact(chatty)
        after = _conversation(_prompt(chatty))
        assert after == before
        assert not chat_store.get_thread(chatty).get("cleared_through")

    def test_it_does_not_disturb_an_existing_compact_cut(self, chatty, monkeypatch):
        from routes import chat as chat_routes
        monkeypatch.setattr(chat_routes, "_summarize_sync", lambda prev, msgs: "S")
        asyncio.run(_fold_now(chatty))
        cut_before = chat_store.cut_state(chatty)
        chat_store.note_auto_compact(chatty)
        assert chat_store.cut_state(chatty) == cut_before

    def test_it_is_a_quiet_no_op_on_a_thread_that_is_gone(self, owned):
        # `_fold` returns None for an unknown thread; this must return quietly
        # rather than raise, the same as `note_session_reset`. A well-formed
        # id (hex + dashes) that was never created, so it fails at "no such
        # thread" rather than at id validation.
        chat_store.note_auto_compact("deadbeef-dead-beef-dead-beefdeadbeef")


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
        asyncio.run(_fold_now(chatty))
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
