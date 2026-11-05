"""Errors the web UI could not show anywhere useful, kept where they can be read.

Mounts at /client-errors. A page that crashes tells the person only that it
did; the detail used to live in that tab's console, which nobody should be sent
to. The UI posts it here instead, one line per report in
`.runtime/client-errors.jsonl`.

    POST /client-errors   report one error (body: ClientError)
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter
from pydantic import BaseModel, Field

from wsplatform import paths

router = APIRouter(prefix="/client-errors", tags=["client-errors"])

_MAX = 4000


class ClientError(BaseModel):
    path: str = Field(max_length=500)
    name: str = Field(default="", max_length=200)
    message: str = Field(max_length=_MAX)
    stack: str = Field(default="", max_length=_MAX)
    component_stack: str = Field(default="", max_length=_MAX)
    user_agent: str = Field(default="", max_length=500)


class Reported(BaseModel):
    ok: bool


@router.post("", response_model=Reported)
def report(body: ClientError) -> Reported:
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **body.model_dump()}
    with (paths.runtime_dir() / "client-errors.jsonl").open("a") as fh:
        fh.write(json.dumps(line) + "\n")
    return Reported(ok=True)
