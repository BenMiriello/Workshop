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
server itself reported, a `count_tokens` endpoint that actually answered. Ollama's
Messages implementation lacks `count_tokens`, `cache_control` and `tool_choice`,
and llama.cpp makes no strong claim of full spec compatibility, so "it speaks
anthropic_messages" is not a capability claim about any of them.

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
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from wsplatform import paths

from services import llm_providers

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
READ_TIMEOUT_S = 10.0

# A turn is not a probe. It runs as long as generation takes on hardware the
# operator owns, which on a large local weight is minutes.
TURN_TIMEOUT_S = 900.0


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
    """`{"base_url": ...}`, or `{}` when nothing is configured.

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


def set_base_url(value: str | None) -> str | None:
    """Configure (or, on None/empty, clear) the local model server.

    Forgets the probe cache, because the answer it holds is about a different
    server the moment this changes.
    """
    path = config_path()
    if not (value or "").strip():
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        forget_probe()
        return None
    origin = normalize_base_url(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"base_url": origin}, indent=2))
    tmp.replace(path)
    forget_probe()
    return origin


# ------------------------------------------------------------------- the probe


@dataclass(frozen=True)
class Probe:
    """One reading of what is at the base URL, and when it was taken."""

    reach: llm_providers.Reach
    models: tuple[llm_providers.ModelInfo, ...]
    at: float


_PROBE: dict[str, Probe] = {}


def forget_probe() -> None:
    """Drop the cached reading. Called whenever the URL changes and by the
    Refresh the Providers screen offers."""
    _PROBE.clear()


def _client(timeout_s: float):
    return httpx.Client(timeout=httpx.Timeout(timeout_s, connect=CONNECT_TIMEOUT_S))


def _capabilities(origin: str, entry: dict, client) -> llm_providers.Capabilities:
    """What this model can do, from what the SERVER said and what answered.

    Everything starts False. `context_tokens` is taken from whichever field the
    server used — the Models API's `max_input_tokens`, or the `context_length`
    that the llama.cpp-derived servers report — and stays 0 when none is present,
    which every caller renders as unknown rather than as zero.
    """
    window = 0
    for key in ("max_input_tokens", "context_length", "context_window",
                "max_context_length"):
        value = entry.get(key)
        if isinstance(value, int) and value > 0:
            window = value
            break
    return llm_providers.Capabilities(
        cache=False,
        thinking=False,
        tools=False,
        vision=False,
        count_tokens=_counts_tokens(origin, entry.get("id"), client),
        context_tokens=window,
    )


def _counts_tokens(origin: str, model_id: object, client) -> bool:
    """THE RUNTIME CAPABILITY PROBE. Does `/v1/messages/count_tokens` answer?

    This is the one capability that can be established cheaply and without
    generating anything, and it is the one that differs most between servers
    claiming the same wire. A 404 or a 405 is a clear no; a connection error is
    also a no, because the answer this feeds is "what can be relied on".
    """
    if not isinstance(model_id, str) or not model_id:
        return False
    try:
        r = client.post(
            f"{origin}/v1/messages/count_tokens",
            json={"model": model_id,
                  "messages": [{"role": "user", "content": "hi"}]},
            headers={"anthropic-version": "2023-06-01"},
        )
    except Exception:
        return False
    return r.status_code == 200


def probe(force: bool = False) -> Probe:
    """Read the server: is it there, and what does it hold.

    Never raises and never lets an exception out of a request path. Both the
    success and the failure are cached for `PROBE_TTL_S`, because a failure that
    is not cached means one TCP connect per model-list read, several times per
    page, against a port with nothing on it.
    """
    origin = base_url()
    if origin is None:
        return Probe(llm_providers.Reach(llm_providers.REACH_UNCONFIGURED,
                                         "No local model server is configured."),
                     (), time.time())
    cached = _PROBE.get(origin)
    if cached is not None and not force and (time.time() - cached.at) < PROBE_TTL_S:
        return cached
    result = _read(origin)
    _PROBE.clear()
    _PROBE[origin] = result
    return result


def _read(origin: str) -> Probe:
    """One live reading. Separated from the caching so a test can call it."""
    try:
        with _client(READ_TIMEOUT_S) as client:
            r = client.get(f"{origin}/v1/models")
            if r.status_code != 200:
                return Probe(
                    llm_providers.Reach(
                        llm_providers.REACH_UNREACHABLE,
                        f"{origin}/v1/models answered {r.status_code}."),
                    (), time.time())
            entries = _entries(r.json())
            models = tuple(
                llm_providers.ModelInfo(
                    id=str(entry["id"]),
                    label=str(entry.get("display_name") or entry["id"]),
                    provider_id=PROVIDER_ID,
                    wire=llm_providers.WIRE_ANTHROPIC,
                    capabilities=_capabilities(origin, entry, client),
                    # NO EFFORT LADDER. `output_config.effort` is a first-party
                    # feature; a local server that ignores it would give the
                    # operator a control that moves no needle, which is the exact
                    # failure `model_caps` exists to prevent.
                    efforts=(),
                )
                for entry in entries if isinstance(entry.get("id"), str)
            )
    except Exception as e:
        return Probe(
            llm_providers.Reach(llm_providers.REACH_UNREACHABLE,
                                f"Could not reach {origin}: {e.__class__.__name__}."),
            (), time.time())
    if not models:
        return Probe(
            llm_providers.Reach(llm_providers.REACH_READY,
                                f"{origin} answered, but has no models loaded."),
            (), time.time())
    return Probe(llm_providers.Reach(llm_providers.REACH_READY, ""),
                 models, time.time())


def _entries(payload: object) -> list[dict]:
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
    transport = llm_providers.TRANSPORT_HTTP
    wire = llm_providers.WIRE_ANTHROPIC
    auth_mode = AUTH_NONE

    def __init__(self, origin: str) -> None:
        self.base_url = origin

    def models(self) -> list[llm_providers.ModelInfo]:
        return list(probe().models)

    def reach(self) -> llm_providers.Reach:
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
        success. `model_resolution` already keeps such a turn off this provider;
        this is the backstop for a caller that constructed one by hand.
        """
        from services import llm_router

        if kw.get("allowed_tools") or kw.get("mcp_servers"):
            raise llm_router.CloudCallFailed(
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
        start = time.time()
        try:
            with _client(float(kw.get("timeout_s") or TURN_TIMEOUT_S)) as client:
                r = client.post(f"{self.base_url}/v1/messages", json=payload,
                                headers={"anthropic-version": "2023-06-01"})
        except Exception as e:
            raise llm_router.CloudCallFailed(
                f"{PROVIDER_LABEL} at {self.base_url} did not answer: "
                f"{e.__class__.__name__}") from e
        elapsed = time.time() - start
        if r.status_code != 200:
            raise llm_router.CloudCallFailed(
                f"{PROVIDER_LABEL} answered {r.status_code}: "
                f"{_error_detail(r)}")
        try:
            body = r.json()
        except ValueError as e:
            raise llm_router.CloudCallFailed(
                f"{PROVIDER_LABEL} answered 200 with a body that is not JSON"
            ) from e
        text = _text_of(body)
        _meter(body, model, prompt)
        if not text:
            raise llm_router.CloudCallFailed(
                f"{PROVIDER_LABEL} answered with no assistant text "
                f"(stop_reason={body.get('stop_reason')})")
        return {
            "text": text,
            "model": str(body.get("model") or model),
            "elapsed_s": round(elapsed, 3),
            "exit_code": 0,
            "stop_reason": body.get("stop_reason"),
            "recovered": False,
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


def _meter(body: dict, model: str, prompt: str) -> None:
    """Record this turn's spend through the ONE usage seam.

    A local model costs no dollars and real tokens, and `model_prices` carries
    no price for it, so `cost_usd` reads None and every view renders it as
    unpriced rather than as $0.00 — which is the honest shape and already the
    rule for any model the table does not carry. Never raises: metering is not
    allowed to cost a turn.
    """
    from services import usage_ledger

    try:
        parsed = usage_ledger.usage_from_envelope(
            {"type": "result", "usage": body.get("usage"), "model": model})
        components = parsed.get("components")
        if not components:
            return
        usage_ledger.record(
            None, usage_ledger.token_usage.row_tokens({"components": components}),
            components=components, model=model, source=parsed.get("source"),
            calls=1)
    except Exception as e:  # pragma: no cover - metering must not sink a turn
        log.warning("local model turn: could not record usage (%s)", e)
    del prompt


def configured_provider() -> LocalHttpProvider | None:
    """The provider, when a base URL is configured. None otherwise.

    Returned even when the server is unreachable, because "configured but not
    running" is a state the operator has to be able to see on the Providers
    screen, and a provider that disappears from the list when the process stops
    cannot show it.
    """
    origin = base_url()
    return None if origin is None else LocalHttpProvider(origin)
