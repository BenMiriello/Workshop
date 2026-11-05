"""Sound the shell heard from something not on screen, kept where it can be read.

Mounts at /client-media. Media in a page view or app frame plays only while it
is on screen, and the shell enforces that itself; when it finds a page audible
that should be quiet, or has to let go of one that would not stop, the web UI
posts what it saw here, one line per report in `.runtime/client-media.jsonl`,
so a leak is read from the record instead of being noticed by ear.

    POST /client-media   report one event (body: ClientMedia)
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from wsplatform import paths

router = APIRouter(prefix="/client-media", tags=["client-media"])


class ClientMedia(BaseModel):
    event: str = Field(max_length=60)
    app: str = Field(default="", max_length=100)
    page: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=2000)
    shown_app: str = Field(default="", max_length=100)
    visible: bool = True
    held: bool | None = None
    muted: bool | None = None
    user_agent: str = Field(default="", max_length=500)


class Reported(BaseModel):
    ok: bool


@router.post("", response_model=Reported)
def report(body: ClientMedia) -> Reported:
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **body.model_dump()}
    with (paths.runtime_dir() / "client-media.jsonl").open("a") as fh:
        fh.write(json.dumps(line) + "\n")
    return Reported(ok=True)
