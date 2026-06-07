"""Bird detection app + event bridge.

Two halves:
  1. Read API over the BirdNET-Go SQLite DB (private, tailnet-only via _SECURE
     in main.py) backing the /apps/birds dashboard and audio playback.
  2. POST /birds/webhook: BirdNET-Go calls this on every detection. We enrich it
     with a first-seen flag computed from SQLite, append it to the Workshop event
     log as a "birdnet" source event, and enqueue runs for subscribed agents so
     the OpenClaw assistant can narrate new arrivals. This is intentionally NOT
     behind the token gate (BirdNET-Go posts from loopback; require_token already
     trusts loopback).

The sanitized public dataset is served at /birds/public so the publish job can
fetch exactly what goes to Cloudflare (and so we can eyeball the boundary).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from services import birds_store, birds_analyze
from services.birds_store import DatabaseMissing

router = APIRouter(prefix="/birds", tags=["birds"])
log = logging.getLogger("workshop.birds")


def _guard(fn):
    """Translate a missing-DB (pre-hardware) into an empty-but-OK response so the
    UI renders an honest 'no data yet' state instead of a 500."""
    try:
        return fn()
    except DatabaseMissing:
        return None


@router.get("/summary")
async def get_summary(include_nonbird: bool = False):
    data = _guard(lambda: birds_store.summary(not include_nonbird))
    if data is None:
        return {"available": False, "total_detections": 0, "species_count": 0,
                "today": {"detections": 0, "species": 0, "date": None}, "last_detection": None}
    return {"available": True, **data}


@router.get("/species")
async def get_species(include_nonbird: bool = False):
    data = _guard(lambda: birds_store.species_list(not include_nonbird))
    return {"available": data is not None, "species": data or []}


@router.get("/detections")
async def get_detections(limit: int = Query(100, ge=1, le=1000),
                         species: str | None = None, date: str | None = None,
                         min_conf: float = Query(0.0, ge=0.0, le=1.0),
                         include_nonbird: bool = False):
    data = _guard(lambda: birds_store.detections(limit, species, date, min_conf, not include_nonbird))
    return {"available": data is not None, "detections": data or []}


@router.get("/recent-arrivals")
async def get_recent_arrivals(days: int = Query(7, ge=1, le=90), include_nonbird: bool = False):
    data = _guard(lambda: birds_store.recent_arrivals(days, not include_nonbird))
    return {"available": data is not None, "arrivals": data or []}


@router.get("/activity/daily")
async def get_daily(days: int = Query(90, ge=1, le=730), include_nonbird: bool = False):
    data = _guard(lambda: birds_store.daily_counts(days, not include_nonbird))
    return {"available": data is not None, "daily": data or []}


@router.get("/activity/hourly")
async def get_hourly(days: int = Query(30, ge=1, le=730), include_nonbird: bool = False):
    data = _guard(lambda: birds_store.hourly_activity(days, not include_nonbird))
    return {"available": data is not None, "hourly": data or []}


@router.get("/timeseries")
async def get_timeseries(species: str, days: int = Query(90, ge=1, le=730)):
    data = _guard(lambda: birds_store.timeseries(species, days))
    return {"available": data is not None, "species": species, "series": data or []}


@router.get("/clip/{note_id}")
async def get_clip(note_id: int):
    """Stream a detection's WAV. PRIVATE only. The public build never renders the
    audio component and the public deploy has no route here at all."""
    path = _guard(lambda: birds_store.resolve_clip(note_id))
    if not path:
        raise HTTPException(status_code=404, detail="clip not found")
    return FileResponse(str(path), media_type="audio/wav", filename=path.name)


@router.get("/public")
async def get_public():
    """The sanitized dataset (what crosses to Cloudflare). Served here so the
    publish job fetches the exact bytes and we can audit the boundary."""
    generated_at = datetime.now(timezone.utc).isoformat()
    return birds_store.build_public_dataset(generated_at)


# --------------------------------------------------------------------------
# Browser mic-test: live detection from the device mic, no always-on rig.
# Detections go to a SEPARATE backend DB under a named test station - never the
# yard DB, never published.
# --------------------------------------------------------------------------

@router.get("/analyze/health")
async def analyze_health():
    return birds_analyze.analyzer_health()


def _opt_float(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


@router.post("/analyze")
async def analyze(audio: UploadFile, station: str = Form(""),
                  token: str = Form(""), min_conf: float = Form(0.25),
                  device_name: str = Form(""), mode: str = Form("adaptive"),
                  lat: str = Form(""), lon: str = Form(""), address: str = Form(""),
                  location_id: str = Form("")):
    """Analyze one audio chunk from the web app and return its detections live,
    attributed to the posting device and its resolved location. The UI posts
    rolling 1s chunks while listening."""
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    name = device_name.strip() or station.strip()
    lid = None
    if location_id.strip().isdigit():
        lid = int(location_id.strip())
    try:
        result = birds_analyze.analyze_chunk(
            data, audio.content_type or "", name or "Untitled device",
            token.strip(), max(0.05, min(min_conf, 0.99)),
            device_name=name, mode=(mode.strip() or "adaptive"),
            lat=_opt_float(lat), lon=_opt_float(lon), address=address.strip(),
            location_id=lid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"analyze failed: {e}")
    return {"ok": True, **result}


@router.get("/analyze/recent")
async def analyze_recent(station: str, limit: int = Query(100, ge=1, le=500),
                         since: str | None = None, include_nonbird: bool = False):
    return {
        "station": station,
        "recent": birds_analyze.recent(station, limit, include_nonbird),
        "session_species": birds_analyze.session_species(station, since, include_nonbird) if since else [],
    }


@router.post("/analyze/station")
async def analyze_register_station(token: str = Form(...), label: str = Form(...)):
    birds_analyze.register_station(token.strip(), label.strip())
    return {"ok": True, "station": label.strip()}


@router.get("/analyze/station")
async def analyze_get_station(token: str):
    return {"station": birds_analyze.station_for_token(token)}


# Per-location (station) analytics for the mic-test stations.
@router.get("/stations")
async def stations_list(include_nonbird: bool = False):
    return {"stations": birds_analyze.list_stations(include_nonbird)}


@router.get("/station/summary")
async def station_summary(station: str, include_nonbird: bool = False):
    return birds_analyze.station_summary(station, include_nonbird)


@router.get("/station/species")
async def station_species(station: str, include_nonbird: bool = False):
    return {"station": station, "species": birds_analyze.station_species(station, include_nonbird)}


@router.get("/station/activity")
async def station_activity(station: str, include_nonbird: bool = False):
    return {"station": station, **birds_analyze.station_activity(station, include_nonbird)}


@router.get("/station/species-detail")
async def station_species_detail(station: str, sci: str):
    return birds_analyze.species_detail(station, sci)


# --------------------------------------------------------------------------
# Devices, locations, the home source, and per-bird detail (new model)
# --------------------------------------------------------------------------

@router.get("/devices")
async def devices_list():
    return {"devices": birds_analyze.list_devices()}


@router.get("/device")
async def device_get(token: str):
    return {"device": birds_analyze.get_device(token)}


@router.post("/device")
async def device_upsert(token: str = Form(...), name: str = Form(None),
                        mode: str = Form(None), fixed_location_id: str = Form(None)):
    fid = int(fixed_location_id) if (fixed_location_id or "").strip().isdigit() else (
        0 if (fixed_location_id or "").strip() == "0" else None)
    dev = birds_analyze.upsert_device(token.strip(), name=name, mode=mode, fixed_location_id=fid)
    return {"ok": True, "device": dev}


@router.get("/locations")
async def locations_list(include_nonbird: bool = False):
    return birds_analyze.list_locations(include_nonbird)


@router.get("/location")
async def location_get(location_id: int):
    loc = birds_analyze.get_location(location_id)
    if not loc:
        raise HTTPException(status_code=404, detail="location not found")
    return loc


@router.post("/location")
async def location_create_or_update(location_id: str = Form(None), name: str = Form(None),
                                    lat: str = Form(None), lon: str = Form(None),
                                    address: str = Form(None)):
    flat, flon = _opt_float(lat or ""), _opt_float(lon or "")
    if (location_id or "").strip().isdigit():
        loc = birds_analyze.update_location(int(location_id), name=name, lat=flat,
                                            lon=flon, address=address)
        if not loc:
            raise HTTPException(status_code=404, detail="location not found")
        return {"ok": True, "location": loc}
    return {"ok": True, "location": birds_analyze.create_location(
        (name or "New location"), lat=flat, lon=flon, address=(address or ""))}


@router.post("/location/merge")
async def location_merge(src: int = Form(...), dst: int = Form(...)):
    loc = birds_analyze.merge_locations(src, dst)
    if not loc:
        raise HTTPException(status_code=400, detail="merge failed (bad ids)")
    return {"ok": True, "location": loc}


@router.get("/location/summary")
async def location_summary_ep(location_id: int, include_nonbird: bool = False):
    return birds_analyze.location_summary(location_id, include_nonbird)


@router.get("/location/species")
async def location_species_ep(location_id: int, include_nonbird: bool = False):
    return {"location_id": location_id, "species": birds_analyze.location_species(location_id, include_nonbird)}


@router.get("/location/activity")
async def location_activity_ep(location_id: int, include_nonbird: bool = False):
    return {"location_id": location_id, **birds_analyze.location_activity(location_id, include_nonbird)}


@router.get("/bird")
async def bird_detail_ep(sci: str, location_id: int | None = None,
                         device: str | None = None, days: int = 0):
    return birds_analyze.bird_detail(sci, location_id, device, days)


@router.get("/home")
async def home_get():
    return {"home": birds_analyze.get_setting("home_source")}


@router.post("/home")
async def home_set(source: str = Form(...)):
    birds_analyze.set_setting("home_source", source.strip())
    return {"ok": True, "home": source.strip()}


# --------------------------------------------------------------------------
# Event bridge: BirdNET-Go -> Workshop event bus -> OpenClaw agents
# --------------------------------------------------------------------------

@router.post("/webhook")
async def detection_webhook(request: Request):
    """Receive one detection from BirdNET-Go, enrich with first-seen, fan out to
    subscribed agents. Body shape is brain/webhook-template.json."""
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    sci = (payload.get("scientific_name") or "").strip()
    date = (payload.get("date") or birds_store._today()).strip()

    # First-seen enrichment (best effort; DB may lag the webhook by a row).
    flags = {"first_ever": False, "first_today": False, "total_for_species": 0}
    if sci:
        try:
            flags = birds_store.first_seen_flags(sci, date)
        except DatabaseMissing:
            pass

    summary = {
        "kind": "bird_detection",
        "scientific_name": sci,
        "common_name": payload.get("common_name"),
        "species_code": payload.get("species_code"),
        "confidence": payload.get("confidence"),
        "date": date,
        "time": payload.get("time"),
        "source_node": payload.get("source_node"),
        # Derived flags the assistant keys off of:
        "first_ever": flags["first_ever"],
        "first_today": flags["first_today"],
        "total_for_species": flags["total_for_species"],
    }

    # Append to the shared Workshop event log + enqueue subscriber runs, using
    # the same record shape routes/events.py writes so /events/recent and the
    # cron consumer treat it like any other source.
    from routes.events import EVENTS_LOG, _load_subscriptions
    from services.event_dispatch import enqueue_event

    EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": "birdnet",
        "known_source": True,
        "signature": {"ok": True, "reason": "loopback"},
        "headers": {},
        "body": summary,
    }
    offset = EVENTS_LOG.stat().st_size if EVENTS_LOG.exists() else 0
    with EVENTS_LOG.open("a") as f:
        f.write(json.dumps(record) + "\n")

    dispatch = enqueue_event("birdnet", event_offset=offset, event_summary=summary)
    subscribers = _load_subscriptions().get("birdnet", [])
    log.info("bird detection sci=%s conf=%s first_ever=%s first_today=%s subs=%d enqueued=%d",
             sci, summary["confidence"], flags["first_ever"], flags["first_today"],
             len(subscribers), dispatch["enqueued"])

    return {"ok": True, "first_ever": flags["first_ever"], "first_today": flags["first_today"],
            "enqueued_runs": dispatch["enqueued"]}
