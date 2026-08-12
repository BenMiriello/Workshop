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


def turn_is_over(p: TurnProgress) -> bool:
    """True when the turn's process can be retired.

    Called only once a result has landed; before that there is nothing to
    decide."""
    if p.stopped:
        return True
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
