"""Telling every open client that a thread changed, the moment it changes.

WHY THIS EXISTS. The chat learned about anything it had not done itself by
POLLING the sync endpoint every ten seconds. A turn started on another device, a
message sent from a phone, a queued message the server delivered, a compaction:
all of it arrived up to ten seconds late, on every client but the one that
caused it. The operator's words for it, 2026-08-12: "it doesn't show up in the
chat message in the other device until I refresh".

There was already a stream, but it was the wrong shape: `/chat/turns/{id}/stream`
carries ONE RUN's output to whoever knows that run's id. A client that has not
started a turn has no id to subscribe to, so the only thing it could do was ask
again in ten seconds. The thing worth subscribing to is the THREAD.

### The design

A version number per thread, bumped by every write that changes what a client
would render, and an `asyncio.Event` per thread that a request can wait on.
`GET /chat/threads/{id}/stream` waits and emits `changed`; the client then calls
the same `sync()` it already had. The stream says WHETHER, never WHAT — the
sync endpoint remains the single source of the transcript, the queue and the
cut, so this adds a trigger and not a second way to learn what a chat contains.
That is deliberate: a stream that carried content would be a second answer to
"what is in this chat", and every reorder and eaten-message bug in this system's
history was two owners of that answer disagreeing.

### Where the bumps come from

The three writes that can change a rendered chat, and nothing else:

    chat_log.append_sync      a message, a run marker, a boundary, a scope change
    chat_log.patch_meta_sync  the active run, the title, the cut, the summary
    chat_queue._write         a message queued, drained, cancelled, held

`write_live_sync` is deliberately NOT one of them. A running turn rewrites its
draft hundreds of times, and that output already has its own stream; bumping on
it would wake every idle client on every token to tell it something it is
already being told.

### It is a hint, not a ledger

A missed bump costs latency, never correctness: the client's cursor-based sync
is idempotent and the poll still runs behind this as a backstop. So nothing here
needs durability, ordering, or delivery guarantees, and a client that was
disconnected for an hour catches up in one request when it reconnects.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# thread_id -> (version, event). The version lets a client that reconnects say
# what it last saw, so a change that landed while it was away wakes it
# immediately instead of after a full wait.
_versions: dict[str, int] = {}
_events: dict[str, asyncio.Event] = {}


def version(thread_id: str) -> int:
    return _versions.get(thread_id, 0)


def _event_for(thread_id: str) -> asyncio.Event:
    ev = _events.get(thread_id)
    if ev is None:
        ev = _events[thread_id] = asyncio.Event()
    return ev


def bump(thread_id: str) -> None:
    """Something a client would render has changed on this thread.

    Never raises and never blocks: every caller is in the middle of a write that
    matters, and a notification failing must not fail the write it is about.
    """
    if not thread_id:
        return
    try:
        _versions[thread_id] = _versions.get(thread_id, 0) + 1
        ev = _events.get(thread_id)
        if ev is None:
            return          # nobody is listening; the version is enough
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Off the loop (a worker thread, a script, a test). Waiters are
            # woken by the loop, and the version has already moved, so a waiter
            # sees the change on its next wake either way.
            return
        ev.set()
    except Exception as e:  # pragma: no cover - defensive
        log.debug("chat change notification failed for %s: %s", thread_id, e)


async def wait(thread_id: str, since: int, timeout: float) -> int:
    """Block until this thread's version passes `since`, or the timeout.

    Returns the current version. A caller that is already behind returns
    immediately, which is what makes a reconnect instant rather than one wait
    behind.
    """
    current = version(thread_id)
    if current > since:
        return current
    ev = _event_for(thread_id)
    ev.clear()
    try:
        await asyncio.wait_for(ev.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    return version(thread_id)


def forget(thread_id: str) -> None:
    """Drop a deleted thread's slot. Waiters are woken first so a client
    watching a chat that has just been deleted is not left hanging until its
    timeout."""
    ev = _events.pop(thread_id, None)
    if ev is not None:
        try:
            ev.set()
        except Exception:  # pragma: no cover - defensive
            pass
    _versions.pop(thread_id, None)
