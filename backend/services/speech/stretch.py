"""Where the speech is in a stretch of microphone audio, and that audio with
its long pauses shortened for reading.

Live dictation re-reads everything said since the last settled point, pauses
included, so the model hears a sentence that was interrupted as one sentence.
Silence carries nothing for it, so each pause is cut down to GAP_KEEP_MS
before the read (half kept on each side of it), and a map turns a time in the
shortened audio back into a sample of the real stream.
"""
from __future__ import annotations

import numpy as np

from services.voice.timeline import SAMPLE_RATE

GAP_KEEP_MS = 600


def samples(ms: float) -> int:
    return int(ms * SAMPLE_RATE / 1000)


class SpeechRuns:
    """The runs of speech in the stream, as merged [start, end) samples."""

    def __init__(self) -> None:
        self.runs: list[list[int]] = []

    def add(self, start: int, end: int) -> None:
        if self.runs and start <= self.runs[-1][1]:
            self.runs[-1][1] = max(self.runs[-1][1], end)
        else:
            self.runs.append([start, end])

    def drop_before(self, at: int) -> None:
        self.runs = [[max(s, at), e] for s, e in self.runs if e > at]

    def total(self, start: int) -> int:
        return sum(e - max(s, start) for s, e in self.runs if e > start)

    @property
    def last_end(self) -> int:
        return self.runs[-1][1] if self.runs else 0

    def keep(self, start: int, end: int) -> list[tuple[int, int]]:
        """The parts of [start, end) to read: each run with half a kept gap
        either side, merged where they meet."""
        pad = samples(GAP_KEEP_MS) // 2
        out: list[list[int]] = []
        for s, e in self.runs:
            a, b = max(start, s - pad), min(end, e + pad)
            if a >= b:
                continue
            if out and a <= out[-1][1]:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [(a, b) for a, b in out]


class Shortened:
    """Audio made of kept parts, and the way back to stream samples."""

    def __init__(self, parts: list[tuple[int, np.ndarray]]) -> None:
        self._map: list[tuple[int, int, int]] = []     # (at, stream start, length)
        at = 0
        for start, pcm in parts:
            self._map.append((at, start, pcm.shape[0]))
            at += pcm.shape[0]
        self.pcm = (np.concatenate([p for _, p in parts]) if parts
                    else np.zeros(0, dtype=np.int16))

    @property
    def ms(self) -> int:
        return int(self.pcm.shape[0] * 1000 / SAMPLE_RATE)

    def stream_at(self, seconds: float) -> int:
        """The stream sample `seconds` into the shortened audio."""
        at = int(seconds * SAMPLE_RATE)
        for i, (a, start, n) in enumerate(self._map):
            if at < a + n or i == len(self._map) - 1:
                return start + max(0, min(n, at - a))
        return 0
