"""What a read of some audio heard: its text, and when each word began.

Times are seconds into the audio that was read. A word's `last` is when its
last piece was heard (its closing punctuation, when it has some), so the space
between one word's `last` and the next word's `start` is where a cut between
them falls cleanly.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    last: float


@dataclass(frozen=True)
class Heard:
    text: str
    words: tuple[Word, ...] = field(default=())


def from_tokens(tokens: list[str], stamps: list[float]) -> Heard:
    """Words from subword pieces, where a piece starting with a space starts
    a word (the speech model's own tokens)."""
    words: list[Word] = []
    text, start, last = "", 0.0, 0.0
    for tok, at in zip(tokens, stamps):
        if tok.startswith(" ") and text:
            words.append(Word(text, start, last))
            text = ""
        if not text:
            start = at
        text += tok.strip() if not text else tok
        last = at
    if text:
        words.append(Word(text, start, last))
    return Heard(" ".join(w.text for w in words), tuple(words))


def from_spans(text: str, spans: list[dict]) -> Heard:
    """Words from a server that reports them with start and end times."""
    words = tuple(Word(str(s.get("word", "")).strip(), float(s.get("start", 0.0)),
                       float(s.get("end", s.get("start", 0.0))))
                  for s in spans if str(s.get("word", "")).strip())
    return Heard(text.strip(), words)


# Sounds people make while thinking, not words they mean to write.
FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "hm", "mm"})
_ENDS = (".", "?", "!")


def _bare(text: str) -> str:
    return text.strip(".,?!;:-").lower()


def without_fillers(heard: Heard) -> Heard:
    """The words with "um", "uh" and the like taken out. A filler that ended a
    sentence passes its end to the word before it, and one that began a
    sentence passes its capital to the word after it."""
    if not heard.words:
        kept = [w for w in heard.text.split() if _bare(w) not in FILLERS]
        return Heard(" ".join(kept)) if len(kept) != len(heard.text.split()) else heard
    if not any(_bare(w.text) in FILLERS for w in heard.words):
        return heard
    out: list[Word] = []
    capital = False
    for w in heard.words:
        if _bare(w.text) in FILLERS:
            if w.text.endswith(_ENDS) and out and not out[-1].text.endswith(_ENDS):
                out[-1] = Word(out[-1].text.rstrip(",;:") + w.text[-1], out[-1].start, out[-1].last)
            capital = capital or (w.text[:1].isupper() and (not out or out[-1].text.endswith(_ENDS)))
            continue
        if capital and w.text[:1].islower():
            w = Word(w.text[0].upper() + w.text[1:], w.start, w.last)
        capital = False
        out.append(w)
    return Heard(" ".join(w.text for w in out), tuple(out))
