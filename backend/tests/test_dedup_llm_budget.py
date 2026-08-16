"""The same-work confirmation's wall time belongs to whoever is waiting on it.

`dedup_add` runs inside the request. Measured on this box 2026-08-15: the local
confirmation call takes ~2.0s with the model resident and 9-11s when it has to
be loaded, which is every filing after an idle stretch. That is dead time on the
File ticket button, and it is what the operator reported.

So the budget is a caller's argument, and a timeout is not a failure: it is the
documented downgrade to a `related` link, which still files the ticket.
"""
from __future__ import annotations

import urllib.request

import pytest

from services import task_dedup as td


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def seen(monkeypatch):
    """Capture the timeout the confirmation call actually asks urllib for."""
    calls: list[float] = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        return _FakeResponse(b'{"response": "YES"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(td, "_DEDUP_LLM_MODEL", "test-model")
    return calls


def test_a_caller_that_names_a_budget_gets_it(seen):
    assert td.same_work_llm("a", "b", timeout_s=6.0) is True
    assert seen == [6.0]


def test_a_caller_that_names_none_gets_the_unattended_budget(seen):
    """A sweep, a triager and an import all wait: nobody is watching, and a
    false merge silently drops a unit of work."""
    assert td.same_work_llm("a", "b") is True
    assert seen == [td._LLM_TIMEOUT_S]


def test_the_interactive_budget_clears_a_warm_call_and_not_a_cold_load(seen):
    """The number is chosen from measurement, so the measurement is the test:
    warm is ~2.0s and a cold load is 9s and up."""
    assert 2.0 < td.INTERACTIVE_LLM_TIMEOUT_S < 9.0


def test_decide_hands_its_budget_to_the_confirmation(monkeypatch):
    monkeypatch.setattr(td, "embed_texts", lambda texts: [[1.0, 0.0]] * len(texts))
    got: list[float | None] = []

    def fake_confirm(a, b, timeout_s=None):
        got.append(timeout_s)
        return True

    monkeypatch.setattr(td, "same_work_llm", fake_confirm)
    # Different wording, near-identical meaning: the hash-exact path must not
    # short-circuit before the cosine tier the confirmation gates.
    d = td.decide("file a ticket from the chat card",
                  [{"id": "t1", "text": "file a ticket from the card in chat"}],
                  llm_timeout_s=6.0)
    assert got == [6.0]
    assert d["decision"] == "merge"


def test_a_confirmation_that_times_out_still_files_the_ticket(monkeypatch):
    """The whole reason a short budget is safe: an unconfirmed match is linked,
    not merged and not dropped."""
    monkeypatch.setattr(td, "embed_texts", lambda texts: [[1.0, 0.0]] * len(texts))
    monkeypatch.setattr(td, "same_work_llm",
                        lambda a, b, timeout_s=None: None)  # what a timeout returns
    d = td.decide("file a ticket from the chat card",
                  [{"id": "t1", "text": "file a ticket from the card in chat"}],
                  llm_timeout_s=6.0)
    assert d["decision"] == "related"
    assert d["stage"] == "cosine+llm-downgrade"
    assert d["matched_id"] == "t1"


def test_a_timeout_reaches_the_caller_as_cannot_confirm(monkeypatch):
    """urlopen raising is the real timeout path, and it must not propagate: the
    filing has to survive an unreachable or slow model."""
    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(td, "_DEDUP_LLM_MODEL", "test-model")
    assert td.same_work_llm("a", "b", timeout_s=0.01) is None
