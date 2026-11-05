"""Whether the last thing said in a stretch of live dictation is a spoken
command (docs/dictation-and-clipboard.md, "Spoken commands").

An action counts only when said on its own: its words are a whole UTTERANCE,
with PAUSE_BEFORE_MS of silence before it (or nothing said before it in the
stream: the start of dictation) and SILENCE_AFTER_MS after. Utterances are
measured on the voice detector's runs of real speech, not on the read audio,
whose pauses are shortened (services/speech/stretch.py). A command said right
after another one is on its own too, since the first needed silence after it.

While the last utterance could still become an action, the client is not shown
its words, so a command never flashes into the field as text.

In commands mode every utterance is said to the app, and in mixed mode so is
one that starts with the trigger word: when such an utterance is none of the
phrases it is held the same way and then understood by what it means
(services/speech/meaning.py). While the client shows numbered things, a number
said on its own picks one.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.speech.command_list import spoken_number
from services.speech.commands import Match, Table, tokens
from services.speech.cuts import Reading
from services.speech.stretch import SpeechRuns, samples

PAUSE_BEFORE_MS = 400
SILENCE_AFTER_MS = 700
# A run of speech this short, alone between pauses, is a click or a breath.
BLIP_MS = 100
# Word times run early, so a word is placed this far past where it is heard.
WORD_SLACK_MS = 150
# The command's span ends this long after its speech, inside the silence after.
TAIL_MS = 200


@dataclass(frozen=True)
class Utterance:
    first: int              # its first word, in the reading
    cut: int                # stream sample between it and the words before
    end: int                # where its speech ends
    words: tuple[str, ...]  # as heard


def _groups(speech: SpeechRuns, upto: int) -> list[list[int]]:
    """Utterances in the stream: runs of speech less than PAUSE_BEFORE_MS
    apart, joined; one that is only a blip left out."""
    out: list[list[int]] = []
    for s, e in speech.runs:
        if s >= upto:
            break
        if out and s - out[-1][1] < samples(PAUSE_BEFORE_MS):
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [g for g in out if g[1] - g[0] >= samples(BLIP_MS)]


def last_said(speech: SpeechRuns, r: Reading) -> Utterance | None:
    """The reading's last utterance, when it began inside this stretch (so
    after a pause, or with nothing said before it)."""
    words = r.heard.words
    groups = _groups(speech, r.to)
    if not words or not groups or groups[-1][0] < r.start:
        return None
    start, end = groups[-1]
    before = groups[-2][1] if len(groups) > 1 and groups[-2][1] >= r.start else None
    first = 0
    if before is not None:
        # Word end times land on either side of the speech detector's run
        # ends (the browser's noise suppression moves them by 0.2 s), so the
        # split is the middle of the pause, never closer than the slack.
        after = max(before + samples(WORD_SLACK_MS), (before + start) // 2)
        first = next((i for i, w in enumerate(words) if r.audio.stream_at(w.last) > after),
                     len(words))
    if first >= len(words):
        return None
    cut = (before + start) // 2 if before is not None else r.start
    return Utterance(first, cut, end, tuple(w.text for w in words[first:]))


def utterances(speech: SpeechRuns, upto: int) -> list[list[int]]:
    """[start, end) of each utterance that began before `upto`."""
    return _groups(speech, upto)


def quiet_after(speech: SpeechRuns, at: int, now: int) -> int:
    """Samples of silence from `at` to `now`, or 0 when real speech came in
    between. Speech after `now` is a later read's business."""
    if any(min(e, now) - max(s, at) >= samples(BLIP_MS) for s, e in speech.runs if s < now):
        return 0
    return max(0, now - at)


@dataclass(frozen=True)
class Candidate:
    """What the end of a reading is, as far as commands go."""
    said: Utterance
    match: Match | None
    could_be: bool
    # Said to the app but none of its phrases: these words, to understand.
    loose: tuple[str, ...] | None = None


def look(table: Table, speech: SpeechRuns, r: Reading, paused: bool,
         numbers: bool = False) -> Candidate | None:
    """The reading's last utterance, when it is an action or could still
    become one."""
    u = last_said(speech, r)
    if u is None:
        return None
    match = table.match(list(u.words), paused)
    toks = tokens(" ".join(u.words))
    if match is None and numbers and not paused and (n := spoken_number(toks)) is not None:
        match = Match("pick", str(n))
    elif match and match.name == "pick" and (n := spoken_number(tokens(match.arg or ""))) is not None:
        match = Match("pick", str(n))
    loose = table.loose(toks) if match is None and not paused and toks else None
    could = match is not None or loose is not None or table.could_be(list(u.words), paused)
    return Candidate(u, match, could, loose) if could else None


def quiet(speech: SpeechRuns, at: int, now: int) -> bool:
    """Enough silence followed speech ending at `at` to decide what it was."""
    return quiet_after(speech, at, now) >= samples(SILENCE_AFTER_MS)


def span_end(h: Candidate, now: int) -> int:
    return min(now, h.said.end + samples(TAIL_MS))


def outside(start: int, end: int, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The parts of [start, end) no span covers."""
    out: list[tuple[int, int]] = []
    at = start
    for a, b in sorted(spans):
        if b <= at or a >= end:
            continue
        if a > at:
            out.append((at, a))
        at = max(at, b)
    if at < end:
        out.append((at, end))
    return out
