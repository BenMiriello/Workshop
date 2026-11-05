"""The microphone audio of a live dictation, and where the speech is in it.

Frames arrive sample-addressed (services/voice/protocol.py) into a timeline;
each new stretch of audio is run through the voice detector, whose runs of
speech say where words are and how long the pauses between them really were
(services/speech/stretch.py). services/speech/live.py builds on this.
"""
from __future__ import annotations

import time

import numpy as np

from services.speech.stretch import Shortened, SpeechRuns
from services.voice.detectors import VAD_WINDOW, SpeechDetector
from services.voice.timeline import AudioTimeline, RangeExpired, Span


class Listening:
    def __init__(self, start: int, detector: SpeechDetector | None) -> None:
        self.timeline = AudioTimeline()
        self.vad = detector or SpeechDetector()
        self.speech = SpeechRuns()
        self.open_from = start
        self._analysed_to = start
        self._carry = np.zeros(0, dtype=np.float32)
        self.asked_to = start                   # where the last read asked for ends
        self.fresh_at: float | None = None      # when speech past it first came in

    def write(self, offset: int, pcm: np.ndarray) -> None:
        if offset + pcm.shape[0] <= self.open_from:
            return
        self.timeline.write_at(offset, pcm)
        self._analyse()
        if self.fresh_at is None and self.speech.last_end > self.asked_to:
            self.fresh_at = time.monotonic()

    def _analyse(self) -> None:
        end = self.timeline.end
        if end <= self._analysed_to:
            return
        try:
            fresh = self.timeline.read_float(Span(max(self._analysed_to, self.timeline.start), end))
        except RangeExpired:
            fresh = np.zeros(0, dtype=np.float32)
        self._analysed_to = end
        buf = np.concatenate([self._carry, fresh]) if self._carry.size else fresh
        n = buf.shape[0] // VAD_WINDOW
        self._carry = buf[n * VAD_WINDOW:]
        base = end - buf.shape[0]
        for w in range(n):
            if self.vad(buf[w * VAD_WINDOW:(w + 1) * VAD_WINDOW]).speech:
                at = base + w * VAD_WINDOW
                self.speech.add(at, at + VAD_WINDOW)

    def _shortened(self, start: int, end: int) -> Shortened:
        start = max(start, self.timeline.start)
        return Shortened([(a, self.timeline.read(Span(a, b)))
                          for a, b in self.speech.keep(start, end)])
