"""Live dictation lag reaches the journal: each read measured, slow ones
written at once, a summary when the dictation ends."""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from services.speech import lag
from services.speech.heard import Heard
from services.speech.live import LiveDictation
from services.voice.detectors import SpeechFrame


class LoudIsSpeech:
    tier = "test"

    def __call__(self, frame: np.ndarray) -> SpeechFrame:
        loud = bool(np.abs(frame).mean() > 0.05)
        return SpeechFrame(loud, float(loud))


def test_speech_info_lines_have_a_handler_of_their_own():
    log = logging.getLogger("workshop.speech")
    assert log.getEffectiveLevel() <= logging.INFO
    assert any(getattr(h, "speech_journal", False) for h in log.handlers)


def test_a_read_records_stretch_wait_time_and_device():
    def transcribe(pcm):
        lag.note("cpu", 5.0)
        time.sleep(0.02)
        return Heard("one")

    s = LiveDictation(transcribe, detector=LoudIsSpeech())
    s.write(0, np.full(16000, 8000, dtype=np.int16))
    time.sleep(0.05)

    async def emit(_):
        pass

    asyncio.run(s.tick(emit))
    ((stretch, wait, took, device),) = s.lag.reads
    assert 0.9 <= stretch <= 1.4
    assert wait >= 50 and 15 <= took < 500 and device == "cpu"


def test_slow_reads_are_written_at_once_and_the_rest_summed_up(caplog):
    caplog.set_level(logging.INFO, logger="workshop.speech")
    m = lag.Lag()
    m.read(8.0, 20, 300, "cpu")
    assert not caplog.records
    m.read(20.0, 40, 900, "cpu")
    assert "read 900ms on cpu" in caplog.records[-1].getMessage()
    m.summary()
    line = caplog.records[-1].getMessage()
    assert "2 reads (0 on the card)" in line and "max 900ms" in line
