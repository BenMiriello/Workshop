"""A dictation ending, told to the apps subscribed to `dictated`, and its
recording for them to copy (docs/dictation-and-clipboard.md, "Events for apps").

  PUT  /speech/dictations/{id}     the client, while a dictation runs: its
                                   pieces so far, kept for a day
                                   (services/speech/dictation_log.py).
  GET  /speech/dictations          the last day of dictations, newest first.
  POST /speech/dictations          the client, when a dictation ends (or once
                                   per entry of a device's past history, with
                                   `imported`). Answers how many subscribers
                                   took it, so the client clears imported
                                   history only once one has.
  GET  /speech/recordings/{id}     the recording as WAV. Loopback only, and
                                   refused at the public edge
                                   (workshop_public_app._BLOCKED_PREFIXES):
                                   it is for the subscribers' own servers.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from services import app_events, installation
from services.speech import dictation_log, recordings

router = APIRouter(prefix="/speech/dictations", tags=["speech"])
recordings_router = APIRouter(prefix="/speech/recordings", tags=["speech"])


class Piece(BaseModel):
    place: app_events.Place
    text: str = Field(max_length=app_events.MAX_TEXT)
    state: Literal["placed", "waiting", "clipboard", "unplaced", "sent"]
    at: str = Field(max_length=64)


class DictationSoFar(BaseModel):
    started_at: str = Field(max_length=64)
    pieces: list[Piece] = Field(default_factory=list, max_length=500)


class KeptDictation(BaseModel):
    id: str
    started_at: str
    updated_at: str
    ended_at: str | None = None
    status: Literal["live", "done", "cancelled", "interrupted"]
    pieces: list[Piece]


class KeptDictations(BaseModel):
    dictations: list[KeptDictation]


class DictationEnded(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=app_events.MAX_TEXT)
    places: list[app_events.Place] = Field(default_factory=list, max_length=100)
    pieces: list[Piece] | None = Field(default=None, max_length=500)
    started_at: str = Field(max_length=64)
    ended_at: str = Field(max_length=64)
    status: Literal["done", "cancelled"] = "done"
    imported: bool = False


class DictationDelivered(BaseModel):
    delivered: int
    subscribers: int


def recording_url(dictation: str) -> str:
    port = installation.backend_port() or 8000
    return f"http://127.0.0.1:{port}/api/speech/recordings/{dictation}"


def _audio(body: DictationEnded) -> app_events.DictatedAudio | None:
    if body.imported:
        return None
    ms = recordings.length_ms(body.id)
    if not ms:
        return None
    return app_events.DictatedAudio(url=recording_url(body.id), mime="audio/wav", ms=ms)


@router.put("/{dictation}", status_code=204)
async def dictation_so_far(dictation: str, body: DictationSoFar) -> Response:
    if not recordings.valid(dictation):
        raise HTTPException(400, detail="bad dictation id")
    pieces = [p.model_dump() for p in body.pieces]
    await asyncio.to_thread(dictation_log.save, dictation, body.started_at, pieces)
    return Response(status_code=204)


@router.get("", response_model=KeptDictations)
async def dictations_kept() -> KeptDictations:
    entries = await asyncio.to_thread(dictation_log.recent)
    return KeptDictations(dictations=[KeptDictation(**e) for e in entries])


@router.post("", response_model=DictationDelivered)
async def dictation_ended(body: DictationEnded) -> DictationDelivered:
    if body.pieces is not None and not body.imported and recordings.valid(body.id):
        pieces = [p.model_dump() for p in body.pieces]
        await asyncio.to_thread(
            dictation_log.save, body.id, body.started_at, pieces, body.ended_at, body.status)
    fields = body.model_dump(exclude={"places", "pieces"})
    event = app_events.Dictated(**fields, places=body.places, audio=_audio(body))
    results = await asyncio.to_thread(app_events.deliver, "dictated", event.model_dump())
    ok = sum(1 for _, status in results if isinstance(status, int) and status < 300)
    return DictationDelivered(delivered=ok, subscribers=len(results))


@recordings_router.get("/{dictation}")
async def dictation_recording(dictation: str) -> Response:
    data = await asyncio.to_thread(recordings.wav, dictation)
    if data is None:
        raise HTTPException(404, detail="no such recording")
    return Response(content=data, media_type="audio/wav", headers={"Cache-Control": "no-store"})
