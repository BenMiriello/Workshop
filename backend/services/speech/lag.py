"""How long live dictation keeps a word waiting, written where it can be read.

The backend sets up no root log handler, so only warnings reach the service
journal (through Python's last-resort handler) and an INFO line from a speech
logger is dropped. The `workshop.speech` logger gets its own stderr handler at
INFO, which is what the journal collects.

Per live dictation: every read's stretch length, how long the newest speech
waited for the read to start (`wait`, the worker queue included), how long the
read took and where it ran. A read slower than SLOW_MS is written at once; the
rest go into one summary line when the dictation ends.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable, TypeVar

SLOW_MS = 800
T = TypeVar("T")
R = TypeVar("R")

log = logging.getLogger("workshop.speech")
if not any(getattr(h, "speech_journal", False) for h in log.handlers):
    _h = logging.StreamHandler(sys.stderr)
    _h.speech_journal = True  # type: ignore[attr-defined]
    _h.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

_call = threading.local()


def note(device: str, queued_ms: float) -> None:
    """The worker call just made on this thread: where it ran, and how long it
    waited for the worker to be free."""
    _call.device, _call.queued_ms = device, queued_ms


def taken() -> tuple[str, float]:
    """The last worker call made on this thread, and forget it."""
    out = (getattr(_call, "device", "?"), getattr(_call, "queued_ms", 0.0))
    _call.device, _call.queued_ms = "?", 0.0
    return out


def timed(call: Callable[[T], R], arg: T) -> tuple[R, float, str, float]:
    """`call(arg)` on this thread: its result, how long the worker took on
    it, where it ran, and how long it waited for the worker."""
    taken()
    t = time.monotonic()
    out = call(arg)
    device, queued_ms = taken()
    return out, (time.monotonic() - t) * 1000 - queued_ms, device, queued_ms


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0.0


class Lag:
    def __init__(self) -> None:
        self.reads: list[tuple[float, float, float, str]] = []

    def read(self, stretch_s: float, wait_ms: float, read_ms: float, device: str) -> None:
        self.reads.append((stretch_s, wait_ms, read_ms, device))
        if read_ms > SLOW_MS or wait_ms > SLOW_MS:
            log.info("live read slow: stretch %.1fs wait %dms read %dms on %s",
                     stretch_s, wait_ms, read_ms, device)

    def summary(self) -> None:
        if not self.reads:
            return
        stretch = [r[0] for r in self.reads]
        wait = [r[1] for r in self.reads]
        took = [r[2] for r in self.reads]
        gpu = sum(r[3] == "cuda" for r in self.reads)
        log.info("live dictation: %d reads (%d on the card), stretch p50 %.1fs max %.1fs, "
                 "wait p50 %dms p95 %dms, read p50 %dms p95 %dms max %dms",
                 len(self.reads), gpu, _pct(stretch, .5), max(stretch), _pct(wait, .5),
                 _pct(wait, .95), _pct(took, .5), _pct(took, .95), max(took))
