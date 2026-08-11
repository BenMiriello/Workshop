"""Auto-generate a short chat title once the chat has a nameable topic.

Shared by the data chat path (POST /messages) and the build streaming path
(/turns). A new thread shows a first-message snippet as a placeholder; once an
exchange lands that has a discernible subject we replace it with a concise
model-generated title. The namer reads the LATEST exchange and may answer NONE
when there is no topic yet (a greeting, a bare command), so a thin opener no
longer locks in a meaningless name — it waits for real content, up to a small
attempt cap. Best-effort: any failure leaves the existing title untouched, and
we never clobber a title the operator set in the meantime.
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


def _last_content(messages: list[dict], role: str) -> str:
    """The MOST RECENT non-empty message from a role. Naming reads the latest
    exchange, not a frozen first one: a thin opener (a greeting, a bare command)
    yields no topic, we skip it, and the next turn names from real content
    instead of locking onto the fragment that happened to be first. Because a
    title locks the moment one lands, reading the latest never makes it wander —
    it only lets it start from something worth naming."""
    for m in reversed(messages):
        if m.get("role") == role and (m.get("content") or "").strip():
            return m["content"]
    return ""


# A model asked for "output ONLY the title" occasionally breaks character and
# answers the *conversation* instead — one thread's permanent title ended up
# "I don't have tools available in", the start of a refusal-flavored reply to
# the naming prompt itself, saved verbatim because nothing checked it before
# persisting. These are the shapes that response takes; reject them and let
# the retry-every-turn behavior above try again on the next reply rather than
# locking in whatever the model said.
_NON_TITLE_STARTS = (
    "i don't", "i do not", "i can't", "i cannot", "i'm not able", "i am not able",
    "i'm sorry", "i apologize", "as an ai", "sorry,", "unfortunately",
)

# The namer's own "no topic yet" signal. The exchange may be genuinely
# unnameable (a greeting, an acknowledgement, a bare command), and the model
# is a far better judge of that than any length threshold — a short user turn
# can be perfectly substantive. So the naming prompt is allowed to answer NONE,
# and we treat it exactly like a rejection: skip now, try again next turn.
_NO_TOPIC = "NONE"

# How many times a chat may return "nothing nameable" before we stop asking.
# This bounds the ONE cost of the retry loop — an undeterminable chat would
# otherwise pay a naming call every turn forever. After this it keeps its seed
# / "New chat" fallback. Only CONTENT rejections count; a transient call
# failure (rate limit, restart) does not, so the original "name eventually
# even if the first attempt was unlucky" property still holds.
_MAX_NAME_ATTEMPTS = 3


def _looks_like_a_title(title: str) -> bool:
    return not title.lower().startswith(_NON_TITLE_STARTS)


def compute_title(thread_id: str) -> tuple[str | None, bool]:
    """Blocking: ask a cheap model for a name for this thread.

    Returns (title, counted_rejection):
      - (title, False)  a usable name — apply it.
      - (None, True)    the model saw no nameable topic yet, or broke
                        character. This counts against `_MAX_NAME_ATTEMPTS`.
      - (None, False)   nothing to do, or a TRANSIENT failure (no full exchange
                        yet, a rate limit, a restart). Does NOT count, so an
                        unlucky attempt never spends the budget.

    READ ONLY. Safe in a worker thread: it makes the model call and touches
    nothing. Both the title write (`apply_title`) and the rejection count
    (`_count_rejection`) happen back on the loop. The two were split because a
    ~2s model call between a read and an off-loop write of the thread dropped
    everything the operator sent in that window.

    Reads the LATEST exchange and retries each turn while the chat is unnamed.
    A thin opener (a greeting, a bare command) yields NONE and is skipped; the
    next turn names from real content. A title locks the moment one lands
    (`is_blank_title`), so reading the latest never makes the name wander — it
    only lets naming start from something worth naming, and the attempt cap
    stops it asking forever when nothing ever is.
    """
    t = chat_store.get_thread(thread_id)
    if t is None or not is_blank_title(t.get("title")):
        return None, False
    if int(t.get("_name_attempts", 0) or 0) >= _MAX_NAME_ATTEMPTS:
        return None, False
    msgs = t.get("messages", [])
    last_user = _last_content(msgs, "user")
    last_assistant = _last_content(msgs, "assistant")
    if not last_user or not last_assistant:
        return None, False   # no full exchange to name yet — transient, uncounted

    prompt = (
        "Generate a concise chat title (at most 6 words, Title Case, no quotes, "
        "no trailing punctuation, no emoji) describing what this conversation "
        "is ABOUT. If the exchange has no clear subject yet (a greeting, an "
        "acknowledgement, a bare command, small talk), output exactly NONE "
        "instead. Output only the title, or NONE.\n\n"
        f"User: {last_user[:600]}\n\n"
        f"Assistant: {last_assistant[:600]}"
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
        return None, False   # transient — do not count it

    title = (result.get("text") or "").strip().strip('"').strip()
    title = title.splitlines()[0].strip() if title else ""  # first line only
    if not title or title.rstrip(".").strip().upper() == _NO_TOPIC:
        log.info("auto-name: no nameable topic yet for %s", thread_id)
        return None, True
    if not _looks_like_a_title(title):
        log.info("auto-name skipped (model broke character) for %s: %r", thread_id, title)
        return None, True
    words = title.split()
    if len(words) > 6:
        title = " ".join(words[:6])
    return title, False


def apply_title(thread_id: str, title: str) -> None:
    """Write the generated name, if the chat is still unnamed.

    Must run on the event loop: it writes. The guard re-reads rather than
    trusting what `compute_title` saw, so a name the operator typed during the
    model call wins."""
    cur = chat_store.get_thread(thread_id)
    if cur is not None and is_blank_title(cur.get("title")):
        chat_store.rename_thread(thread_id, title, source="auto")
        log.info("auto-named thread %s -> %r", thread_id, title)


def _count_rejection(thread_id: str) -> None:
    """Record that this turn's naming attempt found no nameable topic. After
    `_MAX_NAME_ATTEMPTS` of these the chat stops asking and keeps its seed /
    "New chat" fallback. Writes, so it runs on the loop; re-reads so a name the
    operator typed meanwhile still wins (an already-named chat is left alone)."""
    cur = chat_store.get_thread(thread_id)
    if cur is None or not is_blank_title(cur.get("title")):
        return
    n = int(cur.get("_name_attempts", 0) or 0) + 1
    chat_store.update_thread(thread_id, _name_attempts=n)


def generate_title(thread_id: str) -> None:
    """Name the thread, start to finish, on the calling thread. For callers that
    are already on the loop and can afford to block (scripts, tests)."""
    title, rejected = compute_title(thread_id)
    if title:
        apply_title(thread_id, title)
    elif rejected:
        _count_rejection(thread_id)


async def name_thread(thread_id: str) -> None:
    """Name the thread without blocking the loop: the model call goes to a
    worker thread, the write stays here."""
    title, rejected = await asyncio.to_thread(compute_title, thread_id)
    if title:
        apply_title(thread_id, title)
    elif rejected:
        _count_rejection(thread_id)


def schedule(thread_id: str) -> None:
    """Fire-and-forget auto-naming. Safe to call from an async handler (the
    common case) or a plain function; never delays the caller."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(name_thread(thread_id))
    else:  # pragma: no cover — no running loop (rare): just do it inline.
        generate_title(thread_id)
