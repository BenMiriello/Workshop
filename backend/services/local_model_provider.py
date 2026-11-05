"""A model server on the operator's own machine, behind the provider seam.

WHAT THIS BUYS. The operator points Workshop at something already listening on
localhost — llama.cpp's server, LM Studio, Ollama, mlx-lm, vLLM — configures the
base URL in Settings, and a turn runs on it. No new turn path, no fork of
`llm_router`: `services/llm_providers` is the seam and this is the second thing
plugged into it.

IT SPEAKS `anthropic_messages`, NOT OpenAI-compat, and that is a decision rather
than a preference. This platform's turn model IS the Anthropic one — effort
ladders, permission modes, typed thinking, tool_use blocks and the streaming
parser are all shaped by it — so translating out to Chat Completions and back
loses information at both ends. As of January 2026 the local runtimes a desktop
install would actually bundle all serve `/v1/messages` natively, which is what
removed the thing that used to force OpenAI-compat. `openai_chat` is still a
declared wire in `llm_providers` and is not implemented here; when it is, what it
COSTS is declared per provider (no cache control, no typed thinking with budget,
no token counting, tool arguments as strings) rather than guessed at by callers.

UNREACHABLE IS A STATE, NOT AN ERROR. This is the single biggest difference from
a cloud provider and it shapes every function below. A model server on a laptop
is off most of the time; a design that raises when it cannot connect makes the
model picker fail and the Providers screen red for the normal case. So `reach()`
returns a state, `models()` returns an empty catalog, and nothing raises until
somebody actually asks for a turn on a model that is not there.

NOTHING HERE IS INFERRED FROM THE WIRE FORMAT. The capabilities on a probed
model start FALSE and are set only by something observed: a context window the
server itself reported. Ollama's Messages implementation lacks `count_tokens`,
`cache_control` and `tool_choice`, and llama.cpp makes no strong claim of full
spec compatibility, so "it speaks anthropic_messages" is not a capability claim
about any of them. A reading never names a model: see `_capabilities`.

A PINNED MODEL THAT VANISHED IS NOT AN ERROR EITHER. A server restarted with a
different weight loaded has a different model list, and a thread pinned to the
old one must still answer. That degrade is `services/model_resolution`'s job and
it names the reason; this module's only duty is to report the catalog honestly
so the resolver can see the id is gone.

The design is `docs/platform-redesign/providers-credentials-and-limits-2026-08-23
.md` sections 4.1 to 4.3, stage 5.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from wsplatform import paths

from services import llm_contract, local_thinking

log = logging.getLogger("workshop.local_model_provider")

# The one id this provider ever has. There is one local server, because the
# thing being configured is "the model server on this machine".
PROVIDER_ID = "local_http"
PROVIDER_LABEL = "Local model server"

# No credential of any kind. Declared rather than left implicit, so a reader of
# `llm_providers` does not have to work out whether an empty `env_var` means
# "none" or "not filled in yet".
AUTH_NONE = "none"

# How long a probe result is trusted. The model picker reads the catalog on
# every composer fetch, so an uncached probe would put a TCP connect on a
# request path several times per page. Short enough that starting the server and
# reloading Settings shows it.
PROBE_TTL_S = 20.0

# A connect that has not landed in this long is a server that is not running.
# Deliberately small: this budget is spent on a page load.
CONNECT_TIMEOUT_S = 1.5
READ_TIMEOUT_S = 3.0

# The tier aliases the CLI resolves on its own for side requests. See
# `LocalHttpProvider.cli_env`.
_CLI_MODEL_ALIASES = (
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

# Cloud credentials a CLI child may carry, none of which a local turn uses.
_CLOUD_CREDENTIAL_ENV = frozenset({
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
})

# A turn is not a probe. It runs as long as generation takes on hardware the
# operator owns, which on a large local weight is minutes.
TURN_TIMEOUT_S = 900.0

# How long one reply on a local model may run, which is also what decides when
# the CLI compacts. Its threshold is `window - min(max_output, 20000) - 13000`
# (CLI 2.1.287; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower it). At the
# CLI's own 32000 default a 65k window compacts at 32.5k, half of it; at this
# value it compacts at 44.5k. A quarter of the window caps it on a small one.
LOCAL_MAX_OUTPUT_TOKENS = 8000

# How many times the CLI retries a failed model request before it gives up and
# ends the turn with the error (`CLAUDE_CODE_MAX_RETRIES`; its own default is
# 10, CLI 2.1.287). A local server that fails a request usually fails the next
# one the same way: the model was unloaded, or the server is down. Retried ten
# times per request, with each attempt able to wait out the CLI's ten-minute
# request timeout, one turn sat "working" for two hours on 2026-10-03.
# `services/model_failures.py` bounds the turn whatever the CLI does.
LOCAL_MAX_RETRIES = 2

# The hop lives in this backend, so a restart of it refuses connections for a
# few seconds. Six retries wait 31.5 s at their shortest (0.5 + 1 + 2 + 4 + 8 +
# 16), which rides that out; a server that is down is answered by the hop with
# a 503 at once, so the extra retries cost seconds, not the ten-minute waits.
HOP_MAX_RETRIES = 6


def hop_url() -> str | None:
    """Where a local turn's requests go to have their thinking set: this
    backend's `/local-llm`. None when the backend does not know its own port
    (`WORKSHOP_BACKEND_PORT`), in which case the turn goes straight to the
    server; guessing a port could reach another install's backend."""
    port = os.environ.get("WORKSHOP_BACKEND_PORT", "").strip()
    return f"http://127.0.0.1:{port}/local-llm" if port.isdigit() else None


def config_path() -> Path:
    """`.runtime/local-model-provider.json` — instance-local, never tracked.

    This is a fact about ONE INSTALLATION (a URL on one machine), so it lives in
    the runtime partition beside the other instance state rather than in tracked
    source or in a prompt layer.
    """
    return paths.env_path("WORKSHOP_LOCAL_MODEL_CONFIG",
                          "local-model-provider.json")


class InvalidBaseUrl(ValueError):
    """A paste error, caught before anything tries to connect."""


def normalize_base_url(value: str | None) -> str:
    """The origin to talk to, or InvalidBaseUrl.

    TRAILING `/v1` IS STRIPPED, deliberately. Every runtime documents its URL a
    different way — `http://127.0.0.1:11434/v1` for one, a bare origin for
    another — and both spellings are what an operator will paste. This module
    owns the path segments after the origin, so it takes either and stores the
    origin. A shape check only; whether anything is listening is `probe()`.
    """
    raw = (value or "").strip()
    if not raw:
        raise InvalidBaseUrl("a base URL is required")
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidBaseUrl(
            f"{parts.scheme!r} is not a URL scheme this can reach; use http or https")
    if not parts.netloc:
        raise InvalidBaseUrl("that URL has no host")
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return f"{parts.scheme}://{parts.netloc}{path}"


def load_config() -> dict:
    """`{"base_url": ..., "compaction_model": ...}`, or `{}` when nothing is
    configured.

    An unreadable or corrupt file reads as "not configured" rather than raising:
    the alternative is a backend that will not answer a model list because of a
    JSON typo in an optional setting.
    """
    try:
        data = json.loads(config_path().read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def base_url() -> str | None:
    """The configured origin, or None."""
    raw = load_config().get("base_url")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return normalize_base_url(raw)
    except InvalidBaseUrl:
        log.warning("local model provider: the stored base URL is not usable; "
                    "treating it as unconfigured")
        return None


def _write_config(key: str, value) -> None:
    """Set (or, on None, clear) one key of the config, keeping the others. The
    file goes when nothing is left in it."""
    path = config_path()
    config = load_config()
    if value is None:
        config.pop(key, None)
    else:
        config[key] = value
    if not config:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2))
    tmp.replace(path)


def set_base_url(value: str | None) -> str | None:
    """Configure (or, on None/empty, clear) the local model server.

    Forgets the probe cache, because the answer it holds is about a different
    server the moment this changes.
    """
    origin = normalize_base_url(value) if (value or "").strip() else None
    _write_config("base_url", origin)
    forget_probe()
    return origin


def compaction_model() -> str | None:
    """The local model a compaction is sent to instead of the chat's own, or
    None to keep the chat's. Installation-wide, beside the server's URL, because
    the hop that applies it (`routes/local_llm`) serves the CLI rather than a
    signed-in request and so has no owner to read a preference for."""
    raw = load_config().get("compaction_model")
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def compaction_candidates() -> dict[str, llm_contract.ModelInfo]:
    """The models a compaction may be sent to: those this server lists with a
    window, since the hop takes a model only when its window is known to fit."""
    return {m.id: m for m in probe().models if m.capabilities.context_tokens > 0}


def set_compaction_model(model: str | None) -> str | None:
    """Persist (or, on empty/None, clear) the compaction model. Raises
    ValueError unless the model is one this local server lists: a compaction is
    rewritten inside the hop to this server, so a model elsewhere cannot serve
    it."""
    value = (model or "").strip() or None
    if value is not None and value not in compaction_candidates():
        raise ValueError(f"{model!r} is not a model the local model server "
                         "lists with a context window")
    _write_config("compaction_model", value)
    return value


def scope_compaction_models() -> dict[str, str]:
    """The compaction model chosen per scope (`app:<slug>`), kept in the same
    file as the global one for the same reason: the hop that applies it has no
    owner to read a preference for."""
    raw = load_config().get("scope_compaction_models")
    if not isinstance(raw, dict):
        return {}
    return {k: v.strip() for k, v in raw.items()
            if isinstance(k, str) and k and isinstance(v, str) and v.strip()}


def set_scope_compaction_model(scope: str, model: str | None) -> str | None:
    """Persist (or, on empty/None, clear) the compaction model of one scope,
    under the rule of `set_compaction_model`."""
    value = (model or "").strip() or None
    if value is not None and value not in compaction_candidates():
        raise ValueError(f"{model!r} is not a model the local model server "
                         "lists with a context window")
    chosen = scope_compaction_models()
    if value is None:
        chosen.pop(scope, None)
    else:
        chosen[scope] = value
    _write_config("scope_compaction_models", chosen or None)
    return value


def compaction_model_for(scope: str | None) -> str | None:
    """The model a compaction in `scope` is sent to: the scope's own choice,
    else the global one, else None to keep the chat's model."""
    return (scope_compaction_models().get(scope or "")
            or compaction_model())


# ------------------------------------------------------------------- the probe


@dataclass(frozen=True)
class Probe:
    """One reading of what is at the base URL, and when it was taken."""

    reach: llm_contract.Reach
    models: tuple[llm_contract.ModelInfo, ...]
    at: float


_PROBE: dict[str, Probe] = {}
_REFRESHING = threading.Lock()


def forget_probe() -> None:
    """Drop the cached reading. Called whenever the URL changes and by the
    Refresh the Providers screen offers."""
    _PROBE.clear()


def _client(timeout_s: float):
    return httpx.Client(timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S))


def window_of(entry: dict) -> int:
    """The context window a model-list entry states, 0 when it states none."""
    for key in ("max_input_tokens", "context_length", "context_window",
                "max_context_length"):
        value = entry.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def window_env(window: int) -> dict[str, str]:
    """What tells the CLI a model's window, which is also what decides when
    it compacts (`LOCAL_MAX_OUTPUT_TOKENS`). Without a window the CLI assumes
    200k for a model it does not know, so its auto-compact waits for the server
    to overflow."""
    out = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(
        min(LOCAL_MAX_OUTPUT_TOKENS, window // 4) if window > 0
        else LOCAL_MAX_OUTPUT_TOKENS)}
    if window > 0:
        out["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(window)
    return out


def _capabilities(entry: dict) -> llm_contract.Capabilities:
    """What this model can do, from what the SERVER said.

    Everything starts False. `context_tokens` is taken from whichever field the
    server used — the Models API's `max_input_tokens`, or the `context_length`
    that the llama.cpp-derived servers report — and stays 0 when none is present,
    which every caller renders as unknown rather than as zero.

    `count_tokens` stays False because establishing it means a request that
    names the model, and a server that swaps weights per request (llama-swap)
    LOADS whatever a request names. Probing it on every catalog read loaded each
    model in turn, evicted whatever else held the card, and held the caller for
    the length of the load.
    """
    window = window_of(entry)
    return llm_contract.Capabilities(
        cache=False,
        thinking=False,
        tools=False,
        vision=False,
        count_tokens=False,
        context_tokens=window,
    )


def probe(force: bool = False) -> Probe:
    """Read the server: is it there, and what does it hold.

    Never raises and never lets an exception out of a request path. Both the
    success and the failure are cached for `PROBE_TTL_S`, because a failure that
    is not cached means one TCP connect per model-list read, several times per
    page, against a port with nothing on it.

    ONLY THE FIRST READING IS WAITED FOR. The catalog is read on the event loop
    by every composer fetch, so a reading past its TTL is still the answer while
    one background thread takes the next. A server that is slow to answer then
    costs nothing but freshness; it used to freeze every request in the backend
    for as long as the server took.
    """
    origin = base_url()
    if origin is None:
        return Probe(llm_contract.Reach(llm_contract.REACH_UNCONFIGURED,
                                         "No local model server is configured."),
                     (), time.time())
    cached = _PROBE.get(origin)
    if cached is None or force:
        return _remember(origin, _read(origin))
    if time.time() - cached.at >= PROBE_TTL_S:
        _refresh_soon(origin)
    return cached


def _remember(origin: str, result: Probe) -> Probe:
    _PROBE.clear()
    _PROBE[origin] = result
    return result


def _refresh_soon(origin: str) -> None:
    """Take a new reading off the caller's thread. One at a time: the lock is
    held by the refreshing thread, and a caller that finds it held moves on."""
    if not _REFRESHING.acquire(blocking=False):
        return

    def run() -> None:
        try:
            result = _read(origin)
            if base_url() == origin:
                _remember(origin, result)
        finally:
            _REFRESHING.release()

    threading.Thread(target=run, name="local-model-probe", daemon=True).start()


def _read(origin: str) -> Probe:
    """One live reading. Separated from the caching so a test can call it."""
    try:
        with _client(READ_TIMEOUT_S) as client:
            r = client.get(f"{origin}/v1/models")
            if r.status_code != 200:
                return Probe(
                    llm_contract.Reach(
                        llm_contract.REACH_UNREACHABLE,
                        f"{origin}/v1/models answered {r.status_code}."),
                    (), time.time())
            entries = model_entries(r.json())
            models = tuple(
                llm_contract.ModelInfo(
                    id=str(entry["id"]),
                    label=str(entry.get("display_name") or entry["id"]),
                    provider_id=PROVIDER_ID,
                    wire=llm_contract.WIRE_ANTHROPIC,
                    capabilities=_capabilities(entry),
                    # Effort is the thinking level, applied by the platform's
                    # own hop in front of the server (`routes/local_llm`), not
                    # by anything the server reads from the request itself. No
                    # hop, no ladder: the level would move nothing.
                    efforts=local_thinking.LEVELS if hop_url() else (),
                )
                for entry in entries if isinstance(entry.get("id"), str)
            )
    except Exception as e:
        return Probe(
            llm_contract.Reach(llm_contract.REACH_UNREACHABLE,
                                f"Could not reach {origin}: {e.__class__.__name__}."),
            (), time.time())
    if not models:
        return Probe(
            llm_contract.Reach(llm_contract.REACH_READY,
                                f"{origin} answered, but has no models loaded."),
            (), time.time())
    return Probe(llm_contract.Reach(llm_contract.REACH_READY, ""),
                 models, time.time())


def model_entries(payload: object) -> list[dict]:
    """The model list out of either shape a server may return: the Models API's
    `{"data": [...]}`, or a bare list."""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        return []
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    return []


# ---------------------------------------------------------------- the provider


class LocalHttpProvider:
    """One model server, reached over HTTP, speaking `anthropic_messages`."""

    id = PROVIDER_ID
    label = PROVIDER_LABEL
    transport = llm_contract.TRANSPORT_HTTP
    wire = llm_contract.WIRE_ANTHROPIC
    auth_mode = AUTH_NONE

    def __init__(self, origin: str) -> None:
        self.base_url = origin

    # THE AGENTIC HARNESS IS THE CLI, POINTED HERE. Tools, permission modes,
    # MCP servers and the streaming parser are all CLI concerns on this
    # platform, and the CLI speaks `anthropic_messages` to whatever base URL it
    # is given. So an agentic turn on a local model is the same `claude -p`
    # spawn as any other, with `cli_env` redirecting it; nothing about the turn
    # path forks. `run_turn` below stays the blocking, tool-less path.
    cli_harness = True

    # WHAT IT RUNS ON (services/resources). A server on this machine shares its
    # accelerator with everything else here, so a turn on it is recorded as
    # holding "gpu" unless the config names something else (a CPU-only server
    # declares `"resources": []`).
    @property
    def resources(self) -> tuple[str, ...]:
        raw = load_config().get("resources")
        if isinstance(raw, list):
            return tuple(str(r) for r in raw if str(r).strip())
        return ("gpu",)

    def models(self) -> list[llm_contract.ModelInfo]:
        return list(probe().models)

    def cli_env(self, model: str, env: dict) -> dict:
        """`env` for a CLI turn that runs `model` on this server.

        EVERY MODEL ALIAS THE CLI USES IS THIS MODEL. Besides `--model`, the CLI
        sends side requests (titles, summaries, subagents) under its own tier
        aliases, and a local server answers only for what it has loaded; an
        alias left at its default asks for a model that is not there.

        The subscription credentials are REMOVED, not just outranked. The auth
        token below wins over them, but nothing here should hand a cloud
        credential to a process whose every request goes to a server the
        operator may not control.

        ATTRIBUTION OFF: the CLI otherwise puts a per-request header block at the
        top of the system prompt, which changes every turn and so misses the
        server's prompt cache from the first token.
        """
        out = {k: v for k, v in env.items() if k not in _CLOUD_CREDENTIAL_ENV}
        hop = hop_url()
        out.update({
            "ANTHROPIC_BASE_URL": hop or self.base_url,
            # Any non-empty value: a local server checks no credential, and the
            # CLI refuses to start without one.
            "ANTHROPIC_AUTH_TOKEN": PROVIDER_ID,
            "ANTHROPIC_MODEL": model,
            **{alias: model for alias in _CLI_MODEL_ALIASES},
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_MAX_RETRIES": str(
                HOP_MAX_RETRIES if hop else LOCAL_MAX_RETRIES),
        })
        if hop:
            out["CLAUDE_CODE_GATEWAY_HINT_HEADERS"] = "1"
        # The window the server itself reported, when it reported one. A
        # server may report none for a model it has not loaded lately; an app
        # gateway in front of it can say instead (`app_chat_gateway`).
        window = next((m.capabilities.context_tokens for m in self.models()
                       if m.id == model), 0)
        out.update(window_env(window))
        return out

    def reach(self) -> llm_contract.Reach:
        return probe().reach

    def run_turn(self, prompt: str, *, model: str, **kw) -> dict:
        """One blocking turn, in `llm_router.call_cloud`'s own return shape.

        THE SHAPE IS THE POINT. Every caller of `call_cloud` reads
        `{"text", "model", "elapsed_s", "exit_code", "stop_reason",
        "recovered"}`, so a turn that lands here is indistinguishable to them
        from one that ran on the CLI. That is what "no change to the turn path"
        means concretely.

        A TOOL-BEARING TURN IS REFUSED, LOUDLY. Tools and permission modes are
        CLI FLAGS on this platform, so there is nothing here to carry them, and
        quietly dropping them would run an agent with no hands and report
        success. An agentic turn on this provider runs through the CLI instead
        (`cli_env`); this is the backstop for a caller that constructed a
        blocking one by hand.

        `base_url` and `headers` send the request somewhere that stands in for
        this server, with what it needs to know: an app's gateway in front of
        it, told which chat the request is for (`services/own_model`).
        `thinking=False` asks the model to answer without reasoning first.
        """
        if kw.get("allowed_tools") or kw.get("mcp_servers"):
            raise llm_contract.ProviderCallFailed(
                f"{PROVIDER_LABEL} has no tool harness; a turn needing tools "
                "cannot run on it")
        max_turns = kw.get("max_turns")
        if max_turns is not None and int(max_turns) > 1:
            # One request, one response. Nothing here loops.
            log.info("local model turn: --max-turns %s has no meaning over HTTP",
                     max_turns)
        payload = {
            "model": model,
            "max_tokens": int(kw.get("max_tokens") or 8192),
            "messages": [{"role": "user", "content": prompt}],
        }
        if kw.get("thinking") is False:
            # Both spellings: the Messages API's own, and the chat-template
            # switch the llama.cpp and vLLM servers read (llama.cpp's
            # `/v1/messages` ignores `thinking.type: disabled`).
            payload["thinking"] = {"type": "disabled"}
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        origin = kw.get("base_url") or self.base_url
        headers = {**(kw.get("headers") or {}), "anthropic-version": "2023-06-01"}
        start = time.time()
        try:
            with _client(float(kw.get("timeout_s") or TURN_TIMEOUT_S)) as client:
                r = client.post(f"{origin}/v1/messages", json=payload,
                                headers=headers)
        except Exception as e:
            raise llm_contract.ProviderCallFailed(
                f"{PROVIDER_LABEL} at {origin} did not answer: "
                f"{e.__class__.__name__}") from e
        elapsed = time.time() - start
        if r.status_code != 200:
            raise llm_contract.ProviderCallFailed(
                f"{PROVIDER_LABEL} answered {r.status_code}: "
                f"{_error_detail(r)}")
        try:
            body = r.json()
        except ValueError as e:
            raise llm_contract.ProviderCallFailed(
                f"{PROVIDER_LABEL} answered 200 with a body that is not JSON"
            ) from e
        text = _text_of(body)
        if not text:
            raise llm_contract.ProviderCallFailed(
                f"{PROVIDER_LABEL} answered with no assistant text "
                f"(stop_reason={body.get('stop_reason')})")
        return {
            "text": text,
            "model": str(body.get("model") or model),
            "elapsed_s": round(elapsed, 3),
            "exit_code": 0,
            "stop_reason": body.get("stop_reason"),
            "recovered": False,
            # The caller meters. See `LlmProvider.run_turn` in `llm_contract`.
            "usage": body.get("usage"),
        }


def _error_detail(response) -> str:
    """The SERVER's own words where it gave any. "invalid model" tells the
    operator what to do and "request failed" does not."""
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str):
            return err[:300]
    except Exception:
        pass
    return (response.text or "")[:300] or str(response.status_code)


def _text_of(body: object) -> str:
    """The assistant text out of a Messages response's content blocks."""
    if not isinstance(body, dict):
        return ""
    blocks = body.get("content")
    if isinstance(blocks, str):
        return blocks.strip()
    if not isinstance(blocks, list):
        return ""
    parts = [b.get("text", "") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(parts).strip()


def configured_provider() -> LocalHttpProvider | None:
    """The provider, when a base URL is configured. None otherwise.

    Returned even when the server is unreachable, because "configured but not
    running" is a state the operator has to be able to see on the Providers
    screen, and a provider that disappears from the list when the process stops
    cannot show it.
    """
    origin = base_url()
    return None if origin is None else LocalHttpProvider(origin)
