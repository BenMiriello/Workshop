"""Commute API routes — surfaces the data + scoring + alert engine from
backend/services/commute/ to the frontend Commute page and to the cron
checkpointers.

Endpoints (mounted at /commute/):
    GET  /commute/status              — current brief (active schedule, leg, ETA, late_risk)
    GET  /commute/state               — latest raw poller state (caltrain.json + sfmta.json + status)
    POST /commute/poll                — trigger a poll right now (runs in foreground)
    GET  /commute/schedule            — full schedule.json
    POST /commute/schedule            — replace schedule.json (UI editor)
    GET  /commute/targets             — full targets.json
    POST /commute/targets             — replace targets.json
    GET  /commute/legs                — legs.json
    GET  /commute/sources             — sources registry + per-source status
    GET  /commute/alerts/recent       — recent alert-log entries
    POST /commute/alerts/check        — score + maybe_alert (no force)
    POST /commute/alerts/force        — force-send a late-risk alert (testing)
    POST /commute/llm/decide          — invoke LLM decision with a checkpoint
    GET  /commute/llm/recent          — recent LLM decisions
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.commute import alerter, config, llm, poller, scoring


router = APIRouter(prefix="/commute", tags=["commute"])


@router.get("/status")
async def status() -> dict[str, Any]:
    return scoring.evaluate()


@router.get("/state")
async def state() -> dict[str, Any]:
    return poller.read_state()


@router.post("/poll")
async def trigger_poll() -> dict[str, Any]:
    return await poller.poll_all()


@router.get("/schedule")
async def get_schedule() -> dict[str, Any]:
    return config.load_schedule()


@router.post("/coords")
async def set_coords(body: dict[str, Any]) -> dict[str, Any]:
    """Set home/work lat,lng on schedule.json. Operator pastes coords from
    a map app — we don't geocode addresses externally because that would
    leak residential location to a third party."""
    name = body.get("name")
    if name not in ("home", "work"):
        raise HTTPException(status_code=400, detail="name must be 'home' or 'work'")
    try:
        lat = float(body.get("lat"))
        lon = float(body.get("lon"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lat/lon must be numbers")
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise HTTPException(status_code=400, detail="lat/lon out of range")
    sch = config.load_schedule()
    entry = sch.setdefault(name, {})
    entry["lat"] = lat
    entry["lon"] = lon
    config.write_schedule(sch)
    return {"ok": True, "name": name, "lat": lat, "lon": lon}


class ScheduleBody(BaseModel):
    data: dict[str, Any]


@router.post("/schedule")
async def post_schedule(body: ScheduleBody) -> dict[str, Any]:
    if not isinstance(body.data, dict) or "schedules" not in body.data:
        raise HTTPException(status_code=400, detail="schedule.json must include 'schedules' list")
    config.write_schedule(body.data)
    return {"ok": True}


@router.get("/targets")
async def get_targets() -> dict[str, Any]:
    return config.load_targets()


@router.post("/targets")
async def post_targets(body: ScheduleBody) -> dict[str, Any]:
    if not isinstance(body.data, dict) or "policy" not in body.data:
        raise HTTPException(status_code=400, detail="targets.json must include 'policy'")
    config.write_targets(body.data)
    return {"ok": True}


@router.get("/legs")
async def get_legs() -> dict[str, Any]:
    return config.load_legs()


@router.get("/sources")
async def get_sources() -> dict[str, Any]:
    state = poller.read_state()
    return {
        "registry": config.load_sources(),
        "status": state.get("status", {}),
    }


@router.get("/alerts/recent")
async def alerts_recent(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"alerts": alerter.recent_alerts(limit=limit)}


@router.post("/alerts/check")
async def alerts_check() -> dict[str, Any]:
    """Score + apply policy + maybe send. Standard cron-time endpoint."""
    return await alerter.maybe_alert()


@router.post("/alerts/force")
async def alerts_force(reason: str = Query("manual force from API")) -> dict[str, Any]:
    """Bypass cooldown + late-risk check. Operator-test only — keep this
    behind operator-trusted auth IRL (single-user box: fine for now)."""
    brief = scoring.evaluate()
    return await alerter.maybe_alert(brief, force=True, reason=reason)


class BriefBody(BaseModel):
    checkpoint: str = "during"  # pre-1h | pre-30m | during
    force: bool = False


@router.post("/brief")
async def send_brief(body: BriefBody) -> dict[str, Any]:
    """Send a proactive status brief (not a late-risk alert) to the commute
    channel. Cooldowned per (schedule, day, checkpoint) so retrying cron
    invocations don't spam. `force=True` bypasses cooldown."""
    brief = scoring.evaluate()
    dispatch = await alerter.send_brief(brief, checkpoint=body.checkpoint, force=body.force)
    return {"dispatch": dispatch, "brief": brief}


class DecideBody(BaseModel):
    checkpoint: str = "during"  # pre-1h | pre-30m | during | post


@router.post("/llm/decide")
async def llm_decide(body: DecideBody) -> dict[str, Any]:
    """Run the LLM decision layer. If interject=True, ALSO dispatch through
    the alerter (subject to cooldown). Returns the decision + dispatch result."""
    brief = scoring.evaluate()
    decision = await llm.decide_interject(brief, checkpoint=body.checkpoint)
    dispatch: dict[str, Any] = {"sent": False, "reason": "interject=False"}
    if decision.get("interject"):
        dispatch = await alerter.maybe_alert(brief, force=False, reason=f"LLM[{decision.get('checkpoint')}]: {decision.get('reasoning','')[:160]}")
    return {"decision": decision, "dispatch": dispatch, "brief": brief}


@router.get("/llm/recent")
async def llm_recent(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"decisions": llm.recent_decisions(limit=limit)}
