"""Ranking the controls on screen by what a spoken click means: the cache stays
bounded, kind words leave the query, and the route carries the thresholds."""
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import speech
from services.speech import ui_match


class FakeEmbedder:
    """One axis per word, so a text is near another sharing its words."""
    ready = True

    def __init__(self):
        self.embedded: list[str] = []

    def load(self):
        pass

    def embed(self, texts, template):
        self.embedded.extend(texts)
        return np.stack([_vec(t) for t in texts])


def _vec(text: str) -> np.ndarray:
    v = np.zeros(64, np.float32)
    for w in text.lower().replace(",", " ").split():
        v[hash(w) % 64] += 1
    return v / max(np.linalg.norm(v), 1e-9)


@pytest.fixture
def matcher():
    e = FakeEmbedder()
    return ui_match.Matcher(e), e


def test_scores_follow_the_items_and_rank_by_shared_words(matcher):
    m, _ = matcher
    s = m.scores("the trash can", ["Delete, button, trash icon", "Rename, button, pencil icon"])
    assert len(s) == 2 and s[0] > s[1]


def test_descriptions_are_embedded_once(matcher):
    m, e = matcher
    m.scores("gear", ["Settings, button", "Help, button"])
    m.scores("help", ["Settings, button", "Help, button"])
    assert e.embedded.count("Settings, button") == 1


def test_the_cache_is_bounded(matcher, monkeypatch):
    m, _ = matcher
    monkeypatch.setattr(ui_match, "CACHE_MAX", 3)
    m.scores("x", ["a", "b", "c", "d", "e"])
    assert len(m._vecs) == 3
    m.scores("x", ["a"])
    assert "a" in m._vecs and len(m._vecs) == 3


def test_kind_words_leave_the_query():
    assert ui_match.clean("the export button") == "export"
    assert ui_match.clean("the button") == "the button"


def test_no_model_means_no_scores():
    class Absent(FakeEmbedder):
        ready = False
    assert ui_match.Matcher(Absent()).scores("gear", ["Settings"]) is None


def test_route_returns_scores_and_thresholds(monkeypatch):
    monkeypatch.setattr(ui_match.shared, "scores", lambda q, items: [0.5] * len(items))
    app = FastAPI()
    app.include_router(speech.router)
    body = TestClient(app).post("/speech/match", json={"query": "gear", "items": ["a", "b"]}).json()
    assert body == {"scores": [0.5, 0.5], "act": ui_match.ACT, "margin": ui_match.MARGIN, "ask": ui_match.ASK}
