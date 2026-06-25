"""Auto-generate a short chat title from the first exchange.

Shared by the data chat path (POST /messages) and the build streaming path
(/turns). A new thread shows a first-message snippet as a placeholder; once the
first assistant reply lands we replace it with a concise model-generated title.
Best-effort: any failure leaves the existing title untouched, and we never
clobber a title the operator set in the meantime.
"""
from __future__ import annotations

import asyncio
import logging

from services import chat_store, llm_router

log = logging.getLogger("workshop.chat_naming")


def is_blank_title(title: str | None) -> bool:
    """A title that should be auto-generated: empty or the 'New chat' seed. The
    first-message snippet is stored separately (_seed_title) and only used for
    display, so the real title stays blank until we name it here."""
    return (title or "").strip() in ("", "New chat")


def _first_content(messages: list[dict], role: str) -> str:
    for m in messages:
        if m.get("role") == role and (m.get("content") or "").strip():
            return m["content"]
    return ""


def generate_title(thread_id: str) -> None:
    """Blocking: name the thread from its first exchange, if it is still unnamed
    and exactly one assistant reply has landed. Runs in a worker thread (the
    model call blocks). Reads the messages itself so either chat path can call it
    with just the thread id."""
    t = chat_store.get_thread(thread_id)
    if t is None or not is_blank_title(t.get("title")):
        return
    msgs = t.get("messages", [])
    assistants = [
        m for m in msgs
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    # Only after the FIRST assistant reply (not on every later turn).
    if len(assistants) != 1:
        return
    first_user = _first_content(msgs, "user")
    if not first_user:
        return
    first_assistant = assistants[0].get("content", "")

    prompt = (
        "Generate a concise chat title (at most 6 words, Title Case, no quotes, "
        "no trailing punctuation, no emoji) summarizing this exchange. Output "
        "ONLY the title.\n\n"
        f"User: {first_user[:600]}\n\n"
        f"Assistant: {first_assistant[:600]}"
    )
    try:
        result = llm_router.call_cloud(
            prompt,
            model=llm_router.TIER_HAIKU,
            tools_disabled=True,
            max_turns=1,
            timeout_s=60,
        )
    except Exception as e:
        log.info("auto-name skipped (model call failed) for %s: %s", thread_id, e)
        return

    title = (result.get("text") or "").strip().strip('"').strip()
    title = title.splitlines()[0].strip() if title else ""  # first line only
    words = title.split()
    if len(words) > 6:
        title = " ".join(words[:6])
    if not title:
        return

    # Final guard: only rename if still blank (operator may have renamed).
    cur = chat_store.get_thread(thread_id)
    if cur is not None and is_blank_title(cur.get("title")):
        chat_store.rename_thread(thread_id, title)
        log.info("auto-named thread %s -> %r", thread_id, title)


def schedule(thread_id: str) -> None:
    """Fire-and-forget auto-naming. Safe to call from an async handler (the
    common case) or a plain function; never delays the caller."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(asyncio.to_thread(generate_title, thread_id))
    else:  # pragma: no cover — no running loop (rare): just do it inline.
        generate_title(thread_id)
