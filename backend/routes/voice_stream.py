"""`/api/voice/stream` — the one socket a voice conversation runs over.

Mounted WITHOUT the router-level `require_user` dependency that the rest of the
voice surface uses, because that dependency is typed for an HTTP `Request` and a
WebSocket is not one. Authentication is done here instead, against the same
`auth_store`, with the same fail-closed rule: no valid credential, no socket.

WebSockets are not covered by the same-origin policy, so a cross-site page can
open one and it will carry cookies. The Origin check below is what closes that,
and it is the standard mitigation rather than an extra layer of policy: it
enforces exactly the boundary the CSRF header enforces on the HTTP routes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services import auth_store, chat_store, ownership
from services.voice import machine as m
from services.voice import protocol
from services.voice.runtime import TTS_SAMPLE_RATE, VoiceRuntime
from services.voice.timeline import SAMPLE_RATE

log = logging.getLogger("workshop.voice.stream")
router = APIRouter()

SESSION_COOKIE = "workshop_session"

_DEV_ORIGINS = {
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5174", "http://127.0.0.1:5174",
}


def _origin_ok(ws: WebSocket) -> bool:
    """Is this socket coming from a page we serve?

    THE HOST TO COMPARE AGAINST IS THE ONE THE BROWSER TYPED, NOT OURS.
    Nearly every real connection arrives through the public edge, which
    terminates the browser's socket and dials this process on 127.0.0.1:8000.
    Comparing the browser's Origin against THIS process's Host therefore
    compares `workshop.<host>` against `127.0.0.1:8000` and rejects every
    genuine connection with a 403, which is exactly what happened: the operator
    got a spinner that never resolved while the log filled with
    `WebSocket /voice/stream 403`.

    The edge forwards the original host as `x-forwarded-host` and does its own
    same-origin check before dialing (it is the boundary the browser actually
    talks to). This is the standard reverse-proxy arrangement, and it adds no
    trust: an attacker who could set `x-forwarded-host` is already inside the
    loopback interface, where they could talk to this port directly anyway.
    """
    origin = ws.headers.get("origin")
    if not origin:
        return True                      # a native client sends none
    if origin in _DEV_ORIGINS:
        return True
    for var in ("WORKSHOP_CANONICAL_ORIGIN", "WORKSHOP_PUBLIC_ORIGIN", "WORKSHOP_LOCAL_ORIGIN"):
        configured = os.environ.get(var, "").rstrip("/")
        if configured and origin.rstrip("/") == configured:
            return True
    host = ws.headers.get("x-forwarded-host") or ws.headers.get("host", "")
    return bool(host) and origin.split("//", 1)[-1] == host


def _user(ws: WebSocket):
    user = auth_store.session_user(ws.cookies.get(SESSION_COOKIE))
    if user is not None:
        return user
    auth = ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth_store.device_user(auth[7:].strip())
    return None


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    if not _origin_ok(ws):
        await ws.close(code=4403)
        return
    user = _user(ws)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    ownership.set_current_owner(user["id"])
    owner = user["id"]

    send_lock = asyncio.Lock()

    async def send_json(payload: dict) -> None:
        async with send_lock:
            try:
                await ws.send_text(json.dumps(payload))
            except (WebSocketDisconnect, RuntimeError):
                raise asyncio.CancelledError()

    async def send_audio(token: int, pcm: np.ndarray) -> None:
        async with send_lock:
            try:
                await ws.send_bytes(protocol.encode_audio(protocol.KIND_TTS, token, pcm))
            except (WebSocketDisconnect, RuntimeError):
                raise asyncio.CancelledError()

    runtime: VoiceRuntime | None = None
    cursor = protocol.Cursor()
    said_bye = False
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break

            # ---- audio ---------------------------------------------------
            if (data := msg.get("bytes")) is not None:
                if runtime is None:
                    continue
                try:
                    kind, raw, pcm = protocol.decode_audio(data)
                except ValueError:
                    continue
                if kind != protocol.KIND_MIC:
                    continue
                await runtime.on_audio(cursor.absolute(raw), pcm)
                continue

            # ---- control -------------------------------------------------
            text = msg.get("text")
            if not text:
                continue
            try:
                ev = json.loads(text)
            except ValueError:
                continue
            kind = ev.get("t")

            if kind == "hello":
                # Owner is pinned per message, not just at accept: the runtime's
                # background tasks run in their own contexts and the per-owner
                # stores read the contextvar.
                ownership.set_current_owner(owner)
                # A CONNECTION IS NOT A CONVERSATION. This used to mint a real,
                # persisted chat titled "Voice" for every hello that named no
                # thread, i.e. on every connect, before a word had been said.
                # Nothing about opening a socket says the operator is going to
                # speak, and three things reconnect it on their own: the
                # transport's own backoff ladder (which replays the SAME hello,
                # so each rung asked for another thread), a rebuilt session
                # object, and every voice surface mounted at once. On 2026-08-12
                # that produced 22 empty "Voice" chats in five minutes, five of
                # them from one delete refreshing the list.
                #
                # The thread is created by the first utterance instead
                # (VoiceRuntime._thread), which is the first moment there is a
                # conversation to put in it. An unknown id is dropped rather
                # than honoured: it is a stale client, and binding a runtime to
                # a thread the store does not have would fail on the first turn.
                thread_id = ev.get("thread_id")
                if thread_id and not chat_store.get_thread(thread_id):
                    thread_id = None
                runtime = VoiceRuntime(
                    thread_id=thread_id,
                    context_key=ev.get("context_key") or "general",
                    model=ev.get("model"),
                    send_json=send_json,
                    send_audio=send_audio,
                )
                runtime.apply_prefs(ev.get("prefs") or {})
                await send_json({
                    "t": "ready",
                    "threadId": thread_id,
                    "micSampleRate": SAMPLE_RATE,
                    "ttsSampleRate": TTS_SAMPLE_RATE,
                })
                await runtime.start()
                continue

            if runtime is None:
                continue
            ownership.set_current_owner(owner)

            if kind == "prefs":
                patch = ev.get("patch") or {}
                runtime.apply_prefs({**{"voice": runtime.voice, "speed": runtime.speed}, **patch})
                if "mic_on" in patch or "speak_on" in patch:
                    await runtime.dispatch(m.SetLanes(
                        mic_on=patch.get("mic_on"), speak_on=patch.get("speak_on"),
                    ))
                else:
                    await runtime.emit()
            elif kind == "text":
                await runtime.dispatch(m.SubmitText(ev.get("body") or ""))
            elif kind == "interrupt":
                await runtime.dispatch(m.Interrupt())
            elif kind == "cancel":
                await runtime.dispatch(m.CancelTurn())
            elif kind == "device":
                await runtime.dispatch(m.DeviceState(ok=bool(ev.get("ok", True))))
            elif kind == "played":
                # THE CLIENT IS THE AUTHORITY ON WHAT WAS HEARD. Only it knows
                # when a sample actually left the speaker, and whether the next
                # thing the operator says is a continuation or an interruption
                # turns on exactly that.
                turn, index = ev.get("turn") or "", int(ev.get("index") or 0)
                if ev.get("phase") == "start":
                    await runtime.dispatch(m.ClauseAudible(turn, index))
                else:
                    await runtime.dispatch(m.ClauseHeard(turn, index))
                    await runtime._maybe_finish(turn)
            elif kind == "ping":
                # A round-trip marker. Everything the server had queued before
                # this is delivered before the pong, which is what lets a client
                # (and a test) drain deterministically instead of guessing.
                await send_json({"t": "pong", "n": ev.get("n")})
            elif kind == "retry":
                await runtime.dispatch(m.SubmitText(runtime.state.last_user))
            elif kind == "endpoint":
                # "I am done" without waiting for the turn-end model to agree.
                await runtime.force_endpoint()
            elif kind == "typing":
                # Typing holds the turn. A person typing mid-utterance has not
                # finished talking, and endpointing under them sends half of it.
                runtime.set_typing(bool(ev.get("active")))
            elif kind == "test_mic":
                await send_json({"t": "mic_test", "text": await runtime.test_mic()})
            elif kind == "bye":
                said_bye = True
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:                                   # noqa: BLE001
        log.exception("voice socket failed")
    finally:
        if runtime is not None:
            try:
                await runtime.close(release=said_bye)
            except Exception:                           # noqa: BLE001
                log.warning("runtime teardown failed", exc_info=True)
        try:
            await ws.close()
        except Exception:                               # noqa: BLE001
            pass
