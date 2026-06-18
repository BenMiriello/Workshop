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

from services.commute import (
    alerter, config, journeys, llm, messages, patterns, places, poller, reason, scoring,
)


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


# --- adaptive layer: messages feed, journeys, places, patterns, reasoner -----

@router.get("/messages")
async def get_messages(limit: int = Query(20, ge=1, le=200),
                       offset: int = Query(0, ge=0)) -> dict[str, Any]:
    """The in-app Now/Suggestions feed: canonical messages, newest-first,
    paginated. Same objects Discord renders."""
    return messages.recent(limit=limit, offset=offset)


@router.get("/messages/latest")
async def get_latest_message() -> dict[str, Any]:
    return {"message": messages.latest()}


class ReasonBody(BaseModel):
    checkpoint: str = "during"     # pre-1h | pre-30m | during | post
    dispatch: str = "no"           # no | auto | always  (auto = caution/urgent or a pre-* checkpoint)
    force_llm: bool = False        # always spend Claude (the app "Check now" button)


@router.post("/reason")
async def reason_now(body: ReasonBody) -> dict[str, Any]:
    """Compose ONE adaptive message from the live brief + learned patterns,
    store it to the feed (deduped), and dispatch to Discord per policy. This is
    the cron-driven path that replaces the rigid template brief."""
    brief = scoring.evaluate()
    # Spend Claude only when it's worth it: an explicit app request, a non-during
    # checkpoint (pre-1h/pre-30m/post), or an active late-risk. Routine on-track
    # 'during' ticks take the cheap deterministic path (and dedup anyway).
    allow_llm = (body.force_llm or body.checkpoint != "during"
                 or bool(brief.get("late_risk")))
    res = reason.compose_and_store(brief, checkpoint=body.checkpoint, allow_llm=allow_llm)
    msg = res["message"]
    # Discord dispatch is state-keyed (see alerter.gate_dispatch): suppresses the
    # LLM's reworded repeats and the non-actionable blind state, so the feed stays
    # complete while Discord only buzzes on a genuine change.
    gate = alerter.gate_dispatch(body.dispatch, body.checkpoint, msg, brief, res["duplicate"])
    if gate["dispatch"]:
        dispatch = await alerter.dispatch_message(msg)
        alerter.mark_dispatch(gate["state_key"])
    else:
        dispatch = {"sent": False, "reason": gate["reason"]}
    return {"message": msg, "stored": res["stored"], "duplicate": res["duplicate"], "dispatch": dispatch}


@router.get("/journeys")
async def get_journeys(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"journeys": journeys.recent(limit=limit)}


@router.post("/journeys/finalize")
async def finalize_journey() -> dict[str, Any]:
    return journeys.finalize()


@router.get("/patterns")
async def get_patterns() -> dict[str, Any]:
    return patterns.compute()


@router.get("/places")
async def get_places() -> dict[str, Any]:
    return places.load()


@router.post("/places/learn")
async def learn_places() -> dict[str, Any]:
    return places.learn()


class LabelBody(BaseModel):
    id: int
    label: str


@router.post("/places/label")
async def label_place(body: LabelBody) -> dict[str, Any]:
    return places.set_label(body.id, body.label)


@router.post("/learn")
async def learn_all() -> dict[str, Any]:
    """Cron convenience: refresh learned places + finalize any completed
    journey. Cheap and idempotent."""
    pl = places.learn()
    fin = journeys.finalize()
    return {"places": len(pl.get("places", [])), "finalize": fin}


@router.post("/stopmon")
async def poll_stopmon() -> dict[str, Any]:
    """Poll SIRI StopMonitoring for the LEARNED boarding stops only (best-effort
    enrichment; gated to when we actually know the user's stops)."""
    stops = journeys.learned_boarding_stops()
    results = []
    for s in stops[:6]:  # cap to protect the 60-req/hr budget
        agency = "CT" if s["agency"] == "caltrain" else "SF"
        results.append(await poller.poll_stop_monitoring(agency, s["stop_id"]))
    return {"polled": len(results), "stops": stops, "results": results}
