"""Ticket 17d81555: every message and every queued entry carries a sender,
so the client can tell the operator's words from anything else without reading
the text.

A run report is `sender: run:<job-id>`. A platform signal (continuation seed,
ticket question) is `sender: platform`. An operator message has no sender field
(absence means the operator, matching chat_queue's own comment and keeping
existing records correct without a backfill).
"""
import json
import uuid

import pytest

from services import background_runs, chat_drain, chat_log, chat_queue, ownership, run_report


def _make_thread(tmp_path, owner_id: int = 6) -> str:
    """Create a thread directory with a minimal meta.json, bypassing
    chat_store.create_thread which lazily imports chat_branches (needs starlette).
    """
    thread_id = uuid.uuid4().hex
    thread_dir = tmp_path / "users" / str(owner_id) / "chat" / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    (thread_dir / "meta.json").write_text(
        json.dumps({"id": thread_id, "kind": "general", "last_seq": 0})
    )
    return thread_id


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    # _mirror calls chat_branches which needs starlette (not installed in the
    # unit-test environment). The mirror is a Postgres side-channel; patching it
    # out here does not affect the file-log assertions we care about.
    monkeypatch.setattr(chat_log, "_mirror", lambda *a, **kw: None)
    monkeypatch.setattr(chat_log, "_mirror_meta", lambda *a, **kw: None)
    # run_report resolves _RUNTIME at import time; redirect it so owner_of_thread
    # can find threads written under tmp_path.
    monkeypatch.setattr(run_report, "_RUNTIME", tmp_path)
    ownership.set_current_owner(6)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def tid(owned):
    return _make_thread(owned)


def test_run_report_carries_sender(tid):
    background_runs._deliver(
        thread_id=tid, owner_id=6, job_id="job-42", label="probe",
        status="done", text="the answer")
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "assistant"]
    assert len(msgs) == 1
    assert msgs[0]["meta"]["sender"] == "run:job-42"


def test_operator_message_has_no_sender(tid):
    # Use the log layer directly: chat_store.append_message for user messages
    # calls _patch → get_thread → _fold → chat_branches (needs starlette).
    # The file-log write is what we're asserting on; the Postgres mirror is patched out.
    chat_log.append_message_sync(tid, "user", "hello from the operator")
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "user"]
    assert len(msgs) == 1
    assert "sender" not in (msgs[0].get("meta") or {})


def test_run_report_and_operator_message_are_distinguishable(tid):
    """The field alone tells them apart; no reading of the text required."""
    chat_log.append_message_sync(tid, "user", "operator typed this")
    background_runs._deliver(
        thread_id=tid, owner_id=6, job_id="job-55", label="sweep",
        status="done", text="sweep found something")
    events = [e for e in chat_log.read_events(tid) if e.get("kind") == "message"]
    by_role = {e["role"]: e for e in events}
    operator_sender = (by_role["user"].get("meta") or {}).get("sender")
    run_sender = (by_role["assistant"].get("meta") or {}).get("sender")
    assert operator_sender is None
    assert run_sender == "run:job-55"
    assert operator_sender != run_sender


def test_continuation_seed_writes_thread_state_not_queue(tid):
    """A wake signal is thread state, not a queue entry.

    After request_continuation: the continuation file holds the seed,
    the operator's queue is empty (no user bubble), and the signal is
    idempotent on the same dedupe_id.
    """
    ok = chat_drain.request_continuation(
        tid, seed="read and continue", dedupe_id="jobA-" + tid)
    assert ok is True
    # Thread state: continuation file exists
    entry = chat_queue.read_continuation(tid)
    assert entry is not None
    assert entry["text"] == "read and continue"
    assert entry["auto"] is True
    assert entry["client_id"] == "auto-jobA-" + tid
    # Operator queue: no entry (no user bubble in the browser)
    assert chat_queue.read(tid) == []
    # Idempotent: same dedupe_id does not double-arm
    ok2 = chat_drain.request_continuation(
        tid, seed="read and continue", dedupe_id="jobA-" + tid)
    assert ok2 is True
    assert chat_queue.read_continuation(tid) is not None


def test_background_run_produces_one_transcript_record(tid):
    """A background agent finishing writes exactly one message (the report) and
    no queued user entry — the wake signal is thread state, invisible in the
    transcript and in the operator's queue.
    """
    background_runs._deliver(
        thread_id=tid, owner_id=6, job_id="job-77", label="audit",
        status="done", text="found nothing")
    # One assistant message (the report)
    msgs = [e for e in chat_log.read_events(tid) if e.get("kind") == "message"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert msgs[0]["meta"]["sender"] == "run:job-77"
    # No operator-voice queue entry (no user bubble)
    assert chat_queue.read(tid) == []
    # The wake signal is in the continuation file, not the queue
    entry = chat_queue.read_continuation(tid)
    assert entry is not None
    assert entry["auto"] is True


def test_question_report_carries_platform_sender(tid, owned):
    """A ticket question posted into a chat is sender: platform."""
    from services import question_report, run_report
    # owner_of_thread scans _RUNTIME/users/<id>/chat/<tid>/meta.json — the
    # fixture creates exactly that, so the scan finds owner 6.
    task = {"id": "t-1", "source": {"kind": "chat", "ref": tid}}
    request = {"ask": "Should I proceed?", "kind": "approval"}
    result = question_report.post_for_task(task, request, scope="workshop")
    assert result is True
    msgs = [e for e in chat_log.read_events(tid)
            if e.get("kind") == "message" and e.get("role") == "assistant"]
    assert len(msgs) == 1
    assert msgs[0]["meta"]["sender"] == "platform"
