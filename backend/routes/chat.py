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
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from services import chat_attachments
from services import chat_branches
from services import chat_drain
from services import chat_events
from services import tenancy
from services import chat_queue
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
from services import paths
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
    # Entity-model 2026-07-18 §7 (P1). An agent joins the chat as a participant,
    # not as its home scope. Optional at create; UI can add/remove after.
    participants: list[str] | None = None
    scope: str | None = None
    # How this chat handles work (direct | orchestrated). Omitted means "use the
    # operator's default for new chats" — the toolbar sends it only when the mode
    # was chosen before the first message, which is the whole point of showing
    # the control on a blank chat.
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


class RenameThread(BaseModel):
    title: str


class RescopeThread(BaseModel):
    context_key: str


class SetChatMode(BaseModel):
    mode: str  # direct | orchestrated (chat_context.CHAT_MODES)


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
    # An explicit scope KEY (workshop / project:x / agent:x / build:x). Task
    # proposal cards let the operator choose where a ticket lands, and they used
    # to POST /api/tasks directly to honour that — which meant filing from a card
    # recorded no chat provenance and the ticket had no way back to the
    # conversation that produced it. This is how one endpoint serves both.
    scope: str | None = None
    acceptance: dict | None = None
    priority: int = 0
    effort: str | None = None
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


@router.get("/providers")
async def providers(scope: str | None = None):
    return chat_context.list_providers(scope=scope)


@router.get("/threads")
async def threads(context: str | None = None, archived: bool = False):
    """The live conversations, or the archive. Never both: see list_threads."""
    return chat_store.list_threads(context_key=context, archived=archived)


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


@router.get("/drafts/{key:path}")
async def read_draft(key: str):
    return draft_store.get_draft(key)


@router.put("/drafts/{key:path}")
async def write_draft(key: str, body: DraftPut):
    return draft_store.put_draft(
        key, body.text, body.ts, body.client_id,
        [a.model_dump() for a in (body.attachments or [])])


@router.delete("/drafts/{key:path}")
async def remove_draft(key: str):
    draft_store.clear_draft(key)
    return {"deleted": True}


@router.post("/threads")
async def new_thread(body: CreateThread):
    if body.mode is not None and body.mode not in chat_context.CHAT_MODES:
        raise HTTPException(400, f"mode must be one of {list(chat_context.CHAT_MODES)}")
    return chat_store.create_thread(
        context_key=body.context_key, title=body.title, agent_id=body.agent_id,
        scope=body.scope, participants=body.participants, mode=body.mode,
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


@router.get("/prefs")
async def get_chat_prefs():
    """Operator-level chat settings: the mode a NEW chat starts in, plus the
    effort and permission-mode defaults every chat that never chose for itself
    resolves through. Existing chats keep the `mode` they were created with; the
    other two are consulted per turn, so changing one moves those chats."""
    return _prefs_payload()


@router.post("/prefs")
async def set_chat_prefs(body: ChatPrefs):
    if body.default_mode is not None:
        if body.default_mode not in chat_context.CHAT_MODES:
            raise HTTPException(400, f"default_mode must be one of {list(chat_context.CHAT_MODES)}")
        chat_store.set_default_mode(body.default_mode)
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


@router.get("/threads/{thread_id}")
async def thread(thread_id: str):
    """The thread as a document: meta plus a `messages` list.

    A run in flight appears as a trailing assistant message carrying
    `streaming: true` and its `turn_id`, built as a VIEW of `meta.live_message`
    (chat_store._live_view) rather than stored. There is nothing to self-heal
    here any more: the flag is derived from `active_run_id`, so it cannot be
    orphaned, and a run that did not survive a restart is closed out once at
    startup (build_streaming.reconcile_threads) instead of on every read."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    # Opening a chat shows what is waiting to be sent in it, on the first paint,
    # from the same read that produced the transcript.
    return {**t, **chat_queue.state(thread_id)}


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
    carried ONE RUN's output to whoever knew that run's id, so a client that had
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


@router.get("/threads/{thread_id}/events")
async def thread_events(thread_id: str, after: int = 0, limit: int | None = None):
    """The thread's event log from a cursor. THE sync endpoint.

    Everything needed to render a transcript is here: `events` in seq order,
    `last_seq` to poll from next, and `active_run` with the live snapshot of a
    turn in progress. A client calls this on mount, on focus, on reconnect and
    after any write, and merges it with nothing except the live SSE for the
    active run.

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
      * `active_run` reads the run's start seq straight out of meta now, so it
        no longer needs the events at all.

    That leaves `read_events`, and a poll with nothing new to fetch does not need
    it either: `after >= last_seq` means the caller is already current. `last_seq`
    is reconciled against the log's real tail inside `read_meta`, so this is an
    exact test, not an optimistic one."""
    meta = chat_log.read_meta(thread_id)
    if meta is None:
        raise HTTPException(404, "thread not found")
    last_seq = int(meta.get("last_seq") or 0)
    cursor = max(0, after)
    events = (
        chat_log.read_events(thread_id, after=cursor, limit=limit)
        if cursor < last_seq else []
    )
    # WHICH BRANCH THIS IS, on every poll, and it has to be here rather than on
    # its own endpoint. This is a CURSOR sync: the client merges what it is given
    # into what it already holds. Filtering alone could never make it forget the
    # abandoned tail, because a cursor fetch after a fork returns nothing new at
    # all. So the branch identity travels with the events, the client notices it
    # changed, and re-reads from zero. Same reasoning as the cut and the queue
    # below: one endpoint answers "what is in this chat", and things that must
    # agree are not allowed to arrive separately.
    branch = chat_branches.branch_state_sync(thread_id)
    events = chat_branches.keep_visible(events, branch["hidden"])
    return {
        "events": events,
        "last_seq": last_seq,
        "branch": branch["branch"],
        "branch_count": branch["count"],
        "active_run": _active_run_payload(thread_id, meta),
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
    }


def _cut_payload(thread_id: str) -> dict:
    """`{cleared_through, cut_mode}`: how many messages are behind the context
    cut, and which command put them there. Zero means no cut.

    Meta only. This runs on every poll from every open tab, and reading it
    through `get_thread` parsed the entire log to answer it — the exact cost
    this endpoint was rebuilt to remove, and pinned by
    test_chat_run_lifecycle.TestTheSyncEndpointDoesNotReparseTheLog."""
    return chat_store.cut_state(thread_id)


def _active_run_payload(thread_id: str, meta: dict) -> dict | None:
    """The live run, plus whether it can be spoken to.

    `command` is added HERE rather than in chat_log because only this layer can
    see the turn registry. The client needs it to decide between injecting a
    message and queueing it: a command turn takes no input, and a client that
    does not know that sends into a void."""
    run = chat_log.active_run(thread_id, meta=meta)
    if run and not build_streaming.run_accepts_input(run.get("run_id")):
        return {**run, "accepts_input": False}
    return run


@router.patch("/threads/{thread_id}")
async def rename(thread_id: str, body: RenameThread):
    t = chat_store.rename_thread(thread_id, body.title)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


@router.post("/threads/{thread_id}/archive")
async def archive(thread_id: str):
    """Close the conversation without destroying it. This is what the chat's own
    menu does; DELETE below is reachable only from the archive."""
    t = chat_store.archive_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


@router.post("/threads/{thread_id}/restore")
async def restore(thread_id: str):
    t = chat_store.restore_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


# --- branches (ADR 0020) -----------------------------------------------------
# Going back to a point in the conversation and carrying on from there. There is
# ONE verb for it, because in a store where nothing is deleted, "return to here"
# and "branch from here" are the same act: the line you were on keeps its own
# ref and stays listed below. Two buttons would be two names for one operation.

@router.get("/threads/{thread_id}/branches")
async def branches(thread_id: str):
    """Every branch of this conversation, most recently active first.

    Returns an empty list rather than an error when the relational store is not
    reachable: a chat renders without this, it just cannot be branched.
    """
    return {"branches": await chat_branches.list_branches(thread_id)}


@router.post("/threads/{thread_id}/branches")
async def fork_thread(thread_id: str, body: ForkThread):
    """Carry on from `anchor`. Nothing is deleted."""
    from db import chat_repo
    if body.mode not in ("after", "before"):
        raise HTTPException(400, "mode must be 'after' or 'before'")
    try:
        out = await chat_branches.fork(thread_id, body.anchor, body.mode)
    except chat_repo.MessageNotFound as e:
        raise HTTPException(404, str(e))
    except chat_repo.BranchError as e:
        raise HTTPException(409, str(e))
    except tenancy.TenantUnresolved as e:
        raise HTTPException(503, str(e))
    chat_events.bump(thread_id)
    return out


@router.post("/threads/{thread_id}/branches/{branch_id}")
async def switch_branch(thread_id: str, branch_id: str):
    """Make another branch the visible one. Reversible, and nothing moves but
    one pointer, which is why it does not ask for confirmation."""
    from db import chat_repo
    try:
        out = await chat_branches.switch(thread_id, branch_id)
    except chat_repo.BranchError as e:
        raise HTTPException(404, str(e))
    except tenancy.TenantUnresolved as e:
        raise HTTPException(503, str(e))
    chat_events.bump(thread_id)
    return out


@router.patch("/threads/{thread_id}/branches/{branch_id}")
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


@router.delete("/threads/{thread_id}")
async def remove(thread_id: str):
    # The queue goes with the conversation it belongs to. Left behind it would
    # be text nothing can ever deliver, held forever under an id with no thread.
    chat_queue.forget(thread_id)
    chat_events.forget(thread_id)
    t = chat_store.get_thread(thread_id)
    draft_store.clear_for_thread(t.get("context_key") if t else None, thread_id)
    return {"deleted": chat_store.delete_thread(thread_id)}


@router.post("/threads/{thread_id}/card-state")
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
    "guessed at or asked about, and both cost a run.\n"
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


@router.get("/threads/{thread_id}/ticket-drafts")
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


@router.post("/threads/{thread_id}/ticket-drafts")
async def make_ticket_drafts(thread_id: str):
    """Scan the conversation and file 0..N proposals as draft tickets.

    Zero is a normal answer. A conversation is not one ticket, and most of what
    is said in one is discussion, reversal, or work already done. Filing them as
    drafts is safe: `next_ready` never picks a draft and the run endpoint refuses
    one, so proposing can never start work."""
    scope_key, root = _drafts_scope(thread_id)
    try:
        return await ticket_drafts.propose(thread_id, root, scope_key)
    except KeyError:
        raise HTTPException(404, "thread not found")
    except Exception as e:
        raise HTTPException(502, f"could not draft tickets: {e}")


@router.patch("/threads/{thread_id}/ticket-drafts/{task_id}")
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


@router.delete("/threads/{thread_id}/ticket-drafts/{task_id}")
async def delete_ticket_draft(thread_id: str, task_id: str):
    """Discard a proposal. `cancelled`, not deleted: no ticket is ever deleted by
    any path, so a rejected proposal stays auditable and cannot be re-proposed
    into the same slot by the next scan."""
    scope_key, root = _drafts_scope(thread_id)
    if tasks.update(root, task_id, status="cancelled") is None:
        raise HTTPException(404, f"draft not found: {task_id}")
    return ticket_drafts.get(thread_id, root, scope_key)


@router.get("/threads/{thread_id}/ticket-scope")
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


@router.post("/threads/{thread_id}/spawn-task")
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

    # Resolve a canonical scope KEY (workshop / project:<slug> / agent:<slug> /
    # build:<slug>) so the same key both files the task AND drives the run
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
    # Cross-chat dedup (Stage 7 / 2e): fold a duplicate into the existing task
    # (merge), link a near-match as related, or create fresh. The decision is
    # returned so the chat can tell the operator what happened.
    from services import task_dedup
    result = task_dedup.dedup_add(
        root, text=text, acceptance=acceptance,
        priority=body.priority or 0,
        effort=(body.effort or "").strip() or None,
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
    )
    resp = {
        "task": result["task"],
        "scope": scope_key,
        "thread_id": thread_id,
        "decision": result["decision"],
        "matched_id": result.get("matched_id"),
        "cosine": result.get("cosine"),
        "message": result["message"],
    }

    # File AND run: launch a worker now for a freshly filed task. Never run a
    # merge/exact (it folded into an existing task; running that would be a
    # surprise re-run of unrelated work). C12 (2026-07-18): the server used
    # to append a second `job-status` ui-card message after the run started,
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
                # The thread is the run's origin. Recording it here is what
                # makes the run navigable back to the conversation that asked
                # for it; it was the only linkage field never written.
                run_res = launch_task_run(scope_key, task_id, chat_id=thread_id)
            except HTTPException as e:
                resp["run_error"] = e.detail
            else:
                resp["job_id"] = run_res["job_id"]
    return resp


@router.post("/threads/{thread_id}/mode")
async def set_chat_mode(thread_id: str, body: SetChatMode):
    """Switch a chat between Direct and Orchestrated.

    Cheap and reversible, so it commits on change rather than behind a Save. The
    mode is re-stated to the model on every turn (chat_context.chat_mode_block),
    which is what lets a switch take effect mid-conversation instead of only on
    a fresh session."""
    mode = (body.mode or "").strip().lower()
    if mode not in chat_context.CHAT_MODES:
        raise HTTPException(400, f"mode must be one of {list(chat_context.CHAT_MODES)}")
    t = chat_store.set_thread_mode(thread_id, mode)
    if t is None:
        raise HTTPException(404, "thread not found")
    return {"id": thread_id, "mode": t.get("mode")}


@router.post("/threads/{thread_id}/rescope")
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
    # provider for any project:/build: key and returns the matching-key provider
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
        "last_message": (msgs[-1]["content"][:120] if msgs else ""),
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
) -> str:
    # Layered prompt (see docs 01 §3):
    #   1. ROLE PREAMBLE (platform-owned safety/capability facts) — first, and
    #      never overridden by anything below it. Computed from the real run
    #      config in the caller and passed in, so it can't drift from reality.
    #   2. App/project context (persona + live snapshot).
    #   3. User-owned custom prompt (provider.custom_prompt(); default "").
    #   4. The conversation.
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    # Build providers get the templated runtime briefing right after the platform
    # preamble: cwd, stack, app services/ports, how to run+see the app, edit
    # scope, negative capabilities. Empty for data providers.
    briefing = chat_context.runtime_briefing(provider)
    if briefing:
        parts.append(briefing)
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

    Two homes, because build agents predate per-thread sessions: an agent chat's
    session hangs off the AGENT record (one session per agent), everything else
    off the thread. Checking only the thread would silently skip the native
    compact for exactly the chats that need it most."""
    if isinstance(provider, chat_context.BuildAgentContextProvider):
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
    if isinstance(provider, chat_context.BuildAgentContextProvider):
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


@router.post("/threads/{thread_id}/compact")
async def compact_thread(thread_id: str):
    """Reclaim the LIVE window: drop the session, keep the transcript.

    THE CLI CANNOT DO THIS FOR US, which is the correction this endpoint exists
    to carry. `/compact` was sent as a raw turn for the CLI's own command to
    run, and in headless mode (`-p --input-format stream-json`) a leading slash
    is parsed into a local-command block that nothing executes. The turn at
    2026-08-12T05:10:18Z came back in two seconds and the CLI session recorded
    the whole of it as

        user    <command-name>/compact</command-name>
        system  local_command
                <local-command-caveat> ... DO NOT respond to these messages

    with no `compact_boundary` behind it, no summarization, and a window exactly
    as full as before. (A real one, in a terminal the same night, took 136
    seconds and reported 492981 -> 14880 tokens.) There is no control request
    for it either: the protocol's entire list is initialize, set_model,
    rename_session, read_file, interrupt, set_permission_mode,
    set_max_thinking_tokens, mcp_oauth_callback_url, file_suggestions,
    get_usage, set_color. Compaction is not in it. The chat reported "Command
    ran." over that for months.

    DETACHING IS HALF THE ANSWER, and on its own it is not compaction. The next
    turn mints a fresh session and re-assembles the prompt, so the CLI's private
    copy of the conversation goes. But the prompt it re-assembles is the whole
    transcript, so a long chat comes straight back to where it was. Measured on
    2026-08-12: the cut was recorded, the meter subtracted it, and the prompt
    still carried every message from before it, because `_assemble_full_prompt`
    never applied the cut at all. Both this and /clear were bookkeeping.

    So this folds, and the fold is the point:

      1. everything except the last `_COMPACT_KEEP_TAIL` messages is summarized
         into a running summary stored on the thread;
      2. the cut moves to that point, so the prompt carries `summary + tail`;
      3. the live session is detached, so the next turn actually uses it.

    NOTHING IS DELETED. The log is append-only and untouched: the operator's
    transcript still shows every message, and the summary stands in only for
    what the MODEL is handed. A `boundary` event records the fold, so the
    divider in the transcript is read rather than guessed and says the same
    thing on every device.

    IT REPORTS WHAT HAPPENED, including nothing. The summarizer is a model call
    and it can fail; when it does, this says so and changes nothing, rather than
    answering 200 over a fold that did not happen. That failure is not
    hypothetical: this call passed its whole prompt as an argv element until
    today, so it died with E2BIG on exactly the threads long enough to need
    compacting, the error was swallowed as best-effort, and the chat said
    "Compacted. Earlier messages are now a summary." over it."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    # ONE OPERATION AT A TIME, and admission respects it: from here until the
    # fold lands (or is handed to the landing task), a new message on this
    # thread queues instead of becoming a turn against the pre-fold prompt.
    # That is the 2026-08-14 incident: compact pressed, a message sent during
    # the ~30s summarize started a turn carrying the whole 467k transcript, and
    # the boundary then landed mid-turn. See services/thread_ops.
    if not thread_ops.begin(thread_id, "compact"):
        # REFUSED is not DEFERRED: nothing was computed and nothing will land
        # from THIS request. Saying "deferred" here made the client promise a
        # landing that was never going to happen.
        return {"folded": 0, "session_reset": False, "busy": True,
                "error": "", "summary_chars": 0}
    handed_off = False
    try:
        fold_n, summary, folded_through, error = await _fold_summary(thread_id, t)
        if error or fold_n <= 0:
            # Nothing to land. Detaching alone is still useful (it is what
            # /clear's session half does) and touches no ordering, so it
            # happens inline whatever the thread is doing.
            session_reset = _detach_live_session(thread_id, t, provider)
            return {"folded": 0, "session_reset": session_reset,
                    "error": error or "", "summary_chars": 0}
        rid = chat_store.active_run_id(thread_id)
        if rid and build_streaming.run_is_live(rid):
            # A reply is being written. The fold's WRITES wait for it: landing
            # a boundary mid-turn is how a reply ends up on the far side of the
            # divider from the messages it answers. The summary is computed
            # from a count-from-the-front, so it names the same messages
            # however long the wait runs. The task is HELD (thread_ops), and
            # handed_off flips only once it exists: a create_task that raised
            # would otherwise skip end() and hold the door forever.
            thread_ops.land_in_background(
                thread_id, _land_fold_when_quiet(thread_id, summary, folded_through))
            handed_off = True
            return {"folded": fold_n, "session_reset": False, "deferred": True,
                    "error": "", "summary_chars": len(summary)}
        session_reset = _land_fold(thread_id, summary, folded_through)
        return {"folded": fold_n, "session_reset": session_reset,
                "error": "", "summary_chars": len(summary)}
    finally:
        if not handed_off:
            thread_ops.end(thread_id)


# How many messages stay verbatim. The tail is what the conversation is ABOUT
# right now, and summarizing it is what makes a compacted chat feel like it has
# had a stroke: it can describe what you were doing but not continue it.
_COMPACT_KEEP_TAIL = 6


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


def _summarize_sync(prev_summary: str, to_fold: list[dict]) -> str:
    """One summarization call. Text in, text out, no tools.

    `tools_disabled` matters: without it this runs in the CLI's default agentic
    mode, which spins up every MCP server just to write a paragraph and takes
    minutes, which is what made /compact look hung."""
    prompt = (
        "You are compacting a chat transcript to reclaim context. Write a running "
        "summary that preserves decisions made, facts established, the operator's "
        "stated preferences, file paths and identifiers, and anything unfinished. "
        "Keep what a reader would need to continue the conversation without the "
        "original. Drop pleasantries and repetition. Write plainly, no em-dashes, "
        "no emojis.\n\n"
        + (f"=== SUMMARY SO FAR ===\n{prev_summary}\n\n" if prev_summary else "")
        + f"=== MESSAGES TO FOLD IN ===\n{_fmt_for_summary(to_fold)}\n\n"
        "=== OUTPUT ===\nThe updated summary only, no preamble."
    )
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU,
                               tools_disabled=True, timeout_s=180)
    return (res.get("text") or "").strip()


async def _fold_summary(thread_id: str, t: dict) -> tuple[int, str, int, str]:
    """Summarize everything but the tail, WITHOUT writing anything.

    Returns (messages folded, summary, folded_through, error). `(0, ...)` with
    an empty error means there was nothing to fold; with an error it means the
    fold was attempted and failed. The caller reports both; neither is silent.

    Compute and landing are split on purpose: this half takes tens of seconds
    (a model call) and holds no ordering guarantees, so it runs while the
    thread lives its life; the landing half (`_land_fold`) is a handful of
    writes and runs only on a quiet thread. The cut is a COUNT FROM THE FRONT
    and appends land at the back, so the count still names the same messages
    however many arrive between the two halves.
    """
    msgs = [m for m in (t.get("messages") or [])
            if not (m.get("meta") or {}).get("streaming")]
    ct = int(t.get("cleared_through", 0) or 0)
    tail = msgs[ct:] if 0 < ct <= len(msgs) else msgs
    fold_n = max(0, len(tail) - _COMPACT_KEEP_TAIL)
    if fold_n <= 0:
        return 0, "", 0, ""
    to_fold = tail[:fold_n]
    try:
        summary = await asyncio.to_thread(
            _summarize_sync, (t.get("summary") or "").strip(), to_fold)
    except Exception as e:
        log.warning("chat compaction failed for %s: %s", thread_id, e)
        return 0, "", 0, f"the summarizer failed: {e}"
    # STRIPPED HERE, not trusted from the call. A whitespace-only answer is a
    # failed summary, and storing one would move the cut and leave the model
    # with neither the messages nor anything standing in for them.
    summary = (summary or "").strip()
    if not summary:
        return 0, "", 0, "the summarizer returned nothing"
    return fold_n, summary, ct + fold_n, ""


def _land_fold(thread_id: str, summary: str, folded_through: int) -> bool:
    """The fold's writes: summary + cut + boundary, then the session detach.
    A handful of synchronous calls; the caller guarantees the thread is quiet."""
    chat_store.set_summary(thread_id, summary, folded_through=folded_through)
    t = chat_store.get_thread(thread_id)
    if t is None:
        return False
    provider = chat_context.get_provider(t.get("context_key"))
    return _detach_live_session(thread_id, t, provider)


async def _land_fold_when_quiet(thread_id: str, summary: str, folded_through: int) -> None:
    """Background landing for a fold computed while a run was live. Owns the
    thread_ops registration it inherited; messages queued behind the operation
    drain when `end` fires."""
    try:
        await thread_ops.wait_quiet(thread_id)
        _land_fold(thread_id, summary, folded_through)
        log.info("compact: deferred fold landed on %s", thread_id)
    except Exception as e:
        log.warning("compact: deferred fold failed for %s: %s", thread_id, e)
    finally:
        thread_ops.end(thread_id)


@router.post("/threads/{thread_id}/clear")
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
        rid = chat_store.active_run_id(thread_id)
        if rid and build_streaming.run_is_live(rid):
            thread_ops.land_in_background(thread_id, _land_clear_when_quiet(thread_id))
            handed_off = True
            return {"cleared": True, "cleared_through": 0,
                    "session_reset": False, "deferred": True}
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
    time, never from a snapshot taken before the wait."""
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


# Context window per model. This is the DENOMINATOR of the composer meter, and
# it was wrong for every Claude model except Haiku: the table said 200k across
# the board, but the whole 4.6-and-later generation is 1M. A real chat holding
# 310,931 tokens therefore computed 155% and clamped to a permanent, confident
# 100% — the meter read "full" on a chat using under a third of its window.
#
# Haiku 4.5 is the genuine 200k exception; do not "simplify" this table back to
# one number.
_CONTEXT_WINDOW = {
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-fable-5": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
# Smallest-to-largest, for the self-correction in `_window_for`.
_WINDOW_TIERS = (200_000, 1_000_000)


def _window_for(model: str | None, measured: int = 0) -> int:
    """The model's context window, widened to fit what we've actually measured.

    A hardcoded table goes stale every time a model ships, and the failure is
    silent and one-directional: too small a window pins the meter at 100% and
    the operator has no way to tell a wrong denominator from a genuinely full
    chat. So the measurement gets a vote. If the model reports more context in
    use than the table allows, the table is the thing that's wrong — step up to
    the smallest tier that actually fits rather than rendering a confident lie.
    """
    if model and model in _CONTEXT_WINDOW:
        window = _CONTEXT_WINDOW[model]
    elif model and model.startswith("claude"):
        # An unrecognized Claude model is far more likely to be newer than
        # older, and every current family except Haiku is 1M.
        window = 1_000_000
    elif model:
        window = 32_768  # local / Ollama
    else:
        window = 1_000_000
    if measured > window:
        for tier in _WINDOW_TIERS:
            if tier > measured:
                return tier
        return measured  # past every known tier — report honestly, never clamp
    return window


@router.get("/threads/{thread_id}/context")
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
                           summary=(t.get("summary") or ""))
    used = max(1, round(len(prompt) / 4))
    return {
        "used_tokens": used,
        "window_tokens": window,
        "pct": min(100, round(used / window * 100)),
        "model": model,
        "source": "estimated",
    }


def _side_query_cwd(provider) -> str:
    """The directory a side query should run the CLI in.

    A build chat's session belongs to its project directory; everything else
    lives at the workshop root. Shared by the two side queries below so a
    `/context` reading and an MCP listing can never describe different places.
    """
    return (str(provider.dir) if chat_context.is_build_provider(provider)
            else str(context_report.WORKSHOP_ROOT))


@router.get("/threads/{thread_id}/context/detail")
async def thread_context_detail(thread_id: str, refresh: bool = False):
    """What is actually filling this chat's window, broken down.

    Powers the context modal. A SIDE QUERY, not a turn: it claims no run, writes
    no run event, and appends no message, because opening a panel is not
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


@router.get("/threads/{thread_id}/mcp/health")
async def thread_mcp_health(thread_id: str, refresh: bool = False):
    """Which MCP servers are configured for this chat's directory, and whether
    they connect.

    The same SIDE QUERY contract as `/context/detail` and for the same reasons:
    it claims no run, emits no run event, and appends no message. It runs
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


@router.put("/threads/{thread_id}/mcp/servers")
async def set_thread_mcp_servers(thread_id: str, body: McpSelectionIn):
    """Choose which MCP servers this chat's turns may reach.

    A CONFIG WRITE, which is why the UI puts it behind Edit mode and an explicit
    Save rather than committing on each toggle (frontend/CLAUDE.md rule 7): it
    changes what the next turn can do.

    The selection is stored against the thread exactly as the model, effort and
    permission mode are, and is read at spawn time — so it applies from the next
    turn, not to a run already in flight, whose argv was fixed when its process
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

    Distinct from `provider.role`, which is the provider CLASS
    (assistant/builder) and is a different axis entirely. Only an agent-backed
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
    (streaming) so the two never fork. Returns (prompt, is_build_mode); persists
    nothing. Build providers on a resumed session use a runtime briefing instead
    of this full assembly (the caller handles that); this is the first-turn /
    stateless assembly.

    Capability facts come from the SAME branch that decides the run config
    (capability_for mirrors the build/narrow/tools-off ladder), so the role
    preamble can never claim tools the run does not actually have."""
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
                           summary=summary)
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
    # handed to run R carries `during_run = R`, and the run that just ended is
    # the one whose arrivals this turn has not answered. Reading the tail after
    # the last assistant message (what this used to do) stopped working the
    # moment the reply started being appended when it FINISHED, because a
    # delivered message then sits above the reply rather than below it.
    arrivals = chat_context.arrival_notice(
        fresh.get("messages") or [], run_id=chat_store.previous_run_id(tid))
    if arrivals:
        parts.append(arrivals)
    # Record what this thread has now been told, AFTER the prompt is built: a
    # mark written before the turn would suppress the next turn's block even if
    # this one never reached the model.
    if new_marks:
        chat_store.set_turn_marks(t["id"], **new_marks)
    return "\n\n".join(parts)


@router.post("/threads/{thread_id}/messages")
async def send_message(thread_id: str, body: PostMessage):
    """Send a message and BLOCK until the reply is committed. Voice and scripts.

    A thin wrapper over the same run pipeline the streaming path uses: start a
    run (or join the one in flight), await its finalize, return the committed
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
        # The run finished and was reaped between starting it and looking it up.
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
            out.append({
                "text": (p.text or "").strip() or content,
                "client_id": p.client_id or (client_id if i == 0 else None),
                "ts": p.sent_at,
                # Recorded on the first part, exactly as the log records them.
                "attachments": list(attachments or []) if i == 0 else [],
            })
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
        if attachments:
            meta["attachments"] = attachments
        chat_store.append_message_during_turn(
            thread_id, "user", (p0.text or "").strip() or content, meta=meta,
            ts=p0.sent_at, client_id=p0.client_id or client_id)
        return
    if not parts or len(parts) < 2:
        meta = {"attachments": attachments} if attachments else None
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
        if attachments and added == 0:
            meta["attachments"] = attachments
        chat_store.append_message_during_turn(
            thread_id, "user", text, meta=meta, ts=p.sent_at,
            client_id=p.client_id)
        added += 1
    if not added:
        meta = {"attachments": attachments} if attachments else None
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
                                    content: str, model: str, run_id: str,
                                    effort: str | None = None,
                                    permission_mode: str | None = None,
                                    attachments: list[dict] | None = None,
                                    sends: list[dict] | None = None):
    """Run one turn AS the mentioned agent, inside the chat.

    The agent answers with its own definition, its own memory scope and its own
    workspace — it is the agent talking, not the assistant doing an impression of
    it. Deliberately stateless per mention: an agent joins a conversation to
    answer a question, and carrying a resumable session per (agent, chat) pair
    would quietly create a second, invisible conversation alongside this one.
    What it needs from the chat, it is given here.

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
    # run nobody is watching. Passed explicitly rather than defaulted so the
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
            speaker=slug, run_id=run_id, effort=effort,
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
        speaker=slug, run_id=run_id, effort=effort,
        permission_mode=permission_mode, attachments=attachments, origin=sends,
    )
    _ = kscope  # the CHAT's scope; the agent reads its own layers above
    return turn


# ---- Attachments ------------------------------------------------------------
# Upload is its OWN request, not part of the turn: the file is on the server
# before Send is pressed, so a 12MB screenshot is not spent while the operator
# waits for a reply, and the chip can say "ready" or "failed" while there is
# still something to do about it. The turn then carries only ids.

@router.post("/attachments")
async def upload_attachment(file: UploadFile, scope: str = Form(""),
                            thread_id: str = Form("")):
    data = await file.read()
    kind = chat_attachments.kind_for(file.content_type or "", file.filename or "")
    if kind is None:
        raise HTTPException(415, f"{file.filename or 'that file'} is not a type a chat can read")
    try:
        rec = chat_attachments.save(data, file.filename or "file", kind,
                                    scope=scope, thread_id=thread_id)
    except ValueError as e:
        raise HTTPException(413 if "larger than" in str(e) else 400, str(e))
    return {"id": rec["id"], "name": rec["name"], "kind": rec["kind"],
            "size": rec["size"], "created_at": rec["created_at"]}


@router.get("/attachments")
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
    } for r in recs]
    scopes = sorted({r["scope"] for r in out if r["scope"]})
    return {"files": out, "count": len(out), "scopes": scopes}


@router.delete("/attachments/{att_id}")
async def delete_attachment(att_id: str):
    if not chat_attachments.delete(att_id):
        raise HTTPException(404, "attachment not found")
    return {"deleted": att_id}


@router.get("/attachments/{att_id}")
async def get_attachment(att_id: str):
    """The stored bytes, for the composer's thumbnail and the transcript's.

    Inline rather than an attachment disposition: this is a preview of something
    the operator just attached, not a download they asked for. The name is
    re-derived from the record, never from the URL."""
    rec = chat_attachments.get(att_id)
    path = chat_attachments.blob_for(rec) if rec else None
    if not rec or not path:
        raise HTTPException(404, "attachment not found")
    media = rec.get("media_type") or "application/octet-stream"
    if rec.get("kind") == "text":
        media = "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media,
                        headers={"Content-Disposition":
                                 f'inline; filename="{rec["name"]}"'})


@router.get("/threads/{thread_id}/mentionable")
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


async def _run_for_message(thread_id: str, t: dict, content: str,
                           model: str | None, parts: list[TurnPart] | None,
                           *, take_over: str | None = None,
                           already_recorded: bool = False,
                           client_id: str | None = None,
                           effort_in: str | None = None,
                           mode_in: str | None = None,
                           attachments: list[dict] | None = None) -> "build_streaming.Turn":
    """Claim a run for this message and start it. THE send path.

    The claim is taken FIRST, before a single await, and held until the
    subprocess is spawned. Prompt assembly awaits (knowledge search, note
    retrieval, a summarizer), and every one of those awaits used to be a window
    a second POST could start a rival turn through — observed live: one thread
    with two turns streaming at once and the operator's message reaching
    neither. Now the second POST takes the thread's write lock, sees
    `active_run_id`, and joins.

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

    run_id = build_streaming.new_run_id()
    joined = await build_streaming.claim_run(
        thread_id, run_id, model=model, agent=speaker, force=bool(take_over),
        accepts_input=not _is_raw_command(content),
        # The claim is taken now; the LOG MARKER waits until the message that
        # caused this run is recorded, just below. Otherwise the log says a run
        # began before the message that began it. See chat_store.mark_run_started.
        announce=False)
    if joined is not None:
        raise build_streaming.RunInFlight(joined)

    try:
        # THE PROMPT'S SNAPSHOT AND ITS FLOOR, one synchronous stretch. The
        # thread is re-read HERE, after the claim, so the prompt is assembled
        # from everything filed up to this instant instead of from the caller's
        # snapshot (taken before its own awaits; a message filed against this
        # claimed run in that gap used to be in the log, absent from the
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
            # ALREADY in the log: a delivery that was refused mid-run
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
        chat_store.mark_run_started(thread_id, run_id)
        _turn_obj = build_streaming.get_turn(run_id)
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
                model=model, run_id=run_id, effort=effort,
                permission_mode=permission_mode, attachments=attachments,
                sends=sends)
        return await _start_assistant_turn(
            thread_id=thread_id, t=t, provider=provider, content=content,
            model=model, run_id=run_id, effort=effort,
            permission_mode=permission_mode, attachments=attachments,
            sends=sends)
    except Exception:
        # The claim outlives nothing. Give the thread back so the next message is
        # not told a run is in flight that never spawned.
        chat_store.release_run(thread_id, run_id)
        raise


async def _start_assistant_turn(*, thread_id: str, t: dict, provider, content: str,
                                model: str, run_id: str, effort: str | None = None,
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
    # docs/security-audit-2026-08-11-scope-separation.md §4.
    if mcp_selection is None and not ownership.caller_is_operator():
        mcp_selection = []

    if isinstance(provider, chat_context.BuildAgentContextProvider):
        from services import build_agents as _ba
        try:
            sid, is_new = _ba.ensure_session_id(provider.slug)
        except KeyError:
            sid, is_new = None, True
        # Stale-session guard: an existing id whose claude session no longer exists
        # on disk (old agent, post-reboot prune) would make `--resume` fail the
        # whole turn. Detect it up front and start fresh so the turn just works.
        if sid and not is_new and not build_streaming.session_alive(sid, str(provider.dir)):
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
            prompt = (briefing + "\n\n=== OPERATOR MESSAGE ===\n" + content) if briefing else content
            resume_flag = True
        else:
            prompt, _ = await _assemble_full_prompt(t, provider, content)
            resume_flag = False
        _link_build_skills(provider)  # native .claude/skills discovery (Phase 5)
        return await build_streaming.start_turn(
            thread_id=thread_id, agent_slug=provider.slug, prompt=prompt, model=model,
            cwd=str(provider.dir), session_id=sid, resume=resume_flag,
            timeout_s=CHAT_TIMEOUT_S, run_id=run_id, effort=effort,
            permission_mode=permission_mode, mcp_selection=mcp_selection,
            origin=sends,
        )

    is_project = chat_context.is_build_provider(provider)
    if is_project:
        _link_build_skills(provider)
        cwd = str(provider.dir)
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
        timeout_s=CHAT_TIMEOUT_S, run_id=run_id, effort=effort,
        permission_mode=permission_mode, attachments=attachments,
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
    """Send one message into a chat: deliver it to the run in flight, or start
    one for it. Returns `{turn_id, injected, at_segment}`.

    ---- ONE RUN PER THREAD -------------------------------------------------
    If a run is already going here, this message JOINS it instead of starting a
    rival. The decision belongs on the server: a browser only knows whether ITS
    OWN stream is open, so a second tab, a dropped SSE, or a reload each believe
    the chat is idle and cheerfully start another turn.

    Joining is also what the operator actually wants from a message typed during
    a turn: delivered now, at the running turn's next step boundary, rather than
    queued behind everything it is doing.

    ---- PERSIST, THEN DELIVER ----------------------------------------------
    The message is written to the log before anything is written to the run's
    input channel, so the transcript can never be behind what the model read. If
    delivery then fails, the message is already recorded and a fresh run is
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

    # Effort is a per-thread preference, so it is recorded HERE, before the
    # join-or-start fork. A message that joins a run in flight cannot change that
    # run's effort — the process is spawned and --effort is fixed at spawn — but
    # the level the operator just picked still has to survive to the next turn,
    # or the picker and the thread disagree the moment the run ends.
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
    run_id = _live_run_id(thread_id)
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
    if not run_id and (chat_queue.read(thread_id) or thread_ops.pending(thread_id)):
        st = chat_queue.enqueue(
            thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
            ts=_first_part_ts(parts) or sent_at, attachments=attachments,
            model=model_in, effort=effort_in, permission_mode=mode_in)
        chat_drain.drain_soon(thread_id, reason="joined the waiting queue")
        return {"turn_id": None, "injected": False, "queued": True,
                "at_segment": None, "queue": st}
    if run_id and (not build_streaming.run_accepts_input(run_id)
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
        # visible from every device, and `chat_drain` sends it when this run
        # ends whether or not anything is watching. See services/chat_queue.
        st = chat_queue.enqueue(
            thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
            ts=_first_part_ts(parts) or sent_at, attachments=attachments,
            model=model_in, effort=effort_in, permission_mode=mode_in)
        # The run may have finished between the check above and the write. The
        # drain is idempotent and refuses when a run is live, so asking costs
        # nothing and not asking would strand the message until the next turn.
        chat_drain.drain_soon(thread_id, reason="enqueued behind a command turn")
        return {"turn_id": run_id, "injected": False, "queued": True,
                "at_segment": None, "queue": st}
    if run_id:
        at_segment, msg_seq = _file_during_run(thread_id, run_id, content,
                                               client_id=client_id,
                                               attachments=attachments)
        if build_streaming.inject(run_id, content, attachments,
                                  client_id=client_id, sent_at=sent_at):
            return {"turn_id": run_id, "injected": True, "at_segment": at_segment}
        if not build_streaming.run_is_ending(run_id):
            # Claimed but not spawned yet. Whether its prompt will carry this
            # message is a COMPARISON, not a hope: the prompt's history floor is
            # stamped in the same synchronous stretch that assembles it
            # (_run_for_message), so a floor at or above this seq means the
            # message is in the prompt, and an older floor means it provably is
            # not. The unstamped case means the floor stretch has not run yet,
            # and the fresh read it starts with will include this message.
            _t_obj = build_streaming.get_turn(run_id)
            _floor = _t_obj.prompt_through_seq if _t_obj is not None else None
            if _floor is not None and msg_seq > _floor:
                # In the log, not in the prompt, and the input channel is not
                # up: nothing would ever deliver it. Queue it (marked recorded,
                # so the drain does not mistake log-presence for answered) and
                # it goes out when this run ends.
                st = chat_queue.enqueue(
                    thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
                    ts=_first_part_ts(parts) or sent_at, attachments=attachments,
                    model=model_in, effort=effort_in, permission_mode=mode_in,
                    recorded=True)
                chat_drain.drain_soon(thread_id, reason="missed a spawning run's prompt")
                return {"turn_id": run_id, "injected": False, "queued": True,
                        "at_segment": at_segment, "queue": st}
            return {"turn_id": run_id, "injected": False, "at_segment": at_segment}
        # It has spawned and its input channel is gone: it is finishing and will
        # never read this. The message is already in the log. If messages are
        # ALREADY WAITING (the finalize window requeues undelivered ones),
        # taking the thread over now would answer this message before them and
        # bake that order into the transcript; join the back of the queue
        # instead. A PENDING OPERATION queues it for the same reason as the
        # idle branch above: without this check, a send landing in the finalize
        # window of the run a fold is waiting out started a fresh turn on the
        # pre-fold prompt, which is the incident itself through a side door.
        # Only an empty queue with no pending operation takes the thread over.
        if chat_queue.read(thread_id) or thread_ops.pending(thread_id):
            st = chat_queue.enqueue(
                thread_id, text=content, client_id=client_id or chat_log.new_ulid(),
                ts=_first_part_ts(parts) or sent_at, attachments=attachments,
                model=model_in, effort=effort_in, permission_mode=mode_in,
                recorded=True)
            chat_drain.drain_soon(thread_id, reason="joined behind requeued messages")
            return {"turn_id": run_id, "injected": False, "queued": True,
                    "at_segment": at_segment, "queue": st}
        log.info("thread %s: run %s is finishing; starting a fresh run for this message",
                 thread_id, run_id)
        take_over, already_recorded = run_id, True

    try:
        turn = await _run_for_message(thread_id, t, content, model, parts,
                                      take_over=take_over,
                                      already_recorded=already_recorded,
                                      client_id=client_id,
                                      effort_in=effort_in,
                                      mode_in=mode_in,
                                      attachments=attachments)
    except build_streaming.RunInFlight as e:
        # Another POST won the same event-loop tick. Join its run rather than
        # starting a rival, which is the whole reason the claim is atomic.
        at_segment = (_file_during_run(thread_id, e.run_id, content, client_id=client_id,
                                       attachments=attachments)[0]
                      if not already_recorded else _at_segment(e.run_id, thread_id))
        build_streaming.inject(e.run_id, content, attachments,
                               client_id=client_id, sent_at=sent_at)
        return {"turn_id": e.run_id, "injected": True, "at_segment": at_segment}
    return {"turn_id": turn.id, "injected": False, "at_segment": None}


async def _start_queued_batch(thread_id: str, batch: list[dict]) -> bool:
    """Turn a drained batch into a turn. Registered with `chat_drain`.

    This is the one place the queue re-enters the send path, and it goes in at
    the same door a live message does: the batch becomes `parts`, so every
    entry is recorded as its own message at its own send time (see
    `_record_user_turn`), under the client id it was queued with — which is what
    lets the browser reconcile the bubble it has been showing all along instead
    of drawing a second one.

    Returns False rather than raising when the thread is busy: a run claimed
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
                      client_id=e.get("client_id"))
             for e in batch]
    content = chat_queue.joined_text(batch)
    if not content:
        return False
    atts = chat_queue.attachments_of(batch)
    try:
        await _run_for_message(
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
    except build_streaming.RunInFlight:
        # Something claimed the thread first. The batch stays queued and the
        # drain runs again when that run ends.
        return False
    return True


chat_drain.register_starter(_start_queued_batch)


@router.post("/threads/{thread_id}/queued/{client_id}/cancel")
async def cancel_queued_message(thread_id: str, client_id: str):
    """Take a queued message back out.

    It is the operator's text and they must be able to unsend it before it goes.
    This is the only way anything leaves the queue undelivered, and it is a
    deliberate act by the person who wrote it."""
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")
    return chat_queue.drop(thread_id, [client_id])


@router.post("/threads/{thread_id}/queued/release")
async def release_queued_messages(thread_id: str):
    """Send what is waiting: clears a Stop hold or a failed drain's diagnosis.

    The Retry beside a queue that could not be sent, and the "send these now"
    after a Stop, are the same action, so they are one endpoint."""
    if chat_store.get_thread(thread_id) is None:
        raise HTTPException(404, "thread not found")
    chat_queue.set_held(thread_id, False)
    await chat_drain.drain(thread_id, reason="operator released the queue")
    return chat_queue.state(thread_id)


@router.post("/threads/{thread_id}/turns")
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
    # on. Every branch below is an acceptance — running now, handed to the run in
    # flight, or queued — so the clear is unconditional and happens once, on the
    # server, under the key the operator was actually typing in.
    if body.draft_key:
        draft_store.clear_draft(body.draft_key)
    if out.get("queued"):
        # Taken and durable, just not running yet: the thread is busy with
        # something that cannot be spoken to. It is on the thread, every device
        # can see it, and the server sends it when this run ends.
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


@router.post("/threads/{thread_id}/turns/{turn_id}/permission")
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


def _live_run_id(thread_id: str) -> str | None:
    """The run currently going on this thread, or None. One field read.

    Server-derived, and now trivially so: `meta.active_run_id` names it and
    `build_streaming.run_is_live` says whether this process is still driving it.

    This used to scan the transcript backwards for an assistant message flagged
    `streaming`, which was fragile in exactly the way the rebuild removes. It
    first looked only at the last message, so the FIRST mid-turn message made the
    last message a user message and every message after it was told no turn was
    running — two turns ended up streaming into one thread at once. Scanning
    backwards fixed that symptom and left the flag itself orphanable by a
    restart. There is no flag any more, and nothing to orphan."""
    run_id = chat_store.active_run_id(thread_id)
    return run_id if build_streaming.run_is_live(run_id) else None


def _at_segment(run_id: str, thread_id: str) -> int:
    """How much of the running reply had streamed when a message landed.

    Ask the LIVE run, not the stored snapshot: the snapshot is written on a
    throttle, so it lags what is on screen, and the error is one-directional. A
    low `at_segment` renders the message EARLIER than it arrived, above output it
    never saw, which is what makes a reply look like an answer to a message it
    had not read. The snapshot remains the fallback for a run this process no
    longer holds."""
    live = build_streaming.get_turn(run_id)
    if live is not None:
        return len(live.segments)
    return len(_snapshot_segments(run_id, thread_id))


def _snapshot_segments(run_id: str, thread_id: str) -> list[dict]:
    run = chat_log.active_run(thread_id)
    snap = (run or {}).get("live") or {}
    return ((snap.get("meta") or {}).get("segments")) or []


def _cut_point(run_id: str, thread_id: str) -> tuple[int, int, str]:
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

    The snapshot branch is the degraded path, for a run this process does not
    hold. It cannot record a position (there is no live buffer to record it on),
    so it returns an empty token and the client falls back to the index."""
    live = build_streaming.get_turn(run_id)
    if live is not None:
        ref = chat_log.new_ulid()
        return len(live.segments), build_streaming.cut_here(live, ref), ref
    segs = _snapshot_segments(run_id, thread_id)
    return len(segs), build_streaming.unit_count(segs), ""


def _file_during_run(thread_id: str, run_id: str, content: str,
                     client_id: str | None = None,
                     attachments: list[dict] | None = None) -> int:
    """Persist a message arriving mid-run, BEFORE anyone tries to deliver it.

    Order matters and it used to be the other way round: the bytes went to the
    turn's stdin FIFO first and the store write happened after, inside a
    best-effort `except` that only logged. So a failed write meant the model had
    read a message the transcript never recorded, and the next fresh session
    re-assembled a history missing it.

    Persist first and the transcript can only ever be AHEAD of what the model
    saw, which is the recoverable direction. Returns `(at_segment, seq)`:
    `at_segment` is reported to the client, `seq` is what the caller compares
    against the run's prompt floor when delivery fails."""
    at_segment, at_unit, cut_id = _cut_point(run_id, thread_id)
    meta = {"injected": True, "during_run": run_id,
            "at_segment": at_segment, "at_unit": at_unit}
    if cut_id:
        meta["cut_id"] = cut_id
    if attachments:
        meta["attachments"] = attachments
    msg = chat_store.append_message_during_turn(
        thread_id, "user", content, meta=meta, client_id=client_id,
    )
    return at_segment, int(msg.get("seq") or 0)


@router.post("/threads/{thread_id}/inject")
async def inject_into_thread(thread_id: str, body: InjectMessage):
    """Hand a message to whatever run is going on this thread.

    The client should not have to know a run id to talk to its own chat. It knows
    which conversation it is in; the server resolves the rest.

    The message is PERSISTED before delivery is attempted, so `delivered: false`
    means "it is in the transcript and no run has it", never "it is gone"."""
    content = (body.content or "").strip()
    atts = [a.model_dump() for a in (body.attachments or [])]
    if not content and atts:
        content = chat_attachments.describe(atts)
    if not content:
        raise HTTPException(400, "empty message")
    if not chat_log.exists(thread_id):
        raise HTTPException(404, "thread not found")
    if _live_run_id(thread_id) is None:
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
    run_id = out["turn_id"]
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
        # it; false here means a fresh run was started for it instead.
        "delivered": True,
        "injected": delivered,
        "queued_for_turn": delivered,
        "turn_id": run_id,
        "at_segment": at_segment,
        "reason": "" if delivered else "the running turn refused input; started a new turn",
    }


@router.post("/turns/{turn_id}/stop")
async def stop_turn(turn_id: str):
    """Stop a running turn. What the composer's Stop button calls.

    STOP MEANS STOP. It used to mean "close this tab's socket": the turn kept
    running and kept spending, and the operator's only signal that it had worked
    was the indicator going quiet, which it did by lying. The indicator now reads
    the server's `active_run`, so the honest version of the old behaviour was the
    chat still saying "Still running" after Stop — which is what was reported.

    The run's process group is signalled and whatever it produced is committed as
    the reply, marked `meta.stopped`. Never an error: stopping a run that has
    already finished is a no-op that returns `stopped: false`, because "it is
    already stopped" is the outcome the caller wanted.

    STOP ALSO HOLDS THE QUEUE. Stopping ends the run, and the drain fires on
    every run ending — so without this, pressing Stop would immediately send
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
