"""Mirror writes for one thread land in the order they were appended.

THE BUG THIS PINS, measured 2026-08-14 by driving four appends through the real
path: `chat_log._mirror` fired every write as an independent task, so writes for
one thread ran CONCURRENTLY. Each one parents its row to the branch's current
head, so two at once read the same head. Worse at the start of a conversation,
where the thread row is created lazily by whichever write arrives first: the
first few events of a new chat all found no row, all tried to create one, and
every loser died on `uq_chat_thread_external` and dropped its message from the
tree. Three of four messages never reached it, and the branch head was left
pointing at the first one.

Rare at human typing speed. Not rare at the start of a thread, which is where a
message and the reply to it arrive close together.

A second, quieter bug in the same lines: `_MIRROR_TASKS` was referenced and never
defined, so the NameError landed in a bare `except` and was logged at DEBUG. The
task had already been created by then, so the mirror still ran and nothing looked
wrong; what was missing was the HOLD, leaving every mirror write collectable
mid-flight, which is exactly the silently-missing row the backfill exists to
repair.

No database here on purpose: what broke is the SCHEDULING, and it is testable as
scheduling.
"""
import asyncio

import pytest

from services import chat_log


@pytest.fixture(autouse=True)
def clean():
    chat_log._MIRROR_TASKS.clear()
    chat_log._MIRROR_CHAIN.clear()
    yield
    chat_log._MIRROR_TASKS.clear()
    chat_log._MIRROR_CHAIN.clear()


def _run(body):
    return asyncio.run(body())


def _spy(monkeypatch, order, delay=0.01):
    """Stand in for the real mirror write, slow enough that a concurrent
    scheduler would visibly interleave."""
    async def record_message(thread_ref, ev, meta=None):
        order.append(("start", thread_ref, ev["seq"]))
        await asyncio.sleep(delay)
        order.append(("end", thread_ref, ev["seq"]))

    import services.chat_branches as cb
    monkeypatch.setattr(cb, "record_message", record_message)


def test_writes_for_one_thread_do_not_overlap(monkeypatch):
    order: list = []
    _spy(monkeypatch, order)

    async def body():
        for seq in range(1, 5):
            chat_log._mirror("t1", {"seq": seq, "kind": "message"}, {})
        await chat_log.drain_mirror_writes()

    _run(body)
    # Strictly start,end,start,end...: no write begins before its predecessor
    # finished, which is what "parents to the current head" requires.
    assert [k for k, _, _ in order] == ["start", "end"] * 4
    assert [s for k, _, s in order if k == "start"] == [1, 2, 3, 4]


def test_a_failed_write_does_not_stall_the_ones_behind_it(monkeypatch):
    """The tree missing one row is recoverable. A stalled mirror is not."""
    done: list = []

    async def record_message(thread_ref, ev, meta=None):
        if ev["seq"] == 2:
            raise RuntimeError("mirror exploded")
        done.append(ev["seq"])

    import services.chat_branches as cb
    monkeypatch.setattr(cb, "record_message", record_message)

    async def body():
        for seq in range(1, 5):
            chat_log._mirror("t1", {"seq": seq, "kind": "message"}, {})
        await chat_log.drain_mirror_writes()

    _run(body)
    assert done == [1, 3, 4]


def test_different_threads_still_run_concurrently(monkeypatch):
    """The requirement is order WITHIN a thread. Serializing across threads
    would put every chat behind every other chat's mirror."""
    order: list = []
    _spy(monkeypatch, order)

    async def body():
        chat_log._mirror("t1", {"seq": 1, "kind": "message"}, {})
        chat_log._mirror("t2", {"seq": 1, "kind": "message"}, {})
        await chat_log.drain_mirror_writes()

    _run(body)
    assert [k for k, _, _ in order] == ["start", "start", "end", "end"]


def test_the_tasks_are_held(monkeypatch):
    """asyncio keeps only a weak reference; an unheld task can be collected
    mid-write."""
    _spy(monkeypatch, [])

    async def body():
        chat_log._mirror("t1", {"seq": 1, "kind": "message"}, {})
        assert len(chat_log._MIRROR_TASKS) == 1
        await chat_log.drain_mirror_writes()
        assert not chat_log._MIRROR_TASKS

    _run(body)


def test_the_chain_entry_is_released_when_the_thread_goes_quiet(monkeypatch):
    """Otherwise the registry grows one entry per thread, forever, each holding
    a finished task alive."""
    _spy(monkeypatch, [])

    async def body():
        for seq in (1, 2):
            chat_log._mirror("t1", {"seq": seq, "kind": "message"}, {})
        await chat_log.drain_mirror_writes()
        assert chat_log._MIRROR_CHAIN == {}

    _run(body)


def test_no_loop_means_no_mirror_and_no_error():
    """Called off the loop (a script, a worker thread), it declines quietly:
    the log line is already committed and the backfill can reconstruct place."""
    chat_log._mirror("t1", {"seq": 1, "kind": "message"}, {})
    assert not chat_log._MIRROR_TASKS
