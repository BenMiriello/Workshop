"""LLM router — cloud calls via `claude -p`, plus an explicit local (Ollama) path.

The workshop autonomy tier-ladder (auto-classify a task and route the cheap ones
to Ollama) has been retired: autonomous work runs through `claude -p` (see
scripts/ralph.sh). What remains is the two explicit call paths:

    Local (Ollama qwen2.5:32b)  used only when a caller ASKS for it — the
                                redaction classifier and the /relay/local
                                passthrough. Health/GPU gated so a saturated or
                                down GPU raises OllamaUnavailable instead of
                                blocking; the caller decides the fallback.
    Cloud (Claude via claude -p)  Haiku / Sonnet / Opus / Fable by explicit model.

Public API:
    call_local(prompt, **kw) -> dict      # raises OllamaUnavailable if gated out
    call_cloud(prompt, model, **kw)       # subprocess to `claude -p --model <model>`
    ollama_health() / ollama_available()  # health probes (redaction + status)

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
from typing import Any

import httpx

from services import agent_slots


log = logging.getLogger("workshop.llm_router")


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:32b")
# Cloud chat shells out to the `claude` CLI. Per-deployment and portable: honor
# an explicit CLAUDE_CLI, else resolve `claude` on PATH, else bare "claude".
# Under systemd --user the PATH can be minimal (no ~/.local/bin), so a local
# build should set CLAUDE_CLI explicitly on its service. See
# docs/simonsays-local-instance.md.
CLAUDE_CLI = os.environ.get("CLAUDE_CLI") or shutil.which("claude") or "claude"
# Optional extra env for the subprocess (e.g. an API key). Missing file is fine —
# a CLI already logged in (its own ~/.claude creds) needs nothing here.
#
# Resolved through `auth_env` rather than held here: this module used to reach
# the file under its own second name (CLAUDE_AUTH_ENV) and its own default
# directory, which is how one file came to have two addresses.
def auth_env_path() -> str:
    from services import auth_env
    return str(auth_env.auth_env_path())

# Tier model handles (claude -p --model arg)
TIER_HAIKU = "claude-haiku-4-5-20251001"
TIER_SONNET = "claude-sonnet-4-6"
TIER_SONNET_5 = "claude-sonnet-5"
TIER_OPUS = "claude-opus-4-8"
TIER_OPUS_5 = "claude-opus-5"
TIER_FABLE = "claude-fable-5"

# Short tier names, for anything DECLARED rather than coded. A definition file
# says `model: sonnet` and keeps saying it when the handle behind that tier
# rotates; writing "claude-sonnet-4-6" into a declaration would pin a file the
# operator edits by hand to a string only this module should know.
TIERS = {
    "haiku": TIER_HAIKU,
    "sonnet": TIER_SONNET,
    "sonnet-5": TIER_SONNET_5,
    "opus": TIER_OPUS,
    "opus-5": TIER_OPUS_5,
    "fable": TIER_FABLE,
}


def tier(name: object, default: str = TIER_HAIKU) -> str:
    """Resolve a short tier name to a model handle. An unknown or absent name
    falls back to `default` rather than passing an unusable string to the CLI.
    A full handle is returned unchanged, so either spelling works."""
    if not isinstance(name, str) or not name.strip():
        return default
    n = name.strip().lower()
    if n in TIERS:
        return TIERS[n]
    return name.strip() if n.startswith("claude-") else default


# Health thresholds
OLLAMA_HEALTH_TIMEOUT_S = 3
OLLAMA_REQUEST_TIMEOUT_S = 60
OLLAMA_REDACTION_TIMEOUT_S = 180
GPU_BUSY_UTIL_PCT = 85

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
    from services import auth_env

    # THE LICENSING BOUNDARY, enforced where the credential is actually spent.
    #
    # Every turn in this process shares one subscription token read from one
    # file. That is correct for one person on their own machine and is the
    # violation itself in a hosted deployment: the second user's turn would run
    # on the first user's subscription, which Anthropic prohibits and blocks
    # server side. This path has no notion of WHOSE turn it is, so it cannot be
    # made safe for multiple users; it can only refuse.
    #
    # The refusal lives here rather than only in the new seam
    # (services/model_credentials) because this is the function that hands the
    # token to a child TODAY. Until stage 4 moves the call sites, flipping the
    # deployment mode without this check would leave the old path quietly
    # serving everyone from the operator's account.
    from services import model_credentials

    if model_credentials.is_multi_user():
        raise RuntimeError(
            "the `claude -p` subscription path cannot serve a multi-user "
            "deployment: a Claude subscription may only be used by the person "
            "who bought it. Resolve a per-user credential through "
            "services.model_credentials instead. See "
            "docs/platform-redesign/model-access-and-byok-2026-08-11.md")

    env = os.environ.copy()
    # An MCP server that loads its own credentials from a file is shadowed by
    # whatever the parent process already has set for the same names: dotenv
    # will not override an existing env var, so an empty or stale value in
    # uvicorn's environment silently wins over the real one on disk. Each app
    # declares the names it owns (AppSpec.secret_env) and they are stripped from
    # the child, so the server reads its file. The platform does not need to
    # know which app that is.
    from services import app_registry

    for _k in app_registry.installed_secret_env():
        env.pop(_k, None)
    auth_env.apply_to(env)
    # AFTER the overlay, not before. This install is subscription-only, and the
    # strip used to run first — so an ANTHROPIC_API_KEY sitting in the auth file
    # would have been put straight back and every spawned turn would have billed
    # against it. Nothing else here can undo this line.
    env.pop("ANTHROPIC_API_KEY", None)
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
        # A sidechain event is a SUBAGENT's output, not the turn's reply. Without
        # this the subagent's interim text is concatenated into the answer the
        # operator reads. See build_streaming._parse_stream_event.
        if ev.get("parent_tool_use_id"):
            continue
        msg = ev.get("message") or {}
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text") or ""
                if txt:
                    parts.append(txt)
    return "\n".join(parts).strip()


def _summarize_tool_input(inp) -> str:
    """One-line preview of a tool call's input for the UI chip. Mirrors
    build_streaming._summarize_tool_input (kept local to avoid an import cycle:
    build_streaming imports llm_router)."""
    if not isinstance(inp, dict):
        return str(inp)[:300]
    for key in ("file_path", "path", "pattern", "command", "url", "query", "prompt"):
        if key in inp:
            v = str(inp[key])
            return v if len(v) <= 300 else v[:297] + "…"
    keys = ",".join(list(inp.keys())[:4])
    return f"({keys})"


def _extract_segments_from_stream_json(stdout: str) -> list[dict]:
    """Walk stream-json events into the ordered text/tool segment shape the chat
    UI renders (`AssistantTurn`): {"t":"text","text":...} / {"t":"tool","name",
    "input"}. Consecutive text blocks merge into one segment, matching the build
    streaming parser. This is what lets a data/assistant chat surface its tool
    use (web search, workshop queries) as chips instead of a flat text answer."""
    segs: list[dict] = []
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
        # Sidechain (subagent) steps are the delegate's work, not this turn's.
        # The blocking path renders one chip for the Agent call itself; the
        # streaming path (build_streaming) nests the steps under it.
        if ev.get("parent_tool_use_id"):
            continue
        msg = ev.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                txt = block.get("text") or ""
                if not txt:
                    continue
                if segs and segs[-1].get("t") == "text":
                    segs[-1]["text"] += txt
                else:
                    segs.append({"t": "text", "text": txt})
            elif block.get("type") == "tool_use":
                segs.append({
                    "t": "tool",
                    "name": block.get("name") or "?",
                    "input": _summarize_tool_input(block.get("input") or {}),
                })
    return segs


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
    "Answer in pure text only. Do not attempt to call any tool."
)
# Surfaced at the write boundary (when Write or Edit is in the allowed toolset).
# Reminder only — never blocks. Complements the work-placement rule in the
# persona (which fires at turn start) by re-anchoring the tradeoff at the moment
# the agent is about to write.
_WRITE_BOUNDARY_NUDGE = (
    "WRITE PLACEMENT REMINDER: you can call Write or Edit directly. "
    "A single small file edit inline is fine. "
    "If you are about to touch multiple files, that usually wants the queue's "
    "review gate: emit a task-proposal card instead, which adds touches "
    "enforcement, a reviewer pass, and a receipt. "
    "Your call. This is a reminder, not a restriction."
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

# MCP server launch specs available to chat subprocesses are no longer hardcoded
# here: each is registered by the app that needs it (e.g. the plaid server by the
# finance/budget apps) and is available only when that app is installed. See
# services/app_registry.installed_mcp_servers. A server appears only if some
# provider's allowed_tools needs it AND its app is installed. (This landed the
# migration the previous NOTE anticipated — queue f963c8e.)
def _mcp_server_specs() -> dict[str, dict]:
    # Lazy import breaks the app_registry <-> llm_router module cycle.
    from services import app_registry
    return app_registry.installed_mcp_servers()


# ---- per-chat MCP selection ------------------------------------------------
#
# A chat may run with a SUBSET of the machine's MCP servers. Three functions,
# all pure, because the thing that must not be got wrong here is the argv, and
# an argv is only trustworthy if it can be asserted on without spawning a CLI
# (tests/test_mcp_selection.py).
#
# THE ONE INVARIANT: `selection is None` means the chat has never chosen, and
# every function below must then return exactly what the caller would have got
# before per-chat selection existed. Default is ALL servers, never none — a
# chat nobody has configured must not quietly lose its tools.


def select_names(available: list[str], selection: list[str] | None) -> list[str]:
    """`available`, narrowed to the chat's chosen servers, order preserved.

    A FILTER, not a set: a name in `selection` that is not in `available` adds
    nothing. That direction matters. The selection is written by a UI listing
    what the machine had at the time; a server that has since been removed, or
    that this particular code path never offered, must not be conjured into a
    turn by a stale checkbox.
    """
    if selection is None:
        return list(available)
    keep = set(selection)
    return [name for name in available if name in keep]


def select_servers(available: dict[str, dict], selection: list[str] | None) -> dict[str, dict]:
    """`select_names`, over a name -> launch-spec mapping."""
    kept = set(select_names(list(available), selection))
    return {name: spec for name, spec in available.items() if name in kept}


def filter_allowed_tools(allowed_tools: list[str], servers: dict[str, dict],
                         selection: list[str] | None) -> list[str]:
    """`allowed_tools` with the tools of disabled MCP servers removed.

    `mcp__<server>__<tool>` is the CLI's naming, so the server a tool belongs to
    is readable off the tool name. Built-ins (anything without the prefix) are
    untouched — the selection governs MCP, not the toolset.

    Unchanged when nothing was chosen, so a chat that never touched the control
    keeps the byte-identical allowlist it had.
    """
    if selection is None:
        return list(allowed_tools)
    kept: list[str] = []
    for t in allowed_tools:
        if not t.startswith("mcp__"):
            kept.append(t)
            continue
        parts = t.split("__")
        if len(parts) >= 3 and parts[1] in servers:
            kept.append(t)
    return kept


def strict_mcp_flags(servers: dict[str, dict]) -> list[str]:
    """`--mcp-config <json> --strict-mcp-config` for exactly `servers`.

    Strict is the whole mechanism and the whole hazard: the turn gets these
    servers and nothing else, so an omission is silent. Every caller that builds
    a subset goes through here so there is one place the JSON shape is decided.

    THE ORDER IS DELIBERATE AND IT IS NOT COSMETIC. `--mcp-config` is VARIADIC
    (`<configs...>` — "JSON files or strings, space-separated"), so it keeps
    eating arguments until it meets something starting with `--`. Measured
    2026-08-10: `--mcp-config '{...}' hi` made the CLI look for a config file
    named `hi` and exit 1 with "MCP config file not found". Emitting
    `--strict-mcp-config` AFTER the JSON makes this group terminate itself, so
    it stays correct wherever a caller splices it in — including in front of a
    positional prompt, which is how call_cloud builds its argv.
    """
    return ["--mcp-config", json.dumps({"mcpServers": servers}), "--strict-mcp-config"]


def resolve_agentic_selection(selection: list[str], cwd: str) -> dict[str, dict]:
    """The chosen servers' real launch specs, for a full-agentic turn.

    A selected server whose spec cannot be found is DROPPED and logged, because
    the alternative is worse in both directions: inventing a spec launches
    something the operator did not configure, and aborting the turn would make a
    settings screen able to break a chat. The UI does not offer a toggle for a
    server with no spec (`mcp_registry.annotate` -> `spec_available: false`), so
    reaching this branch means the config changed under a saved selection.
    """
    from services import mcp_registry
    specs = mcp_registry.resolve_specs(cwd)
    chosen: dict[str, dict] = {}
    for name in selection:
        entry = specs.get(name)
        if entry is None:
            log.warning("mcp selection: no launch spec for %r in %s; it cannot be "
                        "re-declared, so this turn runs without it", name, cwd)
            continue
        chosen[name] = entry["spec"]
    return chosen


def agentic_mcp_flags(selection: list[str] | None, cwd: str) -> list[str]:
    """MCP flags for a FULL-AGENTIC turn that has chosen a subset of servers.

    The agentic path normally sends no MCP flags at all and inherits whatever
    the CLI's own configuration provides. There is no CLI flag for "all of that,
    minus one", so honouring a selection means switching that turn into strict
    mode and re-declaring the survivors from their real config
    (services/mcp_registry — read off ~/.claude.json, <cwd>/.mcp.json and the
    installed plugins, never reconstructed from `claude mcp list` output).

    Returns [] when the chat has never chosen, which is what keeps every
    existing turn byte-identical.
    """
    if selection is None:
        return []
    return strict_mcp_flags(resolve_agentic_selection(selection, cwd))


def tool_flags(
    *,
    tools_disabled: bool = False,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    mcp_servers: list[str] | None = None,
    permission_mode: str = "bypassPermissions",
    mcp_selection: list[str] | None = None,
    cwd: str = "",
) -> tuple[list[str], dict, bool]:
    """The tool / MCP / permission CLI flags for one of the three tool modes,
    shared by call_cloud (the blocking data path) and build_streaming (streaming
    turns) so the modes never drift. Returns (flags, env_updates,
    block_mcp_startup). Does NOT set --output-format, --max-turns, --model, or
    session flags: the caller owns those (call_cloud uses text/stream-json per
    mode; build_streaming is always stream-json).

    - tools_disabled -> hardened tools-off (no permission-mode; there's nothing
      to permit).
    - allowed_tools  -> narrow surface: only the named MCP tools + built-ins,
      bypassPermissions (the allowlist is the gate), block on MCP startup when a
      server is actually spawned.
    - neither        -> full agentic inheritance; NO flags here (the caller adds
      its own permission-mode: call_cloud keeps the CLI default, build_streaming
      sets bypassPermissions for its headless turns).

    `mcp_selection` is the CHAT's own per-thread choice of servers, and it is a
    filter over whichever set the mode above would have used — never an addition
    to it. None means the chat has never chosen, which must behave exactly as it
    did before this parameter existed. See `select_servers`. `cwd` is only read
    on the full-agentic path, where the surviving servers have to be re-declared
    from the configuration that directory actually sees."""
    if tools_disabled:
        return ([
            "--strict-mcp-config",
            "--mcp-config", _EMPTY_MCP_CONFIG_JSON,
            "--tools", "",
            "--append-system-prompt", _TOOLS_OFF_SYSTEM_NUDGE,
        ], {}, False)
    if allowed_tools:
        _specs = _mcp_server_specs()
        servers_subset = select_servers(
            {name: _specs[name] for name in (mcp_servers or []) if name in _specs},
            mcp_selection,
        )
        # A disabled server's tools must not stay on the allowlist: leaving
        # `mcp__plaid__*` in --allowedTools while plaid is absent from the
        # strict config names a tool that cannot exist, which is at best noise
        # in the prompt and at worst an invitation to hallucinate the call.
        kept_tools = filter_allowed_tools(allowed_tools, servers_subset, mcp_selection)
        builtin_tools = [t for t in kept_tools if not t.startswith("mcp__")]
        flags = strict_mcp_flags(servers_subset)
        flags += (["--tools", *builtin_tools] if builtin_tools else ["--tools", ""])
        # The allowlist is the gate on this path, so the mode defaults to bypass
        # and every non-chat caller (voice, jobs, blocking data turns) keeps
        # exactly the posture it had. A chat that has chosen a mode passes it in.
        #
        # An empty allowlist is written as one explicit empty argument rather
        # than omitted: `--allowedTools` followed immediately by
        # `--permission-mode` would eat the next flag as its value, since the
        # option is variadic. Disabling every server a narrow chat had is a real
        # state (it leaves the built-ins), and it must not corrupt the argv.
        flags += ["--allowedTools", *(kept_tools or [""]), "--permission-mode", permission_mode]
        # Deny list applies AFTER the allow list, which is what makes it a
        # reversibility gate rather than a second allowlist: a maker keeps Bash
        # and loses exactly the commands that cannot be undone. See
        # agent_authority.IRREVERSIBLE_PATTERNS.
        if disallowed_tools:
            flags += ["--disallowedTools", *disallowed_tools]
        _write_tools = {"Write", "Edit", "MultiEdit"}
        if _write_tools & set(kept_tools):
            flags += ["--append-system-prompt", _WRITE_BOUNDARY_NUDGE]
        env = dict(_MCP_BLOCK_STARTUP_ENV) if servers_subset else {}
        return (flags, env, bool(servers_subset))
    # Full agentic. No flags unless the chat has chosen a subset — see
    # agentic_mcp_flags for why a subset has to become strict mode here.
    if mcp_selection is not None:
        chosen = resolve_agentic_selection(mcp_selection, cwd)
        # Same reason as the narrow path: when a server IS being spawned for
        # this turn, the CLI must finish the stdio handshake before the first
        # model turn, or the model sees `tools: []` and either says it has none
        # or fabricates a call.
        env = dict(_MCP_BLOCK_STARTUP_ENV) if chosen else {}
        return (strict_mcp_flags(chosen), env, bool(chosen))
    return ([], {}, False)


def call_cloud(
    prompt: str,
    *,
    model: str = TIER_HAIKU,
    max_turns: int | None = None,
    timeout_s: float = 600,
    # None = "resolve it now" (paths.chat_cwd). Not a default expression: this is
    # a function default, evaluated once at import, so the old
    # `os.environ.get("WORKSHOP_CHAT_CWD", str(Path.home()))` also froze the
    # answer for the life of the process — and froze it to the account's home
    # whenever the env var was unset.
    cwd: str | None = None,
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

    `max_turns=None` (the default) passes NO `--max-turns` to the CLI: the turn
    runs until the model is done. A step cap is only ever appropriate for the
    single-shot internal helpers (naming a thread, classifying a note) that pass
    an explicit small number; it is NOT a control for conversational or agentic
    work, which must be free to keep going. Spend is bounded by the usage cap,
    not by counting steps.

    Returns: {"text": str, "model": str, "elapsed_s": float, "exit_code": int,
              "stop_reason": str | None, "recovered": bool}
    `recovered` is True if exit_code != 0 but we salvaged assistant text from
    the transcript (only meaningful for the stream-json paths).
    """
    if cwd is None:
        from wsplatform import paths as _paths
        cwd = str(_paths.chat_cwd())
    # THE PROMPT GOES ON STDIN, NOT IN ARGV.
    #
    # `[CLAUDE_CLI, "-p", prompt]` fails with E2BIG — "[Errno 7] Argument list
    # too long" — once the prompt passes Linux's MAX_ARG_STRLEN, which is 128KB
    # for a SINGLE argument regardless of how much room the rest of the argv
    # has. Every caller that summarizes something long crosses it: chat
    # compaction, distillation, any digest of a real transcript.
    #
    # And it crossed it silently. `_compact_thread` catches, logs a warning and
    # returns the thread untouched, the endpoint answers 200, and the chat says
    # "Compacted. Earlier messages are now a summary." Measured on 2026-08-12:
    # thread 4102a2fe, a 397KB log, `chat compaction failed ... Argument list too
    # long` at 17:43:43Z, `boundary_seq` still 0 afterwards. So compaction failed
    # on exactly the threads big enough to need it, and said it had worked.
    #
    # `claude -p` with no prompt argument reads it from stdin, which has no such
    # limit.
    cmd: list[str] = [
        CLAUDE_CLI, "-p",
        "--model", model,
    ]
    use_stream_json = False
    # Whether to force the CLI to block on MCP-server startup before the first
    # model turn (fixes the handshake race — see _MCP_BLOCK_STARTUP_ENV). Only
    # the narrow-tool path sets this; it spawns the server the model must use.
    block_mcp_startup = False
    # Tool/MCP/permission flags are shared with build_streaming via tool_flags()
    # so the three modes never drift. call_cloud owns the output-format decision
    # per mode (stream-json for tools-off/narrow so partial text is recoverable;
    # text for full agentic).
    cap = [] if max_turns is None else ["--max-turns", str(max_turns)]
    _tflags, _tenv, block_mcp_startup = tool_flags(
        tools_disabled=tools_disabled, allowed_tools=allowed_tools, mcp_servers=mcp_servers,
    )
    if tools_disabled:
        # Tools-off path: single Q&A round-trip, no agentic loop to bound.
        cmd.extend(["--output-format", "stream-json", "--verbose", "--max-turns", "1", *_tflags])
        use_stream_json = True
    elif allowed_tools:
        # Narrow-tool path: uncapped unless the caller asks for a cap (the
        # one-shot helpers do). Stream-json for partial-text recovery.
        cmd.extend(["--output-format", "stream-json", "--verbose", *cap, *_tflags])
        use_stream_json = True
    else:
        cmd.extend(["--output-format", "text", *cap])

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

    # 0 (or negative) means NO wall clock — the call runs until the model is
    # done. subprocess.run treats timeout=0 as "already expired", so the value
    # has to become None here rather than being passed through; getting that
    # wrong kills every turn instantly.
    _timeout = float(timeout_s or 0)
    _timeout = _timeout if _timeout > 0 else None
    # Bound how many of these run at once. The slot is taken BEFORE the spawn and
    # the clock starts AFTER it, so time spent waiting for a slot is never
    # charged to the model call or counted against its timeout.
    with agent_slots.helper_slot():
        start = time.time()
        try:
            proc = subprocess.run(
                cmd, env=env, cwd=cwd, input=prompt,
                capture_output=True, text=True, timeout=_timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise CloudCallFailed(f"claude -p timed out after {_timeout}s: {e}") from e
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
                # Ordered text/tool segments so a data/assistant chat can render
                # its tool use (web search, workshop queries) as chips, the same
                # way a build turn does. Empty when the model emitted plain text.
                "segments": _extract_segments_from_stream_json(proc.stdout),
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


# -------------------------------------------------------------------- Diagnostics


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
            "auth_env": auth_env_path(),
            "subscription_auth": "ANTHROPIC_API_KEY" not in os.environ,
        },
    }


# -------------------------------------------------------------- mcp-config CLI
#
# `python -m services.llm_router mcp-config --server workshop` writes a
# `--mcp-config` file and prints its path.
#
# WHO THIS IS FOR. Shell callers that spawn `claude -p` THEMSELVES rather than
# through `call_cloud` — scripts/ralph.sh is the one that matters. It ran with no
# MCP flags at all, so the unattended worker had no `mcp__workshop__search` while
# every chat surface did. The launch specs live in Python (app_registry), so the
# only way for a bash caller to get a correct one is to ask Python for it; a
# second copy in the shell is a copy that will be wrong.
#
# THE OWNER IS THE WHOLE POINT, and it is the way this ships broken while every
# smoke test passes. The workshop MCP server is OWNER-SCOPED
# (backend/mcp/workshop_query_server.py `_pin_owner`): the semantic index, the
# chat logs and the knowledge root all resolve under `.runtime/users/<owner>/`.
# A worker running as the service user with no owner pinned reaches an EMPTY
# index, and an empty index is not an error — the tool answers "nothing has been
# written about it", the run succeeds, and nothing anywhere says the search saw
# no data. So the owner is resolved HERE, where auth_store can answer, and
# written into the spec's env as `WORKSHOP_QUERY_OWNER`.
#
# NOT `--strict-mcp-config`. That flag belongs to callers building a closed
# subset. The worker is full-agentic by design, and strict mode would silently
# drop every other server the tree legitimately configures. The emitted file is
# ADDITIVE: `--mcp-config <path>` on its own merges with the CLI's own
# configuration.

def mcp_config_json(servers: list[str] | None = None,
                    *, owner_id: int | None = None,
                    context_key: str | None = None) -> str:
    """The `--mcp-config` document for `servers`, with the owner and the
    caller's scope pinned.

    `servers=None` means every installed server. An unknown name is an error
    rather than a silent omission: a caller asking for `workshop` and getting an
    empty config back would spawn a worker with no search and no complaint.
    """
    from services import app_registry, auth_store, ownership

    # Pin BEFORE building the specs. `_platform_mcp_servers` reads
    # `ownership.owner_or_none()` to fill WORKSHOP_QUERY_OWNER, and in a CLI
    # process nothing has set it, so without this the env var is simply absent.
    oid = owner_id if owner_id is not None else auth_store.operator_id()
    if oid is not None:
        ownership.set_current_owner(int(oid))
    # Same seam, for scope: a caller that knows which chat this is
    # (build_streaming._build_cmd) hands its thread's context_key down
    # explicitly. A caller that does not -- scripts/ralph.sh's CLI use, or any
    # other background pass with no chat behind it -- leaves this None, and
    # `_platform_mcp_servers` omits WORKSHOP_QUERY_SCOPE rather than guessing;
    # the subprocess's own fallback ("workshop", never "development") applies.
    # See `app_registry.set_current_query_scope`'s docstring for why this used
    # to always resolve to "workshop": it was reading THIS process's own
    # environment, which nothing had ever set.
    app_registry.set_current_query_scope(context_key)
    available = app_registry.installed_mcp_servers()
    if servers is None:
        chosen = dict(available)
    else:
        missing = [s for s in servers if s not in available]
        if missing:
            raise KeyError(f"no MCP launch spec for: {', '.join(missing)}")
        chosen = {s: available[s] for s in servers}
    return json.dumps({"mcpServers": chosen}, indent=2)


def _main(argv: list[str]) -> int:
    import argparse
    import tempfile

    ap = argparse.ArgumentParser(prog="llm_router")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("mcp-config",
                        help="write a --mcp-config file and print its path")
    sp.add_argument("--server", action="append", dest="servers", default=None,
                    help="server name; repeatable. Default: every installed one.")
    sp.add_argument("--owner", type=int, default=None,
                    help="app user id whose data the servers see. "
                         "Default: the operator, which is correct for a "
                         "background loop (see services/ownership).")
    sp.add_argument("--stdout", action="store_true",
                    help="print the JSON itself instead of a path to it")
    args = ap.parse_args(argv)

    doc = mcp_config_json(args.servers, owner_id=args.owner)
    if args.stdout:
        print(doc)
        return 0
    # A real file, not a JSON string argument: `--mcp-config` is variadic, and a
    # brace-laden argv element is the shape that broke a caller before (see
    # strict_mcp_flags). The caller owns the cleanup; these are small and land in
    # the system temp dir.
    fd = tempfile.NamedTemporaryFile("w", prefix="workshop-mcp-", suffix=".json",
                                     delete=False)
    with fd:
        fd.write(doc)
    print(fd.name)
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    _BACKEND = str(_Path(__file__).resolve().parent.parent)
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    raise SystemExit(_main(sys.argv[1:]))
