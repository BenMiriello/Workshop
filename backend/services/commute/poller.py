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

from services import auth_env

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
# One address for the secret file, resolved by services.auth_env. This module
# used to hardcode a different directory than the rest of the platform read, and
# only a symlink between the two kept it working.
#
# None means "resolve at call time", which is the point: a constant captured at
# import is the same mistake in a new place, and this module is where it already
# cost a whole commute of dark realtime. A test overrides it with a fixture path.
AUTH_ENV: str | None = None

POLL_HTTP_TIMEOUT = 8.0


def _key_511() -> str:
    """The 511.org API key. Prefer the env var, else fall back to auth.env so the
    key works regardless of how the backend was launched. The platform's
    auto-restart doesn't always inject the env — when it didn't, the old
    import-time constant stayed empty and every poll returned `needs_key` with
    realtime dark for the whole commute. Read at call time (not import) so a key
    present on disk is always picked up. Mirrors reason._oauth_token."""
    return auth_env.value("WORKSHOP_511_API_KEY", path=AUTH_ENV)


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
    key = _key_511()
    if not key:
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
        params = {"agency": agency, "api_key": key}
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
    """Run every poller in parallel, return a summary. Also appends the freshly
    fetched vehicle positions to a rolling history (FREE — no extra request) so
    a finished rail/transit leg can later be matched to the vehicle that ran it."""
    import asyncio
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=POLL_HTTP_TIMEOUT) as client:
        results = await asyncio.gather(
            poll_caltrain(client),
            poll_sfmta(client),
            return_exceptions=True,
        )
    elapsed = time.monotonic() - started
    out = {
        "ts": _now_iso(),
        "elapsed_s": round(elapsed, 3),
        "caltrain": results[0] if not isinstance(results[0], BaseException) else {"error": str(results[0])},
        "sfmta": results[1] if not isinstance(results[1], BaseException) else {"error": str(results[1])},
    }
    try:
        record_vehicle_history()
    except Exception:  # noqa: BLE001 - history is best-effort
        pass
    return out


# ---------------------------------------------------------------------------
# Rolling vehicle-position history (for transit_match) — derived for free from
# the vehicle_positions feed already fetched each poll. Append-only jsonl,
# trimmed to the last VEHICLE_HISTORY_HOURS so it can't grow without bound.
# ---------------------------------------------------------------------------
VEHICLE_HISTORY_HOURS = 6.0


def record_vehicle_history(now: datetime | None = None) -> int:
    """Append the latest vehicle snapshot (both agencies) to vehicles.jsonl.
    Returns how many vehicle rows were written. Trims old rows opportunistically."""
    poll_ts = (now or datetime.now(timezone.utc)).isoformat()
    rows: list[dict[str, Any]] = []
    for agency, fname in (("caltrain", "caltrain.json"), ("sfmta", "sfmta.json")):
        feeds = (_read_json(fname) or {}).get("feeds") or {}
        for v in (feeds.get("vehicle_positions") or {}).get("vehicles", []):
            if v.get("lat") is None or v.get("lon") is None:
                continue
            rows.append({
                "poll_ts": poll_ts,
                "agency": agency,
                "trip_id": v.get("trip_id"),
                "route_id": v.get("route_id"),
                "lat": v.get("lat"),
                "lon": v.get("lon"),
                "vts": v.get("ts"),          # feed-reported vehicle timestamp (epoch s)
                "stop_id": v.get("stop_id"),
            })
    if not rows:
        return 0
    p = config.runtime_path("vehicles.jsonl")
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    _trim_vehicle_history(p, now)
    return len(rows)


def _trim_vehicle_history(p: "Path", now: datetime | None = None) -> None:
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - VEHICLE_HISTORY_HOURS * 3600
    try:
        with p.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    if len(lines) < 2000:  # only rewrite when it's worth it
        return
    kept = []
    for ln in lines:
        try:
            rec = json.loads(ln)
            ts = datetime.fromisoformat(str(rec["poll_ts"]).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if ts >= cutoff:
            kept.append(ln)
    tmp = p.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    tmp.replace(p)


def read_vehicle_history(since: datetime | None = None) -> list[dict[str, Any]]:
    """All vehicle rows with poll_ts >= since (default: all retained)."""
    p = config.runtime_path("vehicles.jsonl")
    if not p.exists():
        return []
    cut = since.timestamp() if since else None
    out: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip():
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if cut is not None:
                try:
                    ts = datetime.fromisoformat(str(rec["poll_ts"]).replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                if ts < cut:
                    continue
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# StopMonitoring (SIRI/JSON) — predicted departures at a SPECIFIC stop. Used for
# the LEARNED boarding stops only, during active windows, to answer "when does
# the next train actually leave my stop / will I make it". 1 request per stop.
# ---------------------------------------------------------------------------
async def poll_stop_monitoring(agency: str, stop_code: str,
                               client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch SIRI StopMonitoring JSON for one stop. Returns a compact summary
    of upcoming departures: {stop_code, agency, ts, visits:[{line, dest,
    aimed, expected, mins}]}. Persisted into stopmon.json keyed by stop_code."""
    key = _key_511()
    if not key:
        return {"status": "needs_key"}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=POLL_HTTP_TIMEOUT, follow_redirects=True)
    try:
        r = await client.get(f"{TRANSIT_511_BASE}/StopMonitoring",
                             params={"api_key": key, "agency": agency,
                                     "stopCode": stop_code, "format": "json"})
        if r.status_code != 200:
            return {"status": "error", "http": r.status_code, "body": r.text[:160]}
        # 511 JSON often carries a UTF-8 BOM.
        data = json.loads(r.content.decode("utf-8-sig"))
        summary = _summarize_stop_monitoring(data, agency, stop_code)
        cur = _read_json("stopmon.json")
        cur[f"{agency}:{stop_code}"] = summary
        _write_json("stopmon.json", cur)
        return summary
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)[:160]}
    finally:
        if owns:
            await client.aclose()


def _summarize_stop_monitoring(data: dict[str, Any], agency: str, stop_code: str) -> dict[str, Any]:
    visits_out: list[dict[str, Any]] = []
    try:
        delivery = (data["ServiceDelivery"]["StopMonitoringDelivery"])
        if isinstance(delivery, list):
            delivery = delivery[0]
        msv = delivery.get("MonitoredStopVisit", []) or []
    except (KeyError, TypeError, IndexError):
        msv = []
    for v in msv:
        try:
            j = v["MonitoredVehicleJourney"]
            call = j.get("MonitoredCall", {}) or {}
            aimed = call.get("AimedDepartureTime") or call.get("AimedArrivalTime")
            expected = call.get("ExpectedDepartureTime") or call.get("ExpectedArrivalTime")
            visits_out.append({
                "line": j.get("LineRef"),
                "dest": j.get("DestinationName"),
                "aimed": aimed,
                "expected": expected,
            })
        except (KeyError, TypeError):
            continue
    return {
        "agency": agency,
        "stop_code": stop_code,
        "ts": _now_iso(),
        "visits": visits_out,
    }


def read_stop_monitoring() -> dict[str, Any]:
    return _read_json("stopmon.json")


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
