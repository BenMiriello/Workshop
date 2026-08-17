"""Privacy relay — routes prompts to local Qwen (Ollama) for boilerplate work,
keeps Claude reserved for architecture-class reasoning. Skeleton implementation;
classification is heuristic for now and can be upgraded to a small fine-tuned
classifier later."""
from __future__ import annotations

import os
import re
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from wsplatform import paths


router = APIRouter(prefix="/relay", tags=["relay"])

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
LOCAL_MODEL = os.environ.get("RELAY_LOCAL_MODEL", "qwen2.5:32b")

# Keywords that suggest "boilerplate / mechanical / local-suitable" work.
BOILERPLATE_HINTS = [
    "format", "rename", "convert", "translate to", "summarize",
    "extract", "list", "grep", "regex", "csv", "json", "yaml",
    "lowercase", "uppercase", "trim", "split", "join",
]
# Keywords that suggest "architecture / multi-step / cloud-class" work.
ARCHITECTURE_HINTS = [
    "design", "architecture", "refactor", "plan", "trade-off",
    "evaluate", "decide", "recommend", "review", "audit",
    "compare", "explain why", "implement", "build", "debug",
]


class RouteRequest(BaseModel):
    task: str
    context: str | None = None


class RouteDecision(BaseModel):
    route: Literal["local", "cloud"]
    reason: str
    local_model: str
    cloud_model: str = "claude (via the bridge)"


class LocalRequest(BaseModel):
    task: str
    context: str | None = None
    max_tokens: int = 1024


class LocalResponse(BaseModel):
    model: str
    response: str
    duration_ms: int


def _classify(task: str) -> RouteDecision:
    t = task.lower()
    local_hits = sum(1 for k in BOILERPLATE_HINTS if k in t)
    cloud_hits = sum(1 for k in ARCHITECTURE_HINTS if k in t)
    if cloud_hits > local_hits:
        return RouteDecision(route="cloud", reason=f"architecture_hints={cloud_hits} > boilerplate_hints={local_hits}", local_model=LOCAL_MODEL)
    if local_hits > 0 and cloud_hits == 0:
        return RouteDecision(route="local", reason=f"boilerplate_hints={local_hits}, no architecture_hints", local_model=LOCAL_MODEL)
    # Short tasks default local (cheap to try); longer ones go cloud
    if len(re.findall(r"\w+", task)) < 30:
        return RouteDecision(route="local", reason="short task (<30 words), default local", local_model=LOCAL_MODEL)
    return RouteDecision(route="cloud", reason="default for non-trivial task with no clear local signal", local_model=LOCAL_MODEL)


RELAY_CORPUS = str(paths.env_path("WORKSHOP_RELAY_CORPUS", "relay-corpus.jsonl"))


def _log_decision(task: str, decision: RouteDecision) -> None:
    """Append every routed decision for future heuristic tuning."""
    import json, time
    try:
        with open(RELAY_CORPUS, "a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "task": task[:500],  # cap for storage hygiene
                "route": decision.route,
                "reason": decision.reason,
                # `did_it_work` is filled in later by the caller via /relay/feedback
                "did_it_work": None,
            }) + "\n")
    except OSError:
        pass  # corpus logging is best-effort


@router.post("/classify", response_model=RouteDecision)
async def classify(req: RouteRequest) -> RouteDecision:
    """Pure classification — no execution. Caller decides what to do with the decision.
    Also logs the decision to the relay corpus for future heuristic tuning."""
    decision = _classify(req.task)
    _log_decision(req.task, decision)
    return decision


@router.post("/local", response_model=LocalResponse)
async def execute_local(req: LocalRequest) -> LocalResponse:
    """Execute a task against the local Ollama Qwen model. Use this when classify
    returned route=local, or when the caller wants to force local execution."""
    import time
    prompt = req.task if not req.context else f"{req.context}\n\n---\n\n{req.task}"
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": LOCAL_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": req.max_tokens},
            },
        )
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ollama error {r.status_code}: {r.text[:200]}")
    data = r.json()
    return LocalResponse(
        model=LOCAL_MODEL,
        response=data.get("response", ""),
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/auto", response_model=LocalResponse | RouteDecision)
async def route_and_execute(req: RouteRequest):
    """Classify; if local, execute and return. If cloud, return the decision
    (caller is responsible for forwarding to the bridge/Claude — relay does NOT
    spend Claude tokens automatically)."""
    decision = _classify(req.task)
    if decision.route == "local":
        local_req = LocalRequest(task=req.task, context=req.context)
        return await execute_local(local_req)
    return decision


class ExplainResponse(BaseModel):
    task: str
    word_count: int
    boilerplate_hits: list[str]
    architecture_hits: list[str]
    decision: RouteDecision


@router.post("/explain", response_model=ExplainResponse)
async def explain(req: RouteRequest) -> ExplainResponse:
    """Debug helper — same classification logic as /classify, but exposes the
    matched keyword sets and word count so the operator (or a tuning loop)
    can see exactly WHY a task routed local vs cloud. Does NOT execute."""
    t = req.task.lower()
    local_hits = [k for k in BOILERPLATE_HINTS if k in t]
    cloud_hits = [k for k in ARCHITECTURE_HINTS if k in t]
    decision = _classify(req.task)
    return ExplainResponse(
        task=req.task,
        word_count=len(re.findall(r"\w+", req.task)),
        boilerplate_hits=local_hits,
        architecture_hits=cloud_hits,
        decision=decision,
    )
