"""Service registry. Probes the health of the processes this install expects.

Each probe is a HEAD/GET against a known health path with a tight timeout. No
background polling — checks run on request so the response reflects the current
state, not a stale cache.

Two sources, because there are two kinds of process. The PLATFORM's own are
listed here. An app that runs out of process (a separate backend, a worker, a
dashboard) declares its own on its AppSpec, so its probes exist exactly while it
is installed and a fresh instance does not report three wearables services down
because it inherited someone else's roster.

This route also carries `credentials`, which are NOT services and are kept in
their own key rather than folded into the list. A process is up or down at a
URL; a credential is present, valid, or unprovable, and `auth_health` reports
which of those it actually established. Flattening the two would mean inventing
a URL for a token and a confidence level for a port.
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


PLATFORM_SERVICES: list[Service] = [
    Service("workshop-backend", "core",
            os.environ.get("WORKSHOP_BACKEND_URL", "http://127.0.0.1:8000"), "/healthz"),
    Service("workshop-frontend", "core",
            os.environ.get("WORKSHOP_FRONTEND_URL", "http://127.0.0.1:5173"), "/"),
    Service("ollama", "models",
            os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"), "/api/tags"),
]


def services() -> list[Service]:
    """Platform processes plus whatever the installed apps expect."""
    from services import app_registry

    out = list(PLATFORM_SERVICES)
    for name, category, url, probe_path, expect in app_registry.installed_service_probes():
        out.append(Service(name, category, url, probe_path, expect_status=expect))
    return out


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
async def list_services(refresh: bool = False):
    from services import auth_health

    async with httpx.AsyncClient() as client:
        results, (creds, checked_at, cached) = await asyncio.gather(
            asyncio.gather(*(_probe(client, s) for s in services())),
            auth_health.cached_check_all(force=refresh),
        )
    return {
        "services": results,
        "summary": {
            "up": sum(1 for r in results if r["status"] == "up"),
            "degraded": sum(1 for r in results if r["status"] == "degraded"),
            "down": sum(1 for r in results if r["status"] == "down"),
        },
        "credentials": creds,
        "credentials_summary": auth_health.summarize(creds),
        "credentials_checked_at": checked_at,
        "credentials_cached": cached,
    }
