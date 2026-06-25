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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import chat_store, chat_context
from services import llm_router
from services import build_streaming

log = logging.getLogger("workshop.chat")
router = APIRouter()

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


def _build_prompt(
    provider: chat_context.ContextProvider,
    history: list[dict],
    user_msg: str,
    *,
    preamble: str = "",
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
    ctx = ""
    try:
        ctx = provider.live_context()
    except Exception as e:  # context is best-effort — never block the reply
        log.warning("chat context build failed for %s: %s", provider.key, e)
    if ctx:
        parts.append("=== LIVE CONTEXT (current app state) ===\n" + ctx)
    try:
        custom = provider.custom_prompt()
    except Exception as e:
        log.warning("chat custom_prompt failed for %s: %s", provider.key, e)
        custom = ""
    if custom:
        parts.append("=== CUSTOM INSTRUCTIONS (operator-set) ===\n" + custom)
    if history:
        convo = []
        for m in history[-MAX_HISTORY:]:
            who = "Operator" if m["role"] == "user" else "You"
            convo.append(f"{who}: {m['content']}")
        parts.append("=== CONVERSATION SO FAR ===\n" + "\n\n".join(convo))
    parts.append(
        "=== NOW REPLY ===\n"
        f"Operator: {user_msg}\n\n"
        "Reply as the assistant. Output only your reply text (plus a plan-patch block if a "
        "change was requested) — no 'You:' prefix."
    )
    return "\n\n".join(parts)


def _is_blank_title(title: str | None) -> bool:
    """A thread title that should be auto-generated: empty/blank or the
    placeholder. (chat_store seeds 'New chat'; append_message also seeds a
    first-user-message snippet, so we treat that snippet as auto-nameable too
    only when still the seed — see _maybe_autoname which checks msg count.)"""
    return (title or "").strip() in ("", "New chat")


def _autoname_blocking(thread_id: str, first_user: str, first_assistant: str) -> None:
    """Generate a short (<=6 word) title from the first exchange via a cheap
    Haiku, tools-off, single-turn call, then rename the thread. Runs in a
    worker thread (call_cloud blocks). Best-effort: any failure leaves the
    existing title untouched. Re-checks the title under the current state so we
    never clobber a title the operator set in the meantime."""
    cur = chat_store.get_thread(thread_id)
    if cur is None or not _is_blank_title(cur.get("title")):
        return
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
    # First line only; guard against the model adding a preface.
    title = title.splitlines()[0].strip() if title else ""
    # Hard cap at 6 words.
    words = title.split()
    if len(words) > 6:
        title = " ".join(words[:6])
    if not title:
        return
    # Final guard: only rename if still blank (operator may have renamed).
    cur = chat_store.get_thread(thread_id)
    if cur is not None and _is_blank_title(cur.get("title")):
        chat_store.rename_thread(thread_id, title)


async def _maybe_autoname(thread_id: str, user_msg: str, assistant_msg: str) -> None:
    """Fire-and-forget auto-naming after the first assistant reply lands in a
    thread whose title is still the seed. Non-blocking: scheduled as a task so
    it never delays the user-facing reply."""
    t = chat_store.get_thread(thread_id)
    if t is None or not _is_blank_title(t.get("title")):
        return
    # Only after the first exchange (1 user + 1 assistant message persisted).
    msgs = t.get("messages", [])
    if len([m for m in msgs if m.get("role") == "assistant"]) != 1:
        return
    await asyncio.to_thread(_autoname_blocking, thread_id, user_msg, assistant_msg)


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

    # Capability facts: computed from the SAME branch that decides the run config
    # (capability_for mirrors the build/narrow/tools-off ladder below), so the
    # role preamble can never claim tools the run doesn't actually have.
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
    prompt = _build_prompt(provider, history, content, preamble=preamble)

    # Persist the user's message first so it isn't lost if the model call fails.
    chat_store.append_message(thread_id, "user", content)

    # Build mode (Project chats + Build-agent chats): full agentic toolset
    # (Read/Write/Edit/Bash/Grep/etc), cwd pinned to the project/agent directory,
    # larger max_turns + timeout, and — for Build-agent chats — a persistent
    # Claude Code session via --session-id (first turn) / --resume (subsequent).
    # The narrow-tool / tools_disabled branches don't apply — those are for
    # Data-mode surfaces.
    if is_build_mode:
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
    elif ENABLE_CHAT_TOOLS and provider.allowed_tools:
        # Narrow-tool path: read-only MCP, no built-ins.
        call_kwargs = {
            "allowed_tools": list(provider.allowed_tools),
            "mcp_servers": list(provider.mcp_servers),
            "max_turns": provider.max_turns,
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
    if not cleaned:
        cleaned = "(done)"
    meta = {"notes": notes} if notes else None
    msg = chat_store.append_message(thread_id, "assistant", cleaned, meta=meta)

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
    asyncio.create_task(_maybe_autoname(thread_id, content, cleaned))

    return {"message": msg, "notes": notes}


# ---- SSE streaming for Build chats -----------------------------------------
# Per turn, the operator gets a real-time event stream: thinking → tool calls
# → text chunks → done. Implemented by:
#   1) POST /threads/{id}/turns starts a streaming claude subprocess and
#      returns a turn_id immediately.
#   2) GET /turns/{turn_id}/stream is an SSE channel with `id: <offset>` per
#      event so EventSource auto-reconnect resumes via Last-Event-ID.
# Only Build providers (Project / BuildAgent) use this path; Data chats stay
# on the synchronous POST /messages route since their turns are short.


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
    if not isinstance(provider, chat_context.BuildAgentContextProvider):
        raise HTTPException(400, "streaming turns are Build-only; use POST /messages for Data chats")

    # Resolve + persist the model (explicit > thread > provider default). 400 on
    # a bad id. Build providers no longer hard-force sonnet — opus is selectable.
    model = _resolve_model(body.model, t, provider)
    if body.model is not None and model != t.get("model"):
        chat_store.set_thread_model(thread_id, model)

    history = t.get("messages", [])
    chat_store.append_message(thread_id, "user", content)

    # Persistent session via build_agents.ensure_session_id.
    from services import build_agents as _ba
    try:
        sid, is_new = _ba.ensure_session_id(provider.slug)
    except KeyError:
        sid, is_new = None, True

    # First turn: full prompt with the platform role preamble (capability +
    # egress facts) + runtime briefing + persona + live context. Resumed turn:
    # the new user message, with the runtime briefing re-sent (the session
    # retains persona/history but the environment block must refresh every turn).
    if content.startswith("/"):
        # Claude Code slash command (/compact, /cost, /context, /review, …):
        # forward it RAW so the CLI registers it as a command rather than a chat
        # message. A wrapped "/compact" (briefing + preamble in front) would not
        # register. Resume the live session when there is one so the command acts
        # on THIS conversation's context.
        prompt = content
        resume_flag = bool(sid and not is_new)
    elif sid and not is_new:
        briefing = chat_context.runtime_briefing(provider)
        prompt = (briefing + "\n\n=== OPERATOR MESSAGE ===\n" + content) if briefing else content
        resume_flag = True
    else:
        tools_active, tool_summary = chat_context.capability_for(
            provider, enable_chat_tools=ENABLE_CHAT_TOOLS
        )
        preamble = chat_context.role_preamble(
            tools_active=tools_active,
            tool_summary=tool_summary,
            cwd=str(provider.dir),
            is_build=True,
        )
        prompt = _build_prompt(provider, history, content, preamble=preamble)
        resume_flag = False

    turn = await build_streaming.start_turn(
        thread_id=thread_id,
        agent_slug=provider.slug,
        prompt=prompt,
        model=model,
        cwd=str(provider.dir),
        session_id=sid,
        resume=resume_flag,
        max_turns=provider.max_turns,
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
