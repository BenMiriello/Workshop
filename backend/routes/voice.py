"""Voice mode surface — local STT + TTS for the Workshop chat.

Presentation/capture layer only: a spoken utterance is transcribed here and then
sent through the ordinary chat path by the frontend; a reply is spoken by asking
`/voice/tts` for audio one sentence at a time. No new LLM/auth surface.

Mounted at `/voice` with the per-user gate, so `current_owner()` is pinned and
per-owner prefs live under `.runtime/users/<owner>/voice-prefs.json`.
"""
from __future__ import annotations

import json
import logging

import asyncio

from fastapi import APIRouter, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import chat_store, voice_engine, voice_worker
from services.gpu_warmhold import VOICE_HOLD
from services.ownership import user_runtime_dir

log = logging.getLogger("workshop.voice")
router = APIRouter()

_PREFS_DEFAULTS = {
    "enabled": True,
    "tts_provider": "auto",   # auto | server | browser | off
    "stt_provider": "auto",   # auto | server | browser
    "voice": voice_engine.DEFAULT_VOICE,
    "speed": 1.0,
    "verbosity": "normal",    # terse | normal
    "speak_replies": True,    # voice-output lane; replaces the old tts "off" mute
    "endpoint_silence_ms": 900,  # trailing silence that ends an utterance (server VAD)
    "barge_in": True,
    "barge_sensitivity": 0.5,  # 0..1 VAD threshold knob (client-side)
    # rolling adaptation signal — updated by the client per session
    "interrupt_rate": 0.0,
}


def _prefs_path():
    return user_runtime_dir() / "voice-prefs.json"


def _load_prefs() -> dict:
    p = _prefs_path()
    data = dict(_PREFS_DEFAULTS)
    if p.exists():
        try:
            data.update(json.loads(p.read_text()))
        except (ValueError, OSError):
            pass
    return data


def _save_prefs(patch: dict) -> dict:
    data = _load_prefs()
    for k, v in (patch or {}).items():
        if k in _PREFS_DEFAULTS:
            data[k] = v
    p = _prefs_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)
    return data


@router.get("/voice/health")
async def voice_health():
    return voice_engine.health()


@router.get("/voice/voices")
async def voice_voices():
    if not voice_engine.TTS.available:
        return {"available": False, "voices": []}
    return {"available": True, "voices": voice_engine.TTS.voices()}


@router.post("/voice/stt")
async def voice_stt(audio: UploadFile, language: str = Form("en")):
    if not voice_engine.STT.available:
        raise HTTPException(status_code=503, detail="server STT unavailable")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    await VOICE_HOLD.touch("voice stt")
    import asyncio

    text = await asyncio.to_thread(
        voice_engine.STT.transcribe, data, (language or None)
    )
    return {"text": text}


class TtsBody(BaseModel):
    text: str
    voice: str | None = None
    speed: float = 1.0
    # When true, split into sentences and return a multipart-ish concatenated
    # stream of WAV chunks (one per sentence) so the client can start playing
    # the first sentence while later ones render.
    stream: bool = False


@router.post("/voice/tts")
async def voice_tts(body: TtsBody):
    if not voice_engine.TTS.available:
        raise HTTPException(status_code=503, detail="server TTS unavailable")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    await VOICE_HOLD.touch("voice tts")
    import asyncio

    if not body.stream:
        wav = await asyncio.to_thread(
            voice_engine.TTS.synth_wav, text, body.voice, body.speed
        )
        return Response(content=wav, media_type="audio/wav")

    # Streaming: emit length-prefixed WAV frames, one per sentence.
    sentences = voice_engine.split_sentences(text) or [text]

    async def gen():
        for i, sent in enumerate(sentences):
            wav = await asyncio.to_thread(
                voice_engine.TTS.synth_wav, sent, body.voice, body.speed
            )
            header = json.dumps({"i": i, "n": len(sentences), "bytes": len(sent), "text": sent})
            hb = header.encode("utf-8")
            # frame = 4-byte header length | header json | 4-byte wav length | wav
            yield len(hb).to_bytes(4, "big") + hb + len(wav).to_bytes(4, "big") + wav

    return StreamingResponse(gen(), media_type="application/octet-stream")


@router.get("/voice/prefs")
async def voice_get_prefs():
    return _load_prefs()


class PrefsBody(BaseModel):
    enabled: bool | None = None
    tts_provider: str | None = None
    stt_provider: str | None = None
    voice: str | None = None
    speed: float | None = None
    verbosity: str | None = None
    speak_replies: bool | None = None
    endpoint_silence_ms: int | None = None
    barge_in: bool | None = None
    barge_sensitivity: float | None = None
    interrupt_rate: float | None = None


@router.put("/voice/prefs")
async def voice_put_prefs(body: PrefsBody):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return _save_prefs(patch)


# ------------------------------------------------------------------ resident
# worker turns (voice-mode v2, docs/voice-mode/design.md section 3). Streaming
# replies over a long-lived `claude -p` process, one per voice session.

class SessionBody(BaseModel):
    context_key: str = "general"
    thread_id: str | None = None
    model: str | None = None


@router.post("/voice/session")
async def voice_session(body: SessionBody):
    """Prewarm/bind the resident worker for a voice session. Called on surface
    mount so the process is up before the first utterance finishes."""
    thread_id = body.thread_id
    if not thread_id or not chat_store.get_thread(thread_id):
        thread = chat_store.create_thread(context_key=body.context_key, title="Voice")
        thread_id = thread["id"]
    await voice_worker.ensure_worker(body.context_key, thread_id, body.model)
    return {"session_key": thread_id, "thread_id": thread_id}


class TurnBody(BaseModel):
    context_key: str = "general"
    text: str
    model: str | None = None
    speculative: bool = False


@router.post("/voice/session/{thread_id}/turns")
async def voice_start_turn(thread_id: str, body: TurnBody):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty utterance")
    turn = await voice_worker.start_turn(
        context_key=body.context_key, thread_id=thread_id, text=text,
        model=body.model, speculative=body.speculative,
    )
    return {"turn_id": turn.id, "thread_id": thread_id}


@router.get("/voice/turns/{turn_id}/stream")
async def voice_stream_turn(turn_id: str, request: Request):
    turn = voice_worker.get_turn(turn_id)
    if not turn:
        raise HTTPException(404, "turn not found")
    last_event_id = request.headers.get("Last-Event-ID") or "0"
    try:
        after = max(0, int(last_event_id))
    except ValueError:
        after = 0

    async def gen():
        try:
            async for offset, ev in voice_worker.iter_events(turn, after=after):
                if await request.is_disconnected():
                    return
                kind = ev["kind"]
                payload = json.dumps({"kind": kind, "payload": ev["payload"], "ts": ev["ts"]})
                yield f"id: {offset}\nevent: {kind}\ndata: {payload}\n\n"
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


class CancelBody(BaseModel):
    reason: str = "superseded"
    heard: int | None = None


@router.post("/voice/turns/{turn_id}/cancel")
async def voice_cancel_turn(turn_id: str, body: CancelBody):
    ok = await voice_worker.cancel_turn(turn_id, reason=body.reason, heard=body.heard)
    if not ok:
        raise HTTPException(404, "turn not found")
    return {"ok": True}


@router.post("/voice/turns/{turn_id}/commit")
async def voice_commit_turn(turn_id: str):
    ok = await voice_worker.commit_turn(turn_id)
    if not ok:
        raise HTTPException(404, "turn not found")
    return {"ok": True}


@router.delete("/voice/session/{thread_id}")
async def voice_stop_session(thread_id: str):
    await voice_worker.stop_session(thread_id)
    return {"ok": True}
