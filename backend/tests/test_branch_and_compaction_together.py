"""Compaction is per LINE, and moving between lines carries each line's own.

The operator's questions, 2026-08-14, after the fork-across-a-fold fix landed:

  * if we compact from a branch, does that make its own compaction?
  * if we go back to the old line that WAS compacted, do we get the compaction
    back?
  * does any of this leak between the two?

All three fall out of one decision, which is what these pin: a compaction is a
`boundary` ROW carrying its own summary, so it belongs to the line it was made
on exactly the way a message does. Nothing about a fold is thread-global any
more, so there is nothing left to leak.

WHY WE CAN DO THIS AT ALL. The transcript is ours: an append-only local log
(`chat_log`), and nothing in it is ever deleted, including by a fold. Our
/compact does not ask the CLI to compact anything (it could, as of the
2026-08-27 measurement in compact_thread's docstring, but it does not: whether
to hand the operation to the CLI is an open call, not something these tests
assume either way). It summarizes OUR copy, records a boundary, and
drops the live session so the next turn re-sends the folded transcript. So going
back past a fold is not undoing something in someone else's process, it is
declining to apply a row we still hold.

These drive the branch filter directly, like test_chat_branch_prompt.py: what is
under test is which boundary `_fold` honours, not the Postgres query that decides
which rows are on a line.
"""
import pytest

from services import chat_branches, chat_log, chat_store, ownership


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield
    finally:
        ownership.clear_current_owner()


def _boundary_ids(tid):
    return [e["id"] for e in chat_log.read_events(tid) if e.get("kind") == "boundary"]


def _on_line(monkeypatch, hidden: set):
    """Pretend the current branch hides exactly these events."""
    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: hidden)


def _doc(tid):
    t = chat_store.get_thread(tid)
    assert t is not None
    return t


@pytest.fixture()
def two_folds(owned, monkeypatch):
    """Six messages, compacted once; then a fork behind that fold, compacted
    again. Two boundaries on two different lines, each with its own summary."""
    tid = chat_store.create_thread("general")["id"]
    for i in range(6):
        chat_store.append_message(tid, "user" if i % 2 == 0 else "assistant", f"m{i}")

    _on_line(monkeypatch, set())
    chat_store.set_summary(tid, "SUMMARY-A", folded_through=4)
    first = _boundary_ids(tid)[0]

    # Go back to before the fold. On this line the first boundary does not exist.
    _on_line(monkeypatch, {first})
    chat_store.set_summary(tid, "SUMMARY-B", folded_through=2)
    second = [b for b in _boundary_ids(tid) if b != first][0]
    return tid, first, second


def test_compacting_after_going_back_makes_its_own_fold(two_folds, monkeypatch):
    """Not a new thread, and not a second fold stacked on the first: one more
    boundary row, on the line the operator is actually on."""
    tid, first, second = two_folds
    assert len(_boundary_ids(tid)) == 2

    _on_line(monkeypatch, {first})
    t = _doc(tid)
    assert t["cleared_through"] == 2
    assert t["cut_mode"] == "compact"
    assert t["summary"] == "SUMMARY-B"


def test_going_back_to_the_line_that_was_compacted_restores_its_compaction(
        two_folds, monkeypatch):
    """The operator's "do we then kind of restore the compaction". Yes, and
    without a restore step: the boundary is on that line, so being on that line
    IS the fold being in force."""
    tid, first, second = two_folds

    _on_line(monkeypatch, {second})
    t = _doc(tid)
    assert t["cleared_through"] == 4
    assert t["cut_mode"] == "compact"
    assert t["summary"] == "SUMMARY-A", "the other line's summary came back with it"


def test_neither_fold_leaks_into_the_other(two_folds, monkeypatch):
    """The failure this design exists to prevent. `meta.summary` holds whichever
    fold was written LAST, so a thread-global summary would hand SUMMARY-B to
    both lines."""
    tid, first, second = two_folds
    assert (chat_log.read_meta(tid) or {}).get("summary") == "SUMMARY-B"

    _on_line(monkeypatch, {second})
    assert _doc(tid)["summary"] == "SUMMARY-A"

    _on_line(monkeypatch, {first})
    assert _doc(tid)["summary"] == "SUMMARY-B"


def test_a_line_with_no_fold_on_it_has_no_fold(two_folds, monkeypatch):
    """Going back past BOTH folds is the full conversation, verbatim, with no
    summary at all."""
    tid, first, second = two_folds

    _on_line(monkeypatch, {first, second})
    t = _doc(tid)
    assert "cleared_through" not in t
    assert "summary" not in t
    assert [m["content"] for m in t["messages"]] == [f"m{i}" for i in range(6)]


def test_the_cached_cut_follows_the_line(two_folds, monkeypatch):
    """The divider and the prompt must answer about the same conversation.

    `cut_state` serves the sync endpoint from `meta` alone and never reads the
    log, which is deliberate (it runs on every poll from every open tab) and
    means it is the one piece of cut state that is not branch-aware on its own.
    The fork and switch routes re-resolve it, so the cache is a cache and not a
    second opinion."""
    tid, first, second = two_folds

    _on_line(monkeypatch, {first, second})
    chat_store.resolve_cut(tid)
    assert chat_store.cut_state(tid) == {"cleared_through": 0, "cut_mode": ""}

    _on_line(monkeypatch, {second})
    chat_store.resolve_cut(tid)
    assert chat_store.cut_state(tid) == {"cleared_through": 4, "cut_mode": "compact"}

    _on_line(monkeypatch, {first})
    chat_store.resolve_cut(tid)
    assert chat_store.cut_state(tid) == {"cleared_through": 2, "cut_mode": "compact"}
