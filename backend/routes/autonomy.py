"""Autonomy power switches and GPU memory control.

Two independent off-switches for the Workshop autonomy stack:

  1. Cron autonomy disable/enable — drops a flag file that cron_agent.sh
     and curriculum_tick.sh check at their top and early-exit on. Halts the
     Ralph loop without touching crontab.
  2. Ollama unload — sends keep_alive=0 to each currently-resident model so
     the GPU is freed for other workloads (ComfyUI, another tenant, etc.).
     Ollama itself keeps running; this only evicts resident models.

Plus read-only status endpoints feeding the Settings UI.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from services import gpubroker_client, jobs


router = APIRouter(tags=["autonomy"])


AUTONOMY_FLAG = Path(os.environ.get(
    "WORKSHOP_AUTONOMY_FLAG",
    "/home/clawd-a/workshop/.runtime/AUTONOMY_DISABLED",
))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Cron autonomy flag
# ============================================================


@router.post("/agents/autonomy/disable")
async def autonomy_disable():
    AUTONOMY_FLAG.parent.mkdir(parents=True, exist_ok=True)
    AUTONOMY_FLAG.write_text(_now_iso() + "\n")
    return {"ok": True, "disabled": True, "at": _now_iso()}


@router.post("/agents/autonomy/enable")
async def autonomy_enable():
    try:
        AUTONOMY_FLAG.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "disabled": False}


@router.get("/agents/autonomy/status")
async def autonomy_status():
    flag_text = None
    if AUTONOMY_FLAG.exists():
        try:
            flag_text = AUTONOMY_FLAG.read_text().strip()
        except OSError:
            flag_text = ""
    return {
        "cron_autonomy": {
            "disabled": AUTONOMY_FLAG.exists(),
            "disabled_at": flag_text,
            "flag_path": str(AUTONOMY_FLAG),
        },
        "jobs": _jobs_summary(),
    }


def _jobs_summary(limit: int = 200) -> dict:
    """Recent Job activity for the autonomy overview, sourced from the Job
    store (replaces the old agent-runs.jsonl tick counts)."""
    recent = jobs.read(limit=limit)
    by_status: dict[str, int] = {}
    for r in recent:
        st = r.get("status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "recent_count": len(recent),
        "by_status": by_status,
        "running": by_status.get("running", 0),
        "last": recent[0] if recent else None,
    }


# ============================================================
# Ollama GPU control
# ============================================================


def _nvidia_smi_snapshot() -> dict:
    """Single nvidia-smi sample of memory.used / memory.free / memory.total /
    utilization.gpu. Returns dict with int MiB / percent. Missing nvidia-smi
    (e.g. running on a workstation without it) returns {available: False}."""
    if shutil.which("nvidia-smi") is None:
        return {"available": False}
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.free,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            timeout=3,
        ).decode().strip()
    except (subprocess.SubprocessError, OSError) as e:
        return {"available": False, "error": str(e)}
    # First GPU only — host has a single 24GB card.
    first_line = out.splitlines()[0].strip()
    parts = [p.strip() for p in first_line.split(",")]
    if len(parts) < 4:
        return {"available": False, "error": f"unexpected nvidia-smi output: {out!r}"}
    try:
        used_mib = int(parts[0])
        free_mib = int(parts[1])
        total_mib = int(parts[2])
        util_pct = int(parts[3])
    except ValueError as e:
        return {"available": False, "error": str(e)}
    return {
        "available": True,
        "memory_used_mib": used_mib,
        "memory_free_mib": free_mib,
        "memory_total_mib": total_mib,
        "utilization_pct": util_pct,
    }


@router.get("/ollama/status")
async def ollama_status():
    """Snapshot of Ollama + GPU state for the Settings UI."""
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            r = await client.get(f"{OLLAMA_URL}/api/ps")
            r.raise_for_status()
            loaded = r.json().get("models", []) or []
            ollama_up = True
        except (httpx.HTTPError, ValueError):
            loaded = []
            ollama_up = False
    return {
        "ollama_up": ollama_up,
        "loaded_models": [
            {
                "name": m.get("name"),
                "size_vram_bytes": m.get("size_vram"),
                "expires_at": m.get("expires_at"),
            }
            for m in loaded
        ],
        "gpu": _nvidia_smi_snapshot(),
    }


async def _unload_one(client: httpx.AsyncClient, model: str) -> dict:
    """Tell Ollama to evict `model` by sending keep_alive=0 with an empty
    prompt. This is the documented unload mechanism."""
    try:
        r = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "keep_alive": 0, "prompt": ""},
            timeout=10.0,
        )
        return {"model": model, "ok": r.status_code < 400, "status": r.status_code}
    except httpx.HTTPError as e:
        return {"model": model, "ok": False, "error": str(e)}


@router.post("/ollama/unload")
async def ollama_unload():
    """Evict every currently-resident Ollama model so the GPU is freed.

    Returns the post-unload GPU snapshot so the UI can show the freed memory
    immediately."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{OLLAMA_URL}/api/ps")
            r.raise_for_status()
            loaded = r.json().get("models", []) or []
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(status_code=502, detail=f"ollama unreachable: {e}")
        if not loaded:
            return {
                "ok": True,
                "unloaded": [],
                "gpu": _nvidia_smi_snapshot(),
                "note": "no models were resident",
            }
        results = await asyncio.gather(*(
            _unload_one(client, m["name"]) for m in loaded if m.get("name")
        ))
    # Give Ollama a moment to actually release VRAM before snapshotting.
    await asyncio.sleep(1.0)
    return {
        "ok": all(r["ok"] for r in results),
        "unloaded": results,
        "gpu": _nvidia_smi_snapshot(),
    }


# ============================================================
# gpubrokerd proxy + Workshop-specific broker actions
# Workshop's local off-switch (above) remains the authoritative control plane.
# The broker is additive: if up, Workshop participates as `workshop-overseer`.
# If down, the UI shows that and these endpoints return gracefully.
# ============================================================


@router.get("/gpu/broker/status")
async def broker_status():
    s = await gpubroker_client.status()
    if not s.get("available"):
        return {"available": False}
    mine = await gpubroker_client.my_leases()
    return {**s, "my_leases": mine}


@router.post("/gpu/broker/register")
async def broker_register():
    """Idempotent app registration. Called on backend startup; exposed here
    so the operator can re-register from the UI if the broker was restarted."""
    ok = await gpubroker_client.ensure_registered()
    return {"registered": ok}


@router.post("/gpu/broker/reserve")
async def broker_reserve(body: dict):
    """Operator hold: acquire a high-priority short-TTL lease so other apps
    yield. Body: {duration_s: int, reason: str}."""
    duration_s = int(body.get("duration_s", 1800))
    reason = body.get("reason", "operator hold")
    res = await gpubroker_client.reserve_exclusive(duration_s, reason)
    if res is None:
        raise HTTPException(status_code=503, detail="gpubrokerd unavailable")
    return res


@router.post("/gpu/broker/release-mine")
async def broker_release_mine():
    n = await gpubroker_client.release_all_mine()
    return {"released_count": n}


@router.post("/gpu/broker/boost")
async def broker_boost(body: dict):
    pri = int(body.get("priority", 80))
    ttl = int(body.get("ttl_s", 1800))
    res = await gpubroker_client.boost(pri, ttl)
    if res is None:
        raise HTTPException(status_code=503, detail="gpubrokerd unavailable")
    return res
