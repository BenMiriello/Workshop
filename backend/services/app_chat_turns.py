"""Telling an app's chat gateway that a turn ended, and how.

A gateway sees a turn only as the model requests it forwards, so it cannot tell
"the turn is over" from "a tool is running between two steps", and work it
holds for the turn (a queue slot, a claim on a shared card) waits out an idle
timer. An app whose `chat.gateway` declares `turn_end` (a path under the
gateway's own path, `services/app_chat`) is told instead: one POST of

    {"thread": <thread id>, "turn": <turn id>,
     "outcome": "finished" | "stopped" | "error" | "lost"}

plus `"model_failed": true` on an `error` that is the model's requests failing
(the CLI gave up on one, or the turn was ended for a run of them,
`model_failures`), so a gateway can tell an inference failure it may wait out
from any other error. Such a turn exits 0 with the CLI's "API Error" line as
its reply, which without this reads as `finished`. Sent
with the `X-Workshop-*` headers the gateway's requests carry, sent to the port
the gateway runs on at that moment. Fire-and-forget: it runs on its own thread
with a short timeout, and a gateway that is down or slow costs a log line,
never the turn's ending. An app that declares nothing is never called.
"""
from __future__ import annotations

import contextvars
import logging
import threading

import httpx

from services import app_chat, app_chat_gateway

log = logging.getLogger("workshop.app_chat_turns")

__all__ = ["OUTCOMES", "POST_TIMEOUT_S", "announce", "outcome"]

OUTCOMES = ("finished", "stopped", "error", "lost")
POST_TIMEOUT_S = 3.0


def outcome(*, rc: int, stopped: bool = False, lost: bool = False,
            halted: bool = False, timed_out: bool = False,
            model_failed: bool = False) -> str:
    """How a turn ended, in the words the gateway is told.

    The operator's Stop wins over everything that follows from it (a stopped
    process exits non-zero); a turn the backend lost is named as lost rather
    than as a failure of its own."""
    if stopped:
        return "stopped"
    if lost:
        return "lost"
    if halted or timed_out or model_failed or rc != 0:
        return "error"
    return "finished"


def announce(thread: dict | None, turn_id: str, ended: str, *,
             model_failed: bool = False) -> threading.Thread | None:
    """Tell `thread`'s app gateway that `turn_id` ended `ended`, off this
    thread. Returns the thread doing it, or None when there is nothing to do.

    `thread` needs only `id`, `scope` and `title`. Nothing is started for a
    chat outside an app's scope, so every other turn's ending costs nothing."""
    thread = thread or {}
    if (not thread.get("id") or not turn_id or ended not in OUTCOMES
            or not str(thread.get("scope") or "").startswith("app:")):
        return None
    body = {"thread": thread["id"], "turn": turn_id, "outcome": ended}
    if model_failed and ended == "error":
        body["model_failed"] = True
    ctx = contextvars.copy_context()
    worker = threading.Thread(
        target=ctx.run, args=(_post, dict(thread), turn_id, body),
        name=f"turn-end-{turn_id[:8]}", daemon=True)
    worker.start()
    return worker


def _post(thread: dict, turn_id: str, body: dict) -> None:
    ended = body["outcome"]
    try:
        decl = app_chat.declaration(str(thread["scope"]).split(":", 1)[1])
        if decl is None or not decl.gateway_turn_end:
            return
        route = app_chat_gateway.gateway_route(thread, turn_id)
        if route is None:
            return
        base, headers = route
        r = httpx.post(f"{base}{decl.gateway_turn_end}", headers=headers,
                       json=body, timeout=POST_TIMEOUT_S)
        if r.status_code >= 400:
            log.info("turn %s: %s's gateway answered %s to its end (%s)",
                     turn_id, decl.app_slug, r.status_code, ended)
    except Exception as e:  # noqa: BLE001 - a notice, never the turn's ending
        log.info("turn %s: could not tell the gateway it ended (%s): %s",
                 turn_id, ended, e)
