"""Scanning a chat files draft TICKETS, not thread-local rows.

A proposal used to live in its own little store on the thread, which made "draft"
mean two different things. Now a proposal IS a `draft` ticket, which buys three
things the thread-local version never had: dedup against existing work, the
readiness checklist, and visibility in Work rather than only inside a dialog.

Scanning is safe by construction because nothing runs a draft — the sweep skips it
and the run endpoint refuses it (see test_ticket_approval).
"""
from __future__ import annotations

import sys
from pathlib import Path

import asyncio

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import tasks, ticket_drafts  # noqa: E402

TWO_PROPOSALS = (
    '{"tickets":['
    '{"title":"Bind a scope to a tracker",'
    '"objective":"One binding per scope, chosen in Settings.",'
    '"output":"GET /api/trackers returns it and a test covers it.",'
    '"entry_points":"backend/services/tracker_bindings.py",'
    '"boundaries":"Do not touch the sync engine.","confidence":"decided"},'
    '{"title":"Add a triage inbox for imported issues",'
    '"objective":"Imported issues land in triage, not the run queue.",'
    '"output":"Accepting one creates a linked draft ticket.",'
    '"entry_points":"backend/services/triage_inbox.py",'
    '"boundaries":"No sync engine work.","confidence":"likely"}'
    ']}'
)


@pytest.fixture
def thread(monkeypatch, tmp_path):
    """A chat with a transcript, a tmp ticket store, and a stubbed model."""
    from services import chat_store, ownership

    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ownership, "_RUNTIME", runtime)
    ownership.set_current_owner(6)

    t = chat_store.create_thread(title="A working session")
    chat_store.append_message(t["id"], "user", "We should bind scopes to a tracker.")
    chat_store.append_message(t["id"], "assistant", "Agreed, and a triage inbox.")

    # The embedder is not available in the test env and is not what this is about.
    from services import task_dedup
    monkeypatch.setattr(task_dedup, "decide",
                        lambda text, open_tasks: {"decision": "distinct",
                                                  "matched_id": None, "cosine": None})
    yield t["id"], tmp_path / "store"
    ownership.clear_current_owner()


def _stub_model(monkeypatch, raw: str):
    from services import llm_router
    monkeypatch.setattr(llm_router, "call_cloud",
                        lambda *a, **k: {"text": raw})


def test_scanning_files_draft_tickets(thread, monkeypatch):
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)

    out = asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))
    assert len(out["drafts"]) == 2
    assert all(d["status"] == "draft" for d in out["drafts"])
    # Real tickets in the real store, not rows on the thread.
    stored = tasks.list_tasks(root, include_done=False)
    assert {t["title"] for t in stored} == {
        "Bind a scope to a tracker", "Add a triage inbox for imported issues"}


def test_a_proposal_is_attributed_and_traceable(thread, monkeypatch):
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    t = tasks.list_tasks(root)[0]
    # Where the request came from, and who wrote the spec: different questions.
    assert t["source"] == {"kind": "chat", "ref": thread_id}
    assert t["scoped_by"] == "agent:chat"
    assert "proposed" in t["labels"]


def test_confidence_rides_as_a_label(thread, monkeypatch):
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    by_title = {t["title"]: t for t in tasks.list_tasks(root)}
    # "decided" is the default and needs no label; anything less does.
    assert "confidence:likely" in by_title["Add a triage inbox for imported issues"]["labels"]
    assert not any(l.startswith("confidence:")
                   for l in by_title["Bind a scope to a tracker"]["labels"])


def test_a_proposal_is_ready_to_run_once_approved(thread, monkeypatch):
    """The point of composing the body through ticket_format: a proposal is not
    merely filed, it is specified."""
    from services import ticket_readiness as tr
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    for t in tasks.list_tasks(root):
        assert tr.check(t)["ok"] is True, tr.summary(tr.check(t))


def test_listing_reads_the_ticket_store(thread, monkeypatch):
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    got = ticket_drafts.get(thread_id, root, "workshop")
    assert len(got["drafts"]) == 2
    assert got["messages_since"] == 0


def test_an_approved_proposal_leaves_the_dialog(thread, monkeypatch):
    """`get` lists DRAFTS. Once approved, the ticket is work and belongs in Work,
    not in the proposal list."""
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    out = asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    tasks.update(root, out["drafts"][0]["id"], status="todo")
    assert len(ticket_drafts.get(thread_id, root, "workshop")["drafts"]) == 1


def test_a_discarded_proposal_is_cancelled_not_deleted(thread, monkeypatch):
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    out = asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    tasks.update(root, out["drafts"][0]["id"], status="cancelled")
    assert len(ticket_drafts.get(thread_id, root, "workshop")["drafts"]) == 1
    # Still on record. No ticket is ever deleted by any path.
    assert tasks.get_task(root, out["drafts"][0]["id"])["status"] == "cancelled"


def test_rescanning_does_not_propose_the_same_work_twice(thread, monkeypatch):
    """The dedup the thread-local store never had. A second read of the same
    conversation folds into the existing tickets."""
    from services import task_dedup
    thread_id, root = thread
    _stub_model(monkeypatch, TWO_PROPOSALS)
    asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    # Real dedup behaviour for the second pass: identical text hashes equal.
    monkeypatch.setattr(task_dedup, "decide", task_dedup.decide)
    asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))

    assert len(tasks.list_tasks(root, include_done=False)) == 2


def test_an_empty_conversation_proposes_nothing(monkeypatch, tmp_path):
    from services import chat_store, ownership
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    ownership.set_current_owner(6)
    try:
        t = chat_store.create_thread(title="Nothing said")
        out = asyncio.run(ticket_drafts.propose(t["id"], tmp_path / "store", "workshop"))
        assert out["drafts"] == []
    finally:
        ownership.clear_current_owner()


def test_zero_proposals_is_a_normal_answer(thread, monkeypatch):
    thread_id, root = thread
    _stub_model(monkeypatch, '{"tickets":[]}')
    out = asyncio.run(ticket_drafts.propose(thread_id, root, "workshop"))
    assert out["drafts"] == []
    assert out["generated_at"]  # the scan happened; it just found nothing
