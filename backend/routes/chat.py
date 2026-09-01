"""Generalized in-app chat.

App-agnostic core: threads + message history live in chat_store; per-app
behavior (persona, live context injection, write actions) lives behind a
ContextProvider keyed by the thread's `context_key`. The same React <ChatPanel>
drives every app — only the contextKey differs.

Responses go through the existing subscription-auth `claude -p` path
(llm_router.call_cloud); no new auth surface. Each turn is a stateless
completion: we rebuild the prompt from persona + fresh app context + the stored
transcript, so history is fully owned here, not in the model session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import os
import re
import unicodedata
import urllib.parse
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from routes import chat_models as cm
from routes import chat_resource_models as crm
from routes.api_models import DeletedOut
from services import chat_attachments
from services import context_window
from services import chat_branches
from services import chat_drain
from services import chat_worktree
from services import worktrees
from services import build_agents
from services import chat_events
from services import tenancy
from services import chat_queue
from services import wakeups
from services import chat_store, chat_context, chat_log
from services import tasks
from services import thread_ops
from services import task_scope
from services import context_report
from services import mcp_health
from services import mcp_registry
from services import model_caps
from services import task_scope
from services import scope_version
from services import agent_authority
from services import ownership
from wsplatform import paths
from services import ticket_drafts
from services import ticket_format
from services import knowledge
from services import origin
from services import skills
from services import llm_router
from services import build_streaming
from services import draft_store

log = logging.getLogger("workshop.chat")
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# Default chat model — haiku is plenty fast and good enough for the chat surface.
# An app that wants a bigger model for its own assistant says so on its AppSpec
# (chat_model_tier); everything else stays on haiku to keep latency tight. The
# platform does not keep a list of which apps are special.
DEFAULT_CHAT_MODEL = llm_router.TIER_HAIKU


# Per-provider tool policy: providers define allowed_tools + mcp_servers
# (see chat_context.ContextProvider). The narrow-tool path in
# call_cloud honors these — built-in tools (Bash/Read/Edit) stay off
# everywhere; only the named MCP tools are surfaced. Providers with empty
# allowed_tools fall through to tools_disabled (single-turn text).
#
# Gated by ENABLE_CHAT_TOOLS. The MCP-startup race that kept this off has been
# resolved (2026-06-15): the `claude -p` CLI starts --mcp-config servers fully
# async by default and fires the first model turn ~176ms BEFORE the stdio
# handshake completes, so the model saw `tools: []` (init event: server
# `pending`, tools_count=0) and either admitted no-tools or fabricated a tool
# call as literal text. The fix lives in llm_router.call_cloud's narrow-tool
# path: it sets MCP_CONNECTION_NONBLOCKING=false in the subprocess env, which
# flips the CLI's internal gate to AWAIT the MCP connection before the first
# turn (bounded by the CLI's own 30s connect timeout). Verified end to end and
# reproducibly: with it set the init event shows the server `connected`,
# tools_count=7, and the model emits a real tool_use + consumes a real
# tool_result (e.g. budget chat searching Plaid transactions). See
# llm_router._MCP_BLOCK_STARTUP_ENV for the full mechanism.
ENABLE_CHAT_TOOLS = True
# Timeout: 300s comfortably covers cold-start + Sonnet on prompt-heavy budget
# turns + one optional Plaid roundtrip + synthesis.
# There is deliberately NO budget on a chat turn: no step cap and no wall clock.
#
# A chat turn is the same kind of work a build turn does, and this platform
# exists for that work to run to completion on its own. Both caps produced the
# same failure from different directions — a turn guillotined mid-task, files
# half-changed, reported to the operator as "stopped at the time limit" — and
# both are the wrong lever. Spend is governed by the usage cap.
#
# 0 means no deadline (services/build_streaming reads it that way). A turn ends
# when the model is done, when the process dies, or when the operator stops it.
CHAT_TIMEOUT_S = 0

# THERE IS NO MESSAGE CAP. `MAX_HISTORY = 60` used to slice the transcript here,
# and it was measured in the wrong unit: the largest thread in the store is about
# 110k tokens in THIRTEEN messages and sailed past a 60-message cap untouched,
# while a 129-message thread less than half that size, ~52k tokens, was
# amputated. A cap that fires on the count and not the size cannot protect the
# window it exists to protect, and it silently deleted the beginning of long
# conversations, which is the part that says what the work is for.
#
# Nothing in the store exceeds 11% of a 1M window. If a thread ever does
# approach it, the CLI compacts its own session, and `/compact` is there
# natively. The only thing that removes a message from a prompt is an explicit
# `/clear`. Operator, 2026-08-12.


class CreateThread(BaseModel):
    context_key: str = "general"
    title: str = ""
    agent_id: str | None = None
    # NO `participants`. It was accepted here, stored, mirrored into Postgres,
    # and read by nothing that answers a turn: an agent in a chat is either the
    # chat's `context_key` (`agent:<slug>`, it answers every turn) or an
    # @mention (it answers one). Send `context_key: "agent:<slug>"` to start a
    # chat with an agent.
    # NO `scope`. It was accepted here, never sent by any client, and passed
    # straight into the stored field as a second answer to a question
    # `context_key` already answers. A chat's scope is derived from its
    # context_key on every read now (services.scope_tree.scope_for_context).
    # How this chat handles work (direct | auto). Omitted means
    # "use the operator's default for new chats" — the toolbar sends it only
    # when the mode was chosen before the first message, which is the whole
    # point of showing the control on a blank chat.
    mode: str | None = None


class PostMessage(BaseModel):
    content: str
    # Optional per-turn model override (short tier name "haiku"/"sonnet"/"opus"
    # OR a full model id). Normalized + validated in the handler; persisted on
    # the thread so it sticks for subsequent turns.
    model: str | None = None
    # See StartTurnIn.client_id. Optional here: the callers of this blocking path
    # are the voice worker and scripts, which retry at a level where a duplicate
    # would be visible too.
    client_id: str | None = None


class PatchThread(BaseModel):
    """The thread's own settings, as a partial write.

    ONE PATCH, NOT A ROUTE PER FIELD. `title` was the only thing this endpoint
    could set, under the name `RenameThread`, which is why `mode` ended up with
    a POST of its own. `armed` and `role` are ordinary settings and go here.

    An omitted key is left alone; `role: null` CLEARS the role, which is a real
    write and is why the handler asks `model_fields_set` rather than testing for
    None. `title` stays optional so the rename call that every client already
    makes keeps working unchanged.
    """

    title: str | None = None
    # See chat_store.ARMED_MODES. Out-of-range values clamp to `off` rather than
    # 400: the failure direction for "what runs unasked" is a thread that does
    # nothing.
    armed: str | None = None
    role: str | None = None


class RescopeThread(BaseModel):
    context_key: str


class SetChatMode(BaseModel):
    mode: str  # direct | auto (chat_context.CHAT_MODES)


class ForkThread(BaseModel):
    """`anchor` is the id of the message to carry on FROM.

    It is the file-event id the transcript already renders with, not a new
    coordinate: the client points at what it is showing. The server resolves it
    to a node in the tree (ADR 0020).

    `mode` says whether the anchor is KEPT. `after` (a reply: keep this answer,
    drop what followed) or `before` (your own message: take it back, so the
    anchor becomes its parent)."""
    anchor: str
    mode: str = "after"


class RenameBranch(BaseModel):
    name: str


class CardState(BaseModel):
    """The outcome of acting on an in-chat ui-card, so the card stops offering an
    action that has already been taken. `key` is a stable digest of the card's own
    block, computed by the client."""
    key: str
    phase: str                    # "success" | "dismissed"
    note: str | None = None
    slug: str | None = None
    title: str | None = None


class SpawnTask(BaseModel):
    text: str | None = None       # explicit task text; else distilled from the chat
    project: str | None = None    # target project slug; else the chat's own scope
    # An explicit scope KEY (workshop / project:x / agent:x). Task
    # proposal cards let the operator choose where a ticket lands, and they used
    # to POST /api/tasks directly to honour that — which meant filing from a card
    # recorded no chat provenance and the ticket had no way back to the
    # conversation that produced it. This is how one endpoint serves both.
    scope: str | None = None
    acceptance: dict | None = None
    priority: int = 0
    effort: str | None = None
    # The work CATEGORY (bug|feature|task|chore), orthogonal to `execution`.
    # Optional; a filed ticket carries it through. Unset/unrecognized normalizes
    # to None in the store — the store never invents a type.
    type: str | None = None
    run: bool = False             # file AND run now (open a Job, launch a worker)


def _resolve_model(
    body_model: str | None,
    thread: dict,
    provider: chat_context.ContextProvider,
) -> str:
    """Resolution order: explicit body.model > thread.model > provider default.
    Returns a full model id. Raises HTTPException(400) on an invalid body.model."""
    try:
        explicit = chat_context.normalize_model(body_model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if explicit:
        return explicit
    # thread.model was already normalized when persisted, but normalize again
    # defensively (tolerate hand-edited thread files).
    try:
        stored = chat_context.normalize_model(thread.get("model"))
    except ValueError:
        stored = None
    if stored:
        return stored
    return provider.default_model


def _resolve_effort(body_effort: str | None, thread: dict, model: str) -> str | None:
    """Resolution order: explicit body.effort > thread.effort > operator pref >
    the model's own default (None).

    None means "send no --effort at all", which is the model's own default and
    byte-identical to the argv a chat spawns today. That matters beyond tidiness:
    effort is part of the rendered prompt, so emitting it where it was never
    chosen would cost every untouched chat its prompt-cache prefix.

    Two rules about a level the resolved model doesn't support:

      - from the BODY it is dropped to None, full stop. That is the operator's
        live click and it wins over every stored tier, even when the win is
        "fall back to the model default".
      - from the THREAD or the PREF it is skipped, and the next tier down is
        consulted. A thread carrying `xhigh` from when it was on Opus should not
        blank out an operator default of `max` just because Sonnet 4.6 has no
        `xhigh` rung.

    Raises 400 only for a value that isn't a level at all — and only from the
    body, since a stored tier the operator cannot see must never fail a turn."""
    try:
        explicit = model_caps.normalize_effort(body_effort, model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if explicit:
        return explicit
    if (body_effort or "").strip():
        return None
    for candidate in (thread.get("effort"), chat_store.get_default_effort()):
        try:
            level = model_caps.normalize_effort(candidate, model)
        except ValueError:
            continue
        if level:
            return level
    return None


def _resolve_permission_mode(body_mode: str | None, thread: dict) -> str:
    """Resolution order: explicit body > thread > operator pref > the platform
    default.

    Always returns a mode (never None): unlike effort, there is no "let the
    vendor decide" here — the CLI has its own default and it is not the one this
    chat has always run at, so the value is made explicit at every spawn.

    The operator pref sits BELOW the thread, so setting a default never reaches
    into a chat that has chosen for itself; it moves the chats that never did."""
    try:
        explicit = model_caps.normalize_permission_mode(body_mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if explicit:
        return explicit
    try:
        stored = model_caps.normalize_permission_mode(thread.get("permission_mode"))
    except ValueError:
        stored = None
    return (stored or chat_store.get_default_permission_mode()
            or model_caps.DEFAULT_PERMISSION_MODE)


def _accepted_mode(value: str | None, field: str = "mode") -> str:
    """A chat mode the API will store, or a 400.

    ACCEPTS A RETIRED VALUE AND MAPS IT FORWARD. `orchestrated` left the enum on
    2026-08-27 (it and `auto` were one behaviour under two names), but a tab that
    was open before then still has it in its bundle, and the answer to a stale
    client naming the mode it has been using for months is that mode's new name
    -- not a 400 that reads as the control being broken. `chat_store._clamp_mode`
    owns the mapping; membership is tested BEFORE the clamp so that genuine junk
    still fails loudly instead of silently becoming the default."""
    raw = (value or "").strip().lower()
    if raw not in chat_context.CHAT_MODES and raw not in chat_store._LEGACY_CHAT_MODES:
        raise HTTPException(400, f"{field} must be one of {list(chat_context.CHAT_MODES)}")
    return chat_store._clamp_mode(raw)


@router.get("/providers", response_model=list[cm.Provider], response_model_exclude_unset=True)
async def providers(scope: str | None = None):
    return chat_context.list_providers(scope=scope)


@router.get("/threads", response_model=list[cm.ThreadSummary], response_model_exclude_unset=True)
async def threads(context: str | None = None, archived: bool = False):
    """The live conversations, or the archive. Never both: see list_threads.

    Served from Postgres (services.chat_list_pg) when WORKSHOP_CHAT_LIST_PG is
    on AND the query succeeds; otherwise from the file fold
    (chat_store.list_threads, unchanged). Either way the file fold is the
    fallback on any failure, so a Postgres outage costs this endpoint speed and
    never the chat.

    WHY IT IS OFF BY DEFAULT, and what has to be true to turn it on. Parity
    against the file fold was measured on 2026-08-15 over the real store and is
    close but NOT yet exact:

      * archived: exact. 3/3 rows, every field, order matching.
      * live: 68 rows from the file, 70 from Postgres. The two extra are test
        artifacts titled "SCRATCH branch e2e" that never existed on disk,
        leaked by tests before services/tenancy.py closed that hole. They would
        show up in the operator's sidebar as two fake chats, which is the one
        difference that is plainly a regression rather than a nuance.
        db/prune_test_threads.py is the sanctioned cleanup and refuses to touch
        anything that does not match a fixture signature, but it needs
        WORKSHOP_DATABASE_ADMIN_URL, which this service deliberately does not
        load.
      * 13 rows differ on `last_message` only, never on ordering: `last_user_at`
        agrees everywhere. The preview is the last MESSAGE-kind node of the
        branch path, and an interrupted reply's tail stretch is not always the
        same text the file's single message event holds.

    So: the ordering, the counts, the titles, the modes and the timestamps all
    agree. Flip the flag once the two scratch rows are gone and the preview
    rule is settled. `db/chat_list_parity.py` is the check to re-run.
    """
    from services import chat_list_pg
    rows: list[dict] | None = None
    if os.environ.get("WORKSHOP_CHAT_LIST_PG", "").strip().lower() in {"1", "true", "yes"}:
        rows = await chat_list_pg.list_threads(context_key=context, archived=archived)
    if rows is None:
        rows = chat_store.list_threads(context_key=context, archived=archived)
    # WHICH OF THESE IS LIVE, stamped over whichever source answered so the two
    # cannot disagree about it. See chat_store.stamp_running.
    return chat_store.stamp_running(rows)


# ---- Composer drafts -------------------------------------------------------
# The unsent text in a chat composer, persisted per owner + context_key so a
# reload (or another device) doesn't lose it. The browser keeps the instant
# local copy; this is the durable cross-device mirror. See services/draft_store.
class AttachmentRefIn(BaseModel):
    """A file the client already uploaded. `name` and `kind` are carried for the
    transcript to render without a second lookup; the server re-reads both from
    its own index before handing anything to a model, so a wrong one here can
    only mislabel a chip, never change what is sent."""
    id: str
    name: str = ""
    kind: str = ""


class DraftPut(BaseModel):
    text: str = ""
    ts: int = 0
    client_id: str = ""
    # FILES ATTACHED TO THIS COMPOSER. A draft is everything in the box, and a
    # screenshot dropped into it is as much "what I have not sent yet" as the
    # words beside it. The bytes are already on the server (upload happens on
    # drop); this is the association, which used to live only in React state and
    # so was lost by any reload that kept the text.
    attachments: list[AttachmentRefIn] | None = None


@router.get("/drafts/{key:path}", response_model=cm.Draft, response_model_exclude_unset=True)
async def read_draft(key: str):
    return draft_store.get_draft(key)


@router.put("/drafts/{key:path}", response_model=cm.Draft, response_model_exclude_unset=True)
async def write_draft(key: str, body: DraftPut):
    return draft_store.put_draft(
        key, body.text, body.ts, body.client_id,
        [a.model_dump() for a in (body.attachments or [])])


@router.delete("/drafts/{key:path}", response_model=DeletedOut, response_model_exclude_unset=True)
async def remove_draft(key: str):
    draft_store.clear_draft(key)
    return {"deleted": True}


@router.post("/threads", response_model=cm.ThreadDocument, response_model_exclude_unset=True)
async def new_thread(body: CreateThread):
    mode = _accepted_mode(body.mode) if body.mode is not None else None
    return chat_store.create_thread(
        context_key=body.context_key, title=body.title, agent_id=body.agent_id,
        mode=mode,
    )


class ChatPrefs(BaseModel):
    """Every field is optional and only what is SENT is written, so the settings
    screen can save one control without having to echo the others back.

    An empty string clears a default (`""` means "no preference", i.e. fall back
    to the model's / platform's own). None means "not sent"; the two cannot be
    the same value or clearing would be unexpressible."""
    default_mode: str | None = None
    default_effort: str | None = None
    default_permission_mode: str | None = None


def _prefs_payload() -> dict:
    """What the settings screen renders from. The CHOICES are served, never
    assumed by the client: which permission modes a chat may pick is this
    system's decision (bypass is not among them) and the effort ladder is the
    vendor's."""
    return {
        "default_mode": chat_store.get_default_mode(),
        "modes": list(chat_context.CHAT_MODES),
        # None = no preference: the model's own default effort, and the platform
        # permission mode. Rendered as the "model default" / "system default"
        # option rather than as a blank.
        "default_effort": chat_store.get_default_effort(),
        "efforts": list(model_caps.EFFORT_LEVELS),
        "default_permission_mode": chat_store.get_default_permission_mode(),
        "permission_modes": list(model_caps.OFFERED_MODES),
        "platform_permission_mode": model_caps.DEFAULT_PERMISSION_MODE,
    }


@router.get("/prefs", response_model=cm.ChatPrefsOut, response_model_exclude_unset=True)
async def get_chat_prefs():
    """Operator-level chat settings: the mode a NEW chat starts in, plus the
    effort and permission-mode defaults every chat that never chose for itself
    resolves through. Existing chats keep the `mode` they were created with; the
    other two are consulted per turn, so changing one moves those chats."""
    return _prefs_payload()


@router.post("/prefs", response_model=cm.ChatPrefsOut, response_model_exclude_unset=True)
async def set_chat_prefs(body: ChatPrefs):
    if body.default_mode is not None:
        chat_store.set_default_mode(_accepted_mode(body.default_mode, "default_mode"))
    if body.default_effort is not None:
        try:
            chat_store.set_default_effort(body.default_effort)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.default_permission_mode is not None:
        try:
            chat_store.set_default_permission_mode(body.default_permission_mode)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return _prefs_payload()


def _without_messages(t: dict) -> dict:
    """A thread document as SETTINGS: everything except the transcript.

    `chat_store` documents carry `messages`, and every route that answers with a
    thread is answering a settings question, not a transcript one (the
    transcript is served by GET /events). Measured 2026-08-14: the worst
    thread's `messages` was 4.77 MB of a 4.79 MB response no client reads, and
    `lib/chat.ts` types all four of these as `ThreadSummary`, which has no
    `messages` field at all.

    This is a function rather than a dict comprehension repeated four times
    because it was a comprehension in ONE of the four. GET stripped it and
    carried a comment saying not to add it back; PATCH, archive and restore sat
    three functions below returning the same document raw, so closing a chat
    shipped its whole history to a client that discards it. One rule, one place.
    Strip here rather than in chat_store.get_thread(): backend callers of that
    function legitimately need the messages."""
    return {k: v for k, v in t.items() if k != "messages"}


@router.get("/threads/{thread_id}", response_model=cm.ThreadWithQueue, response_model_exclude_unset=True)
async def thread(thread_id: str):
    """The thread as a document: meta plus a `messages` list.

    A turn in flight appears as a trailing assistant message carrying
    `streaming: true` and its `turn_id`, built as a VIEW of `meta.live_message`
    (chat_store._live_view) rather than stored. There is nothing to self-heal
    here any more: the flag is derived from `active_turn_id`, so it cannot be
    orphaned, and a turn that did not survive a restart is closed out once at
    startup (build_streaming.reconcile_threads) instead of on every read."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    # Opening a chat shows what is waiting to be sent in it, on the first paint,
    # from the same read that produced the transcript. See _without_messages
    # for why the transcript itself is not in this response.
    # A compact or clear in flight is part of "what is happening in this chat",
    # and it comes from the same read for the same reason the queue does.
    return (_without_messages(t) | chat_queue.state(thread_id)
            | {"pending_operation": thread_ops.pending(thread_id)})


# How long a thread stream waits before sending a keepalive. Long enough that an
# idle chat costs almost nothing, short enough that a dead connection is noticed
# by the proxy and the browser rather than lingering.
_THREAD_STREAM_KEEPALIVE_S = 25.0


@router.get("/threads/{thread_id}/stream")
async def stream_thread(thread_id: str, request: Request, since: int = 0):
    """Say WHEN this thread changes. The client then syncs.

    THE POLL WAS THE PROBLEM. Everything a client did not do itself — a turn
    started on another device, a message sent from a phone, a queued message the
    server delivered, a compaction — arrived up to ten seconds late, on every
    client but the one that caused it. There was already a stream, but it
    carried ONE TURN's output to whoever knew that turn's id, so a client that had
    not started a turn had nothing to subscribe to and could only ask again.

    IT CARRIES NO CONTENT, deliberately. `changed` and a version, nothing else;
    the client calls the same cursor-based sync it always had. A stream that
    carried the transcript would be a second answer to "what is in this chat",
    and every reorder and eaten-message bug in this system's history was two
    owners of that answer disagreeing. This adds a trigger, not a source.

    `since` is the version the client last saw, so a reconnect that missed a
    change wakes immediately instead of one keepalive behind. Missing a bump
    costs latency and never correctness: the sync is idempotent and the poll
    still runs behind this as a backstop.
    """
    if not chat_log.exists(thread_id):
        raise HTTPException(404, "thread not found")

    async def gen():
        seen = max(0, since)
        # The current version first, so a client that connects after a change it
        # has not synced yet acts on it immediately.
        yield f"event: changed\ndata: {chat_events.version(thread_id)}\n\n"
        seen = chat_events.version(thread_id)
        while True:
            if await request.is_disconnected():
                return
            if build_streaming.SHUTTING_DOWN:
                # Release the connection so a restart is a pause, not a 90s
                # stall. This generator ends only on disconnect, so an idle open
                # tab held uvicorn's graceful shutdown until systemd's
                # TimeoutStopSec SIGKILLed it. The client reconnects with
                # backoff and re-syncs, and this stream carries no content, so
                # dropping it loses precisely nothing.
                return
            v = await chat_events.wait(thread_id, seen, _THREAD_STREAM_KEEPALIVE_S)
            if await request.is_disconnected():
                return
            if v > seen:
                seen = v
                yield f"event: changed\ndata: {v}\n\n"
            else:
                # Nothing happened. A comment frame keeps the connection honest
                # without waking the client's reducer.
                yield ": keepalive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # The edge buffers by default, which would hold a one-line frame until
        # something else came along — turning an instant update back into a slow
        # one, which is the whole thing this replaces.
        "X-Accel-Buffering": "no",
    })


# The page size a `before` request uses when the caller does not also say
# `tail`. Not load-bearing on correctness -- any positive size behaves the same
# way, this just picks a reasonable one so `before` alone is a valid call.
_BACK_PAGE_DEFAULT = 50


@router.get("/threads/{thread_id}/events", response_model=cm.ThreadEvents, response_model_exclude_unset=True)
async def thread_events(thread_id: str, after: int = 0, limit: int | None = None,
                        tail: int | None = None, before: int | None = None):
    """The thread's event log from a cursor. THE sync endpoint.

    Everything needed to render a transcript is here: `events` in seq order,
    `last_seq` to poll from next, and `active_turn` with the live snapshot of a
    turn in progress. A client calls this on mount, on focus, on reconnect and
    after any write, and merges it with nothing except the live SSE for the
    active turn.

    That is the point. A transcript assembled by merging several endpoints with
    local state is the anti-pattern behind every reorder and eaten-message bug in
    this system's history: five owners of the message list, concatenated by
    convention, disagreeing one pair at a time.

    `after` is a cursor, not an index. `after=0` is the whole log.

    IT READS THE LOG AT MOST ONCE, and usually not at all. This used to call
    `read_thread` — which reads and parses the ENTIRE log to build a tail it then
    threw away — and then `read_events` to read and parse the whole file a second
    time for the part it actually wanted. Every 10 seconds. Per open tab. Per
    thread, for the life of the conversation, on a file that only grows.

    Two changes, neither of them a cache (a cache here would go stale against a
    log another process appends to, which is the failure mode this endpoint
    exists to avoid):

      * `read_meta` is a small fixed-size read (meta.json plus the log's last
        64KB, to reconcile `last_seq`), and it answers `last_seq` on its own;
      * `active_turn` reads the turn's start seq straight out of meta now, so it
        no longer needs the events at all.

    That leaves `read_events`, and a poll with nothing new to fetch does not need
    it either: `after >= last_seq` means the caller is already current. `last_seq`
    is reconciled against the log's real tail inside `read_meta`, so this is an
    exact test, not an optimistic one.

    `tail` and `before` are the other end: paging BACKWARD from the newest
    events, for an initial render that wants to show the bottom of a long
    conversation without pulling everything above it.

      tail=N             the last N events of the CURRENT branch path.
      tail=N&before=S     the N events of that path immediately before seq S,
                          for walking further back a page at a time. `before`
                          without `tail` uses `_BACK_PAGE_DEFAULT`.

    Composable without gaps or duplicates: call `tail=20`, note the page's
    `first_seq`, then call `before=<that first_seq>&tail=20` for the next page
    back, repeating. Each page is exactly the visible events strictly below the
    previous page's floor, because both are windows into the same seq-ordered
    log filtered by the same branch snapshot.

    `tail`/`before` are IGNORED once `after` is a real cursor (`after > 0`): the
    incremental-sync path always wins, because that is the request a client still
    mid-poll is making and a tail read must never intercept it instead. This
    endpoint answers ONE question per call.

    THE SOURCE IS STILL THE FILE for `tail`/`before` too, not the branch tree in
    Postgres, for the same reason it is for `after`. ADR 0020 says outright that
    this is unfinished: "the transcript read still comes from the file; only
    branch membership comes from the database... the tree is authoritative for
    STRUCTURE and the log for CONTENT, which is a seam that must be closed rather
    than left." Checked against a live thread before writing this (65341b40...):
    the Postgres mirror has 98 rows against the log's 175 events, because it
    never mirrors `turn` or `title` kind events at all, and its `seq` is a
    SEPARATE counter from the log's -- Postgres seq 1 is the log's seq 2 for the
    same message. A page built from Postgres could not be spliced against an
    `after` cursor from this same endpoint without silently dropping events and
    misaligning the cursor space it shares with every other client on the
    thread. So the WINDOW is new; the source and the branch filter are not:
    `keep_visible` against `chat_branches.branch_state_sync`'s hidden set, the
    same recursive CTE path walk that answers `after=0` today.

    The live (hot) half of the log (`read_live_events`) is read first and is
    almost always enough, because rotation keeps at least `KEEP_MIN_EVENTS`
    events live; `_tail_page` only reaches into the cold archive when the
    visible events in that window are sparser than the page (a heavily branched
    thread). That is the one case this does not avoid an O(archive) read for --
    correct, just not cheap, and rare rather than the common case this exists to
    fix."""
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        raise HTTPException(404, "thread not found")
    last_seq = int(meta.get("last_seq") or 0)
    cursor = max(0, after)
    # WHICH BRANCH THIS IS, on every poll, and it has to be here rather than on
    # its own endpoint. This is a CURSOR sync: the client merges what it is given
    # into what it already holds. Filtering alone could never make it forget the
    # abandoned tail, because a cursor fetch after a fork returns nothing new at
    # all. So the branch identity travels with the events, the client notices it
    # changed, and re-reads from zero. Same reasoning as the cut and the queue
    # below: one endpoint answers "what is in this chat", and things that must
    # agree are not allowed to arrive separately.
    branch = chat_branches.branch_state_sync(thread_id)
    hidden = branch["hidden"]
    has_more = False
    first_seq = None
    if cursor <= 0 and (tail is not None or before is not None):
        page_size = tail if tail is not None else _BACK_PAGE_DEFAULT
        events, has_more, first_seq = _tail_page(thread_id, hidden, page_size, before)
    else:
        events = (
            chat_log.read_events(thread_id, after=cursor, limit=limit)
            if cursor < last_seq else []
        )
        events = chat_branches.keep_visible(events, hidden)
        if events:
            first_seq = events[0]["seq"]
    return {
        "events": events,
        "last_seq": last_seq,
        "branch": branch["branch"],
        "branch_count": branch["count"],
        "active_turn": _active_turn_payload(thread_id, meta),
        # ADDITIVE. `after=0` with no `tail` always has `has_more=False` and
        # `first_seq` set from whatever `events` came back (or None if empty):
        # that call already returns the whole log, so nothing is being withheld.
        # A `tail`/`before` page is the only case either one carries information
        # a client did not already have.
        "has_more": has_more,
        "first_seq": first_seq,
        # THE CUT THE SERVER IS ACTUALLY APPLYING, so the divider in the
        # transcript describes the prompt rather than the log. Derived from the
        # same resolution the prompt uses (chat_store._fold), which is what makes
        # the two incapable of disagreeing.
        **_cut_payload(thread_id),
        # THE QUEUE RIDES ON THE SYNC ENDPOINT, because it is part of the answer
        # to "what is in this chat". It is small (it is empty on almost every
        # poll), and sending it here rather than from a second endpoint is what
        # makes it impossible for the transcript and the queue to disagree about
        # a message that is in one and not the other. Every device sees the same
        # queue for free.
        **chat_queue.state(thread_id),
        # A fold in flight rides here for exactly the same reason, and it is the
        # half that was missing: thread_ops held it in memory, no read published
        # it, and no stream frame announced it, so the only way to discover a
        # running compaction was to start a second one and be refused.
        "pending_operation": thread_ops.pending(thread_id),
    }


def _tail_page(thread_id: str, hidden: set[str] | None, n: int,
               before: int | None) -> tuple[list[dict], bool, int | None]:
    """The last `n` VISIBLE events of the current branch path, optionally ending
    strictly before seq `before`. Returns `(events, has_more, first_seq)`.

    Reads only the live file first (`read_live_events`); falls back to the full
    log (`read_events`, the existing after=0 path) only when that window does
    not hold `n` visible events after the branch filter -- see the route
    docstring for why the archive is the one case this stays O(log size) for.

    `has_more` is exact, never a guess, computed from what was actually counted:

      * whenever `visible` (this call's candidate pool, before slicing to `n`)
        holds more than the page it produced, there is provably more -- those
        events were seen and set aside, not merely suspected;
      * otherwise it depends on whether `visible` is EXHAUSTIVE for the range
        (whether it already accounts for every event below `before`, live and
        archived). It is exhaustive whenever the fallback ran (the archive read
        that produced it covers the whole path) or there never was an archive to
        miss. A live-only pool that under-filled `n` for a branchy thread with NO
        archive is exhaustive for the same reason. Only a live-only pool that
        already met `n` without checking the archive is not exhaustive: there
        might be more back there, and finding out for certain costs exactly the
        archive read this function exists to avoid on the common thread, so it
        answers `True` rather than pay for the certainty.

    A page whose count did not need trimming and whose pool is not proven
    exhaustive is the ONLY case that can over-report `has_more`; it never
    under-reports, so a client cannot stop paging with events still unseen.
    """
    n = max(0, int(n))
    live = chat_log.read_live_events(thread_id, before=before)
    visible = chat_branches.keep_visible(live, hidden)
    has_archive = chat_log.archive_path(thread_id).exists()
    exhaustive = not has_archive
    if len(visible) < n and has_archive:
        everything = chat_log.read_events(thread_id, after=0)
        if before is not None:
            everything = [e for e in everything if e["seq"] < before]
        visible = chat_branches.keep_visible(everything, hidden)
        exhaustive = True
    page = visible[-n:] if n and len(visible) > n else (visible if n else [])
    has_more = len(visible) > len(page) or not exhaustive
    return page, has_more, (page[0]["seq"] if page else None)


def _cut_payload(thread_id: str) -> dict:
    """`{cleared_through, cut_mode}`: how many messages are behind the context
    cut, and which command put them there. Zero means no cut.

    Meta only. This runs on every poll from every open tab, and reading it
    through `get_thread` parsed the entire log to answer it — the exact cost
    this endpoint was rebuilt to remove, and pinned by
    test_chat_turn_lifecycle.TestTheSyncEndpointDoesNotReparseTheLog."""
    return chat_store.cut_state(thread_id)


def _active_turn_payload(thread_id: str, meta: dict) -> dict | None:
    """The live turn, plus whether it can be spoken to.

    `command` is added HERE rather than in chat_log because only this layer can
    see the turn registry. The client needs it to decide between injecting a
    message and queueing it: a command turn takes no input, and a client that
    does not know that sends into a void."""
    turn = chat_log.active_turn(thread_id, meta=meta)
    if turn and not build_streaming.turn_accepts_input(turn.get("turn_id")):
        return {**turn, "accepts_input": False}
    return turn


@router.patch("/threads/{thread_id}", response_model=cm.ThreadSettings, response_model_exclude_unset=True)
async def patch_thread(thread_id: str, body: PatchThread):
    """Write this thread's settings. Only the keys present in the body change.

    `armed` is what advances the thread without a person; `role` names the role
    definition it runs under. They are independent, so both are ordinary
    optional keys here and setting one never touches the other."""
    sent = body.model_fields_set
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    if "title" in sent and body.title is not None:
        t = chat_store.rename_thread(thread_id, body.title)
    if "armed" in sent:
        t = chat_store.set_thread_armed(thread_id, body.armed)
    if "role" in sent:
        t = chat_store.set_thread_role(thread_id, body.role)
    if t is None:
        raise HTTPException(404, "thread not found")
    return _without_messages(t)


@router.post("/threads/{thread_id}/archive", response_model=cm.ThreadSettings, response_model_exclude_unset=True)
async def archive(thread_id: str):
    """Close the conversation without destroying it. This is what the chat's own
    menu does; DELETE below is reachable only from the archive."""
    t = chat_store.archive_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    return _without_messages(t)


@router.post("/threads/{thread_id}/restore", response_model=cm.ThreadSettings, response_model_exclude_unset=True)
async def restore(thread_id: str):
    t = chat_store.restore_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    return _without_messages(t)


# --- branches (ADR 0020) -----------------------------------------------------
# Going back to a point in the conversation and carrying on from there. There is
# ONE verb for it, because in a store where nothing is deleted, "return to here"
# and "branch from here" are the same act: the line you were on keeps its own
# ref and stays listed below. Two buttons would be two names for one operation.

@router.get("/threads/{thread_id}/branches", response_model=cm.BranchList, response_model_exclude_unset=True)
async def branches(thread_id: str):
    """Every branch of this conversation, most recently active first.

    Returns an empty list rather than an error when the relational store is not
    reachable: a chat renders without this, it just cannot be branched.
    """
    return {"branches": await chat_branches.list_branches(thread_id)}


def _require_quiet_for_branching(thread_id: str, verb: str) -> None:
    """Branching is an operation on the stored conversation, so it obeys the
    one-op-per-thread rule (ADR 0021) like /compact and /clear do.

    It REFUSES rather than queueing, which is where it differs from those two. A
    fold is slow and can sensibly wait its turn; going back to an earlier point
    cannot, because the thing it would wait for is the very turn it is trying to
    get rid of. Landing it afterwards would answer a message on a line that no
    longer contains it.

    Unguarded, that is not hypothetical: `chat_repo.append_message` parents a new
    message to the CURRENT branch head, and the fork has already moved that
    pointer, so an in-flight reply committed onto the new branch as a child of
    the fork anchor. The operator would go back to before a question, and the
    answer to that question would arrive on the line he had just cleared it
    from.
    """
    if thread_ops.pending(thread_id):
        raise HTTPException(
            409, f"this chat is busy ({thread_ops.pending(thread_id)}). "
                 f"Try {verb} again in a moment.")
    rid = chat_store.active_turn_id(thread_id)
    if rid and build_streaming.turn_is_live(rid):
        raise HTTPException(
            409, "a turn is running on this chat. Wait for it to finish, or "
                 "stop it first.")


@router.post("/threads/{thread_id}/branches", response_model=cm.ForkResult, response_model_exclude_unset=True)
async def fork_thread(thread_id: str, body: ForkThread):
    """Carry on from `anchor`. Nothing is deleted."""
    from db import chat_repo
    if body.mode not in ("after", "before"):
        raise HTTPException(400, "mode must be 'after' or 'before'")
    _require_quiet_for_branching(thread_id, "going back")
    # Messages the SERVER is holding to send next were typed against the line
    # being left, and when the drain wakes they will go out on the new one. That
    # is usually right (unsent text is still what you meant to say) and
    # occasionally not ("yes, do that" now refers to nothing), and nothing here
    # can tell the two apart. So it is REPORTED rather than guessed at: cancelling
    # for him would destroy work he never asked to destroy, and staying silent is
    # how it became a surprise. Requiring a quiet thread above already makes this
    # the narrow case it looks like — the queue only fills behind a turn or an
    # operation, and neither is allowed to be in flight here.
    held = len(chat_queue.read(thread_id) or [])
    try:
        out = await chat_branches.fork(thread_id, body.anchor, body.mode)
    except chat_repo.MessageNotFound as e:
        raise HTTPException(404, str(e))
    except chat_repo.BranchError as e:
        raise HTTPException(409, str(e))
    except tenancy.TenantUnresolved as e:
        raise HTTPException(503, str(e))
    # THE MODEL'S COPY OF THE CONVERSATION HAS TO GO, or the fork is a no-op for
    # everything except the screen.
    #
    # A turn resumes the thread's live CLI session when it has one, and a resumed
    # turn sends ONLY the new message: the history the model reasons over is the
    # session's own, private to the CLI, and it still holds every message on the
    # line just abandoned. So going back to an earlier point changed what the
    # operator saw and nothing about what the model read. Dropping the session
    # forces the next turn to mint a fresh one and re-assemble the prompt from
    # the transcript, which _fold now filters to the current branch.
    #
    # This is also what makes going back past a /compact behave the way it reads.
    # The summary lives on the boundary row (chat_store._fold), so a fork to
    # before that boundary leaves it off the path and the prompt is the full
    # earlier history again, with no summary of a line that no longer exists.
    # Without the detach the CLI would have kept the compacted session regardless
    # and none of that would have been visible.
    # THE CACHED CUT IS RE-DERIVED, because it is the one piece of cut state that
    # is not branch-aware. `_fold` reads the log through the branch filter, but
    # `cut_state` answers the sync endpoint from `meta.cut_through` alone (that is
    # deliberate: it runs on every poll from every open tab and must not read the
    # log). Moving to a line whose boundary is somewhere else leaves that cache
    # describing a cut this line does not have, so the divider and the prompt
    # would be answering about different conversations. Re-resolving costs one
    # log read here, at an action the operator took, instead of one per poll.
    chat_store.resolve_cut(thread_id)
    t = chat_store.get_thread(thread_id)
    if t is not None:
        _detach_live_session(
            thread_id, t, chat_context.get_provider(t.get("context_key")))
    chat_events.bump(thread_id)
    return {**out, "queued_held": held}


@router.post("/threads/{thread_id}/branches/{branch_id}", response_model=cm.SwitchResult, response_model_exclude_unset=True)
async def switch_branch(thread_id: str, branch_id: str):
    """Make another branch the visible one. Reversible, and nothing moves but
    one pointer, which is why it does not ask for confirmation."""
    from db import chat_repo
    # Same rule as forking, for the same reason: the pointer this moves is the
    # one an in-flight reply parents itself to.
    _require_quiet_for_branching(thread_id, "switching")
    try:
        out = await chat_branches.switch(thread_id, branch_id)
    except chat_repo.BranchError as e:
        raise HTTPException(404, str(e))
    except tenancy.TenantUnresolved as e:
        raise HTTPException(503, str(e))
    # Same reason as forking: switching lines changes which conversation the
    # transcript describes, and a resumed session would carry the old one's
    # history into the next turn regardless. See fork_thread. Switching BACK to a
    # line that was compacted is what makes re-resolving load-bearing rather than
    # tidy: that line's boundary is real again, so its cut has to come back.
    chat_store.resolve_cut(thread_id)
    t = chat_store.get_thread(thread_id)
    if t is not None:
        _detach_live_session(
            thread_id, t, chat_context.get_provider(t.get("context_key")))
    chat_events.bump(thread_id)
    return out


@router.patch("/threads/{thread_id}/branches/{branch_id}", response_model=cm.RenameBranchResult, response_model_exclude_unset=True)
async def rename_branch(thread_id: str, branch_id: str, body: RenameBranch):
    from db import chat_repo
    try:
        out = await chat_branches.rename(thread_id, branch_id, body.name)
    except chat_repo.BranchError as e:
        raise HTTPException(404, str(e))
    except tenancy.TenantUnresolved as e:
        raise HTTPException(503, str(e))
    chat_events.bump(thread_id)
    return out


@router.delete("/threads/{thread_id}", response_model=cm.ThreadDeleted, response_model_exclude_unset=True)
async def remove(thread_id: str):
    # The queue goes with the conversation it belongs to. Left behind it would
    # be text nothing can ever deliver, held forever under an id with no thread.
    chat_queue.forget(thread_id)
    # And so do its alarms: a wake-up for a deleted conversation can only ever
    # fire into nothing.
    wakeups.forget(thread_id)
    chat_events.forget(thread_id)
    t = chat_store.get_thread(thread_id)
    draft_store.clear_for_thread(t.get("context_key") if t else None, thread_id)
    return {"deleted": chat_store.delete_thread(thread_id)}


@router.post("/threads/{thread_id}/card-state", response_model=cm.CardStates, response_model_exclude_unset=True)
async def set_card_state(thread_id: str, body: CardState):
    """Record that an in-chat card has been acted on.

    Cards used to be stateless: the acted-on state lived in component state only,
    so a card the operator had already filed and run came back offering the same
    buttons on the next load, with no way to tell it apart from one that had
    never been touched. Acting on a card is a real event about the conversation,
    so it is stored with the conversation."""
    t = chat_store.set_card_state(thread_id, body.key, body.model_dump(exclude={"key"}))
    if t is None:
        raise HTTPException(404, "thread not found")
    return {"card_states": t.get("card_states", {})}


class SetCustomInstructions(BaseModel):
    scope: str = "workshop"
    text: str = ""


def _canon_scope(scope: str) -> str:
    """Canonicalize an incoming scope/context key to the knowledge scope the
    store and the chat's custom_prompt() both use, so a value the UI holds as a
    context key ("general", or an app id) lands on the same key the chat reads
    ("workshop", "app:<id>"). Idempotent for an already-canonical scope."""
    from services import knowledge
    try:
        return knowledge.scope_for_context(scope)
    except Exception:
        return scope or "workshop"


@router.get("/custom-instructions", response_model=crm.CustomInstructions)
def get_custom_instructions(scope: str = "workshop"):
    """The operator's L3 custom instructions for one scope (empty if none)."""
    from services import custom_instructions
    key = _canon_scope(scope)
    return {"scope": key, "text": custom_instructions.get(key)}


@router.get("/custom-instructions/all", response_model=crm.CustomInstructionsScopes)
def all_custom_instructions():
    """Every scope that has custom instructions set, for the settings UI. Keys
    are knowledge scopes (workshop, development, project:*, app:*, agent:*)."""
    from services import custom_instructions
    return {"scopes": custom_instructions.all_scopes()}


@router.post("/custom-instructions", response_model=crm.CustomInstructions)
def set_custom_instructions(body: SetCustomInstructions):
    """Set (or clear, when text is blank) the L3 custom instructions for a scope.
    Applies to that scope's chats from their next turn; the platform layers above
    it are unaffected and cannot be edited here."""
    from services import custom_instructions
    key = _canon_scope(body.scope)
    saved = custom_instructions.set(key, body.text)
    return {"scope": key, "text": saved}


_DISTILL_PROMPT = (
    "From this conversation, write ONE self-contained ticket a background worker "
    "can execute WITHOUT seeing the chat.\n\n"
    "Return STRICT JSON, no prose, no code fence:\n\n"
    '{"title": "short imperative line, under 80 characters",\n'
    ' "objective": "the concrete outcome, one sentence",\n'
    ' "output": "what done looks like: the artifact, or how it is judged",\n'
    ' "entry_points": "the files, commands or symbols to read or run first",\n'
    ' "boundaries": "what NOT to touch and what not to build"}\n\n'
    "objective, output and boundaries are REQUIRED. Never return one of them "
    "empty. The worker reads the title and the body and NOTHING ELSE: not this "
    "conversation, not the operator, not you. Anything the ticket does not say is "
    "guessed at or asked about, and both cost a turn.\n"
    "  - output is the finish line. Write the observable thing ('the endpoint "
    "returns the new field and a test covers it'), never 'it works'. Without it "
    "the worker picks its own finish line and is judged against a different one.\n"
    "  - boundaries is what to leave alone: the files, directories or systems "
    "that are off limits, and what NOT to build. Without it a one-file change "
    "becomes a refactor nobody asked for. If the work genuinely has no boundary, "
    "write 'none: may touch anything the objective requires'. A stated none is a "
    "decision; an empty field is an omission.\n"
    "  - entry_points: the honest answer counts. 'The chat named no files; start "
    "by grepping for X' is a real entry point.\n\n"
    "Do not invent a file path or an acceptance test the conversation never "
    "established. No em-dashes anywhere.\n\n"
    "=== CONVERSATION ===\n{convo}\n\n=== JSON ==="
)


async def _distill_task_from_thread(t: dict) -> str:
    """Turn the recent conversation into ONE self-contained ticket a background
    worker can execute without seeing the chat. Cheap Haiku call; best-effort.

    Returns `title\\nbody`, the shape `tasks.add` splits on, with the body composed
    through `ticket_format.compose()` so it carries the same four labelled fields
    every other producer emits and `ticket_readiness` already looks for. This used
    to ask for "one paragraph", which is why tickets filed from a chat failed the
    acceptance and boundaries checks at 74% and 68%: a paragraph states an
    objective and almost never states a finish line or an out-of-scope.

    A model that returns something other than JSON is NOT discarded. The raw text
    becomes the ticket, exactly as before, because a worse-specified ticket beats
    a 422 at the moment the operator pressed the button.
    """
    msgs = t.get("messages", [])[-12:]
    convo = "\n\n".join(
        f"{'Operator' if m.get('role') == 'user' else 'Assistant'}: {(m.get('content') or '').strip()}"
        for m in msgs if (m.get("content") or "").strip()
    )
    if not convo.strip():
        return ""
    prompt = _DISTILL_PROMPT.replace("{convo}", convo)
    try:
        res = await asyncio.to_thread(llm_router.call_cloud, prompt, model=llm_router.TIER_HAIKU)
        # The prompt asks for no em-dashes, but enforce it: the operator's hard rule
        # is no em-dashes anywhere, and Haiku emits them anyway. (review L7)
        text = (res.get("text") or "").strip().replace("—", ", ")
    except Exception as e:
        log.warning("spawn-task distill failed: %s", e)
        return ""
    return _distilled_text(text)[:2000]


def _distilled_text(raw: str) -> str:
    """`title\\nbody` out of the distiller's reply. Pure, so it is testable.

    Tolerant on the way in for the same reason `ticket_drafts._parse` is: a fence
    or a stray sentence around the JSON is a formatting slip, not a reason to
    lose the ticket the operator just asked for.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    body = text
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        body = fence.group(1).strip()
    brace = body.find("{")
    if brace >= 0:
        try:
            data = _json.loads(body[brace:])
        except _json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            def field(name: str, limit: int = 600) -> str:
                return str(data.get(name) or "").strip().replace("—", ", ")[:limit]

            title = field("title", 140)
            composed = ticket_format.compose(
                objective=field("objective"),
                output=field("output"),
                entry_points=field("entry_points"),
                boundaries=field("boundaries"),
            )
            if title and composed:
                return f"{title}\n{composed}"
            # A JSON reply with no title is not a ticket; fall through to the raw
            # text rather than filing a body with no first line to split off.
    return text


class DraftPatch(BaseModel):
    # Acceptance is no longer a separate field: it lives in the body as the
    # `Output:` line (services/ticket_format), so editing the body edits it.
    title: str | None = None
    body: str | None = None


def _drafts_scope(thread_id: str) -> tuple[str, object]:
    """The scope a chat's tickets live in, and its root. Same resolution the
    file-a-ticket path uses, so a proposal and a filed ticket never land in two
    different queues."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    scope_key = t.get("context_key")
    root = task_scope.root_for_context(scope_key)
    if root is None:
        scope_key, root = "workshop", task_scope.root_for_context("workshop")
    return scope_key, root


@router.get("/threads/{thread_id}/ticket-drafts", response_model=cm.TicketDrafts, response_model_exclude_unset=True)
async def get_ticket_drafts(thread_id: str):
    """This chat's open DRAFT TICKETS, with staleness against the transcript.

    Never regenerates: scanning costs a model call and tens of seconds, and
    silently redoing it (or losing the operator's corrections) is the behaviour
    that makes people stop using a feature. Reads the ticket store, so a draft
    edited or approved elsewhere shows up here with nothing to sync."""
    scope_key, root = _drafts_scope(thread_id)
    try:
        return ticket_drafts.get(thread_id, root, scope_key)
    except KeyError:
        raise HTTPException(404, "thread not found")


@router.post("/threads/{thread_id}/ticket-drafts", response_model=cm.TicketDrafts, response_model_exclude_unset=True)
async def make_ticket_drafts(thread_id: str):
    """Scan the conversation and file 0..N proposals as draft tickets.

    Zero is a normal answer. A conversation is not one ticket, and most of what
    is said in one is discussion, reversal, or work already done. Filing them as
    drafts is safe: `next_ready` never picks a draft and the turn endpoint refuses
    one, so proposing can never start work."""
    scope_key, root = _drafts_scope(thread_id)
    try:
        return await ticket_drafts.propose(thread_id, root, scope_key)
    except KeyError:
        raise HTTPException(404, "thread not found")
    except Exception as e:
        raise HTTPException(502, f"could not draft tickets: {e}")


@router.patch("/threads/{thread_id}/ticket-drafts/{task_id}", response_model=cm.TicketDrafts, response_model_exclude_unset=True)
async def patch_ticket_draft(thread_id: str, task_id: str, body: DraftPatch):
    """Edit a proposed ticket before approving it. Writes the real ticket."""
    scope_key, root = _drafts_scope(thread_id)
    patch = body.model_dump(exclude_none=True)
    updated = tasks.update(
        root, task_id,
        title=patch.get("title"),
        body=patch.get("body"),
    )
    if updated is None:
        raise HTTPException(404, f"draft not found: {task_id}")
    return ticket_drafts.get(thread_id, root, scope_key)


@router.delete("/threads/{thread_id}/ticket-drafts/{task_id}", response_model=cm.TicketDrafts, response_model_exclude_unset=True)
async def delete_ticket_draft(thread_id: str, task_id: str):
    """Discard a proposal. `cancelled`, not deleted: no ticket is ever deleted by
    any path, so a rejected proposal stays auditable and cannot be re-proposed
    into the same slot by the next scan."""
    scope_key, root = _drafts_scope(thread_id)
    if tasks.update(root, task_id, status="cancelled") is None:
        raise HTTPException(404, f"draft not found: {task_id}")
    return ticket_drafts.get(thread_id, root, scope_key)


@router.get("/threads/{thread_id}/ticket-scope", response_model=cm.TicketScope, response_model_exclude_unset=True)
async def ticket_scope(thread_id: str):
    """Where this chat's tickets file, and whether that scope can run them."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    scope_key = t.get("context_key")
    if task_scope.root_for_context(scope_key) is None:
        scope_key = "workshop"
    from routes.tasks_all import scope_pickup
    # `runnable` answers "can this be run by hand". `automatic` answers "will
    # anything pick it up on its own", which is false for every project scope
    # today and was previously invisible until the ticket sat forever.
    return scope_pickup(scope_key)


@router.get("/threads/{thread_id}/work", response_model=cm.ThreadWork, response_model_exclude_unset=True)
async def thread_work(thread_id: str):
    """Everything this chat's work surfaces show, in one read.

    The badge, the Work panel and every ticket card in the transcript poll this
    and nothing else. They used to poll `/inbox` and `/tasks` on separate
    intervals, which meant three surfaces could hold three copies of one ticket
    and disagree about its status. Assembly is in services/chat_work.
    """
    from services import chat_work
    out = chat_work.thread_work(thread_id)
    if out is None:
        raise HTTPException(404, "thread not found")
    return out


@router.post("/threads/{thread_id}/spawn-task", response_model=cm.SpawnedTask, response_model_exclude_unset=True)
async def spawn_task(thread_id: str, body: SpawnTask):
    """Turn a chat into a self-contained background task (Stage 4). Files a ticket
    into a project's structured queue, with provenance back to this chat, so the
    work is tracked and executed in the background by the dispatcher and never
    lost when the conversation scrolls away. Scope: an explicit `project`, else the
    chat's own binding, else the workshop root queue (a general chat has nowhere
    of its own to file to)."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")

    # Resolve a canonical scope KEY (workshop / project:<slug> / agent:<slug>)
    # so the same key both files the task AND drives the turn
    # launcher. An explicit project wins; else the chat's own binding when it has
    # a task scope; else the workshop root queue (a general chat has none of its
    # own to file to yet).
    root = None
    scope_key = None
    if body.scope:
        candidate = body.scope.strip()
        resolved = task_scope.root_for_context(candidate)
        if resolved is not None:
            scope_key, root = candidate, resolved
    if root is None and body.project:
        scope_key = f"project:{body.project}"
        root = task_scope.root_for_context(scope_key)
    if root is None:
        ctx = t.get("context_key")
        resolved = task_scope.root_for_context(ctx)
        if resolved is not None:
            scope_key = ctx
            root = resolved
    if root is None:
        scope_key = "workshop"
        root = task_scope.root_for_context("workshop")

    text = (body.text or "").strip()
    if not text:
        text = await _distill_task_from_thread(t)
    if not text:
        raise HTTPException(422, "could not derive a task from this chat; add a note first")

    acceptance = body.acceptance if isinstance(body.acceptance, dict) else None
    # Cross-chat dedup (Stage 7 / 2e): fold an exact duplicate into the existing
    # task, or create fresh. The decision is returned so the chat can tell the
    # operator what happened.
    #
    # ONLY THE FREE TIER RUNS HERE. The semantic tier embeds the new text
    # against every open ticket and then asks a local model to confirm a merge;
    # measured 2026-08-16, that was 17.5s on a cold vector cache and 2-10s on a
    # warm one, all of it inside the click. "Why does creating tickets take
    # several seconds" is that, and the answer is that a button must not wait
    # for it. The canonical-hash tier is pure computation and stays.
    from services import task_dedup
    # A refusal here is the acceptance gate (services/ticket_format): the dialog
    # gets a 400 whose message says what would satisfy it, beside the draft it
    # already has, rather than an unhandled 500 on a button click.
    try:
        result = task_dedup.dedup_add(
            root, text=text, acceptance=acceptance,
            priority=body.priority or 0,
            effort=(body.effort or "").strip() or None,
            type=(body.type or "").strip() or None,
            source={"kind": "chat", "ref": thread_id},
            # The operator asked for the work; the ASSISTANT wrote the spec. `source`
            # records the first, `scoped_by` the second, and conflating them is what
            # makes a bad spec untraceable — the field would say the operator wrote a
            # paragraph a model actually wrote. Distilled-from-thread specs are the
            # main way tickets get filed, so this is the common case, not an edge.
            scoped_by="agent:chat",
            # `todo`, both for "File ticket" and "File and run". This endpoint is only
            # ever reached by the operator clicking a button in the ticket-drafts
            # dialog or on a task-proposal card, having read and often edited the
            # draft first. That click IS the approval, and making it land as a `draft`
            # for a second approval would be the "say the magic word" step the
            # operator has explicitly rejected.
            #
            # `draft` is for filings NOBODY looked at: a task-patch block the
            # assistant emitted mid-reply (services/task_scope), the triager turning
            # a note into a ticket, and imports from an external tracker.
            status="todo",
            # SOMEONE IS WATCHING A SPINNER, so the comparison that costs seconds
            # runs after this returns (below) instead of inside it.
            semantic=False,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # The semantic comparison, off the request. It LINKS a near-duplicate rather
    # than merging one, because a merge decided after the operator has watched
    # his ticket appear would take it away again.
    _filed_id = (result.get("task") or {}).get("id")
    # CHAT -> LOOP. If this scope is ARMED for background execution, a freshly
    # filed ticket is classified executable (execution="code") so the sweep can
    # actually pick it up; in a reactive scope it stays unclassified and
    # fail-closed, waiting for a person. This is the one place the "auto-run work
    # filed here" setting connects to filing. It only makes the ticket ELIGIBLE:
    # supervised mode still lands it at in_review with the operator as final gate.
    if _filed_id and result.get("decision") not in ("exact", "merge"):
        try:
            from services import scope_execution
            if scope_execution.is_armed_root(root) and (result.get("task") or {}).get("execution") is None:
                tasks.update(root, _filed_id, execution="code")
                result["task"]["execution"] = "code"
        except Exception:
            log.warning("execution classification on file failed for %s", _filed_id, exc_info=True)
    if _filed_id and result.get("decision") not in ("exact", "merge"):
        async def _link_after_response(root_=root, tid=_filed_id):
            try:
                out = await asyncio.to_thread(task_dedup.link_near_matches, root_, tid)
                if out.get("status") == "linked":
                    log.info("dedup linked %s to %s (cosine %s)",
                             tid, out.get("matched_id"), out.get("cosine"))
            except Exception:
                log.warning("dedup link pass failed for %s", tid, exc_info=True)
        asyncio.create_task(_link_after_response())
    resp = {
        "task": result["task"],
        "scope": scope_key,
        "thread_id": thread_id,
        "decision": result["decision"],
        "matched_id": result.get("matched_id"),
        "cosine": result.get("cosine"),
        "message": result["message"],
    }

    # Signal the resident queue runner so an armed scope starts on this ticket
    # now instead of waiting out the poll. The /tasks POST path has always done
    # this; the chat filing path never did, which is the path that matters most
    # here because it is the one the operator files from.
    try:
        (paths.workshop_root() / ".runtime" / "queue-runner.wake").touch()
    except OSError:
        pass

    # File AND run: launch a worker now for a freshly filed task. Never run a
    # merge/exact (it folded into an existing task; running that would be a
    # surprise re-run of unrelated work). C12 (2026-07-18): the server used
    # to append a second `job-status` ui-card message after the turn started,
    # so the operator saw two cards for one spawn (the task-proposal card's
    # own success state + the separate job-status card). We now return the
    # job_id in the response and let the source card render it; Work and the
    # Home attention strip surface the live run without a duplicate here.
    if body.run and result["decision"] not in ("merge", "exact"):
        task_rec = result.get("task") or {}
        task_id = task_rec.get("id")
        if task_id and scope_key:
            from routes.tasks_all import launch_task_run
            try:
                # The thread is the turn's origin. Recording it here is what
                # makes the turn navigable back to the conversation that asked
                # for it; it was the only linkage field never written.
                run_res = launch_task_run(scope_key, task_id, chat_id=thread_id)
            except HTTPException as e:
                resp["run_error"] = e.detail
            else:
                resp["job_id"] = run_res["job_id"]
    return resp


@router.post("/threads/{thread_id}/mode", response_model=cm.ChatModeSet, response_model_exclude_unset=True)
async def set_chat_mode(thread_id: str, body: SetChatMode):
    """Switch a chat between Direct and Auto.

    Cheap and reversible, so it commits on change rather than behind a Save. The
    mode is re-stated to the model on every turn (chat_context.chat_mode_block),
    which is what lets a switch take effect mid-conversation instead of only on
    a fresh session."""
    t = chat_store.set_thread_mode(thread_id, _accepted_mode(body.mode))
    if t is None:
        raise HTTPException(404, "thread not found")
    return {"id": thread_id, "mode": t.get("mode")}


@router.post("/threads/{thread_id}/rescope", response_model=cm.Rescoped, response_model_exclude_unset=True)
async def rescope_thread(thread_id: str, body: RescopeThread):
    """Re-bind a thread to a new scope. Chat and Project are loosely coupled:
    this is a binding change, not a conversion or a promotion.

    Thread identity is its id + message history, which are unchanged: only the
    context_key field moves (e.g. `general` -> `project:<slug>`), so the next
    turn layers the new provider's context on top of the same conversation.
    Multiple threads may share a context_key (the store filters by key, it never
    assumes uniqueness), so no collision handling is needed."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")

    new_key = (body.context_key or "").strip()
    if not new_key:
        raise HTTPException(400, "context_key required")

    # Validate the target resolves to a REAL provider. get_provider constructs a
    # provider for any project: key and returns the matching-key provider
    # for a registered static key, but falls back to `general` for an unknown
    # static key, so a resolved key that doesn't equal the request is bogus.
    provider = chat_context.get_provider(new_key)
    if provider.key != new_key:
        raise HTTPException(400, f"unknown context_key: {new_key}")
    # A project: key constructs a provider for ANY slug, so also require the
    # project to actually exist on disk; otherwise the re-keyed thread would be
    # a builder chat over a nonexistent cwd that fails on its next turn.
    if isinstance(provider, chat_context.ProjectContextProvider) and not provider.dir.exists():
        raise HTTPException(400, f"unknown project: {new_key.split(':', 1)[1]}")

    old_key = t.get("context_key") or ""
    t["context_key"] = new_key
    chat_store.save_thread(t)
    # A REBIND IS SOMETHING THAT HAPPENED TO THIS CONVERSATION, so it goes in the
    # log, at the point in the order where it happened.
    #
    # The transcript used to show this as a client-side annotation held in React
    # state: the note appeared for the browser that flipped the picker, vanished
    # on reload, and never existed at all on any other device. A line in the
    # transcript that only one browser can see is the same defect as a message
    # only one browser is holding — the reader cannot tell which parts of what
    # they are looking at are real. Everything rendered in a transcript comes
    # from the transcript.
    if old_key and old_key != new_key:
        chat_log.append_sync(thread_id, {
            "kind": "scope", "from": old_key, "to": new_key,
        })
    # AND DROP THE LIVE SESSION. The next turn resumes the claude session this
    # thread already has, and a resumed session is handed only the new message:
    # the persona, the protocol blocks and the project briefing were assembled
    # once, at the start, from the OLD context. So re-keying alone moved the cwd
    # and the write tools to the new scope while leaving the model believing it
    # was still in the old one — the half-move that makes a rescoped chat act
    # like a project chat with someone else's instructions.
    #
    # Clearing it is cheap and loses nothing: the next turn re-assembles the full
    # prompt (the transcript after the cut) for a fresh session, which is the same
    # path a pruned session already takes.
    chat_store.clear_thread_session(thread_id)

    msgs = t.get("messages", [])
    return {
        "id": t["id"],
        "context_key": new_key,
        "agent_id": t.get("agent_id"),
        "model": t.get("model"),
        "title": t.get("title", "New chat"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
        "message_count": len(msgs),
        "last_message": (msgs[-1]["content"][:300] if msgs else ""),
    }


def _within_ceiling(history: list[dict]) -> tuple[list[dict], int]:
    """(messages to send, how many were left out). Left out is 0 almost always.

    The fuse described at PROMPT_CEILING_TOKENS. It keeps the NEWEST messages,
    because the recent end is what the next reply is actually about, and it
    returns the count it dropped so the caller can say so in the prompt. It never
    touches the stored transcript: this is an assembly-time decision and the
    thread is unchanged.

    Whole messages only. Cutting one in half to hit a byte target would hand the
    model a fragment with no speaker and no end, which is worse than one fewer
    message."""
    if not history:
        return history, 0
    budget = PROMPT_CEILING_TOKENS * _CHARS_PER_TOKEN
    total = sum(len(m.get("content") or "") for m in history)
    if total <= budget:
        return history, 0
    kept: list[dict] = []
    used = 0
    for m in reversed(history):
        size = len(m.get("content") or "")
        if used + size > budget and kept:
            break
        kept.append(m)
        used += size
    kept.reverse()
    dropped = len(history) - len(kept)
    log.warning("prompt ceiling hit: %d of %d messages left out (~%dk tokens)",
                dropped, len(history), total // _CHARS_PER_TOKEN // 1000)
    return kept, dropped


def _build_prompt(
    provider: chat_context.ContextProvider,
    history: list[dict],
    user_msg: str,
    *,
    preamble: str = "",
    summary: str = "",
    tools_active: bool = False,
    thread_id: str = "",
) -> str:
    # `thread_id` is used for exactly one thing: telling the scope's working
    # state apart from THIS thread's own compaction summary when they are the
    # same text (see the WORKING STATE block below). Defaulted to "" rather
    # than made required, because a caller that does not know which thread it
    # is building for is asking for a prompt, not for that de-duplication, and
    # the worst case of not knowing is one block injected twice rather than
    # anything incorrect.
    # Layered prompt (see docs 01 §3 and agent-context-and-toolkit-2026-08-20):
    #   0. PLATFORM KERNEL (L0) — confidential, non-editable, highest authority.
    #   1. ROLE PREAMBLE (platform-owned safety/capability facts) — computed from
    #      the real run config in the caller, so it can't drift from reality.
    #   1b. ORIENTATION MANIFEST (L1) — the tool-centric "where things are" map,
    #      only when tools are active (it points at tools).
    #   2. App/project context (persona + live snapshot).
    #   3. User-owned custom prompt (provider.custom_prompt(); default "").
    #   4. The conversation.
    parts: list[str] = []
    # L0 first, always, so it heads the cache-stable prefix and outranks
    # everything below it.
    parts.append(chat_context.platform_kernel())
    if preamble:
        parts.append(preamble)
    # Build providers get the templated runtime briefing right after the platform
    # preamble: cwd, stack, app services/ports, how to run+see the app, edit
    # scope, negative capabilities. Empty for data providers.
    briefing = chat_context.runtime_briefing(provider)
    if briefing:
        parts.append(briefing)
    # L1 orientation: the tool-centric map, so nothing has to grep to find
    # uploads/notes/docs/tickets/runs. Only when tools are actually granted,
    # since it points at tools a tools-off chat would not have.
    if tools_active:
        manifest = chat_context.orientation_manifest(provider)
        if manifest:
            parts.append(manifest)
    parts.append(provider.persona())
    # Capability-aware fragment (doc 01 §3): consult the permissions framework so
    # an app-bound assistant states its REAL allowed actions instead of promising
    # things it can't do. Additive + best-effort: returns "" for non-app-bound
    # providers (general/build) and never raises, so it can't break the chat path.
    # Default-permissive — for the operator this only adds accurate "what you may
    # do" facts; it does not gate the operator.
    try:
        cap_note = provider.capability_note("operator")
    except Exception as e:
        log.warning("chat capability_note failed for %s: %s", provider.key, e)
        cap_note = ""
    if cap_note:
        parts.append(cap_note)
    # These three blocks were briefly operator-only, because all three read
    # stores that had no owner concept: `learnings`, `workspace_digest`'s
    # cross-chat roll-up, and `shared/knowledge/`'s `workshop` layer, which
    # `read_layers` puts into EVERY scope's prompt by design. Gating them
    # stopped the leak and left a second account with a Workshop whose memory,
    # digest and knowledge silently did nothing.
    #
    # The stores are per-owner now (`knowledge._shared_knowledge_root`,
    # `learnings._dir`), so this runs for everybody again, against their own.
    # That is the whole point of partitioning rather than gating: the check
    # disappears instead of accumulating.
    # Learned memory (Stage 8 / 2f): workspace guardrails for every agent, plus a
    # scoped agent/project's own accumulated memory. Sits in the cache-stable
    # prefix (learnings append oldest-first so the earlier bytes stay stable).
    # Best-effort; never blocks the reply.
    try:
        learn_block = chat_context.learnings_context(provider)
    except Exception as e:
        log.warning("chat learnings block failed for %s: %s", provider.key, e)
        learn_block = ""
    if learn_block:
        parts.append(learn_block)
    # Ambient cross-chat work digest (Stage 7 / 2e): open tasks + in-flight/recent
    # jobs from the relevant scope (own scope for a builder/project/agent chat;
    # workspace-wide for a general chat) so chats stop clashing and losing track
    # of each other's work. Cache-stable prefix, capped small. Best-effort.
    try:
        ambient_block = chat_context.ambient_digest(provider)
    except Exception as e:
        log.warning("chat ambient digest failed for %s: %s", provider.key, e)
        ambient_block = ""
    if ambient_block:
        parts.append(ambient_block)
    # THIS SCOPE'S WORKING STATE, written by the last compaction that happened
    # anywhere in this scope (routes/chat._write_working_state_now ->
    # knowledge.write_working_state). It is what makes compaction worth
    # something to a chat OTHER than the one that compacted: objective,
    # decisions with their reasons, done, not done, shortcuts owed, open
    # questions. Sits right after the ambient digest because the two are
    # complementary and the block itself states which of them owns what: the
    # digest owns ticket and job STATUS, this owns REASONING.
    #
    # Bounded to knowledge.WORKING_STATE_MAX_CHARS (~1200 tokens) at both the
    # write and the read, and NOT taken from the knowledge index's 500-token
    # budget, which is spent on note titles and would be emptied by one
    # handoff. Best-effort like its neighbours; never blocks the reply.
    try:
        ws_block = chat_context.working_state_block(provider)
    except Exception as e:
        log.warning("chat working state block failed for %s: %s", provider.key, e)
        ws_block = ""
    if ws_block:
        parts.append(ws_block)
    # Ambient in-scope knowledge index (Phase 2): the note titles for this scope so
    # the model knows what it can recall. Small + cache-stable (bodies arrive via
    # retrieval in the reply region, not here). Best-effort; never blocks the reply.
    # Read the UNION of the layers this agent learns from, not just its own
    # place: workshop (house style) + role:<role> (how this KIND of work is done
    # well) + place + private. Without the role layer, everything two reviewers
    # in two different projects learn about reviewing is either stranded in one
    # project or dumped into `workshop` where it pollutes every other reader.
    # See knowledge.read_layers.
    try:
        kscope = provider.knowledge_scope()
        krole = _agent_role_for(provider)
        kindex, kused = knowledge.layered_index_block(kscope, krole)
    except Exception as e:
        log.warning("chat knowledge index failed for %s: %s", provider.key, e)
        kindex, kused = "", []
    if kindex:
        parts.append(
            "=== KNOWLEDGE (layers, narrowest last - a narrower note wins) ===\n"
            f"{origin.escape_markers(kindex)}"
        )
        log.debug("chat knowledge layers for %s: %s", provider.key, kused)
    # Ambient skills (Phase 5): available skill names + descriptions in the stable
    # prefix so a data-mode assistant knows what it can invoke; full bodies load on
    # demand via the use_skill action (see _expand_use_skill). Builders discover
    # skills natively from their cwd .claude/skills, so this is data-mode only.
    if not chat_context.is_build_provider(provider):
        try:
            sblock = skills.descriptions_block(provider.knowledge_scope())
        except Exception as e:
            log.warning("chat skills block failed for %s: %s", provider.key, e)
            sblock = ""
        if sblock:
            parts.append(
                "=== SKILLS (available) ===\n" + sblock + "\n\n" + chat_context.USE_SKILL_PROTOCOL
            )
    # Operator-set custom instructions are stable (rarely change), so they stay in
    # the cache-stable prefix alongside persona/capability/knowledge index.
    try:
        custom = provider.custom_prompt()
    except Exception as e:
        log.warning("chat custom_prompt failed for %s: %s", provider.key, e)
        custom = ""
    if custom:
        parts.append("=== CUSTOM INSTRUCTIONS (operator-set) ===\n" + origin.escape_markers(custom))
    # WHAT COMPACTION LEAVES BEHIND. The messages this stands in for are cut from
    # `history` by the caller, so this is what the model is given about them
    # instead: one block, above the verbatim tail, in the stable part of the
    # prompt. Marked as a summary rather than pasted in as if it were the
    # conversation, because a model that cannot tell a summary from a transcript
    # will quote it back as something the operator said.
    if summary:
        parts.append(
            "=== EARLIER IN THIS CONVERSATION (summary) ===\n"
            "The messages before this point were folded into the summary below to "
            "reclaim context. They are NOT deleted: the full text is still in the "
            "transcript and is searchable with mcp__workshop__search. Treat this as "
            "a summary, not as anyone's words.\n\n"
            + origin.escape_markers(summary)
        )
    if history:
        history, over_ceiling = _within_ceiling(history)
        if over_ceiling:
            # SAID, not done quietly. The predecessor's whole failure was that it
            # deleted the start of a conversation and told nobody, so the model
            # answered confidently out of a transcript it did not know was
            # missing a beginning. Naming the count and the reason is what lets
            # it say "I cannot see the start of this" instead.
            parts.append(
                f"=== EARLIER MESSAGES NOT INCLUDED ({over_ceiling}) ===\n"
                f"This conversation is larger than one prompt can carry, so the "
                f"{over_ceiling} oldest messages were left out. They are NOT deleted: "
                f"they are still in the transcript, and they are searchable with "
                f"mcp__workshop__search. Say so if the answer depends on them."
            )
        # `meta.agent` names the AGENT that produced a reply when the operator
        # addressed one by @mention. It is stored on every such message and was
        # dropped here, so the assistant was handed another agent's words as its
        # own. The @mention path already did this correctly; this is the same
        # rule on the default path. See docs/decisions/0015 §8 case 1.
        convo = []
        # IN THE ORDER THINGS HAPPENED, not in log order. A message sent while a
        # reply was being written has a lower seq than that reply, so flat it
        # sits above the whole thing — including the part of the reply written
        # before it was sent. See chat_context.interleave_history.
        for m in chat_context.interleave_history(history):
            who = origin.speaker_label(m["role"], m.get("meta"))
            convo.append(f"{who}: {origin.escape_markers(m['content'])}")
        parts.append("=== CONVERSATION SO FAR ===\n" + "\n\n".join(convo))
    # --- VOLATILE TAIL (Phase 3 cache-stable ordering) ---------------------------
    # Everything that changes every turn goes AFTER the conversation so it never
    # invalidates the cached prefix above: live app state, then retrieved notes,
    # then the user turn.
    try:
        ctx = provider.live_context()
    except Exception as e:  # context is best-effort — never block the reply
        log.warning("chat context build failed for %s: %s", provider.key, e)
        ctx = ""
    if ctx:
        parts.append("=== LIVE CONTEXT (current app state) ===\n" + origin.escape_markers(ctx))
    parts.append(
        "=== NOW REPLY ===\n"
        f"Operator: {origin.escape_markers(user_msg)}\n\n"
        "Reply as the assistant. Output only your reply text (plus a plan-patch block if a "
        "change was requested). No 'You:' prefix."
    )
    return "\n\n".join(parts)


def _link_build_skills(provider) -> None:
    """Symlink the builder's in-scope skills into its cwd `.claude/skills/` so the
    Claude Code CLI discovers them natively. Idempotent, best-effort — never
    blocks a build turn."""
    try:
        linked = skills.link_into_cwd(str(provider.dir), provider.knowledge_scope())
        if linked:
            log.info("linked skills into %s: %s", provider.dir, linked)
    except Exception as e:
        log.info("skill link skipped for %s: %s", getattr(provider, "key", "?"), e)


# --- The context cut ---------------------------------------------------------
# THERE IS NO SERVER-SIDE FOLDING. It is not disabled, it is gone: no running
# summary, no Haiku summarize call, no distillation pass, no `/threads/:id/compact`
# endpoint, no `_FOLDING` guard, no keep-tail constant.
#
# What replaced it is one rule. A thread's prompt is every message after the last
# cut, and the only thing that makes a cut is an explicit `/clear`. That is the
# whole model, and it is enforced in chat_store: `update_thread` now REFUSES to
# move the cut, so nothing can hide messages as a side effect of writing a field.
#
# `/compact` still exists and still works. It is the CLI's own command, sent as a
# raw turn, and it reclaims the LIVE session's window, which is the thing that
# actually fills up. The server keeps no state for it and the stored transcript
# is untouched. The old implementation ran a second, server-side fold alongside
# it that replaced real turns with a summary of them, which is what made
# `/compact` feel destructive.
#
# The size backstop for a thread that gets away from us lives in `_build_prompt`
# (`PROMPT_CEILING_TOKENS`). It fires on SIZE, never on a message count, and it
# says so in the prompt rather than dropping messages silently.

# The backstop itself. NOT a policy, a fuse: it exists so a thread that somehow
# gets away from us produces a degraded prompt instead of an unsendable one.
#
# It fires on estimated SIZE, never on a message count. That distinction is the
# whole lesson of the thing it replaces: `MAX_HISTORY = 60` capped the count, and
# the largest thread in this store is ~110k tokens in THIRTEEN messages, so it
# sailed straight past while a 129-message thread less than half its size was
# amputated. A fuse measured in the wrong unit protects nothing.
#
# Set high on purpose. Nothing in the store is within 5x of it, and the CLI
# compacts its own session long before a live conversation could get here; this
# only covers fresh-session assembly, where the whole transcript is re-sent at
# once. If it ever fires, that is a fact worth seeing, not a routine trim.
PROMPT_CEILING_TOKENS = int(os.environ.get("WORKSHOP_PROMPT_CEILING_TOKENS", "600000"))
# The usual 4-chars-per-token approximation. Precision does not matter for a
# fuse; being in the right order of magnitude does.
_CHARS_PER_TOKEN = 4

def _has_live_session(t: dict, provider) -> bool:
    """Does this chat have a claude session whose window /compact can reclaim?

    Two homes, because maker agents predate per-thread sessions: a maker chat's
    session hangs off the AGENT record (one session per agent), everything else
    off the thread. Checking only the thread would silently skip the native
    compact for exactly the chats that need it most."""
    if isinstance(provider, chat_context.MakerAgentContextProvider):
        try:
            from services import build_agents as _ba
            rec = _ba.get_agent(provider.slug) or {}
            return bool(rec.get("claude_session"))
        except Exception as e:
            log.debug("compact: agent session lookup failed for %s: %s", provider.slug, e)
            return False
    return bool(t.get("claude_session"))


def _reset_live_session(t: dict, provider) -> bool:
    """Detach the live claude session so the next turn mints a fresh one instead
    of --resuming. Same two homes as `_has_live_session`. This is what /clear
    uses instead of a native CLI /clear: a native /clear empties the live
    window but leaves the *stored* transcript's cut alone, so
    the next stale-session prune or restart would re-resume from disk and
    silently undo the clear. A brand-new session id has nothing to resume from.

    Returns whether a session was actually there to drop (informational only —
    the thread's own `claude_session` is dropped by `chat_store.clear_thread_context`,
    this only has to reach the agent-keyed home)."""
    if isinstance(provider, chat_context.MakerAgentContextProvider):
        try:
            from services import build_agents as _ba
            rec = _ba.get_agent(provider.slug) or {}
            had = bool(rec.get("claude_session"))
            if had:
                _ba.reset_session(provider.slug)
            return had
        except Exception as e:
            log.debug("clear: agent session reset failed for %s: %s", provider.slug, e)
            return False
    return bool(t.get("claude_session"))


def _detach_live_session(thread_id: str, t: dict, provider) -> bool:
    """Drop the live session AND the meter reading that described it.

    `_reset_live_session` reaches the agent-keyed home; the thread's own
    `claude_session` is dropped here, because unlike /clear there is no
    `clear_thread_context` call behind this to do it.

    The context reading goes with it. It measured the window of the session
    being dropped, so keeping it pins the composer meter to a window that no
    longer exists: the exact symptom that made /compact read as dead, a
    four-message chat sitting at 142k because that was the last thing
    measured."""
    had = _reset_live_session(t, provider)
    chat_store.clear_thread_session(thread_id)
    chat_store.clear_thread_context_tokens(thread_id)
    return had


@router.post("/threads/{thread_id}/operation/cancel", response_model=cm.OperationCancelled, response_model_exclude_unset=True)
async def cancel_thread_operation(thread_id: str):
    """Call off the /compact or /clear in flight on this thread.

    THIS IS WHAT MAKES THE STOP CONTROL ON THE FOLD LINE REAL. A compact is a
    ~30s model call that holds the thread's queue while it runs, and until now
    the only way out was to wait: there was no cancel path at any layer, so a
    stop button there would have been decoration.

    It refuses the LANDING rather than killing the task. An operation computes
    for its whole life and writes once at the end, so declining to write is a
    complete cancel with no partial state to unwind. The summarizer finishes and
    its result is dropped; the registration is released the same way it always
    is, so anything queued behind the operation drains immediately.

    Answering `{"cancelled": false}` is not an error: it means the operation
    landed while this request was on the wire, which is a race, not a failure."""
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")
    op = thread_ops.cancel(thread_id)
    # LOGGED EITHER WAY. When this was reported as "stop did nothing", the access
    # log proved the request arrived and answered 200 and could say nothing about
    # whether it had found an operation to stop, which is the only interesting
    # part. One line settles that next time.
    log.info("operation cancel on %s: %s", thread_id, op or "nothing in flight")
    return {"cancelled": bool(op), "operation": op or ""}


# The command Workshop sends to run the CLI's own compaction. A constant so the
# route, the queue's command test and `build_streaming`'s "replace the CLI's
# context-usage dump with one sentence" branch are all naming the same string.
_COMPACT_COMMAND = "/compact"


@router.post("/threads/{thread_id}/compact", response_model=cm.CompactResult, response_model_exclude_unset=True)
async def compact_thread(thread_id: str):
    """Compact this chat by running the CLI's OWN `/compact` on its session.

    WORKSHOP NO LONGER SUMMARIZES ANYTHING ITSELF. Until 2026-08-29 this
    endpoint ran a custom fold: a Haiku call summarized everything but a
    ~4000-token tail, the summary was stored on the thread, the cut moved, and
    the live session was detached so the next turn would re-assemble
    `summary + tail`. That was ~280 lines carrying a token-budget walk, a
    user-turn alignment pass, a "meaningful fold" guardrail, a deferred landing
    task and a cancel protocol, and every one of those knobs was an answer to
    the question "what should a compaction keep" -- a question this project
    never actually had to answer, because the provider answers it.

    So it is deleted and the provider's own compaction is what runs. The CLI
    compacts here (measured 2026-08-27 on 2.1.143 with this backend's own spawn
    flags: `trigger:manual`, 27,088 -> 2,104 tokens in 17.4s), and it is already
    what happens UNASKED when a window fills -- 29 `trigger:auto` boundaries on
    record. The button and the automatic path now do the same thing, which they
    did not before.

    IT IS A TURN, because that is what a slash command is. `_is_raw_command`
    sends a single-line "/" message to the CLI verbatim instead of wrapping it
    in a prompt, which is what makes the CLI register it as a command rather
    than read it as text. `build_streaming` already handles the rest end to end:
    it watches for the `compact_boundary` meta event, replaces the CLI's
    context-usage dump with one sentence, writes the boundary the transcript
    draws its divider from, and re-derives the window reading.

    SO THIS DOES NOT WAIT FOR IT. It returns the turn that is now running; the
    divider, the reply and the new meter reading arrive on the change stream
    like any other turn's output. Reporting a fold count here is not possible
    any more and was never worth much: the count Workshop reported described
    Workshop's own fold, not the provider's.

    NOTHING IS DELETED, still. The transcript is append-only and untouched.
    Compaction shrinks what the MODEL is handed, never what the operator can
    read.

    THE SCOPE'S WORKING STATE IS STILL WRITTEN, and it is a separate feature
    that happened to ride on the fold's summary. See `_write_working_state`:
    one note per scope, so a chat that does not exist yet can continue from
    this one. It runs in the background off the transcript as it stands right
    now, and never delays or fails the compaction."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    # Off the transcript AS IT IS NOW, before the provider collapses it. Fired
    # and not awaited: the operator pressed compact to reclaim context, and a
    # handoff note is not allowed to stand between them and that.
    _spawn_working_state(thread_id, t)
    try:
        res = await _send(thread_id, _COMPACT_COMMAND)
    except Exception as e:  # noqa: BLE001 - report it, do not 500 the button
        log.warning("compact: could not start the command turn on %s: %s", thread_id, e)
        return {"error": str(e)}
    # QUEUED IS AN ACCEPTANCE, not a refusal. A command cannot be spoken into a
    # running turn (see `_send`), so it waits in the thread's own queue and the
    # drain sends it the moment the turn ends. Saying "busy, nothing happened"
    # over that would be false.
    if res.get("queued"):
        return {"queued": True}
    return {"turn_id": res.get("turn_id") or ""}


def _spawn_working_state(thread_id: str, t: dict) -> None:
    """Kick off the scope's working-state note without blocking the caller.

    Best effort in the strongest sense: a failure to schedule is a log line.
    Nothing about compaction depends on this."""
    try:
        asyncio.create_task(asyncio.to_thread(_write_working_state_now, thread_id, t))
    except Exception as e:  # noqa: BLE001
        log.warning("compact: could not schedule the working state for %s: %s", thread_id, e)


def _fmt_for_summary(messages: list[dict]) -> str:
    # In the order things happened, for the same reason `_build_prompt` does it:
    # a message sent mid-reply sits above that whole reply in the log. Here it
    # matters more than anywhere, because this text BECOMES the history once the
    # fold lands, so a wrong order is baked in rather than re-derived.
    out = []
    for m in chat_context.interleave_history(messages):
        who = origin.speaker_label(m.get("role", "user"), m.get("meta"))
        out.append(f"{who}: {m.get('content', '')}")
    return "\n\n".join(out)


def _summary_prompt(prev_summary: str, to_fold: list[dict]) -> str:
    """The exact text `_summarize_sync` sends. Split out so the ledger estimate
    can measure the SAME prompt `call_cloud` actually saw, without duplicating
    it out of sync or re-plumbing `_summarize_sync`'s return type (tests
    monkeypatch that function directly)."""
    headings = "\n".join(f"## {h}" for h in knowledge.WORKING_STATE_SECTIONS)
    return (
        "You are writing this scope's WORKING STATE from a chat transcript: the "
        "handoff a DIFFERENT session reads later to continue this work without "
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
        "carried into the prompt of every other chat in this scope, so its "
        "length is a cost paid on every one of their turns.\n\n"
        "You are given the working state so far, if there is one, and the "
        "conversation to fold into it. Return the UPDATED working state: merge it "
        "in, move items from Not done to Done as they finish, drop what has stopped "
        "being true, and keep everything from the earlier state that is still "
        "true.\n\n"
        + (f"=== WORKING STATE SO FAR ===\n{prev_summary}\n\n" if prev_summary else "")
        + f"=== THE CONVERSATION ===\n{_fmt_for_summary(to_fold)}\n\n"
        "=== OUTPUT ===\nThe updated working state only. Start with the first "
        "heading. No preamble, no closing remark."
    )


def _summarize_sync(prev_summary: str, to_fold: list[dict]) -> str:
    """One summarization call. Text in, text out, no tools.

    `tools_disabled` matters: without it this runs in the CLI's default agentic
    mode, which spins up every MCP server just to write a paragraph and takes
    minutes, which is what made /compact look hung."""
    prompt = _summary_prompt(prev_summary, to_fold)
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU,
                               tools_disabled=True, timeout_s=180)
    return (res.get("text") or "").strip()


def _write_working_state_now(thread_id: str, t: dict) -> None:
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
    `_spawn_working_state`). The operator pressed compact to reclaim context; a
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
        # MERGE, don't overwrite. The note is one per scope and every chat in
        # the scope writes it, so starting from scratch each time would throw
        # away what another chat recorded. The prompt already knows how to
        # merge: move items from Not done to Done, drop what stopped being
        # true, keep what still is.
        prev, _ = knowledge.read_working_state(scope)
        summary = _summarize_sync(prev, msgs)
        if not summary:
            log.warning("compact: the working-state summary came back empty for %s", thread_id)
            return
        knowledge.write_working_state(
            scope, summary,
            # `derived:<model>`, the origin vocabulary for "a model wrote
            # this", so the Memory page and anything else reading provenance
            # can tell it from a note the operator or an agent wrote. The
            # model named is the one that actually produced the text.
            author=origin.derived(llm_router.TIER_HAIKU),
            # The originating chat, which `put_note` already records on every
            # note, so the Memory page can say where a working state came from.
            chat_id=thread_id,
        )
        log.info("compact: working state for scope %s written from %s (%d chars)",
                 scope, thread_id, len(summary))
    except Exception as e:
        log.warning("compact: working-state note write failed for %s: %s", thread_id, e)


# Below this, a chat has not had a session worth handing to the next one, and
# summarizing it would spend a model call to say almost nothing.
_WORKING_STATE_MIN_MESSAGES = 4




@router.post("/threads/{thread_id}/clear", response_model=cm.ClearResult, response_model_exclude_unset=True)
async def clear_thread_context(thread_id: str):
    """Drop this thread's context, keeping the transcript — the other half of
    the /clear vs /compact split (see `slashCommands.ts`).

    THIS IS THE ONLY THING THAT CUTS A THREAD'S CONTEXT. `/compact` reclaims the
    live CLI session's window and leaves the transcript alone; this draws the
    line the prompt is assembled from. A handful of field writes, no model call.

    THE WRITES WAIT FOR A QUIET THREAD, same as compact's landing. A cut taken
    while a turn was appending counted the messages as of its own read, so a
    message that landed between the read and the boundary fell ABOVE the cut
    and silently left the next prompt. One operation at a time, landed between
    runs; a message sent meanwhile queues. See services/thread_ops."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    if not thread_ops.begin(thread_id, "clear"):
        # Refused, not deferred: see compact_thread.
        return {"cleared": False, "cleared_through": 0,
                "session_reset": False, "busy": True}
    handed_off = False
    try:
        rid = chat_store.active_turn_id(thread_id)
        if rid and build_streaming.turn_is_live(rid):
            thread_ops.land_in_background(thread_id, _land_clear_when_quiet(thread_id))
            handed_off = True
            return {"cleared": True, "cleared_through": 0,
                    "session_reset": False, "deferred": True}
        if thread_ops.cancelled(thread_id):
            return {"cleared": False, "cleared_through": 0,
                    "session_reset": False, "cancelled": True}
        thread, cleared, session_reset = _land_clear(thread_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
        return {
            "cleared": cleared,
            "cleared_through": int(thread.get("cleared_through", 0) or 0),
            "session_reset": session_reset,
        }
    finally:
        if not handed_off:
            thread_ops.end(thread_id)


def _land_clear(thread_id: str) -> tuple[dict | None, bool, bool]:
    """The clear's writes, against the thread as it is NOW: re-read at landing
    time, never from a snapshot taken before the wait. Refuses a cut the operator
    called off, on both paths, for the same reason `_land_fold` does."""
    if thread_ops.cancelled(thread_id):
        log.info("clear: cut called off before landing on %s", thread_id)
        return None, False, False
    t = chat_store.get_thread(thread_id)
    if t is None:
        return None, False, False
    provider = chat_context.get_provider(t.get("context_key"))
    session_reset = _reset_live_session(t, provider)
    result = chat_store.clear_thread_context(thread_id)
    if result is None:
        return None, False, session_reset
    thread, cleared = result
    return thread, cleared, session_reset


async def _land_clear_when_quiet(thread_id: str) -> None:
    try:
        await thread_ops.wait_quiet(thread_id)
        _land_clear(thread_id)
        log.info("clear: deferred cut landed on %s", thread_id)
    except Exception as e:
        log.warning("clear: deferred cut failed for %s: %s", thread_id, e)
    finally:
        thread_ops.end(thread_id)


# The context-window table used to live here. It moved to
# `services/context_window` when the chat LIST grew a per-thread reading (the
# dashboard draws a ring per row and must not make one HTTP call per row): two
# surfaces answering "how full is this chat" from two tables is a silent
# disagreement, so there is one table and both read it.
def _window_for(model: str | None, measured: int = 0) -> int:
    return context_window.window_for(model, measured)


@router.get("/threads/{thread_id}/context", response_model=crm.ThreadContext, response_model_exclude_unset=True)
async def thread_context(thread_id: str):
    """Real context-window usage for the composer meter.

    Preferred source: the actual context size the model reported on the latest
    turn (input + cached-input tokens), persisted as `context_tokens`. This is the
    truth for BOTH build/session chats and data chats, and it DROPS after an
    in-session or threshold /compact instead of climbing forever with the stored
    transcript (the bug that pinned long, repeatedly compacted chats at 100%).

    Fallback (a thread that has not completed a streamed turn yet): estimate from
    the same prompt a turn WOULD send. For data providers that compact, that is
    only the messages after the last /clear, never more, so the
    estimate already respects compaction too. ~4 chars/token (no local Claude
    tokenizer)."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    model = _resolve_model(None, t, provider)
    stored = t.get("context_tokens")
    measured = int(stored) if isinstance(stored, (int, float)) and stored > 0 else 0
    # Let the measurement correct a stale table (see `_window_for`).
    window = _window_for(model, measured)

    if measured > 0:
        used = measured
        return {
            "used_tokens": used,
            "window_tokens": window,
            "pct": min(100, round(used / window * 100)),
            "model": model,
            "source": "measured",
        }

    tools_active, tool_summary = chat_context.capability_for(
        provider, enable_chat_tools=ENABLE_CHAT_TOOLS
    )
    is_build_mode = chat_context.is_build_provider(provider)
    preamble = chat_context.role_preamble(
        tools_active=tools_active,
        tool_summary=tool_summary,
        cwd=(str(provider.dir) if is_build_mode else None),
        is_build=is_build_mode,
    )
    # Same stop rule on the first turn as on every resumed one, so behaviour
    # does not differ between a fresh chat and a continued one.
    preamble = chat_context.turn_contract(CHAT_TIMEOUT_S) + "\n\n" + preamble
    # Mirror what a real turn sends: everything after the last /clear.
    history = t.get("messages", [])
    ct = int(t.get("cleared_through", 0) or 0)
    if 0 <= ct <= len(history):
        history = history[ct:]
    # The SAME inputs a turn would use, summary included: this estimate exists to
    # answer "how full is this chat", and it is only worth anything if it is
    # measuring the prompt that actually gets sent. It measured a trimmed history
    # while turns sent the whole thing, which is how a chat could read 12% and
    # still be re-sending every message ever written in it.
    prompt = _build_prompt(provider, history, "", preamble=preamble,
                           summary=(t.get("summary") or ""), tools_active=tools_active,
                           thread_id=str(t.get("id") or ""))
    used = max(1, round(len(prompt) / 4))
    return {
        "used_tokens": used,
        "window_tokens": window,
        "pct": min(100, round(used / window * 100)),
        "model": model,
        "source": "estimated",
    }


# ---- Which tree this chat works in ------------------------------------------
# Section 10 and section 14 of
# `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`: the ref
# is "inherited, shown, changeable", and the chat is where it is shown and
# changed. `services/chat_worktree.py` holds the rules; these two routes are the
# door.


def _tree_read(thread_id: str):
    """The chat, its provider and where its next turn would run. Writes nothing."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    fallback = (str(provider.dir) if chat_context.is_build_provider(provider)
                else str(paths.chat_cwd()))
    return t, provider, chat_worktree.plan(t, provider, fallback_cwd=fallback,
                                           holder_id=thread_id)


def _tree_payload(tree) -> dict:
    project = tree.project_slug
    display_name: str | None = None
    if project and tree.branch_slug:
        rec = worktrees.worktree_record(project, tree.branch_slug)
        display_name = (rec or {}).get("display_name")
    return {
        **tree.as_dict(),
        "display_name": display_name,
        "default_ref": worktrees.default_ref(project) if project else "",
        "branches": chat_worktree.branch_options(project),
        "can_pin": bool(project),
    }


@router.get("/threads/{thread_id}/tree", response_model=crm.ChatWorktree,
            response_model_exclude_unset=True)
async def thread_tree(thread_id: str):
    """Which tree this chat works in, who holds it, and where else it could go.

    A READ, not a turn: opening the ref menu claims nothing and writes no
    presence. The lease it reports is whatever the lease table says right now,
    which for an idle chat is usually nothing at all -- a chat holds its tree
    for the length of a turn, not for the length of the conversation
    (section 7: sessions are disposable, trees are not).
    """
    _t, _provider, tree = _tree_read(thread_id)
    return _tree_payload(tree)


class ThreadTreeIn(BaseModel):
    """Move this chat to another tree.

    `action` is section 10.3's closed set and nothing else is accepted:
    `join` an existing branch, cut a `new_branch`, or `release` the pin and go
    back to the shared tree. Joining requires a branch that was actually
    offered -- `services/session_defaults.choose` enforces that, and the point
    of routing through it is that a join is never something that just happens.
    """
    action: str = "join"
    branch: str | None = None


@router.put("/threads/{thread_id}/tree", response_model=crm.ChatWorktree,
            response_model_exclude_unset=True)
async def set_thread_tree(thread_id: str, body: ThreadTreeIn):
    """Pin this chat to a branch, cut a new one, or go back to the shared tree.

    APPLIES FROM THE NEXT TURN, exactly as the model, effort and MCP selection
    do: a turn already in flight had its cwd fixed when its process started, and
    moving a running turn's directory underneath it is not a thing that can be
    done. The pill re-reads after the write, so what it says is what the next
    turn will do.

    WHERE THE PIN IS STORED DEPENDS ON WHAT IS BEING PINNED, and that is
    section 7 rather than an inconsistency. A maker agent is one worker with one
    tree, so its pin is on the agent and every chat addressed to it moves
    together; a project chat is one of many against a shared tree, so its pin is
    on the conversation.
    """
    t, provider, tree = _tree_read(thread_id)
    project = tree.project_slug
    if not project:
        raise HTTPException(400, "this chat has no project tree to branch from")

    if body.action == "release":
        branch_slug = None
    elif body.action in ("join", "new_branch"):
        offer = chat_worktree.pin_offer(project, current_ref=worktrees.default_ref(project))
        try:
            chosen = chat_worktree.apply_choice(project, offer, body.action,
                                            branch=body.branch,
                                            allocated_by=f"chat:{thread_id}")
        except ValueError as e:
            raise HTTPException(400, str(e))
        branch_slug = chosen["branch_slug"]
    else:
        raise HTTPException(400, "action must be join, new_branch or release")

    key = str(getattr(provider, "key", "") or "")
    if key.startswith("agent:"):
        try:
            build_agents.set_branch(provider.slug, branch_slug)
        except KeyError:
            raise HTTPException(404, f"build agent not found: {provider.slug}")
        except ValueError as e:
            raise HTTPException(400, str(e))
    elif chat_store.set_thread_branch(thread_id, branch_slug) is None:
        raise HTTPException(404, "thread not found")

    _t2, _p2, moved = _tree_read(thread_id)
    return _tree_payload(moved)


def _side_query_cwd(provider) -> str:
    """The directory a side query should run the CLI in.

    A build chat's session belongs to its project directory; everything else
    lives at the workshop root. Shared by the two side queries below so a
    `/context` reading and an MCP listing can never describe different places.
    """
    return (str(provider.dir) if chat_context.is_build_provider(provider)
            else str(context_report.WORKSHOP_ROOT))


@router.get("/threads/{thread_id}/context/detail", response_model=crm.ContextDetail, response_model_exclude_unset=True)
async def thread_context_detail(thread_id: str, refresh: bool = False):
    """What is actually filling this chat's window, broken down.

    Powers the context modal. A SIDE QUERY, not a turn: it claims no turn, writes
    no turn event, and appends no message, because opening a panel is not
    something that belongs in the transcript.

    Free to serve. The CLI computes `/context` locally — measured 2026-08-10 at
    `num_turns: 0`, `duration_api_ms: 0`, `total_cost_usd: 0` — so this can be
    refreshed as often as the operator likes.

    Cached per session for `context_report.CONTEXT_TTL_S`, against a fingerprint
    of the thread's own counters so any turn invalidates it at once. `?refresh=1`
    is the panel's explicit Refresh and bypasses the cache. `checked_at` is when
    the CLI actually ran, which is what the footer's age counts from.

    `available: false` is a normal answer, not an error: a thread that has not
    run a turn yet has no session to inspect, and some CLI builds do not offer
    the command here. The panel says so and still shows the meter's own total.
    """
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    model = _resolve_model(None, t, provider)
    window = _window_for(model, int(t.get("context_tokens") or 0))
    session = t.get("claude_session")
    base = {
        "model": model,
        "window_tokens": window,
        # OUR measurement stays the headline number, exactly as the meter shows
        # it. The breakdown explains that number; it must never quietly replace
        # it with the CLI's, which is computed against a different window.
        "used_tokens": int(t.get("context_tokens") or 0),
    }
    if not session:
        return {**base, "available": False, "checked_at": None, "cached": False,
                "reason": "This chat has not run a turn yet, so it has no session to inspect."}
    # What makes a cached reading stale: the conversation moved. Both counters
    # change on every turn, so a report never outlives the state it describes.
    fingerprint = f"{t.get('context_tokens') or 0}:{len(t.get('messages') or [])}:{model}"
    report, checked_at, cached = await context_report.cached_context_report(
        session, _side_query_cwd(provider), model,
        fingerprint=fingerprint, force=bool(refresh),
    )
    if report is None:
        return {**base, "available": False, "checked_at": checked_at, "cached": False,
                "reason": "The breakdown could not be read for this chat."}
    return {**base, "available": True, "checked_at": checked_at, "cached": cached,
            **{k: v for k, v in report.items() if k not in ("model",)}}


@router.get("/threads/{thread_id}/mcp/health", response_model=crm.McpHealth, response_model_exclude_unset=True)
async def thread_mcp_health(thread_id: str, refresh: bool = False):
    """Which MCP servers are configured for this chat's directory, and whether
    they connect.

    The same SIDE QUERY contract as `/context/detail` and for the same reasons:
    it claims no turn, emits no turn event, and appends no message. It runs
    `claude mcp list`, which reads configuration and pings each server; it never
    resumes a session and never reaches a model.

    Why it exists: on 2026-08-10 three servers on this host were failing to
    connect and two were unauthenticated, and no screen in the app said so. A
    server that cannot connect is a cost with no return, paid silently.

    Cached per directory for `mcp_health.MCP_HEALTH_TTL_S`; `?refresh=1`
    bypasses it. `available: false` with a `reason` is what an unrunnable CLI
    looks like — there is no fallback list, because a made-up server list would
    report health for things that may not exist.

    Also carries this chat's own MCP selection (`enabled`, null for "all"), so
    the panel can draw the toggles from the same reading that drew the list.
    Each row gains `spec_available`: whether a launch spec for that server can
    be found, and therefore whether it can be part of a subset at all. See
    services/mcp_registry.
    """
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    cwd = _side_query_cwd(provider)
    result, checked_at, cached = await mcp_health.cached_mcp_health(cwd, force=bool(refresh))
    if result.get("available"):
        result = {**result, "servers": mcp_registry.annotate(
            result.get("servers") or [], mcp_registry.resolve_specs(cwd))}
    return {**result, "checked_at": checked_at, "cached": cached,
            "enabled": chat_store.get_thread_mcp_servers(thread_id)}


class McpSelectionIn(BaseModel):
    """`null` = every server (the default, and what a chat that never chose
    has). A list = exactly those. `[]` = none, which is a real choice."""
    servers: list[str] | None = None


@router.put("/threads/{thread_id}/mcp/servers", response_model=crm.McpSelection, response_model_exclude_unset=True)
async def set_thread_mcp_servers(thread_id: str, body: McpSelectionIn):
    """Choose which MCP servers this chat's turns may reach.

    A CONFIG WRITE, which is why the UI puts it behind Edit mode and an explicit
    Save rather than committing on each toggle (frontend/CLAUDE.md rule 7): it
    changes what the next turn can do.

    The selection is stored against the thread exactly as the model, effort and
    permission mode are, and is read at spawn time — so it applies from the next
    turn, not to a turn already in flight, whose argv was fixed when its process
    started.

    Sending every server the machine currently has is stored as "all" rather
    than as that list; see chat_store.normalize_mcp_selection for why a frozen
    full list is a different, worse thing later.
    """
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    cwd = _side_query_cwd(provider)
    known: list[str] | None = None
    if body.servers is not None:
        # The full set as the CLI sees it right now, so "everything ticked"
        # collapses to the default. Read from cache: this is a write path and
        # must not wait on a 90s probe, and a stale-by-minutes membership list
        # only ever costs the collapse, never correctness.
        health, _at, _cached = await mcp_health.cached_mcp_health(cwd)
        if health.get("available"):
            known = [s["name"] for s in health.get("servers") or []]
    try:
        updated = chat_store.set_thread_mcp_servers(thread_id, body.servers, known)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if updated is None:
        raise HTTPException(404, "thread not found")
    return {"enabled": chat_store.get_thread_mcp_servers(thread_id)}


def _agent_role_for(provider) -> str | None:
    """The ROLE (maker/reviewer/triager/keeper/broker) behind a chat, or None.

    There used to be a second field called `role` on the provider itself,
    carrying the retired assistant/builder axis; it was deleted on 2026-08-17,
    so `role` now means one thing here. Only an agent-backed
    chat has a role in the closed-enum sense, so everything else reads the
    workshop + place layers and no role layer, which is correct: a general chat
    is not doing a kind of work.
    """
    slug = getattr(provider, "slug", None)
    if not slug:
        return None
    try:
        from services import agents_registry
        rec = agents_registry.get(slug)
    except Exception:
        return None
    if not rec:
        return None
    role = str(rec.get("role") or "").strip().lower()
    return role or None


async def _assemble_full_prompt(t: dict, provider, content: str) -> tuple[str, bool]:
    """Assemble the full turn prompt (role preamble + persona + capability facts +
    compacted history + this message) for either a data
    or a build provider. Shared by POST /messages (blocking) and POST /turns
    (streaming) so the two never fork. Returns (prompt, is_build_mode). Build
    providers on a resumed session use a runtime briefing instead of this full
    assembly (the caller handles that); this is the first-turn / stateless
    assembly.

    Persists ONE bookkeeping mark and nothing else: the digest of the
    instruction contract this prompt carries, so `_resumed_prompt` can tell
    whether the session's protocols have since changed. It used to persist
    nothing at all, which is exactly why a contract change could never reach a
    session already running.

    Capability facts come from the SAME branch that decides the turn config
    (capability_for mirrors the build/narrow/tools-off ladder), so the role
    preamble can never claim tools the turn does not actually have."""
    tools_active, tool_summary = chat_context.capability_for(
        provider, enable_chat_tools=ENABLE_CHAT_TOOLS
    )
    is_build_mode = chat_context.is_build_provider(provider)
    preamble = chat_context.role_preamble(
        tools_active=tools_active,
        tool_summary=tool_summary,
        cwd=(str(provider.dir) if is_build_mode else None),
        is_build=is_build_mode,
    )
    # THE CUT IS APPLIED HERE, and until 2026-08-12 it was applied NOWHERE.
    #
    # `get_thread` hands over every message the thread has ever held plus a
    # `cleared_through` COUNT; trimming is the caller's job, and this caller —
    # the one that builds what the model actually reads — did not do it. So
    # /clear recorded a boundary, dropped the CLI session, drew a divider in the
    # transcript, and then the next turn re-sent the entire history anyway. The
    # context meter DID subtract the cut (see `_context_estimate`), so the meter
    # went down while nothing else did.
    #
    # That is the whole of "compact does not compact": both commands were
    # bookkeeping. Measured on a six-exchange thread: /clear, then a fresh turn,
    # and every message from before the clear was still in the prompt.
    history = t.get("messages", [])
    ct = int(t.get("cleared_through", 0) or 0)
    if 0 < ct <= len(history):
        history = history[ct:]
    # ...and what compaction left in their place. Empty after a /clear, which is
    # exactly the difference between the two commands: /clear drops the earlier
    # conversation, /compact replaces it with this.
    summary = (t.get("summary") or "").strip()
    # AUTO-RECALL IS REMOVED (operator, 2026-08-12). It ran `knowledge.search` on
    # every message and pasted the top 3 notes in here. That search scored a note
    # as `sum(hay.count(t))`: raw term frequency, no stopwords, no IDF, no length
    # normalisation, so longer notes won by containing more of every common word.
    # Probed against the live scope, a query of pure stopwords returned the same
    # three notes as a real question, and those three were the three largest
    # files. It was a length sort, injected unasked, on every turn.
    #
    # Replaced by `mcp__workshop__search` (services/semantic_index), which the
    # model CALLS. It searches notes, docs and past chats by meaning, returns the
    # passage itself with its provenance, and returns nothing when nothing is
    # relevant. Pull, not push. Do not reintroduce an injection here.
    # The transcript goes as it is. `chat_store` already cut it at the last
    # /clear before handing it over, and nothing else may remove a message.
    prompt = _build_prompt(provider, history, content, preamble=preamble,
                           summary=summary, tools_active=tools_active,
                           thread_id=str(t.get("id") or ""))
    # Stated after the persona so it can override the ticket protocols the
    # persona carries, in the direction that removes capability.
    prompt = prompt + "\n\n" + chat_context.chat_mode_block(t.get("mode"))
    # THE STOP RULE GOES ON THIS PATH TOO, and until 2026-08-12 it went only on
    # the resumed one. `_resumed_prompt` opens with it every turn; this assembly
    # — a fresh chat, and every FIRST TURN AFTER A /compact or /clear, which
    # drop the CLI session — sent the persona, the transcript and the mode block
    # without it. So the rule that tells an agent how its turn ends was present
    # on every turn except the one right after a compaction.
    #
    # Measured on thread 65341b40 (2026-08-12): /compact at 23:05:58, and the
    # 322,142-character prompt at 23:10:39 carries no contract. That turn closed
    # with "Building it now." and `stop_reason: end_turn`. The two resumed
    # prompts either side of it carry it and did not stop.
    #
    # `_context_estimate` has prepended it to this same preamble since a7faffa,
    # with a comment saying behaviour must not differ between a fresh chat and a
    # continued one. The METER measured a prompt containing the rule while the
    # turn ran without it. Keep the two paths together.
    #
    # It goes LAST here, not first as on the resumed path: this prompt ends with
    # the operator's message, and the rule has to sit near it rather than
    # 322k characters back behind the whole transcript.
    prompt = prompt + "\n\n" + chat_context.turn_contract(CHAT_TIMEOUT_S)
    # RECORD WHAT THIS SESSION HAS NOW BEEN TOLD. This prompt carries the role
    # preamble and the persona in full, so the session holds the current
    # contract; without this mark the very next (resumed) turn would compare
    # against nothing and re-state a contract that arrived one turn ago.
    # `_resumed_prompt` re-sends only when this digest moves.
    try:
        tid = t.get("id")
        if tid:
            chat_store.set_turn_marks(tid, contract_hash=chat_context.contract_digest(
                chat_context.instruction_contract(
                    provider, enable_chat_tools=ENABLE_CHAT_TOOLS)))
    except Exception as e:  # noqa: BLE001 - bookkeeping must not fail a turn
        log.warning("could not record the contract digest: %s", e)
    return prompt, is_build_mode


def _is_raw_command(content: str) -> bool:
    """Is this turn a slash command for the CLI to execute, or just a message
    that happens to begin with "/"?

    Only a single line qualifies. The client drains everything typed behind a
    running turn into ONE next turn, joined by blank lines, so a queued
    "/compact" followed by any other message arrives here as a multi-line blob.
    Sent raw, the CLI reads the whole blob as one command name and the turn
    fails — and the operator's other message is then neither executed as a
    command nor read as text. It is simply lost."""
    stripped = content.strip()
    return stripped.startswith("/") and "\n" not in stripped


def _thread_session(thread_id: str, cwd: str) -> tuple[str | None, bool]:
    """(session_id, is_new) for this thread's persistent claude session.

    Stale-session guard, same as the build-agent path: an id whose session file
    is gone (pruned, or written under a different cwd) would make `--resume` fail
    the entire turn, so drop it and mint a fresh one. Starting fresh is safe —
    the caller re-assembles the full prompt (the transcript after the cut) for a new
    session, so continuity is preserved even when the CLI's copy is gone."""
    got = chat_store.ensure_thread_session(thread_id)
    if got is None:
        return None, True
    sid, is_new = got
    if sid and not is_new and not build_streaming.session_alive(sid, cwd):
        log.info("thread %s: claude session %s is gone; starting fresh", thread_id, sid)
        chat_store.clear_thread_session(thread_id)
        got = chat_store.ensure_thread_session(thread_id)
        if got is None:
            return None, True
        sid, is_new = got
    return sid, is_new


async def _resumed_prompt(t: dict, provider, content: str) -> str:
    """The per-turn message for a chat whose claude session is being RESUMED.

    The session already holds the persona, the history, and everything the first
    turn assembled, so re-sending that would both waste the window and fight the
    session's own memory. What must refresh every turn is the volatile part: the
    runtime briefing (a builder's cwd/stack/ports) or the provider's live app
    state, plus any notes retrieved for this specific message."""
    parts: list[str] = []
    # The stop rule goes FIRST, every turn. An agent cannot observe the
    # condition that ends its own turn; being told once at session start is not
    # enough, because that instruction is thousands of tokens back by the time
    # it matters. See chat_context.turn_contract.
    parts.append(chat_context.turn_contract(CHAT_TIMEOUT_S))
    parts.append(chat_context.chat_mode_block(t.get("mode")))
    marks = chat_store.get_turn_marks(t)
    new_marks: dict[str, object] = {}
    # THE CONTRACTS THIS SESSION WAS GIVEN AT SPAWN CAN CHANGE UNDER IT, and
    # until 2026-08-16 nothing could tell it. The ui-card format and the
    # task-proposal field list live in the role preamble and the persona, which
    # are sent on the first turn and never again, so a chat open across a deploy
    # kept emitting the previous contract for the rest of its life. See
    # chat_context.instruction_contract for the measurement.
    #
    # A thread with no recorded digest gets the block once. That is deliberate:
    # it is either a session from before this existed (stale by definition, and
    # this is what heals it) or one whose fresh path did not record, and re-
    # stating a contract the session already holds is harmless where staying
    # silent about a changed one is not.
    try:
        contract = chat_context.instruction_contract(
            provider, enable_chat_tools=ENABLE_CHAT_TOOLS)
        c_digest = chat_context.contract_digest(contract)
        if c_digest != marks.get("contract_hash"):
            parts.append(chat_context.contract_update_block(contract))
            new_marks["contract_hash"] = c_digest
    except Exception as e:  # noqa: BLE001 - a protocol refresh is not worth a dead turn
        log.warning("contract freshness check skipped for %s: %s", provider.key, e)
    briefing = chat_context.runtime_briefing(provider)
    if briefing:
        parts.append(briefing)
    else:
        try:
            live = provider.live_context()
        except Exception as e:
            log.warning("chat live_context failed for %s: %s", provider.key, e)
            live = ""
        if live:
            # Two things this block has to get right, neither of which it did.
            #
            # 1. SUPERSESSION. Every turn appends a fresh snapshot and nothing
            #    removes the last one, so a twenty-turn conversation holds twenty
            #    of them and nineteen are wrong. Balances and location genuinely
            #    move; asked "where am I", the model can read a forty-minute-old
            #    block as readily as this one. Appending is otherwise the right
            #    shape (Anthropic's cache invalidates tools -> system -> messages,
            #    so dynamic state belongs in the appended turn and NEVER in the
            #    system prompt) — it just has to say which copy counts.
            #
            # 2. REPETITION. Re-sending a byte-identical snapshot buys nothing
            #    and costs its tokens on every turn. When nothing has changed,
            #    say so in one line and let the earlier block stand.
            digest = hashlib.sha256(live.encode("utf-8")).hexdigest()[:16]
            if digest == marks.get("live_hash"):
                parts.append(
                    "=== LIVE CONTEXT ===\nUnchanged since the last turn; the most "
                    "recent LIVE CONTEXT block above is still current."
                )
            else:
                parts.append(
                    "=== LIVE CONTEXT (current app state) ===\n"
                    "This REPLACES every earlier LIVE CONTEXT block in this "
                    "conversation. Older copies are stale, so ignore them.\n\n"
                    + origin.escape_markers(live)
                )
                new_marks["live_hash"] = digest

    # What changed in this scope while this chat was not looking. The counter is
    # bumped by whoever wrote (services/scope_version), so learning "nothing
    # happened" costs one integer compare and produces no text at all — which is
    # the common case. Only a real change spends tokens, and even then it spends
    # them on a pointer, not on the content: the chat reads the store if and when
    # it cares.
    scope_key = task_scope.key_for_root(task_scope.root_for_context(provider.key))
    if scope_key:
        try:
            if scope_version.changed_since(scope_key, marks.get("seen_scope_version")):
                d = scope_version.describe(scope_key)
                parts.append(
                    f"=== SHARED WORK UPDATED ({scope_key}) ===\n"
                    f"Another chat or agent changed this scope's {d['kind']}s "
                    f"since your last turn ({d['detail']}, {d['at']}). Read them "
                    f"before acting on anything that depends on their state."
                )
                new_marks["seen_scope_version"] = d["version"]
        except Exception as e:
            log.debug("scope freshness check skipped for %s: %s", scope_key, e)
    # The per-message "=== RELEVANT NOTES ===" injection was here. Removed with
    # its twin on the fresh-session path above; see the note there for why the
    # ranking made it noise, and for what replaces it.
    parts.append("=== OPERATOR MESSAGE ===\n" + origin.escape_markers(content))
    # Immediately after the message, because it is a fact ABOUT that message:
    # whether it was typed while the previous turn was still running, and whether
    # others were typed alongside it before any had been answered. Placed last so
    # nothing separates it from the text it qualifies.
    # Read the thread FRESH, not from `t`. The route fetches `t` before it
    # records this turn's user message(s), deliberately — the fresh-session path
    # uses that pre-append snapshot as history so the current message is not
    # duplicated into it. But it means `t` cannot see the queued messages just
    # written by _record_user_turn, and building the notice from it would make
    # this silently never fire for exactly the case it was written for.
    tid = t.get("id") or ""
    fresh = chat_store.get_thread(tid) or t
    # Which messages waited is a LOOKUP, not a guess about position: a message
    # handed to run R carries `during_turn = R`, and the turn that just ended is
    # the one whose arrivals this turn has not answered. Reading the tail after
    # the last assistant message (what this used to do) stopped working the
    # moment the reply started being appended when it FINISHED, because a
    # delivered message then sits above the reply rather than below it.
    arrivals = chat_context.arrival_notice(
        fresh.get("messages") or [], turn_id=chat_store.previous_turn_id(tid))
    if arrivals:
        parts.append(arrivals)
    # Record what this thread has now been told, AFTER the prompt is built: a
    # mark written before the turn would suppress the next turn's block even if
    # this one never reached the model.
    if new_marks:
        chat_store.set_turn_marks(t["id"], **new_marks)
    return "\n\n".join(parts)


@router.post("/threads/{thread_id}/messages", response_model=cm.MessageAccepted, response_model_exclude_unset=True)
async def send_message(thread_id: str, body: PostMessage):
    """Send a message and BLOCK until the reply is committed. Voice and scripts.

    A thin wrapper over the same turn pipeline the streaming path uses: start
    a turn (or join the one in flight), await its finalize, return the committed
    message. There is no second completion path any more.

    That mattered because the two had separate finalize pipelines. Provider
    actions, task-patch blocks, learning notes and note blocks were applied in
    BOTH, in code that had already drifted: an action block emitted on a streamed
    turn and the same block on a blocking turn were handled by different lines.
    The pipeline now exists once, in build_streaming._commit_turn.

    No deadline. A chat turn has no wall clock (see CHAT_TIMEOUT_S), so neither
    does waiting for one; the caller's own client timeout is the bound."""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "empty message")

    out = await _send(thread_id, content, body.model, client_id=body.client_id)
    turn = build_streaming.get_turn(out["turn_id"])
    if turn is None:
        # The turn finished and was reaped between starting it and looking it up.
        # Its message is committed; hand back the tail of the thread.
        t = chat_store.get_thread(thread_id) or {}
        msgs = [m for m in (t.get("messages") or []) if m.get("role") == "assistant"]
        return {"message": msgs[-1] if msgs else None, "notes": []}
    await turn.finished.wait()
    return {"message": turn.committed, "notes": turn.notes or []}


# ---- SSE streaming for Build chats -----------------------------------------
# Per turn, the operator gets a real-time event stream: thinking → tool calls
# → text chunks → done. Implemented by:
#   1) POST /threads/{id}/turns starts a streaming claude subprocess and
#      returns a turn_id immediately.
#   2) GET /turns/{turn_id}/stream is an SSE channel with `id: <offset>` per
#      event so EventSource auto-reconnect resumes via Last-Event-ID.
# EVERY chat uses this streaming path (general, app, project, and build), so the
# turn shows its work (tool calls, interim text) live instead of a blocking spinner.
# The provider class decides the tool surface + session: BuildAgent = persistent
# session + full tools, Project = full tools no session, Data/assistant = narrow
# tools (web + declared MCP) no cwd-build. POST /messages remains for non-UI
# callers (the voice worker, scripts).


class TurnPart(BaseModel):
    """One message from a queue that drained into this turn, with when it was
    actually typed."""
    text: str
    sent_at: str | None = None
    # The browser's own id for this send. Carried per part because the queue
    # holds separate messages and each becomes its own log event; without one id
    # each, a retry of the batch could only be reconciled by matching text.
    client_id: str | None = None
    # WHO SENT IT, when it was not the operator at a keyboard: a finished run, a
    # trigger, another thread. None means the operator, which is what every
    # message in the transcript already assumes.
    sender: str | None = None


class StartTurnIn(BaseModel):
    content: str
    # Optional per-turn model override (short tier name or full id); same
    # semantics as PostMessage.model.
    model: str | None = None
    # Optional per-turn effort level (low/medium/high/xhigh/max). Persisted onto
    # the thread the same way `model` is, so it sticks until changed. Omitted
    # entirely (None) leaves the stored preference alone — an older client that
    # never sends this must not silently reset it.
    effort: str | None = None
    # Optional per-turn permission mode (default/plan/acceptEdits/auto/
    # bypassPermissions). Persisted like `effort`; omitted leaves it alone.
    permission_mode: str | None = None
    # Present only when several queued messages became ONE turn. `content` is
    # still what the model reads (they are joined); this is how they were sent.
    # Without it the transcript stored one blob stamped at the instant the queue
    # drained, so a burst typed in one minute and thoughts spread over an
    # afternoon were recorded identically — and the client, which had rendered
    # them as separate bubbles all along, disagreed with the server after a
    # reload.
    parts: list[TurnPart] | None = None
    # The browser's id for this send, stamped before the request leaves and
    # reused verbatim on a retry. The log dedupes on it, so a resend after a
    # dropped response cannot show the message twice, and the client reconciles
    # its optimistic bubble against the committed event by id instead of by
    # matching the tail of the transcript against the text it just sent.
    client_id: str | None = None
    # Files already uploaded via POST /attachments. Ids only: the bytes are on
    # the server before Send is pressed, so a turn body stays small and a slow
    # upload is not paid for inside the send.
    attachments: list[AttachmentRefIn] | None = None
    # WHEN THE OPERATOR HIT ENTER, stamped by the browser because that is the
    # only party present at that moment. It matters when the message cannot run
    # immediately: a queued message keeps its own send time rather than being
    # dated at whatever later instant the queue drained, so the transcript can
    # still tell a burst typed in one minute from thoughts spread over an hour.
    # Absent (an older client) means "now", which is what it always meant.
    sent_at: str | None = None
    # WHICH COMPOSER THIS CAME OUT OF, so the server can clear it once the
    # message is accepted. The client cannot reliably do this itself: sending
    # the first message of a blank chat CHANGES the composer's key (the chat
    # becomes a thread), so a clear issued after the round trip lands on the new
    # key and the blank-chat slot keeps the text forever — which is why a
    # message you already sent turned up in the next new chat you started. See
    # services/draft_store.
    draft_key: str | None = None


def _first_part_ts(parts: list[TurnPart] | None) -> str | None:
    """When a batch was first typed. The batch's own oldest send time, not the
    instant it reached the server."""
    for p in parts or []:
        if p.sent_at:
            return p.sent_at
    return None


def _sends_behind(content: str, parts: list[TurnPart] | None,
                  client_id: str | None, attachments: list[dict] | None) -> list[dict]:
    """The operator send(s) this turn's prompt was assembled from.

    Mirrors what `_record_user_turn` writes to the log, in the shape the thread
    queue holds, and rides with the prompt into the spawn. It is what goes back
    on the queue if the CLI never reads that prompt: same client ids, same send
    times, so the message is re-sent without being written to the transcript
    twice. See build_streaming._requeue_unread."""
    if parts and any(p.sent_at or p.client_id for p in parts):
        out = []
        for i, p in enumerate(parts):
            entry = {
                "text": (p.text or "").strip() or content,
                "client_id": p.client_id or (client_id if i == 0 else None),
                "ts": p.sent_at,
                # Recorded on the first part, exactly as the log records them.
                "attachments": list(attachments or []) if i == 0 else [],
            }
            # WHO SENT IT rides along too, and ONLY when there is one, which is
            # the same rule the message meta follows: absence means the operator,
            # so an operator send's payload keeps exactly the shape it had.
            # Without this a message the CLI never read comes back through
            # `_requeue_unread` as an operator send, and the queue the operator
            # is looking at attributes another thread's message to them. The
            # transcript row keeps its own `meta.sender` either way, because the
            # log is idempotent on client id, which is precisely why losing it
            # here would be silent.
            if p.sender:
                entry["sender"] = p.sender
            out.append(entry)
        return out
    return [{"text": content, "client_id": client_id, "ts": None,
             "attachments": list(attachments or [])}]


def _record_user_turn(thread_id: str, content: str, parts: list[TurnPart] | None,
                      client_id: str | None = None,
                      attachments: list[dict] | None = None) -> None:
    """Persist what the operator actually sent for this turn.

    One message normally. When a queue drained into this turn, the separate
    messages it held, each at its own send time and marked `queued` so the next
    prompt can say they were written before any had been answered.

    Attachments are recorded on the message that carried them, so reopening the
    thread shows the same chips it showed when it was sent. They ride on the
    FIRST part of a drained queue rather than being copied onto each: they were
    attached once."""
    # ONE PART IS STILL A PART. A single message that WAITED — queued behind a
    # command turn, then sent by the server when it ended — is recorded at the
    # moment the operator hit enter, not at the moment it went out. Falling
    # through to the plain append here dated it at the drain, so a message
    # written at 17:44 and delivered at 17:46 read as having been written at
    # 17:46, and the transcript quietly disagreed with the queue the operator
    # had been watching.
    if parts and len(parts) == 1 and (parts[0].sent_at or parts[0].client_id):
        p0 = parts[0]
        meta = {"queued": True}
        # WHO SENT IT. Recorded only when it was not the operator, so absence
        # keeps its existing meaning and no message already in the log becomes
        # wrong. This is the fact `meta.autonomous` was guessing at on 2 of
        # 2,723 messages: the producer knew who it was and had nowhere to put it.
        if p0.sender:
            meta["sender"] = p0.sender
        if attachments:
            meta["attachments"] = attachments
        chat_store.append_message_during_turn(
            thread_id, "user", (p0.text or "").strip() or content, meta=meta,
            ts=p0.sent_at, client_id=p0.client_id or client_id)
        return
    if not parts or len(parts) < 2:
        meta: dict | None = {"attachments": attachments} if attachments else None
        # Not reachable from the drain today, because every queued entry carries
        # a client id and so takes the branch above. It is written this way
        # anyway: these are the two places a caller passing a sender without a
        # client id or a send time would lose it, and silently.
        sender = parts[0].sender if parts else None
        if sender:
            meta = dict(meta or {})
            meta["sender"] = sender
        chat_store.append_message(thread_id, "user", content, meta=meta,
                                  client_id=client_id)
        return
    # Each part at the time it was actually typed, not the instant the queue
    # drained. Without that the model cannot tell a burst typed in one minute
    # from thoughts spread over an afternoon, and `arrival_notice` reads these
    # times off to say so. Appended one by one: the log assigns each its own seq
    # and nothing can reorder them, which is what a whole-document write-back
    # used to be needed for.
    added = 0
    for p in parts:
        text = (p.text or "").strip()
        if not text:
            continue
        meta = {"queued": True}
        # Per part, like the send time beside it: a batch can in principle hold
        # entries from different senders, and copying one onto all of them would
        # be the same class of error as dating them all at the drain.
        if p.sender:
            meta["sender"] = p.sender
        if attachments and added == 0:
            meta["attachments"] = attachments
        chat_store.append_message_during_turn(
            thread_id, "user", text, meta=meta, ts=p.sent_at,
            client_id=p.client_id)
        added += 1
    if not added:
        meta = {"attachments": attachments} if attachments else None
        sender = next((p.sender for p in parts if p.sender), None)
        if sender:
            meta = dict(meta or {})
            meta["sender"] = sender
        chat_store.append_message(thread_id, "user", content, meta=meta,
                                  client_id=client_id)


def _agent_effort(rec: dict, slug: str, model: str, chat_effort: str | None) -> str | None:
    """The effort level a mentioned agent's turn runs at.

    The agent's declared level wins, then the chat's, then the model's own
    default (None) — and every candidate is checked against THE MODEL THAT WILL
    ACTUALLY RUN, which may be the agent's rather than the composer's. A level
    that model has no rung for is skipped rather than passed through: the CLI
    accepts every level for every model and then ignores the ones the API does
    not implement, so passing it would be a control that does nothing.

    Tolerant, like the model override beside it: nothing here fails a turn the
    operator started by addressing an agent."""
    for candidate in (rec.get("effort"), chat_effort):
        try:
            level = model_caps.normalize_effort(candidate, model)
        except ValueError:
            log.warning("agent %s: effort %r is not a level; ignoring", slug, candidate)
            continue
        if level:
            return level
    return None


async def _start_agent_mention_turn(*, thread_id: str, thread: dict, agent: dict,
                                    content: str, model: str, turn_id: str,
                                    effort: str | None = None,
                                    permission_mode: str | None = None,
                                    attachments: list[dict] | None = None,
                                    sends: list[dict] | None = None):
    """Run one turn AS the mentioned agent, inside the chat.

    The agent answers with its own definition, its own memory scope and its own
    workspace — it is the agent talking, not the assistant doing an impression of
    it.

    A MENTION IS NOT STATELESS. This docstring used to say it was, and argued
    that a resumable session per (agent, chat) pair would create a second,
    invisible conversation alongside this one; three hundred lines below,
    `chat_store.ensure_agent_session` has been keying exactly that session since
    it shipped, and its own docstring argues the opposite case. The code is
    right and the paragraph was stale. Per (agent, thread) is what gives an
    agent continuity in a conversation that is actually continuing while keeping
    two chats that merely mention the same agent apart; without it, every
    mention rebuilt the agent's definition, knowledge layers and conversation
    excerpt from nothing, which was the one path in the system that genuinely
    did reset every turn.

    What the agent needs from the chat beyond that session, it is given here.

    The reply is stamped with `meta.agent` so the transcript can attribute it,
    which is the whole point: an unattributed agent reply is indistinguishable
    from the assistant and the operator would have no idea who answered.
    """
    from services import agents_registry, mentions as _mentions

    slug = agent["slug"]
    rec = agents_registry.get(slug) or {}
    kscope = knowledge.scope_for_context(thread.get("context_key") or "general")

    # The agent's workspace. A project agent works in its project tree; anything
    # else answers from the chat cwd. This is the "own little bubble" bound —
    # narrow now, and the place a per-session worktree slots in later.
    cwd = str(task_scope.root_for_context(agent["scope"]) or paths.chat_cwd())

    # Strip the mention token from what the agent reads. It is addressing
    # information, not part of the question, and leaving it in reliably produces
    # replies that open by explaining who they are.
    _slug, start, end = _mentions.find_first(content)
    question = (content[:start] + content[end:]).strip() if start >= 0 else content

    # Recent conversation, so the agent can answer a question that refers to what
    # was just said instead of asking the operator to repeat themselves.
    recent = []
    for m in (thread.get("messages") or [])[-8:]:
        body = (m.get("content") or "").strip()
        if not body:
            continue
        who = "Operator" if m.get("role") == "user" else ((m.get("meta") or {}).get("agent") or "Assistant")
        recent.append(f"{who}: {origin.escape_markers(body[:1500])}")
    convo = "\n\n".join(recent[:-1]) if len(recent) > 1 else ""

    definition = (rec.get("definition") or "").strip()
    role = rec.get("role") or "agent"
    # Decided ONCE, here, and then both used and described. The self-preamble is
    # built from this same value, so what the agent is told about its tools is
    # the value that was enforced rather than a parallel sentence about it.
    #
    # Derived from the role's WRITE SET rather than from `role == "maker"`. That
    # test was the only thing `role` changed anywhere in the codebase, which made
    # reviewer, triager, keeper and broker identical to each other and to no role
    # at all — every one of them tools-off, whatever their contract said. A
    # keeper's tools now follow from the fact that it writes notes, a maker's
    # from the fact that it writes the tree, and a broker gets reads only.
    # An explicit `tools:` on the definition may narrow this, never widen it.
    agent_tools = agent_authority.narrow_tools(role, rec.get("tools"))
    tools_active = bool(agent_tools)
    # The servers those tools live in. This used to be the literal `[]`, which is
    # why an agent could be granted `mcp__workshop__search` and still have no
    # search: `tool_flags` builds its strict MCP config from THIS list, so a tool
    # whose server is absent is filtered straight back out of the allowlist.
    # Derived from the granted toolset so the grant and the spawn cannot disagree.
    agent_mcp = agent_authority.mcp_servers_for(agent_tools)
    # Reversibility gate. A mention is a manual invocation — the operator is
    # right there — so nothing is denied here; the list is non-empty only for a
    # turn nobody is watching. Passed explicitly rather than defaulted so the
    # unattended path cannot forget it.
    agent_denied = agent_authority.disallowed_for(
        unattended=agent_authority.is_unattended(rec.get("autonomy")))

    # The agent's OWN model, when its definition pins one. An @mentioned agent
    # used to run on whatever the composer happened to be set to, so an agent
    # declared to run on a cheap tier answered on the expensive one and vice
    # versa, silently, depending on a dropdown that is about the chat's
    # assistant. The definition is the agent; the composer is the chat.
    #
    # Tolerant on the way in: a definition naming a model this build cannot pick
    # falls back to the composer's rather than failing the turn, because the
    # operator addressing an agent should get an answer, not a 400 about a file
    # they may not have written. It is logged so the bad value is findable.
    if rec.get("model"):
        try:
            declared = chat_context.normalize_model(rec.get("model"))
            if declared:
                model = declared
        except ValueError as e:
            log.warning("agent %s declares a model this build cannot pick (%s); "
                        "using the chat's model instead", slug, e)

    # The agent's own TURN POSTURE, on the same terms as its model: what the
    # definition declares wins over the chat, because a reviewer should be able
    # to be careful inside a conversation that is otherwise on Auto. An agent
    # that declares nothing changes nothing.
    #
    # Effort is re-normalized against the model settled on JUST ABOVE, not the
    # chat's. The two can differ (an agent pinning Sonnet 4.6 inside a chat on
    # Opus 5), and `--effort xhigh` on a model without an xhigh rung is accepted
    # by the CLI and then quietly ignored — the exact failure model_caps exists
    # to prevent.
    effort = _agent_effort(rec, slug, model, effort)
    permission_mode = rec.get("permission_mode") or permission_mode

    # This agent's session IN THIS THREAD. Every mention used to spawn with no
    # session, so an agent rebuilt its preamble, definition and knowledge layers
    # from scratch each time it was addressed — the one path in the system that
    # genuinely did reset on every turn. On a resume none of that is re-sent:
    # the session already holds it, and only what has happened since is new.
    got = chat_store.ensure_agent_session(thread_id, slug)
    sid, is_new = got if got else (None, True)
    if sid and not is_new and not build_streaming.session_alive(sid, cwd):
        # Same stale-session guard the chat path uses: resuming a session the
        # CLI no longer has on disk fails the whole turn.
        log.info("agent %s in thread %s: session %s is gone; starting fresh", slug, thread_id, sid)
        chat_store.clear_agent_session(thread_id, slug)
        got = chat_store.ensure_agent_session(thread_id, slug)
        sid, is_new = got if got else (None, True)

    if not is_new:
        # Volatile only: what was said since, and what is being asked now.
        resumed: list[str] = []
        if convo:
            resumed.append("=== RECENT CONVERSATION (context; do not reply to it) ===\n" + convo)
        resumed.append("=== THE OPERATOR ASKS YOU ===\n" + origin.escape_markers(question or content))
        turn = await build_streaming.start_turn(
            thread_id=thread_id, agent_slug=slug, prompt="\n\n".join(resumed), model=model,
            cwd=cwd, session_id=sid, resume=True, timeout_s=CHAT_TIMEOUT_S,
            allowed_tools=agent_tools, disallowed_tools=agent_denied,
            mcp_servers=agent_mcp, tools_disabled=not tools_active,
            speaker=slug, turn_id=turn_id, effort=effort,
            permission_mode=permission_mode, origin=sends,
        )
        _ = kscope  # the CHAT's scope; the agent read its own layers on turn one
        return turn

    parts = [
        chat_context.agent_self_preamble(
            name=agent["name"], slug=slug, role=role, scope=agent["scope"], cwd=cwd,
            # The list itself, so the sentence the agent reads and the flag the
            # CLI is given are literally the same value.
            tools=agent_tools,
            triggers=rec.get("triggers"),
            mentionable=bool(rec.get("mentionable")),
            delegatable=bool(rec.get("delegatable")),
        ),
        f"You are answering INSIDE a chat, because the operator addressed you "
        f"directly with @{slug}. Answer as yourself, briefly and directly. Do not "
        "greet, do not introduce yourself, and do not restate the question.",
    ]
    if definition:
        parts.append("=== YOUR DEFINITION ===\n" + origin.escape_markers(definition))

    # An agent reads the union of the layers it learns from, exactly as a chat
    # does: workshop (house style) + role:<role> (how this KIND of work is done
    # well) + its place + its OWN private notes. P6 built this and the mention
    # path — the only path where an agent actually speaks — was not reading it,
    # so two keepers in two projects still shared nothing. Its own home scope,
    # not the chat's: it brings what IT knows into someone else's conversation.
    try:
        aindex, aused = knowledge.layered_index_block(
            _mentions.home_scope(rec or agent), role, private=f"agent:{slug}")
    except Exception as e:
        log.warning("agent knowledge index failed for %s: %s", slug, e)
        aindex, aused = "", []
    if aindex:
        parts.append(
            "=== WHAT YOU KNOW (layers, narrowest last - a narrower note wins) ===\n"
            + aindex
        )
        log.debug("agent knowledge layers for %s: %s", slug, aused)

    if convo:
        parts.append("=== RECENT CONVERSATION (context; do not reply to it) ===\n" + convo)
    parts.append("=== THE OPERATOR ASKS YOU ===\n" + origin.escape_markers(question or content))
    prompt = "\n\n".join(parts)

    turn = await build_streaming.start_turn(
        thread_id=thread_id, agent_slug=slug, prompt=prompt, model=model,
        cwd=cwd, session_id=sid, resume=False, timeout_s=CHAT_TIMEOUT_S,
        allowed_tools=agent_tools, disallowed_tools=agent_denied,
        mcp_servers=agent_mcp, tools_disabled=not tools_active,
        speaker=slug, turn_id=turn_id, effort=effort,
        permission_mode=permission_mode, attachments=attachments, origin=sends,
    )
    _ = kscope  # the CHAT's scope; the agent reads its own layers above
    return turn


# ---- Attachments ------------------------------------------------------------
# Upload is its OWN request, not part of the turn: the file is on the server
# before Send is pressed, so a 12MB screenshot is not spent while the operator
# waits for a reply, and the chip can say "ready" or "failed" while there is
# still something to do about it. The turn then carries only ids.

@router.post("/attachments", response_model=crm.AttachmentUploaded, response_model_exclude_unset=True)
async def upload_attachment(file: UploadFile, scope: str = Form(""),
                            thread_id: str = Form("")):
    data = await file.read()
    try:
        rec = chat_attachments.ingest(data, file.filename or "file",
                                      file.content_type or "",
                                      scope=scope, thread_id=thread_id)
    except ValueError as e:
        msg = str(e)
        code = 413 if "larger than" in msg else (415 if "not a type" in msg else 400)
        raise HTTPException(code, msg)
    return {"id": rec["id"], "name": rec["name"], "kind": rec["kind"],
            "size": rec["size"], "created_at": rec["created_at"]}


@router.get("/attachments", response_model=crm.AttachmentList, response_model_exclude_unset=True)
async def list_attachments(scope: str = "", q: str = ""):
    """Every stored attachment, newest first, for the Files tab on Memory.

    Newest first because the thing you are looking for is almost always the
    thing you just attached. `missing` says the index has a row whose blob is
    gone, which the list shows rather than hiding: a file that cannot be opened
    should say so where you would go looking for it."""
    recs = chat_attachments.list_records()
    if scope:
        recs = [r for r in recs if (r.get("scope") or "") == scope]
    needle = q.strip().lower()
    if needle:
        recs = [r for r in recs if needle in (r.get("name") or "").lower()]
    # `created_at` is second-granularity, and a multi-file drop writes several
    # rows inside one second. Tie-break on index position so the order is
    # deterministic rather than whatever the stable sort happened to preserve:
    # later-appended is later-attached.
    order = {r["id"]: i for i, r in enumerate(chat_attachments.list_records())}
    recs = sorted(recs, key=lambda r: (r.get("created_at") or "", order.get(r["id"], 0)),
                  reverse=True)
    out = [{
        "id": r["id"], "name": r["name"], "kind": r["kind"], "size": r["size"],
        "scope": r.get("scope") or "", "thread_id": r.get("thread_id") or "",
        "created_at": r.get("created_at") or "",
        "missing": chat_attachments.blob_for(r) is None,
        # A converted binary (docx/xlsx/pptx/archive): the viewer shows its
        # derived text and offers the original for download.
        "converted": bool(r.get("converter")),
        "source": chat_attachments.attachment_convert.SOURCE_LABEL.get(r.get("converter") or "", ""),
    } for r in recs]
    scopes = sorted({r["scope"] for r in out if r["scope"]})
    return {"files": out, "count": len(out), "scopes": scopes}


@router.delete("/attachments/{att_id}", response_model=crm.AttachmentDeleted, response_model_exclude_unset=True)
async def delete_attachment(att_id: str):
    if not chat_attachments.delete(att_id):
        raise HTTPException(404, "attachment not found")
    return {"deleted": att_id}



def _content_disposition(kind: str, filename: str) -> str:
    """A Content-Disposition value that survives a non-latin-1 filename.

    HTTP header values are latin-1, and a bare f-string interpolation of the
    stored name raises UnicodeEncodeError inside Starlette rather than in this
    handler -- so the route answers 500 with no obvious culprit. macOS names
    every screenshot with U+202F (a narrow no-break space) before AM/PM, which
    is exactly such a character: measured 2026-08-22, 4 of 33 stored
    attachments could not be opened at all, and check_routes_answer had been
    reporting both attachment routes as 5xx since 2026-08-17.

    RFC 6266: send both parameters. `filename` carries an ASCII fallback for
    anything that only understands the original form, and `filename*` carries
    the real name percent-encoded as UTF-8, which every current browser
    prefers. NFKD is what folds U+202F back to a plain space rather than
    dropping it, so the fallback stays readable instead of losing a character.
    """
    ascii_name = (unicodedata.normalize("NFKD", filename)
                  .encode("ascii", "ignore").decode("ascii"))
    # A quote or a backslash would terminate the quoted-string early, and a
    # control character is not legal in a header at all.
    ascii_name = re.sub(r'[\\"\x00-\x1f\x7f]', "", ascii_name).strip() or "download"
    encoded = urllib.parse.quote(filename, safe="")
    return f'{kind}; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


@router.get("/attachments/{att_id}")
async def get_attachment(att_id: str):
    """The stored bytes, for the composer's thumbnail and the transcript's.

    Inline rather than an attachment disposition: this is a preview of something
    the operator just attached, not a download they asked for. The name is
    re-derived from the record, never from the URL.

    For a CONVERTED file (docx/xlsx/archive) this serves the derived TEXT, not
    the original binary, so the text viewer renders the readable rendering. The
    raw original is reachable at `/original` for download."""
    rec = chat_attachments.get(att_id)
    if not rec:
        raise HTTPException(404, "attachment not found")
    if rec.get("converter"):
        dpath = chat_attachments.derived_path(rec)
        if dpath is None:
            note = rec.get("note") or "could not be converted"
            return PlainTextResponse(f"This file {note}. Download the original to open it.")
        return FileResponse(dpath, media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition":
                                     _content_disposition("inline", f'{rec["name"]}.txt')})
    path = chat_attachments.blob_for(rec)
    if not path:
        raise HTTPException(404, "attachment not found")
    media = rec.get("media_type") or "application/octet-stream"
    if rec.get("kind") == "text":
        media = "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media,
                        headers={"Content-Disposition":
                                 _content_disposition("inline", rec["name"])})


@router.get("/attachments/{att_id}/thumb")
async def get_attachment_thumb(att_id: str, w: int = 320, h: int = 320):
    """A small raster preview of an image or pdf attachment, fitted inside (w, h).

    Generated lazily and cached, so the ref preview card gets small bytes instead
    of the full file. 404 when the attachment is gone or has no raster preview
    (text/converted); the client then shows the kind icon."""
    path = chat_attachments.thumbnail(att_id, w, h)
    if path is None:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(path, media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/attachments/{att_id}/original")
async def get_attachment_original(att_id: str):
    """The raw original bytes of a converted file, as a download. The derived
    text is lossy, so the real docx/xlsx/archive stays reachable."""
    rec = chat_attachments.get(att_id)
    path = chat_attachments.blob_for(rec) if rec else None
    if not rec or not path:
        raise HTTPException(404, "attachment not found")
    media = rec.get("media_type") or "application/octet-stream"
    return FileResponse(path, media_type=media,
                        headers={"Content-Disposition":
                                 _content_disposition("attachment", rec["name"])})


@router.get("/threads/{thread_id}/mentionable", response_model=crm.MentionableAgents, response_model_exclude_unset=True)
async def list_mentionable(thread_id: str):
    """Agents this chat may @-mention, in picker order.

    Server-resolved because the rule is a policy, not a display preference: an
    agent is addressable only if it opted in (`mentionable`) AND this chat's
    scope reaches it. Computing that on the client would put the policy in two
    places and let them disagree."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    from services import mentions as _mentions
    scope = knowledge.scope_for_context(t.get("context_key") or "general")
    return {"scope": scope, "agents": _mentions.mentionable_agents(scope)}


def _without_trailing_copy(t: dict, content: str) -> dict:
    """A copy of the thread with the LAST user message matching `content`
    removed. Used when the ask is already in the log (a refused delivery, a
    recorded queue entry): prompt assembly treats history as everything BEFORE
    the ask, so the stored copy has to come back out or the model reads the
    message twice, once as history and once as the question."""
    msgs = list(t.get("messages") or [])
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user" and (msgs[i].get("content") or "") == content:
            out = dict(t)
            out["messages"] = msgs[:i] + msgs[i + 1:]
            return out
    return t


async def _turn_for_message(thread_id: str, t: dict, content: str,
                            model: str | None, parts: list[TurnPart] | None,
                            *, take_over: str | None = None,
                            already_recorded: bool = False,
                            client_id: str | None = None,
                            effort_in: str | None = None,
                            mode_in: str | None = None,
                            attachments: list[dict] | None = None) -> "build_streaming.Turn":
    """Claim a turn for this message and start it. THE send path.

    The claim is taken FIRST, before a single await, and held until the
    subprocess is spawned. Prompt assembly awaits (knowledge search, note
    retrieval, a summarizer), and every one of those awaits used to be a window
    a second POST could start a rival turn through — observed live: one thread
    with two turns streaming at once and the operator's message reaching
    neither. Now the second POST takes the thread's write lock, sees
    `active_turn_id`, and joins.

    `already_recorded` means the message is in the log because a delivery was
    attempted and refused; recording it again would show it twice."""
    provider = chat_context.get_provider(t.get("context_key"))
    if model is not None and model != t.get("model"):
        chat_store.set_thread_model(thread_id, model)
    model = model or _resolve_model(None, t, provider)
    # Effort resolves against the model we just settled on, not the one stored
    # on the thread, so switching model and level in the same send is judged by
    # what the operator is actually looking at.
    effort = _resolve_effort(effort_in, t, model)
    if effort_in is not None and effort != t.get("effort"):
        chat_store.set_thread_effort(thread_id, effort)
    permission_mode = _resolve_permission_mode(mode_in, t)

    from services import mentions as _mentions
    _chat_scope = knowledge.scope_for_context(t.get("context_key") or "general")
    _mentioned = _mentions.resolve(content, _chat_scope)
    speaker = _mentioned["slug"] if _mentioned is not None else None

    turn_id = build_streaming.new_turn_id()
    joined = await build_streaming.claim_turn(
        thread_id, turn_id, model=model, agent=speaker, force=bool(take_over),
        accepts_input=not _is_raw_command(content),
        # The claim is taken now; the LOG MARKER waits until the message that
        # caused this turn is recorded, just below. Otherwise the log says a turn
        # began before the message that began it. See chat_store.mark_turn_started.
        announce=False)
    if joined is not None:
        raise build_streaming.TurnInFlight(joined)

    try:
        # THE PROMPT'S SNAPSHOT AND ITS FLOOR, one synchronous stretch. The
        # thread is re-read HERE, after the claim, so the prompt is assembled
        # from everything filed up to this instant instead of from the caller's
        # snapshot (taken before its own awaits; a message filed against this
        # claimed turn in that gap used to be in the log, absent from the
        # prompt, undeliverable, and unqueued: silently never answered). The
        # floor is then stamped on the Turn: on one event loop nothing can
        # interleave between the read and the stamp, so "seq <= floor" is
        # exactly "the prompt carries it", and _send routes anything newer to
        # the queue when injection cannot take it.
        fresh = chat_store.get_thread(thread_id)
        if fresh is not None:
            t = fresh
        if already_recorded:
            # The trigger is already in the log, so the fresh read includes it;
            # history must exclude the ask or the model reads it twice.
            t = _without_trailing_copy(t, content)
        elif parts:
            # A queued batch (or a retried send) can carry entries that are
            # ALREADY in the log: a delivery that was refused mid-turn
            # (queue-entry recorded=True), or a resend of an accepted message.
            # Their append below no-ops on client_id; their stored copies come
            # out of history for the same reason as above.
            for _p in parts:
                if _p.client_id and chat_log.find_message_by_client_id(
                        thread_id, _p.client_id):
                    t = _without_trailing_copy(t, _p.text or "")
        if not already_recorded:
            _record_user_turn(thread_id, content, parts, client_id=client_id,
                              attachments=attachments)
        chat_store.mark_turn_started(thread_id, turn_id)
        _turn_obj = build_streaming.get_turn(turn_id)
        if _turn_obj is not None:
            _m = chat_log.read_meta(thread_id) or {}
            _turn_obj.prompt_through_seq = int(_m.get("last_seq") or 0)
        # ---- @mention: this message is addressed to an AGENT, not the assistant.
        # The agent answers in the chat, attributed to it, and the ordinary
        # assistant stays out of it. Resolution is scope-bounded and opt-in
        # (services/mentions); anything that does not resolve is ordinary text and
        # falls through, so an email address or a stray "@" can never fail a message.
        sends = _sends_behind(content, parts, client_id, attachments)
        if _mentioned is not None:
            return await _start_agent_mention_turn(
                thread_id=thread_id, thread=t, agent=_mentioned, content=content,
                model=model, turn_id=turn_id, effort=effort,
                permission_mode=permission_mode, attachments=attachments,
                sends=sends)
        return await _start_assistant_turn(
            thread_id=thread_id, t=t, provider=provider, content=content,
            model=model, turn_id=turn_id, effort=effort,
            permission_mode=permission_mode, attachments=attachments,
            sends=sends)
    except Exception:
        # The claim outlives nothing. Give the thread back so the next message is
        # not told a turn is in flight that never spawned.
        chat_store.release_turn(thread_id, turn_id)
        raise


async def _start_assistant_turn(*, thread_id: str, t: dict, provider, content: str,
                                model: str, turn_id: str, effort: str | None = None,
                                permission_mode: str | None = None,
                                attachments: list[dict] | None = None,
                                sends: list[dict] | None = None):
    """Spawn the turn for an ordinary (non-mention) message.

    Build-agent chats carry the AGENT's persistent Claude Code session; every
    other chat (project, general, app assistant) carries ONE session per THREAD,
    so a conversation continues instead of being re-derived each turn. The tool
    surface still differs by provider; only the continuity is shared."""
    # This chat's own MCP choice: None (the overwhelmingly common case) means
    # every server, and every branch below must then produce exactly the argv it
    # produced before the control existed. A list narrows the servers the turn
    # can reach, and — because a tool named in --allowedTools whose server is
    # not in the strict config is a tool that cannot exist — it narrows the
    # allowlist to match. Applied HERE rather than deeper down so the filter
    # runs once, on the values actually handed to the spawn.
    #
    # Computed ABOVE the build-agent branch, not below it: that branch returns
    # early, so a build-agent chat never reached this at all and its per-thread
    # MCP choice was silently discarded — the one chat kind whose turns run the
    # full agentic toolset.
    mcp_selection = chat_store.get_thread_mcp_servers(thread_id)
    # EVERY MCP server on this instance is configured with the OPERATOR's own
    # credentials (Plaid, Google, Discord...) — there is no per-user credential
    # provisioning yet, for any provider. `mcp_selection is None` reads as "give
    # it every server this provider can reach": on the narrow path that is
    # `provider.mcp_servers` (e.g. Plaid for budget/finance), and on the
    # full-agentic path (project/build/development) it is literally every server
    # in `~/.claude.json` — no provider list scopes it at all. Both are correct
    # for the operator's OWN chats, which is what this was always exercised
    # against. A non-operator caller gets an explicit empty selection instead of
    # the implicit "everything", so the exact same downstream code hands out
    # nothing rather than someone else's accounts. See
    # the 2026-08-11 scope-separation security audit §4.
    if mcp_selection is None and not ownership.caller_is_operator():
        mcp_selection = []

    # WHICH TREE THIS TURN RUNS IN, and the lease on it. Connection C-3 of
    # `docs/platform-redesign/workspace-ownership-and-leases-2026-08-22.md`:
    # every other piece of that design was built and none of it had ever run,
    # because nothing gave an interactive chat a lease.
    #
    # OPT-IN, AND THE OPT-IN IS THE PIN. A chat that was never pinned to a
    # branch comes back with `cwd` exactly as it was computed here before —
    # `provider.dir` for a project or maker chat — holding nothing, writing no
    # presence and taking no lease. That is section 10.2 ("most sessions never
    # leave this state") and it is why turning this on cannot surprise anybody
    # who never asked for a branch.
    #
    # NEVER RAISES. `enter` answers with a usable cwd for every failure it can
    # meet, including no lease store at all, so a turn cannot fail to spawn
    # because the bookkeeping was unavailable.
    tree = chat_worktree.enter(
        thread_id, t, provider,
        fallback_cwd=(str(provider.dir) if chat_context.is_build_provider(provider)
                      else str(paths.chat_cwd())),
        activity=f"a chat turn in {t.get('title') or thread_id}",
        preempt=True)

    if isinstance(provider, chat_context.MakerAgentContextProvider):
        from services import build_agents as _ba
        try:
            sid, is_new = _ba.ensure_session_id(provider.slug)
        except KeyError:
            sid, is_new = None, True
        # Stale-session guard: an existing id whose claude session no longer exists
        # on disk (old agent, post-reboot prune) would make `--resume` fail the
        # whole turn. Detect it up front and start fresh so the turn just works.
        if sid and not is_new and not build_streaming.session_alive(sid, tree.cwd):
            log.info("agent %s: claude session %s is gone; starting fresh", provider.slug, sid)
            _ba.reset_session(provider.slug)
            try:
                sid, is_new = _ba.ensure_session_id(provider.slug)
            except KeyError:
                sid, is_new = None, True
        # First turn: full assembled prompt. Resumed turn: just the new message
        # with the runtime briefing re-sent (the session retains persona/history
        # but the environment block must refresh every turn). Slash commands go
        # raw so the CLI registers them as commands.
        if _is_raw_command(content):
            prompt = content
            resume_flag = bool(sid and not is_new)
        elif sid and not is_new:
            briefing = chat_context.runtime_briefing(provider)
            head = [briefing] if briefing else []
            # The same contract drift the thread path guards, on the branch that
            # resumes an AGENT's session. Marks live on the thread while this
            # session is keyed by agent slug and shared across that agent's
            # chats, so two threads on one agent can each refresh it once. That
            # is a few hundred wasted tokens on a deploy, against a session that
            # otherwise obeys a retired contract forever.
            try:
                contract = chat_context.instruction_contract(
                    provider, enable_chat_tools=ENABLE_CHAT_TOOLS)
                c_digest = chat_context.contract_digest(contract)
                if c_digest != chat_store.get_turn_marks(t).get("contract_hash"):
                    head.append(chat_context.contract_update_block(contract))
                    chat_store.set_turn_marks(thread_id, contract_hash=c_digest)
            except Exception as e:  # noqa: BLE001 - never fail a turn over this
                log.warning("agent contract refresh skipped for %s: %s", provider.key, e)
            head.append("=== OPERATOR MESSAGE ===\n" + content)
            prompt = "\n\n".join(head) if len(head) > 1 else content
            resume_flag = True
        else:
            prompt, _ = await _assemble_full_prompt(t, provider, content)
            resume_flag = False
        _link_build_skills(provider)  # native .claude/skills discovery (Phase 5)
        return await build_streaming.start_turn(
            thread_id=thread_id, agent_slug=provider.slug, prompt=prompt, model=model,
            cwd=tree.cwd, session_id=sid, resume=resume_flag,
            timeout_s=CHAT_TIMEOUT_S, turn_id=turn_id, effort=effort,
            permission_mode=permission_mode, mcp_selection=mcp_selection,
            tree=(tree.as_dict() if tree.held else None),
            origin=sends,
        )

    is_project = chat_context.is_build_provider(provider)
    if is_project:
        _link_build_skills(provider)
        cwd = tree.cwd
        allowed_tools: list[str] | None = None
        mcp_servers: list[str] = []
        tools_disabled = False
    else:
        cwd = str(paths.chat_cwd())
        if ENABLE_CHAT_TOOLS:
            mcp_servers = llm_router.select_names(list(provider.mcp_servers), mcp_selection)
            allowed_tools = llm_router.filter_allowed_tools(
                chat_context.effective_allowed_tools(provider),
                {n: {} for n in mcp_servers}, mcp_selection)
            tools_disabled = False
        else:
            allowed_tools, mcp_servers, tools_disabled = None, [], True

    sid, is_new = _thread_session(thread_id, cwd)
    if _is_raw_command(content):
        # A slash command goes raw so the CLI registers it as a command (this is
        # what makes a native /compact actually compact the live session).
        prompt, resume_flag = content, bool(sid and not is_new)
    elif sid and not is_new:
        prompt, resume_flag = await _resumed_prompt(t, provider, content), True
    else:
        prompt, _ = await _assemble_full_prompt(t, provider, content)
        resume_flag = False
    return await build_streaming.start_turn(
        thread_id=thread_id, agent_slug=getattr(provider, "slug", provider.key),
        prompt=prompt, model=model, cwd=cwd,
        session_id=sid, resume=resume_flag,
        timeout_s=CHAT_TIMEOUT_S, turn_id=turn_id, effort=effort,
        permission_mode=permission_mode, attachments=attachments,
        tree=(tree.as_dict() if tree.held else None),
        allowed_tools=allowed_tools, mcp_servers=mcp_servers, tools_disabled=tools_disabled,
        # Redundant on the narrow branch above (which already filtered
        # `mcp_servers` and `allowed_tools` through it, and the filter is
        # idempotent), load-bearing on the project branch, which sets
        # `allowed_tools=None` and would otherwise emit no MCP flags at all.
        mcp_selection=mcp_selection,
        origin=sends,
    )


async def _send(thread_id: str, content: str, model_in: str | None = None,
                parts: list[TurnPart] | None = None,
                client_id: str | None = None,
                effort_in: str | None = None,
                mode_in: str | None = None,
                attachments: list[dict] | None = None,
                sent_at: str | None = None) -> dict:
    """Send one message into a chat: deliver it to the turn in flight, or start
    one for it. Returns `{turn_id, injected, at_segment}`.

    ---- ONE TURN PER THREAD -------------------------------------------------
    If a turn is already going here, this message JOINS it instead of starting a
    rival. The decision belongs on the server: a browser only knows whether ITS
    OWN stream is open, so a second tab, a dropped SSE, or a reload each believe
    the chat is idle and cheerfully start another turn.

    Joining is also what the operator actually wants from a message typed during
    a turn: delivered now, at the running turn's next step boundary, rather than
    queued behind everything it is doing.

    ---- PERSIST, THEN DELIVER ----------------------------------------------
    The message is written to the log before anything is written to the turn's
    input channel, so the transcript can never be behind what the model read. If
    delivery then fails, the message is already recorded and a fresh turn is
    started for it; it is not written twice.
    """
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    # An archived conversation is closed, and the archive shows it without a way
    # in. A tab left open on it before it was archived still has a composer,
    # though, so the refusal lives here rather than in the UI that hides it. It
    # says what to do, because there is something to do.
    if t.get("archived_at"):
        raise HTTPException(409, "this chat is archived. Restore it to send.")
    # A chat bound to a developer-mode scope, on an installation where the flag
    # is off, must not just answer as if it were an ordinary chat. get_provider
    # degrades to `general` there (it is total by contract and cannot raise), so
    # without this the thread keeps its `development` badge, silently loses its
    # cwd and its tools, and replies anyway. Refuse at the one moment the
    # operator is present, with the sentence that explains the flag.
    if chat_context.dev_scope_refused(t.get("context_key")):
        raise HTTPException(403, task_scope.dev_mode_refusal(t.get("context_key"))
                            or "developer mode is off, so this scope cannot run here")

    try:
        model = chat_context.normalize_model(model_in)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # THE THREE PICKERS COMMIT ON CHANGE AND APPLY TO THE NEXT TURN. Model,
    # effort and permission mode are all per-thread preferences, so all three are
    # recorded HERE, before the join-or-start fork. A message that joins a turn in
    # flight cannot change that turn's settings (the process is spawned and its
    # argv is fixed at spawn), but what the operator just picked still has to
    # survive to the next turn, or the picker and the thread disagree the moment
    # the turn ends.
    #
    # MODEL USED TO BE THE ODD ONE OUT, and it was the only one that lost data.
    # It was persisted only by `_turn_for_message`, which runs ONLY when this
    # message starts a fresh turn. A model picked while a turn was streaming was
    # therefore dropped outright whenever the message joined that turn instead:
    # never written, so the picker silently snapped back to the old model on the
    # next thread load, with nothing on screen to say it had.
    if model is not None and model != t.get("model"):
        chat_store.set_thread_model(thread_id, model)
        t = chat_store.get_thread(thread_id) or t
    if effort_in is not None:
        _eff = _resolve_effort(
            effort_in, t, _resolve_model(model, t, chat_context.get_provider(t.get("context_key"))))
        if _eff != t.get("effort"):
            chat_store.set_thread_effort(thread_id, _eff)
            t = chat_store.get_thread(thread_id) or t
    if mode_in is not None:
        _pm = _resolve_permission_mode(mode_in, t)
        if _pm != t.get("permission_mode"):
            chat_store.set_thread_permission_mode(thread_id, _pm)
            t = chat_store.get_thread(thread_id) or t

    take_over: str | None = None
    already_recorded = False
    turn_id = _live_turn_id(thread_id)
    # NOTHING OVERTAKES SOMETHING ALREADY WAITING. A queue means earlier
    # messages are owed a turn, so this one joins the back of it rather than
    # starting a turn of its own — otherwise the window between a command turn
    # ending and the drain firing is one where a message typed LATER is answered
    # FIRST, and the transcript records the two in the wrong order permanently.
    # The queue is ordered by when enter was hit; this is what keeps that true
    # once the thread goes idle.
    # A PENDING OPERATION QUEUES A MESSAGE THE SAME WAY A WAITING QUEUE DOES.
    # While a fold or clear is in flight (services/thread_ops), a new turn
    # would run on the pre-operation prompt and race the operation's writes:
    # exactly the 2026-08-14 compact incident. The message is accepted and
    # durable; the drain sends it the moment the operation lands.
    if not turn_id and (chat_queue.read(thread_id) or thread_ops.pending(thread_id)):
        st = chat_queue.enqueue(
            thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
            ts=_first_part_ts(parts) or sent_at, attachments=attachments,
            model=model_in, effort=effort_in, permission_mode=mode_in)
        chat_drain.drain_soon(thread_id, reason="joined the waiting queue")
        return {"turn_id": None, "injected": False, "queued": True,
                "at_segment": None, "queue": st}
    if turn_id and (not build_streaming.turn_accepts_input(turn_id)
                    or chat_queue.is_command(content)):
        # A COMMAND TURN CANNOT BE SPOKEN TO. `/compact` folds the session and
        # exits; it never reads a word of its input. Filing this message against
        # it is what made one disappear on 2026-08-10 — recorded in the log,
        # delivered to a process that had stopped listening, and never answered.
        #
        # A COMMAND CANNOT BE INJECTED EITHER, which is the same fact from the
        # other side: injection hands text to the MODEL, and a command is
        # executed by the CLI's command layer, which only runs between turns.
        # Sent mid-turn it is swallowed with no user message, no command, no
        # error.
        #
        # SO IT IS QUEUED, AND THE QUEUE IS OURS. This used to answer
        # `deferred: true` and record nothing, on the reasoning that nothing is
        # owed by a message that has not been accepted — leaving the only copy
        # in the browser's localStorage. It is owed: the operator hit enter. Two
        # of them were lost that way on 2026-08-12 when the tab moved to another
        # chat before the command finished. The server takes it now, it is
        # visible from every device, and `chat_drain` sends it when this turn
        # ends whether or not anything is watching. See services/chat_queue.
        st = chat_queue.enqueue(
            thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
            ts=_first_part_ts(parts) or sent_at, attachments=attachments,
            model=model_in, effort=effort_in, permission_mode=mode_in)
        # The turn may have finished between the check above and the write. The
        # drain is idempotent and refuses when a turn is live, so asking costs
        # nothing and not asking would strand the message until the next turn.
        chat_drain.drain_soon(thread_id, reason="enqueued behind a command turn")
        return {"turn_id": turn_id, "injected": False, "queued": True,
                "at_segment": None, "queue": st}
    if turn_id:
        # ONE IDENTITY BEFORE ANYTHING IS WRITTEN. The paths below may file the
        # message in the log AND enqueue it (a refused delivery); the log
        # dedupes and the drain strips by client_id, so the two copies must
        # share one. Minting it lazily at the enqueue meant a caller that sent
        # none produced a log row with no id and a queue entry with a fresh
        # one, and the drain re-recorded the message as a duplicate.
        client_id = client_id or chat_log.new_ulid()
        at_segment, msg_seq = _file_during_turn(thread_id, turn_id, content,
                                                client_id=client_id,
                                                attachments=attachments)
        if build_streaming.inject(turn_id, content, attachments,
                                  client_id=client_id, sent_at=sent_at):
            return {"turn_id": turn_id, "injected": True, "at_segment": at_segment}
        if not build_streaming.turn_is_ending(turn_id):
            # Claimed but not spawned yet. Whether its prompt will carry this
            # message is a COMPARISON, not a hope: the prompt's history floor is
            # stamped in the same synchronous stretch that assembles it
            # (_turn_for_message), so a floor at or above this seq means the
            # message is in the prompt, and an older floor means it provably is
            # not. The unstamped case means the floor stretch has not run yet,
            # and the fresh read it starts with will include this message.
            _t_obj = build_streaming.get_turn(turn_id)
            _floor = _t_obj.prompt_through_seq if _t_obj is not None else None
            if _floor is not None and msg_seq > _floor:
                # In the log, not in the prompt, and the input channel is not
                # up: nothing would ever deliver it. Queue it (marked recorded,
                # so the drain does not mistake log-presence for answered) and
                # it goes out when this turn ends.
                st = chat_queue.enqueue(
                    thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
                    ts=_first_part_ts(parts) or sent_at, attachments=attachments,
                    model=model_in, effort=effort_in, permission_mode=mode_in,
                    recorded=True)
                chat_drain.drain_soon(thread_id, reason="missed a spawning turn's prompt")
                return {"turn_id": turn_id, "injected": False, "queued": True,
                        "at_segment": at_segment, "queue": st}
            return {"turn_id": turn_id, "injected": False, "at_segment": at_segment}
        # It has spawned and its input channel is gone: it is finishing and will
        # never read this. The message is already in the log. If messages are
        # ALREADY WAITING (the finalize window requeues undelivered ones),
        # taking the thread over now would answer this message before them and
        # bake that order into the transcript; join the back of the queue
        # instead. A PENDING OPERATION queues it for the same reason as the
        # idle branch above: without this check, a send landing in the finalize
        # window of the turn a fold is waiting out started a fresh turn on the
        # pre-fold prompt, which is the incident itself through a side door.
        # Only an empty queue with no pending operation takes the thread over.
        if chat_queue.read(thread_id) or thread_ops.pending(thread_id):
            st = chat_queue.enqueue(
                thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
                ts=_first_part_ts(parts) or sent_at, attachments=attachments,
                model=model_in, effort=effort_in, permission_mode=mode_in,
                recorded=True)
            chat_drain.drain_soon(thread_id, reason="joined behind requeued messages")
            return {"turn_id": turn_id, "injected": False, "queued": True,
                    "at_segment": at_segment, "queue": st}
        log.info("thread %s: turn %s is finishing; starting a fresh turn for this message",
                 thread_id, turn_id)
        take_over, already_recorded = turn_id, True

    try:
        turn = await _turn_for_message(thread_id, t, content, model, parts,
                                       take_over=take_over,
                                       already_recorded=already_recorded,
                                       client_id=client_id,
                                       effort_in=effort_in,
                                       mode_in=mode_in,
                                       attachments=attachments)
    except build_streaming.TurnInFlight as e:
        # Another POST won the same event-loop tick. Join its turn rather than
        # starting a rival, which is the whole reason the claim is atomic.
        at_segment = (_file_during_turn(thread_id, e.turn_id, content, client_id=client_id,
                                        attachments=attachments)[0]
                      if not already_recorded else _at_segment(e.turn_id, thread_id))
        build_streaming.inject(e.turn_id, content, attachments,
                               client_id=client_id, sent_at=sent_at)
        return {"turn_id": e.turn_id, "injected": True, "at_segment": at_segment}
    return {"turn_id": turn.id, "injected": False, "at_segment": None}


async def _start_queued_batch(thread_id: str, batch: list[dict]) -> bool:
    """Turn a drained batch into a turn. Registered with `chat_drain`.

    This is the one place the queue re-enters the send path, and it goes in at
    the same door a live message does: the batch becomes `parts`, so every
    entry is recorded as its own message at its own send time (see
    `_record_user_turn`), under the client id it was queued with — which is what
    lets the browser reconcile the bubble it has been showing all along instead
    of drawing a second one.

    Returns False rather than raising when the thread is busy: a turn claimed
    between the drain's check and this call is not an error, it is the reason
    the queue exists.
    """
    t = chat_store.get_thread(thread_id)
    if t is None:
        # The thread was deleted while messages waited. Nothing can carry them;
        # dropping the queue is the only honest outcome, and it is the operator's
        # own delete that caused it.
        chat_queue.forget(thread_id)
        return False
    if t.get("archived_at"):
        # ARCHIVING A CHAT PUTS IT AWAY, and a queue that fired afterwards would
        # start a turn in a conversation the operator has closed. The messages
        # are NOT dropped: they stay queued, so restoring the chat restores them
        # with it, and the queue holds so nothing goes out unattended in the
        # meantime. The send path refuses an archived thread for the same reason
        # (409); this is that rule on the path a browser is not driving.
        chat_queue.set_held(thread_id, True, error="This chat is archived. Restore it to send these.")
        return False
    if chat_context.dev_scope_refused(t.get("context_key")):
        raise RuntimeError(task_scope.dev_mode_refusal(t.get("context_key"))
                           or "developer mode is off, so this scope cannot run here")
    # A CONTINUATION SEED is not an operator message and never batches: a
    # finished background run left it so the thread would take its own next turn
    # (services/chat_drain.request_continuation). It starts a turn with
    # `already_recorded=True`, so nothing is attributed to the operator and no
    # user bubble appears; the run's result is already an autonomous message in
    # the thread, and this seed only tells the model to read it and continue.
    if len(batch) == 1 and batch[0].get("auto"):
        seed = (batch[0].get("text") or "").strip()
        if not seed:
            return False
        try:
            await _turn_for_message(
                thread_id, t, seed, None, None,
                already_recorded=True, client_id=None)
        except build_streaming.TurnInFlight:
            # A real turn claimed the thread first. The seed stays queued and the
            # continuation loop retries when that turn ends.
            return False
        return True
    # ALREADY DELIVERED? Entries leave the queue only after the turn carrying
    # them has been recorded, which is the right order — a crash in between
    # leaves them queued rather than gone. The cost is that a crash in exactly
    # that window leaves them queued AND committed, and the startup drain would
    # run them a second time. The log itself is idempotent on `client_id` so
    # nothing would be duplicated in the transcript, but the model would be
    # handed the message twice. Checking first is what makes the drain
    # idempotent rather than merely non-destructive.
    done = [e for e in batch
            if e.get("client_id") and not e.get("recorded")
            and chat_log.find_message_by_client_id(thread_id, e["client_id"])]
    if done:
        chat_queue.drop(thread_id, [e["client_id"] for e in done])
        batch = [e for e in batch if e not in done]
        log.info("thread %s: %d queued message(s) were already delivered; dropped",
                 thread_id, len(done))
    if not batch:
        return False
    parts = [TurnPart(text=e.get("text") or "", sent_at=e.get("ts"),
                      client_id=e.get("client_id"), sender=e.get("sender"))
             for e in batch]
    content = chat_queue.joined_text(batch)
    if not content:
        return False
    atts = chat_queue.attachments_of(batch)
    try:
        await _turn_for_message(
            thread_id, t, content, chat_queue.setting_of(batch, "model"),
            # ALWAYS the parts, even for one: each queued message is recorded at
            # the moment it was typed, under the id the browser has been showing
            # it with. See `_record_user_turn`.
            parts,
            client_id=parts[0].client_id if parts else None,
            effort_in=chat_queue.setting_of(batch, "effort"),
            mode_in=chat_queue.setting_of(batch, "permission_mode"),
            attachments=atts or None,
        )
    except build_streaming.TurnInFlight:
        # Something claimed the thread first. The batch stays queued and the
        # drain runs again when that turn ends.
        return False
    return True


chat_drain.register_starter(_start_queued_batch)


@router.post("/threads/{thread_id}/queued/{client_id}/cancel", response_model=cm.QueueState, response_model_exclude_unset=True)
async def cancel_queued_message(thread_id: str, client_id: str):
    """Take a queued message back out.

    It is the operator's text and they must be able to unsend it before it goes.
    This is the only way anything leaves the queue undelivered, and it is a
    deliberate act by the person who wrote it."""
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")
    return chat_queue.drop(thread_id, [client_id])


@router.post("/threads/{thread_id}/queued/release", response_model=cm.QueueState, response_model_exclude_unset=True)
async def release_queued_messages(thread_id: str):
    """Send what is waiting: clears a Stop hold or a failed drain's diagnosis.

    The Retry beside a queue that could not be sent, and the "send these now"
    after a Stop, are the same action, so they are one endpoint."""
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")
    chat_queue.set_held(thread_id, False)
    await chat_drain.drain(thread_id, reason="operator released the queue")
    return chat_queue.state(thread_id)


@router.post("/threads/{thread_id}/turns", response_model=cm.MessageAccepted, response_model_exclude_unset=True)
async def start_streaming_turn(thread_id: str, body: StartTurnIn):
    content = (body.content or "").strip()
    atts = [a.model_dump() for a in (body.attachments or [])]
    # A turn may be files with nothing typed: dropping a screenshot and pressing
    # Send is a complete message. It still needs SOMETHING as the text block, so
    # the filenames stand in for it rather than sending an empty string.
    if not content and atts:
        content = chat_attachments.describe(atts)
    if not content:
        raise HTTPException(400, "empty message")
    out = await _send(thread_id, content, body.model, body.parts,
                      client_id=body.client_id, effort_in=body.effort,
                      mode_in=body.permission_mode, attachments=atts,
                      sent_at=body.sent_at)
    # ACCEPTED IS ACCEPTED, and the composer it came out of is empty from here
    # on. Every branch below is an acceptance — running now, handed to the turn in
    # flight, or queued — so the clear is unconditional and happens once, on the
    # server, under the key the operator was actually typing in.
    if body.draft_key:
        draft_store.clear_draft(body.draft_key)
    if out.get("queued"):
        # Taken and durable, just not running yet: the thread is busy with
        # something that cannot be spoken to. It is on the thread, every device
        # can see it, and the server sends it when this turn ends.
        return {"turn_id": out["turn_id"], "injected": False, "queued": True,
                "queue": out.get("queue") or {}}
    if out["injected"]:
        return {"turn_id": out["turn_id"], "injected": True,
                "at_segment": out["at_segment"]}
    return {"turn_id": out["turn_id"]}


class PermissionAnswer(BaseModel):
    request_id: str
    # allow | deny. Spelled out rather than a bool so the wire says what it means
    # and a third option (allow-and-stop-asking) can join it without a migration.
    behavior: str
    # Read by the model on a deny. "Not that file, use the other one" steers the
    # next attempt; a bare refusal makes it guess what went wrong.
    message: str | None = None
    # Apply the CLI's own `setMode` suggestion at the same time — this is what
    # "allow and stop asking" is, in the vendor's words rather than ours.
    apply_suggestion: bool = False


@router.post("/threads/{thread_id}/turns/{turn_id}/permission", response_model=cm.PermissionAnswered, response_model_exclude_unset=True)
async def answer_turn_permission(thread_id: str, turn_id: str, body: PermissionAnswer):
    """Answer a tool-permission request from a running turn.

    The turn is BLOCKED on stdin until this lands. There is no wall clock on a
    chat turn, so nothing else will ever unblock it."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    if body.behavior not in ("allow", "deny"):
        raise HTTPException(400, "behavior must be 'allow' or 'deny'")
    allow = body.behavior == "allow"
    # Approving a plan always leaves plan mode; every other "stop asking" is
    # opt-in, because it loosens the chat and the operator has to mean it.
    suggestion = _mode_suggestion(turn_id, body.request_id, t) if allow and (
        body.apply_suggestion or _is_plan_exit(turn_id, body.request_id)
    ) else None
    ok, detail = build_streaming.resolve_permission(
        turn_id, body.request_id, allow, message=body.message)
    if not ok:
        raise HTTPException(409, detail)
    # Applied only after the answer landed: a mode change recorded for a turn
    # that never got the approval would leave the chat looser than the operator
    # believes, on the strength of a click that did nothing.
    if suggestion:
        chat_store.set_thread_permission_mode(thread_id, suggestion)
    return {"state": detail, "permission_mode": suggestion or t.get("permission_mode")}


# Where a chat lands after its plan is approved when there is NOTHING BETTER TO
# GO ON. Plan mode is a ONE-TURN posture: staying in it after the plan is agreed
# leaves a chat that has just been told to go ahead and still cannot edit
# anything.
#
# This is the fallback, not the destination. `chat_store` records the mode a
# chat was running at when it entered plan (`pre_plan_permission_mode`) and
# `_post_plan_mode` below puts it back there, because a chat on Manual that
# stepped into Plan for one question should come out on Manual — a fixed landing
# mode silently loosens it instead.
POST_PLAN_MODE = "acceptEdits"


def _post_plan_mode(thread: dict) -> str:
    """Where this chat goes when its plan is approved: back where it came from,
    else POST_PLAN_MODE.

    A recorded `plan` is ignored — that would be a chat that entered plan twice
    without the note being spent, and honouring it would trap the chat in plan
    exactly when it has been told to proceed."""
    try:
        prev = model_caps.normalize_permission_mode(
            thread.get("pre_plan_permission_mode"))
    except ValueError:
        prev = None
    return prev if (prev and prev != "plan") else POST_PLAN_MODE


def _is_plan_exit(turn_id: str, request_id: str) -> bool:
    turn = build_streaming.TURNS.get(turn_id)
    if turn is None:
        return False
    return any(
        seg.get("t") == "approval" and seg.get("id") == request_id
        and seg.get("tool") == "ExitPlanMode"
        for seg in turn.segments
    )


def _mode_suggestion(turn_id: str, request_id: str, thread: dict) -> str | None:
    """The mode to move this chat to on approving this request, or None.

    For an ExitPlanMode the answer is THIS CHAT'S OWN pre-plan mode, and the
    CLI's `setMode` suggestion is not consulted: the CLI proposes a generic
    landing spot (it has no idea the operator was on Manual before), while the
    thread recorded where the chat actually came from. Leaving plan mode is what
    approving a plan MEANS, so there is always an answer here — the fallback
    applies only when nothing was recorded.

    For every other request the CLI's own `setMode` suggestion IS the answer,
    since that is what "allow and stop asking" means in the vendor's words.
    Anything other than `setMode` is ignored rather than guessed at."""
    turn = build_streaming.TURNS.get(turn_id)
    if turn is None:
        return None
    for seg in turn.segments:
        if seg.get("t") != "approval" or seg.get("id") != request_id:
            continue
        if seg.get("tool") == "ExitPlanMode":
            return _post_plan_mode(thread)
        for s in seg.get("suggestions") or []:
            if isinstance(s, dict) and s.get("type") == "setMode" and s.get("mode"):
                try:
                    return model_caps.normalize_permission_mode(str(s["mode"]))
                except ValueError:
                    break
        return None
    return None


class InjectMessage(BaseModel):
    content: str
    # See StartTurnIn.client_id. A mid-turn message is persisted before it is
    # delivered, so the browser's id is the only thing that lets a retry of a
    # request whose response was lost land as the same message rather than a
    # second one.
    client_id: str | None = None
    # Same as StartTurnIn.attachments: files already uploaded, ids only. A file
    # attached to a message typed DURING a turn reaches the running model too.
    attachments: list[AttachmentRefIn] | None = None
    # See StartTurnIn: when the operator hit enter, and which composer this came
    # out of. Both matter here for the same reasons — a message typed during a
    # command turn is queued with its own send time, and the composer it was
    # typed in is cleared by the server that accepted it.
    sent_at: str | None = None
    draft_key: str | None = None


def _live_turn_id(thread_id: str) -> str | None:
    """The turn currently going on this thread, or None. One field read.

    Server-derived, and now trivially so: `meta.active_turn_id` names it and
    `build_streaming.turn_is_live` says whether this process is still driving it.

    This used to scan the transcript backwards for an assistant message flagged
    `streaming`, which was fragile in exactly the way the rebuild removes. It
    first looked only at the last message, so the FIRST mid-turn message made the
    last message a user message and every message after it was told no turn was
    running — two turns ended up streaming into one thread at once. Scanning
    backwards fixed that symptom and left the flag itself orphanable by a
    restart. There is no flag any more, and nothing to orphan."""
    turn_id = chat_store.active_turn_id(thread_id)
    return turn_id if build_streaming.turn_is_live(turn_id) else None


def _at_segment(turn_id: str, thread_id: str) -> int:
    """How much of the running reply had streamed when a message landed.

    Ask the LIVE turn, not the stored snapshot: the snapshot is written on a
    throttle, so it lags what is on screen, and the error is one-directional. A
    low `at_segment` renders the message EARLIER than it arrived, above output it
    never saw, which is what makes a reply look like an answer to a message it
    had not read. The snapshot remains the fallback for a turn this process no
    longer holds."""
    live = build_streaming.get_turn(turn_id)
    if live is not None:
        return len(live.segments)
    return len(_snapshot_segments(turn_id, thread_id))


def _snapshot_segments(turn_id: str, thread_id: str) -> list[dict]:
    turn = chat_log.active_turn(thread_id)
    snap = (turn or {}).get("live") or {}
    return ((snap.get("meta") or {}).get("segments")) or []


def _cut_point(turn_id: str, thread_id: str) -> tuple[int, int, str]:
    """Where a message landed in the running reply: (segment, unit, cut token).

    THE TOKEN IS THE ANSWER; the two numbers are kept for what is already on
    disk. `cut_here` writes the token onto the segment the message arrived
    after, so the position is RECORDED rather than re-derived from an index into
    a buffer that keeps growing. See docs/decisions/0018 for the measured way an
    index drifts (two parallel delegations, and the second falls below a message
    typed after it was launched).

    `at_segment` counts top-level segments and freezes for the whole of a
    subagent delegation. `at_unit` counts nested steps too and moves with the
    work, but moves under a recorded coordinate as well. Both are still written:
    every message already on disk carries them and nothing may re-render an old
    transcript differently.

    Sealing happens inside `cut_here`, in the same call: the cut is only real if
    text written afterwards cannot merge back above it, and it must not be
    possible for the anchor to move between the two.

    The snapshot branch is the degraded path, for a turn this process does not
    hold. It cannot record a position (there is no live buffer to record it on),
    so it returns an empty token and the client falls back to the index."""
    live = build_streaming.get_turn(turn_id)
    if live is not None:
        ref = chat_log.new_ulid()
        return len(live.segments), build_streaming.cut_here(live, ref), ref
    segs = _snapshot_segments(turn_id, thread_id)
    return len(segs), build_streaming.unit_count(segs), ""


def _file_during_turn(thread_id: str, turn_id: str, content: str,
                      client_id: str | None = None,
                      attachments: list[dict] | None = None) -> int:
    """Persist a message arriving mid-turn, BEFORE anyone tries to deliver it.

    Order matters and it used to be the other way round: the bytes went to the
    turn's stdin FIFO first and the store write happened after, inside a
    best-effort `except` that only logged. So a failed write meant the model had
    read a message the transcript never recorded, and the next fresh session
    re-assembled a history missing it.

    Persist first and the transcript can only ever be AHEAD of what the model
    saw, which is the recoverable direction. Returns `(at_segment, seq)`:
    `at_segment` is reported to the client, `seq` is what the caller compares
    against the turn's prompt floor when delivery fails."""
    at_segment, at_unit, cut_id = _cut_point(turn_id, thread_id)
    meta = {"injected": True, "during_turn": turn_id,
            "at_segment": at_segment, "at_unit": at_unit}
    if cut_id:
        meta["cut_id"] = cut_id
        # THE SAME FACT, WRITTEN AS STRUCTURE. `_cut_point` just sealed the open
        # text, which is the server saying "this stretch of the reply is
        # finished and a message goes next". That is the one moment the stretch
        # can be given a row with its parent already correct, so the message
        # below can hang from it instead of from the whole reply.
        #
        # Queued on the thread's mirror chain, so it lands BEFORE the message's
        # own tree write however slow the database is; the two must not race for
        # the branch head. Nothing is awaited here: a mid-turn message must not
        # wait on Postgres to reach the model.
        #
        # `at_segment == 0` is the `cut_head` case — nothing had been emitted
        # yet, so there is no stretch to open and the message belongs before the
        # whole reply. See docs/decisions/0018.
        if at_segment:
            chat_log.mirror_next(
                thread_id,
                lambda: chat_branches.open_stretch(thread_id, turn_id))
    if attachments:
        meta["attachments"] = attachments
    msg = chat_store.append_message_during_turn(
        thread_id, "user", content, meta=meta, client_id=client_id,
    )
    return at_segment, int(msg.get("seq") or 0)


@router.post("/threads/{thread_id}/inject", response_model=cm.InjectResult, response_model_exclude_unset=True)
async def inject_into_thread(thread_id: str, body: InjectMessage):
    """Hand a message to whatever turn is going on this thread.

    The client should not have to know a turn id to talk to its own chat. It knows
    which conversation it is in; the server resolves the rest.

    The message is PERSISTED before delivery is attempted, so `delivered: false`
    means "it is in the transcript and no turn has it", never "it is gone"."""
    content = (body.content or "").strip()
    atts = [a.model_dump() for a in (body.attachments or [])]
    if not content and atts:
        content = chat_attachments.describe(atts)
    if not content:
        raise HTTPException(400, "empty message")
    if not chat_log.exists(thread_id):
        raise HTTPException(404, "thread not found")
    if _live_turn_id(thread_id) is None:
        # A PENDING OPERATION (compact/clear) IS NOT A TURN, so it never reaches
        # `_send` below, but this message must not be left for the client's
        # ordinary-turn fallback to sort out. That fallback (POST /turns, which
        # re-enters `_send` and correctly checks `thread_ops.pending`) is not a
        # data-loss bug -- it does queue the message correctly in the end -- but
        # it costs a second round trip on exactly the path an operator hits
        # while a fold is already taking ~30s to answer, and it opens a real if
        # narrow window: the operation can land BETWEEN this response and the
        # client's follow-up POST, and a client that started that POST before
        # reading this one's `queued_for_turn: false` would have no way to know
        # to retry through `/turns` instead of assuming the thread is idle. This
        # request already knows the message and already knows the operation is
        # pending, so it queues it directly -- the same call `_send` makes for
        # the idle+pending case just below.
        if thread_ops.pending(thread_id) or chat_queue.read(thread_id):
            st = chat_queue.enqueue(
                thread_id, text=content, client_id=body.client_id or chat_log.new_ulid(),
                ts=body.sent_at, attachments=atts)
            chat_drain.drain_soon(thread_id, reason="joined the queue via inject")
            if body.draft_key:
                draft_store.clear_draft(body.draft_key)
            return {"delivered": False, "queued_for_turn": True, "queued": True,
                    "turn_id": None, "at_segment": None, "queue": st,
                    "reason": "a compact or clear is running on this chat; this goes out next"}
        # Nothing is running and nothing has been written. The client sends it as
        # an ordinary turn instead, which is the only case where re-sending is
        # correct: this endpoint has not taken the message.
        return {"delivered": False, "queued_for_turn": False, "turn_id": None,
                "at_segment": None,
                "reason": "no turn is running on this chat"}
    out = await _send(thread_id, content, client_id=body.client_id, attachments=atts,
                      sent_at=body.sent_at)
    if body.draft_key:
        draft_store.clear_draft(body.draft_key)
    if out.get("queued"):
        # TAKEN, and durable on the thread. A command turn owns the chat and will
        # never read this, so it waits — but it waits HERE, not in the browser,
        # and the server sends it when that turn ends. `delivered: false` with
        # `queued: true` is "we have it, it has not gone yet"; the client must
        # not re-send it.
        return {"delivered": False, "queued_for_turn": True, "queued": True,
                "turn_id": out["turn_id"], "at_segment": None,
                "queue": out.get("queue") or {},
                "reason": "a command is running on this chat; this goes out next"}
    delivered = bool(out["injected"])
    turn_id = out["turn_id"]
    at_segment = out["at_segment"]
    return {
        # NOT "the agent read it". This means the bytes reached the running
        # process. Measured (docs/platform-redesign/chat-message-ordering-audit-
        # 2026-08-05.md §2): the model addresses a mid-turn message at the END of
        # its current plan, skipping every intervening step boundary — 24.5s and
        # four boundaries in the probe. `queued_for_turn` is what is actually
        # known, and the composer says that instead of implying it landed.
        # THE CHAT HAS THE MESSAGE either way, which is what the client needs to
        # know: it is in the log before any delivery is attempted, so re-sending
        # it would show it twice. `delivered` says whether the RUNNING turn took
        # it; false here means a fresh turn was started for it instead.
        "delivered": True,
        "injected": delivered,
        "queued_for_turn": delivered,
        "turn_id": turn_id,
        "at_segment": at_segment,
        "reason": "" if delivered else "the running turn refused input; started a new turn",
    }


@router.post("/turns/{turn_id}/stop", response_model=cm.TurnStopped, response_model_exclude_unset=True)
async def stop_turn(turn_id: str):
    """Stop a running turn. What the composer's Stop button calls.

    STOP MEANS STOP. It used to mean "close this tab's socket": the turn kept
    running and kept spending, and the operator's only signal that it had worked
    was the indicator going quiet, which it did by lying. The indicator now reads
    the server's `active_turn`, so the honest version of the old behaviour was the
    chat still saying "Still running" after Stop — which is what was reported.

    The turn's process group is signalled and whatever it produced is committed as
    the reply, marked `meta.stopped`. Never an error: stopping a turn that has
    already finished is a no-op that returns `stopped: false`, because "it is
    already stopped" is the outcome the caller wanted.

    STOP ALSO HOLDS THE QUEUE. Stopping ends the turn, and the drain fires on
    every turn ending — so without this, pressing Stop would immediately send
    everything typed ahead as a new unattended turn, which is the opposite of
    what Stop means. The hold is recorded on the thread (not in a browser: it
    used to live in component state beside a localStorage queue, so a reload
    dropped the hold, kept the queue, and fired it) and the operator's next send
    releases it."""
    turn = build_streaming.get_turn(turn_id)
    if turn is not None and turn.thread_id and not turn.complete:
        chat_queue.set_held(turn.thread_id, True)
    return await build_streaming.stop_turn(turn_id)


@router.get("/turns/{turn_id}/stream")
async def stream_turn(turn_id: str, request: Request):
    turn = build_streaming.get_turn(turn_id)
    if not turn:
        raise HTTPException(404, "turn not found")

    last_event_id = request.headers.get("Last-Event-ID") or "0"
    try:
        after = max(0, int(last_event_id))
    except ValueError:
        after = 0

    async def gen():
        try:
            async for offset, ev in build_streaming.iter_events(turn, after=after):
                if await request.is_disconnected():
                    return
                payload = _json.dumps({"kind": ev.kind, "payload": ev.payload, "ts": ev.ts})
                # SSE frame: id: <offset>\nevent: <kind>\ndata: <json>\n\n
                yield f"id: {offset}\nevent: {ev.kind}\ndata: {payload}\n\n"
            # Final terminator so clients close cleanly.
            yield "event: end\ndata: {}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
