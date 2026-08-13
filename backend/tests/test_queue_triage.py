"""The queue triager: does it stop, and does it stay inside its caps.

THE BUG THIS FILE EXISTS TO PREVENT is not "the mark is wrong". It is "the mark
is written and the ticket is selected again anyway". The note triager keys its
frontier on file mtime, which is safe only because that role cannot write notes.
This role reads tickets and writes tickets, and `tasks.update()` stamps
`updated` on every write, so anything derived from the record's own timestamps
re-arms every ticket the pass just touched. `test_a_second_pass_selects_nothing`
is therefore asserted on the SECOND PASS RETURNING NO DECISIONS, not on the mark
existing: a pass that writes the mark and still re-selects is exactly the defect,
and it passes any test that only checks the mark.

The other four cases guard the things that make this survivable in an inbox:
both write caps, one question per ticket, `raised_by` spelled the way the
attempt-refund suppression in `routes/tasks_all.py` keys on, and children that
land as drafts under their parent rather than as work nobody approved.

No real model calls: `llm_router.call_cloud` is stubbed per test, so the REAL
`ticket_scope_review` engine runs (its parse, its two-child downgrade, its
refusal to split a sub-ticket) against a canned reply. And every store here is a
`tmp_path`; nothing in this file can reach the live queue.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import ownership, tasks, triager  # noqa: E402

SCOPE = "project:probe"


# --- fixtures ----------------------------------------------------------------

@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A task store of our own, and a runtime of our own.

    `ownership._RUNTIME` is redirected even though this pass keeps no sidecar:
    anything reached through `ownership.workspace_path` falls back to the
    OPERATOR's real directories when no owner is pinned, so a test that forgot
    this would write into live stores.
    """
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(triager, "_task_root", lambda scope_key: store)
    return store


def _reply(**kw) -> str:
    payload = {"verdict": "ok", "reason": "one deliverable",
               "subtickets": [], "blockers": [], "stages": []}
    payload.update(kw)
    return json.dumps(payload)


def _stub(monkeypatch: pytest.MonkeyPatch, raw) -> None:
    """Stub the ONE model call the engine makes. `raw` is a JSON string, or a
    callable taking the prompt so a pass over many tickets can answer each one
    differently (the prompt embeds the ticket title)."""
    from services import llm_router

    def call(prompt, *a, **k):
        return {"text": raw(prompt) if callable(raw) else raw}

    monkeypatch.setattr(llm_router, "call_cloud", call)


SPLIT = _reply(
    verdict="split",
    reason="This names two independent deliverables.",
    subtickets=[
        {"title": "Add the binding record", "objective": "Store one binding per scope.",
         "output": "A binding round-trips.",
         "entry_points": "backend/services/tracker_bindings.py", "boundaries": "No UI."},
        {"title": "Add the settings form", "objective": "Bind a scope from Settings.",
         "output": "The form creates a binding.",
         "entry_points": "frontend/src/pages/settings", "boundaries": "No sync engine."},
    ],
)

THIN = _reply(verdict="underspecified",
              reason="The ticket does not say what done looks like.",
              blockers=["The ticket does not say what a passing run looks like."])


# A body that answers every readiness check. The labels have to start a LINE:
# `ticket_format.LABEL_RE` is line-anchored, so a one-line body states nothing
# but its objective.
READY = ("Objective: make the reporting page load faster.\n"
         "Output: the page renders in under a second.\n"
         "Start from: `backend/routes/reports.py`\n"
         "Boundaries: do not touch the frontend.")


def _thin_ticket(root: Path, n: int = 0) -> dict:
    """A ticket that fails the acceptance and boundaries readiness checks, which
    is what most of the live queue looks like."""
    return tasks.add(root, title=f"Do the thing {n}",
                     body="Objective: make the reporting page load faster than it "
                          "does today, somehow, in backend/routes/reports.py.")


# --- 1. THE ONE THAT MATTERS: the pass terminates ----------------------------

def test_a_second_pass_selects_nothing(root: Path, monkeypatch) -> None:
    """A ticket this pass just marked `underspecified` must not come back.

    Asserted on the second pass DECIDING NOTHING, because writing the mark and
    re-selecting anyway is the failure this whole mechanism exists to stop, and a
    test that only checks the mark cannot see it.
    """
    _stub(monkeypatch, THIN)
    _thin_ticket(root)

    first = triager.run_queue_triage(SCOPE, dry_run=False)
    assert first["decided"] == 1
    assert first["counts"]["underspecified"] == 1

    second = triager.run_queue_triage(SCOPE, dry_run=False)
    assert second["due"] == 0, "the ticket was selected again"
    assert second["decided"] == 0
    assert second["decisions"] == []


def test_the_mark_is_what_stops_it_not_the_block(root: Path, monkeypatch) -> None:
    """Clear the question the pass raised and it STILL does not re-select. The
    block is a consequence; the mark is the frontier."""
    _stub(monkeypatch, THIN)
    t = _thin_ticket(root)
    triager.run_queue_triage(SCOPE, dry_run=False)

    cleared = tasks.update(root, t["id"], awaiting={})
    assert cleared["awaiting"] is None
    assert triager.run_queue_triage(SCOPE, dry_run=False)["decided"] == 0

    # ...and it comes back the moment the SPEC actually changes, which is the
    # other half of correct: a rewritten ticket is a new question.
    tasks.update(root, t["id"], body="Objective: something else entirely, Done: it loads.")
    assert triager.run_queue_triage(SCOPE, dry_run=False)["decided"] == 1


def test_every_decided_path_writes_the_mark(root: Path, monkeypatch) -> None:
    """`ok` and `none` change nothing else, so they are the paths most likely to
    be left unmarked, and an unmarked ticket is re-judged forever."""
    _stub(monkeypatch, lambda p: (
        _reply(verdict="underspecified", reason="objected but named nothing")
        if "nothing to ask" in p else _reply()))
    ok = tasks.add(root, title="A fine ticket",
                   body=READY)
    quiet = tasks.add(root, title="nothing to ask",
                      body=READY)

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["decided"] == 2
    assert out["counts"]["ok"] == 1
    # An `underspecified` verdict with no answerable gap is the runner's `none`.
    assert out["counts"]["none"] == 1
    for t in (ok, quiet):
        fresh = tasks.get_task(root, t["id"])
        assert fresh["triage"] is not None
        assert tasks.triage_due(fresh) is False
    assert tasks.get_task(root, quiet["id"])["awaiting"] is None


def test_a_dry_run_writes_nothing(root: Path, monkeypatch) -> None:
    _stub(monkeypatch, THIN)
    t = _thin_ticket(root)
    out = triager.run_queue_triage(SCOPE, dry_run=True)
    assert out["decided"] == 1 and out["counts"]["underspecified"] == 1
    fresh = tasks.get_task(root, t["id"])
    assert fresh["triage"] is None and fresh["awaiting"] is None
    assert tasks.triage_due(fresh) is True


# --- 2. the caps -------------------------------------------------------------

def test_the_look_cap_holds(root: Path, monkeypatch) -> None:
    """20 looked at, the rest still due. Nothing that was never read is marked."""
    _stub(monkeypatch, _reply())
    for i in range(25):
        tasks.add(root, title=f"Ticket {i:02d}",
                  body=READY)

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["due"] == 25
    assert out["looked_at"] == triager.MAX_QUEUE_TICKETS_PER_PASS == 20
    assert out["decided"] == 20
    assert out["deferred"] == 5
    assert sum(1 for t in tasks.list_tasks(root) if tasks.triage_due(t)) == 5
    # And the leftovers are picked up next time rather than starved.
    assert triager.run_queue_triage(SCOPE, dry_run=False)["decided"] == 5


def test_the_split_cap_holds(root: Path, monkeypatch) -> None:
    """At most 5 tickets may file children in one pass, and the sixth is left
    UNMARKED so the split is not lost."""
    _stub(monkeypatch, SPLIT)
    for i in range(8):
        tasks.add(root, title=f"Big ticket {i}", body="Objective: all of it.")

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["counts"]["split"] == triager.MAX_SPLIT_PER_PASS == 5
    assert out["capped"] == 3
    children = [t for t in tasks.list_tasks(root) if t.get("parent")]
    assert len(children) == 10, "two children per split, and no more"
    assert sum(1 for t in tasks.list_tasks(root)
               if t["status"] == "todo" and tasks.triage_due(t)) == 3


def test_the_question_cap_holds(root: Path, monkeypatch) -> None:
    """25 of 34 open tickets fail the acceptance check. Without this cap the
    first pass is a wall of 25 questions in one morning."""
    _stub(monkeypatch, THIN)
    for i in range(8):
        _thin_ticket(root, i)

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["counts"]["underspecified"] == triager.MAX_UNDERSPECIFIED_PER_PASS == 5
    assert out["capped"] == 3
    blocked = [t for t in tasks.list_tasks(root) if t.get("awaiting")]
    assert len(blocked) == 5


def test_a_capped_ticket_is_not_marked(root: Path, monkeypatch) -> None:
    """The dangerous shortcut is downgrading a capped ticket to `none`: it would
    terminate too, by throwing the decision away."""
    _stub(monkeypatch, SPLIT)
    for i in range(6):
        tasks.add(root, title=f"Big ticket {i}", body="Objective: all of it.")
    triager.run_queue_triage(SCOPE, dry_run=False)
    leftover = [t for t in tasks.list_tasks(root)
                if not t.get("parent") and tasks.triage_due(t)]
    assert len(leftover) == 1
    assert leftover[0]["triage"] is None


# --- 3. the string the refund suppression keys on ----------------------------

def test_every_block_is_attributed_to_the_queue_triager(root: Path, monkeypatch) -> None:
    """`routes/tasks_all.py` suppresses the attempt refund by matching this exact
    prefix. Spell it differently and the suppression silently stops working while
    every test that only checks "a block was raised" still passes."""
    _stub(monkeypatch, THIN)
    for i in range(3):
        _thin_ticket(root, i)
    triager.run_queue_triage(SCOPE, dry_run=False)

    blocked = [t for t in tasks.list_tasks(root) if t.get("awaiting")]
    assert len(blocked) == 3
    for t in blocked:
        assert t["awaiting"]["raised_by"] == "agent:queue-triager"
        assert t["awaiting"]["raised_by"] == triager.QUEUE_RAISED_BY
        assert t["awaiting"]["raised_by"].startswith("agent:queue-triager")


def test_the_block_is_an_explicit_expiring_kind_with_a_group_key(root: Path, monkeypatch) -> None:
    """`kind` is never left to the default: `_normalize_awaiting` defaults it to
    `input`, and `input` expires, so a defaulted block could auto-resolve itself
    instead of waiting for the answer it asked for."""
    _stub(monkeypatch, THIN)
    a, b = _thin_ticket(root, 0), _thin_ticket(root, 1)
    triager.run_queue_triage(SCOPE, dry_run=False)

    keys = set()
    for t in (a, b):
        rec = tasks.get_task(root, t["id"])["awaiting"]
        assert rec["kind"] == "missing-info"
        assert rec["unblocks_if"]
        assert rec["why"]
        keys.add(rec["dedupe_key"])
    # Ten tickets missing the same thing are answerable as one gesture.
    assert keys == {f"triage:{SCOPE}:acceptance"}


def test_a_ticket_already_waiting_on_someone_is_left_alone(root: Path, monkeypatch) -> None:
    """A second question would overwrite the first. No block, and no mark: when
    the answer lands in the body the spec hash moves and it comes back due."""
    _stub(monkeypatch, THIN)
    t = _thin_ticket(root)
    tasks.update(root, t["id"], awaiting={
        "ask": "Which database?", "kind": "decision", "raised_by": "operator"})

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["already_blocked"] == 1
    assert out["decided"] == 0
    fresh = tasks.get_task(root, t["id"])
    assert fresh["awaiting"]["ask"] == "Which database?"
    assert fresh["triage"] is None


# --- 4. one question, never a compound one -----------------------------------

def test_exactly_one_question_per_ticket(root: Path, monkeypatch) -> None:
    """The engine returns a LIST of blockers, up to eight. A five-part question
    is not answerable from an inbox card, so four of them are discarded."""
    _stub(monkeypatch, _reply(
        verdict="underspecified", reason="several things are missing",
        blockers=[
            "The ticket does not say what a passing run looks like.",
            "It is not clear which files this may change.",
            "Nobody has decided whether this needs a migration.",
            "The ticket names no entry point.",
        ]))
    t = _thin_ticket(root)
    out = triager.run_queue_triage(SCOPE, dry_run=False)

    rec = tasks.get_task(root, t["id"])["awaiting"]
    ask = rec["ask"]
    assert ask.count("?") == 1, ask
    # Only the highest-value gap survives; the other blockers are gone.
    assert "migration" not in ask
    assert "entry point" not in ask
    assert out["decisions"][0]["check"] == "acceptance"


def test_a_compound_blocker_is_cut_to_its_first_question(root: Path, monkeypatch) -> None:
    """A model that crams three questions into one blocker gets the same
    treatment as a model that returns three blockers."""
    _stub(monkeypatch, _reply(
        verdict="underspecified", reason="unclear",
        blockers=["What does done look like? And which files may it touch? "
                  "And who signs it off?"]))
    t = _thin_ticket(root)
    triager.run_queue_triage(SCOPE, dry_run=False)

    ask = tasks.get_task(root, t["id"])["awaiting"]["ask"]
    assert ask == "What does done look like?"


def test_a_blocker_stated_as_a_fact_still_becomes_one_question(root: Path, monkeypatch) -> None:
    _stub(monkeypatch, _reply(
        verdict="underspecified", reason="unclear",
        blockers=["Nobody has decided what a passing run looks like. "
                  "The ticket also names no owner."]))
    t = _thin_ticket(root)
    triager.run_queue_triage(SCOPE, dry_run=False)

    ask = tasks.get_task(root, t["id"])["awaiting"]["ask"]
    assert ask.count("?") == 1, ask
    assert "no owner" not in ask
    assert ask.endswith("?")


# --- 5. splitting ------------------------------------------------------------

def test_a_split_files_children_as_drafts_under_the_parent(root: Path, monkeypatch) -> None:
    _stub(monkeypatch, SPLIT)
    parent = tasks.add(root, title="Do the tracker work", body="Objective: all of it.",
                       source={"kind": "chat", "ref": "t1"})

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["counts"]["split"] == 1

    children = [t for t in tasks.list_tasks(root) if t.get("parent")]
    assert len(children) == 2
    for c in children:
        # A split is a PROPOSAL. The operator approves the children.
        assert c["status"] == "draft"
        assert c["parent"] == parent["id"]
        assert c["scoped_by"] == "agent:queue-triager"
        # The request still traces to the chat; only the spec was written here.
        assert c["source"] == {"kind": "chat", "ref": "t1"}
        assert c["body"].startswith("Objective: ")

    kept = tasks.get_task(root, parent["id"])
    assert kept["status"] == "todo", "the original wording stays as the record"
    assert kept["title"] == "Do the tracker work"
    assert kept["triage"]["decision"] == "split"
    assert "split" in [e.get("kind") for e in kept["evidence"]]
    # Nothing filed by a split is runnable until someone approves it.
    assert tasks.next_ready(root) is None or tasks.next_ready(root)["id"] == parent["id"]


def test_a_subticket_is_not_split_again(root: Path, monkeypatch) -> None:
    """Depth is capped at two, enforced by the engine's existing `parent`
    refusal. The runner must not route around it."""
    _stub(monkeypatch, SPLIT)
    parent = tasks.add(root, title="Do the tracker work", body="Objective: all of it.")
    triager.run_queue_triage(SCOPE, dry_run=False)
    child = [t for t in tasks.list_tasks(root) if t.get("parent")][0]
    tasks.update(root, child["id"], status="todo")

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["counts"]["split"] == 0, "a sub-ticket was split again"
    grandchildren = [t for t in tasks.list_tasks(root)
                     if t.get("parent") not in (None, parent["id"])]
    assert grandchildren == []
    assert tasks.get_task(root, child["id"])["triage"]["decision"] != "split"


def test_a_split_that_names_one_piece_is_not_a_split(root: Path, monkeypatch) -> None:
    _stub(monkeypatch, _reply(verdict="split", reason="hm", subtickets=[
        {"title": "Just the one", "objective": "x", "output": "y"}]))
    tasks.add(root, title="A ticket", body="Objective: all of it.")
    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["counts"]["split"] == 0
    assert [t for t in tasks.list_tasks(root) if t.get("parent")] == []


# --- selection and failure ---------------------------------------------------

def test_only_todo_tickets_are_selected(root: Path, monkeypatch) -> None:
    _stub(monkeypatch, _reply())
    body = READY
    live = tasks.add(root, title="A live one", body=body)
    for status in ("draft", "doing", "in_review", "done", "parked"):
        t = tasks.add(root, title=f"A {status} one", body=body)
        tasks.update(root, t["id"], status=status)

    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["due"] == 1
    assert [d["id"] for d in out["decisions"]] == [live["id"]]


def test_an_engine_failure_leaves_the_ticket_due(root: Path, monkeypatch) -> None:
    """The safe failure for a triager is to leave the ticket alone. Marking it
    because the model was down would retire it until someone rewrote it."""
    t = _thin_ticket(root)

    def boom(task):
        raise ValueError("the scope review did not return a usable answer")

    out = triager.run_queue_triage(SCOPE, dry_run=False, reviewer=boom)
    assert out["decided"] == 0
    assert len(out["errors"]) == 1
    fresh = tasks.get_task(root, t["id"])
    assert fresh["triage"] is None and tasks.triage_due(fresh) is True


def test_an_unresolvable_scope_writes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(triager, "_task_root", lambda scope_key: None)
    out = triager.run_queue_triage("project:nope", dry_run=False)
    assert out["decided"] == 0
    assert out["skipped"] == "no task store for this scope"


def test_the_mark_does_not_fence_a_running_worker(root: Path, monkeypatch) -> None:
    """Recording a decision is not an edit. A pass over the queue must not yank
    the claim out from under anything mid-run."""
    _stub(monkeypatch, _reply())
    t = tasks.add(root, title="A fine ticket",
                  body=READY)
    tasks.claim(root, t["id"], "job-1")
    # Back to `todo` so the pass selects it; the token it is holding is whatever
    # that left behind, and the triage mark must not move it.
    token = tasks.update(root, t["id"], status="todo")["claim_token"]

    triager.run_queue_triage(SCOPE, dry_run=False)
    assert tasks.get_task(root, t["id"])["claim_token"] == token


def test_every_ticket_is_reported_as_it_is_decided(root: Path, monkeypatch) -> None:
    """A pass is one model call per ticket, so a preview that prints only at the
    end reads as hung for a quarter of an hour. Every ticket looked at reports,
    including the ones that were skipped."""
    _stub(monkeypatch, THIN)
    for i in range(3):
        _thin_ticket(root, i)
    skipped = _thin_ticket(root, 9)
    tasks.update(root, skipped["id"], awaiting={"ask": "Which one?", "kind": "decision"})

    seen: list[dict] = []
    out = triager.run_queue_triage(SCOPE, dry_run=True, progress=seen.append)
    # Everything CONSIDERED reports, including the blocked one...
    assert len(seen) == 4
    assert [r["decision"] for r in seen].count("skipped") == 1
    assert all(r.get("title") for r in seen)
    # ...but `looked_at` is the MODEL-CALL budget, and a blocked ticket costs no
    # call. These two used to be asserted equal, which is exactly the conflation
    # that let permanently-blocked tickets eat the per-pass budget forever.
    assert out["looked_at"] == 3
    assert out["already_blocked"] == 1


def test_the_queue_vocabulary_matches_the_stores(root: Path) -> None:
    assert triager.QUEUE_DECISIONS == tasks.TRIAGE_DECISIONS
    assert triager.MAX_QUEUE_TICKETS_PER_PASS == 20
    assert triager.MAX_SPLIT_PER_PASS == 5
    assert triager.MAX_UNDERSPECIFIED_PER_PASS == 5


# --- the starvation wall ------------------------------------------------------
# A blocked ticket is deliberately left UNMARKED (a second question would clobber
# the first), so it stays `triage_due` forever. While it was skipped INSIDE the
# loop it still consumed one of the twenty look slots, and candidates sort
# oldest-first, so the permanent ones sorted to the front. Once twenty `todo`
# tickets carried an unanswered block, every later pass decided nothing, for
# good, and a ticket filed after the wall formed was never reached.
#
# The triager builds that wall by itself: it writes `awaiting` on a `todo` ticket
# without changing status, five per pass.

def test_blocked_tickets_do_not_consume_the_look_budget(root: Path, monkeypatch) -> None:
    _stub(monkeypatch, THIN)
    blocked = [_thin_ticket(root, i) for i in range(triager.MAX_QUEUE_TICKETS_PER_PASS)]
    for t in blocked:
        tasks.update(root, t["id"], awaiting={"ask": "Which one?", "kind": "missing-info"})
    fresh = _thin_ticket(root, 99)

    out = triager.run_queue_triage(SCOPE, dry_run=True)
    assert out["already_blocked"] == triager.MAX_QUEUE_TICKETS_PER_PASS
    assert out["looked_at"] == 1, "the wall ate the budget"
    assert [d["id"] for d in out["decisions"] if d["decision"] != "skipped"] == [fresh["id"]]


def test_a_wall_of_blocked_tickets_never_starves_a_later_arrival(root: Path, monkeypatch) -> None:
    """The reproduction, end to end: build the wall, then file work behind it."""
    _stub(monkeypatch, THIN)
    for i in range(triager.MAX_QUEUE_TICKETS_PER_PASS + 5):
        t = _thin_ticket(root, i)
        tasks.update(root, t["id"], awaiting={"ask": "Which one?", "kind": "missing-info"})
    later = _thin_ticket(root, 500)
    out = triager.run_queue_triage(SCOPE, dry_run=False)
    assert out["decided"] == 1
    assert tasks.triage_due(tasks.get_task(root, later["id"])) is False


def test_answering_a_block_brings_the_ticket_back(root: Path, monkeypatch) -> None:
    """Clearing `awaiting` is what re-admits it. Editing the body alone does NOT,
    even though it moves the spec hash: that was the escape the old comment
    claimed and the store does not provide."""
    _stub(monkeypatch, THIN)
    t = _thin_ticket(root, 1)
    tasks.update(root, t["id"], awaiting={"ask": "Which one?", "kind": "missing-info"})
    assert triager.run_queue_triage(SCOPE, dry_run=True)["looked_at"] == 0

    tasks.update(root, t["id"], body="Objective: rewritten entirely, still blocked.")
    assert triager.run_queue_triage(SCOPE, dry_run=True)["looked_at"] == 0, \
        "a body edit must not re-admit a ticket that is still waiting on a person"

    tasks.update(root, t["id"], awaiting={})
    assert triager.run_queue_triage(SCOPE, dry_run=True)["looked_at"] == 1
