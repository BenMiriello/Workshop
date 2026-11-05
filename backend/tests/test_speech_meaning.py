"""Understanding a command worded freely (services/speech/meaning.py): what each
command is learned from in each mode, and what a guess says. The embedding
model is stood in for by word-hashing vectors, so these test the classifier
and its bookkeeping, not the model."""
from __future__ import annotations

import threading

import numpy as np
import pytest

from services.speech import command_store
from services.speech.commands import Table
from services.speech.meaning import Understander, examples_for


class Words:
    """Each word a fixed random direction; a text, their sum."""
    ready = True

    def load(self) -> None:
        pass

    def embed(self, texts: list[str]) -> np.ndarray:
        out = []
        for t in texts:
            v = np.zeros(64, np.float32)
            for w in t.lower().split():
                v += np.random.default_rng(abs(hash(w)) % 2**32).standard_normal(64).astype(np.float32)
            out.append(v / max(np.linalg.norm(v), 1e-9))
        return np.stack(out)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(command_store, "store_path", lambda: tmp_path / "commands.json")


def table(mode: str) -> Table:
    return Table.from_commands(command_store.current(), mode)


def learned(u: Understander, t: Table) -> Understander:
    """`t`'s commands learned, as entering a mode that guesses does."""
    u.warm(t)
    for th in threading.enumerate():
        if th.name == "speech-meaning":
            th.join(5)
    return u


def test_learned_from_only_what_the_mode_hears():
    command_store.save([{"id": "send", "phrases": ["send message"], "when": "off"}],
                       [{"id": "sig", "phrases": ["sign it"], "text": "Best, me"}])
    every = examples_for(table("commands"))
    assert "send" not in every and "new_line" not in every and "resume" not in every
    assert "scroll_down" in every and "type:sig" in every and "none" in every
    assert "scroll down" in every["scroll_down"] and "scroll down a bit" in every["scroll_down"]
    assert set(examples_for(table("dictation"))) <= {
        "dictation_only", "mixed_mode", "commands_only", "start_writing", "pause", "stop", "none"}


def test_a_guess_ranks_commands_and_says_how_likely_none_is():
    command_store.save([], [{"id": "sig", "phrases": ["sign it off", "add my signature"],
                             "text": "Best, me"}])
    t = table("commands")
    u = learned(Understander(Words()), t)  # type: ignore[arg-type]
    g = u.guess("scroll down a bit", t)
    assert g["guesses"][0]["name"] == "scroll_down"
    assert 0 <= g["none"] <= 1 and len(g["guesses"]) == 5
    assert sum(x["p"] for x in g["guesses"]) + g["none"] <= 1.0001
    sig = u.guess("add my signature", t)["guesses"][0]
    assert sig == {"name": "type", "p": sig["p"], "text": "Best, me"}


def test_a_whole_set_of_commands_is_learned_in_the_background_never_in_a_guess():
    u = Understander(Words())  # type: ignore[arg-type]
    t = table("commands")
    assert u.guess("scroll down a bit", t) is None
    for th in threading.enumerate():
        if th.name == "speech-meaning":
            th.join(5)
    assert u.guess("scroll down a bit", t)["guesses"][0]["name"] == "scroll_down"


def test_no_guess_until_the_model_is_loaded():
    class NotYet(Words):
        ready = False
    assert Understander(NotYet()).guess("scroll down", table("commands")) is None  # type: ignore[arg-type]
