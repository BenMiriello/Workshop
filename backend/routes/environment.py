"""Environment (weather/astro) read API for the Birds app.

Tailnet-private like the birds router (mounted with the same _SECURE deps in
main.py). Read-only over environment.db; the spine is populated by the
services.environment refresh loop / CLI.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from services import environment as env

router = APIRouter(prefix="/environment", tags=["environment"])


@router.get("/status")
def env_status():
    return env.status()


@router.get("/astro")
def astro(location_id: int = Query(...), date: str = Query(...)):
    """Sun/moon for a location + civil date (YYYY-MM-DD)."""
    return env.get_astro(location_id, date) or {}


@router.get("/conditions")
def conditions(
    location_id: int = Query(...),
    start: str = Query(..., description="ISO-8601 UTC"),
    end: str = Query(..., description="ISO-8601 UTC"),
):
    """Hourly weather for a location in [start, end]."""
    return {"conditions": env.get_conditions(location_id, start, end)}


@router.get("/day")
def day(location_id: int = Query(...), date: str = Query(...)):
    """Convenience: astro for `date` plus that UTC day's hourly conditions.
    (Frontend day charts render in local time; it can request a wider
    /conditions range when it needs to align to a local day.)"""
    d = datetime.fromisoformat(date).date()
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()
    end = (datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
           + timedelta(hours=23, minutes=59)).isoformat()
    return {
        "astro": env.get_astro(location_id, date) or {},
        "conditions": env.get_conditions(location_id, start, end),
    }
