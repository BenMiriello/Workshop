"""Where an app chat's model requests go: the app's own gateway service.

An app whose `chat` block names a `gateway` (`services/app_chat`) has a
turn's model requests sent through that service instead of straight to the
model server, so the app can hold, order and show them as its own work. It
applies only to a turn the platform has already pointed at a model server
(`llm_providers.cli_turn_env`); a turn the CLI serves itself is untouched. The
gateway is told which thread and turn each request belongs to in
`X-Workshop-Thread`, `X-Workshop-Thread-Title` (URL-encoded) and
`X-Workshop-Turn`. A gateway that is not running is skipped, and the turn goes
straight to the model server as before.

THE WINDOW THE CLI IS TOLD IS THE GATEWAY'S. A gateway may send a request to
another model than the one it names (an app with two writers, a hand-off), and
the model server may list no window for a model it has not loaded lately, so the
window is read from the gateway's own `/v1/models` when it gives one
(`gateway_window`). Without it the CLI compacted by the model server's reading,
or by its own 200k guess when that was missing, and on 2026-10-14 a chat grew to
84,627 tokens against an 81,920 window without compacting once.

The CLI is handed the gateway's port when the turn starts and keeps it, which
holds because an app's gateway runs on the port its manifest pins. What does
happen mid-turn is a restart of the app on that same port, refusing
connections for the seconds it takes. The CLI rides that out with its own
retries (`retries_to_ride_out`), so nothing of the platform's sits between a
turn and its gateway.

Nothing here names an app: every answer comes from the app's declaration.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from services import app_chat, local_model_provider

log = logging.getLogger("workshop.app_chat")

__all__ = ["COMPACTION_HEADER", "GATEWAY_RETRIES", "RIDE_OUT_S",
           "STREAM_IDLE_MS", "declared_efforts", "gateway_env", "gateway_route",
           "gateway_window", "retries_to_ride_out"]

# The gateway's model list is read as a turn spawns, on the event loop. It is a
# service on this host: one that has not answered in this long is not going to,
# and the turn goes on with the model server's window.
WINDOW_TIMEOUT_S = 2.0

# THE CLI'S RETRY SCHEDULE, read from the CLI 2.1.287 binary. A failed model
# request, a refused connection included, is retried up to
# `CLAUDE_CODE_MAX_RETRIES` times (default 10, clamped at 15), and the wait
# before retry n is min(32 s, 0.5 s * 2**(n-1)) plus up to 25% jitter.
# Measured on 2026-10-04: with 6 retries against a port that refused for 25 s,
# the retries went out at 1, 1.5, 2.6, 5, 9 and 18.6 s and the sixth, at 38 s,
# was answered.
_CLI_RETRY_BASE_S = 0.5
_CLI_RETRY_CAP_S = 32.0
_CLI_RETRIES_MAX = 15

# How long an app restart may refuse connections and a turn still go on. The
# runner brings a restarted service back in a median 8 s and a p90 of 14 s, and
# a scripted restart that checks its routes takes about 20 s; 30 s is the hold
# the app proxy uses for the same restarts (`routes/base_apps._HOLD_S`).
RIDE_OUT_S = 30.0


def retries_to_ride_out(seconds: float) -> int:
    """The fewest CLI retries whose waits, at their shortest (no jitter), add
    up to `seconds`: 6 for 30 s (0.5 + 1 + 2 + 4 + 8 + 16 = 31.5 s)."""
    waited, n = 0.0, 0
    while waited < seconds and n < _CLI_RETRIES_MAX:
        n += 1
        waited += min(_CLI_RETRY_CAP_S, _CLI_RETRY_BASE_S * 2 ** (n - 1))
    return n


GATEWAY_RETRIES = retries_to_ride_out(RIDE_OUT_S)

# The compaction model the operator chose for the chat's app, else for every
# chat, as of the turn's start. A gateway sends a compaction to it when it
# can hold the prompt; the platform's own hop reads the choice live instead.
COMPACTION_HEADER = "X-Workshop-Compaction-Model"

# A GATEWAY MAY HOLD A REQUEST IN ITS OWN QUEUE (a shared card, its turn not
# yet come), keeping the stream open with SSE comments meanwhile. The CLI
# 2.1.302 abandons a stream that has sent no message in 600 s however many
# comments it carries, then retries without streaming, which a gateway can
# hold with no bytes at all; measured on 2026-10-20 against a server holding
# 660 s with a comment every 10 s: aborted at 600 s by default, answered with
# `CLAUDE_STREAM_IDLE_TIMEOUT_MS` at its ceiling, which is this.
STREAM_IDLE_MS = 1_800_000


def declared_efforts(scope: str | None) -> tuple[tuple[str, ...], str | None]:
    """The effort levels the scope's gateway says it honours and its declared
    default, as written.

    `((), None)` for any scope but an app's, for an app with no gateway, and for
    one whose gateway declares no `efforts`. Which of them the CLI can spell,
    and for which models they apply, is `model_caps.efforts_for`'s question."""
    scope = str(scope or "")
    if not scope.startswith("app:"):
        return (), None
    decl = app_chat.declaration(scope.split(":", 1)[1])
    if decl is None or not decl.gateway_service or not decl.gateway_efforts:
        return (), None
    return decl.gateway_efforts, decl.gateway_effort_default


def gateway_route(thread: dict | None,
                  turn_id: str = "") -> tuple[str, dict[str, str]] | None:
    """The base URL of the app's gateway and the headers naming the thread
    (and turn) a request belongs to, or None.

    None unless the thread belongs to an app scope, that app declares a
    `gateway`, and the gateway's service has a port. The CLI's turns reach it
    through `gateway_env`; a platform side call made for the same chat on the
    same model (`services/own_model`) posts to it directly.
    """
    thread = thread or {}
    thread_id = str(thread.get("id") or "")
    if not thread_id:
        return None
    scope = str(thread.get("scope") or "")
    if not scope.startswith("app:"):
        return None
    decl = app_chat.declaration(scope.split(":", 1)[1])
    if decl is None or not decl.gateway_service:
        return None
    port = app_chat.service_port(decl.gateway_project or decl.project,
                                 decl.gateway_service)
    if not port:
        log.info("app chat: %s's gateway is not running; going direct",
                 decl.app_slug)
        return None
    # `X-Workshop-App` says whose chat it is, since a gateway can queue
    # another app's chat as well as its own (`gateway.app`).
    headers = {"X-Workshop-Thread": thread_id,
               "X-Workshop-Thread-Title": quote(str(thread.get("title") or "")),
               "X-Workshop-App": decl.app_slug}
    if turn_id:
        headers["X-Workshop-Turn"] = turn_id
    return f"http://127.0.0.1:{port}{decl.gateway_path}", headers


def gateway_window(base: str, headers: dict[str, str], model: str) -> int:
    """The context window the gateway at `base` gives `model` in its
    `/v1/models`, 0 when it gives none or does not answer."""
    if not model:
        return 0
    try:
        r = httpx.get(f"{base}/v1/models", headers=headers,
                      timeout=WINDOW_TIMEOUT_S)
        entries = (local_model_provider.model_entries(r.json())
                   if r.status_code == 200 else [])
    except (httpx.HTTPError, ValueError) as e:
        log.info("app chat: no window from the gateway at %s: %s", base, e)
        return 0
    return next((local_model_provider.window_of(e) for e in entries
                 if e.get("id") == model), 0)


def _scope(thread: dict | None) -> str | None:
    return str((thread or {}).get("scope") or "") or None


def _custom_headers(env: dict) -> list[str]:
    return [h for h in str(env.get("ANTHROPIC_CUSTOM_HEADERS") or "")
            .splitlines() if h.strip()]


def _scoped(thread: dict | None, env: dict) -> dict:
    """`env` naming the app whose chat this is, for a turn that reaches the
    model server through the platform's own hop (`routes/local_llm`), which
    reads it to pick the app's compaction model. Unchanged for any other
    scope."""
    scope = _scope(thread) or ""
    if not scope.startswith("app:"):
        return env
    headers = _custom_headers(env) + [f"X-Workshop-App: {scope.split(':', 1)[1]}"]
    return {**env, "ANTHROPIC_CUSTOM_HEADERS": "\n".join(headers)}


def _retries(env: dict) -> int:
    try:
        return int(env.get("CLAUDE_CODE_MAX_RETRIES") or 0)
    except (TypeError, ValueError):
        return 0


def gateway_env(thread: dict | None, env: dict, turn_id: str = "") -> dict:
    """`env` with the CLI's model requests sent through the app's gateway.

    Unchanged unless the turn is already going to a model server
    (`ANTHROPIC_BASE_URL` set) and `gateway_route` finds a gateway for its
    thread. `thread` is the turn's thread as the caller already holds it, so
    this module stays clear of the chat store. `turn_id` names the turn, so the
    app can tell one turn's requests from the next one's in the same
    conversation (a Stop in the app ends that turn, not the chat).

    The CLI is also asked to say what each request is for
    (`x-claude-code-request-class`: main, subagent, compaction, auxiliary), so
    the gateway can treat a compaction differently from the turn's own steps,
    and told the compaction model chosen for the app (`COMPACTION_HEADER`),
    and given enough retries to wait out a restart of the app
    (`GATEWAY_RETRIES`), and the window the gateway gives the turn's model
    (`gateway_window`) when it gives one.
    """
    if "ANTHROPIC_BASE_URL" not in env:
        return env
    route = gateway_route(thread, turn_id)
    if route is None:
        return _scoped(thread, env)
    base, named = route
    headers = _custom_headers(env)
    headers += [f"{k}: {v}" for k, v in named.items()]
    chosen = local_model_provider.compaction_model_for(_scope(thread))
    if chosen:
        headers.append(f"{COMPACTION_HEADER}: {chosen}")
    out = {**env, "ANTHROPIC_BASE_URL": base,
           "ANTHROPIC_CUSTOM_HEADERS": "\n".join(headers),
           "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
           "CLAUDE_CODE_MAX_RETRIES": str(max(_retries(env), GATEWAY_RETRIES)),
           "CLAUDE_STREAM_IDLE_TIMEOUT_MS": str(STREAM_IDLE_MS)}
    window = gateway_window(base, named, str(env.get("ANTHROPIC_MODEL") or ""))
    if window > 0:
        out.update(local_model_provider.window_env(window))
    return out
