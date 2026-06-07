"""HTTP API for the redaction service.

Endpoints:
    POST /redact            body: {"text": str, "skip_ollama": bool=False}
                            returns: {"redacted": str, "mapping": {placeholder: original},
                                      "stats": {regex_hits, ollama_hits, ollama_used, elapsed_s}}
    POST /restore           body: {"text": str, "mapping": dict}
                            returns: {"restored": str}
    GET  /redact/health     returns: {"ollama_available": bool, "tier_status": {...}}
"""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter
from pydantic import BaseModel

from services.redaction import redact, restore
from services.llm_router import status as router_status, ollama_available


router = APIRouter(prefix="/redact", tags=["redaction"])


class RedactRequest(BaseModel):
    text: str
    skip_ollama: bool = False


class RestoreRequest(BaseModel):
    text: str
    mapping: Dict[str, str]


@router.post("")
def do_redact(req: RedactRequest) -> dict:
    r = redact(req.text, skip_ollama=req.skip_ollama)
    return {
        "redacted": r.redacted_text,
        "mapping": r.mapping,
        "stats": {
            "regex_hits": r.regex_hits,
            "ollama_hits": r.ollama_hits,
            "ollama_used": r.ollama_used,
            "elapsed_s": r.elapsed_s,
        },
    }


# Separate router so the path is /restore (not /redact/restore)
restore_router = APIRouter(tags=["redaction"])


@restore_router.post("/restore")
def do_restore(req: RestoreRequest) -> dict:
    return {"restored": restore(req.text, req.mapping)}


@router.get("/health")
def redact_health() -> dict:
    return {
        "ollama_available_for_redaction": ollama_available("redaction"),
        "router_status": router_status(),
    }
