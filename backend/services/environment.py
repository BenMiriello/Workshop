"""
Environment / weather data spine for the Birds app.

Self-contained on purpose:
  - Its OWN SQLite DB (~/birdnet/data/environment.db), separate from the
    birds browser DB, so concurrent edits to birds_analyze.py never collide
    and a schema change here can't corrupt detections.
  - Astro (sun/moon) is pure-stdlib (NOAA sunrise/sunset + synodic moon
    phase) — no third-party deps, no network. Runnable anywhere, anytime.
  - Weather (Open-Meteo) + migration (BirdCast) fetchers use httpx and
    therefore need outbound egress. They DEGRADE GRACEFULLY (log + return
    None) when egress is blocked, so the astro layer is never held hostage
    to the network question.

Location-keyed throughout (location_id + lat/lon) so the same spine serves
the yard, travel, and forecast-only pinned locations later.

CLI (run from backend/):
    .venv/bin/python -m services.environment backfill-astro
    .venv/bin/python -m services.environment astro 37.3696 -122.0957 2026-06-09
    .venv/bin/python -m services.environment backfill-weather   # needs egress
    .venv/bin/python -m services.environment status
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

# ---------------------------------------------------------------- paths / db

_DB = os.environ.get(
    "BIRDNET_ENV_DB", os.path.expanduser("~/birdnet/data/environment.db")
)
_BROWSER_DB = os.environ.get(
    "BIRDNET_BROWSER_DB", os.path.expanduser("~/birdnet/data/browser-stations.db")
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS astro (
    location_id INTEGER NOT NULL,
    date        TEXT    NOT NULL,          -- local civil date YYYY-MM-DD
    sunrise     TEXT, sunset      TEXT,    -- ISO-8601 UTC
    civil_dawn  TEXT, civil_dusk  TEXT,
    solar_noon  TEXT,
    day_length_min REAL,
    moon_phase  REAL,                       -- 0..1 (0=new, .5=full)
    moon_illum  REAL,                       -- 0..1 illuminated fraction
    moon_name   TEXT,
    PRIMARY KEY (location_id, date)
);
CREATE TABLE IF NOT EXISTS conditions (
    location_id INTEGER NOT NULL,
    ts_hour     TEXT    NOT NULL,           -- ISO-8601 UTC, top of hour
    temp REAL, apparent_temp REAL,
    pressure_msl REAL, surface_pressure REAL,
    wind_speed REAL, wind_gust REAL, wind_dir REAL,
    precip REAL, precip_prob REAL,
    cloud_total REAL, cloud_low REAL, cloud_mid REAL, cloud_high REAL,
    humidity REAL, radiation REAL, wx_code INTEGER,
    source TEXT,                            -- 'archive' | 'forecast'
    PRIMARY KEY (location_id, ts_hour)
);
CREATE TABLE IF NOT EXISTS migration (
    location_id INTEGER NOT NULL,
    night_date  TEXT    NOT NULL,
    traffic_total REAL, rate REAL, direction REAL, speed REAL, altitude REAL,
    source TEXT,
    PRIMARY KEY (location_id, night_date)
);
CREATE TABLE IF NOT EXISTS env_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = sqlite3.connect(_DB, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    c.executescript(_SCHEMA)
    return c


# ---------------------------------------------------------------- astro (stdlib)
# Classic Almanac sunrise/sunset algorithm (Ed Williams / USNO). Returns the
# event time in UTC for the given civil date, or None when the sun stays up/down.

_ZENITH_OFFICIAL = 90.833
_ZENITH_CIVIL = 96.0


def _sun_event(lat, lon, d: date, zenith: float, rising: bool):
    N = d.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = N + ((6 if rising else 18) - lng_hour) / 24.0
    M = (0.9856 * t) - 3.289
    L = M + (1.916 * math.sin(math.radians(M))) \
        + (0.020 * math.sin(math.radians(2 * M))) + 282.634
    L %= 360
    RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
    RA += ((math.floor(L / 90) * 90) - (math.floor(RA / 90) * 90))
    RA /= 15.0
    sin_dec = 0.39782 * math.sin(math.radians(L))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(lat)))) \
        / (cos_dec * math.cos(math.radians(lat)))
    if cos_h > 1 or cos_h < -1:
        return None  # never rises / never sets at this zenith
    H = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    H /= 15.0
    T = H + RA - (0.06571 * t) - 6.622
    UT = (T - lng_hour) % 24
    h = int(UT)
    m = int((UT - h) * 60)
    s = int(((UT - h) * 60 - m) * 60)
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=timezone.utc)


_SYNODIC = 29.530588853
_NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)  # known new moon
_MOON_NAMES = [
    "New", "Waxing crescent", "First quarter", "Waxing gibbous",
    "Full", "Waning gibbous", "Last quarter", "Waning crescent",
]


def moon_state(d: date):
    noon = datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc)
    age = ((noon - _NEW_MOON_REF).total_seconds() / 86400.0) % _SYNODIC
    phase = age / _SYNODIC                                  # 0..1
    illum = (1 - math.cos(2 * math.pi * phase)) / 2         # 0..1
    name = _MOON_NAMES[int((phase * 8 + 0.5) % 8)]
    return round(phase, 4), round(illum, 4), name


def astro_for(lat: float, lon: float, d: date) -> dict:
    sr = _sun_event(lat, lon, d, _ZENITH_OFFICIAL, True)
    ss = _sun_event(lat, lon, d, _ZENITH_OFFICIAL, False)
    cd = _sun_event(lat, lon, d, _ZENITH_CIVIL, True)
    cdk = _sun_event(lat, lon, d, _ZENITH_CIVIL, False)
    # _sun_event stamps the event on civil date `d` with a UTC time in [0,24).
    # West of Greenwich, sunset/dusk fall in the next UTC day, so their instant
    # rolls before sunrise on the same date — push them forward a day so the
    # stored instants are correct and day length is positive.
    if sr and ss and ss <= sr:
        ss += timedelta(days=1)
    if cd and cdk and cdk <= cd:
        cdk += timedelta(days=1)
    noon = None
    day_len = None
    if sr and ss:
        noon = sr + (ss - sr) / 2
        day_len = round((ss - sr).total_seconds() / 60.0, 1)
    ph, il, nm = moon_state(d)
    iso = lambda x: x.isoformat() if x else None
    return {
        "sunrise": iso(sr), "sunset": iso(ss),
        "civil_dawn": iso(cd), "civil_dusk": iso(cdk),
        "solar_noon": iso(noon), "day_length_min": day_len,
        "moon_phase": ph, "moon_illum": il, "moon_name": nm,
    }


# ---------------------------------------------------------------- locations

def _locations():
    """Active locations with coords, read from the birds browser DB."""
    if not os.path.exists(_BROWSER_DB):
        return []
    c = sqlite3.connect(f"file:{_BROWSER_DB}?mode=ro", uri=True)
    try:
        rows = c.execute(
            "SELECT id, name, lat, lon FROM locations "
            "WHERE deleted_at IS NULL AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
    finally:
        c.close()
    return [{"id": r[0], "name": r[1], "lat": r[2], "lon": r[3]} for r in rows]


def _detection_date_range():
    """(min_date, max_date) over stored detections, or (None, None)."""
    if not os.path.exists(_BROWSER_DB):
        return None, None
    c = sqlite3.connect(f"file:{_BROWSER_DB}?mode=ro", uri=True)
    try:
        r = c.execute("SELECT min(ts), max(ts) FROM browser_detections").fetchone()
    finally:
        c.close()
    if not r or not r[0]:
        return None, None
    f = lambda s: datetime.fromisoformat(s).date()
    return f(r[0]), f(r[1])


# ---------------------------------------------------------------- backfill astro

def backfill_astro(pad_days: int = 1) -> int:
    """Compute astro for every active location across the detection date
    range (+pad). Idempotent. Returns rows written."""
    locs = _locations()
    lo, hi = _detection_date_range()
    if not locs or not lo:
        print("backfill-astro: no locations or no detections yet")
        return 0
    lo -= timedelta(days=pad_days)
    hi += timedelta(days=pad_days)
    n = 0
    with _conn() as c:
        d = lo
        while d <= hi:
            for L in locs:
                a = astro_for(L["lat"], L["lon"], d)
                c.execute(
                    "INSERT OR REPLACE INTO astro (location_id,date,sunrise,sunset,"
                    "civil_dawn,civil_dusk,solar_noon,day_length_min,moon_phase,"
                    "moon_illum,moon_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (L["id"], d.isoformat(), a["sunrise"], a["sunset"],
                     a["civil_dawn"], a["civil_dusk"], a["solar_noon"],
                     a["day_length_min"], a["moon_phase"], a["moon_illum"],
                     a["moon_name"]),
                )
                n += 1
            d += timedelta(days=1)
        c.execute("INSERT OR REPLACE INTO env_meta VALUES ('astro_backfilled', ?)",
                  (datetime.now(timezone.utc).isoformat(),))
    return n


# ---------------------------------------------------------------- weather (egress-gated)

_OPEN_METEO_HOURLY = (
    "temperature_2m,apparent_temperature,surface_pressure,pressure_msl,"
    "wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation,"
    "precipitation_probability,cloud_cover,cloud_cover_low,cloud_cover_mid,"
    "cloud_cover_high,relative_humidity_2m,shortwave_radiation,weather_code"
)


def fetch_weather(lat, lon, start: date, end: date, archive=True):
    """Pull hourly weather from Open-Meteo. Returns parsed hourly dict or None
    if egress is blocked / the call fails. Never raises."""
    try:
        import httpx
    except Exception:
        return None
    base = ("https://archive-api.open-meteo.com/v1/archive" if archive
            else "https://api.open-meteo.com/v1/forecast")
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": _OPEN_METEO_HOURLY, "timezone": "UTC",
    }
    try:
        r = httpx.get(base, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("hourly")
    except Exception as e:  # egress blocked, rate limit, etc.
        print(f"fetch_weather: egress/fetch failed ({type(e).__name__}: {e})")
        return None


def backfill_weather() -> int:
    """Backfill hourly conditions for active locations over the detection
    range. No-op (returns -1) if egress is blocked. Idempotent."""
    locs = _locations()
    lo, hi = _detection_date_range()
    if not locs or not lo:
        print("backfill-weather: no locations or no detections yet")
        return 0
    wrote = 0
    blocked = False
    with _conn() as c:
        for L in locs:
            h = fetch_weather(L["lat"], L["lon"], lo, hi, archive=True)
            if h is None:
                blocked = True
                continue
            times = h.get("time", [])
            col = lambda k, i: (h.get(k) or [None] * len(times))[i]
            for i, t in enumerate(times):
                ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).isoformat()
                c.execute(
                    "INSERT OR REPLACE INTO conditions (location_id,ts_hour,temp,"
                    "apparent_temp,pressure_msl,surface_pressure,wind_speed,wind_gust,"
                    "wind_dir,precip,precip_prob,cloud_total,cloud_low,cloud_mid,"
                    "cloud_high,humidity,radiation,wx_code,source) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (L["id"], ts, col("temperature_2m", i), col("apparent_temperature", i),
                     col("pressure_msl", i), col("surface_pressure", i),
                     col("wind_speed_10m", i), col("wind_gusts_10m", i),
                     col("wind_direction_10m", i), col("precipitation", i),
                     col("precipitation_probability", i), col("cloud_cover", i),
                     col("cloud_cover_low", i), col("cloud_cover_mid", i),
                     col("cloud_cover_high", i), col("relative_humidity_2m", i),
                     col("shortwave_radiation", i), col("weather_code", i), "archive"),
                )
                wrote += 1
        c.execute("INSERT OR REPLACE INTO env_meta VALUES ('weather_backfilled', ?)",
                  (datetime.now(timezone.utc).isoformat(),))
    return -1 if (blocked and wrote == 0) else wrote


# ---------------------------------------------------------------- status / cli

def refresh(days_back: int = 3, days_fwd: int = 7) -> int:
    """Keep recent + near-future conditions fresh via the forecast endpoint
    (covers recent past days AND a 7-day forecast for the week view). Plus astro
    for the window. Run nightly (lifespan loop or cron). Idempotent. -1 if egress
    blocked."""
    locs = _locations()
    if not locs:
        return 0
    today = datetime.now(timezone.utc).date()
    lo, hi = today - timedelta(days=days_back), today + timedelta(days=days_fwd)
    wrote, blocked = 0, False
    with _conn() as c:
        d = lo
        while d <= hi:
            for L in locs:
                a = astro_for(L["lat"], L["lon"], d)
                c.execute(
                    "INSERT OR REPLACE INTO astro (location_id,date,sunrise,sunset,"
                    "civil_dawn,civil_dusk,solar_noon,day_length_min,moon_phase,"
                    "moon_illum,moon_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (L["id"], d.isoformat(), a["sunrise"], a["sunset"], a["civil_dawn"],
                     a["civil_dusk"], a["solar_noon"], a["day_length_min"],
                     a["moon_phase"], a["moon_illum"], a["moon_name"]))
            d += timedelta(days=1)
        for L in locs:
            h = fetch_weather(L["lat"], L["lon"], lo, hi, archive=False)
            if h is None:
                blocked = True
                continue
            times = h.get("time", [])
            col = lambda k, i: (h.get(k) or [None] * len(times))[i]
            for i, t in enumerate(times):
                ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).isoformat()
                c.execute(
                    "INSERT OR REPLACE INTO conditions (location_id,ts_hour,temp,"
                    "apparent_temp,pressure_msl,surface_pressure,wind_speed,wind_gust,"
                    "wind_dir,precip,precip_prob,cloud_total,cloud_low,cloud_mid,"
                    "cloud_high,humidity,radiation,wx_code,source) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (L["id"], ts, col("temperature_2m", i), col("apparent_temperature", i),
                     col("pressure_msl", i), col("surface_pressure", i),
                     col("wind_speed_10m", i), col("wind_gusts_10m", i),
                     col("wind_direction_10m", i), col("precipitation", i),
                     col("precipitation_probability", i), col("cloud_cover", i),
                     col("cloud_cover_low", i), col("cloud_cover_mid", i),
                     col("cloud_cover_high", i), col("relative_humidity_2m", i),
                     col("shortwave_radiation", i), col("weather_code", i), "forecast"))
                wrote += 1
        c.execute("INSERT OR REPLACE INTO env_meta VALUES ('last_refresh', ?)",
                  (datetime.now(timezone.utc).isoformat(),))
    return -1 if (blocked and wrote == 0) else wrote


def get_astro(location_id: int, date_str: str):
    with _conn() as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM astro WHERE location_id=? AND date=?",
                      (location_id, date_str)).fetchone()
    return dict(r) if r else None


def get_conditions(location_id: int, start_iso: str, end_iso: str):
    """Hourly conditions in [start, end] (ISO-8601 UTC), ascending."""
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT * FROM conditions WHERE location_id=? AND ts_hour>=? AND ts_hour<=? "
            "ORDER BY ts_hour", (location_id, start_iso, end_iso)).fetchall()
    return [dict(r) for r in rows]


def status() -> dict:
    with _conn() as c:
        a = c.execute("SELECT count(*) FROM astro").fetchone()[0]
        w = c.execute("SELECT count(*) FROM conditions").fetchone()[0]
        m = c.execute("SELECT count(*) FROM migration").fetchone()[0]
        meta = dict(c.execute("SELECT key,value FROM env_meta").fetchall())
    lo, hi = _detection_date_range()
    return {"astro_rows": a, "conditions_rows": w, "migration_rows": m,
            "detection_range": [lo and lo.isoformat(), hi and hi.isoformat()],
            "locations": _locations(), "meta": meta, "db": _DB}


def _main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "backfill-astro":
        print("astro rows written:", backfill_astro())
    elif cmd == "backfill-weather":
        n = backfill_weather()
        print("EGRESS BLOCKED" if n == -1 else f"conditions rows written: {n}")
    elif cmd == "refresh":
        n = refresh()
        print("EGRESS BLOCKED" if n == -1 else f"conditions rows refreshed: {n}")
    elif cmd == "astro" and len(argv) >= 4:
        lat, lon, d = float(argv[1]), float(argv[2]), date.fromisoformat(argv[3])
        import json
        print(json.dumps(astro_for(lat, lon, d), indent=2))
    elif cmd == "status":
        import json
        print(json.dumps(status(), indent=2, default=str))
    else:
        print(__doc__)


if __name__ == "__main__":
    _main(sys.argv[1:])
