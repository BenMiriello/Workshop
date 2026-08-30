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

    def test_it_does_not_disturb_an_existing_cut(self, chatty):
        chat_store.clear_thread_context(chatty)
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

    def test_it_still_reports_a_stored_compact_cut(self, chatty):
        """Workshop no longer WRITES one, but threads compacted before
        2026-08-29 carry a summary and a compact cut, and the prompt still
        honours both. The divider has to agree with it."""
        chat_store.set_summary(chatty, "S", folded_through=14)
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


class TestTheStopRuleSurvivesACut:
    """A `/clear` drops the CLI session, so the turn after it re-assembles the
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

    def test_it_is_still_there_on_the_turn_after_a_cut(self, chatty):
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


class TestCompactIsTheProvidersOwnCommand:
    """The button runs the CLI's `/compact` on the live session.

    It used to run a fold Workshop computed itself: a Haiku summarizer over
    everything but a ~4000-token tail, a stored summary, a second cut, a
    deferred landing task and a cancel protocol. All of it existed to answer
    "what should a compaction keep", which the provider answers. Deleted
    2026-08-29 by operator decision.

    So what is pinned here is the WIRING, because that is all this endpoint is
    now: the right command goes out as a turn, and a thread that cannot take a
    turn right now keeps it instead of dropping it.
    """

    def test_it_sends_the_command_the_cli_recognises(self, chatty, monkeypatch):
        from routes import chat as chat_routes
        sent = {}

        async def fake_send(thread_id, content, **kw):
            sent["thread_id"], sent["content"] = thread_id, content
            return {"turn_id": "t-1"}

        monkeypatch.setattr(chat_routes, "_send", fake_send)
        monkeypatch.setattr(chat_routes, "_spawn_working_state", lambda *a: None)
        out = asyncio.run(chat_routes.compact_thread(chatty))
        assert sent == {"thread_id": chatty, "content": "/compact"}
        assert out == {"turn_id": "t-1"}

    def test_the_command_is_the_one_build_streaming_watches_for(self):
        """`build_streaming` replaces the CLI's context-usage dump with one
        sentence only when the prompt is exactly this string, so the two must
        not drift apart."""
        from routes import chat as chat_routes
        assert chat_routes._COMPACT_COMMAND.strip().lower() == "/compact"

    def test_a_busy_thread_queues_it_rather_than_refusing(self, chatty, monkeypatch):
        """A slash command cannot be spoken into a running turn, so it waits.
        That is an acceptance: reporting it as a refusal would tell the operator
        nothing happened when something will."""
        from routes import chat as chat_routes

        async def fake_send(thread_id, content, **kw):
            return {"turn_id": None, "queued": True}

        monkeypatch.setattr(chat_routes, "_send", fake_send)
        monkeypatch.setattr(chat_routes, "_spawn_working_state", lambda *a: None)
        assert asyncio.run(chat_routes.compact_thread(chatty)) == {"queued": True}

    def test_a_failure_to_start_is_reported_not_raised(self, chatty, monkeypatch):
        from routes import chat as chat_routes

        async def boom(thread_id, content, **kw):
            raise RuntimeError("no session")

        monkeypatch.setattr(chat_routes, "_send", boom)
        monkeypatch.setattr(chat_routes, "_spawn_working_state", lambda *a: None)
        assert asyncio.run(chat_routes.compact_thread(chatty)) == {"error": "no session"}

    def test_it_does_not_fold_the_transcript_itself(self, chatty, monkeypatch):
        """The cut does not move and no summary is written. Everything the
        operator can read is exactly where it was; only the provider's own
        window changes."""
        from routes import chat as chat_routes

        async def fake_send(thread_id, content, **kw):
            return {"turn_id": "t-1"}

        monkeypatch.setattr(chat_routes, "_send", fake_send)
        monkeypatch.setattr(chat_routes, "_spawn_working_state", lambda *a: None)
        before = _conversation(_prompt(chatty))
        asyncio.run(chat_routes.compact_thread(chatty))
        assert _conversation(_prompt(chatty)) == before
        assert not chat_store.get_thread(chatty).get("summary")
        assert not chat_store.get_thread(chatty).get("cleared_through")

    def test_an_unknown_thread_is_a_404(self, owned):
        from fastapi import HTTPException
        from routes import chat as chat_routes
        with pytest.raises(HTTPException) as e:
            asyncio.run(chat_routes.compact_thread("deadbeef-dead-beef-dead-beefdeadbeef"))
        assert e.value.status_code == 404


class TestTheScopesWorkingStateSurvivesTheFoldsDeletion:
    """The working-state note is what made compaction worth anything to a chat
    OTHER than the one compacting: one note per scope, holding the objective,
    the decisions and their reasons, what is done and what is owed.

    It used to be handed the fold's summary. The fold is gone, so it makes its
    own summarizing call over the transcript. Deleting the fold must not have
    quietly deleted this with it.
    """

    def test_it_writes_the_note_for_the_scope(self, chatty, monkeypatch, tmp_path):
        from routes import chat as chat_routes
        from services import knowledge
        written = {}
        monkeypatch.setattr(chat_routes, "_summarize_sync", lambda prev, msgs: "## Objective\n- ship it")
        monkeypatch.setattr(knowledge, "read_working_state", lambda scope: ("", ""))
        monkeypatch.setattr(
            knowledge, "write_working_state",
            lambda scope, text, **kw: written.update(scope=scope, text=text))
        chat_routes._write_working_state_now(chatty, chat_store.get_thread(chatty))
        assert written["text"] == "## Objective\n- ship it"
        assert written["scope"]

    def test_it_merges_with_what_another_chat_left(self, chatty, monkeypatch):
        """One note per scope, and every chat in the scope writes it. Starting
        from scratch each time would throw away what another chat recorded."""
        from routes import chat as chat_routes
        from services import knowledge
        seen = {}
        monkeypatch.setattr(knowledge, "read_working_state", lambda scope: ("EARLIER", "other"))
        monkeypatch.setattr(knowledge, "write_working_state", lambda *a, **k: None)
        monkeypatch.setattr(
            chat_routes, "_summarize_sync",
            lambda prev, msgs: seen.setdefault("prev", prev) or "S")
        chat_routes._write_working_state_now(chatty, chat_store.get_thread(chatty))
        assert seen["prev"] == "EARLIER"

    def test_a_barely_started_chat_costs_no_model_call(self, owned, monkeypatch):
        from routes import chat as chat_routes
        called = []
        monkeypatch.setattr(chat_routes, "_summarize_sync",
                            lambda prev, msgs: called.append(1) or "S")
        tid = chat_store.create_thread("general")["id"]
        chat_store.append_message(tid, "user", "hi")
        chat_routes._write_working_state_now(tid, chat_store.get_thread(tid))
        assert called == []

    def test_a_failure_here_never_escapes(self, chatty, monkeypatch):
        """The operator pressed compact to reclaim context. A handoff note that
        could not be filed is a log line, never a failed operation."""
        from routes import chat as chat_routes

        def boom(prev, msgs):
            raise RuntimeError("summarizer down")

        monkeypatch.setattr(chat_routes, "_summarize_sync", boom)
        chat_routes._write_working_state_now(chatty, chat_store.get_thread(chatty))
