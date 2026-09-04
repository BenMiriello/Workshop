"""The running turn's draft lives in its own file, and what that has to preserve.

While a turn is in flight the reply so far is rewritten every ~0.4s and on every
tool event. That draft used to be a field inside meta.json, so each rewrite was
a whole-document, fsynced write of the thread's DURABLE record, of a document
that grew with the reply: measured at 3.6ms a time on a 120-segment reply, about
five seconds of event loop for a ten-minute turn, and rising with reply length.

It now lives in `live.json`: atomic (temp + rename, so no torn read) but not
fsynced, because the draft is derived. The journal is the buffer of record while
a turn runs and `commit_turn` appends the real message at the end, so a power cut
loses the process anyway and a durably-synced half-draft buys nothing.

These pin the parts of that which are silent when broken.
"""
import json

import pytest

from services import chat_log, chat_store, ownership


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")["id"]
    finally:
        ownership.clear_current_owner()


def _claim(tid, turn_id="r1", **kw):
    chat_store.begin_turn(tid, turn_id, **kw)


def _segs(n=3):
    return [{"t": "text", "text": f"chunk {i}"} for i in range(n)]


class TestTheDraftRoundTrips:
    def test_what_was_written_is_what_active_run_reports(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", _segs(), "hello so far")
        run = chat_log.active_turn(thread)
        assert run is not None
        assert run["turn_id"] == "r1"
        assert run["live"]["content"] == "hello so far"
        assert run["live"]["meta"]["segments"] == _segs()

    def test_a_later_write_replaces_an_earlier_one(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", _segs(1), "one")
        chat_store.update_turn(thread, "r1", _segs(5), "one two three")
        live = chat_log.active_turn(thread)["live"]
        assert live["content"] == "one two three"
        assert len(live["meta"]["segments"]) == 5

    def test_the_live_view_reaches_the_thread_document(self, thread):
        """A reload renders the turn in progress, which is the draft's whole job."""
        _claim(thread)
        chat_store.update_turn(thread, "r1", _segs(), "in progress")
        msgs = chat_store.get_thread(thread)["messages"]
        assert msgs[-1]["role"] == "assistant"
        assert msgs[-1]["content"] == "in progress"
        assert msgs[-1]["meta"]["streaming"] is True
        assert msgs[-1]["meta"]["turn_id"] == "r1"


class TestTheRunsImmutableFactsSurvive:
    """model/agent are written ONCE at the claim and are not in the draft.

    Merging the two with a plain `{**base, **draft}` drops them, because each
    side owns different keys of the nested `meta`. That would silently remove
    the byline the transcript renders over a mentioned agent's streaming reply.
    """

    def test_model_and_agent_survive_a_draft_write(self, thread):
        _claim(thread, model="claude-opus-5", agent="researcher")
        chat_store.update_turn(thread, "r1", _segs(), "partial")
        m = chat_log.active_turn(thread)["live"]["meta"]
        assert m["model"] == "claude-opus-5"
        assert m["agent"] == "researcher"
        # and the draft's own keys are there too
        assert m["segments"] == _segs()
        assert m["turn_id"] == "r1"

    def test_the_start_timestamp_survives(self, thread):
        _claim(thread)
        started = chat_log.active_turn(thread)["live"]["ts"]
        assert started
        chat_store.update_turn(thread, "r1", _segs(), "partial")
        assert chat_log.active_turn(thread)["live"]["ts"] == started


class TestADraftCannotOutliveItsRun:
    def test_a_draft_naming_another_run_is_ignored(self, thread):
        """A backend killed mid-turn leaves live.json with no commit to clear it.

        The next run to claim the thread must not inherit the dead turn's text.
        """
        _claim(thread, turn_id="r1")
        chat_store.update_turn(thread, "r1", _segs(), "the dead turn's words")
        # Forge the situation a crash leaves: the draft is on disk, but meta now
        # points at a different run.
        chat_log.patch_meta_sync(thread, active_turn_id="r2", live_message=None)
        assert chat_log.live_path(thread).exists()
        live = chat_log.active_turn(thread)["live"]
        assert live is None or "dead turn" not in (live.get("content") or "")

    def test_claiming_a_thread_clears_a_stale_draft(self, thread):
        _claim(thread, turn_id="r1")
        chat_store.update_turn(thread, "r1", _segs(), "stale")
        _claim(thread, turn_id="r2")
        assert not chat_log.live_path(thread).exists()

    def test_committing_removes_the_draft(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", _segs(), "partial")
        assert chat_log.live_path(thread).exists()
        chat_store.commit_turn(thread, "r1", _segs(), "the finished reply")
        assert not chat_log.live_path(thread).exists()
        assert chat_log.active_turn(thread) is None
        assert chat_store.get_thread(thread)["messages"][-1]["content"] == "the finished reply"

    def test_releasing_a_claim_removes_the_draft(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", _segs(), "never finished")
        chat_store.release_turn(thread, "r1")
        assert not chat_log.live_path(thread).exists()

    def test_a_write_from_a_run_that_lost_the_thread_is_refused(self, thread):
        _claim(thread, turn_id="r1")
        _claim(thread, turn_id="r2")
        chat_store.update_turn(thread, "r1", _segs(), "I am not the active run")
        live = chat_log.active_turn(thread)["live"]
        assert live is None or "not the active run" not in (live.get("content") or "")


class TestTheDurableRecordIsLeftAlone:
    """The point of the whole change: a turn stops rewriting meta.json."""

    def test_meta_json_is_not_touched_by_a_draft_write(self, thread):
        _claim(thread)
        path = chat_log.meta_path(thread)
        before_bytes = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns
        for i in range(20):
            chat_store.update_turn(thread, "r1", _segs(i + 1), "x" * (100 * i))
        assert path.read_bytes() == before_bytes
        assert path.stat().st_mtime_ns == before_mtime

    def test_meta_json_does_not_grow_with_the_reply(self, thread):
        _claim(thread)
        small = chat_log.meta_path(thread).stat().st_size
        chat_store.update_turn(
            thread, "r1", [{"t": "text", "text": "y" * 500} for _ in range(200)], "y" * 40000)
        assert chat_log.meta_path(thread).stat().st_size == small
        # ...and the draft file is where that size went.
        assert chat_log.live_path(thread).stat().st_size > 40000


class TestTheOlderShapeStillRenders:
    """A turn already in flight when this build deployed has no live.json."""

    def test_a_draft_only_in_meta_is_still_returned(self, thread):
        _claim(thread)
        chat_log.clear_live(thread)
        chat_log.patch_meta_sync(thread, live_message={
            "turn_id": "r1", "content": "written by the old build",
            "meta": {"segments": _segs(2), "turn_id": "r1"}, "ts": "2026-08-11T00:00:00Z",
        })
        live = chat_log.active_turn(thread)["live"]
        assert live["content"] == "written by the old build"
        assert len(live["meta"]["segments"]) == 2

    def test_such_a_turn_upgrades_on_its_next_write(self, thread):
        _claim(thread)
        chat_log.clear_live(thread)
        chat_store.update_turn(thread, "r1", _segs(4), "now on the new path")
        live = chat_log.active_turn(thread)["live"]
        assert live["content"] == "now on the new path"
        assert len(live["meta"]["segments"]) == 4


class TestTheDraftFileItself:
    def test_it_is_valid_json_and_compact(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", _segs(), "hello")
        raw = chat_log.live_path(thread).read_text()
        assert json.loads(raw)["content"] == "hello"
        # Compact separators: the draft is machine-read only, and indent on a
        # document rewritten thousands of times is pure cost.
        assert ", " not in raw and ": " not in raw

    def test_no_temp_file_is_left_behind(self, thread):
        _claim(thread)
        for _ in range(5):
            chat_store.update_turn(thread, "r1", _segs(), "hello")
        leftovers = [p.name for p in chat_log.thread_dir(thread).iterdir()
                     if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_reading_a_missing_draft_is_not_an_error(self, thread):
        assert chat_log.read_live(thread) is None
        chat_log.clear_live(thread)  # idempotent


class TestMergeLive:
    """The merge rule on its own, since it is where the subtle loss would be."""

    def test_no_draft_returns_the_base_untouched(self):
        base = {"turn_id": "r1", "content": "a", "meta": {"model": "m"}}
        assert chat_log.merge_live(base, None) == base

    def test_no_base_returns_the_draft(self):
        draft = {"turn_id": "r1", "content": "b", "meta": {"segments": []}}
        assert chat_log.merge_live(None, draft) == draft

    def test_nested_meta_is_merged_not_replaced(self):
        base = {"turn_id": "r1", "content": "", "ts": "t0", "meta": {"model": "m", "agent": "a"}}
        draft = {"turn_id": "r1", "content": "words", "meta": {"segments": [1], "turn_id": "r1"}}
        out = chat_log.merge_live(base, draft)
        assert out["content"] == "words"
        assert out["ts"] == "t0"
        assert out["meta"] == {"model": "m", "agent": "a", "segments": [1], "turn_id": "r1"}

    def test_the_merge_does_not_mutate_either_side(self):
        base = {"turn_id": "r1", "meta": {"model": "m"}}
        draft = {"turn_id": "r1", "meta": {"segments": [1]}}
        chat_log.merge_live(base, draft)
        assert base == {"turn_id": "r1", "meta": {"model": "m"}}
        assert draft == {"turn_id": "r1", "meta": {"segments": [1]}}


class TestACutPositionSurvivesAReattach:
    """A cut position is never unlearned within a turn, so an empty list in
    memory must not erase one already on disk.

    A backend restart rebuilds a running Turn from its journal, and the journal
    carries segments but not cut positions, so the re-attached turn's
    `cut_head` is empty. `write_live_sync` replaces the whole file, so the next
    incremental persist DELETED the cut positions rather than merely failing to
    update them, and the client's next fresh read had no way to place the
    messages the operator sent mid-turn. They render after the whole reply.

    Measured 2026-09-03 on the live installation: a message filed at 21:40:26
    recorded `at_segment 0` with a cut id, the backend restarted six seconds
    later, and every later snapshot carried 56 segments and no `cut_head`.
    """

    def test_a_later_write_with_no_cut_head_keeps_the_recorded_one(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", [], "", ["m1"])
        assert (chat_log.read_live(thread)["meta"]).get("cut_head") == ["m1"]

        # The re-attached turn: same turn id, rebuilt segments, no cut_head.
        chat_store.update_turn(thread, "r1", _segs(5), "the reply so far")

        live = chat_log.read_live(thread)["meta"]
        assert live.get("cut_head") == ["m1"], "the cut position was erased"
        assert len(live["segments"]) == 5, "the rebuilt segments still land"

    def test_the_active_turn_snapshot_still_reports_it(self, thread):
        _claim(thread)
        chat_store.update_turn(thread, "r1", [], "", ["m1"])
        chat_store.update_turn(thread, "r1", _segs(2), "more")
        run = chat_log.active_turn(thread)
        assert (run["live"]["meta"]).get("cut_head") == ["m1"]

    def test_a_growing_cut_head_still_wins_over_the_stored_one(self, thread):
        """Carrying forward is a floor, not a freeze: a turn that learns a
        SECOND cut position must still be able to record it."""
        _claim(thread)
        chat_store.update_turn(thread, "r1", [], "", ["m1"])
        chat_store.update_turn(thread, "r1", _segs(1), "x", ["m1", "m2"])
        assert (chat_log.read_live(thread)["meta"]).get("cut_head") == ["m1", "m2"]

    def test_a_different_turn_does_not_inherit_the_previous_cut_head(self, thread):
        """The carry-forward is fenced on the turn id, like every other read of
        the draft: a new turn must not show the dead one's cut positions."""
        _claim(thread)
        chat_store.update_turn(thread, "r1", [], "", ["m1"])
        chat_store.commit_turn(thread, "r1", _segs(1), "done")
        _claim(thread, turn_id="r2")
        chat_store.update_turn(thread, "r2", _segs(2), "fresh")
        assert (chat_log.read_live(thread)["meta"]).get("cut_head") is None
