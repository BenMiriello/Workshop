"""Sending what the queue is holding, without a browser in the loop.

`chat_queue` is where a message waits. This is what makes it leave.

THE WHOLE POINT: nothing here needs a client. The old arrangement drained the
queue from the browser's stream-finished handler, which meant delivery of a
message the operator had already sent depended on a tab being open, bound to
that thread, and awake at the instant the turn ended. Every other way a turn can
end — the tab moved to another chat, the tab closed, the laptop slept, the
stream dropped past its reconnect budget, the backend restarted, the operator
picked up a different device — left the message sitting in that browser looking
patient forever. The turn ends HERE, so the queue drains HERE.

Triggers, all of which reduce to "the thread might be idle now":

  * a turn finalizes (`build_streaming._finalize_turn`) — the ordinary case
  * a run is released or lost without finalizing — a crash, a restart mid-turn
  * a message is enqueued and it turns out nothing is running after all
  * backend startup, for anything left queued across a restart

They are all safe to fire spuriously: the drain re-reads the world, holds a
per-thread lock, and does nothing at all unless the thread is genuinely idle
with something waiting.

### The starter is injected

Forming a turn is `routes/chat.py`'s job (prompt assembly, provider resolution,
mentions, the run claim). Importing that from here, or from build_streaming,
would be a cycle. So the send path registers itself at import time and this
module only decides WHEN.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from services import chat_queue

log = logging.getLogger(__name__)

# (thread_id, entries) -> awaitable. Returns True when the batch has been
# recorded and a turn is running for it. Registered by routes/chat.py.
Starter = Callable[[str, list[dict]], Awaitable[bool]]

_starter: Starter | None = None

_locks: dict[str, asyncio.Lock] = {}


def register_starter(fn: Starter) -> None:
    global _starter
    _starter = fn


def _lock_for(thread_id: str) -> asyncio.Lock:
    lk = _locks.get(thread_id)
    if lk is None:
        lk = _locks[thread_id] = asyncio.Lock()
    return lk


async def drain(thread_id: str, *, reason: str = "") -> bool:
    """Send the next batch if the thread is idle and something is waiting.

    Returns True if a turn was started. Never raises: every caller is a
    lifecycle hook on some other operation, and a queue that could break a turn
    commit would be worse than the queue this replaces.
    """
    if _starter is None:  # pragma: no cover - set at import by routes/chat
        return False
    lock = _lock_for(thread_id)
    if lock.locked():
        # Another drain is mid-flight for this thread. It will re-read the queue
        # when it is done, so a second one would either duplicate its batch or
        # find nothing; either way there is nothing useful to do here.
        return False
    async with lock:
        batch = chat_queue.next_batch(thread_id)
        if not batch:
            return False
        # Asked last, and read fresh: a run can start between the queue read and
        # here (another device sending, an autonomous turn), and starting a
        # rival is the one outcome worse than waiting.
        from services import chat_store
        if chat_store.active_turn_id(thread_id):
            return False
        # A pending fold/clear holds the queue the same way a live run does: a
        # turn started now would read the pre-operation prompt and race the
        # operation's writes. thread_ops.end() kicks this drain when it lands.
        from services import thread_ops
        if thread_ops.pending(thread_id):
            return False
        ids = [e.get("client_id") for e in batch]
        try:
            started = await _starter(thread_id, batch)
        except Exception as e:
            # The text is still queued: nothing has been dropped, and the entries
            # are exactly where the operator can see them. Park the queue on the
            # reason rather than retrying in a loop that would spend a turn's
            # worth of tokens per attempt against a fault that is not going away
            # on its own.
            log.warning("chat queue drain failed for %s (%s): %s", thread_id, reason, e)
            chat_queue.set_held(thread_id, True, error=str(e))
            return False
        if not started:
            return False
        # ONLY NOW. The entries leave the queue after the turn that carries them
        # has been recorded, never before: a crash in between leaves them queued
        # and they go out again, which is the failure everyone can live with.
        # The other order loses them.
        chat_queue.drop(thread_id, [i for i in ids if i])
        log.info("chat queue: sent %d queued message(s) on %s (%s)",
                 len(batch), thread_id, reason or "drain")
        return True


def drain_soon(thread_id: str, *, reason: str = "") -> None:
    """Fire-and-forget drain, for callers that must not wait on a turn starting.

    `_finalize_turn` is the main one: it is committing a reply and waking
    whoever is blocked on that, and it cannot spend the assembly of the next
    turn's prompt inside that critical path.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - off-loop caller
        return
    # HELD: asyncio keeps only a weak reference, and a collected drain task is
    # a queued message waiting for the next poll with no error anywhere. Same
    # class as the naming task that silently never ran (93768fd).
    task = loop.create_task(_guarded(thread_id, reason))
    _DRAIN_TASKS.add(task)
    task.add_done_callback(_DRAIN_TASKS.discard)


_DRAIN_TASKS: set = set()


async def _guarded(thread_id: str, reason: str) -> None:
    try:
        await drain(thread_id, reason=reason)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("chat queue drain task failed for %s: %s", thread_id, e)


def request_continuation(thread_id: str, *, seed: str, dedupe_id: str) -> bool:
    """Ask a thread to take its own next turn after a background run finished.

    This is the write half of fire-and-continue. A run's completion handler
    (services/run_report) calls it, and that handler runs in the DETACHED WORKER
    process, not the backend, so all this can safely do is persist the seed: the
    enqueue writes the queue file (idempotent on `dedupe_id`, so re-finishing the
    same job cannot double-fire), and `drain_soon` starts the turn immediately
    IF we happen to be on the backend's event loop, or no-ops if we are not. When
    it no-ops, the backend's `continuation_loop` picks the seed up on its next
    pass. Either way the turn starts exactly once, and survives a restart in
    between because the seed is on disk. Returns True if the seed was persisted.
    """
    if not (seed or "").strip():
        return False
    try:
        chat_queue.enqueue(
            thread_id,
            text=seed,
            client_id=f"auto-{dedupe_id}",
            auto=True,
            sender="platform",
        )
    except Exception as e:  # pragma: no cover - defensive: a run must still close
        log.warning("continuation seed for %s could not be queued: %s", thread_id, e)
        return False
    drain_soon(thread_id, reason="continuation")
    return True


def _all_owner_ids() -> list[int]:
    """Every owner with a runtime store. The chat stores are per-owner and
    resolved through an `ownership` ContextVar, so a background loop that means
    to touch chats has to pin each owner in turn; nothing is pinned by default
    (ownership.current_owner() would raise). Same directory scan run_report uses
    to find a thread's owner."""
    from services import ownership
    out: list[int] = []
    try:
        for d in (ownership._RUNTIME / "users").iterdir():
            if d.is_dir():
                try:
                    out.append(int(d.name))
                except ValueError:
                    continue
    except OSError:  # pragma: no cover - defensive
        pass
    return out


async def continuation_loop(*, interval: float = 5.0) -> None:
    """Start the turn for any thread holding a continuation seed, once it is idle.

    The seed is usually written by a worker process that cannot reach this event
    loop (see request_continuation), so nothing else would ever start that turn.
    This is that trigger: it is the backend, it outlives every run, and `drain`
    already refuses to start while a turn or a thread-op is in flight, so a seed
    simply waits here until the thread is genuinely free. Only threads that have
    a seed are touched, so an idle system does no work.

    Runs with NO owner pinned, so it pins each owner in turn before reading that
    owner's chat stores (see _all_owner_ids). Setting the owner on this task
    before awaiting `drain` keeps it pinned through the drain and the turn spawn,
    because a ContextVar set on a task is held across that task's awaits.
    """
    from services import ownership
    while True:
        await asyncio.sleep(interval)
        try:
            for owner_id in _all_owner_ids():
                ownership.set_current_owner(owner_id)
                try:
                    for tid in chat_queue.threads_with_queues():
                        try:
                            if chat_queue.has_auto(tid):
                                await drain(tid, reason="continuation")
                        except Exception as e:  # pragma: no cover - defensive
                            log.warning("continuation drain failed for %s: %s", tid, e)
                finally:
                    ownership.clear_current_owner()
        except Exception as e:  # pragma: no cover - defensive
            log.warning("continuation loop pass failed: %s", e)


async def drain_all(*, reason: str = "startup") -> int:
    """Every thread holding a queue, across every owner. Runs at startup.

    A backend that went down while a turn was running comes back with the turn
    gone and the queue still there. Nothing else would ever notice: the browser
    that queued the message may never open that thread again.

    Pins each owner in turn: the queue stores are per-owner behind an ownership
    ContextVar, and this runs with none pinned, so an un-owned pass would raise
    OwnerUnset off the first store read and deliver nothing. (It did, silently,
    until 2026-08-20 — the operator-queue drains that DO work all fire from
    already-owned contexts like turn-finalize, so the gap never showed.)
    """
    from services import ownership
    sent = 0
    for owner_id in _all_owner_ids():
        ownership.set_current_owner(owner_id)
        try:
            for tid in chat_queue.threads_with_queues():
                try:
                    if await drain(tid, reason=reason):
                        sent += 1
                except Exception as e:  # pragma: no cover - defensive
                    log.warning("startup drain failed for %s: %s", tid, e)
        finally:
            ownership.clear_current_owner()
    if sent:
        log.info("chat queue: delivered queued messages on %d thread(s) at %s", sent, reason)
    return sent
