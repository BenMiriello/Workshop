"""One exclusive operation per thread, and admission that respects it.

A chat thread has exactly two kinds of long-lived work: a RUN (a turn being
driven) and an OPERATION on the stored conversation (a /compact fold, a /clear
cut). Runs already serialize against each other through the claim in
build_streaming. Operations serialized against nothing: a fold spent thirty
seconds summarizing while the send path happily started a turn on the pre-fold
prompt, the fold then landed its boundary mid-turn, and the reply committed on
the far side of the divider from the messages it answered. Measured 2026-08-14
03:20 on thread 57e6c4b8, and it is the shape of half the "chat ordering is
broken" reports before it.

The rule this module carries:

  A thread admits ONE operation at a time, an operation LANDS only on a quiet
  thread, and a message that arrives while one is pending QUEUES behind it.

Concretely:

  * `begin` registers the operation. From that moment the send path routes new
    turns onto the durable queue (routes/chat.py `_send`) and the drain refuses
    to start one (chat_drain).
  * The operation's cheap reads and model calls happen while registered; its
    WRITES (the landing: summary + cut + boundary + detach) happen only when no
    run is live, via `wait_quiet`.
  * `end` unregisters and kicks the drain, so anything that queued behind the
    operation goes out in order, on the post-operation prompt.

This registry is in-process memory, deliberately. The backend is one process;
an operation is an HTTP request plus at most one background landing task, and a
restart kills both halves together, leaving only the append-only log, which is
always coherent. Persisting "an op is pending" would create exactly the stale
flag this module exists to delete. The cost is honest and documented: a fold
deferred past a restart simply never lands, and the operator compacts again.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# thread_id -> operation name ("compact" | "clear"). One at most.
_PENDING: dict[str, str] = {}

# Landing tasks, HELD. asyncio keeps only a weak reference to a task, and a
# collected landing would leave _PENDING set forever: every send queued, the
# drain refusing, until restart. Same bug class chat_naming holds its tasks for.
_LANDINGS: set = set()


def land_in_background(thread_id: str, coro) -> None:
    """Run an operation's landing as a held background task. The coroutine owns
    the registration: it must call end(thread_id) in its finally."""
    task = asyncio.get_running_loop().create_task(
        coro, name=f"op-land:{thread_id}")
    _LANDINGS.add(task)
    task.add_done_callback(_LANDINGS.discard)


def pending(thread_id: str) -> str | None:
    """The operation this thread is inside, or None."""
    return _PENDING.get(thread_id)


def begin(thread_id: str, op: str) -> bool:
    """Register an operation. False means one is already in flight; the caller
    reports that rather than stacking a second fold on the first."""
    if thread_id in _PENDING:
        return False
    _PENDING[thread_id] = op
    return True


def end(thread_id: str) -> None:
    """Unregister, then wake the queue: messages that arrived during the
    operation are waiting on exactly this."""
    op = _PENDING.pop(thread_id, None)
    if op is None:
        return
    try:
        from services import chat_drain
        chat_drain.drain_soon(thread_id, reason=f"{op} finished")
    except Exception as e:  # pragma: no cover - drain is best-effort by contract
        log.debug("thread_ops: post-%s drain kick failed for %s: %s", op, thread_id, e)


async def wait_quiet(thread_id: str) -> None:
    """Return when no run is live on this thread.

    Waits on the turn's own completion event, re-checking every 60s. The
    re-check is not a timeout on the turn: it is what covers a run whose
    `finished` event never fires, which is real, not defensive. A VOICE turn
    claims the thread through the same registry but is driven by voice_worker,
    which releases the claim without touching the shell Turn's event; the
    re-check sees `active_run_id` cleared and returns. Worst case a landing
    lags a voice turn's end by one re-check interval.
    """
    from services import build_streaming, chat_store

    while True:
        rid = chat_store.active_run_id(thread_id)
        if not rid or not build_streaming.run_is_live(rid):
            return
        turn = build_streaming.get_turn(rid)
        if turn is None:
            # run_is_live and get_turn read the same registry in the same
            # tick; only a prune racing between the two calls lands here.
            await asyncio.sleep(0.5)
            continue
        try:
            await asyncio.wait_for(turn.finished.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            continue
