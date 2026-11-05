"""What the context meter shows between a compaction and the next model reply.

The meter's number is the prompt size the model reports with each reply. A
compaction lands mid-turn (`compact_boundary`), and the next reply can be a
minute away on a local model with a long prefill, so until it arrives the meter
holds the pre-compaction figure. The boundary event carries `post_tokens`, the
size of the summary that replaced the history, but the window the next call
reads is that plus the system prompt, tool schemas and memory, which the
boundary does not count.

So the figure shown at the boundary is an ESTIMATE:

    post_tokens + overhead

where `overhead` is what the previous compaction on this thread taught us: the
first real reading after it, minus the `post_tokens` known then. With none
remembered, `post_tokens` alone. A real reading always replaces the estimate.
"""
from __future__ import annotations

import logging

from services import chat_store

log = logging.getLogger("workshop.compaction_context")


def post_tokens_of(payload: dict) -> int:
    """The summary size the boundary reports, 0 when it reports none."""
    meta = payload.get("compact_metadata")
    if not isinstance(meta, dict):
        return 0
    value = meta.get("post_tokens")
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def estimate_window(post_tokens: int, overhead: int) -> int:
    return post_tokens + max(0, overhead) if post_tokens > 0 else 0


def remembered_overhead(thread_id: str) -> int:
    try:
        thread = chat_store.get_thread(thread_id) or {}
    except Exception:
        return 0
    value = thread.get("context_overhead")
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def on_boundary(turn, meta: dict, state: dict, payload: dict) -> int:
    """Replace the stale reading with the estimate. Returns the estimate, or 0
    when the boundary carried no `post_tokens` and the stored reading is left
    alone."""
    post = post_tokens_of(payload)
    if post <= 0:
        return 0
    estimate = estimate_window(post, remembered_overhead(turn.thread_id))
    state["ctx_post_tokens"] = post
    try:
        chat_store.set_thread_context_tokens(
            turn.thread_id, estimate, measured_session=meta.get("session_id"))
    except Exception as e:  # pragma: no cover - the meter must not cost a turn
        log.debug("turn %s: estimate persist failed: %s", turn.id, e)
    return estimate


def on_reading(thread_id: str, state: dict, reading: int) -> None:
    """The first real main-conversation reading after a boundary teaches the
    overhead. A reading below the summary itself cannot be the window that
    contains it, so it teaches nothing."""
    post = state.pop("ctx_post_tokens", 0)
    if not post or reading <= post:
        return
    try:
        chat_store.set_thread_context_overhead(thread_id, reading - post)
    except Exception as e:  # pragma: no cover
        log.debug("thread %s: overhead persist failed: %s", thread_id, e)
