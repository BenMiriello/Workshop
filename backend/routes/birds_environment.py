"""Environment (weather/astro) read API for the Birds app.

Tailnet-private like the birds router (mounted with the same _SECURE deps in
main.py). Read-only over environment.db; the spine is populated by the
services.birds_environment refresh loop / CLI.
"""
from fastapi import APIRouter, Query

from services import birds_environment as env

router = APIRouter(prefix="/environment", tags=["environment"])


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
