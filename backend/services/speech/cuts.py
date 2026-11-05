"""Where a stretch of live dictation is cut: just past its last word, or
between two words. The speech model's word times run early (a few hundred ms
at a word's start), so where a word ends is taken from the voice detector's
runs of speech instead (services/speech/live.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from services.speech.heard import Heard
from services.speech.stretch import Shortened, SpeechRuns, samples

# A word whose speech runs straight on into the next is given this long after
# its last piece.
WORD_TAIL_MS = 600


@dataclass(frozen=True)
class Reading:
    start: int
    to: int
    heard: Heard
    audio: Shortened


def run_after(speech: SpeechRuns, at: int) -> tuple[int, int | None]:
    """The end of the run of speech going on at (or next after) `at`, and
    where the run after that starts."""
    runs = speech.runs
    i = next((i for i, (_, e) in enumerate(runs) if e > at), None)
    if i is None:
        return at, None
    return runs[i][1], runs[i + 1][0] if i + 1 < len(runs) else None


def heard_through(speech: SpeechRuns, r: Reading) -> int:
    """Just past the last word heard: where its run of speech ends, or a
    word's tail after its last piece if the speech carries straight on."""
    if not r.heard.words:
        return r.start if not r.heard.text else r.to
    last = r.audio.stream_at(r.heard.words[-1].last)
    run_end, _ = run_after(speech, last)
    return max(r.start, min(run_end + samples(100), last + samples(WORD_TAIL_MS), r.to))


def between(speech: SpeechRuns, r: Reading, i: int) -> int:
    """A cut between word i and the next: in the pause between them when
    there is one, else halfway between their times."""
    words = r.heard.words
    last = r.audio.stream_at(words[i].last)
    nxt = r.audio.stream_at(words[i + 1].start)
    run_end, next_start = run_after(speech, last)
    if next_start is not None and run_end - last <= samples(WORD_TAIL_MS) \
            and next_start <= nxt + samples(WORD_TAIL_MS):
        return (run_end + next_start) // 2
    return r.audio.stream_at((words[i].last + words[i + 1].start) / 2)


# Every update reads the whole open stretch again, and a read's time grows
# with its length (on the processor about 0.3 s at 6 s, 0.5 s at 12 s, 0.8 s at
# 24 s, more on a busy machine), so a stretch is kept short: what a word waits
# for is the read before it finishing. Measured on a replay of run-on
# dictation, 6 s and 12 s give the same words as 12 s and 24 s.
SETTLE_AFTER_MS = 6_000
SETTLE_CONTEXT_MS = 2500
LONGEST_MS = 12_000
ENDS_SENTENCE = (".", "?", "!")


def sentence_cut(r: Reading, agreed: int) -> int | None:
    """The word a long stretch settles through, if any: past SETTLE_AFTER_MS,
    a sentence end within the first `agreed` words (two reads agree on them)
    with SETTLE_CONTEXT_MS heard after it; past LONGEST_MS, the widest gap
    between words, so no read takes long."""
    words = r.heard.words
    length_ms = r.audio.ms
    if length_ms < SETTLE_AFTER_MS or len(words) < 2:
        return None
    limit = (length_ms - SETTLE_CONTEXT_MS) / 1000
    for i in range(min(agreed, len(words) - 1) - 1, -1, -1):
        if words[i].text.endswith(ENDS_SENTENCE) and words[i + 1].start <= limit:
            return i
    if length_ms < LONGEST_MS:
        return None
    fits = [i for i in range(len(words) - 1) if words[i + 1].start <= limit]
    return max(fits, key=lambda i: words[i + 1].start - words[i].last) if fits else None
