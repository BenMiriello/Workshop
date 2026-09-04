"""`armed = on-ready-ticket` actually advances a thread.

WHAT WAS WRONG. `chat_thread.armed` landed with four values, a check constraint
and a PATCH route that validates it, and no reader anywhere in the tree: the
audit of 2026-09-04 measured `grep -rln armed backend/services/` finding only
`scope_execution.is_armed_root` (a different sense entirely). So the field the
settled model calls "the ONLY field that decides whether a thread works unasked"
decided nothing, and an operator arming a thread changed nothing about it.

WHAT IS ASSERTED HERE. The four properties `2f1b6a66` names, against the real
seed store: an armed thread with ready work is seeded exactly once; a second pass
while that seed is still pending adds nothing; an `off` thread with the same
ready work is never seeded; an armed thread whose scope has no ready ticket is
never seeded. Plus the one that matters most for what may run unattended at all:
the readiness question is asked with `unattended=True`, which is the conjunction
of arming, execution kind and spec readiness -- not "the next open ticket".

The ticket store is the one thing stubbed, and only because reaching it means
reaching this installation's Postgres tenant, which `conftest`'s guard correctly
refuses a test. Everything else -- the threads, their arming, the seeds -- is the
real store under a tmp runtime root.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import chat_drain, chat_queue, chat_store, ownership  # noqa: E402
from services import task_scope, tasks  # noqa: E402

READY = {"id": "abc12345-0000", "text": "Widen the widget\nmore detail here",
         "status": "todo", "execution": "code"}


@pytest.fixture()
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(ownership, "_RUNTIME", tmp_path)
    ownership.set_current_owner(1)
    try:
        yield tmp_path
    finally:
        ownership.clear_current_owner()


@pytest.fixture()
def store(owned, tmp_path, monkeypatch):
    """A resolvable scope whose ticket store answers a fixed question.

    `next_ready` is replaced rather than driven, because driving it means
    `tasks.add`, which resolves an organization through `tenancy` -- i.e. the
    operator's real tenant, which conftest refuses. The replacement records HOW
    it was asked, which is the part this wiring is responsible for.
    """
    root = tmp_path / "scope"
    root.mkdir()
    calls: list[dict] = []

    def fake_next_ready(r, **kw):
        calls.append({"root": Path(r), **kw})
        return dict(READY)

    monkeypatch.setattr(task_scope, "root_for_context", lambda ck: root)
    monkeypatch.setattr(tasks, "next_ready", fake_next_ready)
    return {"root": root, "calls": calls}


def _thread(armed: str) -> str:
    tid = chat_store.create_thread("general")["id"]
    chat_store.set_thread_arming(tid, armed=armed)
    assert chat_store.get_thread(tid)["armed"] == armed
    return tid


def test_an_armed_thread_with_ready_work_is_seeded_once(store):
    tid = _thread("on-ready-ticket")

    assert chat_drain.seed_ready_ticket_threads() == 1
    seed = chat_queue.read_continuation(tid)
    assert seed is not None
    assert "abc12345" in seed["text"]
    # The ticket, not a copy of it: the seed names it and stops.
    assert "more detail here" not in seed["text"]


def test_a_second_pass_does_not_stack_a_second_seed(store):
    tid = _thread("on-ready-ticket")
    assert chat_drain.seed_ready_ticket_threads() == 1
    first = chat_queue.read_continuation(tid)

    assert chat_drain.seed_ready_ticket_threads() == 0
    assert chat_queue.read_continuation(tid) == first


def test_an_off_thread_with_the_same_ready_work_is_never_seeded(store):
    tid = _thread("off")

    assert chat_drain.seed_ready_ticket_threads() == 0
    assert chat_queue.read_continuation(tid) is None
    # And the ticket store was not even asked: an unarmed thread is not a
    # question about tickets.
    assert store["calls"] == []


def test_an_armed_thread_whose_scope_has_no_ready_ticket_is_not_seeded(
        store, monkeypatch):
    tid = _thread("on-ready-ticket")
    monkeypatch.setattr(tasks, "next_ready", lambda r, **kw: None)

    assert chat_drain.seed_ready_ticket_threads() == 0
    assert chat_queue.read_continuation(tid) is None


def test_a_thread_running_a_turn_is_not_given_a_second_reason_to_wake(
        store, monkeypatch):
    """The one-at-a-time rule `wakeups.fire_due` follows. Without it an armed
    thread whose scope keeps answering "ready" is re-seeded every five seconds
    for the whole length of the turn it already started."""
    tid = _thread("on-ready-ticket")
    monkeypatch.setattr(chat_store, "active_turn_id", lambda t: "turn-1")

    assert chat_drain.seed_ready_ticket_threads() == 0
    assert chat_queue.read_continuation(tid) is None


def test_readiness_is_asked_unattended_which_is_the_narrow_predicate(store):
    """THE SELECTION RULE, asserted where it is chosen.

    `unattended=True` is what makes this incapable of picking "the next open
    ticket": it requires the scope to be armed, the ticket's `execution` to be
    `code`, and `ticket_readiness.check` to pass on its spec, all before
    priority order is even consulted. Asking without it would hand an armed
    thread the whole backlog, which is the specific outcome the operator refused
    when he turned autonomy off.
    """
    _thread("on-ready-ticket")
    chat_drain.seed_ready_ticket_threads()

    assert store["calls"], "the ticket store was never asked"
    assert all(c.get("unattended") is True for c in store["calls"]), store["calls"]


def test_an_unresolvable_scope_is_skipped_rather_than_guessed(store, monkeypatch):
    """`root_for_context` is the chokepoint that refuses a scope this owner may
    not reach and refuses the platform's own tree under ADR 0014. A refusal is
    None, and None must mean "no work here", not "use some other root"."""
    tid = _thread("on-ready-ticket")
    monkeypatch.setattr(task_scope, "root_for_context", lambda ck: None)

    assert chat_drain.seed_ready_ticket_threads() == 0
    assert chat_queue.read_continuation(tid) is None


def test_the_resident_loop_is_what_runs_the_pass():
    """A producer nobody calls leaves the field exactly as inert as it was. The
    call site is asserted, not assumed -- that is the whole failure this closes."""
    src = (Path(__file__).resolve().parents[1] / "services" / "chat_drain.py").read_text()
    loop = src.split("async def continuation_loop", 1)[1]
    assert "seed_ready_ticket_threads()" in loop
