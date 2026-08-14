"""Auto-generate a short chat title once the chat has a nameable topic.

WHEN IT RUNS: as soon as the operator's message lands
(`chat_store.append_message`), and again after each turn commits
(`build_streaming._commit_turn`) in case the first attempt found no subject and
the reply has since supplied one. Both call `schedule()`, which is safe to call
at any time because the namer re-reads the thread and guards itself.

It used to run ONLY at turn commit, and that one fact produced the whole
"naming is inconsistent, sometimes it doesn't run" report. A title could not
exist before the first turn finished, so it inherited that turn's duration:
measured across the real threads, a median of 9.4 minutes after the opening
message, ranging from 1.9 to 38.8. The chat sat on "New chat" for the entire
first turn and then renamed itself at an apparently arbitrary moment.

A new thread shows a first-message snippet as a placeholder; once there is a
discernible subject we replace it with a concise model-generated title.

The namer reads the conversation FROM THE START, one exchange at a time. It may
answer NONE when an exchange has no topic (a greeting, a bare command), and the
next attempt moves to the NEXT exchange, so a thin opener never locks in a
meaningless name and the title still describes what the chat is ABOUT.

It used to read the LATEST exchange instead, which is what made a name land on a
tangent: turns 1-2 answer NONE, turn 7 goes off on a side question, and the chat
is permanently named after the side question. Worse, that rename is VISIBLE as a
change, because the list shows the first-message seed until a real title lands
(chat_store.list_threads), so the operator watches the name flip mid-conversation
from "what I opened with" to "some tangent". Naming from the front cannot do
that: whatever it picks comes from the opening of the conversation.

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


def _exchanges(messages: list[dict]) -> list[tuple[str, str]]:
    """Every (user, assistant) pair in the thread, IN ORDER. The assistant half
    may be "" for an exchange whose reply has not landed yet.

    Naming walks this list from the front, so exchange 0 is the conversation's
    opening and is tried first. Consecutive user messages (the operator typing
    twice before a reply) collapse onto the first of them: the reply answers the
    run, and the opener is the part that says what the chat is about.

    A PENDING EXCHANGE COUNTS, and that is what lets a chat be named while the
    first turn is still running. It used to require the reply, which meant the
    earliest a name could exist was the moment the turn committed. Measured over
    this operator's real threads, that put the name a median of 9.4 minutes after
    the opening message, spread from 1.9 to 38.8, entirely because it inherited
    the length of whatever turn it was waiting on. That variance is what reads as
    "the naming is inconsistent and sometimes does not run": the chat sits on
    "New chat" for the whole first turn, and turns here run long.

    The reply is still used when it is there. It is simply no longer required,
    because what a conversation is ABOUT is almost always already in its opening
    message, which is exactly what the operator can see while they wait."""
    out: list[tuple[str, str]] = []
    pending_user: str | None = None
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        role = m.get("role")
        if role == "user":
            if pending_user is None:
                pending_user = content
        elif role == "assistant" and pending_user is not None:
            out.append((pending_user, content))
            pending_user = None
    if pending_user is not None:
        out.append((pending_user, ""))
    return out


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

# How many exchanges from the START of the chat may be tried before we stop
# asking. This doubles as the INDEX of the exchange each attempt reads (attempt
# N reads exchange N), which is what keeps naming anchored to the opening of the
# conversation instead of wherever it happens to be now.
#
# It bounds the ONE cost of the retry loop: an unnameable chat would otherwise
# pay a naming call every turn forever. After this it keeps its seed / "New chat"
# fallback. Only CONTENT rejections count; a transient call failure (rate limit,
# restart) does not, so "name eventually even if one attempt was unlucky" holds.
#
# 5, not 3: each rejection now burns a specific early exchange rather than
# retrying the same latest one, so the budget is spent walking past greetings and
# acknowledgements. Three was enough to give up on a chat that opened with a
# couple of pleasantries, which is one half of "sometimes the name never gets
# set" — the other half being that an errored turn never schedules a naming pass.
_MAX_NAME_ATTEMPTS = 5


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

    Reads the chat FROM THE FRONT: attempt N names from exchange N. A thin opener
    (a greeting, a bare command) yields NONE, which counts, so the next attempt
    reads the next exchange. The title therefore always describes the beginning
    of the conversation and can never be taken from a later tangent, which is the
    "the name changed to something irrelevant mid-chat" bug. The attempt cap stops
    it asking forever when nothing near the start is ever nameable.
    """
    t = chat_store.get_thread(thread_id)
    if t is None or not is_blank_title(t.get("title")):
        return None, False
    attempt = int(t.get("_name_attempts", 0) or 0)
    if attempt >= _MAX_NAME_ATTEMPTS:
        return None, False
    exchanges = _exchanges(t.get("messages", []))
    if attempt >= len(exchanges):
        # The exchange this attempt is for has not happened yet. Transient and
        # UNCOUNTED: waiting for the next reply is not a rejection, and counting
        # it here would burn the budget on a chat that is simply still young.
        return None, False
    last_user, last_assistant = exchanges[attempt]

    # The reply is included only when there IS one. An empty "Assistant:" line
    # invites the model to describe the absence ("Awaiting Response") rather than
    # the subject, which is a title that is wrong the moment the reply lands.
    body = f"User: {last_user[:600]}"
    if last_assistant:
        body += f"\n\nAssistant: {last_assistant[:600]}"
    prompt = (
        "Generate a concise chat title (at most 6 words, Title Case, no quotes, "
        "no trailing punctuation, no emoji) describing what this conversation "
        "is ABOUT. If it has no clear subject yet (a greeting, an "
        "acknowledgement, a bare command, small talk), output exactly NONE "
        "instead. Title the SUBJECT, never the state of the conversation: never "
        "answer with something like Awaiting Reply or New Request. "
        "Output only the title, or NONE.\n\n"
        f"{body}"
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


# In-flight auto-naming tasks BY THREAD, held so nothing is collected
# mid-flight, and keyed so one thread never runs two namings at once. A burst
# of messages used to schedule a task per message: each read the same
# `_name_attempts`, each wrote N+1 (undercounting the budget), and each spent a
# model call asking a question the first answer had already settled.
_NAMING_TASKS: dict = {}


def schedule(thread_id: str) -> None:
    """Fire-and-forget auto-naming. Safe to call from an async handler (the
    common case) or a plain function; never delays the caller. One task per
    thread: while one is in flight, another schedule is a no-op, which is
    correct because the in-flight task reads the thread fresh."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        if thread_id in _NAMING_TASKS:
            return
        # HELD, so the loop cannot garbage-collect a task nothing else
        # references. asyncio keeps only a weak reference: an unheld task can be
        # collected mid-flight, and a thread simply never gets its name, with no
        # error anywhere. Same pattern as routes/chat.py's _DISTILL_TASKS.
        task = loop.create_task(name_thread(thread_id))
        _NAMING_TASKS[thread_id] = task
        task.add_done_callback(lambda _t: _NAMING_TASKS.pop(thread_id, None))
    else:  # pragma: no cover — no running loop (rare): just do it inline.
        generate_title(thread_id)
