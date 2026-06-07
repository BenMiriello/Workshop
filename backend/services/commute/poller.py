"""Live-transit pollers.

Caltrain GTFS-Realtime: public, no key — three .pb feeds on caltrain.com.
SFMTA Muni GTFS-Realtime: free, requires 511.org developer key (env
    WORKSHOP_511_API_KEY; absent ⇒ source is marked `needs_key` and skipped
    until operator registers).
Traffic for short drive legs: deferred (Google Maps key not yet wired);
    falls back to legs.json default_minutes.

State written under .runtime/commute/:
    caltrain.json         — last successful trip-updates parse (sketch)
    caltrain.raw.pb       — raw feed bytes (debug)
    sfmta.json            — same shape, when key present
    sources_status.json   — last poll status per source key (for /status UI)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config

# Lazy import — protobuf is optional at startup; only the pollers need it.
try:
    from google.transit import gtfs_realtime_pb2  # type: ignore
    GTFS_AVAILABLE = True
except ImportError:  # pragma: no cover
    GTFS_AVAILABLE = False


# 511.org is now the canonical Bay Area transit feed for both Caltrain and Muni.
# Caltrain's own caltrain.com/files/rt/gtfsrt/*.pb endpoints currently 302 to HTML
# (verified 2026-05-21) — they likely deprecated in favor of 511. Same protobuf
# shape, just different host + auth.
TRANSIT_511_BASE = "https://api.511.org/transit"
CALTRAIN_AGENCY = "CT"
SFMTA_AGENCY = "SF"
KEY_511 = os.environ.get("WORKSHOP_511_API_KEY", "")

POLL_HTTP_TIMEOUT = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(name: str, data: dict[str, Any]) -> None:
    p = config.runtime_path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def _read_json(name: str) -> dict[str, Any]:
    p = config.runtime_path(name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _record_source_status(key: str, status: str, **extras: Any) -> None:
    """Merge a per-source status record into sources_status.json."""
    cur = _read_json("sources_status.json")
    cur[key] = {"status": status, "ts": _now_iso(), **extras}
    _write_json("sources_status.json", cur)


# ---------------------------------------------------------------------------
# Caltrain GTFS-RT
# ---------------------------------------------------------------------------

async def _poll_511_agency(agency: str, source_key: str, out_file: str,
                            client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Generic 511.org GTFS-RT puller for one agency. Returns the summary
    dict and writes state to .runtime/commute/<out_file>.

    Caltrain (agency=CT) and SFMTA (agency=SF) both ride this code path.
    """
    if not KEY_511:
        _record_source_status(source_key, "needs_key",
                              note="set WORKSHOP_511_API_KEY env var; register at https://511.org/open-data/token")
        return {"status": "needs_key"}
    if not GTFS_AVAILABLE:
        _record_source_status(source_key, "missing_deps",
                              note="gtfs-realtime-bindings not installed")
        return {"status": "missing_deps"}

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=POLL_HTTP_TIMEOUT, follow_redirects=True)
    try:
        endpoints = {
            "trip_updates": f"{TRANSIT_511_BASE}/tripupdates",
            "service_alerts": f"{TRANSIT_511_BASE}/servicealerts",
            "vehicle_positions": f"{TRANSIT_511_BASE}/vehiclepositions",
        }
        params = {"agency": agency, "api_key": KEY_511}
        parsed: dict[str, Any] = {"ts": _now_iso(), "agency": agency, "feeds": {}}
        for name, url in endpoints.items():
            try:
                r = await client.get(url, params=params)
                if r.status_code != 200:
                    parsed["feeds"][name] = {"error": f"http {r.status_code}", "body": r.text[:200]}
                    continue
                # Sanity: protobuf bytes don't start with `<` (HTML).
                if r.content[:1] == b"<":
                    parsed["feeds"][name] = {"error": "HTML returned (not GTFS-RT) — check api_key/agency"}
                    continue
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.ParseFromString(r.content)
                parsed["feeds"][name] = _summarize_feed(feed, kind=name)
            except Exception as e:  # noqa: BLE001
                parsed["feeds"][name] = {"error": str(e)[:200]}
        _write_json(out_file, parsed)
        _record_source_status(source_key, "ok",
                              entity_count=sum(
                                  f.get("entity_count", 0)
                                  for f in parsed["feeds"].values()
                                  if isinstance(f, dict)
                              ))
        return parsed
    finally:
        if owns_client:
            await client.aclose()


async def poll_caltrain(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Caltrain via 511.org (agency=CT)."""
    return await _poll_511_agency(CALTRAIN_AGENCY, "caltrain.gtfs_rt", "caltrain.json", client)


def _summarize_feed(feed: Any, kind: str) -> dict[str, Any]:
    """Reduce a GTFS-RT FeedMessage to a JSON-friendly summary. Keep this
    small — full proto serialization is huge and we only need delays + alerts
    along the operator's route."""
    out: dict[str, Any] = {
        "kind": kind,
        "header_ts": feed.header.timestamp,
        "entity_count": len(feed.entity),
    }
    if kind == "trip_updates":
        # Only keep trip_updates with at least one stop_time_update.
        trips = []
        for e in feed.entity:
            if not e.HasField("trip_update"):
                continue
            tu = e.trip_update
            stus = []
            for stu in tu.stop_time_update:
                rec: dict[str, Any] = {"stop_id": stu.stop_id, "stop_seq": stu.stop_sequence}
                if stu.HasField("arrival"):
                    rec["arrival_time"] = stu.arrival.time
                    rec["arrival_delay"] = stu.arrival.delay
                if stu.HasField("departure"):
                    rec["departure_time"] = stu.departure.time
                    rec["departure_delay"] = stu.departure.delay
                stus.append(rec)
            trips.append({
                "trip_id": tu.trip.trip_id,
                "route_id": tu.trip.route_id,
                "start_date": tu.trip.start_date,
                "start_time": tu.trip.start_time,
                "stops": stus,
            })
        out["trips"] = trips
    elif kind == "service_alerts":
        alerts = []
        for e in feed.entity:
            if not e.HasField("alert"):
                continue
            a = e.alert
            alerts.append({
                "header": _proto_translated(a.header_text),
                "description": _proto_translated(a.description_text),
                "effect": a.effect,
                "cause": a.cause,
                "informed_entity_count": len(a.informed_entity),
            })
        out["alerts"] = alerts
    elif kind == "vehicle_positions":
        vehicles = []
        for e in feed.entity:
            if not e.HasField("vehicle"):
                continue
            v = e.vehicle
            vehicles.append({
                "trip_id": v.trip.trip_id,
                "route_id": v.trip.route_id,
                "lat": v.position.latitude if v.HasField("position") else None,
                "lon": v.position.longitude if v.HasField("position") else None,
                "ts": v.timestamp,
                "stop_id": v.stop_id,
                "current_status": v.current_status,
            })
        out["vehicles"] = vehicles
    return out


def _proto_translated(field: Any) -> str:
    """Pull the first translation out of a GTFS-RT TranslatedString."""
    try:
        if field.translation:
            return field.translation[0].text
    except Exception:  # noqa: BLE001
        pass
    return ""


# ---------------------------------------------------------------------------
# SFMTA Muni via 511.org
# ---------------------------------------------------------------------------

async def poll_sfmta(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """SFMTA Muni via 511.org (agency=SF)."""
    return await _poll_511_agency(SFMTA_AGENCY, "sfmta.gtfs_rt", "sfmta.json", client)


# ---------------------------------------------------------------------------
# poll_all() — driver
# ---------------------------------------------------------------------------

async def poll_all() -> dict[str, Any]:
    """Run every poller in parallel, return a summary."""
    import asyncio
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=POLL_HTTP_TIMEOUT) as client:
        results = await asyncio.gather(
            poll_caltrain(client),
            poll_sfmta(client),
            return_exceptions=True,
        )
    elapsed = time.monotonic() - started
    return {
        "ts": _now_iso(),
        "elapsed_s": round(elapsed, 3),
        "caltrain": results[0] if not isinstance(results[0], BaseException) else {"error": str(results[0])},
        "sfmta": results[1] if not isinstance(results[1], BaseException) else {"error": str(results[1])},
    }


def read_state() -> dict[str, Any]:
    """Return the most recent state snapshot for all sources (UI consumption)."""
    return {
        "caltrain": _read_json("caltrain.json"),
        "sfmta": _read_json("sfmta.json"),
        "status": _read_json("sources_status.json"),
    }


def read_alerts() -> list[dict[str, Any]]:
    """Combined service-alert list across Caltrain + Muni for the brief."""
    out: list[dict[str, Any]] = []
    for src in ("caltrain.json", "sfmta.json"):
        data = _read_json(src)
        feeds = data.get("feeds") or {}
        for alert in (feeds.get("service_alerts") or {}).get("alerts", []):
            out.append({
                "source": src.split(".", 1)[0],
                "header": alert.get("header", ""),
                "description": alert.get("description", ""),
                "effect": alert.get("effect"),
            })
    return out
