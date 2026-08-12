"""Voice mode surface — local STT + TTS for the Workshop chat.

Presentation/capture layer only: a spoken utterance is transcribed here and then
sent through the ordinary chat path by the frontend; a reply is spoken by asking
`/voice/tts` for audio one sentence at a time. No new LLM/auth surface.

Mounted at `/voice` with the per-user gate, so `current_owner()` is pinned and
per-owner prefs live under `.runtime/users/<owner>/voice-prefs.json`.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services import chat_store, voice_engine, voice_worker
from services.gpu_warmhold import VOICE_HOLD
from services.ownership import user_runtime_dir

log = logging.getLogger("workshop.voice")
router = APIRouter()

# NOTE: enter_interrupts / typing_hold_ms / eager_silence_ms / verbosity are no
# longer surfaced in the settings UI (Phase 4 rework) — kept here for compat so
# existing stored prefs files and API clients still round-trip.
_PREFS_DEFAULTS = {
    "enabled": True,
    "tts_provider": "auto",   # auto | server | browser | off
    "stt_provider": "auto",   # auto | server | browser
    "voice": voice_engine.DEFAULT_VOICE,
    "speed": 1.0,
    "verbosity": "normal",    # terse | normal
    # The modality matrix — the single source of truth for how a conversation is
    # carried. Any of the nine combinations is legal (talk-in/read-out,
    # type-in/listen-out, either side, both sides).
    "input_mode": "both",     # voice | text | both  — which capture lanes run
    "output_mode": "both",    # voice | text | both  — spoken and/or captioned
    # Legacy booleans, kept because stored prefs files and the API carry them.
    # They are DERIVED from the modes on read and fold back into them on write,
    # so old clients and old files keep working; see _normalize_modes.
    "mic_enabled": True,      # input lane; off = composer-only
    "speak_replies": True,    # voice-output lane; replaces the old tts "off" mute
    "typing_hold": True,      # typing defers the voice endpoint
    "typing_hold_ms": 3000,
    "enter_interrupts": True,  # Enter sends and interrupts ongoing speech
    # Trailing silence that ends an utterance (server VAD).
    #
    # This was raised to 1400ms to stop a mid-thought pause splitting one sentence
    # into several messages. That was the wrong lever: it bought fewer splits by
    # making every single turn wait longer, which read as sluggish, and it still
    # split anyone who paused for longer than the threshold.
    #
    # Firing early is RECOVERABLE now (see voiceTurnAction / reclaimTurn: carrying
    # on speaking takes the turn back and merges), so the threshold can go back to
    # being responsive. It no longer has to be right, only close.
    "endpoint_silence_ms": 1000,
    "eager_endpoint": True,   # speculative endpointing: think during an early pause
    "eager_silence_ms": 400,  # the early-pause threshold (< endpoint_silence_ms)
    "barge_in": True,
    "barge_sensitivity": 0.5,  # 0..1 VAD threshold knob (client-side)
    # How soon it takes a turn: snappy | natural | patient. ONE number, applied
    # as a bias on the turn-end model's threshold and its silence ceiling.
    #
    # It replaces four knobs that all tried to answer this question with timers
    # and could not: endpoint_silence_ms (no value works — short splits a
    # thought, long makes every turn sluggish), eager_endpoint and
    # eager_silence_ms (speculative drafts, which semantic endpointing makes
    # unnecessary), and barge_sensitivity, which was labelled as an interruption
    # preference and was actually the master gate on whether you were heard at
    # all. The four above are kept only so stored prefs files still round-trip.
    "reply_pace": "natural",
    # rolling adaptation signal — updated by the client per session
    "interrupt_rate": 0.0,
}


def _prefs_path():
    return user_runtime_dir() / "voice-prefs.json"


_MODES = ("voice", "text", "both")


def _normalize_modes(data: dict, patch: dict | None = None) -> dict:
    """Keep the modality matrix and the legacy booleans in agreement.

    Whichever side the caller wrote wins: a patch naming `input_mode` /
    `output_mode` drives the booleans, and a patch naming only `mic_enabled` /
    `speak_replies` (an older client, or a stored file written before the matrix
    existed) drives the modes. With neither, the modes are authoritative."""
    touched = set(patch or {})
    if "input_mode" not in touched and "mic_enabled" in touched:
        data["input_mode"] = "both" if data["mic_enabled"] else "text"
    if "output_mode" not in touched and "speak_replies" in touched:
        data["output_mode"] = "both" if data["speak_replies"] else "text"
    if data.get("input_mode") not in _MODES:
        data["input_mode"] = "both"
    if data.get("output_mode") not in _MODES:
        data["output_mode"] = "both"
    data["mic_enabled"] = data["input_mode"] != "text"
    data["speak_replies"] = data["output_mode"] != "text"
    return data


def _load_prefs() -> dict:
    p = _prefs_path()
    data = dict(_PREFS_DEFAULTS)
    stored: dict = {}
    if p.exists():
        try:
            stored = json.loads(p.read_text())
            data.update(stored)
        except (ValueError, OSError):
            stored = {}
    # A file written before the matrix existed carries only the booleans; let
    # them define the modes rather than silently resetting to the default.
    return _normalize_modes(data, {k: v for k, v in stored.items() if k in _PREFS_DEFAULTS})


def _save_prefs(patch: dict) -> dict:
    data = _load_prefs()
    for k, v in (patch or {}).items():
        if k in _PREFS_DEFAULTS:
            data[k] = v
    _normalize_modes(data, patch)
    p = _prefs_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)
    return data


@router.get("/voice/health")
async def voice_health():
    """Engine truth, including WHICH perception tier is actually running.

    The tier is reported rather than configured: the settings sheet must be able
    to say "energy VAD, heuristic turn detection" when the ONNX weights are not
    on disk, instead of implying an accuracy the box does not have."""
    from services.voice import detectors

    return {**voice_engine.health(), "perception": detectors.status()}


@router.get("/voice/voices")
async def voice_voices():
    if not voice_engine.TTS.available:
        return {"available": False, "voices": []}
    return {"available": True, "voices": voice_engine.TTS.voices()}


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
    input_mode: str | None = None
    output_mode: str | None = None
    mic_enabled: bool | None = None
    speak_replies: bool | None = None
    typing_hold: bool | None = None
    typing_hold_ms: int | None = None
    enter_interrupts: bool | None = None
    endpoint_silence_ms: int | None = None
    eager_endpoint: bool | None = None
    eager_silence_ms: int | None = None
    barge_in: bool | None = None
    barge_sensitivity: float | None = None
    reply_pace: str | None = None
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
    """Prewarm the resident worker for a voice session. Called on surface mount
    so the process is up before the first utterance finishes.

    IT DOES NOT CREATE A CHAT. It used to, for any call naming no thread, and
    then discarded the id: the caller is a fire-and-forget `fetch` that reads no
    response, so every prewarm on a new chat left an empty "Voice" thread nobody
    could be handed. Warming is keyed by thread anyway, so there was never
    anything to warm there. A session with no thread yet gets one when it is
    spoken to (services/voice/runtime.VoiceRuntime._thread)."""
    thread_id = body.thread_id
    if not thread_id or not chat_store.get_thread(thread_id):
        return {"session_key": None, "thread_id": None, "warmed": False}
    await voice_worker.ensure_worker(body.context_key, thread_id, body.model)
    return {"session_key": thread_id, "thread_id": thread_id, "warmed": True}


# The per-turn REST surface (POST /turns, GET /turns/{id}/stream,
# /cancel, /commit) is GONE. A turn used to be assembled out of a POST, an SSE
# stream and two more POSTs racing each other, which is why the client needed an
# epoch counter to make stale continuations no-ops. It is one socket now:
# /voice/stream (routes/voice_stream.py). `voice_worker` still owns generation
# and is driven directly by services/voice/runtime.py.
#
# What stays here: prefs, the voice catalog, health, and session prewarm.
#
# The one-shot POST /voice/stt and POST /voice/tts are GONE, and so is DELETE
# /voice/session/{thread_id}. They were the v1 request-per-utterance path that
# the socket replaced, and they had no caller left in the app: the live path is
# services/voice/runtime driving the engines directly, and a session is released
# when its socket closes. Two ways to do the same thing, one of them untested
# and unreachable, is how the surface drifts from what actually runs.


