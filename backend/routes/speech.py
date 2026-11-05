"""Speech for any page: dictation into a text field, reading text aloud, and
which control on screen a spoken click means.

Thin over services/speech/engine.py. Voice mode reaches the same engine through
its own streaming session (routes/voice_stream.py); these routes are the
one-shot form every other surface uses.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services.speech import audio, engine, ui_match, voices
from services.speech.workers import WorkerError

router = APIRouter(prefix="/speech", tags=["speech"])

MAX_AUDIO_BYTES = 64 * 1024 * 1024
MAX_TEXT = 4000
MAX_ITEMS = 200
MAX_ITEM = 300


class SpeechPart(BaseModel):
    available: bool
    state: str
    error: str | None = None
    voice: str | None = None


class SpeechStatus(BaseModel):
    stt: SpeechPart
    tts: SpeechPart
    provider: str
    device: str
    mode: str


class Transcript(BaseModel):
    text: str


class SpeechVoice(BaseModel):
    id: str
    name: str
    language: str
    gender: voices.Gender | None = None


class VoiceList(BaseModel):
    voices: list[SpeechVoice]
    default: str


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    voice: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class SpeechMatchBody(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    items: list[str] = Field(max_length=MAX_ITEMS)


class SpeechMatch(BaseModel):
    """`scores` follows `items`; null when the model is not available. A best
    score of at least `act`, ahead of the next by `margin`, is a clear match;
    from `ask` it is worth offering as a choice."""
    scores: list[float] | None
    act: float
    margin: float
    ask: float


def _unavailable() -> HTTPException:
    return HTTPException(503, detail="speech is not installed on this server")


@router.get("/status", response_model=SpeechStatus)
async def speech_status() -> SpeechStatus:
    h = engine.health()
    error = h["error"] if h["available"] else "speech packages not installed"
    part = SpeechPart(available=h["available"], state=h["state"], error=error)
    return SpeechStatus(stt=part, tts=part.model_copy(update={"voice": h["voice"]}),
                        provider=h["provider"], device=h["device"], mode=h["mode"])


@router.post("/warm", status_code=204)
async def speech_warm() -> Response:
    if not engine.available():
        raise _unavailable()
    await asyncio.to_thread(engine.warm)
    return Response(status_code=204)


@router.post("/transcribe", response_model=Transcript)
async def speech_transcribe(request: Request) -> Transcript:
    if not engine.available():
        raise _unavailable()
    data = await request.body()
    if not data:
        raise HTTPException(400, detail="no audio")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, detail="recording too long")
    try:
        text = await asyncio.to_thread(engine.transcribe_audio, data)
    except audio.AudioError as e:
        raise HTTPException(400, detail=str(e)) from e
    except WorkerError as e:
        raise HTTPException(503, detail=str(e)) from e
    return Transcript(text=text)


@router.post("/match", response_model=SpeechMatch)
async def speech_match(body: SpeechMatchBody) -> SpeechMatch:
    items = [i[:MAX_ITEM] for i in body.items]
    scores = await asyncio.to_thread(ui_match.shared.scores, body.query, items)
    return SpeechMatch(scores=scores, act=ui_match.ACT, margin=ui_match.MARGIN, ask=ui_match.ASK)


@router.post("/speak")
async def speech_speak(body: SpeakBody) -> Response:
    if not engine.available():
        raise _unavailable()
    try:
        wav = await asyncio.to_thread(engine.speak_wav, body.text, body.voice, body.speed)
    except WorkerError as e:
        raise HTTPException(503, detail=str(e)) from e
    return Response(content=wav, media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


@router.get("/voices", response_model=VoiceList)
async def speech_voices() -> VoiceList:
    if not engine.available():
        return VoiceList(voices=[], default=engine.voice())
    try:
        names = await asyncio.to_thread(engine.voices)
    except WorkerError as e:
        raise HTTPException(503, detail=str(e)) from e
    return VoiceList(voices=[SpeechVoice(**v) for v in voices.labeled(names)],
                     default=engine.voice())
