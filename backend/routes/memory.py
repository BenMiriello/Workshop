"""Semantic search over the workshop corpus, exposed for the app UI.

Agents reach the same service through the MCP workshop_query_server.
This is a thin HTTP shim so the operator can search from the browser.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query
from pydantic import BaseModel

from services import semantic_index

router = APIRouter(prefix="/memory", tags=["memory"])


class SearchHit(BaseModel):
    score: float
    kind: str
    ref: str
    title: str | None = None
    scope: str | None = None
    ts: str | None = None
    text: str
    status: str = ""


class SearchResult(BaseModel):
    hits: list[SearchHit]
    mode: str
    rebuilding: bool
    searched: int
    corpus: int


class IndexStatus(BaseModel):
    chunks: int
    dim: int
    by_kind: dict[str, int]
    model: str
    embedder_ok: bool
    embedder_error: str


@router.get("/search", response_model=SearchResult)
async def search(
    q: str,
    limit: int = Query(default=20, ge=1, le=50),
):
    # No from_scope: the operator owns every scope, and from_scope's only effect
    # is engaging the share policy in _keep_mask, which is the layer that erased
    # the document corpus for agents (finding-and-surfacing-memory-2026-08-23.md).
    return await asyncio.to_thread(semantic_index.search, q, limit=limit)


@router.get("/status", response_model=IndexStatus)
async def index_status():
    return await asyncio.to_thread(semantic_index.status)
