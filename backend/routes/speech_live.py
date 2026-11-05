"""`/api/speech/live`: the socket live dictation runs over.

Client to server: binary mic frames (services/voice/protocol.py), then JSON
  {"t": "hello", "from": <sample offset>, "dictation": <id>, "paused": bool,
   "mode"?: <mode>}                        first; audio before it is ignored.
                                           `from` is where this connection's
                                           audio starts, so a reconnect resends
                                           only what was never settled. The
                                           audio is kept under `dictation`
                                           (services/speech/recordings.py).
  {"t": "commit", "to"}                    the text the client shows, read
                                           through sample `to`, is final
  {"t": "pause", "paused": bool}           stop or start writing words
  {"t": "mode", "mode": <mode>}             change the mode
                                           (services/speech/modes.py)
  {"t": "numbers", "on": bool}             numbered things are on screen: a
                                           number said on its own picks one
  {"t": "actions", "list": [{"id": "app:<n>", "phrases", "fixed"?,
   "examples"?, "title"?, "description"?, "takesArg"?}], "off": [<id>]}
                                           the actions the open pages declare,
                                           heard as the operator's own commands
                                           are and sent back as a command named
                                           by their id; `off`: the commands and
                                           actions that do not apply now, heard
                                           as words. Sent after hello and on
                                           change (services/speech/app_actions.py)
  {"t": "stop"}                            settle the rest, answer "done".
Server to client (services/speech/live.py says what each one means):
  {"t": "ready", "vad": "silero" | "energy"}
  {"t": "text", "text", "from", "to"}      the open stretch as heard now
  {"t": "final", "text", "from", "to", "rest", "restTo"}
  {"t": "command", "name", "arg", "words", "from", "to"}   (name "guess":
                                           also "guesses", "none" or "loading")
  {"t": "paused", "paused"}
  {"t": "mode", "mode", "said"}
  {"t": "done"}
  {"t": "error", "message"}

Closing the socket is how a dictation is cancelled. Signed in the same way as
the voice socket, with the same Origin check.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from deps_user import socket_origin_ok, socket_user
from services import ownership
from services.speech import engine, recordings
from services.speech.command_store import load_table
from services.speech.live import LiveDictation
from services.speech.meaning import shared as meaning
from services.speech.modes import MODES
from services.speech.workers import WorkerError
from services.voice import protocol

log = logging.getLogger("workshop.speech.live")
router = APIRouter()

TICK_S = 0.04

# One dictation per user: the live socket of each user's newest dictation and
# how to speak to it. A new dictation tells the older one it was taken over
# (another device, or a window this browser could not reach), and that client
# stops. A reconnect of the same dictation replaces its socket quietly.
_LIVE: dict[str, tuple[str, Callable[[dict], Awaitable[None]]]] = {}


async def _take_over(user: str, dictation: str, emit: Callable[[dict], Awaitable[None]]) -> None:
    was = _LIVE.get(user)
    _LIVE[user] = (dictation, emit)
    if was and was[0] != dictation:
        try:
            await was[1]({"t": "taken"})
        except (WebSocketDisconnect, RuntimeError):
            pass


@router.websocket("/speech/live")
async def speech_live(ws: WebSocket) -> None:
    if not socket_origin_ok(ws):
        await ws.close(code=4403)
        return
    user = socket_user(ws)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    ownership.set_current_owner(user["id"])
    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(payload))

    if not engine.available():
        await emit({"t": "error", "message": "Dictation is not installed on this server."})
        await ws.close()
        return
    engine.warm()
    session: LiveDictation | None = None
    cursor = protocol.Cursor()
    ticker: asyncio.Task | None = None
    recorder: recordings.Recorder | None = None

    async def tick_loop(s: LiveDictation) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await s.tick(emit)
            except WorkerError as e:
                await emit({"t": "error", "message": f"Dictation is not available right now: {e}"})
                await asyncio.sleep(1.0)

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                if session is None:
                    continue
                try:
                    kind, raw, pcm = protocol.decode_audio(data)
                except ValueError:
                    continue
                if kind == protocol.KIND_MIC:
                    at = cursor.absolute(raw)
                    session.write(at, pcm)
                    if recorder is not None:
                        recorder.write(at, pcm)
                continue
            try:
                ev = json.loads(msg.get("text") or "")
            except ValueError:
                continue
            if ev.get("t") == "hello" and session is None:
                start = max(0, int(ev.get("from") or 0))
                cursor.absolute(start & 0xFFFFFFFF)
                table = load_table()
                if ev.get("mode") in MODES:   # what this client last set, which may not be saved yet
                    table = table.with_mode(ev["mode"])
                session = LiveDictation(engine.transcribe_timed, start=start,
                                        commands=table, paused=ev.get("paused") is True)
                if table.mode != "dictation":
                    meaning.warm(table)
                if recordings.valid(str(ev.get("dictation") or "")):
                    recorder = recordings.Recorder(str(ev["dictation"]))
                ticker = asyncio.create_task(tick_loop(session))
                await _take_over(user["id"], str(ev.get("dictation") or id(ws)), emit)
                await emit({"t": "ready", "vad": session.vad.tier})
            elif ev.get("t") == "commit" and session is not None:
                try:
                    await session.commit(int(ev.get("to") or 0), emit)
                except WorkerError as e:
                    await emit({"t": "error", "message": f"Dictation is not available right now: {e}"})
            elif ev.get("t") == "pause" and session is not None:
                try:
                    await session.set_paused(ev.get("paused") is True, emit)
                except WorkerError as e:
                    await emit({"t": "error", "message": f"Dictation is not available right now: {e}"})
            elif ev.get("t") == "mode" and session is not None:
                try:
                    await session.set_mode(str(ev.get("mode") or ""), emit)
                except WorkerError as e:
                    await emit({"t": "error", "message": f"Dictation is not available right now: {e}"})
            elif ev.get("t") == "numbers" and session is not None:
                session.set_numbers(ev.get("on") is True)
            elif ev.get("t") == "actions" and session is not None:
                session.set_actions(ev.get("list"), ev.get("off"))
            elif ev.get("t") == "stop" and session is not None:
                if ticker:
                    ticker.cancel()
                    ticker = None
                try:
                    await session.finish(emit)
                except WorkerError as e:
                    await emit({"t": "error", "message": f"Dictation is not available right now: {e}"})
                await emit({"t": "done"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:                           # noqa: BLE001
        log.exception("live dictation failed")
        try:
            await emit({"t": "error", "message": "Dictation stopped on the server."})
        except (WebSocketDisconnect, RuntimeError):
            pass
    finally:
        if _LIVE.get(user["id"], (None, None))[1] is emit:
            _LIVE.pop(user["id"], None)
        if ticker:
            ticker.cancel()
        if recorder is not None:
            recorder.close()
        if session is not None:
            session.lag.summary()
