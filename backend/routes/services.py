"""Service registry. Probes the health of known Workshop services.

The list is static (defined here); each probe is a HEAD/GET against a known
health path with a tight timeout. No background polling — checks run on
request so the response reflects the current state, not stale cache.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter


router = APIRouter(prefix="/services", tags=["services"])


@dataclass(frozen=True)
class Service:
    name: str
    category: str
    url: str
    probe_path: str = "/"
    method: str = "GET"
    expect_status: tuple[int, ...] = (200,)
    notes: str = ""


SERVICES: list[Service] = [
    Service("workshop-backend", "core", "http://127.0.0.1:8000", "/healthz"),
    Service("workshop-frontend", "core", "http://127.0.0.1:5173", "/"),
    Service("letta", "memory", "http://127.0.0.1:8283", "/v1/health/", expect_status=(200, 404)),
    Service("ollama", "models", "http://127.0.0.1:11434", "/api/tags"),
    Service("open-wearables-backend", "wearables", "http://127.0.0.1:8100", "/", expect_status=(200, 301, 302, 404)),
    Service("open-wearables-frontend", "wearables", "http://127.0.0.1:3100", "/", expect_status=(200, 301, 302)),
    Service("open-wearables-flower", "wearables", "http://127.0.0.1:5556", "/", expect_status=(200, 401)),
]


async def _probe(client: httpx.AsyncClient, s: Service) -> dict:
    t0 = time.monotonic()
    try:
        r = await client.request(s.method, s.url + s.probe_path, timeout=2.0)
        ms = int((time.monotonic() - t0) * 1000)
        ok = r.status_code in s.expect_status
        return {
            "name": s.name,
            "category": s.category,
            "url": s.url,
            "probe_path": s.probe_path,
            "status": "up" if ok else "degraded",
            "http_status": r.status_code,
            "latency_ms": ms,
            "notes": s.notes,
        }
    except Exception as e:
        return {
            "name": s.name,
            "category": s.category,
            "url": s.url,
            "probe_path": s.probe_path,
            "status": "down",
            "http_status": None,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "error": str(e),
            "notes": s.notes,
        }


@router.get("/")
async def list_services():
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(_probe(client, s) for s in SERVICES))
    return {
        "services": results,
        "summary": {
            "up": sum(1 for r in results if r["status"] == "up"),
            "degraded": sum(1 for r in results if r["status"] == "degraded"),
            "down": sum(1 for r in results if r["status"] == "down"),
        },
    }
