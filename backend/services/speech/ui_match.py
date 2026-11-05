"""Which control on screen a spoken "click ..." means when its words are not
the control's name: "the gear", "the trash can", "log out" for Sign out.

The client describes every control it can see in one line (name, kind, icon,
title, the row it sits in, its section) and this ranks those lines against
what was said with the same EmbeddingGemma model the command classifier loads
(services/speech/meaning.py). Matching speech to controls is retrieval, not
classification, so it uses the model card's retrieval prompts: on 110 labelled
requests over ten walked Workshop pages the right control ranked first for 80
of 92 that were on screen, against 51 with the classification prompt.

The thresholds come from that set and travel with the scores, so the client
holds no number that belongs to this model. Measured: act when the best score
is at least ACT and leads the best differently described control by MARGIN
(48 right presses, 2 wrong, both for controls not on screen); offer choices
from ASK; below that, nothing fits. Held out on alternate halves, a margin of
0.05 pressed the wrong control 6 times in 55 and 0.065 none, hence 0.07.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np

from services.speech import meaning

QUERY = "task: search result | query: {}"
DOCUMENT = "title: none | text: {}"
ACT = 0.35
MARGIN = 0.07
ASK = 0.31
CACHE_MAX = 4000
# Words that say which kind of control, which the descriptions already say
# once each: left in, "the export button" ranks every short button high.
DROP = frozenset({"the", "a", "an", "button", "link", "icon", "tab", "please"})


def clean(query: str) -> str:
    words = [w for w in query.lower().split() if w not in DROP]
    return " ".join(words) or query


class Matcher:
    def __init__(self, embedder: meaning.Embedder) -> None:
        self.embedder = embedder
        self._vecs: OrderedDict[str, np.ndarray] = OrderedDict()
        self._lock = threading.Lock()

    def _documents(self, items: list[str]) -> np.ndarray:
        with self._lock:
            missing = [t for t in dict.fromkeys(items) if t not in self._vecs]
        if missing:
            fresh = self.embedder.embed(missing, DOCUMENT)
            with self._lock:
                for t, v in zip(missing, fresh):
                    self._vecs[t] = v
        with self._lock:
            for t in items:
                self._vecs.move_to_end(t)
            out = np.stack([self._vecs[t] for t in items])
            while len(self._vecs) > CACHE_MAX:
                self._vecs.popitem(last=False)
        return out

    def scores(self, query: str, items: list[str]) -> list[float] | None:
        """How well each item fits `query`, in the items' order; None when the
        model cannot be loaded."""
        self.embedder.load()
        if not self.embedder.ready:
            return None
        if not items:
            return []
        docs = self._documents(items)
        q = self.embedder.embed([clean(query)], QUERY)[0]
        return [round(float(s), 4) for s in docs @ q]


shared = Matcher(meaning.shared.embedder)
