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
import json as _json
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import chat_store, chat_context
from services import task_scope
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
# Per-provider tool policy: providers define allowed_tools + mcp_servers +
# max_turns (see chat_context.ContextProvider). The narrow-tool path in
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
CHAT_TIMEOUT_S = 300
MAX_HISTORY = 24  # messages of transcript carried into the prompt


class CreateThread(BaseModel):
    context_key: str = "general"
    title: str = ""
    agent_id: str | None = None


class PostMessage(BaseModel):
    content: str
    # Optional per-turn model override (short tier name "haiku"/"sonnet"/"opus"
    # OR a full model id). Normalized + validated in the handler; persisted on
    # the thread so it sticks for subsequent turns.
    model: str | None = None


class RenameThread(BaseModel):
    title: str


class RescopeThread(BaseModel):
    context_key: str


class SpawnTask(BaseModel):
    text: str | None = None       # explicit task text; else distilled from the chat
    project: str | None = None    # target project slug; else the chat's own scope
    acceptance: dict | None = None
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
    return chat_store.create_thread(
        context_key=body.context_key, title=body.title, agent_id=body.agent_id
    )


@router.get("/threads/{thread_id}")
async def thread(thread_id: str):
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    # Self-heal: a message left mid-stream by a backend restart (turn no longer
    # in memory) would otherwise stay streaming=true forever and make the client
    # keep trying to reconnect. Clear the flag so its persisted segments render
    # as final.
    changed = False
    for m in t.get("messages", []):
        meta = m.get("meta") or {}
        if meta.get("streaming"):
            live = build_streaming.get_turn(meta.get("turn_id") or "")
            if live is None or live.complete:
                meta["streaming"] = False
                m["meta"] = meta
                changed = True
    if changed:
        chat_store.save_thread(t)
    return t


@router.patch("/threads/{thread_id}")
async def rename(thread_id: str, body: RenameThread):
    t = chat_store.rename_thread(thread_id, body.title)
    if t is None:
        raise HTTPException(404, "thread not found")
    return t


@router.delete("/threads/{thread_id}")
async def remove(thread_id: str):
    return {"deleted": chat_store.delete_thread(thread_id)}


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
    if body.project:
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
        source={"kind": "chat", "ref": thread_id},
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
    # surprise re-run of unrelated work). The launcher opens the Job; we then
    # server-append a job-status ui-card so the live run persists in the thread
    # across reloads with zero new transport.
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
                job_id = run_res["job_id"]
                resp["job_id"] = job_id
                _append_job_status_card(thread_id, job_id, scope_key, task_id, task_rec)
    return resp


def _append_job_status_card(
    thread_id: str, job_id: str, scope: str, task_id: str, task_rec: dict
) -> None:
    """Persist the live run as an assistant message carrying a ```ui-card
    `job-status` block. The SERVER emits this card (never the model): the client
    polls the Job and renders the lifecycle inline, and the message survives a
    reload. Best-effort; a store hiccup must not fail the spawn."""
    title = (task_rec.get("text") or "").split("\n", 1)[0].strip()[:80] or "the task"
    card = {
        "id": f"job-{job_id}",
        "kind": "job-status",
        "title": "Background run",
        "job_id": job_id,
        "task_id": task_id,
        "scope": scope,
    }
    content = f'Running "{title}" now.\n\n```ui-card\n' + _json.dumps(card) + "\n```"
    try:
        chat_store.append_message(thread_id, "assistant", content, meta={"job_status": True})
    except Exception as e:
        log.warning("job-status card append failed: %s", e)


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
    try:
        kscope = provider.knowledge_scope()
        kindex = knowledge.index_block(kscope)
    except Exception as e:
        log.warning("chat knowledge index failed for %s: %s", provider.key, e)
        kindex = ""
    if kindex:
        parts.append(f"=== KNOWLEDGE (in scope: {kscope}) ===\n{kindex}")
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


# --- Phase 5: skill loader (use_skill one-hop expansion) ---------------------
# A data-mode assistant sees skill descriptions ambiently and can request a
# skill's full body by emitting a ```use_skill\n<name>``` block. When it does, we
# feed that skill's instructions back and let it finish the SAME reply — bounded
# to a single expansion so a turn can't loop.
_USE_SKILL_RE = re.compile(r"```use_skill\s*\n\s*([a-z0-9][a-z0-9-]{0,63})\s*\n```", re.IGNORECASE)


def _expand_use_skill_sync(prompt: str, reply: str, scope_key: str, model: str,
                           timeout_s: float, call_kwargs: dict) -> tuple[str, str | None]:
    m = _USE_SKILL_RE.search(reply)
    if not m:
        return reply, None
    name = m.group(1).strip()
    sk = skills.get_skill(scope_key, name)
    if not sk:
        # Requested a skill that doesn't exist in scope: strip the block so the
        # operator doesn't see raw protocol, keep whatever prose the model wrote.
        return _USE_SKILL_RE.sub("", reply).strip() or reply, None
    expanded = (
        prompt
        + f"\n\n=== SKILL LOADED: {name} ===\n{sk['body']}\n\n"
        "=== CONTINUE ===\nThe skill you requested is now in context above. Complete your "
        "reply to the operator following its instructions. Do NOT emit another use_skill block."
    )
    res = llm_router.call_cloud(expanded, model=model, timeout_s=timeout_s, **call_kwargs)
    final = (res.get("text") or "").strip()
    # Guard against a runaway second use_skill; strip any that slipped through.
    final = _USE_SKILL_RE.sub("", final).strip()
    return (final or reply), name


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
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU)
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
    res = llm_router.call_cloud(prompt, model=llm_router.TIER_HAIKU)
    lines = [ln.strip("-*• \t") for ln in (res.get("text") or "").splitlines()]
    return [ln for ln in lines if ln and len(ln) > 8][:5]


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


async def _maybe_compact(thread: dict, scope_key: str = "") -> tuple[str, list[dict]]:
    """Append-until-threshold + one compaction pass. Returns (summary, recent
    verbatim turns). Best-effort: any failure leaves the thread untouched and
    returns the current (summary, tail), so a compaction hiccup never blocks a
    reply or loses messages. On a successful fold it also distills durable facts
    from the folded turns into the scope's knowledge (Phase 3)."""
    msgs = thread.get("messages", [])
    summary = thread.get("summary", "") or ""
    ct = int(thread.get("compacted_through", 0) or 0)
    if ct < 0 or ct > len(msgs):
        ct = 0
    tail = msgs[ct:]
    if len(tail) < 6 or _est_tokens(tail) < _COMPACT_TOKEN_THRESHOLD:
        return summary, tail
    fold_n = len(tail) // 2
    to_fold, keep = tail[:fold_n], tail[fold_n:]
    try:
        new_summary = await asyncio.to_thread(_summarize_sync, summary, to_fold)
    except Exception as e:
        log.warning("chat compaction failed for %s: %s", thread.get("id"), e)
        return summary, tail
    if not new_summary:
        return summary, tail
    thread["summary"] = new_summary
    thread["compacted_through"] = ct + fold_n
    chat_store.save_thread(thread)
    if scope_key:
        await _distill(to_fold, scope_key)
    return new_summary, keep


# Context-window windows per model (mirrors the frontend meter). Claude = 200k;
# local models are smaller.
_CONTEXT_WINDOW = {
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


def _window_for(model: str | None) -> int:
    if model and model in _CONTEXT_WINDOW:
        return _CONTEXT_WINDOW[model]
    if model and model.startswith("claude"):
        return 200_000
    return 32_768 if model else 200_000


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
    window = _window_for(model)

    stored = t.get("context_tokens")
    if isinstance(stored, (int, float)) and stored > 0:
        used = int(stored)
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
    # Mirror what a real turn sends. Data providers fold the oldest turns into a
    # running summary (see _maybe_compact); only the summary + the tail after
    # `compacted_through` actually go to the model, so count that, not everything.
    history = t.get("messages", [])
    summary = ""
    if not is_build_mode:
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
        # into a running summary so the cached prefix stays stable and the window
        # does not overflow. Data mode only (build agents keep their own session).
        summary, history = await _maybe_compact(t, provider.knowledge_scope())
    prompt = _build_prompt(provider, history, content, preamble=preamble, recalled=recalled, summary=summary)
    return prompt, is_build_mode


@router.post("/threads/{thread_id}/messages")
async def send_message(thread_id: str, body: PostMessage):
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "empty message")

    provider = chat_context.get_provider(t.get("context_key"))
    history = t.get("messages", [])

    # Resolve the model up front: explicit body.model > thread.model > provider
    # default. Persist the choice so it sticks for later turns. (400 on a bad id.)
    model = _resolve_model(body.model, t, provider)
    if body.model is not None and model != t.get("model"):
        chat_store.set_thread_model(thread_id, model)

    prompt, is_build_mode = await _assemble_full_prompt(t, provider, content)

    # Persist the user's message first so it isn't lost if the model call fails.
    chat_store.append_message(thread_id, "user", content)

    # Build mode (Project chats + Build-agent chats): full agentic toolset
    # (Read/Write/Edit/Bash/Grep/etc), cwd pinned to the project/agent directory,
    # larger max_turns + timeout, and — for Build-agent chats — a persistent
    # Claude Code session via --session-id (first turn) / --resume (subsequent).
    # The narrow-tool / tools_disabled branches don't apply — those are for
    # Data-mode surfaces.
    if is_build_mode:
        _link_build_skills(provider)  # native .claude/skills discovery (Phase 5)
        call_kwargs = {
            "max_turns": provider.max_turns,
            "cwd": str(provider.dir),
        }
        # Build runs are often long. Keep a hard ceiling well above Data mode.
        call_timeout = 900

        # Persistent --resume sessions: only for BuildAgentContextProvider for
        # now (Project chats don't have a dedicated agent record to hang a
        # session_id on; spawn a Build agent for a project to get that).
        is_resumed_turn = False
        if isinstance(provider, chat_context.BuildAgentContextProvider):
            from services import build_agents as _ba
            try:
                sid, is_new = _ba.ensure_session_id(provider.slug)
                call_kwargs["session_id"] = sid
                call_kwargs["resume"] = not is_new
                is_resumed_turn = not is_new
            except KeyError:
                # Agent went away between provider construction and call; just
                # do a stateless turn rather than 500ing the chat.
                pass

        # On resumed turns the session already holds persona + prior context, so
        # we skip the full _build_prompt rebuild — but we STILL re-send the
        # runtime briefing (cwd/stack/ports/how-to-run can change between turns,
        # and the resumed session never re-reads it otherwise). This is the
        # researched "refresh the environment block every turn" pattern.
        if is_resumed_turn:
            briefing = chat_context.runtime_briefing(provider)
            prompt = (briefing + "\n\n=== OPERATOR MESSAGE ===\n" + content) if briefing else content
    elif ENABLE_CHAT_TOOLS:
        # Every data/assistant agent gets web access (built-in WebSearch/WebFetch)
        # plus whatever MCP tools it declares — web tools need no MCP server. See
        # chat_context.effective_allowed_tools (single source of truth, mirrored
        # by capability_for so the preamble can't drift).
        call_kwargs = {
            "allowed_tools": chat_context.effective_allowed_tools(provider),
            "mcp_servers": list(provider.mcp_servers),
            "max_turns": max(provider.max_turns, 6),
        }
        call_timeout = CHAT_TIMEOUT_S
    else:
        call_kwargs = {"tools_disabled": True}
        call_timeout = CHAT_TIMEOUT_S

    try:
        # call_cloud subprocesses `claude -p` and blocks for up to call_timeout —
        # push it off the event loop so other API requests (budget edits, refreshes,
        # parallel chat turns) don't stall behind a single in-flight chat call.
        result = await asyncio.to_thread(
            llm_router.call_cloud,
            prompt,
            model=model,
            timeout_s=call_timeout,
            **call_kwargs,
        )
        reply = result.get("text", "").strip()
        if result.get("recovered"):
            # The CLI exited non-zero but we salvaged the assistant text from
            # the stream-json transcript. For tools_disabled this is a config
            # regression; for the narrow-tool path it's most often a max_turns
            # hit mid-synthesis, which is benign as long as the recovered text
            # is a complete reply. Either way: log so it's visible.
            log.warning(
                "chat completion recovered (provider=%s) after exit=%d stop_reason=%s",
                provider.key, result.get("exit_code"), result.get("stop_reason"),
            )
        # Phase 5: if a data-mode assistant asked for a skill, feed the body back
        # and let it finish this reply (one bounded hop). Build mode is skipped —
        # builders open skills natively from their cwd.
        if not is_build_mode and "use_skill" in reply:
            try:
                reply, used_skill = await asyncio.to_thread(
                    _expand_use_skill_sync, prompt, reply,
                    provider.knowledge_scope(), model, call_timeout, call_kwargs,
                )
                if used_skill:
                    log.info("chat used skill '%s' (scope=%s)", used_skill, provider.knowledge_scope())
            except Exception as e:
                log.warning("use_skill expansion failed: %s", e)
    except Exception as e:
        # Surface the actual error detail to the operator — bare "CloudCallFailed"
        # is opaque and leaves them guessing whether it's a transient blip, an
        # auth issue, or a CLI-missing problem. The detail is already in
        # backend logs but the operator reads the chat bubble first.
        log.warning("chat completion failed: %s", e, exc_info=True)
        detail = str(e)[:240] if str(e) else type(e).__name__
        err = f"(model call failed: {detail})"
        msg = chat_store.append_message(thread_id, "assistant", err, meta={"error": True})
        return {"message": msg, "notes": []}

    cleaned, notes = provider.apply_actions(reply)
    # O2: a task-bound chat can maintain its task list via a task-patch block,
    # applied against THIS scope's store and stripped from the stored reply.
    cleaned, tnotes = task_scope.apply_task_patch(t.get("context_key"), cleaned)
    if tnotes:
        notes = (notes or []) + tnotes
    # Phase 4: an explicit operator preference/correction emitted as a
    # ``learning-note`` block is captured into the learnings store here,
    # deterministically (in addition to compaction-time distillation).
    cleaned, lnotes = task_scope.apply_learning_note(t.get("context_key"), cleaned)
    if lnotes:
        notes = (notes or []) + lnotes
    # Phase N3: a chat can leave a durable scoped ``note`` in the knowledge store
    # (the Notes substrate), captured deterministically in this same finalize path.
    cleaned, nnotes = await task_scope.apply_note_block(t.get("context_key"), cleaned)
    if nnotes:
        notes = (notes or []) + nnotes
    if not cleaned:
        cleaned = "(done)"
    meta: dict = {}
    if notes:
        meta["notes"] = notes
    # If the turn actually called tools (web search, workshop queries, an app's
    # MCP tools), surface them as chips so a data/assistant chat reads like a
    # real agent turn instead of a flat answer. Render = the tool calls as the
    # turn's "work", then the action-cleaned text as the answer. (UI-card fences
    # like project-proposal survive apply_actions and render inside the answer;
    # only server-side actions such as a budget plan-patch are stripped.)
    raw_segments = result.get("segments") or []
    tool_segs = [s for s in raw_segments if s.get("t") == "tool"]
    if tool_segs:
        meta["segments"] = tool_segs + ([{"t": "text", "text": cleaned}] if cleaned else [])
    msg = chat_store.append_message(thread_id, "assistant", cleaned, meta=meta or None)

    # Auto-checkpoint a build turn so the builder's work is a recoverable
    # timeline (doc 05 history substrate). Best-effort, off the event loop.
    if is_build_mode:
        try:
            from services import checkpoints, app_runner
            if not app_runner.is_self_dir(str(provider.dir)):
                await asyncio.to_thread(checkpoints.checkpoint, str(provider.dir), f"{provider.key} turn")
        except Exception as e:
            log.info("build checkpoint skipped: %s", e)

    # Auto-name the thread after the first exchange (non-blocking so it never
    # delays this reply; scheduled as a background task; the reply returns now).
    chat_naming.schedule(thread_id)

    return {"message": msg, "notes": notes}


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


class StartTurnIn(BaseModel):
    content: str
    # Optional per-turn model override (short tier name or full id); same
    # semantics as PostMessage.model.
    model: str | None = None


@router.post("/threads/{thread_id}/turns")
async def start_streaming_turn(thread_id: str, body: StartTurnIn):
    t = chat_store.get_thread(thread_id)
    if t is None:
        raise HTTPException(404, "thread not found")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "empty message")

    provider = chat_context.get_provider(t.get("context_key"))

    # Resolve + persist the model (explicit > thread > provider default). 400 on
    # a bad id. Build providers no longer hard-force sonnet — opus is selectable.
    model = _resolve_model(body.model, t, provider)
    if body.model is not None and model != t.get("model"):
        chat_store.set_thread_model(thread_id, model)

    chat_store.append_message(thread_id, "user", content)

    # ---- Build-agent providers: the persistent Claude Code session path --------
    if isinstance(provider, chat_context.BuildAgentContextProvider):
        history = t.get("messages", [])
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
        if content.startswith("/"):
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
        turn = await build_streaming.start_turn(
            thread_id=thread_id, agent_slug=provider.slug, prompt=prompt, model=model,
            cwd=str(provider.dir), session_id=sid, resume=resume_flag,
            max_turns=provider.max_turns, timeout_s=900,
        )
        return {"turn_id": turn.id}

    # ---- Project providers: full agentic toolset over the project tree, but no
    #      persistent session (there is no per-project agent record to hang one on;
    #      spawn a Build agent in the project to get resumable sessions). ----------
    if chat_context.is_build_provider(provider):
        _link_build_skills(provider)
        prompt, _ = await _assemble_full_prompt(t, provider, content)
        turn = await build_streaming.start_turn(
            thread_id=thread_id, agent_slug=getattr(provider, "slug", provider.key),
            prompt=prompt, model=model, cwd=str(provider.dir),
            session_id=None, resume=False, max_turns=provider.max_turns, timeout_s=900,
        )
        return {"turn_id": turn.id}

    # ---- Data / assistant providers: narrow tools (web + the provider's declared
    #      MCP) or tools-off, stateless, no project cwd. They can search the web and
    #      query the workshop, but cannot free-build without a chosen project. Same
    #      capability ladder (ENABLE_CHAT_TOOLS + effective_allowed_tools) as the
    #      blocking POST /messages path, now streamed so the turn shows its work. --
    prompt, _ = await _assemble_full_prompt(t, provider, content)
    if ENABLE_CHAT_TOOLS:
        allowed_tools = chat_context.effective_allowed_tools(provider)
        mcp_servers = list(provider.mcp_servers)
        tools_disabled = False
    else:
        allowed_tools, mcp_servers, tools_disabled = None, [], True
    default_cwd = os.environ.get("WORKSHOP_CHAT_CWD", os.path.expanduser("~"))
    turn = await build_streaming.start_turn(
        thread_id=thread_id, agent_slug=provider.key, prompt=prompt, model=model,
        cwd=default_cwd, session_id=None, resume=False,
        max_turns=max(provider.max_turns, 6), timeout_s=CHAT_TIMEOUT_S,
        allowed_tools=allowed_tools, mcp_servers=mcp_servers, tools_disabled=tools_disabled,
    )
    return {"turn_id": turn.id}


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
