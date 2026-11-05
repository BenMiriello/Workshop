"""Storage: what the installation holds by app and kind, and the retention
settings. Model: `docs/platform-redesign/storage-retention-2026-10-11.md`."""
from __future__ import annotations

import time

import threading

from fastapi import APIRouter, HTTPException

from routes.storage_models import (CleanupPlan, CleanupStarted, PolicyChanges,
                                   ScanStarted, StorageSummary, TrashEntry)
from services import backend_schedule
from services.storage import expire, policy, report, trash

router = APIRouter(prefix="/storage", tags=["storage"])
STALE_SECONDS = 3600


@router.get("", response_model=StorageSummary)
def read_storage() -> dict:
    """The last scan, starting a fresh one when it is missing or over an hour
    old; `scanning` says one is under way."""
    last = report.latest()
    if last is None or time.time() - last.get("scanned_at", 0) > STALE_SECONDS:
        report.start()
    return report.summary()


@router.put("/policy", response_model=StorageSummary)
def put_policy(body: PolicyChanges) -> dict:
    try:
        policy.update(body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return report.summary()


@router.post("/scan", response_model=ScanStarted)
def start_scan() -> dict:
    started = report.start()
    return {"started": started, "scanning": report.scanning()}


@router.get("/cleanup", response_model=CleanupPlan)
def plan_cleanup() -> dict:
    """What an applied retention pass would do now. Changes nothing."""
    return expire.run(apply=False)


@router.post("/cleanup", response_model=CleanupStarted)
def start_cleanup() -> dict:
    """Run the retention pass now, in the background, under the scheduled
    pass's own lock; its receipt appears as `last_pass` on GET /storage."""
    now = backend_schedule.Pass(
        backend_schedule.STORAGE.name, 0, backend_schedule.STORAGE.timeout,
        lambda: backend_schedule.storage_command(gated=False))
    threading.Thread(target=backend_schedule.run_pass, args=(now,),
                     name="storage-cleanup", daemon=True).start()
    return {"started": True}


@router.get("/trash", response_model=list[TrashEntry])
def list_trash() -> list[dict]:
    return trash.entries()


@router.post("/trash/{entry_id}/restore", response_model=TrashEntry)
def restore_trash(entry_id: str) -> dict:
    try:
        return trash.restore(entry_id)
    except KeyError:
        raise HTTPException(404, f"no trash entry {entry_id}") from None
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
