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
from pydantic import BaseModel

from services import birds_store, birds_analyze
from services.birds_store import DatabaseMissing

router = APIRouter(prefix="/birds", tags=["birds"])
# Ingest endpoints (BirdNET-Go webhook, the yard-mic analyze/live-publish) run
# from loopback / the local network and carry their own device token in the
# body. They are mounted separately (main.py) behind require_internal_caller, and
# are NOT proxied to the public origin — unlike the read endpoints on `router`,
# which require an authenticated user.
ingest_router = APIRouter(prefix="/birds", tags=["birds-ingest"])
log = logging.getLogger("workshop.birds")


class LiveBody(BaseModel):
    token: str
    cols: list = []      # recent spectrogram columns: each a list of band energies 0..255
    hearing: list = []   # current "now hearing" detections (same shape the UI uses)


@ingest_router.post("/live/publish")
async def live_publish(body: LiveBody):
    """A shared, listening device pushes its live spectrogram + detections here."""
    return birds_analyze.publish_live(body.token, body.cols, body.hearing)


@router.get("/live")
async def live_get(location_id: int | None = None):
    """Active live feeds the viewer may see (optionally one location)."""
    return birds_analyze.live_feeds(location_id)


def _guard(fn):
    """Translate a missing-DB (pre-hardware) into an empty-but-OK response so the
    UI renders an honest 'no data yet' state instead of a 500."""
    try:
        return fn()
    except DatabaseMissing:
        return None


# NOTE: the home dashboard's "Yard (always-on)" view reads these. They now serve the
# UNIFIED store (the always-on yard device), not the retired BirdNET-Go birdnet.db.
@router.get("/summary")
async def get_summary(include_nonbird: bool = False):
    return {"available": True, **birds_analyze.yard_summary(include_nonbird)}


@router.get("/species")
async def get_species(include_nonbird: bool = False):
    return {"available": True, "species": birds_analyze.yard_species(include_nonbird)}


@router.get("/detections")
async def get_detections(limit: int = Query(100, ge=1, le=1000),
                         species: str | None = None, date: str | None = None,
                         min_conf: float = Query(0.0, ge=0.0, le=1.0),
                         include_nonbird: bool = False):
    # Unified store (always-on yard device), not the retired birdnet.db.
    data = birds_analyze.yard_detections(limit, min_conf, include_nonbird)
    return {"available": True, "detections": data}


@router.get("/activity/daily")
async def get_daily(days: int = Query(90, ge=1, le=730), include_nonbird: bool = False):
    return {"available": True, "daily": birds_analyze.yard_activity_daily(days, include_nonbird)}


@router.get("/activity/hourly")
async def get_hourly(days: int = Query(30, ge=1, le=730), include_nonbird: bool = False):
    return {"available": True, "hourly": birds_analyze.yard_activity_hourly(days, include_nonbird)}


# /timeseries and /recent-arrivals were unused legacy birds_store readers (no
# frontend consumer) — removed with the birdnet.db retirement.


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


@ingest_router.post("/analyze")
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
            data, audio.content_type or "", name,
            token.strip(), max(0.05, min(min_conf, 0.99)),
            device_name=name, mode=(mode.strip() or "adaptive"),
            lat=_opt_float(lat), lon=_opt_float(lon), address=address.strip(),
            location_id=lid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"analyze failed: {e}")
    return {"ok": True, **result}


@router.get("/analyze/settings")
async def get_analyze_settings():
    """Smart-detection tuning (confidence floor, range-filter strictness, new-
    species consensus). Drives how aggressively false positives are suppressed."""
    return {"settings": birds_analyze.smart_settings()}


@router.post("/analyze/settings")
async def set_analyze_settings(min_conf: float = Form(None),
                               sensitivity: float = Form(None),
                               sf_thresh: float = Form(None),
                               new_species_hits: int = Form(None),
                               prior_floor: float = Form(None),
                               trust_radius_km: float = Form(None)):
    patch = {"min_conf": min_conf, "sensitivity": sensitivity,
             "sf_thresh": sf_thresh, "new_species_hits": new_species_hits,
             "prior_floor": prior_floor, "trust_radius_km": trust_radius_km}
    return {"settings": birds_analyze.update_smart_settings(
        {k: v for k, v in patch.items() if v is not None})}


@router.post("/analyze/recompute")
async def recompute_analyze(sf_thresh: float = Form(None)):
    """Re-apply the range filter to already-stored detections (re-score by each
    detection's place + time). Hides impossible species retroactively. Reversible."""
    try:
        return birds_analyze.recompute_history(sf_thresh)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"recompute failed: {e}")


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
async def devices_list(archived: bool = False, viewer_id: int | None = None):
    # viewer_id is None for the private Workshop app (operator sees everything);
    # the standalone app passes the session user's id to scope to their + friends'
    # shared devices.
    return {"devices": birds_analyze.list_devices(archived=archived, viewer_id=viewer_id)}


@router.get("/device")
async def device_get(token: str):
    return {"device": birds_analyze.get_device(token)}


@router.post("/device")
async def device_upsert(token: str = Form(...), name: str = Form(None),
                        mode: str = Form(None), fixed_location_id: str = Form(None),
                        user_agent: str = Form(None), update_only: bool = Form(False),
                        shared: bool | None = Form(None)):
    fid = int(fixed_location_id) if (fixed_location_id or "").strip().isdigit() else (
        0 if (fixed_location_id or "").strip() == "0" else None)
    dev = birds_analyze.upsert_device(token.strip(), name=name, mode=mode, fixed_location_id=fid,
                                      user_agent=user_agent, update_only=update_only, shared=shared)
    return {"ok": True, "device": dev}


@router.delete("/device")
async def device_delete(token: str):
    return birds_analyze.delete_device(token.strip())


@router.post("/device/restore")
async def device_restore(token: str = Form(...)):
    return birds_analyze.restore_device(token.strip())


# --- Accounts + friends (admin-managed; the standalone app auto-creates a member
# row on first authenticated visit). These run behind the tailnet gate in the
# private app and behind the session gate in the standalone app. ----------------

@router.get("/users")
async def users_list():
    return {"users": birds_analyze.list_users()}


@router.post("/user")
async def user_upsert(email: str = Form(...), name: str = Form(None), role: str = Form(None)):
    try:
        return {"ok": True, "user": birds_analyze.upsert_user(email, name=name, role=role)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/friends")
async def friends_list(user_id: int):
    return {"user_id": user_id, "friends": birds_analyze.list_friends(user_id)}


@router.post("/friend")
async def friend_set(user_a: int = Form(...), user_b: int = Form(...),
                     status: str = Form("accepted")):
    try:
        return {"ok": True, "friendship": birds_analyze.set_friendship(user_a, user_b, status)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/friend")
async def friend_remove(user_a: int, user_b: int):
    return birds_analyze.remove_friendship(user_a, user_b)


@router.get("/locations")
async def locations_list(include_nonbird: bool = False, archived: bool = False,
                         viewer_id: int | None = None):
    return birds_analyze.list_locations(include_nonbird, archived=archived, viewer_id=viewer_id)


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


@router.delete("/location")
async def location_delete(location_id: int):
    return birds_analyze.delete_location(location_id)


@router.post("/location/restore")
async def location_restore(location_id: int = Form(...)):
    return birds_analyze.restore_location(location_id)


@router.get("/location/summary")
async def location_summary_ep(location_id: int, include_nonbird: bool = False,
                              status: str | None = None, device: str | None = None):
    return birds_analyze.location_summary(location_id, include_nonbird, status, device)


@router.get("/location/species")
async def location_species_ep(location_id: int, include_nonbird: bool = False,
                              status: str | None = None, device: str | None = None):
    return {"location_id": location_id,
            "species": birds_analyze.location_species(location_id, include_nonbird, status, device)}


@router.get("/location/activity")
async def location_activity_ep(location_id: int, include_nonbird: bool = False,
                               status: str | None = None, device: str | None = None):
    return {"location_id": location_id,
            **birds_analyze.location_activity(location_id, include_nonbird, status, device)}


@router.get("/now")
async def birds_now_ep(location_id: int):
    """Light + weather + today's early bird + what's-likely-now for a location."""
    return birds_analyze.birds_now(location_id)


@router.get("/forecast/week")
async def birds_forecast_week_ep(location_id: int, days: int = Query(7, ge=1, le=14)):
    """Per-day forecast for the next `days` days: light, weather, activity outlook."""
    from services import birds_forecast
    return birds_forecast.forecast_week(location_id, days)


@router.get("/bird")
async def bird_detail_ep(sci: str, location_id: int | None = None,
                         device: str | None = None, days: int = 0,
                         status: str | None = None):
    return birds_analyze.bird_detail(sci, location_id, device, days, status)


# --- Recordings: save the audio of detections, under per-bird rules + a budget --

@router.get("/recordings/settings")
async def recordings_settings_get():
    return {"settings": birds_analyze.rec_settings()}


@router.post("/recordings/settings")
async def recordings_settings_set(rec_storage_mb: int = Form(None),
                                  rec_when_full: str = Form(None),
                                  rec_save: bool = Form(None)):
    patch = {"rec_storage_mb": rec_storage_mb, "rec_when_full": rec_when_full,
             "rec_save": rec_save}
    return {"settings": birds_analyze.update_rec_settings(
        {k: v for k, v in patch.items() if v is not None})}


@router.get("/recordings/status")
async def recordings_status_ep():
    return birds_analyze.recordings_status()


@router.get("/recordings")
async def recordings_list_ep(sci: str | None = None, location_id: int | None = None,
                             pinned: bool | None = None,
                             limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return birds_analyze.list_recordings(sci, location_id, pinned, limit, offset)


@router.get("/recordings/{rec_id}/audio")
async def recordings_audio_ep(rec_id: int):
    """Stream a saved clip. PRIVATE only — clips never cross the publish boundary."""
    path = birds_analyze.recording_path(rec_id)
    if not path:
        raise HTTPException(status_code=404, detail="recording not found")
    return FileResponse(str(path), media_type="audio/mpeg", filename=path.name)


@router.post("/recordings/{rec_id}/pin")
async def recordings_pin_ep(rec_id: int, pinned: bool = Form(...)):
    return birds_analyze.pin_recording(rec_id, pinned)


@router.delete("/recordings/{rec_id}")
async def recordings_delete_ep(rec_id: int):
    return birds_analyze.delete_recording(rec_id)


@router.delete("/recordings")
async def recordings_clear_ep(sci: str):
    return birds_analyze.clear_recordings(sci)


@router.post("/recordings/reconcile")
async def recordings_reconcile_ep():
    return birds_analyze.reconcile_recordings()


@router.get("/species/recording")
async def species_recording_get(sci: str):
    return {"save_mode": birds_analyze.get_species_save_mode(sci)}


@router.post("/species/recording")
async def species_recording_set(sci: str = Form(...), save_mode: str = Form(...)):
    return birds_analyze.set_species_save_mode(sci, save_mode)


# --- Soundscape Atlas (call-type clusters + labelling) ----------------------

@router.get("/atlas/status")
async def atlas_status_ep():
    return birds_analyze.atlas_status()


@router.get("/atlas/settings")
async def atlas_settings_get():
    return {"settings": birds_analyze.atlas_settings()}


@router.post("/atlas/settings")
async def atlas_settings_set(atlas_clip_save: bool = Form(None),
                             atlas_clip_storage_mb: int = Form(None),
                             atlas_exemplars_per_cluster: int = Form(None),
                             atlas_unknown_dist: float = Form(None)):
    patch = {"atlas_clip_save": atlas_clip_save,
             "atlas_clip_storage_mb": atlas_clip_storage_mb,
             "atlas_exemplars_per_cluster": atlas_exemplars_per_cluster,
             "atlas_unknown_dist": atlas_unknown_dist}
    return {"settings": birds_analyze.update_atlas_settings(
        {k: v for k, v in patch.items() if v is not None})}


@router.get("/atlas/species")
async def atlas_species_ep():
    return {"species": birds_analyze.atlas_species()}


@router.get("/atlas")
async def atlas_points_ep(scope: str = "global", species: str | None = None,
                          location_id: int | None = None,
                          limit: int = Query(8000, ge=1, le=20000)):
    return birds_analyze.atlas_points(scope, species, location_id, limit)


@router.get("/atlas/insights")
async def atlas_insights_ep(days: int = Query(30, ge=1, le=365), include_nonbird: bool = False):
    return birds_analyze.atlas_insights(days, include_nonbird)


@router.get("/atlas/cluster/{cluster_id}/clips")
async def atlas_cluster_clips_ep(cluster_id: int, limit: int = Query(16, ge=1, le=64)):
    return birds_analyze.atlas_cluster_clips(cluster_id, limit)


@router.post("/atlas/cluster/{cluster_id}/label")
async def atlas_cluster_label_ep(cluster_id: int, label: str = Form(None)):
    try:
        return birds_analyze.set_cluster_label(cluster_id, label)
    except KeyError:
        raise HTTPException(status_code=404, detail="cluster not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/atlas/clip/{clip_id}/audio")
async def atlas_clip_audio_ep(clip_id: int):
    """Stream an Atlas exemplar clip. PRIVATE only — never crosses publish."""
    path = birds_analyze.atlas_clip_path(clip_id)
    if not path:
        raise HTTPException(status_code=404, detail="clip not found")
    return FileResponse(str(path), media_type="audio/mpeg", filename=path.name)


@router.post("/atlas/rebuild")
async def atlas_rebuild_ep():
    return birds_analyze.trigger_atlas_build()


# --- Trust model (suggested / confirmed / rejected) -------------------------

def _ids(raw: str) -> list[int]:
    return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()]


@router.get("/suggestions")
async def suggestions_ep(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                         include_nonbird: bool = False):
    return birds_analyze.suggestions(limit, offset, include_nonbird)


@router.get("/new-count")
async def new_count_ep():
    return {"new_count": birds_analyze.new_arrival_count()}


@router.get("/species-events")
async def species_events_ep(sci: str, location_id: int | None = None,
                            status: str | None = None, limit: int = Query(50, ge=1, le=200)):
    return {"events": birds_analyze.species_events(sci, location_id, status, limit)}


@router.post("/confirm")
async def confirm_ep(ids: str = Form(...)):
    return birds_analyze.confirm_detections(_ids(ids))


@router.post("/reject")
async def reject_ep(ids: str = Form(...)):
    return birds_analyze.reject_detections(_ids(ids))


@router.post("/detection/status")
async def detection_status_ep(id: int = Form(...), status: str = Form(...)):
    return birds_analyze.set_detection_status(id, status)


@router.post("/species/review")
async def species_review_ep(sci: str = Form(...), lat: str = Form(""), lon: str = Form(""),
                            common: str = Form("")):
    return birds_analyze.mark_species_reviewed(sci, _opt_float(lat), _opt_float(lon), common)


@router.post("/species/auto")
async def species_auto_ep(sci: str = Form(...), lat: float = Form(...), lon: float = Form(...),
                          on: bool = Form(...), common: str = Form("")):
    return birds_analyze.set_species_auto(sci, lat, lon, on, common)


@router.post("/species/mute")
async def species_mute_ep(sci: str = Form(...), lat: float = Form(...), lon: float = Form(...),
                          common: str = Form("")):
    return birds_analyze.mute_species(sci, lat, lon, common)


@router.post("/species/unmute")
async def species_unmute_ep(sci: str = Form(...), lat: float = Form(...), lon: float = Form(...),
                            common: str = Form("")):
    return birds_analyze.unmute_species(sci, lat, lon, common)


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

@ingest_router.post("/webhook")
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

    # Land the always-on detection in the SAME unified store the browser path
    # writes to (attributed to the 'yard' device) so the app has one read path
    # and the always-on station is just another device. Best-effort: a storage
    # hiccup must not drop the event-bus fan-out below.
    try:
        birds_analyze.ingest_yard_detection(payload)
    except Exception:  # noqa: BLE001
        log.exception("yard ingest failed sci=%s", sci)

    # First-seen enrichment (best effort; DB may lag the webhook by a row).
    flags = {"first_ever": False, "first_today": False, "total_for_species": 0}
    if sci:
        try:
            flags = birds_analyze.yard_first_seen(sci, date)
        except Exception:  # noqa: BLE001
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
    # An app publishes through the platform's event API. This used to import
    # EVENTS_LOG and the private `_load_subscriptions` from routes/events and
    # hand-roll the record, which made one app's route module depend on another
    # route module's internals -- exactly the edge the AppSpec seams remove.
    from services import event_log

    dispatch = event_log.publish_and_dispatch(
        "birdnet",
        summary,
        signature={"ok": True, "reason": "loopback"},
        known=True,
    )
    subscribers = event_log.subscribers("birdnet")
    log.info("bird detection sci=%s conf=%s first_ever=%s first_today=%s subs=%d enqueued=%d",
             sci, summary["confidence"], flags["first_ever"], flags["first_today"],
             len(subscribers), dispatch["enqueued"])

    return {"ok": True, "first_ever": flags["first_ever"], "first_today": flags["first_today"],
            "enqueued_runs": dispatch["enqueued"]}
