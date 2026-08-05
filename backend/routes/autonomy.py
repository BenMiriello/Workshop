"""Autonomy power switches and GPU memory control.

Two switches for the Workshop autonomy stack:

  1. Cron autonomy disable/enable — engages the override overlay
     (services/autonomy_override), which every gate reads through one function.
     Halts the Ralph loop without touching crontab. This used to drop a separate
     AUTONOMY_DISABLED flag file that only the shell scripts read, so the API
     and the loop could disagree about whether autonomy was on; that flag is
     gone and this is the single switch.
  2. Ollama unload — sends keep_alive=0 to each currently-resident model so
     the GPU is freed for other workloads (ComfyUI, another tenant, etc.).
     Ollama itself keeps running; this only evicts resident models.

Plus read-only status endpoints feeding the Settings UI.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import shutil
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from services import autonomy_override, gpubroker_client, jobs
from pydantic import BaseModel


router = APIRouter(tags=["autonomy"])


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", "/home/clawd-a/workshop"))
QUEUE_RUNNER = WORKSHOP_ROOT / "scripts" / "queue_runner.sh"
QUEUE_WATCHDOG = WORKSHOP_ROOT / "scripts" / "queue_runner_watchdog.sh"
QUEUE_RUNNER_LOG = WORKSHOP_ROOT / ".runtime" / "queue-runner.log"
QUEUE_RUNNER_PID = WORKSHOP_ROOT / ".runtime" / "queue-runner.pid"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_lock_path(dir_str: str) -> Path:
    """The lock queue_runner.sh holds for a tree. Mirrors the shell derivation
    exactly: md5 of `printf '%s\\n' "$DIR"`, first 12 hex chars."""
    digest = hashlib.md5((dir_str + "\n").encode()).hexdigest()[:12]
    return Path(f"/tmp/workshop-queue-runner-{digest}.lock")


def _queue_runner_state() -> dict:
    """Whether a worker loop is ACTUALLY resident, by probing the runner's flock
    rather than trusting a flag file. This is the distinction the old status
    endpoint missed: the AUTONOMY_DISABLED flag being absent says only that work
    is *permitted*, not that anything is *running* — which is how the UI came to
    report "enabled (Ralph fires every 20m)" while no worker existed at all.

    `running` = a live process holds the lock. `enabled` = the off-switch is
    clear. Work only happens when BOTH are true, so the UI reports them
    separately instead of collapsing them into one misleading label."""
    lock = _queue_lock_path(str(WORKSHOP_ROOT))
    running = False
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        fd = None
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)  # we got it => nobody home
        except OSError:
            running = True
        finally:
            os.close(fd)

    pid = None
    try:
        pid = int(QUEUE_RUNNER_PID.read_text().strip())
    except (OSError, ValueError):
        pid = None
    # A pidfile outlives a SIGKILLed runner (the EXIT trap never fires), so it is
    # only reported when the lock says someone is actually there.
    if not running:
        pid = None

    last_line = None
    try:
        lines = [ln for ln in QUEUE_RUNNER_LOG.read_text(errors="replace").splitlines() if ln.strip()]
        last_line = lines[-1] if lines else None
    except OSError:
        last_line = None

    return {
        "running": running,
        "pid": pid,
        "enabled": not autonomy_override.is_engaged(),
        "lock_path": str(lock),
        "log_path": str(QUEUE_RUNNER_LOG),
        "last_line": last_line,
    }


# ============================================================
# Cron autonomy flag
# ============================================================


@router.post("/agents/autonomy/disable")
async def autonomy_disable():
    # Writes the ONE switch. This used to touch .runtime/AUTONOMY_DISABLED, a
    # second off-switch that only the shell loop read — so the API and the loop
    # could disagree about whether autonomy was on. That flag is gone; the
    # overlay is what every gate now reads (scripts/autonomy_gate.sh).
    rec = autonomy_override.engage("disabled from the API")
    return {"ok": True, "disabled": True, "at": rec.get("engaged_at")}


@router.post("/agents/autonomy/enable")
async def autonomy_enable():
    # Clearing this is now SUFFICIENT. Before, an operator could clear the flag
    # here and still have every tick skip, because the JSON overlay was engaged
    # and nothing surfaced that. One switch, one writer.
    autonomy_override.clear()
    return {"ok": True, "disabled": False}


@router.get("/agents/autonomy/status")
async def autonomy_status():
    # One source. `read()` already reports the union of everything that can hold
    # autonomy off, so the endpoint no longer stats a flag file of its own — that
    # second read is exactly how the UI came to report "enabled" while every
    # tick skipped.
    override = autonomy_override.read()
    disabled = bool(override.get("engaged"))
    return {
        "cron_autonomy": {
            "disabled": disabled,
            "disabled_at": override.get("engaged_at"),
            "reason": override.get("reason"),
        },
        "override": override,
        "queue_runner": _queue_runner_state(),
        "jobs": _jobs_summary(),
    }


# ============================================================
# JSON override overlay (autonomy-model 2026-07-18 §1.2)
# ============================================================
# The overlay is THE off-switch, and it is distinct from an individual agent's
# autonomy field: this is a brake over everything, that one is a setting.


class OverrideEngageIn(BaseModel):
    reason: str | None = None


@router.get("/agents/autonomy/override")
async def get_autonomy_override():
    """Current override state: {engaged, reason, engaged_at, engaged_by}. File
    absent reads as clear; corrupt / unreadable reads as engaged (fail-closed)."""
    return autonomy_override.read()


@router.post("/agents/autonomy/override")
async def engage_autonomy_override(body: OverrideEngageIn):
    """Engage the override. Every scope answers `reactive` for background
    dispatch until cleared. Per-agent autonomy fields are NEVER mutated."""
    autonomy_override.OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "engaged": True,
        "reason": (body.reason or "").strip() or None,
        "engaged_at": _now_iso(),
        "engaged_by": "operator",
    }
    import json as _json
    autonomy_override.OVERRIDE_PATH.write_text(_json.dumps(payload, indent=2))
    return autonomy_override.read()


@router.delete("/agents/autonomy/override")
async def clear_autonomy_override():
    """Clear the override. Each agent immediately resumes its configured mode
    without a restore step (the field was never mutated).

    There is now ONE switch to clear. This used to have to unlink a second flag
    file as well, because autonomy was gated by `flag.exists() or
    override.engaged` — two switches read as one truth. Clearing only the
    overlay left the flag holding the loop off while the UI reported the
    override cleared, with nothing on screen naming the thing still doing it.
    That is how the pause set for the 2026-07-18 migration survived until
    2026-08-04: whoever tried to lift it would have seen it "cleared" and still
    off. The flag is deleted and every gate reads this file."""
    autonomy_override.clear()
    return {**autonomy_override.read(), "cleared": ["override"]}


# ============================================================
# Queue worker loop (B0)
# ============================================================
# Start/stop the resident loop that drains the task queue. "Start" clears the
# off-switch AND ensures a runner is resident (via the watchdog, which is
# idempotent thanks to the runner's flock); "Stop" sets the off-switch and
# signals the runner, so a cron-restarted runner comes back idle rather than
# immediately resuming work.


@router.get("/queue/runner")
async def queue_runner_status():
    """Real loop state: is a worker resident, and is work permitted."""
    return _queue_runner_state()


@router.post("/queue/runner/start")
async def queue_runner_start():
    autonomy_override.clear()
    if not QUEUE_WATCHDOG.exists():
        raise HTTPException(500, f"watchdog missing: {QUEUE_WATCHDOG}")
    subprocess.Popen(
        ["bash", str(QUEUE_WATCHDOG)],
        cwd=str(WORKSHOP_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    # The watchdog spawns detached; give it a beat so the response reflects the
    # started runner rather than the pre-start state.
    await asyncio.sleep(1.5)
    return _queue_runner_state()


@router.post("/queue/runner/stop")
async def queue_runner_stop():
    """Set the off-switch and stop the resident runner + any in-flight sweep.
    The sweep's own lease/reclaim path returns a half-worked ticket to the queue,
    so stopping mid-run loses no tickets."""
    autonomy_override.engage("queue runner stopped from the API")
    state = _queue_runner_state()
    pid = state.get("pid")
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    await asyncio.sleep(1.5)
    return _queue_runner_state()


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
