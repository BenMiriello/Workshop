"""When speech may start its worker on the card.

A job that renders in bursts drops its claim between renders, for a few
seconds at a time. Taken at every free moment, the card would be borrowed and
handed back again and again: each start loads the speech models onto it, each
eviction stops the worker, and a read caught in the hand-off is done twice.
So speech starts its card worker only once the card has been seen free for
SETTLE_S straight, and only SETTLE_S after it last started there, which keeps
starts (and so evictions) to about one a minute. An eviction itself is never
delayed: the job has the card by right.

The card is seen through the guest claim speech makes before each piece and
while its card worker runs; a gap longer than SEEN_GAP_S between two looks
says nothing about the time between them, so being free is counted again from
the later one.
"""
from __future__ import annotations

import threading
import time

SETTLE_S = 60.0
SEEN_GAP_S = 5.0


class CardTurns:
    def __init__(self, settle_s: float = SETTLE_S) -> None:
        self.settle_s = settle_s
        self._free_since: float | None = None
        self._seen_at = 0.0
        self._started_at: float | None = None
        self._lock = threading.Lock()

    def seen(self, free: bool, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if not free:
                self._free_since = None
            elif self._free_since is None or now - self._seen_at > SEEN_GAP_S:
                self._free_since = now
            self._seen_at = now

    def may_start(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            settled = self._free_since is not None and now - self._free_since >= self.settle_s
            rested = self._started_at is None or now - self._started_at >= self.settle_s
            return settled and rested

    def started(self, now: float | None = None) -> None:
        with self._lock:
            self._started_at = time.monotonic() if now is None else now
