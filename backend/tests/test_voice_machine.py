"""The conversation policy, as executable rules.

Every test here is a symptom the operator reported, or an invariant that a
previous fix earned. The reducer is pure, so all of it runs without a browser,
a microphone or a model. That is the point: every bug reported after a
"verified" claim in the previous rounds was in a path no test executed.
"""
from __future__ import annotations

import pytest

from services.voice import machine as m
from services.voice.machine import (
    Abandon, AbandonGeneration, CancelTurn, ClauseAudible, ClauseHeard,
    ClauseStatus, DeviceState, Endpoint, Interrupt, Phase, ReplyClause,
    ReplyDone, ReplyFailed, SegmentTranscribed, SetLanes, SpeechEnded,
    SpeechOutDone, SpeechStarted, Speak, StartGeneration, Started,
    StopSpeaking, State, SubmitText, Stopped,
)


def run(*events, state=None):
    """Fold events, returning (state, effects)."""
    s = state if state is not None else m.reduce(State(), Started()).state
    fx = []
    for i, ev in enumerate(events):
        out = m.reduce(s, ev, turn_id=f"turn{i}")
        s = out.state
        fx.extend(out.effects)
    return s, fx


def speak_a_turn(text="hello there"):
    """Get to a live turn the ordinary way."""
    return run(SegmentTranscribed(text), Endpoint(complete=True))


def only(fx, kind):
    return [f for f in fx if isinstance(f, kind)]


# ---------------------------------------------------------------- the basics
class TestOrdinaryTurn:
    def test_a_finished_utterance_starts_a_turn(self):
        s, fx = speak_a_turn("what is the weather")
        assert s.phase is Phase.THINKING
        assert s.last_user == "what is the weather"
        started = only(fx, StartGeneration)
        assert len(started) == 1
        assert started[0].text == "what is the weather"

    def test_an_incomplete_endpoint_does_not_start_anything(self):
        # THE CORE OF THE REDESIGN. A pause mid-thought is not an ending, and
        # under the old timer it was: at the operator's stored 700ms, below a
        # normal breath, one sentence routinely arrived as three messages.
        s, fx = run(SegmentTranscribed("i was thinking that"), Endpoint(complete=False))
        assert s.phase is Phase.LISTENING
        assert only(fx, StartGeneration) == []
        assert s.segments == ("i was thinking that",)

    def test_segments_accumulate_across_pauses_into_one_message(self):
        s, fx = run(
            SegmentTranscribed("make the button blue"),
            Endpoint(complete=False),
            SegmentTranscribed("and move it left"),
            Endpoint(complete=True),
        )
        assert only(fx, StartGeneration)[0].text == "make the button blue and move it left"

    def test_the_reply_streams_clause_by_clause(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, fx = run(ReplyClause(tid, "Sure thing,"), ReplyClause(tid, "here it is."), state=s)
        assert [f.text for f in only(fx, Speak)] == ["Sure thing,", "here it is."]
        assert len(s.turn.clauses) == 2

    def test_a_turn_ends_and_returns_to_listening(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, _ = run(ReplyClause(tid, "Done."), ClauseAudible(tid, 0), ClauseHeard(tid, 0),
                   ReplyDone(tid), SpeechOutDone(tid), state=s)
        assert s.phase is Phase.LISTENING
        assert s.turn is None


# --------------------------------------------- continuation vs interruption
class TestTheOneRule:
    """The line is whether anything was HEARD, not how much time passed."""

    def test_speaking_before_the_reply_is_audible_merges(self):
        # The operator paused, the endpoint fired early, and they carried on.
        # They cannot be reacting to something that has not made a sound, so
        # this is one thought, not two messages plus a wasted reply.
        s, _ = speak_a_turn("make the button blue")
        tid = s.turn.id
        s, fx = run(ReplyClause(tid, "Okay."), SpeechStarted(), state=s)
        assert s.turn is None
        assert s.segments == ("make the button blue",)
        ab = only(fx, AbandonGeneration)
        assert len(ab) == 1 and ab[0].reason is Abandon.DISCARD

    def test_the_merged_thought_lands_as_exactly_one_message(self):
        s, _ = speak_a_turn("make the button blue")
        tid = s.turn.id
        s, fx = run(
            ReplyClause(tid, "Okay."), SpeechStarted(),
            SegmentTranscribed("and move it left"), SpeechEnded(), Endpoint(complete=True),
            state=s,
        )
        starts = only(fx, StartGeneration)
        assert len(starts) == 1
        assert starts[0].text == "make the button blue and move it left"

    def test_a_discarded_turn_never_persists(self):
        # Abandon.DISCARD is what stops the fragment reaching the transcript and
        # coming back as a duplicate of the message that replaced it.
        s, _ = speak_a_turn("half a sentence")
        s, fx = run(SpeechStarted(), state=s)
        assert only(fx, AbandonGeneration)[0].reason is Abandon.DISCARD

    def test_speaking_over_audible_speech_interrupts(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, fx = run(ReplyClause(tid, "The answer is four."), ClauseAudible(tid, 0),
                    ClauseHeard(tid, 0), SpeechStarted(), state=s)
        ab = only(fx, AbandonGeneration)
        assert ab[0].reason is Abandon.BARGE
        assert ab[0].heard == 1
        assert ab[0].heard_text == "The answer is four."
        assert only(fx, StopSpeaking)
        assert s.interrupts == 1

    def test_an_interruption_does_not_carry_the_old_utterance_forward(self):
        # They heard the answer and are saying something new. Prepending what
        # they said last time would put words in their mouth.
        s, _ = speak_a_turn("what is two plus two")
        tid = s.turn.id
        s, _ = run(ReplyClause(tid, "Four."), ClauseAudible(tid, 0), ClauseHeard(tid, 0),
                   SpeechStarted(), state=s)
        assert s.segments == ()

    def test_audible_but_not_yet_finished_still_counts_as_heard(self):
        # Mid-sentence is heard. The old code keyed on spokenCount, which is only
        # incremented when a sentence FINISHES, so talking over the first
        # sentence was misread as "nothing heard yet" and merged.
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, fx = run(ReplyClause(tid, "Well, actually the answer is complicated."),
                    ClauseAudible(tid, 0), SpeechStarted(), state=s)
        assert only(fx, AbandonGeneration)[0].reason is Abandon.BARGE


# ------------------------------------------------------------ silence rules
class TestSilenceIsNeverAnError:
    def test_an_empty_endpoint_with_no_speech_says_nothing(self):
        # Reported twice: "it still prints out no signal from the microphone if
        # i dont speak fast enough". A quiet room is not a fault.
        s, _ = run(Endpoint(complete=True, had_speech=False))
        assert s.error is None

    def test_an_empty_endpoint_after_a_cough_says_nothing(self):
        s, _ = run(Endpoint(complete=True, had_speech=False, stt_failed=False))
        assert s.error is None

    def test_an_empty_endpoint_after_real_speech_does_say_something(self):
        s, _ = run(Endpoint(complete=True, had_speech=True))
        assert s.error == m._NOT_CAUGHT

    def test_a_failure_to_reach_the_engine_is_always_reported(self):
        s, _ = run(Endpoint(complete=True, had_speech=False, stt_failed=True))
        assert s.error == m._STT_UNREACHABLE

    def test_a_dead_device_is_reported_from_the_device_not_the_level(self):
        s, _ = run(DeviceState(ok=False))
        assert s.error == m._DEVICE_LOST
        s, _ = run(DeviceState(ok=True), state=s)
        assert s.error is None

    def test_trying_again_clears_a_transient_complaint(self):
        s, _ = run(Endpoint(complete=True, had_speech=True))
        assert s.error
        s, _ = run(SpeechStarted(), state=s)
        assert s.error is None


# ------------------------------------------------------------------- lanes
class TestLanes:
    def test_muting_the_mic_is_silent_and_disarms_capture(self):
        s, fx = run(SetLanes(mic_on=False))
        assert s.mic_on is False
        assert s.error is None
        assert [f.on for f in fx if isinstance(f, m.ArmCapture)] == [False]

    def test_unmuting_the_mic_rearms_it(self):
        s, _ = run(SetLanes(mic_on=False))
        s, fx = run(SetLanes(mic_on=True), state=s)
        assert s.mic_on is True
        assert [f.on for f in fx if isinstance(f, m.ArmCapture)] == [True]

    def test_writing_one_lane_never_moves_the_other(self):
        # The mic button was inert in BOTH directions because its write was
        # recomputed from a mode nobody changed. Lanes are independent here.
        s, _ = run(SetLanes(mic_on=False))
        assert s.speak_on is True
        s, _ = run(SetLanes(speak_on=False), state=s)
        assert s.mic_on is False and s.speak_on is False

    def test_with_speech_muted_clauses_are_captioned_and_heard_at_once(self):
        s, _ = run(SetLanes(speak_on=False))
        s, _ = run(SegmentTranscribed("hi"), Endpoint(complete=True), state=s)
        tid = s.turn.id
        s, fx = run(ReplyClause(tid, "Hello."), state=s)
        assert only(fx, Speak) == []
        assert s.turn.clauses[0].status is ClauseStatus.HEARD
        assert s.turn.heard == 1

    def test_muting_mid_reply_stops_audio_and_keeps_the_captions(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, _ = run(ReplyClause(tid, "One."), ReplyClause(tid, "Two."),
                   ClauseAudible(tid, 0), state=s)
        s, fx = run(SetLanes(speak_on=False), state=s)
        assert only(fx, StopSpeaking)
        assert all(c.status is ClauseStatus.HEARD for c in s.turn.clauses)


# ---------------------------------------------------------------- typed text
class TestTypedInput:
    def test_typing_starts_a_turn_like_speaking_does(self):
        s, fx = run(SubmitText("do the thing"))
        assert only(fx, StartGeneration)[0].text == "do the thing"
        assert s.phase is Phase.THINKING

    def test_typing_over_an_audible_reply_interrupts_it(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, _ = run(ReplyClause(tid, "Sure."), ClauseAudible(tid, 0), ClauseHeard(tid, 0),
                   state=s)
        s, fx = run(SubmitText("actually, no"), state=s)
        assert only(fx, AbandonGeneration)[0].reason is Abandon.BARGE
        assert only(fx, StartGeneration)[0].text == "actually, no"

    def test_typed_words_are_never_mixed_with_half_heard_speech(self):
        # The operator chose these words. Appending a stray transcription to
        # them would send something they did not write.
        s, _ = run(SegmentTranscribed("umm"), SubmitText("send this exactly"))
        assert s.last_user == "send this exactly"
        assert s.segments == ()


# ------------------------------------------------------------ manual controls
class TestManualControls:
    def test_stop_ends_the_reply_and_returns_to_listening(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, _ = run(ReplyClause(tid, "Long answer."), ClauseAudible(tid, 0), state=s)
        s, fx = run(Interrupt(), state=s)
        assert s.phase is Phase.LISTENING
        assert only(fx, StopSpeaking)

    def test_cancel_drops_the_turn_and_the_utterance_that_caused_it(self):
        s, _ = speak_a_turn("this was misheard")
        s, fx = run(CancelTurn(), state=s)
        assert s.last_user == ""
        assert s.segments == ()
        assert only(fx, AbandonGeneration)[0].reason is Abandon.CANCELLED

    def test_a_reply_failure_surfaces_and_re_arms(self):
        s, _ = speak_a_turn()
        s, _ = run(ReplyFailed(s.turn.id, "worker died"), state=s)
        assert s.phase is Phase.LISTENING
        assert "worker died" in s.error


# ------------------------------------------------------------ late events
class TestStaleEvents:
    def test_events_from_an_abandoned_turn_are_ignored(self):
        # This is what the epoch counter did, minus the counter: a turn is
        # identified, so an event naming a dead one simply does not match.
        s, _ = speak_a_turn()
        dead = s.turn.id
        s, _ = run(Interrupt(), state=s)
        before = s
        s, fx = run(ReplyClause(dead, "ghost"), ReplyDone(dead),
                    SpeechOutDone(dead), state=s)
        assert s == before
        assert fx == []

    def test_a_clause_index_out_of_range_does_not_crash(self):
        s, _ = speak_a_turn()
        tid = s.turn.id
        s, _ = run(ClauseHeard(tid, 7), state=s)
        assert s.turn.heard == 0


# --------------------------------------------------------------- lifecycle
class TestLifecycle:
    def test_nothing_happens_before_start(self):
        s, fx = run(SegmentTranscribed("hi"), Endpoint(), state=State())
        assert s.phase is Phase.IDLE
        assert fx == []

    def test_stop_tears_down_the_live_turn(self):
        s, _ = speak_a_turn()
        s, fx = run(Stopped(), state=s)
        assert s.phase is Phase.IDLE
        assert only(fx, AbandonGeneration)
        assert [f.on for f in fx if isinstance(f, m.ArmCapture)] == [False]

    def test_start_keeps_the_lane_settings(self):
        s, _ = run(SetLanes(mic_on=False, speak_on=False))
        s2 = m.reduce(s, Started()).state
        assert s2.mic_on is False and s2.speak_on is False


# ------------------------------------------------------------------ display
class TestWhatTheOperatorSees:
    def test_pending_user_shows_committed_and_live_text_together(self):
        s, _ = run(SegmentTranscribed("make it"), m.PartialTranscript("blue and"))
        assert s.pending_user == "make it blue and"

    def test_phase_is_derived_and_cannot_disagree(self):
        s, _ = speak_a_turn()
        assert s.phase is Phase.THINKING
        s, _ = run(ReplyClause(s.turn.id, "Hi."), ClauseAudible(s.turn.id, 0), state=s)
        assert s.phase is Phase.SPEAKING
