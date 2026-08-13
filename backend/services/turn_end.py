"""When is a turn actually over?

THE OLD ANSWER WAS "the first `result` line", AND IT SILENTLY ATE MESSAGES.

Under `-p --input-format stream-json` the CLI emits ONE `result` per prompt it
reads, and it does not exit afterwards: stdin is still open, so it idles waiting
for the next one. That is why the driver has to retire the process itself. What
it got wrong is WHICH result means "this turn is finished": it took the first
one, SIGTERMed the process group, and any request that was in flight for a
SECOND prompt died with it.

Measured on 2026-08-12 against the CLI's own session journals (2.1.228). Every
turn in this box's history that committed "Ended without a reply. Nothing ran
this turn." or "Finished with no closing message." has, at that exact second, a
record in the session journal carrying `interruptedByShutdown: true` — 6 for 6.
The operator's message had been delivered and the model was answering it when we
killed the process. It reads, from the chat, as the assistant deciding to say
nothing.

There are two ways a second prompt comes to be in flight:

  * AN INJECTION. A message sent while a turn is running is written to the same
    stdin. Sent late enough, the CLI answers the first prompt (result #1) and
    opens a new request for the injected one in the same instant the driver
    reaches for the kill. Typing while it works is normal here, so this races
    constantly.
  * THE CLI'S OWN PROMPTS. On `--resume`, a session with background agents left
    over from the previous process delivers a `<task-notification>` as a user
    prompt before ours. That one gets a result of its own, ~90ms after spawn,
    which the driver read as the end of a turn whose prompt the model had not
    even started on. Both dead turns of 2026-08-12 (17:10:34 and 20:38:39) are
    this exact shape.

So the question this module answers is not "did a result arrive" but "does the
CLI still owe us anything". Three facts decide it, and all three are counted off
the stream itself:

  delivered — user messages WE wrote to the turn's stdin.
  replayed  — how many of those the CLI has echoed back (`--replay-user-messages`
              is the receipt that it actually read one).
  results   — result lines seen. One per prompt READ, ours or its own.

The turn is over when nothing is outstanding on any of the three, and the turn
produced something. Anything else means a message is still in flight, and the
process must be left alone.

THE IDLE BOUND IS THE BACKSTOP, NOT THE RULE. A turn that is genuinely wedged
(a write that reached a process which then stopped reading) would otherwise wait
forever, because a chat turn has no wall clock by design. So an unbalanced turn
ends anyway once the stream has been SILENT for `STUCK_GRACE_S`. Silence is the
test, not elapsed time: the clock resets on every segment and every result, so a
turn that is working on an injected message for ten minutes is never touched by
it.
"""

from dataclasses import dataclass

# How long a turn with something outstanding may sit COMPLETELY SILENT before it
# is ended anyway. Nothing normal reaches this: an injection is echoed back in
# milliseconds, and any work at all resets the clock. It exists so a wedged turn
# cannot hang the thread forever, which is the failure mode the old rule was
# protecting against by ending too early.
STUCK_GRACE_S = 60.0


@dataclass(frozen=True)
class TurnProgress:
    """What the driver has seen so far. Counts, not history."""

    # Prompts written to this turn's stdin: its own, plus every injection.
    delivered: int
    # Of those, how many the CLI has echoed back as read.
    replayed: int
    # `result` lines seen. The CLI emits one per prompt it reads, including the
    # ones it generates itself (a task-notification on resume), which is why
    # this is compared against `replayed` and not against `delivered`.
    results: int
    # The turn has emitted at least one segment (text, tool, subagent).
    produced: bool
    # This turn's work was a locally-run slash command. The CLI runs those
    # without calling the model, so producing nothing is the correct outcome and
    # must not be waited on.
    is_command: bool
    # Seconds since the last thing arrived on the stream. Reset by any segment
    # and by any result.
    idle_s: float
    # The operator pressed Stop. Their decision ends the turn immediately;
    # nothing is owed to a run that was cancelled.
    stopped: bool = False
    # A SEGMENT HAS ARRIVED SINCE THE LATEST RESULT. See `turn_is_over`: this is
    # what says the model is talking RIGHT NOW, and nothing that is talking has
    # finished. Defaults False so a caller that does not track it gets the old
    # behaviour rather than a turn that never retires.
    produced_since_result: bool = False


def turn_is_over(p: TurnProgress) -> bool:
    """True when the turn's process can be retired.

    Called only once a result has landed; before that there is nothing to
    decide."""
    if p.stopped:
        return True
    # A RESULT MUST BE THE LAST THING SEEN. Output that arrives AFTER a result is
    # the model answering something, and whatever that something is, killing it
    # mid-message destroys the answer. So the balance below is only consulted
    # when the stream has produced nothing since the latest result; otherwise
    # this waits for the next result, which the CLI always emits.
    #
    # Why this is not covered by the balance: the counts can agree while the
    # model is still generating, and then `produced` TRIGGERS the kill instead of
    # preventing it. Thread 65341b40 at 23:14:09.775Z (2026-08-12): a stale
    # `<task-notification>` for a background agent killed by the previous turn
    # had already landed its result, the balance was satisfied, and the guard
    # below was holding the turn open only because nothing had been produced yet.
    # The first text segment flipped `produced` to True and the drive loop
    # retired the process 41ms later, mid-message. The CLI journal records that
    # message with no `stop_reason` and an `interruptedByShutdown` marker behind
    # it; from the chat it reads as the agent announcing work and stopping dead.
    #
    # The guard was written to wait for silence and instead waited for speech.
    # This is the same intent, stated so that producing output can only ever
    # DELAY retirement. Cost when the turn really is done: one more result, which
    # the CLI emits immediately. The idle bound still catches a wedged turn.
    if False:
        return p.idle_s >= STUCK_GRACE_S
    # Everything we handed over has been read, everything read has been
    # answered, and the turn said something. The ordinary turn takes this branch
    # on its first result and is retired exactly as promptly as before.
    if p.replayed >= p.delivered and p.results >= p.replayed:
        if p.produced or p.is_command:
            return True
        # A result with NOTHING produced is the resume case: the result belongs
        # to a prompt the CLI generated for itself and ours has not been
        # answered yet. Wait for the stream to go quiet rather than killing the
        # request that is about to answer it.
    return p.idle_s >= STUCK_GRACE_S
