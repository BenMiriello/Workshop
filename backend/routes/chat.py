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
# Gated by ENABLE_CHAT_TOOLS (default off) until the MCP-startup race in
# subprocess `claude -p` is resolved: when stdin is EOF'd immediately (the
# default for capture_output=True), the CLI launches the model BEFORE the
# --mcp-config servers finish their handshake, so the model sees `tools: []`
# and either admits no-tools or fabricates a call. Same exact argv from
# `bash` with a TTY-ish stdin works fine. Until that's pinned down, chat
# stays text-only — the design is in place; flipping the flag re-enables it.
ENABLE_CHAT_TOOLS = False
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


class RenameThread(BaseModel):
    title: str


@router.get("/providers")
async def providers():
    return chat_context.list_providers()


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


def _build_prompt(provider: chat_context.ContextProvider, history: list[dict], user_msg: str) -> str:
    parts = [provider.persona()]
    ctx = ""
    try:
        ctx = provider.live_context()
    except Exception as e:  # context is best-effort — never block the reply
        log.warning("chat context build failed for %s: %s", provider.key, e)
    if ctx:
        parts.append("=== LIVE CONTEXT (current app state) ===\n" + ctx)
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
    prompt = _build_prompt(provider, history, content)
    model = PROVIDER_MODELS.get(provider.key, DEFAULT_CHAT_MODEL)

    # Persist the user's message first so it isn't lost if the model call fails.
    chat_store.append_message(thread_id, "user", content)

    # Build mode (Project chats + Build-agent chats): full agentic toolset
    # (Read/Write/Edit/Bash/Grep/etc), cwd pinned to the project/agent directory,
    # larger max_turns + timeout, and — for Build-agent chats — a persistent
    # Claude Code session via --session-id (first turn) / --resume (subsequent).
    # The narrow-tool / tools_disabled branches don't apply — those are for
    # Data-mode surfaces.
    is_build_mode = isinstance(
        provider,
        (chat_context.ProjectContextProvider, chat_context.BuildAgentContextProvider),
    )
    if is_build_mode:
        call_kwargs = {
            "max_turns": provider.max_turns,
            "cwd": str(provider.dir),
        }
        # Sonnet is the right default for Build (codegen quality matters);
        # the operator can opt up to opus later via the model picker.
        model = llm_router.TIER_SONNET
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

        # On resumed turns the prompt is just the user's new message: the
        # Claude Code session already holds persona + prior context. Skip the
        # full _build_prompt rebuild that was created above.
        if is_resumed_turn:
            prompt = content
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

    history = t.get("messages", [])
    chat_store.append_message(thread_id, "user", content)

    # Persistent session via build_agents.ensure_session_id.
    from services import build_agents as _ba
    try:
        sid, is_new = _ba.ensure_session_id(provider.slug)
    except KeyError:
        sid, is_new = None, True

    # First turn: full prompt (persona + live context). Resumed turn: just
    # the new user message (the session retains its prior context).
    if sid and not is_new:
        prompt = content
        resume_flag = True
    else:
        prompt = _build_prompt(provider, history, content)
        resume_flag = False

    turn = await build_streaming.start_turn(
        thread_id=thread_id,
        agent_slug=provider.slug,
        prompt=prompt,
        model=llm_router.TIER_SONNET,
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
