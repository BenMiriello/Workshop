"""What the open stretch shows while it is still being said.

Each read of the stretch is the model's whole guess again, and from one read to
the next it moves small things all through it: a comma, a capital, "um" for
"uh". Shown as read, the whole stretch shivers. So a word is HELD once two reads
in a row agree on it and on the word after it (the next word is what settles a
word's punctuation) and it ended a moment before the latest audio, and held words are shown as they were held: later reads do
not rewrite them. Only the words after them, the last word or two being said,
still change, the way phone and desktop dictation behave.

A read is lined up with the held words by their text (case and punctuation
aside), the way a diff matches lines, so a read that changes, splits or merges
an earlier word does not shift or repeat the words after it.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from services.speech.heard import Heard, Word

# A word still being said can read the same twice by chance, so only a word
# that ended this long before the end of the audio read is held.
HOLD_AGE_S = 1.0
ENDS = (".", "?", "!")

def _key(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def _agree(a: tuple[str, ...], b: tuple[str, ...]) -> int:
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


class Steady:
    def __init__(self) -> None:
        self.held: list[Word] = []
        self._prev: tuple[str, ...] = ()
        self._shown: list[Word] = []
        self._offset = 0

    def reset(self) -> None:
        self.__init__()

    def _after_held(self, words: tuple[Word, ...]) -> int:
        """Where this read's words after the held ones begin: matched word by
        word (case and punctuation aside), so a word the read changed, split or
        merged neither shifts nor repeats the words after it."""
        n = len(self.held)
        if n == 0:
            return 0
        held = [_key(h.text) for h in self.held]
        read = [_key(w.text) for w in words]
        if read[:n] == held:
            return n
        blocks = [b for b in SequenceMatcher(None, held, read, autojunk=False).get_matching_blocks() if b.size]
        # A held word matched only by skipping words the read has before it is
        # a word the read changed, not those words: "sends the" held, read
        # "sends this one back to the end", is not matched at the later "the".
        while len(blocks) > 1:
            prev, last = blocks[-2], blocks[-1]
            # A word split in two (or merged) changes the count by one or so.
            if last.b - (prev.b + prev.size) <= 2 * (last.a - (prev.a + prev.size)) + 1:
                break
            blocks.pop()
        if not blocks:
            return min(n, len(words))
        last = blocks[-1]
        # Held words after the last match stand for as many words of the read.
        return min(len(words), last.b + last.size + (n - last.a - last.size))

    def _cased(self, read_last: Word, first: Word) -> Word:
        """The word after the held ones, in the case the held ones call for:
        the read may end a sentence where the held word does not, or not end
        one where it does."""
        held_ends = self.held[-1].text.endswith(ENDS)
        if held_ends == read_last.text.endswith(ENDS) or not first.text:
            return first
        t = first.text
        t = t[0].upper() + t[1:] if held_ends else (t[0].lower() + t[1:] if t != "I" and not t.startswith("I'") else t)
        return Word(t, first.start, first.last)

    def show(self, heard: Heard, hold: bool = True, until: float = float("inf")) -> str:
        """The text to show for this read of `until` seconds of audio. `hold`
        False shows it without holding more (a last read, at Stop)."""
        words = heard.words
        if not words:
            return " ".join(w.text for w in self.held) if self.held else heard.text
        k = self._after_held(words)
        tail = list(words[k:])
        if tail and self.held and 0 < k <= len(words):
            tail[0] = self._cased(words[k - 1], tail[0])
        texts = tuple(w.text for w in tail)
        if hold:
            agreed = max(0, _agree(texts, self._prev) - 1)
            lock = 0
            while lock < agreed and tail[lock].last <= until - HOLD_AGE_S:
                lock += 1
            self.held.extend(tail[:lock])
            tail = tail[lock:]
            k += lock
            self._prev = texts[lock:]
        self._shown = self.held + tail
        self._offset = len(self.held) - k
        return " ".join(w.text for w in self._shown)

    def text_before(self, k: int) -> str:
        """The text last shown without its last `k` words."""
        return " ".join(w.text for w in self._shown[:max(0, len(self._shown) - k)])

    def split(self, i: int) -> tuple[str, str]:
        """Settle the shown text through the word read as word `i` of the last
        read shown: (settled, rest). Held words after the cut stay held."""
        held = len(self.held)
        n = i + 1 + self._offset if i + 1 > held - self._offset else i + 1
        n = max(0, min(n, len(self._shown)))
        settled, rest = self._shown[:n], self._shown[n:]
        self.held = self.held[n:]
        if n > held:
            self._prev = self._prev[n - held:]
        self._shown = rest
        self._offset = 0
        return " ".join(w.text for w in settled), " ".join(w.text for w in rest)
