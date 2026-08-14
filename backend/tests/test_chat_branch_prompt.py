"""After going back to an earlier point, the MODEL reads the same conversation
the operator does.

THE BUG THIS PINS, found 2026-08-14 while reviewing the branch control: the tree
knows which messages are on the current line, and the sync endpoint has filtered
the screen by it since branching shipped (`chat_branches.keep_visible`). The
prompt builder never asked. `chat_store._fold` replayed the whole log file, so
after a fork the operator saw the line he had gone back to and the model was
still being handed everything he had branched away from, including the exchange
he went back specifically to get rid of.

`chat_branches.hidden_event_ids_sync` was written for this caller. Its docstring
names `chat_store._fold`. It had never been called by anything in production.

These tests drive the filter directly rather than through Postgres: what broke
was the WIRING, not the query, and the query has its own coverage in
test_db_chat.py. The point here is that `_fold` asks at all, and that "no answer"
still means "show everything".
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


@pytest.fixture()
def branched(owned):
    """A thread whose last two messages are on a line that was abandoned."""
    tid = chat_store.create_thread("general")["id"]
    chat_store.append_message(tid, "user", "KEEP what is the plan")
    chat_store.append_message(tid, "assistant", "KEEP here it is")
    chat_store.append_message(tid, "user", "DROP no, do it the other way")
    chat_store.append_message(tid, "assistant", "DROP fine, the other way")
    ids = [ev["id"] for ev in chat_log.read_events(tid)
           if ev.get("kind") == "message" and "DROP" in (ev.get("content") or "")]
    assert len(ids) == 2
    return tid, set(ids)


def _texts(tid):
    folded = chat_store._fold(tid)
    assert folded is not None
    return [m["content"] for m in folded[1]]


def test_the_fold_drops_the_line_that_was_left(branched, monkeypatch):
    tid, hidden = branched
    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: hidden)
    got = _texts(tid)
    assert any("KEEP what is the plan" in t for t in got)
    assert not any("DROP" in t for t in got)


def test_no_answer_means_show_everything(branched, monkeypatch):
    """A thread that was never branched, and a failure to ask, are the same
    answer on purpose: the file's own order is the whole truth."""
    tid, _ = branched
    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: None)
    assert len(_texts(tid)) == 4


def test_an_empty_hidden_set_hides_nothing(branched, monkeypatch):
    tid, _ = branched
    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: set())
    assert len(_texts(tid)) == 4


def test_a_clear_on_the_abandoned_line_does_not_cut_this_one(owned, monkeypatch):
    """A boundary is an event too, and it belongs to the line it was made on.

    Leaving boundaries unfiltered would let a /clear performed on a line the
    operator walked away from keep cutting the line he went back to, which reads
    as history vanishing for no reason he can see.
    """
    tid = chat_store.create_thread("general")["id"]
    chat_store.append_message(tid, "user", "first")
    chat_store.append_message(tid, "assistant", "second")
    chat_store.clear_thread_context(tid)
    chat_store.append_message(tid, "user", "after the clear")

    boundaries = [ev["id"] for ev in chat_log.read_events(tid)
                  if ev.get("kind") == "boundary"]
    assert boundaries, "the clear should have written a boundary"

    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: None)
    with_cut = chat_store._fold(tid)
    assert with_cut is not None and with_cut[3] > 0, "the clear cuts by default"

    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: set(boundaries))
    without = chat_store._fold(tid)
    assert without is not None
    assert without[3] == 0, "a boundary off the current line must not cut it"


def test_the_filter_is_asked_once_per_fold(branched, monkeypatch):
    """It is a database round trip on a path that also runs per-thread in a
    listing, so it must not creep into the event loop."""
    calls = []
    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync",
                        lambda t: (calls.append(t), None)[1])
    _texts(branched[0])
    assert calls == [branched[0]]
