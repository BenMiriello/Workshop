"""When a `result` line ends the turn, and when it only ends one request.

The failure this pins: the driver retired the CLI on the FIRST result line, so
anything the process had started for a SECOND prompt was killed mid-flight. Six
turns in the operator's log committed "Ended without a reply" or "Finished with
no closing message" over a message the model was in the middle of answering; all
six carry `interruptedByShutdown` in the CLI's own session journal at that
second. See services/turn_end.py for the measurements.
"""

from services.turn_end import STUCK_GRACE_S, TurnProgress, turn_is_over


def progress(**kw) -> TurnProgress:
    """An ordinary finished turn, with the field under test overridden."""
    base = dict(delivered=1, replayed=1, results=1, produced=True,
                is_command=False, idle_s=0.0, stopped=False)
    base.update(kw)
    return TurnProgress(**base)


class TestTheOrdinaryTurnIsUnAffected:
    """The common path must retire exactly as promptly as it did before. This
    rule runs on every turn; making the normal one wait would be a worse bug
    than the one it fixes."""

    def test_one_prompt_read_answered_and_spoken_is_over(self):
        assert turn_is_over(progress()) is True

    def test_a_local_slash_command_is_over_with_nothing_produced(self):
        # The CLI runs it without calling the model, so producing nothing is the
        # right outcome and must not be waited on.
        assert turn_is_over(progress(produced=False, is_command=True)) is True

    def test_a_stopped_turn_is_over_immediately(self):
        # The operator's decision ends it. Nothing is owed to a cancelled run,
        # and holding the process open would ignore the button they pressed.
        assert turn_is_over(progress(delivered=2, replayed=0, results=0,
                                     produced=False, stopped=True)) is True


class TestAMessageStillInFlightHoldsTheProcessOpen:
    """The injection race. A message sent while the turn is running is written
    to the same stdin; sent late enough, the CLI answers the first prompt and
    opens a new request for this one in the same instant the driver reaches for
    the kill."""

    def test_a_delivery_the_cli_has_not_acknowledged_is_not_over(self):
        assert turn_is_over(progress(delivered=2, replayed=1, results=1)) is False

    def test_a_message_read_but_not_yet_answered_is_not_over(self):
        assert turn_is_over(progress(delivered=2, replayed=2, results=1)) is False

    def test_the_second_result_ends_it(self):
        assert turn_is_over(progress(delivered=2, replayed=2, results=2)) is True

    def test_a_turn_working_on_the_injected_message_is_never_timed_out(self):
        # `idle_s` is SILENCE, not elapsed time: every segment resets it. A turn
        # ten minutes into an injected message keeps resetting it and is never
        # touched by the stuck bound.
        assert turn_is_over(progress(delivered=2, replayed=2, results=1,
                                     idle_s=STUCK_GRACE_S - 1)) is False


class TestTheResumeCase:
    """On `--resume`, a session with background agents left over from the
    previous process delivers a `<task-notification>` as a user prompt BEFORE
    ours. It gets a result of its own, ~90ms after spawn, and the driver read
    that as the end of a turn whose prompt the model had not started on. Both
    dead turns of 2026-08-12 are this shape."""

    def test_a_result_with_nothing_produced_does_not_end_the_turn(self):
        assert turn_is_over(progress(produced=False)) is False

    def test_it_ends_once_the_real_answer_lands(self):
        # Our prompt is answered next: output, then its own result.
        assert turn_is_over(progress(replayed=1, results=2, produced=True)) is True


class TestTheStuckBound:
    """A chat turn has no wall clock by design, so an unbalanced turn that goes
    completely silent needs SOME end. Silence is the test, and the bound only
    ever applies to a turn that is already in the broken state."""

    def test_silence_past_the_grace_ends_an_unbalanced_turn(self):
        assert turn_is_over(progress(delivered=2, replayed=1, results=1,
                                     idle_s=STUCK_GRACE_S)) is True

    def test_silence_past_the_grace_ends_a_turn_that_produced_nothing(self):
        assert turn_is_over(progress(produced=False, idle_s=STUCK_GRACE_S)) is True

    def test_the_grace_is_long_enough_that_nothing_normal_reaches_it(self):
        # An echo comes back in milliseconds and any work resets the clock, so
        # this bound must be far above both. It is a backstop against a wedged
        # process, not a turn budget.
        assert STUCK_GRACE_S >= 30
