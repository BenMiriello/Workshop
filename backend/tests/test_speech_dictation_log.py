"""The platform's last day of dictations (services/speech/dictation_log.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.speech import dictation_log

PIECE = {"place": {"kind": "field", "name": "Notes", "path": "/n"}, "text": "hello", "state": "placed",
         "at": "2026-11-04T06:00:00+00:00"}


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(dictation_log, "store_path", lambda: tmp_path / "dictations.json")


def test_a_saved_dictation_is_listed_newest_first():
    dictation_log.save("a" * 16, "2026-11-04T06:00:00+00:00", [PIECE])
    dictation_log.save("b" * 16, "2026-11-04T06:01:00+00:00", [PIECE])
    assert [e["id"] for e in dictation_log.recent()] == ["b" * 16, "a" * 16]


def test_a_late_live_save_never_reopens_an_ended_dictation():
    dictation_log.save("a" * 16, "s", [PIECE], ended_at="e", status="done")
    dictation_log.save("a" * 16, "s", [])
    [e] = dictation_log.recent()
    assert e["status"] == "done" and e["pieces"] == [PIECE]


def test_a_live_dictation_no_longer_saved_reads_as_interrupted(monkeypatch):
    dictation_log.save("a" * 16, "s", [PIECE])
    later = datetime.now(timezone.utc) + dictation_log.STALE + timedelta(seconds=5)
    monkeypatch.setattr(dictation_log, "_now", lambda: later)
    assert dictation_log.recent()[0]["status"] == "interrupted"


def test_only_the_last_day_is_kept(monkeypatch):
    dictation_log.save("a" * 16, "s", [PIECE])
    later = datetime.now(timezone.utc) + dictation_log.KEEP + timedelta(minutes=1)
    monkeypatch.setattr(dictation_log, "_now", lambda: later)
    assert dictation_log.recent() == []
