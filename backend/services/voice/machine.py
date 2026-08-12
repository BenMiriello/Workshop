"""The conversation, as a pure reducer: (state, event) -> (state, effects).

This is the ONLY place voice policy lives. It has no timers, no sockets, no
models and no mutable state: the runtime turns the world into events, the
reducer decides, and the runtime performs the effects. That is what makes it
exhaustively testable without a browser, which matters here more than usual
because every bug the operator reported after a "verified" claim was in a path
no test executed.

WHAT IT REPLACES. The old client-side VoiceSession carried 47 instance fields
and eight near-identical teardown blocks (stop / supersedeDraft / reclaimTurn /
bargeIn / cancelTurn / onReplyError / onThinkingTimeout / muteCurrentSpeech),
each doing some subset of "bump the epoch, cancel the handle, detach the
speaker, stop the speaker, leave barge mode, clear the probe, reset sentences".
They differed in small ways, and the differences were where the bugs were. Here
there is one `_abandon`.

THE MODELLING CHANGE THAT MAKES IT SMALL. The user speaking and the agent
answering are CONCURRENT, not alternating. The old machine was
`listening -> thinking -> speaking`, strictly serial, so every flag with "while"
in its name (`spokeWhileThinking`) existed to smuggle concurrency back in. Here
they are two tracks:

    user  : silent | speaking
    agent : idle (turn is None) | generating | speaking

and the whole interaction policy is the table in `_on_speech_started` and
`_on_endpoint`. It keeps the one rule the previous round got right:

    THE LINE BETWEEN A CONTINUATION AND AN INTERRUPTION IS WHETHER ANYTHING WAS
    HEARD, NOT HOW MUCH TIME PASSED.

You cannot be reacting to a sentence you have not heard, so talking over a reply
that has not made a sound yet means the endpoint fired too early: throw the reply
away and let what comes next stand as its own message. Talking over one you have
heard means you are answering it: interrupt.

WHAT WAS SAID IS RECORDED WHEN IT IS CAPTURED, not when a reply finishes, which
is how typing has always worked (routes/chat appends the user message and only
then runs the model). Voice did the opposite, and so a cancel, a barge, a failed
reply, a dropped socket or a turn that never ended each destroyed words the
operator had already watched appear. Nothing here takes a recorded message back.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Iterable

# ---------------------------------------------------------------- vocabulary


class Phase(str, Enum):
    """The phase the UI renders. DERIVED from the two tracks, never stored, so
    it cannot disagree with them."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class ClauseStatus(str, Enum):
    PENDING = "pending"     # generated, not yet audible
    SPEAKING = "speaking"   # audio in flight
    HEARD = "heard"         # finished playing, or captioned with speech muted


class Abandon(str, Enum):
    """Why a turn is being dropped.

    This governs the REPLY only. It used to decide whether the operator's own
    words were persisted too, which is why a cancel could erase them; they are
    now written when they are captured and no reason here revokes that. What
    `DISCARD` still buys is that the abandoned reply is never written, so a
    superseded turn does not leave half an answer in the transcript."""

    DISCARD = "discard"       # never happened: merged into a later turn, or retried
    BARGE = "barge"           # the operator spoke over it; keep what was heard
    CANCELLED = "cancelled"   # the operator scrapped it
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Clause:
    text: str
    status: ClauseStatus = ClauseStatus.PENDING


@dataclass(frozen=True)
class Turn:
    """The agent track. `None` means idle."""

    id: str
    utterance: str                       # what the operator said to cause it
    clauses: tuple[Clause, ...] = ()
    heard: int = 0                       # clauses fully played
    generating: bool = True              # until ReplyDone
    audible: bool = False                # a sample has actually played

    @property
    def heard_text(self) -> str:
        return self.clauses[self.heard - 1].text if self.heard else ""


@dataclass(frozen=True)
class State:
    # ---- session
    running: bool = False
    mic_on: bool = True                  # input lane armed (voice or both)
    speak_on: bool = True                # output lane spoken (voice or both)
    device_ok: bool = True               # the mic track is live, per the client
    # ---- user track
    user_speaking: bool = False
    segments: tuple[str, ...] = ()       # stable transcript of the utterance so far
    partial: str = ""                    # unstable tail, display only
    # ---- agent track
    turn: Turn | None = None
    # ---- history / telemetry
    last_user: str = ""
    error: str | None = None
    turns: int = 0
    interrupts: int = 0

    @property
    def phase(self) -> Phase:
        if not self.running:
            return Phase.IDLE
        if self.turn is None:
            return Phase.LISTENING
        return Phase.SPEAKING if self.turn.audible else Phase.THINKING

    @property
    def pending_user(self) -> str:
        """Captured but not yet sent. The only proof the system is hearing you."""
        return " ".join(p for p in (" ".join(self.segments).strip(), self.partial) if p).strip()


# -------------------------------------------------------------------- events


@dataclass(frozen=True)
class Started:
    """The session opened and the input lane is armed."""


@dataclass(frozen=True)
class Stopped:
    pass


@dataclass(frozen=True)
class SpeechStarted:
    at: int = 0                          # absolute sample offset, for the record


@dataclass(frozen=True)
class SpeechEnded:
    at: int = 0


@dataclass(frozen=True)
class PartialTranscript:
    text: str


@dataclass(frozen=True)
class SegmentTranscribed:
    """A stable piece of the utterance in progress. Committed, never revised."""

    text: str


@dataclass(frozen=True)
class Endpoint:
    """The turn-end detector says the operator has finished. The runtime only
    raises this after the final segment is transcribed, so the machine never has
    to know how long ASR took."""

    complete: bool = True
    had_speech: bool = False             # the VAD saw real speech in this utterance
    stt_failed: bool = False


@dataclass(frozen=True)
class SubmitText:
    """A typed message. Voice and text are ONE conversation: this takes the same
    path a spoken utterance does, so a typed message can interrupt speech and a
    spoken one can follow a typed one."""

    text: str


@dataclass(frozen=True)
class ReplyClause:
    """A speakable clause cut from the generating reply."""

    turn_id: str
    text: str


@dataclass(frozen=True)
class ReplyDone:
    turn_id: str


@dataclass(frozen=True)
class ReplyFailed:
    turn_id: str
    message: str


@dataclass(frozen=True)
class ClauseAudible:
    """Playback of clause `index` has actually started making sound. This is the
    instant the agent becomes interruptible-as-answered rather than
    interruptible-as-too-early."""

    turn_id: str
    index: int


@dataclass(frozen=True)
class ClauseHeard:
    turn_id: str
    index: int


@dataclass(frozen=True)
class SpeechOutDone:
    turn_id: str


@dataclass(frozen=True)
class Interrupt:
    """The operator pressed stop. Explicit, so it needs no heuristic."""


@dataclass(frozen=True)
class CancelTurn:
    """Scrap the turn AND the utterance that caused it (a misheard auto-send)."""


@dataclass(frozen=True)
class SetLanes:
    mic_on: bool | None = None
    speak_on: bool | None = None


@dataclass(frozen=True)
class DeviceState:
    ok: bool


@dataclass(frozen=True)
class Failed:
    """A condition worth telling the operator about. Silence is never one."""

    message: str


Event = (
    Started | Stopped | SpeechStarted | SpeechEnded | PartialTranscript
    | SegmentTranscribed | Endpoint | SubmitText | ReplyClause | ReplyDone
    | ReplyFailed | ClauseAudible | ClauseHeard | SpeechOutDone | Interrupt
    | CancelTurn | SetLanes | DeviceState | Failed
)


# ------------------------------------------------------------------- effects


@dataclass(frozen=True)
class RecordUtterance:
    """Write what was said to the thread, NOW, before a word of reply exists.

    Typing has always worked this way: `routes/chat` appends the user message and
    only then runs the model, so the message is on the thread even if the model
    never answers. Voice did the opposite. It handed the text to the worker and
    the worker persisted the user and assistant messages TOGETHER at the end of a
    completed turn, so anything that went wrong in between -- a cancel, a barge,
    a dropped socket, a failed reply, a turn that never ended -- took the
    operator's words with it. That is not an edge case: it is what he saw, and
    the forensics showed fourteen run events with no user message on the thread.

    What you said is yours. It does not depend on the machine answering.

    `turn_id` doubles as the idempotency key (chat_store.append_message dedups on
    client_id), so a retry or a reconnect cannot write it twice.
    """
    turn_id: str
    text: str


@dataclass(frozen=True)
class StartGeneration:
    turn_id: str
    text: str


@dataclass(frozen=True)
class AbandonGeneration:
    turn_id: str
    reason: Abandon
    heard: int = 0
    heard_text: str = ""


@dataclass(frozen=True)
class Speak:
    turn_id: str
    index: int
    text: str


@dataclass(frozen=True)
class StopSpeaking:
    """Drop queued audio now. The client empties its jitter buffer on this, which
    is why barge-in is instant rather than "after the current sentence"."""

    turn_id: str


@dataclass(frozen=True)
class ArmCapture:
    on: bool


Effect = (
    RecordUtterance | StartGeneration | AbandonGeneration | Speak | StopSpeaking
    | ArmCapture
)


# ------------------------------------------------------------------- reducer


@dataclass
class Outcome:
    state: State
    effects: list[Effect] = field(default_factory=list)


def _abandon(state: State, reason: Abandon) -> tuple[State, list[Effect]]:
    """Drop the active turn. THE ONE TEARDOWN.

    A COMMITTED MESSAGE IS NOT TAKEN BACK. This used to carry the abandoned
    turn's utterance back into the next one, so that pausing mid-thought and
    resuming folded both halves into a single message. That was reasonable when
    the utterance existed only in memory until a reply finished. It is wrong now
    that it is written to the thread at capture: the first half is already a
    message, so folding it into the second recorded the same words twice, once
    alone and once inside a longer message. That is the duplication the operator
    reported, and it is the reason every reference implementation treats
    committed text as append-only and immutable.

    A continuation is simply the next message. The model reads both, because
    both are on the thread.
    """
    turn = state.turn
    if turn is None:
        return state, []
    effects: list[Effect] = [
        AbandonGeneration(turn.id, reason, heard=turn.heard, heard_text=turn.heard_text)
    ]
    if turn.audible:
        effects.append(StopSpeaking(turn.id))
    return replace(state, turn=None), effects


def _begin_turn(state: State, text: str, turn_id: str) -> tuple[State, list[Effect]]:
    text = text.strip()
    if not text:
        return state, []
    return (
        replace(
            state,
            turn=Turn(id=turn_id, utterance=text),
            segments=(),
            partial="",
            last_user=text,
            turns=state.turns + 1,
            error=None,
        ),
        # RECORD FIRST, THEN GENERATE. The order is the point: the runtime
        # performs effects in order and the write completes before generation
        # starts, so there is no window in which the words exist only in memory.
        [RecordUtterance(turn_id, text), StartGeneration(turn_id, text)],
    )


def reduce(state: State, event: Event, *, turn_id: str = "") -> Outcome:
    """Apply one event. `turn_id` is supplied by the runtime because generating
    an id is I/O; the reducer stays deterministic."""
    s = state
    fx: list[Effect] = []

    # ---- session lifecycle ------------------------------------------------
    if isinstance(event, Started):
        return Outcome(replace(State(), running=True, mic_on=s.mic_on, speak_on=s.speak_on),
                       [ArmCapture(s.mic_on)])

    if isinstance(event, Stopped):
        s, fx = _abandon(s, Abandon.CANCELLED)
        return Outcome(replace(State(), mic_on=s.mic_on, speak_on=s.speak_on),
                       fx + [ArmCapture(False)])

    if isinstance(event, SetLanes):
        mic_on = s.mic_on if event.mic_on is None else event.mic_on
        speak_on = s.speak_on if event.speak_on is None else event.speak_on
        fx = []
        if mic_on != s.mic_on:
            fx.append(ArmCapture(mic_on))
        s = replace(s, mic_on=mic_on, speak_on=speak_on)
        if not mic_on:
            # Turning the input lane off must be completely silent: nothing the
            # capture lane left behind is actionable once it is off by intent.
            s = replace(s, partial="", user_speaking=False, error=None)
        if not speak_on and s.turn is not None and s.turn.audible:
            # Muting mid-reply: stop the audio, keep the captions, mark them
            # heard so interrupt/resume accounting is identical in every mode.
            fx.append(StopSpeaking(s.turn.id))
            s = replace(s, turn=_mark_all_heard(s.turn))
        return Outcome(s, fx)

    if isinstance(event, DeviceState):
        return Outcome(replace(s, device_ok=event.ok,
                               error=None if event.ok else _DEVICE_LOST))

    if isinstance(event, Failed):
        return Outcome(replace(s, error=event.message))

    if not s.running:
        return Outcome(s)

    # ---- user track -------------------------------------------------------
    if isinstance(event, SpeechStarted):
        s = replace(s, user_speaking=True)
        # A transient complaint clears the moment they try again.
        if s.error in _TRANSIENT:
            s = replace(s, error=None)
        turn = s.turn
        if turn is None:
            return Outcome(s)
        if turn.heard == 0 and not turn.audible:
            # CONTINUATION. They cannot be answering something they have not
            # heard, so the endpoint fired too early: drop the reply nobody was
            # given. What they said is already a message on the thread and stays
            # one; what they say next becomes the next message.
            s, fx = _abandon(s, Abandon.DISCARD)
            return Outcome(s, fx)
        # INTERRUPTION. They heard it and spoke anyway.
        s, fx = _abandon(s, Abandon.BARGE)
        return Outcome(replace(s, interrupts=s.interrupts + 1), fx)

    if isinstance(event, SpeechEnded):
        return Outcome(replace(s, user_speaking=False))

    if isinstance(event, PartialTranscript):
        return Outcome(replace(s, partial=event.text))

    if isinstance(event, SegmentTranscribed):
        text = event.text.strip()
        if not text:
            return Outcome(replace(s, partial=""))
        return Outcome(replace(s, segments=s.segments + (text,), partial=""))

    if isinstance(event, Endpoint):
        if not event.complete:
            return Outcome(s)                    # keep listening; not finished
        # EVERYTHING HEARD, INCLUDING THE UNSTABLE TAIL. This used to read
        # `" ".join(s.segments)`, which is the stable part only — so the last
        # words of every utterance, still sitting in `partial` when the endpoint
        # fired, were dropped on the floor. "call the dentist tomorrow" was sent
        # as "call the dentist", on EVERY turn; and when the endpoint beat the
        # first final back (a short utterance, or a slow engine) `segments` was
        # empty, the whole thing was deleted, and the operator was told "Didn't
        # catch that" about words the system had already transcribed.
        #
        # `pending_user` is the joiner that was always correct and that the UI
        # was already showing. The message must be exactly what was on screen.
        final = s.pending_user
        if not final:
            # Nothing came back. A REAL failure to reach the engine always gets
            # said. "Didn't catch that" does not, unless there was enough speech
            # for the claim to be fair, because the VAD trips on a cough and a
            # door exactly as it does on a word.
            error = None
            if event.stt_failed:
                error = _STT_UNREACHABLE
            elif event.had_speech:
                error = _NOT_CAUGHT
            return Outcome(replace(s, segments=(), partial="", error=error))
        if s.turn is not None and s.turn.heard == 0 and not s.turn.audible:
            s, fx = _abandon(s, Abandon.DISCARD)
            final = s.pending_user
        s2, fx2 = _begin_turn(s, final, turn_id)
        return Outcome(s2, fx + fx2)

    if isinstance(event, SubmitText):
        text = event.text.strip()
        if not text:
            return Outcome(s)
        if s.turn is not None:
            # Typing is a deliberate act, so it always takes the turn. Whether
            # the pending words are kept follows the same heard/not-heard rule.
            keep = s.turn.heard == 0 and not s.turn.audible
            s, fx = _abandon(
                s, Abandon.DISCARD if keep else Abandon.BARGE)
            if not keep:
                s = replace(s, interrupts=s.interrupts + 1)
        # Anything half-spoken is abandoned in favour of what was typed: the
        # operator chose the words, so nothing may be appended to them.
        s = replace(s, segments=(), partial="")
        s2, fx2 = _begin_turn(s, text, turn_id)
        return Outcome(s2, fx + fx2)

    if isinstance(event, Interrupt):
        if s.turn is None:
            return Outcome(s)
        heard = s.turn.heard
        s, fx = _abandon(s, Abandon.BARGE)
        return Outcome(replace(s, interrupts=s.interrupts + 1 if heard else s.interrupts), fx)

    if isinstance(event, CancelTurn):
        if s.turn is None:
            return Outcome(s)
        s, fx = _abandon(s, Abandon.CANCELLED)
        # Scrapping the turn scraps the utterance that caused it, so a misheard
        # auto-send is fully recoverable and leaves no trace.
        return Outcome(replace(s, segments=(), partial="", last_user=""), fx)

    # ---- agent track ------------------------------------------------------
    turn = s.turn
    if turn is None or getattr(event, "turn_id", None) != turn.id:
        # Late events from an abandoned turn are no-ops. This is what the epoch
        # counter used to do, minus the counter: identity is enough.
        return Outcome(s)

    if isinstance(event, ReplyClause):
        text = event.text.strip()
        if not text:
            return Outcome(s)
        index = len(turn.clauses)
        if s.speak_on:
            turn = replace(turn, clauses=turn.clauses + (Clause(text),))
            fx.append(Speak(turn.id, index, text))
        else:
            # Muted: the caption is the delivery, so it is heard on arrival.
            turn = replace(
                turn,
                clauses=turn.clauses + (Clause(text, ClauseStatus.HEARD),),
                heard=index + 1,
            )
        return Outcome(replace(s, turn=turn), fx)

    if isinstance(event, ClauseAudible):
        clauses = _set_status(turn.clauses, event.index, ClauseStatus.SPEAKING)
        return Outcome(replace(s, turn=replace(turn, clauses=clauses, audible=True)))

    if isinstance(event, ClauseHeard):
        if not 0 <= event.index < len(turn.clauses):
            # An index we have no clause for tells us nothing. Counting it would
            # inflate `heard`, and `heard` is what decides whether the next thing
            # the operator says is a continuation or an interruption.
            return Outcome(s)
        clauses = _set_status(turn.clauses, event.index, ClauseStatus.HEARD)
        heard = max(turn.heard, event.index + 1)
        return Outcome(replace(s, turn=replace(turn, clauses=clauses, heard=heard)))

    if isinstance(event, ReplyDone):
        turn = replace(turn, generating=False)
        if not s.speak_on or not turn.clauses:
            return Outcome(replace(s, turn=None))     # nothing left to play
        return Outcome(replace(s, turn=turn))

    if isinstance(event, SpeechOutDone):
        return Outcome(replace(s, turn=None))

    if isinstance(event, ReplyFailed):
        return Outcome(replace(s, turn=None, error=f"Reply failed. {event.message}".strip()),
                       [StopSpeaking(turn.id)] if turn.audible else [])

    return Outcome(s)


def reduce_all(state: State, events: Iterable[Event]) -> Outcome:
    """Fold a sequence of events. Used by the tests to describe a scenario."""
    effects: list[Effect] = []
    for i, ev in enumerate(events):
        out = reduce(state, ev, turn_id=f"t{i}")
        state = out.state
        effects.extend(out.effects)
    return Outcome(state, effects)


# ------------------------------------------------------------------ helpers

_DEVICE_LOST = "The microphone stopped. Check the device, or whether another app took it."
_STT_UNREACHABLE = "Couldn't reach the speech engine. Try again."
_NOT_CAUGHT = "Didn't catch that. Try again."
# Errors that mean "that attempt did not land", which the next attempt clears.
_TRANSIENT = {_STT_UNREACHABLE, _NOT_CAUGHT}


def _set_status(
    clauses: tuple[Clause, ...], index: int, status: ClauseStatus
) -> tuple[Clause, ...]:
    if not 0 <= index < len(clauses):
        return clauses
    out = list(clauses)
    out[index] = replace(out[index], status=status)
    return tuple(out)


def _mark_all_heard(turn: Turn) -> Turn:
    return replace(
        turn,
        clauses=tuple(replace(c, status=ClauseStatus.HEARD) for c in turn.clauses),
        heard=len(turn.clauses),
    )
