"""LLM router — picks the right tier per call class with health-gated fallback.

Tier ladder:
    0  Ollama qwen2.5:32b      local, free, fast — but ONLY for short scoped prompts
                               (classification, routing decisions, redaction, single-word answers)
    1  Claude Haiku 4.5        subscription, default cloud — most agent reasoning, simple code
    2  Claude Sonnet 4.6       subscription, mid-cost — bigger tasks
    3  Claude Opus 4.8         subscription, expensive — only when truly needed

The router gates Ollama on health + GPU availability. If Ollama is down or the
GPU is saturated by another workload (image gen, another model, etc.), the
router falls through to Haiku automatically. Failures never permanently block;
each call has a timeout and the system recovers as soon as Ollama is back.

Public API:
    classify_task(task: str) -> str       # returns one of {"local","haiku","sonnet","opus"}
    call_local(prompt, **kw) -> dict      # raises OllamaUnavailable if router falls through
    call_cloud(prompt, model, **kw)       # subprocess to `claude -p --model <model>`
    route_and_call(task, prompt) -> dict  # high-level: classify + dispatch + return result

Subscription auth: all cloud calls go through `claude -p` (subprocess to the
local Claude Code CLI). That uses CLAUDE_CODE_OAUTH_TOKEN; never ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx


log = logging.getLogger("workshop.llm_router")


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:32b")
CLAUDE_CLI = os.environ.get("CLAUDE_CLI", "/home/clawd-a/.nvm/versions/node/v22.22.3/bin/claude")
AUTH_ENV = os.environ.get("CLAUDE_AUTH_ENV", "/home/clawd-a/.config/clawdbot/auth.env")

# Tier model handles (claude -p --model arg)
TIER_HAIKU = "claude-haiku-4-5-20251001"
TIER_SONNET = "claude-sonnet-4-6"
TIER_OPUS = "claude-opus-4-8"

# Health thresholds
OLLAMA_HEALTH_TIMEOUT_S = 3
OLLAMA_REQUEST_TIMEOUT_S = 60
OLLAMA_REDACTION_TIMEOUT_S = 180
GPU_BUSY_UTIL_PCT = 85
GPU_BUSY_SAMPLES = 2     # consecutive samples >threshold = treat as busy

# Auto-cooldown after consecutive failures: after N back-to-back Ollama errors,
# stop trying for COOLDOWN_S to give the system a chance to recover.
OLLAMA_FAILURE_COOLDOWN_THRESHOLD = 3
OLLAMA_FAILURE_COOLDOWN_S = 120


class OllamaUnavailable(RuntimeError):
    """Ollama is down, GPU is busy, or the router has cooled it down."""


class CloudCallFailed(RuntimeError):
    """`claude -p` returned non-zero or no parseable output."""


@dataclass
class RouterState:
    consecutive_failures: int = 0
    cooldown_until_epoch: float = 0.0
    last_gpu_check_epoch: float = 0.0
    last_gpu_util_pct: int = 0
    last_gpu_free_mb: int = 0
    last_health_ok: bool = True
    last_health_check_epoch: float = 0.0


_state = RouterState()


# -------------------------------------------------------------------- Ollama health


def _check_gpu_via_nvidia_smi() -> tuple[int, int]:
    """Returns (utilization_pct, free_memory_mb). On any failure → (0, large) so the
    router doesn't false-positive 'busy' if nvidia-smi isn't available."""
    if not shutil.which("nvidia-smi"):
        return (0, 99999)
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return (0, 99999)
        line = out.stdout.strip().splitlines()[0]
        util_s, free_s = [p.strip() for p in line.split(",")]
        return (int(util_s), int(free_s))
    except (subprocess.SubprocessError, ValueError, IndexError):
        return (0, 99999)


def ollama_health() -> dict:
    """Cheap probe: HTTP /api/tags + GPU util sample. Caches result for 5s to
    avoid storming nvidia-smi from every consecutive call."""
    now = time.time()
    if now - _state.last_health_check_epoch < 5:
        return {
            "ok": _state.last_health_ok,
            "gpu_util_pct": _state.last_gpu_util_pct,
            "gpu_free_mb": _state.last_gpu_free_mb,
            "cooldown_active": now < _state.cooldown_until_epoch,
            "cached": True,
        }
    _state.last_health_check_epoch = now

    try:
        with httpx.Client(timeout=OLLAMA_HEALTH_TIMEOUT_S) as cl:
            r = cl.get(f"{OLLAMA_URL}/api/tags")
            ollama_up = r.status_code == 200
    except (httpx.HTTPError, httpx.TimeoutException):
        ollama_up = False

    util, free = _check_gpu_via_nvidia_smi()
    _state.last_gpu_util_pct = util
    _state.last_gpu_free_mb = free
    _state.last_health_ok = ollama_up
    return {
        "ok": ollama_up,
        "gpu_util_pct": util,
        "gpu_free_mb": free,
        "cooldown_active": now < _state.cooldown_until_epoch,
        "cached": False,
    }


def ollama_available(for_purpose: str = "general") -> bool:
    """Final decision: should this call route to Ollama right now? `for_purpose`
    can be 'redaction' (more tolerant) or 'general'. Redaction is more
    important to do locally, so we tolerate higher GPU util."""
    h = ollama_health()
    if not h["ok"]:
        return False
    if h["cooldown_active"]:
        return False
    gpu_threshold = GPU_BUSY_UTIL_PCT
    if for_purpose == "redaction":
        gpu_threshold = 95   # only reject Ollama for redaction if GPU is truly pinned
    if h["gpu_util_pct"] > gpu_threshold:
        return False
    return True


def _record_failure() -> None:
    _state.consecutive_failures += 1
    if _state.consecutive_failures >= OLLAMA_FAILURE_COOLDOWN_THRESHOLD:
        _state.cooldown_until_epoch = time.time() + OLLAMA_FAILURE_COOLDOWN_S
        log.warning(
            "llm_router: ollama in cooldown for %ds after %d consecutive failures",
            OLLAMA_FAILURE_COOLDOWN_S, _state.consecutive_failures,
        )


def _record_success() -> None:
    _state.consecutive_failures = 0


# -------------------------------------------------------------------- Ollama call


def call_local(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: str = OLLAMA_MODEL,
    json_mode: bool = False,
    timeout_s: float = OLLAMA_REQUEST_TIMEOUT_S,
    purpose: str = "general",
) -> dict:
    """Call Ollama directly. Raises OllamaUnavailable if router gates it out.

    Returns: {"text": str, "model": str, "elapsed_s": float, "tokens_out": int}
    """
    if not ollama_available(for_purpose=purpose):
        h = ollama_health()
        raise OllamaUnavailable(
            f"ollama gated: up={h['ok']} cooldown={h['cooldown_active']} gpu_util={h['gpu_util_pct']}"
        )

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_ctx": 8192, "temperature": 0.2},
    }
    if system:
        payload["system"] = system
    if json_mode:
        payload["format"] = "json"

    start = time.time()
    try:
        with httpx.Client(timeout=timeout_s) as cl:
            r = cl.post(f"{OLLAMA_URL}/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError) as e:
        _record_failure()
        raise OllamaUnavailable(f"ollama call failed: {type(e).__name__}: {e}") from e

    _record_success()
    return {
        "text": data.get("response", ""),
        "model": model,
        "elapsed_s": round(time.time() - start, 3),
        "tokens_out": data.get("eval_count", 0),
    }


# -------------------------------------------------------------------- Cloud call


def _claude_env() -> dict:
    """Build the env for subprocess `claude -p` — subscription auth only."""
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    # Plaid creds reach the MCP via server.js's dotenv load of plaid-auth.env,
    # but dotenv won't override vars already set in the process env. uvicorn
    # carries empty PLAID_CLIENT_ID/SECRET and a stale PLAID_ENV=sandbox, which
    # shadow the file's production creds -> MISSING_FIELDS / "invalid client_id
    # or secret". Strip all three so dotenv loads the real values. Mirrors
    # plaid_bridge._STRIP_FROM_NODE_ENV (same bug, already fixed on that path).
    for _k in ("PLAID_CLIENT_ID", "PLAID_SECRET", "PLAID_ENV"):
        env.pop(_k, None)
    # Source auth.env into env
    if Path(AUTH_ENV).is_file():
        try:
            with open(AUTH_ENV, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    return env


def _extract_text_from_stream_json(stdout: str) -> str:
    """Walk `--output-format stream-json --verbose` events and concatenate every
    assistant text content block. Used to recover partial output when the CLI
    exits non-zero (max_turns, mid-turn error) so the operator sees whatever
    the model actually produced instead of an opaque "(model call failed)".

    Ignores `thinking` blocks (internal) and `tool_use` blocks (no surface
    meaning when tools are off). Returns "" if no text was emitted.
    """
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message") or {}
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text") or ""
                if txt:
                    parts.append(txt)
    return "\n".join(parts).strip()


# Empty MCP-server config — pairs with --strict-mcp-config to truly disable
# all MCP tools. Without this, `--tools ""` only disables the built-in tools
# (Bash/Read/Edit/etc); MCP servers loaded from project .mcp.json or user
# settings still surface to the model, which then tries to call them and
# burns turns on permission denials before falling back to text. Verified
# empirically 2026-06-04: tool-baiting prompts like "read /etc/hostname"
# converge to a text-only response in 1 turn when this config is applied.
_EMPTY_MCP_CONFIG_JSON = '{"mcpServers":{}}'
_TOOLS_OFF_SYSTEM_NUDGE = (
    "You have no tools available in this session. "
    "Answer in pure text only — do not attempt to call any tool."
)

# MCP-startup race fix. By default the `claude -p` CLI starts --mcp-config
# servers FULLY ASYNC ("[MCP] ... running fully async (nonblocking)") and fires
# the first model turn before the stdio handshake completes — so the model sees
# `tools: []`, the init event reports the server as `pending`, and it either
# admits no-tools or FABRICATES a tool call as literal text. Verified in the
# 2.1.177 CLI: the API request went out ~176ms BEFORE the plaid server finished
# connecting. Setting MCP_CONNECTION_NONBLOCKING to a falsy value flips the
# CLI's internal gate (g5q(): `if (P9(env.MCP_CONNECTION_NONBLOCKING)) return
# false; return true`) so it AWAITS the MCP connection (bounded by the CLI's own
# 30s connect timeout) before the first turn. With it set, the same call shows
# the server `connected`, tools_count=7, and a real tool_use + tool_result.
# Only applied on the narrow-tool path (where we actually spawn an MCP server we
# need the model to use); the tools-off path spawns no servers so blocking is a
# no-op there, and we leave it unset for build/agentic runs to preserve their
# existing latency profile.
_MCP_BLOCK_STARTUP_ENV = {"MCP_CONNECTION_NONBLOCKING": "false"}

# Registry of MCP server launch specs available to chat subprocesses. Each
# entry must match the {"command","args"} stdio launch the parent uses (see
# ~/.claude.json). Only servers needed by some provider's allowed_tools live
# here — everything else stays out so the subprocess never spawns it.
_MCP_SERVER_SPECS: dict[str, dict] = {
    "plaid": {
        "type": "stdio",
        "command": "node",
        "args": ["/home/clawd-a/clawdbot/plaid-mcp/server.js"],
    },
}


def call_cloud(
    prompt: str,
    *,
    model: str = TIER_HAIKU,
    max_turns: int = 6,
    timeout_s: float = 600,
    cwd: str = "/home/clawd-a/workshop",
    tools_disabled: bool = False,
    allowed_tools: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    session_id: str | None = None,
    resume: bool = False,
) -> dict:
    """Subprocess `claude -p` with subscription auth.

    Three modes:

    1. `tools_disabled=True` — pure text-in/text-out. `--strict-mcp-config` +
       empty mcp-config + `--tools ""` + system nudge + `--max-turns 1`. Stream-
       json output so partial text is recoverable on any non-zero exit.

    2. `allowed_tools` set (with optional `mcp_servers`) — narrow tool surface
       for read-only Q&A that needs on-demand fetch (e.g. budget chat searching
       transactions). Built-in tools stay off (`--tools ""`); only the MCP tools
       in `allowed_tools` are gated through via `--allowedTools`. Only the MCP
       servers in `mcp_servers` are spawned. Uses stream-json + bypassPermissions
       (non-interactive subprocess; the allowlist itself is the gate). Honors
       the caller-provided `max_turns` so the model can do one tool call + a
       synthesis turn with headroom.

    3. Default — agentic mode, full tool/MCP inheritance, plain text output.

    Returns: {"text": str, "model": str, "elapsed_s": float, "exit_code": int,
              "stop_reason": str | None, "recovered": bool}
    `recovered` is True if exit_code != 0 but we salvaged assistant text from
    the transcript (only meaningful for the stream-json paths).
    """
    cmd: list[str] = [
        CLAUDE_CLI, "-p", prompt,
        "--model", model,
    ]
    use_stream_json = False
    # Whether to force the CLI to block on MCP-server startup before the first
    # model turn (fixes the handshake race — see _MCP_BLOCK_STARTUP_ENV). Only
    # the narrow-tool path sets this; it spawns the server the model must use.
    block_mcp_startup = False
    if tools_disabled:
        # Tools-off path: stream-json so we can salvage partial text on error,
        # hardened MCP/tool config, and pin max_turns=1 (there's no agentic
        # loop to bound — it's a single Q&A round-trip).
        cmd.extend([
            "--output-format", "stream-json",
            "--verbose",                       # required by stream-json
            "--max-turns", "1",
            "--strict-mcp-config",
            "--mcp-config", _EMPTY_MCP_CONFIG_JSON,
            "--tools", "",                     # empty = disable all built-ins
            "--append-system-prompt", _TOOLS_OFF_SYSTEM_NUDGE,
        ])
        use_stream_json = True
    elif allowed_tools:
        # Narrow-tool path: only the MCP servers the caller needs are spawned,
        # only the named tools are permitted, built-ins stay off. Stream-json
        # to preserve partial-text recovery if the model hits max_turns mid-
        # synthesis. bypassPermissions is safe here — there's no interactive
        # operator to confirm prompts, and the allowlist IS the gate.
        servers_subset = {
            name: _MCP_SERVER_SPECS[name]
            for name in (mcp_servers or [])
            if name in _MCP_SERVER_SPECS
        }
        # allowed_tools may mix MCP tools ("mcp__plaid__…") with built-in tools
        # ("WebSearch", "WebFetch"). MCP names are gated via --allowedTools; the
        # built-in names must ALSO be enabled in the built-in set via --tools
        # (default is "" = all built-ins off). So a data-mode assistant can be
        # granted web access without opening the full agentic toolset.
        builtin_tools = [t for t in allowed_tools if not t.startswith("mcp__")]
        cmd.extend([
            "--output-format", "stream-json",
            "--verbose",
            "--max-turns", str(max_turns),
            "--strict-mcp-config",
            "--mcp-config", json.dumps({"mcpServers": servers_subset}),
        ])
        # "" keeps built-ins off (MCP-only surfaces); a list enables exactly the
        # named built-ins (e.g. web tools) and nothing else (no Bash/Edit/Read).
        cmd.extend(["--tools", *builtin_tools] if builtin_tools else ["--tools", ""])
        cmd.extend([
            "--allowedTools", *allowed_tools,
            "--permission-mode", "bypassPermissions",
        ])
        use_stream_json = True
        # Wait for the MCP handshake before the model turn — otherwise the model
        # sees no tools and fabricates the call (see _MCP_BLOCK_STARTUP_ENV).
        block_mcp_startup = bool(servers_subset)
    else:
        cmd.extend([
            "--output-format", "text",
            "--max-turns", str(max_turns),
        ])

    # Session persistence (Build mode): `--session-id` reserves a uuid on the
    # first call so subsequent `--resume` calls continue the same conversation.
    # The Claude Code CLI maintains its own checkpoint on disk; we just pass
    # the handle. `resume=True` means "this is a follow-up turn, pull prior
    # context from the saved session" — the caller's prompt becomes just the
    # new user message rather than rebuilt history.
    if session_id and resume:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--session-id", session_id])

    env = _claude_env()
    if block_mcp_startup:
        env.update(_MCP_BLOCK_STARTUP_ENV)

    start = time.time()
    try:
        proc = subprocess.run(
            cmd, env=env, cwd=cwd,
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        raise CloudCallFailed(f"claude -p timed out after {timeout_s}s: {e}") from e
    elapsed = time.time() - start

    # Stream-json paths (tools_disabled or narrow allowed_tools): parse stream
    # events and prefer recovered text even on non-zero exit, since the model
    # may emit a full reply and THEN the CLI decides to call it an error
    # (e.g. stop_reason=tool_use with no tools, max_turns mid-synthesis).
    if use_stream_json:
        text = _extract_text_from_stream_json(proc.stdout)
        stop_reason = None
        # The final {"type":"result"} event carries stop_reason/terminal_reason.
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                stop_reason = ev.get("terminal_reason") or ev.get("stop_reason")
        if text:
            if proc.returncode != 0:
                log.warning(
                    "call_cloud: recovered %d chars of assistant text despite exit=%d (stop_reason=%s)",
                    len(text), proc.returncode, stop_reason,
                )
            return {
                "text": text,
                "model": model,
                "elapsed_s": round(elapsed, 3),
                "exit_code": proc.returncode,
                "stop_reason": stop_reason,
                "recovered": proc.returncode != 0,
            }
        # No salvageable text — fall through to the error path with detail.
        stderr_snip = (proc.stderr or "").strip()[:300]
        raise CloudCallFailed(
            f"claude -p exited {proc.returncode} with no assistant text "
            f"(stop_reason={stop_reason}, stderr={stderr_snip or '(empty)'})"
        )

    # Tools-enabled path: original behavior, plain text output.
    if proc.returncode != 0:
        # `claude -p` often writes user-facing errors to stdout, not stderr
        # (e.g. "Error: Reached max turns (N)", auth errors, rate-limit
        # messages). Surface BOTH so operators reading the chat-bubble
        # error see the actual cause instead of "stderr=)".
        stderr_snip = (proc.stderr or "").strip()[:300]
        stdout_snip = (proc.stdout or "").strip()[:300]
        detail = stderr_snip or stdout_snip or "(no output)"
        raise CloudCallFailed(
            f"claude -p exited {proc.returncode}: {detail}"
        )
    return {
        "text": proc.stdout.strip(),
        "model": model,
        "elapsed_s": round(elapsed, 3),
        "exit_code": proc.returncode,
        "stop_reason": None,
        "recovered": False,
    }


# -------------------------------------------------------------------- High-level routing


def classify_task(task: str) -> str:
    """Quick local classification of task → tier. Falls back to safe defaults.

    Returns one of: "local", "haiku", "sonnet", "opus".
    Heuristic-first (cheap, no LLM call); only escalates to Ollama for ambiguous cases.
    """
    t = task.lower()
    words = len(task.split())

    # Strong local indicators: classification, yes/no, single-fact extraction
    if any(k in t for k in ["classify", "yes or no", "category", "extract", "redact", "is this", "summarize in one"]):
        if words < 200:
            return "local"

    # Strong opus indicators: complex multi-step, design, architecture
    if any(k in t for k in ["architect", "design the whole", "complex refactor", "audit the entire"]):
        return "opus"

    # Strong sonnet indicators: medium-substantial code work
    if any(k in t for k in ["implement", "write a service", "build a", "refactor"]):
        if words > 50:
            return "sonnet"
        return "haiku"

    # Default: haiku for most agent reasoning + simple code
    return "haiku"


def route_and_call(task: str, prompt: str | None = None, **kwargs) -> dict:
    """High-level: classify the task, dispatch to the right tier, fall through
    on Ollama unavailability. Returns the call result + tier_used."""
    prompt = prompt or task
    tier = classify_task(task)

    # Local tier with fallback to haiku
    if tier == "local":
        try:
            r = call_local(prompt, purpose=kwargs.get("purpose", "general"))
            return {**r, "tier_used": "local"}
        except OllamaUnavailable as e:
            log.info("router: local→haiku fallback: %s", e)
            tier = "haiku"

    model_map = {"haiku": TIER_HAIKU, "sonnet": TIER_SONNET, "opus": TIER_OPUS}
    model = model_map.get(tier, TIER_HAIKU)
    r = call_cloud(prompt, model=model, **{k: v for k, v in kwargs.items() if k in ("max_turns", "timeout_s", "cwd")})
    return {**r, "tier_used": tier}


def status() -> dict:
    """Diagnostic snapshot — used by /health/llm-router endpoint."""
    h = ollama_health()
    return {
        "ollama": {
            "url": OLLAMA_URL,
            "model": OLLAMA_MODEL,
            "up": h["ok"],
            "gpu_util_pct": h["gpu_util_pct"],
            "gpu_free_mb": h["gpu_free_mb"],
            "available_general": ollama_available("general"),
            "available_redaction": ollama_available("redaction"),
            "cooldown_active": h["cooldown_active"],
            "consecutive_failures": _state.consecutive_failures,
        },
        "cloud_tiers": {
            "haiku_model_id": TIER_HAIKU,
            "sonnet_model_id": TIER_SONNET,
            "opus_model_id": TIER_OPUS,
            "auth_env": AUTH_ENV,
            "subscription_auth": "ANTHROPIC_API_KEY" not in os.environ,
        },
    }
