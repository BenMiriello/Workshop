"""Going back to before a /compact gives the earlier conversation back.

THE OPERATOR'S QUESTION, 2026-08-14: the branch control is offered on messages
above a compaction divider, so "does going back past a compact undo the compact,
or have we not handled that?"

We had not handled it, and the answer was worse than either option. The cut was
branch-scoped (it comes from a `boundary` event, which the tree filters like any
other row) while the SUMMARY was thread-global (`meta.summary`, one field for the
whole conversation). So forking to before a compaction dropped the cut and kept
the summary, and the prompt came out as:

    a summary of messages 1-14  +  messages 1-10, verbatim

which double-counts what it summarizes AND smuggles messages 11-14 back in
through the summary, on a line the operator had just branched away from. That is
the exact leak `_fold`'s branch filter was written to close, re-entering through
the other door.

The fix is that the summary belongs to the BOUNDARY, not to the thread: written
onto the event as `fold_summary`, resolved by `_fold` only when that boundary is
on the current line. "No boundary on this path" then means "no cut and no
summary" without a special case, which is the behaviour going back past a
compaction reads as having.

These drive the filter directly, like test_chat_branch_prompt.py, because what
is being pinned is the WIRING between the boundary and the summary.
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
def compacted(owned):
    """Six messages, then a compact folding the first four into a summary."""
    tid = chat_store.create_thread("general")["id"]
    for i in range(6):
        chat_store.append_message(tid, "user" if i % 2 == 0 else "assistant", f"m{i}")
    chat_store.set_summary(tid, "a summary of m0 through m3", folded_through=4)
    return tid


def _folded(tid):
    out = chat_store._fold(tid)
    assert out is not None
    return out


def _doc(tid):
    t = chat_store.get_thread(tid)
    assert t is not None
    return t


def test_the_summary_is_written_onto_the_boundary(compacted):
    """Not only into meta. The event is the copy a branch filter can reach."""
    bounds = [e for e in chat_log.read_events(compacted) if e.get("kind") == "boundary"]
    assert len(bounds) == 1
    assert bounds[0]["mode"] == "compact"
    assert bounds[0]["fold_summary"] == "a summary of m0 through m3"


def test_a_compaction_on_the_current_line_still_applies(compacted):
    """The ordinary case, unchanged: cut in force, summary in the document."""
    t = _doc(compacted)
    assert t["cleared_through"] == 4
    assert t["cut_mode"] == "compact"
    assert t["summary"] == "a summary of m0 through m3"


def test_going_back_past_a_compaction_restores_the_full_history(compacted, monkeypatch):
    """The operator's case. With the boundary off the path there is no cut AND
    no summary, so the prompt is the earlier conversation verbatim."""
    boundary_id = next(e["id"] for e in chat_log.read_events(compacted)
                       if e.get("kind") == "boundary")
    monkeypatch.setattr(chat_branches, "hidden_event_ids_sync", lambda _t: {boundary_id})

    _meta, msgs, _seqs, cut = _folded(compacted)
    assert cut == 0, "a boundary the operator branched away from still cut the thread"
    assert [m["content"] for m in msgs] == [f"m{i}" for i in range(6)]

    t = _doc(compacted)
    assert "cleared_through" not in t
    assert "summary" not in t, (
        "the summary of an abandoned line was still handed to the model, "
        "alongside the very messages it summarizes")


def test_the_retired_folder_s_own_summary_key_stays_inert(owned):
    """`fold_summary`, not `summary`, and this is why.

    The server-side folding removed on 2026-08-12 wrote its summary onto the
    boundary under `summary`. Those events are still on disk and are deliberately
    dead: that era's summarizer was failing, so honouring one hides messages
    behind a summary that was never written. Reusing the key would have revived
    every one of them silently. Pinned here as well as in test_clear_flow because
    the collision is invisible from either side alone."""
    tid = chat_store.create_thread("general")["id"]
    for i in range(6):
        chat_store.append_message(tid, "user", f"m{i}")
    chat_log.append_sync(tid, {"kind": "boundary", "mode": "compact",
                               "summary": "what the retired folder wrote"})
    t = _doc(tid)
    assert t.get("cleared_through", 0) == 0
    assert "summary" not in t


def test_a_clear_after_a_compact_drops_the_earlier_summary(compacted):
    """A clear REPLACES nothing, so reaching one must not inherit the fold's
    summary: the messages behind a clear are gone from the prompt outright."""
    chat_store.clear_thread_context(compacted)
    t = _doc(compacted)
    assert t["cut_mode"] == "clear"
    assert "summary" not in t
