"""A turn adopted after a restart resumes its journal instead of replaying it.

The drive loop started every turn at `offset = 0`, including one it had just
adopted from a previous process. So a restart mid-reply re-read the entire
journal, rebuilt the assistant message's segments from scratch, and persisted
that half-rebuilt version over the stored one. From the chat it looks like the
conversation spontaneously rewinds while you are reading it. It also dropped
`cut_head`, the anchors placing messages the operator sent mid-turn, which live
only in memory (ADR 0018) -- so those messages lost their position in the reply.

The draft now carries the journal offset its segments were built from, written
in the same call as the segments so the pair can never disagree.

THE SAFETY PROPERTY UNDER TEST is the fallback: every uncertainty must resolve
to 0, i.e. to the old full replay. Replaying is ugly; resuming from a wrong
offset silently drops or duplicates part of the operator's reply.
"""
from __future__ import annotations

import pytest

from services import build_streaming as bs


@pytest.fixture
def journal(tmp_path, monkeypatch):
    d = tmp_path / "turn"
    d.mkdir()
    (d / "stream.jsonl").write_text("x" * 500)
    monkeypatch.setattr(bs, "_journal_dir", lambda tid: d)
    return d


def _turn():
    return bs.Turn(id="run-1", thread_id="th-1", agent_slug="general")


def _live(monkeypatch, payload):
    """Patch the real `chat_log.read_live`.

    Not `sys.modules["services.chat_log"]`: `from services import chat_log`
    reads the attribute off the already-imported package, so a sys.modules entry
    is never consulted and every fallback case would pass for the wrong reason
    (the real read_live returning None for a thread that does not exist).
    """
    from services import chat_log

    monkeypatch.setattr(chat_log, "read_live", lambda thread_id: payload)


SEGS = [{"t": "text", "text": "already rendered"}]


def test_resumes_from_the_recorded_offset(journal, monkeypatch):
    _live(monkeypatch, {"turn_id": "run-1", "journal_offset": 120,
                        "meta": {"segments": SEGS, "cut_head": ["m-7"]}})
    t = _turn()
    assert bs._resume_from(t) == 120
    # The offset is only correct relative to these, so they come back with it.
    assert t.segments == SEGS
    assert t.cut_head == ["m-7"]


def test_a_draft_for_a_different_run_is_ignored(journal, monkeypatch):
    """Another run has taken the thread over; its offset means nothing here."""
    _live(monkeypatch, {"turn_id": "run-2", "journal_offset": 120,
                        "meta": {"segments": SEGS}})
    t = _turn()
    assert bs._resume_from(t) == 0
    assert t.segments == []


def test_an_offset_past_the_end_of_the_journal_replays(journal, monkeypatch):
    """Not the file we indexed (truncated, or a recycled turn id). Start over
    rather than seek past the content."""
    _live(monkeypatch, {"turn_id": "run-1", "journal_offset": 99_999,
                        "meta": {"segments": SEGS}})
    assert bs._resume_from(_turn()) == 0


def test_an_older_draft_without_an_offset_replays(journal, monkeypatch):
    """Written before offsets were recorded: the segments are real but we do not
    know where they stop, which is exactly the case that must not guess."""
    _live(monkeypatch, {"turn_id": "run-1", "meta": {"segments": SEGS}})
    assert bs._resume_from(_turn()) == 0


def test_an_offset_with_no_segments_replays(journal, monkeypatch):
    """Resuming here would skip 120 bytes of reply that nothing had rendered."""
    _live(monkeypatch, {"turn_id": "run-1", "journal_offset": 120,
                        "meta": {"segments": []}})
    assert bs._resume_from(_turn()) == 0


def test_no_draft_at_all_replays(journal, monkeypatch):
    _live(monkeypatch, None)
    assert bs._resume_from(_turn()) == 0


def test_an_unreadable_draft_replays(journal, monkeypatch):
    from services import chat_log

    def boom(thread_id):
        raise RuntimeError("draft unreadable")

    monkeypatch.setattr(chat_log, "read_live", boom)
    assert bs._resume_from(_turn()) == 0


def test_a_missing_journal_replays(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "_journal_dir", lambda tid: tmp_path / "gone")
    _live(monkeypatch, {"turn_id": "run-1", "journal_offset": 120,
                        "meta": {"segments": SEGS}})
    assert bs._resume_from(_turn()) == 0


# --- a finished turn's journal is terminal on re-attach -----------------------
# The resume-past-result hole: a turn that wrote its `result` before a restart is
# adopted with the offset at end-of-file, so the tail loop never re-reads the
# result, `saw_result` never flips, and the idle process (stdin still open) never
# dies -- the turn spins "running" forever over a reply already on disk.
# `_journal_terminal_result` is how the driver recognises that and commits.
# docs/platform-redesign/turn-recovery-terminal-journal-2026-08-17.md.
import json as _json


def _write_journal(d, *events):
    d.joinpath("stream.jsonl").write_text(
        "".join(_json.dumps(e) + "\n" for e in events))


def test_terminal_result_detected(journal):
    _write_journal(journal,
                   {"type": "assistant", "message": {"content": []}},
                   {"type": "result", "subtype": "success",
                    "usage": {"input_tokens": 10, "output_tokens": 5}})
    term = bs._journal_terminal_result("run-1")
    assert term is not None and term.get("subtype") == "success"


def test_mid_answer_journal_is_not_terminal(journal):
    # A turn working on an injected second prompt: its first result is NOT the
    # last event, so the tail is not a result and the check does not fire.
    _write_journal(journal,
                   {"type": "result", "subtype": "success"},
                   {"type": "assistant", "message": {"content": []}})
    assert bs._journal_terminal_result("run-1") is None


def test_empty_journal_is_not_terminal(journal):
    journal.joinpath("stream.jsonl").write_text("")
    assert bs._journal_terminal_result("run-1") is None


def test_unparseable_tail_is_not_terminal(journal):
    journal.joinpath("stream.jsonl").write_text("{not json\n")
    assert bs._journal_terminal_result("run-1") is None


def test_missing_journal_is_not_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "_journal_dir", lambda tid: tmp_path / "gone")
    assert bs._journal_terminal_result("run-1") is None


def test_trailing_blank_lines_ignored(journal):
    journal.joinpath("stream.jsonl").write_text(
        _json.dumps({"type": "result", "subtype": "success"}) + "\n\n\n")
    assert bs._journal_terminal_result("run-1") is not None
