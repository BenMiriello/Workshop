"""`/speech/commands`: the operator's spoken commands and the mode dictation
hears them in, for the manager in Settings > Speech and the dictation bar. The
live socket reads them at `hello` (services/speech/command_store.py,
services/speech/commands.py, services/speech/modes.py).
"""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.speech import command_store
from services.speech.command_list import NUMBER_MAX

router = APIRouter(prefix="/speech/commands", tags=["speech"])

Mode = Literal["dictation", "mixed", "commands"]
When = Literal["alone", "trigger", "off"]


class SpeechCommand(BaseModel):
    id: str
    label: str
    kind: Literal["inline", "action"]
    takes_arg: bool
    phrases: list[str]
    defaults: list[str]
    when: When


class CustomSpeechCommand(BaseModel):
    """The operator's own command: any of its phrases types `text`. A blank
    id is given one."""
    id: str = Field(default="", max_length=64)
    phrases: list[str] = Field(max_length=50)
    text: str = Field(min_length=1, max_length=20_000)
    when: When = "alone"


class SpeechLabels(BaseModel):
    """How "show labels" numbers the page: `max`, the most at once."""
    max: int = Field(ge=command_store.LABELS_MIN, le=NUMBER_MAX)


class SpeechCommands(BaseModel):
    mode: Mode
    trigger: str
    # The mode words were last written in, which "start writing" returns to.
    back: Literal["dictation", "mixed"]
    builtins: list[SpeechCommand]
    custom: list[CustomSpeechCommand]
    labels: SpeechLabels


class SpeechCommandChange(BaseModel):
    id: str
    phrases: list[str] = Field(max_length=50)
    when: When = "alone"


class SpeechModeChange(BaseModel):
    """`writing`: back to the mode words were last written in."""
    mode: Literal["dictation", "mixed", "commands", "writing"]
    trigger: str | None = Field(default=None, max_length=60)


class SpeechCommandsUpdate(BaseModel):
    builtins: list[SpeechCommandChange] = Field(default_factory=list)
    custom: list[CustomSpeechCommand] = Field(default_factory=list, max_length=500)


@router.get("", response_model=SpeechCommands)
async def get_commands() -> SpeechCommands:
    return SpeechCommands(**await asyncio.to_thread(command_store.current))


@router.put("", response_model=SpeechCommands)
async def put_commands(body: SpeechCommandsUpdate) -> SpeechCommands:
    saved = await asyncio.to_thread(
        command_store.save, [b.model_dump() for b in body.builtins],
        [c.model_dump() for c in body.custom])
    return SpeechCommands(**saved)


@router.put("/mode", response_model=SpeechCommands)
async def put_mode(body: SpeechModeChange) -> SpeechCommands:
    return SpeechCommands(**await asyncio.to_thread(command_store.set_mode, body.mode, body.trigger))


@router.put("/labels", response_model=SpeechCommands)
async def put_labels(body: SpeechLabels) -> SpeechCommands:
    return SpeechCommands(**await asyncio.to_thread(command_store.set_labels, body.model_dump()))


@router.post("/reset", response_model=SpeechCommands)
async def reset_commands() -> SpeechCommands:
    return SpeechCommands(**await asyncio.to_thread(command_store.reset))
