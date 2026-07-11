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

from fastapi import APIRouter, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import voice_engine
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
    barge_in: bool | None = None
    barge_sensitivity: float | None = None
    interrupt_rate: float | None = None


@router.put("/voice/prefs")
async def voice_put_prefs(body: PrefsBody):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return _save_prefs(patch)
