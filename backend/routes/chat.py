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
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import chat_store, chat_context, chat_log
from services import task_scope
from services import scope_version
from services import agent_authority
from services import ticket_drafts
from services import knowledge
from services import skills
from services import llm_router
from services import build_streaming
from services import chat_naming
from services import draft_store

log = logging.getLogger("workshop.chat")
router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

# Default chat model — haiku is plenty fast and good enough for the chat surface.
# Budget chat opts up to sonnet via PROVIDER_MODELS when a per-app override is
# wanted (richer numeric reasoning); commute/location/finance stay on haiku to
# keep latency tight (operator's prior complaint: 180s timeouts on budget were
# really bad UX).
DEFAULT_CHAT_MODEL = llm_router.TIER_HAIKU
PROVIDER_MODELS: dict[str, str] = {
    "budget": llm_router.TIER_SONNET,  # plan-patch needs careful path generation
}
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
MAX_HISTORY = 60  # messages of transcript carried into a fresh-session prompt


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


class ParticipantIn(BaseModel):
    slug: str


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


@router.get("/providers")
async def providers(scope: str | None = None):
    return chat_context.list_providers(scope=scope)


@router.get("/threads")
async def threads(context: str | None = None):
    return chat_store.list_threads(context_key=context)


# ---- Composer drafts -------------------------------------------------------
# The unsent text in a chat composer, persisted per owner + context_key so a
# reload (or another device) doesn't lose it. The browser keeps the instant
# local copy; this is the durable cross-device mirror. See services/draft_store.
class DraftPut(BaseModel):
    text: str = ""
    ts: int = 0
    client_id: str = ""


@router.get("/drafts")
async def list_drafts():
    return draft_store.get_all()


@router.get("/drafts/{key:path}")
async def read_draft(key: str):
    return draft_store.get_draft(key)


@router.put("/drafts/{key:path}")
async def write_draft(key: str, body: DraftPut):
    return draft_store.put_draft(key, body.text, body.ts, body.client_id)


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
    default_mode: str


@router.get("/prefs")
async def get_chat_prefs():
    """Operator-level chat settings. One field so far: the mode a NEW chat starts
    in. Existing chats keep the mode they were created with."""
    return {"default_mode": chat_store.get_default_mode(), "modes": list(chat_context.CHAT_MODES)}


@router.post("/prefs")
async def set_chat_prefs(body: ChatPrefs):
    if body.default_mode not in chat_context.CHAT_MODES:
        raise HTTPException(400, f"default_mode must be one of {list(chat_context.CHAT_MODES)}")
    return {"default_mode": chat_store.set_default_mode(body.default_mode)}


@router.post("/threads/{thread_id}/participants")
async def add_participant(thread_id: str, body: ParticipantIn):
    """Add an agent to an existing chat as a participant (P1). Idempotent:
    already-present is a no-op. The slug is namespaced to `agent:<slug>` if
    bare; other participant kinds are not defined yet."""
    t = chat_store.add_thread_participant(thread_id, body.slug)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


@router.delete("/threads/{thread_id}/participants/{slug}")
async def remove_participant(thread_id: str, slug: str):
    """Remove an agent from an existing chat (P1). Idempotent: missing is a
    no-op. The `agent:` prefix is optional on the URL."""
    t = chat_store.remove_thread_participant(thread_id, slug)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


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
    return t


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

    `after` is a cursor, not an index. `after=0` is the whole log."""
    data = chat_log.read_thread(thread_id)
    if data is None:
        raise HTTPException(404, "thread not found")
    return {
        "events": chat_log.read_events(thread_id, after=max(0, after), limit=limit),
        "last_seq": data["last_seq"],
        "active_run": data["active_run"],
    }


@router.patch("/threads/{thread_id}")
async def rename(thread_id: str, body: RenameThread):
    t = chat_store.rename_thread(thread_id, body.title)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


@router.delete("/threads/{thread_id}")
async def remove(thread_id: str):
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


async def _distill_task_from_thread(t: dict) -> str:
    """Turn the recent conversation into ONE self-contained task a background
    worker can execute without seeing the chat. Cheap Haiku call; best-effort."""
    msgs = t.get("messages", [])[-12:]
    convo = "\n\n".join(
        f"{'Operator' if m.get('role') == 'user' else 'Assistant'}: {(m.get('content') or '').strip()}"
        for m in msgs if (m.get("content") or "").strip()
    )
    if not convo.strip():
        return ""
    prompt = (
        "From this conversation, write ONE self-contained background task: a crisp, "
        "actionable instruction a worker can execute WITHOUT seeing the chat. State the "
        "concrete goal and any specifics decided (files, values, acceptance criteria). "
        "Output ONLY the task text as one paragraph. No preamble, no heading, no "
        "em-dashes.\n\n=== CONVERSATION ===\n" + convo + "\n\n=== TASK ==="
    )
    try:
        res = await asyncio.to_thread(llm_router.call_cloud, prompt, model=llm_router.TIER_HAIKU)
        # The prompt asks for no em-dashes, but enforce it: the operator's hard rule
        # is no em-dashes anywhere, and Haiku emits them anyway. (review L7)
        text = (res.get("text") or "").strip().replace("—", ", ")
        return text[:2000]
    except Exception as e:
        log.warning("spawn-task distill failed: %s", e)
        return ""


class DraftPatch(BaseModel):
    title: str | None = None
    body: str | None = None
    acceptance: str | None = None


@router.get("/threads/{thread_id}/ticket-drafts")
async def get_ticket_drafts(thread_id: str):
    """Drafts already proposed for this chat. Never regenerates.

    Reopening the file-a-ticket dialog must show what was there, including any
    edits: proposing costs a model call and tens of seconds, and silently
    redoing it (or losing the operator's corrections) is the behaviour that
    makes people stop using a feature."""
    try:
        return ticket_drafts.get(thread_id)
    except KeyError:
        raise HTTPException(404, "thread not found")


@router.post("/threads/{thread_id}/ticket-drafts")
async def make_ticket_drafts(thread_id: str):
    """Read the conversation and propose 0..N tickets, then store them.

    Zero is a normal answer. A conversation is not one ticket, and most of what
    is said in one is discussion, reversal, or work already done."""
    try:
        return await ticket_drafts.propose(thread_id)
    except KeyError:
        raise HTTPException(404, "thread not found")
    except Exception as e:
        raise HTTPException(502, f"could not draft tickets: {e}")


@router.patch("/threads/{thread_id}/ticket-drafts/{draft_id}")
async def patch_ticket_draft(thread_id: str, draft_id: str, body: DraftPatch):
    try:
        return ticket_drafts.update_draft(
            thread_id, draft_id, body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "thread not found")


@router.delete("/threads/{thread_id}/ticket-drafts/{draft_id}")
async def delete_ticket_draft(thread_id: str, draft_id: str):
    try:
        return ticket_drafts.discard(thread_id, draft_id)
    except KeyError:
        raise HTTPException(404, "thread not found")


@router.get("/threads/{thread_id}/ticket-scope")
async def ticket_scope(thread_id: str):
    """Where this chat's tickets file, and whether that scope can run them."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    scope_key = t.get("context_key")
    if task_scope.root_for_context(scope_key) is None:
        scope_key = "workshop"
    from routes.tasks_all import scope_is_runnable
    return {"scope": scope_key, "runnable": scope_is_runnable(scope_key)}


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
                run_res = launch_task_run(scope_key, task_id)
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

    t["context_key"] = new_key
    chat_store.save_thread(t)

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


def _build_prompt(
    provider: chat_context.ContextProvider,
    history: list[dict],
    user_msg: str,
    *,
    preamble: str = "",
    recalled: str = "",
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
            f"{kindex}"
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
        parts.append("=== CUSTOM INSTRUCTIONS (operator-set) ===\n" + custom)
    # A prior-conversation summary (Phase 3 compaction) is comparatively stable: it
    # only changes when a compaction runs, not every turn, so it sits just before
    # the recent verbatim turns.
    if summary:
        parts.append("=== EARLIER CONVERSATION (summary) ===\n" + summary)
    if history:
        convo = []
        for m in history[-MAX_HISTORY:]:
            who = "Operator" if m["role"] == "user" else "You"
            convo.append(f"{who}: {m['content']}")
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
        parts.append("=== LIVE CONTEXT (current app state) ===\n" + ctx)
    if recalled:
        parts.append("=== RELEVANT NOTES (retrieved for this message) ===\n" + recalled)
    parts.append(
        "=== NOW REPLY ===\n"
        f"Operator: {user_msg}\n\n"
        "Reply as the assistant. Output only your reply text (plus a plan-patch block if a "
        "change was requested) — no 'You:' prefix."
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


# --- Phase 3: threshold compaction -------------------------------------------
# Instead of a sliding window (which shifts the cached prefix every turn), keep
# the transcript append-only and, when the uncompacted tail exceeds a token
# budget, fold its oldest half into a running summary in ONE pass. One cache
# invalidation per compaction instead of one per turn; raw messages stay in the
# thread JSON so it is always reversible.
_COMPACT_TOKEN_THRESHOLD = 6000  # tokens of uncompacted tail before folding
_CHARS_PER_TOKEN = 4
_COMPACT_KEEP_TAIL = 4  # turns a forced /compact keeps verbatim
# Summarization is one text-in/text-out call, so it should take seconds. Cap it
# hard: /compact is a foreground request the operator is waiting on, and the
# 10-minute library default meant a slow call read as a dead button.
_COMPACT_CALL_TIMEOUT_S = 120


def _est_tokens(msgs: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in msgs) // _CHARS_PER_TOKEN


def _fmt_turns(msgs: list[dict]) -> str:
    out = []
    for m in msgs:
        who = "Operator" if m.get("role") == "user" else "Assistant"
        out.append(f"{who}: {m.get('content', '')}")
    return "\n\n".join(out)


def _summarize_sync(prev_summary: str, to_fold: list[dict]) -> str:
    prompt = (
        "You are compacting a chat transcript to save context. Produce a concise running "
        "summary that preserves decisions made, facts established, the operator's stated "
        "preferences, and any open threads or unfinished tasks. Drop pleasantries and "
        "redundant back-and-forth. Write plainly. No em-dashes, no emojis.\n\n"
        + (f"=== EXISTING SUMMARY ===\n{prev_summary}\n\n" if prev_summary else "")
        + f"=== NEW TURNS TO FOLD IN ===\n{_fmt_turns(to_fold)}\n\n"
        "=== OUTPUT ===\nThe updated summary only, no preamble."
    )
    # Text in, text out. Without tools_disabled this ran in the DEFAULT agentic
    # mode: a full toolset plus every MCP server spun up just to write a summary,
    # which took minutes and made /compact look like it had hung or done nothing.
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU,
                                tools_disabled=True, timeout_s=_COMPACT_CALL_TIMEOUT_S)
    return (res.get("text") or "").strip()


def _distill_sync(to_fold: list[dict]) -> list[str]:
    prompt = (
        "From the following chat turns, extract only DURABLE facts worth remembering long "
        "term: the operator's stated preferences, decisions, commitments, and stable facts "
        "about their projects or life. Ignore transient chit-chat and anything trivial. "
        "Output each fact as one short plain line, no bullet characters, no em-dashes, no "
        "emojis. If there is nothing durable, output nothing.\n\n"
        f"=== TURNS ===\n{_fmt_turns(to_fold)}\n\n=== FACTS (one per line) ===\n"
    )
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU,
                                tools_disabled=True, timeout_s=_COMPACT_CALL_TIMEOUT_S)
    lines = [ln.strip("-*• \t") for ln in (res.get("text") or "").splitlines()]
    return [ln for ln in lines if ln and len(ln) > 8][:5]


# Scheduled distillations, held so the event loop cannot garbage-collect a task
# that nothing else references.
_DISTILL_TASKS: set = set()

# Thread ids with a fold in its summarization call right now. See _compact_thread.
_FOLDING: set[str] = set()

# Background folds, held for the same reason as _DISTILL_TASKS.
_FOLD_TASKS: set = set()


def _schedule_distill(to_fold: list[dict], scope_key: str) -> None:
    """Run distillation OUT of band.

    It used to be awaited inside the compaction pass, which put a second model
    call plus a note write on the foreground path. On /compact — a request the
    operator is actively waiting on — that turned a summarize call into
    summarize + distill + dedupe, and the button read as dead for ~30 seconds
    before anything appeared in the chat.

    Nothing in the response depends on the distilled facts: they land in the
    scope's knowledge note for LATER turns to recall. So the caller should not
    pay for them. Same reasoning as `chat_naming.schedule`.
    """
    if not scope_key:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — no running loop: do it inline.
        return
    task = loop.create_task(_distill(to_fold, scope_key))
    _DISTILL_TASKS.add(task)
    task.add_done_callback(_DISTILL_TASKS.discard)


async def _distill(to_fold: list[dict], scope_key: str) -> None:
    """Distillation (Phase 3): pull durable facts out of the turns being compacted
    away and into the scope's `chat-distilled` note, so knowledge that would other
    wise scroll out of context is retained and recallable. Best-effort, deduped,
    size-capped. Never raises into the reply path."""
    try:
        facts = await asyncio.to_thread(_distill_sync, to_fold)
        if not facts:
            return
        existing = knowledge.get_note(scope_key, "chat-distilled") or ""
        have = {ln.strip().lstrip("- ").strip().lower() for ln in existing.splitlines() if ln.strip()}
        fresh = [f for f in facts if f.strip().lower() not in have]
        if not fresh:
            return
        prefix = existing.rstrip() + "\n" if existing.strip() else ""
        body = (prefix + "\n".join(f"- {f}" for f in fresh))[-8000:]
        await knowledge.put_note(scope_key, "chat-distilled", "Distilled from chats", body,
                                 hook="facts auto-captured from chat compaction")
    except Exception as e:
        log.warning("chat distillation failed for %s: %s", scope_key, e)


async def _compact_thread(thread: dict, scope_key: str = "", *,
                          force: bool = False) -> tuple[str, list[dict], int]:
    """One compaction pass. Returns (summary, recent verbatim turns, folded count).

    Two entry conditions. Automatic (`force=False`): only when the uncompacted
    tail is both long enough and over the token budget, and then only the oldest
    half folds — that keeps the cached prefix stable across ordinary turns.
    Forced (`force=True`, the /compact command): fold everything except the last
    `_COMPACT_KEEP_TAIL` turns regardless of size, because the operator asked for
    the context back now.

    Best-effort: any failure leaves the thread untouched and returns the current
    (summary, tail, 0), so a compaction hiccup never blocks a reply or loses
    messages. Raw messages stay in the thread JSON either way, so the fold is
    always reversible. On a successful fold it also distills durable facts from
    the folded turns into the scope's knowledge (Phase 3)."""
    # Committed messages only. A run in flight renders as a trailing assistant
    # message (chat_store._live_view) but is not in the record yet, so folding it
    # would summarize a half-written reply and push the boundary past a message
    # that does not exist.
    msgs = [m for m in thread.get("messages", [])
            if not (m.get("meta") or {}).get("streaming")]
    summary = thread.get("summary", "") or ""
    ct = int(thread.get("compacted_through", 0) or 0)
    if ct < 0 or ct > len(msgs):
        ct = 0
    tail = msgs[ct:]
    if force:
        fold_n = max(0, len(tail) - _COMPACT_KEEP_TAIL)
        if fold_n == 0:
            return summary, tail, 0
    else:
        if len(tail) < 6 or _est_tokens(tail) < _COMPACT_TOKEN_THRESHOLD:
            return summary, tail, 0
        fold_n = len(tail) // 2
    to_fold, keep = tail[:fold_n], tail[fold_n:]
    tid = thread.get("id") or ""
    if tid in _FOLDING:
        # Another fold is already in the summarization call for this thread. Both
        # would compute the same `ct` and write the same range, so the second
        # would fold already-folded turns into a summary built without the
        # first's — losing the difference. One at a time; the loser is a no-op.
        return summary, tail, 0
    _FOLDING.add(tid)
    try:
        new_summary = await asyncio.to_thread(_summarize_sync, summary, to_fold)
    except Exception as e:
        log.warning("chat compaction failed for %s: %s", thread.get("id"), e)
        return summary, tail, 0
    finally:
        _FOLDING.discard(tid)
    if not new_summary:
        return summary, tail, 0

    # Write the FIELDS THIS FOLD OWNS, onto the thread as it is NOW — never the
    # snapshot above. That snapshot was read before a summarization call lasting
    # tens of seconds, and saving it back deleted every message the operator sent
    # during the fold. (Measured: 11 messages in, 10 on disk, the missing one
    # theirs. The transcript is an append-only log now, so no write can express
    # that at all — but the snapshot's OTHER fields would still be stale.)
    #
    # `compacted_through` survives the switch because it counts from the FRONT of
    # the transcript and appends land at the BACK: the messages this fold covers
    # are at the same indices no matter how many arrived meanwhile. The store
    # records it as the seq of the last folded message, which nothing can shift.
    # A /clear can land while this call was in flight. It advances
    # `compacted_through` to the end of the transcript and drops `summary`
    # outright; writing this fold's `ct + fold_n` afterward would move the
    # boundary BACKWARD and resurrect a summary of content the operator just
    # asked to forget. Check against the current record, not the snapshot this
    # function started from.
    current = chat_store.get_thread(tid) or {}
    if int(current.get("compacted_through", 0) or 0) >= ct + fold_n:
        return summary, tail, 0

    fields: dict[str, object] = {"summary": new_summary, "compacted_through": ct + fold_n}
    if force:
        # The stored measured value is the pre-fold context size; leaving it
        # would pin the composer meter at its old reading and make /compact look
        # like it did nothing. Drop it so the meter re-estimates from the newly
        # compacted prompt on the next read.
        fields["context_tokens"] = None
    chat_store.update_thread(tid, **fields)
    # Keep the caller's in-memory copy consistent with what was just persisted;
    # it is building this turn's prompt out of it.
    thread["summary"] = new_summary
    thread["compacted_through"] = ct + fold_n
    if force:
        thread.pop("context_tokens", None)
    # Scheduled, not awaited: the fold is what the caller asked for and is now
    # saved. See `_schedule_distill`.
    _schedule_distill(to_fold, scope_key)
    return new_summary, keep, fold_n


async def _maybe_compact(thread: dict, scope_key: str = "") -> tuple[str, list[dict]]:
    """Threshold compaction on the ordinary turn path. See `_compact_thread`."""
    summary, tail, _ = await _compact_thread(thread, scope_key)
    return summary, tail


@router.post("/threads/{thread_id}/compact")
async def compact_thread(thread_id: str):
    """Fold the STORED transcript now (half of the /compact command).

    A chat's context lives in two places and /compact has to reach both:

      1. the live claude session, whose window is what actually fills up. Only
         the CLI's own `/compact` frees that, and it does so by running as a
         normal turn (the raw-slash branch in POST /turns). The client sends
         that turn itself, so the fold is visible in the transcript instead of
         happening invisibly.
      2. the stored transcript, which is what gets re-assembled whenever a fresh
         session starts. That is this endpoint: fold everything except the last
         `_COMPACT_KEEP_TAIL` turns into the running summary, and report what
         moved so the client can render a marker rather than a silent no-op.

    `live_session` tells the client whether (1) is worth doing — a chat that has
    not started a session yet has no live window to reclaim.

    RETURNS IMMEDIATELY. This used to await the fold, which meant a summarization
    call — tens of seconds — stood between the operator pressing enter and the
    visible compaction starting. Their words: "it shows a weird message saying
    compacting... and then 30s later it starts ACTUALLY compacting." Nothing in
    (1) depends on (2): the live session's window is reclaimed by the CLI, and
    the stored fold is housekeeping on a file. Serializing them bought nothing
    and cost the entire delay.

    So the parts the client actually needs are computed here — how many turns
    WILL fold (pure arithmetic) and whether there is a live session — and the
    summarization runs detached. The client starts the visible half at once."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    msgs = t.get("messages", [])
    ct = int(t.get("compacted_through", 0) or 0)
    if ct < 0 or ct > len(msgs):
        ct = 0
    tail = msgs[ct:]
    # Same rule _compact_thread(force=True) applies, stated once here so the
    # client can be told the outcome without waiting for the model call.
    will_fold = max(0, len(tail) - _COMPACT_KEEP_TAIL)

    if will_fold:
        task = asyncio.get_running_loop().create_task(
            _compact_thread(t, provider.knowledge_scope(), force=True))
        _FOLD_TASKS.add(task)
        task.add_done_callback(_FOLD_TASKS.discard)

    return {
        "will_fold": will_fold,
        "kept": min(len(tail), _COMPACT_KEEP_TAIL),
        "before": len(tail),
        "compacted_through": ct,
        "live_session": _has_live_session(t, provider),
    }


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
    window but leaves the *stored* transcript's `compacted_through` alone, so
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


@router.post("/threads/{thread_id}/clear")
async def clear_thread_context(thread_id: str):
    """Drop this thread's context, keeping the transcript — the other half of
    the /clear vs /compact split (see `slashCommands.ts`). No summarization
    call, no detached task: this is a handful of field writes and returns as
    soon as they land. Do NOT "make it consistent with /compact" by adding a
    summarization step here — that is precisely what /clear exists to skip."""
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    provider = chat_context.get_provider(t.get("context_key"))
    had_summary = bool(t.get("summary"))
    session_reset = _reset_live_session(t, provider)
    result = chat_store.clear_thread_context(thread_id)
    if result is None:
        raise HTTPException(404, "thread not found")
    thread, cleared = result
    return {
        "cleared": cleared,
        "compacted_through": int(thread.get("compacted_through", 0) or 0),
        "dropped_summary": had_summary,
        "session_reset": session_reset,
    }


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
    only `summary + the uncompacted tail`, never the whole transcript, so the
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
    # Mirror what a real turn sends. Every provider folds the oldest turns into a
    # running summary (see _maybe_compact); only the summary + the tail after
    # `compacted_through` actually go to the model, so count that, not everything.
    history = t.get("messages", [])
    ct = int(t.get("compacted_through", 0) or 0)
    if 0 <= ct <= len(history):
        history = history[ct:]
    summary = t.get("summary", "") or ""
    prompt = _build_prompt(provider, history, "", preamble=preamble, summary=summary)
    used = max(1, round(len(prompt) / 4))
    return {
        "used_tokens": used,
        "window_tokens": window,
        "pct": min(100, round(used / window * 100)),
        "model": model,
        "source": "estimated",
    }


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
    auto-recalled knowledge + compacted history + this message) for either a data
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
    history = t.get("messages", [])
    recalled = ""
    summary = ""
    if not is_build_mode:
        # Auto-recall (Phase 2): retrieve notes relevant to this message from the
        # provider's knowledge scope and inject them into the reply tail. Build
        # providers read files directly, so skip them. Best-effort.
        if len(content.strip()) >= 8:
            try:
                hits = await knowledge.search(provider.knowledge_scope(), content, limit=3)
                blocks = [f"[{h.get('note_id') or 'note'}] {(h.get('text') or '').strip()}" for h in hits]
                recalled = "\n\n".join(blocks)[:2000]
            except Exception as e:
                log.warning("chat auto-recall failed for %s: %s", provider.key, e)
    # Threshold compaction (Phase 3): fold the oldest half of a long transcript
    # into a running summary so the cached prefix stays stable and the window does
    # not overflow. Applies to EVERY provider here, because this is the fresh-
    # session assembly: whatever it builds is the whole context the model gets, so
    # an uncompacted 200-message project chat would re-send the entire transcript
    # (and, past MAX_HISTORY, silently drop its own beginning instead).
    summary, history = await _maybe_compact(t, provider.knowledge_scope())
    prompt = _build_prompt(provider, history, content, preamble=preamble, recalled=recalled, summary=summary)
    # Stated after the persona so it can override the ticket protocols the
    # persona carries, in the direction that removes capability.
    prompt = prompt + "\n\n" + chat_context.chat_mode_block(t.get("mode"))
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
    the caller re-assembles the full prompt (summary + transcript tail) for a new
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
                    "conversation. Older copies are stale — ignore them.\n\n" + live
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
    if not chat_context.is_build_provider(provider) and len(content.strip()) >= 8:
        try:
            hits = await knowledge.search(provider.knowledge_scope(), content, limit=3)
            blocks = [f"[{h.get('note_id') or 'note'}] {(h.get('text') or '').strip()}" for h in hits]
            recalled = "\n\n".join(blocks)[:2000]
        except Exception as e:
            log.warning("chat auto-recall failed for %s: %s", provider.key, e)
            recalled = ""
        if recalled:
            parts.append("=== RELEVANT NOTES (retrieved for this message) ===\n" + recalled)
    parts.append("=== OPERATOR MESSAGE ===\n" + content)
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


def _record_user_turn(thread_id: str, content: str, parts: list[TurnPart] | None,
                      client_id: str | None = None) -> None:
    """Persist what the operator actually sent for this turn.

    One message normally. When a queue drained into this turn, the separate
    messages it held, each at its own send time and marked `queued` so the next
    prompt can say they were written before any had been answered."""
    if not parts or len(parts) < 2:
        chat_store.append_message(thread_id, "user", content, client_id=client_id)
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
        chat_store.append_message_during_turn(
            thread_id, "user", text, meta={"queued": True}, ts=p.sent_at,
            client_id=p.client_id)
        added += 1
    if not added:
        chat_store.append_message(thread_id, "user", content, client_id=client_id)


async def _start_agent_mention_turn(*, thread_id: str, thread: dict, agent: dict,
                                    content: str, model: str, run_id: str):
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
    cwd = str(task_scope.root_for_context(agent["scope"]) or
              os.environ.get("WORKSHOP_CHAT_CWD", os.path.expanduser("~")))

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
        recent.append(f"{who}: {body[:1500]}")
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
    # Reversibility gate. A mention is a manual invocation — the operator is
    # right there — so nothing is denied here; the list is non-empty only for a
    # run nobody is watching. Passed explicitly rather than defaulted so the
    # unattended path cannot forget it.
    agent_denied = agent_authority.disallowed_for(
        unattended=agent_authority.is_unattended(rec.get("autonomy")))

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
        resumed.append("=== THE OPERATOR ASKS YOU ===\n" + (question or content))
        turn = await build_streaming.start_turn(
            thread_id=thread_id, agent_slug=slug, prompt="\n\n".join(resumed), model=model,
            cwd=cwd, session_id=sid, resume=True, timeout_s=CHAT_TIMEOUT_S,
            allowed_tools=agent_tools, disallowed_tools=agent_denied,
            mcp_servers=[], tools_disabled=not tools_active,
            speaker=slug, run_id=run_id,
        )
        _ = kscope  # the CHAT's scope; the agent read its own layers on turn one
        return turn

    parts = [
        chat_context.agent_self_preamble(
            name=agent["name"], slug=slug, role=role, scope=agent["scope"], cwd=cwd,
            tools_active=tools_active,
            capabilities=rec.get("capabilities"), triggers=rec.get("triggers"),
            mentionable=bool(rec.get("mentionable")),
            delegatable=bool(rec.get("delegatable")),
        ),
        f"You are answering INSIDE a chat, because the operator addressed you "
        f"directly with @{slug}. Answer as yourself, briefly and directly. Do not "
        "greet, do not introduce yourself, and do not restate the question.",
    ]
    if definition:
        parts.append("=== YOUR DEFINITION ===\n" + definition)

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
    parts.append("=== THE OPERATOR ASKS YOU ===\n" + (question or content))
    prompt = "\n\n".join(parts)

    turn = await build_streaming.start_turn(
        thread_id=thread_id, agent_slug=slug, prompt=prompt, model=model,
        cwd=cwd, session_id=sid, resume=False, timeout_s=CHAT_TIMEOUT_S,
        allowed_tools=agent_tools, disallowed_tools=agent_denied,
        mcp_servers=[], tools_disabled=not tools_active,
        speaker=slug, run_id=run_id,
    )
    _ = kscope  # the CHAT's scope; the agent reads its own layers above
    return turn


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


async def _run_for_message(thread_id: str, t: dict, content: str,
                           model: str | None, parts: list[TurnPart] | None,
                           *, take_over: str | None = None,
                           already_recorded: bool = False,
                           client_id: str | None = None) -> "build_streaming.Turn":
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

    from services import mentions as _mentions
    _chat_scope = knowledge.scope_for_context(t.get("context_key") or "general")
    _mentioned = _mentions.resolve(content, _chat_scope)
    speaker = _mentioned["slug"] if _mentioned is not None else None

    run_id = build_streaming.new_run_id()
    joined = await build_streaming.claim_run(
        thread_id, run_id, model=model, agent=speaker, force=bool(take_over))
    if joined is not None:
        raise build_streaming.RunInFlight(joined)

    try:
        if not already_recorded:
            _record_user_turn(thread_id, content, parts, client_id=client_id)
        # ---- @mention: this message is addressed to an AGENT, not the assistant.
        # The agent answers in the chat, attributed to it, and the ordinary
        # assistant stays out of it. Resolution is scope-bounded and opt-in
        # (services/mentions); anything that does not resolve is ordinary text and
        # falls through, so an email address or a stray "@" can never fail a message.
        if _mentioned is not None:
            return await _start_agent_mention_turn(
                thread_id=thread_id, thread=t, agent=_mentioned, content=content,
                model=model, run_id=run_id)
        return await _start_assistant_turn(
            thread_id=thread_id, t=t, provider=provider, content=content,
            model=model, run_id=run_id)
    except Exception:
        # The claim outlives nothing. Give the thread back so the next message is
        # not told a run is in flight that never spawned.
        chat_store.release_run(thread_id, run_id)
        raise


async def _start_assistant_turn(*, thread_id: str, t: dict, provider, content: str,
                                model: str, run_id: str):
    """Spawn the turn for an ordinary (non-mention) message.

    Build-agent chats carry the AGENT's persistent Claude Code session; every
    other chat (project, general, app assistant) carries ONE session per THREAD,
    so a conversation continues instead of being re-derived each turn. The tool
    surface still differs by provider; only the continuity is shared."""
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
            timeout_s=CHAT_TIMEOUT_S, run_id=run_id,
        )

    is_project = chat_context.is_build_provider(provider)
    if is_project:
        _link_build_skills(provider)
        cwd = str(provider.dir)
        allowed_tools: list[str] | None = None
        mcp_servers: list[str] = []
        tools_disabled = False
    else:
        cwd = os.environ.get("WORKSHOP_CHAT_CWD", os.path.expanduser("~"))
        if ENABLE_CHAT_TOOLS:
            allowed_tools = chat_context.effective_allowed_tools(provider)
            mcp_servers = list(provider.mcp_servers)
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
        timeout_s=CHAT_TIMEOUT_S, run_id=run_id,
        allowed_tools=allowed_tools, mcp_servers=mcp_servers, tools_disabled=tools_disabled,
    )


async def _send(thread_id: str, content: str, model_in: str | None = None,
                parts: list[TurnPart] | None = None,
                client_id: str | None = None) -> dict:
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
    try:
        model = chat_context.normalize_model(model_in)
    except ValueError as e:
        raise HTTPException(400, str(e))

    take_over: str | None = None
    already_recorded = False
    run_id = _live_run_id(thread_id)
    if run_id:
        at_segment = _file_during_run(thread_id, run_id, content, client_id=client_id)
        if build_streaming.inject(run_id, content):
            return {"turn_id": run_id, "injected": True, "at_segment": at_segment}
        if not build_streaming.run_is_ending(run_id):
            # Claimed but not spawned yet: it is about to start and will read
            # this message as history. Starting a rival for it is the one thing
            # that must not happen here.
            return {"turn_id": run_id, "injected": False, "at_segment": at_segment}
        # It has spawned and its input channel is gone: it is finishing and will
        # never read this. The message is already in the log, so take the thread
        # over and give it a run of its own without writing it twice.
        log.info("thread %s: run %s is finishing; starting a fresh run for this message",
                 thread_id, run_id)
        take_over, already_recorded = run_id, True

    try:
        turn = await _run_for_message(thread_id, t, content, model, parts,
                                      take_over=take_over,
                                      already_recorded=already_recorded,
                                      client_id=client_id)
    except build_streaming.RunInFlight as e:
        # Another POST won the same event-loop tick. Join its run rather than
        # starting a rival, which is the whole reason the claim is atomic.
        at_segment = (_file_during_run(thread_id, e.run_id, content, client_id=client_id)
                      if not already_recorded else _at_segment(e.run_id, thread_id))
        build_streaming.inject(e.run_id, content)
        return {"turn_id": e.run_id, "injected": True, "at_segment": at_segment}
    return {"turn_id": turn.id, "injected": False, "at_segment": None}


@router.post("/threads/{thread_id}/turns")
async def start_streaming_turn(thread_id: str, body: StartTurnIn):
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "empty message")
    out = await _send(thread_id, content, body.model, body.parts,
                      client_id=body.client_id)
    if out["injected"]:
        return {"turn_id": out["turn_id"], "injected": True,
                "at_segment": out["at_segment"]}
    return {"turn_id": out["turn_id"]}


class InjectMessage(BaseModel):
    content: str
    # See StartTurnIn.client_id. A mid-turn message is persisted before it is
    # delivered, so the browser's id is the only thing that lets a retry of a
    # request whose response was lost land as the same message rather than a
    # second one.
    client_id: str | None = None


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
    run = chat_log.active_run(thread_id)
    snap = (run or {}).get("live") or {}
    return len(((snap.get("meta") or {}).get("segments")) or [])


def _file_during_run(thread_id: str, run_id: str, content: str,
                     client_id: str | None = None) -> int:
    """Persist a message arriving mid-run, BEFORE anyone tries to deliver it.

    Order matters and it used to be the other way round: the bytes went to the
    turn's stdin FIFO first and the store write happened after, inside a
    best-effort `except` that only logged. So a failed write meant the model had
    read a message the transcript never recorded, and the next fresh session
    re-assembled a history missing it.

    Persist first and the transcript can only ever be AHEAD of what the model
    saw, which is the recoverable direction. Returns `at_segment`, which the
    caller also reports to the client."""
    at_segment = _at_segment(run_id, thread_id)
    chat_store.append_message_during_turn(
        thread_id, "user", content,
        meta={"injected": True, "during_run": run_id, "at_segment": at_segment},
        client_id=client_id,
    )
    return at_segment


async def _deliver_or_take_over(thread_id: str, run_id: str, content: str,
                                client_id: str | None = None) -> dict | None:
    """Persist the message, hand it to the running run, and say what happened.

    Returns the response for a successful delivery, or None meaning "the run
    would not take it; start a fresh one for this message, which is ALREADY IN
    THE LOG". The caller must not record it a second time."""
    at_segment = _file_during_run(thread_id, run_id, content, client_id=client_id)
    if build_streaming.inject(run_id, content):
        return {"turn_id": run_id, "injected": True, "at_segment": at_segment}
    # It refused input: finishing, wedged, or its process is gone. Take the
    # thread back so the message it is holding can get a run of its own.
    log.info("thread %s: run %s refused a delivery; taking the thread over",
             thread_id, run_id)
    return None


@router.post("/threads/{thread_id}/inject")
async def inject_into_thread(thread_id: str, body: InjectMessage):
    """Hand a message to whatever run is going on this thread.

    The client should not have to know a run id to talk to its own chat. It knows
    which conversation it is in; the server resolves the rest.

    The message is PERSISTED before delivery is attempted, so `delivered: false`
    means "it is in the transcript and no run has it", never "it is gone"."""
    content = (body.content or "").strip()
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
    out = await _send(thread_id, content, client_id=body.client_id)
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


@router.post("/turns/{turn_id}/inject")
async def inject_into_turn(turn_id: str, body: InjectMessage):
    """Hand a message to a run that is already going, addressed by run id.

    A new message never interrupts the work in flight — only Stop does that. It
    is handed to the running turn, and the agent picks it up when it reaches the
    end of its current plan (measured; see the audit doc §2 — NOT "within a step
    or two", which is what this docstring used to claim).

    `delivered: false` is not an error. It means the run finished between the
    operator hitting enter and this request landing, and the client should queue
    the message as the next turn instead. The message is never dropped: it is
    written to the log before delivery is tried."""
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "empty message")
    turn = build_streaming.get_turn(turn_id)
    if turn is None or turn.complete:
        return {"delivered": False, "reason": "turn is no longer running"}
    at_segment = _file_during_run(turn.thread_id, turn_id, content)
    delivered = build_streaming.inject(turn_id, content)
    return {
        "delivered": delivered,
        "queued_for_turn": delivered,
        "at_segment": at_segment,
        "reason": "" if delivered else "turn is no longer accepting input",
    }


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
