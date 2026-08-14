"""Going back to an earlier point is an OPERATION on the stored conversation, so
it obeys the one-op-per-thread rule (ADR 0021) like /compact and /clear do.

THE BUG THIS PINS, found 2026-08-14: branching was not in the concurrency
model's case matrix at all, and nothing on the fork path asked whether the thread
was busy. `chat_repo.append_message` parents a new message to the CURRENT branch
head and a fork has just moved that pointer, so a turn already in flight
committed its reply onto the NEW branch as a child of the fork anchor. The
operator would go back to before a question and the answer to that question would
arrive on the line he had just removed it from.

It REFUSES rather than queueing, which is where branching differs from a fold. A
fold is slow and can sensibly wait its turn. Going back cannot: the thing it
would be waiting for is the very turn it is trying to get rid of.
"""
import asyncio

import pytest
from fastapi import HTTPException

from services import build_streaming, chat_log, chat_store, ownership, thread_ops


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    monkeypatch.setattr(build_streaming, "TURNS_DIR", tmp_path / "turns")
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()
        build_streaming.TURNS.clear()
        thread_ops._PENDING.clear()


def _guard(tid):
    from routes import chat as chat_routes
    chat_routes._require_quiet_for_branching(tid, "going back")


class TestItRefusesWhileTheThreadIsBusy:
    def test_a_live_turn_blocks_branching(self, thread):
        tid = thread["id"]
        asyncio.run(build_streaming.claim_run(tid, "r-1", model="m", accepts_input=True))
        with pytest.raises(HTTPException) as e:
            _guard(tid)
        assert e.value.status_code == 409
        assert "turn is running" in e.value.detail

    def test_a_pending_operation_blocks_branching(self, thread):
        tid = thread["id"]
        assert thread_ops.begin(tid, "compact") is True
        with pytest.raises(HTTPException) as e:
            _guard(tid)
        assert e.value.status_code == 409
        assert "compact" in e.value.detail

    def test_a_quiet_thread_is_allowed(self, thread):
        _guard(thread["id"])

    def test_it_is_allowed_again_once_the_turn_ends(self, thread):
        tid = thread["id"]
        asyncio.run(build_streaming.claim_run(tid, "r-1", model="m", accepts_input=True))
        build_streaming.release_external_run(tid, "r-1")
        _guard(tid)

    def test_it_is_allowed_again_once_the_operation_ends(self, thread):
        tid = thread["id"]
        thread_ops.begin(tid, "clear")
        thread_ops.end(tid)
        _guard(tid)

    def test_a_stale_run_id_does_not_block_forever(self, thread):
        """`active_run_id` outlives the run it names across a restart. A thread
        whose meta still points at a run this process is not driving is quiet,
        and the whole reason `run_is_live` exists is so every caller agrees."""
        tid = thread["id"]
        chat_log.patch_meta_sync(tid, active_run_id="r-from-a-previous-boot")
        assert chat_store.active_run_id(tid) == "r-from-a-previous-boot"
        assert build_streaming.run_is_live("r-from-a-previous-boot") is False
        _guard(tid)


class TestSwitchingIsGuardedToo:
    def test_switch_uses_the_same_rule(self, thread):
        """Switching moves the same pointer an in-flight reply parents itself
        to, so it is not the cheaper, safer half of the pair."""
        from routes import chat as chat_routes
        tid = thread["id"]
        asyncio.run(build_streaming.claim_run(tid, "r-1", model="m", accepts_input=True))
        with pytest.raises(HTTPException) as e:
            chat_routes._require_quiet_for_branching(tid, "switching")
        assert e.value.status_code == 409

    def test_the_verb_reaches_the_message(self, thread):
        from routes import chat as chat_routes
        tid = thread["id"]
        thread_ops.begin(tid, "compact")
        with pytest.raises(HTTPException) as e:
            chat_routes._require_quiet_for_branching(tid, "switching")
        assert "switching" in e.value.detail
