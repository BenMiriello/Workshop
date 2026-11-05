"""The working-state note a compaction leaves behind, and who may write it.

A compaction collapses one session's window; this is the handoff that outlives
it. Two things trigger it: the Compact button (`routes/chat.compact_thread`)
and a turn the CLI compacted on its own (`build_streaming._commit_turn`). Both
call `spawn`, which never blocks the caller and never fails it.

ONE WRITE PER THREAD AT A TIME, ACROSS PROCESSES. Two triggers for one thread
would summarise the same transcript into the same note, so the second is
dropped rather than queued. What says a write is under way is an exclusive
`flock` on a per-thread file under the runtime directory, taken before the
write is scheduled and released when the file is closed at its end, which the
kernel also does for a process that dies mid-write. A module-level registry of
running writes could not see a write started by another backend worker; the
lock can, the same way `backend_schedule` keeps two backends off one pass.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
from pathlib import Path

from services import (chat_context, knowledge, llm_router, model_resolution,
                      origin, own_model, ownership)
from wsplatform import paths

log = logging.getLogger(__name__)

# Below this, a chat has not had a session worth handing to the next one, and
# summarizing it would spend a model call to say almost nothing.
_WORKING_STATE_MIN_MESSAGES = 4


def _fmt_for_summary(messages: list[dict]) -> str:
    # In the order things happened, for the same reason `routes/chat._build_prompt`
    # does it: a message sent mid-reply sits above that whole reply in the log. Here it
    # matters more than anywhere, because this text BECOMES the history once the
    # fold lands, so a wrong order is baked in rather than re-derived.
    out = []
    for m in chat_context.interleave_history(messages):
        who = origin.speaker_label(m.get("role", "user"), m.get("meta"))
        out.append(f"{who}: {m.get('content', '')}")
    return "\n\n".join(out)


def _summary_prompt(prev_summary: str, to_fold: list[dict], *,
                    per_thread: bool = False, max_chars: int = 0) -> str:
    """The exact text `_summarize_sync` sends. Split out so the ledger estimate
    can measure the SAME prompt `call_cloud` actually saw, without duplicating
    it out of sync or re-plumbing `_summarize_sync`'s return type (tests
    monkeypatch that function directly).

    `per_thread` words it for one chat's own handoff rather than the scope's.
    `max_chars` bounds the whole prompt for a model with a small window, by
    leaving out the OLDEST messages and saying how many."""
    headings = "\n".join(f"## {h}" for h in knowledge.WORKING_STATE_SECTIONS)
    whose, reader = (("this chat's", "this same chat reads after its context is "
                      "reset") if per_thread else
                     ("this scope's", "a DIFFERENT session reads later"))
    carried = ("every later turn of this chat" if per_thread
               else "every other chat in this scope")
    head = (
        f"You are writing {whose} WORKING STATE from a chat transcript: the "
        f"handoff {reader} to continue this work without "
        "the transcript. Write it as a handoff, not as a story about a "
        "conversation.\n\n"
        "Use EXACTLY these six headings, all of them, in this order, spelled "
        "exactly like this:\n"
        f"{headings}\n\n"
        "Under each heading write short lines starting with '- '. A heading with "
        "nothing under it gets the single line '- None.' Never add a heading, "
        "rename one, drop one, reorder them, or write anything outside them.\n\n"
        "What belongs under each:\n"
        "- Objective: what this work is trying to achieve, one or two sentences.\n"
        "- Decisions and why: THE MOST IMPORTANT SECTION. Every decision together "
        "with the reason it was made. A decision recorded without its reason gets "
        "re-litigated by the next session or silently reversed, so a line here "
        "that does not say why is not finished. Include decisions to NOT do "
        "something, which are the ones that get quietly undone.\n"
        "- Done: what is actually finished, each with the identifiers that make it "
        "checkable: file paths, function names, commit hashes, ticket ids. Not "
        "'refactored the router', but which file and which function.\n"
        "- Not done: what remains, and for anything blocked, what it is blocked on "
        "and who or what would unblock it.\n"
        "- Shortcuts taken and what they owe: work done the quick way that needs "
        "revisiting, and what the real version would be. Stubs, hardcoded values, "
        "copy-paste, skipped tests, a second code path added instead of changing "
        "the first, anything deferred 'for now'. A shortcut nobody wrote down is "
        "indistinguishable later from a deliberate design, which is how a codebase "
        "accumulates parallel systems nobody chose.\n"
        "- Open questions: things genuinely undecided, each with who owns the "
        "answer (the operator, or this session).\n\n"
        "Preserve file paths, identifiers and numbers verbatim, and preserve the "
        "operator's stated preferences in their own words. Drop pleasantries, "
        "repetition, and narration of the work as it happened. Write plainly, no "
        "em-dashes, no emojis. Keep the whole thing under about 500 words: it is "
        f"carried into the prompt of {carried}, so its "
        "length is a cost paid on every one of those turns.\n\n"
        "You are given the working state so far, if there is one, and the "
        "conversation to fold into it. Return the UPDATED working state: merge it "
        "in, move items from Not done to Done as they finish, drop what has stopped "
        "being true, and keep everything from the earlier state that is still "
        "true.\n\n"
        + (f"=== WORKING STATE SO FAR ===\n{prev_summary}\n\n" if prev_summary else "")
    )
    tail = ("=== OUTPUT ===\nThe updated working state only. Start with the first "
            "heading. No preamble, no closing remark.")
    msgs, left_out = list(to_fold), 0
    while True:
        convo = _fmt_for_summary(msgs)
        if left_out:
            convo = (f"[{left_out} earlier messages left out to fit the model's "
                     f"window]\n\n{convo}")
        prompt = f"{head}=== THE CONVERSATION ===\n{convo}\n\n{tail}"
        if not max_chars or len(prompt) <= max_chars or len(msgs) <= 1:
            return prompt
        msgs, left_out = msgs[1:], left_out + 1


def _summarize_sync(prev_summary: str, to_fold: list[dict], *,
                    per_thread: bool = False) -> str:
    """One summarization call. Text in, text out, no tools.

    `tools_disabled` matters: without it this runs in the CLI's default agentic
    mode, which spins up every MCP server just to write a paragraph and takes
    minutes, which is what made /compact look hung."""
    prompt = _summary_prompt(prev_summary, to_fold, per_thread=per_thread)
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU,
                               tools_disabled=True, timeout_s=180)
    return (res.get("text") or "").strip()


# Room left in a small window for the answer and the request's own framing.
_OWN_MODEL_SUMMARY_TOKENS = 8192
_OWN_MODEL_HEADROOM_TOKENS = 1024
# Characters per token assumed when fitting a prompt into a window: low, so a
# prompt measured this way fits a tokenizer that packs fewer characters.
_CHARS_PER_TOKEN_FLOOR = 3


def _resolve_model(t: dict) -> str:
    """The model this thread's turns run on."""
    return model_resolution.resolve_model(
        ownership.owner_or_none(), t.get("context_key"),
        model_resolution.ModelRequest.from_thread(t)).model_id


def _summarize_for(prev_summary: str, to_fold: list[dict], t: dict, *,
                   per_thread: bool) -> tuple[str, str]:
    """(working state, the model that wrote it) for thread `t`.

    ON THE CHAT'S OWN MODEL when a provider other than the CLI serves it (a
    local model server), through the app's gateway when its chat declares one
    (`services/own_model`): a chat kept on that server is not summarised by a
    cloud model. Fitted to that model's window. Raises when that server does
    not answer, and never falls back to the cloud for such a chat.

    Every other chat keeps the one cheap cloud call it always had."""
    try:
        model = _resolve_model(t)
    except Exception as e:  # noqa: BLE001 - resolution is not this path's to fail
        log.info("compact: model unresolved for %s, using the cheap tier: %s",
                 t.get("id"), e)
        model = ""
    if not own_model.serves(model):
        text = (_summarize_sync(prev_summary, to_fold, per_thread=True) if per_thread
                else _summarize_sync(prev_summary, to_fold))
        return text, llm_router.TIER_HAIKU
    window = own_model.window_tokens(model)
    max_chars = (max(0, window - _OWN_MODEL_SUMMARY_TOKENS - _OWN_MODEL_HEADROOM_TOKENS)
                 * _CHARS_PER_TOKEN_FLOOR) if window else 0
    prompt = _summary_prompt(prev_summary, to_fold, per_thread=per_thread,
                             max_chars=max_chars)
    res = own_model.call(prompt, model_id=model, thread=t,
                         max_tokens=_OWN_MODEL_SUMMARY_TOKENS)
    return (res.get("text") or "").strip(), str(res.get("model") or model)


def write_now(thread_id: str, t: dict) -> None:
    """Write this SCOPE's working state, so a chat that does not exist yet can
    continue from this one.

    THIS IS WHAT MAKES COMPACTION WORTH ANYTHING TO ANYONE ELSE, and it is a
    separate feature from compaction itself. The provider's `/compact` collapses
    ONE session's window; nothing about that reaches another chat, so a new chat
    in the same scope would start by re-deriving what the last session already
    worked out. The hand-written `HANDOFF-*.md` files under docs are what that
    looks like when somebody remembers to do it manually.

    It used to be handed the fold's summary, because Workshop's own fold had
    just computed one. That fold is gone, so this makes its own summarizing call
    (`_summarize_sync`, the same Haiku call, the same prompt) over the
    transcript as it stands. That is not "deciding what compaction keeps" -- the
    provider decides that, and this text never goes back into the compacted
    chat. It is a handoff note for the NEXT chat.

    It goes into the note store (`knowledge.write_working_state`), NOT into a
    new table or file of its own: one note per scope at the reserved id
    `working-state`, overwritten each time, so the scope has one current
    working state rather than a pile of dated handoffs. That also buys the
    Memory page, search, backlinks and hand-editing for free.

    BEST EFFORT, ALWAYS, and it runs off the request path entirely (see
    `spawn`). The operator pressed compact to reclaim context; a
    handoff that could not be filed is a log line, never a failed operation.
    Logged at WARNING rather than debug because nothing retries this: the next
    compaction writes the NEXT state, so a state lost here is lost.
    """
    try:
        msgs = [m for m in (t.get("messages") or [])
                if not (m.get("meta") or {}).get("streaming")]
        ct = int(t.get("cleared_through", 0) or 0)
        if 0 < ct <= len(msgs):
            msgs = msgs[ct:]
        # Two messages is not a session worth handing off, and it would cost a
        # model call on every press in a chat that has barely started.
        if len(msgs) < _WORKING_STATE_MIN_MESSAGES:
            log.info("compact: no working state for %s, only %d messages",
                     thread_id, len(msgs))
            return
        scope = knowledge.scope_for_context(t.get("context_key") or "")
        # PER THREAD for an app's own assistant, whose chats are separate
        # pieces of work (see `chat_context.working_state_block`).
        per_thread = chat_context.is_app_scoped(
            chat_context.get_provider(t.get("context_key")))
        note_thread = thread_id if per_thread else ""
        # MERGE, don't overwrite. The scope's note is written by every chat in
        # the scope, so starting from scratch each time would throw away what
        # another chat recorded; a thread's own note carries what its earlier
        # compactions recorded. The prompt already knows how to merge: move
        # items from Not done to Done, drop what stopped being true, keep what
        # still is.
        prev, _ = (knowledge.read_working_state(scope, note_thread) if per_thread
                   else knowledge.read_working_state(scope))
        summary, model = _summarize_for(prev, msgs, t, per_thread=per_thread)
        if not summary:
            log.warning("compact: the working-state summary came back empty for %s", thread_id)
            return
        knowledge.write_working_state(
            scope, summary,
            # `derived:<model>`, the origin vocabulary for "a model wrote
            # this", so the Memory page and anything else reading provenance
            # can tell it from a note the operator or an agent wrote. The
            # model named is the one that actually produced the text.
            author=origin.derived(model),
            # The originating chat, which `put_note` already records on every
            # note, so the Memory page can say where a working state came from.
            chat_id=thread_id,
            thread_id=note_thread,
        )
        log.info("compact: working state for scope %s written from %s (%d chars)",
                 scope, thread_id, len(summary))
    except Exception as e:
        log.warning("compact: working-state note write failed for %s: %s", thread_id, e)


def _lock_path(thread_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", thread_id) or "_"
    return paths.runtime_dir() / "working-state" / f"{safe}.lock"


def _try_lock(thread_id: str) -> int | None:
    """An open descriptor holding this thread's write lock, or None when
    another write holds it. Closing the descriptor releases it."""
    lock = _lock_path(thread_id)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _write_holding(fd: int, thread_id: str, t: dict) -> None:
    try:
        write_now(thread_id, t)
    finally:
        os.close(fd)


def spawn(thread_id: str, t: dict) -> asyncio.Future | None:
    """Start writing this thread's working state in a worker thread.

    Returns the running write, or None when none was started: another write
    for this thread holds the lock, or scheduling failed. Best effort in the
    strongest sense: a failure here is a log line, and nothing about
    compaction depends on it."""
    try:
        fd = _try_lock(thread_id)
    except OSError as e:
        log.warning("compact: could not take the working-state lock for %s: %s",
                    thread_id, e)
        return None
    if fd is None:
        log.info("compact: working state for %s is already being written", thread_id)
        return None
    try:
        return asyncio.get_running_loop().run_in_executor(
            None, _write_holding, fd, thread_id, t)
    except Exception as e:  # noqa: BLE001
        os.close(fd)
        log.warning("compact: could not schedule the working state for %s: %s",
                    thread_id, e)
        return None
