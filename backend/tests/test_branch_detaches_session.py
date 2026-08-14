"""Going back to an earlier point takes the MODEL back too.

THE BUG, found 2026-08-14 while answering the operator's question about branching
across a compaction: a fork rewrote the tree and the screen and left the live CLI
session alone. A turn on a thread that HAS a session resumes it and sends only
the new message, so the history the model reasons over is the session's own,
private copy — which still held every message on the line just abandoned.

So the fork was a no-op for the model in the common case. Not a subtle one: the
operator goes back to before a bad instruction, types a correction, and the model
answers with the bad instruction still in front of it. The transcript filter
landed the day before (`_fold`'s branch filter, test_chat_branch_prompt.py) and
was only ever reached on a thread with NO session to resume, which is why it
looked fixed.

Dropping the session forces the next turn to mint a fresh one and re-assemble the
prompt from the transcript, which is the only place the branch is known.
"""
import asyncio

import pytest

from services import chat_store, ownership
from routes.chat import ForkThread


@pytest.fixture()
def thread(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield chat_store.create_thread("general")
    finally:
        ownership.clear_current_owner()


def _routes():
    import routes.chat as rc
    return rc


def _anchor(tid: str, needle: str) -> str:
    from services import chat_log
    return next(e["id"] for e in chat_log.read_events(tid)
                if e.get("kind") == "message" and needle in (e.get("content") or ""))


def test_a_fork_drops_the_live_session(thread, monkeypatch):
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "the plan")
    chat_store.append_message(tid, "assistant", "here it is")
    chat_store.append_message(tid, "user", "no, the other way")
    chat_store.ensure_thread_session(tid)
    assert chat_store.get_thread(tid).get("claude_session")

    # The tree write itself needs Postgres; what is under test is what the ROUTE
    # does around it, so the fork is stubbed and the detach is measured.
    from services import chat_branches
    monkeypatch.setattr(chat_branches, "fork",
                        lambda *_a, **_k: _async({"branch_id": "b1"}))

    asyncio.run(rc.fork_thread(tid, ForkThread(anchor=_anchor(tid, "the plan"), mode="after")))

    assert not chat_store.get_thread(tid).get("claude_session"), (
        "the fork left the CLI session attached, so the next turn would resume "
        "the conversation the operator just branched away from")


def test_a_switch_drops_the_live_session(thread, monkeypatch):
    """Switching lines changes which conversation the transcript describes, so
    it carries exactly the same hazard as forking."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "the plan")
    chat_store.ensure_thread_session(tid)

    from services import chat_branches
    monkeypatch.setattr(chat_branches, "switch",
                        lambda *_a, **_k: _async({"branch_id": "b2"}))

    asyncio.run(rc.switch_branch(tid, "b2"))

    assert not chat_store.get_thread(tid).get("claude_session")


def test_the_context_reading_goes_with_it(thread, monkeypatch):
    """The meter measured the window of the session being dropped. Left behind it
    pins the composer to a window that no longer exists, which is the same defect
    /compact's detach exists to avoid."""
    rc = _routes()
    tid = thread["id"]
    chat_store.append_message(tid, "user", "the plan")
    chat_store.ensure_thread_session(tid)
    chat_store.set_thread_context_tokens(tid, 142_000)

    from services import chat_branches
    monkeypatch.setattr(chat_branches, "fork",
                        lambda *_a, **_k: _async({"branch_id": "b1"}))

    asyncio.run(rc.fork_thread(tid, ForkThread(anchor=_anchor(tid, "the plan"), mode="after")))

    assert not chat_store.get_thread(tid).get("context_tokens")


async def _async(value):
    return value
