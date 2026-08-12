"""Cutting a generating reply into speakable pieces, as early as is fluent.

THE BUG THIS EXISTS TO FIX. The old splitter cut on sentences, withheld the
trailing fragment until generation finished, and merged any piece under 24
characters into its neighbour. The voice system prompt asks for "usually one to
three sentences", so the common case was a reply that produced NOTHING to speak
until the model was completely done. The streaming design did not engage in
precisely the case it was built for.

Cutting on clauses instead means the first audio starts after a handful of
words rather than after a whole sentence, which is worth several hundred
milliseconds on every single turn. The first clause is allowed to be shorter
than later ones: latency is felt at the start of a reply and fluency is felt
through the middle of it.

Everything here is pure, so the phrasing rules are testable without a speech
engine, which is the only way to tell "choppy" from "fluent" without listening
to every change.
"""
from __future__ import annotations

import re

# Enough words to sound like a phrase rather than a stab. The first clause gets
# a lower bar because it buys latency on every turn.
FIRST_MIN_WORDS = 4
NEXT_MIN_WORDS = 8
# Past this, cut at the next word boundary whatever the punctuation says, so a
# comma-free run of prose still streams. The first clause is capped tighter:
# an opening sentence with no early comma is otherwise the one case that can
# still hold up first audio, which is the latency this file exists to remove.
MAX_WORDS = 26
FIRST_MAX_WORDS = 14

_TERMINAL = ".!?…"
_SOFT = ",;:"

# Things that end in a period and are NOT the end of a sentence. Splitting on
# them produces "Dr." as a clause, which Kokoro reads as a letter.
_ABBREV = frozenset("""
mr mrs ms dr prof sr jr st vs etc eg ie approx dept est inc ltd co no vol fig
al am pm cf ca circa min max sec hr
""".split())


def clean_for_speech(text: str) -> str:
    """Strip markdown so TTS reads prose rather than punctuation.

    Server-side now, because the server owns speech output. Previously this ran
    in the browser, which meant the mobile client would have needed its own copy
    of the same rules.
    """
    t = text or ""
    t = re.sub(r"```[\s\S]*?```", " (code) ", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*]\s+", "", t, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", " link ", t)
    t = re.sub(r"[_>#|]", "", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _is_boundary(text: str, i: int) -> bool:
    """Is the character at `i` a real clause boundary in `text`?"""
    ch = text[i]
    if ch not in _TERMINAL and ch not in _SOFT:
        return False
    nxt = text[i + 1] if i + 1 < len(text) else " "
    if not nxt.isspace():
        # "3.14", "10:30", "a,b" — punctuation inside a token, not after it.
        return False
    if ch == ".":
        before = text[:i]
        word = re.split(r"[\s(]", before)[-1].lower().rstrip(".")
        if word in _ABBREV:
            return False
        if len(word) == 1 and word.isalpha():
            return False               # an initial: "J. Smith"
        if word.isdigit():
            return False               # a numbered list item: "1. do this"
    return True


def split_clauses(text: str, *, first: bool = True) -> tuple[list[str], str]:
    """Split `text` into complete clauses plus the trailing remainder.

    The remainder is whatever cannot yet be judged complete; the caller either
    feeds more text or flushes it. `first` lowers the word bar for the opening
    clause.
    """
    out: list[str] = []
    start = 0
    words_since = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace() and i > start and not text[i - 1].isspace():
            words_since += 1
        need = FIRST_MIN_WORDS if (first and not out) else NEXT_MIN_WORDS
        if _is_boundary(text, i):
            hard = text[i] in _TERMINAL
            # Terminal punctuation always ends a clause. Soft punctuation only
            # does once there are enough words for the piece to sound like one.
            if hard or words_since + 1 >= need:
                piece = text[start:i + 1].strip()
                if piece:
                    out.append(piece)
                    start = i + 1
                    words_since = 0
        elif words_since >= (FIRST_MAX_WORDS if (first and not out) else MAX_WORDS) \
                and text[i].isspace():
            piece = text[start:i].strip()
            if piece:
                out.append(piece)
                start = i
                words_since = 0
        i += 1
    return out, text[start:].strip()


class ClauseStream:
    """Incremental splitter over a reply that is still generating.

    Fed the FULL accumulated text on every delta (which is what the generation
    layer produces), it returns only the clauses not yet handed out. Holding the
    remainder is what stops a clause being spoken and then revised.
    """

    def __init__(self) -> None:
        self._emitted: list[str] = []
        self._consumed = 0        # characters of cleaned text already emitted

    @property
    def count(self) -> int:
        return len(self._emitted)

    def push(self, full_text: str) -> list[str]:
        """New complete clauses from the reply so far."""
        cleaned = clean_for_speech(full_text)
        if len(cleaned) < self._consumed:
            return []                             # a shrinking reply: ignore
        pending = cleaned[self._consumed:]
        clauses, _rest = split_clauses(pending, first=not self._emitted)
        if not clauses:
            return []
        used = sum(len(c) for c in clauses)
        # Walk forward over the original slice to find where the last emitted
        # clause actually ends, so whitespace does not drift the offset.
        idx = 0
        for c in clauses:
            found = pending.find(c, idx)
            idx = (found + len(c)) if found >= 0 else (idx + len(c))
        self._consumed += max(idx, used)
        self._emitted.extend(clauses)
        return clauses

    def flush(self, full_text: str) -> list[str]:
        """Everything left when generation finishes, including a bare fragment.

        This is the path the old code did not have: a reply short enough to be
        one clause used to arrive here having spoken nothing at all.
        """
        out = self.push(full_text)
        cleaned = clean_for_speech(full_text)
        rest = cleaned[self._consumed:].strip()
        if rest:
            self._consumed = len(cleaned)
            self._emitted.append(rest)
            out = out + [rest]
        return out
