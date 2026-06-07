"""Browser mic-test bridge: take an audio chunk from the web app, run it through
the resident BirdNET analyzer service, store + return detections.

This is the "test without the always-on mic" path. Detections land in a SEPARATE
backend-owned SQLite (never the BirdNET-Go yard DB) under a named test station,
so test audio never merges into the yard species list and is never published.

The heavy ML runs in the isolated analyzer service (services/../birdnet/analyzer);
this module only does ffmpeg transcode + a localhost HTTP call + storage.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

BROWSER_DB = Path(os.environ.get("BIRDNET_BROWSER_DB", "/home/clawd-a/birdnet/data/browser-stations.db"))
ANALYZER_URL = os.environ.get("BIRDNET_ANALYZER_URL", "http://127.0.0.1:8012")

# Same species at the same location within this many seconds is treated as one
# continuing detection (the live UI sends overlapping 3s windows every 1s).
DEDUP_S = 2.5

# BirdNET GLOBAL_6K_V2.4 recognizes some non-avian classes. We always STORE them
# (the model keeps detecting), but flag them so the UI can hide them by default.
# Two groups: (1) noise/human/machine pseudo-classes, which use a self-named
# label (scientific_name == common_name): Dog_Dog, Engine_Engine, Noise_Noise,
# "Human vocal_Human vocal", Siren_Siren, Power tools, Fireworks, Gun, ...;
# (2) frogs, toads, crickets, and mammals, identified by genus.
_NON_AVIAN_GENERA = {
    "Acris", "Anaxyrus", "Canis", "Dryophytes", "Eleutherodactylus",
    "Gastrophryne", "Hyliola", "Incilius", "Lithobates", "Pseudacris",
    "Scaphiopus", "Spea", "Gryllus", "Miogryllus",
}


def is_bird(scientific_name: str | None, common_name: str | None) -> bool:
    sci = (scientific_name or "").strip()
    if not sci:
        return False
    if sci == (common_name or "").strip():  # noise/human/machine self-class
        return False
    return sci.split(" ", 1)[0] not in _NON_AVIAN_GENERA


def _bird_clause(include_nonbird: bool) -> str:
    """SQL fragment appended to a WHERE that already has a condition."""
    return "" if include_nonbird else " AND is_bird=1"


def nonbird_exclusion_sql(sci: str = "scientific_name", common: str = "common_name") -> str:
    """SQL boolean that is TRUE when a row is a NON-bird, mirroring is_bird().
    For DBs without an is_bird column (the yard BirdNET-Go DB is read-only and
    owned by BirdNET-Go), so non-birds can be filtered by label. The genera are
    hardcoded constants (plain alpha), so inlining them as literals is safe."""
    genera = ",".join(f"'{g}'" for g in sorted(_NON_AVIAN_GENERA))
    return (f"({sci} = {common} OR "
            f"substr({sci},1,instr({sci}||' ',' ')-1) IN ({genera}))")


def _conn() -> sqlite3.Connection:
    BROWSER_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(BROWSER_DB, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS browser_detections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        station TEXT, ts TEXT,
        scientific_name TEXT, common_name TEXT, confidence REAL,
        start_s REAL, end_s REAL, device_token TEXT, is_bird INTEGER DEFAULT 1)""")
    # Legacy "stations" table (token -> label). Kept for back-compat; the model
    # is now split into devices (things that listen) and locations (places).
    c.execute("""CREATE TABLE IF NOT EXISTS stations (
        token TEXT PRIMARY KEY, label TEXT, created_at TEXT, last_seen TEXT)""")
    # A location is a PLACE. A detection is attributed to one. Locations within a
    # house-plot radius merge (see resolve_location). lat/lon are the centroid.
    c.execute("""CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, lat REAL, lon REAL, address TEXT,
        radius_m REAL DEFAULT 40, created_at TEXT)""")
    # A device is a THING that listens (this phone, the Pi). 'adaptive' follows
    # the current GPS each session; 'fixed' is pinned to fixed_location_id.
    c.execute("""CREATE TABLE IF NOT EXISTS devices (
        token TEXT PRIMARY KEY, name TEXT, mode TEXT DEFAULT 'adaptive',
        fixed_location_id INTEGER, created_at TEXT, last_seen TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY, value TEXT)""")

    cols = {r["name"] for r in c.execute("PRAGMA table_info(browser_detections)")}
    # is_bird: no DEFAULT so existing rows become NULL and the backfill below
    # reclassifies them (a plain DEFAULT 1 would mark old noise rows as birds).
    if "is_bird" not in cols:
        c.execute("ALTER TABLE browser_detections ADD COLUMN is_bird INTEGER")
    for col, ddl in (("device_name", "TEXT"), ("location_id", "INTEGER"),
                     ("lat", "REAL"), ("lon", "REAL")):
        if col not in cols:
            c.execute(f"ALTER TABLE browser_detections ADD COLUMN {col} {ddl}")

    c.execute("CREATE INDEX IF NOT EXISTS idx_bd_station_ts ON browser_detections(station, ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bd_loc ON browser_detections(location_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bd_sci ON browser_detections(scientific_name)")

    pending = c.execute(
        "SELECT id, scientific_name, common_name FROM browser_detections WHERE is_bird IS NULL"
    ).fetchall()
    if pending:
        c.executemany("UPDATE browser_detections SET is_bird=? WHERE id=?",
                      [(1 if is_bird(r["scientific_name"], r["common_name"]) else 0, r["id"])
                       for r in pending])

    # One-time migration of the old single-"station" model into devices+locations:
    # each legacy station name becomes BOTH a device (it listened) and a location
    # (the place), so the existing per-station page keeps working. The operator
    # can then rename the location and pin real coordinates.
    if not c.execute("SELECT 1 FROM devices LIMIT 1").fetchone():
        for s in c.execute("SELECT token,label,created_at,last_seen FROM stations").fetchall():
            c.execute("INSERT OR IGNORE INTO devices(token,name,mode,created_at,last_seen) "
                      "VALUES(?,?,'adaptive',?,?)",
                      (s["token"], s["label"], s["created_at"], s["last_seen"]))
    # Legacy detections have no real place. File them under a single "Untitled
    # location" the operator must pin to a real address/GPS; never name a place
    # after a device.
    legacy = c.execute(
        "SELECT DISTINCT station FROM browser_detections "
        "WHERE location_id IS NULL AND station IS NOT NULL AND station<>''").fetchall()
    if legacy:
        u = c.execute("SELECT id FROM locations WHERE name='Untitled location'").fetchone()
        uid = u["id"] if u else c.execute(
            "INSERT INTO locations(name,created_at) VALUES('Untitled location',?)", (_now(),)).lastrowid
        for row in legacy:
            c.execute("UPDATE browser_detections SET location_id=?, "
                      "device_name=COALESCE(device_name,station) "
                      "WHERE station=? AND location_id IS NULL", (uid, row["station"]))
    c.commit()
    return c


def analyzer_health() -> dict:
    try:
        with urllib.request.urlopen(f"{ANALYZER_URL}/health", timeout=3) as r:
            return {"up": True, **json.loads(r.read())}
    except Exception as e:  # noqa: BLE001
        return {"up": False, "error": str(e)}


def register_station(token: str, label: str) -> None:
    if not token or not label:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO stations(token,label,created_at,last_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(token) DO UPDATE SET label=excluded.label, last_seen=excluded.last_seen",
            (token, label, now, now),
        )


def station_for_token(token: str) -> str | None:
    if not token:
        return None
    with _conn() as c:
        row = c.execute("SELECT label FROM stations WHERE token=?", (token,)).fetchone()
    return row["label"] if row else None


def _transcode_to_wav(audio: bytes, content_type: str) -> str:
    """Write the uploaded chunk to a tmp file and ffmpeg it to 48k mono WAV.
    iOS produces mp4/aac, Chrome webm/opus - ffmpeg handles both. Returns the
    wav path (caller deletes)."""
    suffix = ".bin"
    ct = (content_type or "").lower()
    if "mp4" in ct or "aac" in ct or "m4a" in ct:
        suffix = ".mp4"
    elif "webm" in ct:
        suffix = ".webm"
    elif "ogg" in ct or "opus" in ct:
        suffix = ".ogg"
    elif "wav" in ct:
        suffix = ".wav"
    src = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    src.write(audio)
    src.close()
    wav = src.name + ".wav"
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", src.name, "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", wav],
        capture_output=True,
    )
    os.unlink(src.name)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    return wav


def _call_analyzer(wav_path: str, min_conf: float) -> list[dict]:
    body = json.dumps({"path": wav_path, "min_conf": min_conf}).encode()
    req = urllib.request.Request(f"{ANALYZER_URL}/analyze", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("detections", [])


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def resolve_location(c: sqlite3.Connection, token: str, device_name: str, mode: str,
                     lat: float | None, lon: float | None, address: str,
                     location_id: int | None) -> tuple[int | None, str | None]:
    """Map a listening device + its current position to a REAL location id+name.

    A location is always a real place - a verified street address or GPS point -
    NEVER a device name. So this can return (None, None), which the caller turns
    into a "needs_location" signal asking the user to pin one.

    fixed device   -> its pinned location (or the explicit location_id).
    adaptive + GPS -> the nearest existing location within its merge radius
                      (so a phone returning to the same yard re-uses it), else a
                      new location is created at those coordinates (named by the
                      reverse-geocoded address the client passes, else the coords).
    no GPS          -> only the device's last REAL location is reused; we never
                      invent a place out of thin air.
    """
    if mode == "fixed":
        lid = location_id
        if not lid:
            dev = c.execute("SELECT fixed_location_id FROM devices WHERE token=?", (token,)).fetchone()
            lid = dev["fixed_location_id"] if dev else None
        if lid:
            row = c.execute("SELECT id,name FROM locations WHERE id=?", (lid,)).fetchone()
            if row:
                return row["id"], row["name"]

    if lat is not None and lon is not None:
        best, best_d = None, None
        for loc in c.execute(
                "SELECT id,name,lat,lon,radius_m FROM locations "
                "WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall():
            d = _haversine_m(lat, lon, loc["lat"], loc["lon"])
            if d <= (loc["radius_m"] or 40) and (best_d is None or d < best_d):
                best, best_d = loc, d
        if best:
            return best["id"], best["name"]
        name = (address or "").strip() or f"{lat:.5f}, {lon:.5f}"
        cur = c.execute("INSERT INTO locations(name,lat,lon,address,created_at) VALUES(?,?,?,?,?)",
                        (name, lat, lon, address or None, _now()))
        return cur.lastrowid, name

    # No GPS: reuse the device's last real location if it has one, but never
    # create a device-named placeholder.
    last = c.execute(
        "SELECT location_id FROM browser_detections WHERE device_token=? "
        "AND location_id IS NOT NULL ORDER BY id DESC LIMIT 1", (token,)).fetchone()
    if last and last["location_id"]:
        row = c.execute("SELECT id,name FROM locations WHERE id=?", (last["location_id"],)).fetchone()
        if row:
            return row["id"], row["name"]
    return None, None


def analyze_chunk(audio: bytes, content_type: str, station: str,
                  token: str = "", min_conf: float = 0.25, device_name: str = "",
                  mode: str = "adaptive", lat: float | None = None,
                  lon: float | None = None, address: str = "",
                  location_id: int | None = None) -> dict:
    """Transcode -> analyze -> attribute to a (device, location) -> store.
    Returns this chunk's detections plus the resolved location."""
    wav = _transcode_to_wav(audio, content_type)
    try:
        detections = _call_analyzer(wav, min_conf)
    finally:
        try:
            os.unlink(wav)
        except OSError:
            pass

    # Tag every detection bird/non-bird so the client can filter live without a
    # round-trip; the same flag is persisted so server-side aggregates can too.
    for d in detections:
        d["is_bird"] = is_bird(d.get("scientific_name"), d.get("common_name"))

    ts = _now()
    dev_name = (device_name or station or "").strip()
    loc_id: int | None = None
    loc_name = station
    with _conn() as c:
        # Keep the device row fresh (so a just-started device appears even before
        # its first detection).
        if token:
            c.execute(
                "INSERT INTO devices(token,name,mode,created_at,last_seen) VALUES(?,?,?,?,?) "
                "ON CONFLICT(token) DO UPDATE SET "
                "name=COALESCE(NULLIF(excluded.name,''),devices.name), "
                "mode=excluded.mode, last_seen=excluded.last_seen",
                (token, dev_name, mode, ts, ts))
        # Only resolve a location when there is something to attribute (a
        # detection or a real GPS fix). A location is always a real place, so
        # this can come back empty; we then ask the UI to pin one.
        if detections or lat is not None:
            loc_id, loc_name = resolve_location(c, token, dev_name, mode, lat, lon, address, location_id)
        if loc_id is None:
            loc_name = None
        if detections and loc_id is not None:
            # The UI sends overlapping 3s windows every 1s for a live feel, so the
            # same continuous song lands in several consecutive chunks. Dedup on
            # store: skip a detection if the same species was just recorded at
            # this location within DEDUP_S. It still rides back in the live
            # response (flagged stored=False) so the UI shows it without
            # inflating stored counts.
            to_store = []
            for d in detections:
                prev = c.execute(
                    "SELECT ts FROM browser_detections WHERE location_id=? AND scientific_name=? "
                    "ORDER BY id DESC LIMIT 1", (loc_id, d["scientific_name"])).fetchone()
                dup = False
                if prev:
                    try:
                        dup = (datetime.fromisoformat(ts) -
                               datetime.fromisoformat(prev["ts"])).total_seconds() < DEDUP_S
                    except ValueError:
                        dup = False
                d["stored"] = not dup
                if not dup:
                    to_store.append(d)
            if to_store:
                c.executemany(
                    "INSERT INTO browser_detections(station,ts,scientific_name,common_name,"
                    "confidence,start_s,end_s,device_token,is_bird,device_name,location_id,lat,lon) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(loc_name, ts, d["scientific_name"], d["common_name"], d["confidence"],
                      d.get("start_s", 0.0), d.get("end_s", 0.0), token, 1 if d["is_bird"] else 0,
                      dev_name, loc_id, lat, lon)
                     for d in to_store],
                )
        # Mirror into the legacy stations table so old /station/* keeps resolving.
        if token and dev_name:
            c.execute(
                "INSERT INTO stations(token,label,created_at,last_seen) VALUES(?,?,?,?) "
                "ON CONFLICT(token) DO UPDATE SET label=excluded.label, last_seen=excluded.last_seen",
                (token, dev_name, ts, ts))
    # "needs_location" = we heard something but have nowhere real to file it
    # (adaptive device with no GPS and no prior place). The UI prompts the user
    # to pin an address; the live detections still ride back so they see them.
    needs_location = loc_id is None and bool(detections)
    return {"ts": ts, "detections": detections, "location": loc_name,
            "location_id": loc_id, "needs_location": needs_location}


def recent(station: str, limit: int = 100, include_nonbird: bool = False) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, scientific_name, common_name, round(confidence,4) AS confidence, is_bird "
            "FROM browser_detections WHERE station=?" + _bird_clause(include_nonbird) +
            " ORDER BY id DESC LIMIT ?",
            (station, max(1, min(limit, 500))),
        ).fetchall()
    return [dict(r) for r in rows]


def _local(ts: str) -> datetime:
    """Parse a stored UTC ISO ts and convert to the box's local timezone (Pacific)
    so day/hour analytics line up with when the operator actually heard things."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _rows(station: str, include_nonbird: bool = False) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT ts, scientific_name, common_name, confidence "
            "FROM browser_detections WHERE station=?" + _bird_clause(include_nonbird) +
            " ORDER BY ts", (station,)).fetchall()


def list_stations(include_nonbird: bool = False) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT station, COUNT(*) AS detections, COUNT(DISTINCT scientific_name) AS species, "
            "MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM browser_detections "
            "WHERE 1=1" + _bird_clause(include_nonbird) +
            " GROUP BY station ORDER BY last_ts DESC").fetchall()
        known = {r["label"] for r in c.execute("SELECT label FROM stations")}
    out = [dict(r) for r in rows]
    have = {r["station"] for r in rows}
    # Surface registered-but-empty stations too, so a just-named device shows up.
    for label in known - have:
        out.append({"station": label, "detections": 0, "species": 0, "first_ts": None, "last_ts": None})
    return out


def station_summary(station: str, include_nonbird: bool = False) -> dict:
    rows = _rows(station, include_nonbird)
    if not rows:
        return {"station": station, "detections": 0, "species": 0, "first_date": None,
                "last_date": None, "today": 0, "active_days": 0, "last_detection": None}
    days = {_local(r["ts"]).strftime("%Y-%m-%d") for r in rows}
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    last = rows[-1]
    return {
        "station": station,
        "detections": len(rows),
        "species": len({r["scientific_name"] for r in rows}),
        "first_date": min(days),
        "last_date": max(days),
        "active_days": len(days),
        "today": sum(1 for r in rows if _local(r["ts"]).strftime("%Y-%m-%d") == today),
        "last_detection": {
            "common_name": last["common_name"], "scientific_name": last["scientific_name"],
            "confidence": round(last["confidence"], 4),
            "at": _local(last["ts"]).strftime("%Y-%m-%d %H:%M"),
        },
    }


def station_species(station: str, include_nonbird: bool = False) -> list[dict]:
    rows = _rows(station, include_nonbird)
    agg: dict[str, dict] = {}
    for r in rows:
        sci = r["scientific_name"]
        a = agg.setdefault(sci, {
            "scientific_name": sci, "common_name": r["common_name"], "count": 0,
            "max_conf": 0.0, "_sum": 0.0, "_days": set(), "first_ts": r["ts"], "last_ts": r["ts"]})
        a["count"] += 1
        a["max_conf"] = max(a["max_conf"], r["confidence"])
        a["_sum"] += r["confidence"]
        a["_days"].add(_local(r["ts"]).strftime("%Y-%m-%d"))
        a["last_ts"] = r["ts"]
    out = []
    for a in agg.values():
        out.append({
            "scientific_name": a["scientific_name"], "common_name": a["common_name"],
            "count": a["count"], "max_conf": round(a["max_conf"], 4),
            "avg_conf": round(a["_sum"] / a["count"], 4), "days_seen": len(a["_days"]),
            "first_date": _local(a["first_ts"]).strftime("%Y-%m-%d"),
            "last_date": _local(a["last_ts"]).strftime("%Y-%m-%d"),
        })
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def station_activity(station: str, include_nonbird: bool = False) -> dict:
    rows = _rows(station, include_nonbird)
    daily: dict[str, dict] = {}
    hourly = {h: 0 for h in range(24)}
    for r in rows:
        lt = _local(r["ts"])
        d = lt.strftime("%Y-%m-%d")
        day = daily.setdefault(d, {"date": d, "count": 0, "_species": set()})
        day["count"] += 1
        day["_species"].add(r["scientific_name"])
        hourly[lt.hour] += 1
    return {
        "daily": [{"date": d["date"], "count": d["count"], "species": len(d["_species"])}
                  for d in sorted(daily.values(), key=lambda x: x["date"])],
        "hourly": [{"hour": h, "count": hourly[h]} for h in range(24)],
    }


def species_detail(station: str, sci: str) -> dict:
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, common_name, confidence FROM browser_detections "
            "WHERE station=? AND scientific_name=? ORDER BY ts", (station, sci)).fetchall()
    hourly = {h: 0 for h in range(24)}
    daily: dict[str, int] = {}
    for r in rows:
        lt = _local(r["ts"])
        hourly[lt.hour] += 1
        d = lt.strftime("%Y-%m-%d")
        daily[d] = daily.get(d, 0) + 1
    common = rows[-1]["common_name"] if rows else sci
    return {
        "scientific_name": sci, "common_name": common, "count": len(rows),
        "hourly": [{"hour": h, "count": hourly[h]} for h in range(24)],
        "daily": [{"date": d, "count": n} for d, n in sorted(daily.items())],
        "recent": [{"at": _local(r["ts"]).strftime("%m-%d %H:%M:%S"),
                    "confidence": round(r["confidence"], 4)} for r in rows[-30:][::-1]],
    }


def session_species(station: str, since_ts: str, include_nonbird: bool = False) -> list[dict]:
    """Unique species seen for a station since a timestamp, best confidence first
    - the 'detected this session' summary."""
    with _conn() as c:
        rows = c.execute(
            "SELECT scientific_name, MAX(common_name) AS common_name, is_bird, "
            "COUNT(*) AS hits, round(MAX(confidence),4) AS max_conf, MAX(ts) AS last_ts "
            "FROM browser_detections WHERE station=? AND ts>=?" + _bird_clause(include_nonbird) +
            " GROUP BY scientific_name ORDER BY max_conf DESC",
            (station, since_ts),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Settings (home source), devices, and locations
# --------------------------------------------------------------------------

def get_setting(key: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def list_devices() -> list[dict]:
    with _conn() as c:
        devs = c.execute(
            "SELECT token,name,mode,fixed_location_id,created_at,last_seen FROM devices "
            "ORDER BY last_seen DESC").fetchall()
        locs = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM locations")}
        out = []
        for d in devs:
            last = c.execute(
                "SELECT location_id FROM browser_detections WHERE device_token=? "
                "AND location_id IS NOT NULL ORDER BY id DESC LIMIT 1", (d["token"],)).fetchone()
            last_lid = last["location_id"] if last else None
            # The place this device is associated with: its pinned location if
            # fixed, otherwise where it was last heard.
            cur_lid = d["fixed_location_id"] if (d["mode"] == "fixed" and d["fixed_location_id"]) else last_lid
            out.append({
                "token": d["token"], "name": d["name"], "mode": d["mode"] or "adaptive",
                "fixed_location_id": d["fixed_location_id"],
                "fixed_location": locs.get(d["fixed_location_id"]),
                "location_id": cur_lid, "location": locs.get(cur_lid),
                "last_seen": d["last_seen"],
            })
    return out


def get_device(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as c:
        d = c.execute(
            "SELECT token,name,mode,fixed_location_id,created_at,last_seen FROM devices "
            "WHERE token=?", (token,)).fetchone()
        if not d:
            return None
        loc = None
        if d["fixed_location_id"]:
            lr = c.execute("SELECT name FROM locations WHERE id=?", (d["fixed_location_id"],)).fetchone()
            loc = lr["name"] if lr else None
    return {"token": d["token"], "name": d["name"], "mode": d["mode"] or "adaptive",
            "fixed_location_id": d["fixed_location_id"], "fixed_location": loc,
            "last_seen": d["last_seen"]}


def upsert_device(token: str, name: str | None = None, mode: str | None = None,
                  fixed_location_id: int | None = None) -> dict | None:
    """Create or update a device. Only non-None fields are written, so the
    settings form can patch one field at a time."""
    if not token:
        return None
    now = _now()
    with _conn() as c:
        exists = c.execute("SELECT 1 FROM devices WHERE token=?", (token,)).fetchone()
        if not exists:
            c.execute("INSERT INTO devices(token,name,mode,fixed_location_id,created_at,last_seen) "
                      "VALUES(?,?,?,?,?,?)",
                      (token, (name or "").strip() or "New device",
                       mode or "adaptive", fixed_location_id, now, now))
        else:
            sets, args = [], []
            if name is not None:
                sets.append("name=?"); args.append(name.strip())
            if mode is not None:
                sets.append("mode=?"); args.append(mode)
            if fixed_location_id is not None:
                sets.append("fixed_location_id=?"); args.append(fixed_location_id or None)
            sets.append("last_seen=?"); args.append(now)
            args.append(token)
            c.execute(f"UPDATE devices SET {','.join(sets)} WHERE token=?", args)
    return get_device(token)


def create_location(name: str, lat: float | None = None, lon: float | None = None,
                    address: str = "") -> dict:
    with _conn() as c:
        lid = c.execute("INSERT INTO locations(name,lat,lon,address,created_at) VALUES(?,?,?,?,?)",
                        (name.strip() or "New location", lat, lon, address or None, _now())).lastrowid
    return get_location(lid)


def update_location(location_id: int, name: str | None = None, lat: float | None = None,
                    lon: float | None = None, address: str | None = None) -> dict | None:
    with _conn() as c:
        if not c.execute("SELECT 1 FROM locations WHERE id=?", (location_id,)).fetchone():
            return None
        sets, args = [], []
        if name is not None:
            sets.append("name=?"); args.append(name.strip())
        if lat is not None:
            sets.append("lat=?"); args.append(lat)
        if lon is not None:
            sets.append("lon=?"); args.append(lon)
        if address is not None:
            sets.append("address=?"); args.append(address or None)
        if sets:
            args.append(location_id)
            c.execute(f"UPDATE locations SET {','.join(sets)} WHERE id=?", args)
        # If this location now sits on top of another (e.g. two devices' legacy
        # places both pinned to the same address), absorb the others into it so
        # there is one real place, not duplicates.
        me = c.execute("SELECT lat,lon,radius_m FROM locations WHERE id=?", (location_id,)).fetchone()
        if me and me["lat"] is not None and me["lon"] is not None:
            for other in c.execute(
                    "SELECT id,lat,lon,radius_m FROM locations "
                    "WHERE id<>? AND lat IS NOT NULL AND lon IS NOT NULL", (location_id,)).fetchall():
                d = _haversine_m(me["lat"], me["lon"], other["lat"], other["lon"])
                if d <= max(me["radius_m"] or 40, other["radius_m"] or 40):
                    c.execute("UPDATE browser_detections SET location_id=? WHERE location_id=?", (location_id, other["id"]))
                    c.execute("UPDATE devices SET fixed_location_id=? WHERE fixed_location_id=?", (location_id, other["id"]))
                    c.execute("DELETE FROM locations WHERE id=?", (other["id"],))
    return get_location(location_id)


def get_location(location_id: int) -> dict | None:
    with _conn() as c:
        L = c.execute("SELECT id,name,lat,lon,address,radius_m,created_at FROM locations "
                      "WHERE id=?", (location_id,)).fetchone()
        if not L:
            return None
        agg = c.execute(
            "SELECT COUNT(*) d, COUNT(DISTINCT scientific_name) s, MAX(ts) last "
            "FROM browser_detections WHERE location_id=?", (location_id,)).fetchone()
        devs = _location_devices(c, location_id)
    return {"id": L["id"], "name": L["name"], "lat": L["lat"], "lon": L["lon"],
            "address": L["address"], "radius_m": L["radius_m"],
            "detections": agg["d"] or 0, "species": agg["s"] or 0, "last_ts": agg["last"],
            "devices": devs}


def _location_devices(c: sqlite3.Connection, location_id: int) -> list[str]:
    seen = [r["device_name"] for r in c.execute(
        "SELECT DISTINCT device_name FROM browser_detections "
        "WHERE location_id=? AND device_name IS NOT NULL AND device_name<>''",
        (location_id,)).fetchall()]
    fixed = [r["name"] for r in c.execute(
        "SELECT name FROM devices WHERE fixed_location_id=?", (location_id,)).fetchall()]
    return sorted({*seen, *fixed})


def list_locations(include_nonbird: bool = False) -> dict:
    with _conn() as c:
        locs = c.execute("SELECT id,name,lat,lon,address,radius_m FROM locations").fetchall()
        out = []
        for L in locs:
            agg = c.execute(
                "SELECT COUNT(*) d, COUNT(DISTINCT scientific_name) s, MAX(ts) last "
                "FROM browser_detections WHERE location_id=?" + _bird_clause(include_nonbird),
                (L["id"],)).fetchone()
            out.append({
                "id": L["id"], "name": L["name"], "lat": L["lat"], "lon": L["lon"],
                "address": L["address"],
                "detections": agg["d"] or 0, "species": agg["s"] or 0, "last_ts": agg["last"],
                "devices": _location_devices(c, L["id"]),
            })
    out.sort(key=lambda x: (x["last_ts"] or ""), reverse=True)
    return {"locations": out, "home": get_setting("home_source")}


def _loc_rows(location_id: int, include_nonbird: bool = False) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT ts, scientific_name, common_name, confidence "
            "FROM browser_detections WHERE location_id=?" + _bird_clause(include_nonbird) +
            " ORDER BY ts", (location_id,)).fetchall()


def location_summary(location_id: int, include_nonbird: bool = False) -> dict:
    loc = get_location(location_id)
    rows = _loc_rows(location_id, include_nonbird)
    base = {"id": location_id, "name": loc["name"] if loc else None,
            "lat": loc["lat"] if loc else None, "lon": loc["lon"] if loc else None,
            "address": loc["address"] if loc else None,
            "devices": loc["devices"] if loc else []}
    if not rows:
        return {**base, "detections": 0, "species": 0, "first_date": None,
                "last_date": None, "today": 0, "active_days": 0, "last_detection": None}
    days = {_local(r["ts"]).strftime("%Y-%m-%d") for r in rows}
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    last = rows[-1]
    return {
        **base,
        "detections": len(rows),
        "species": len({r["scientific_name"] for r in rows}),
        "first_date": min(days), "last_date": max(days), "active_days": len(days),
        "today": sum(1 for r in rows if _local(r["ts"]).strftime("%Y-%m-%d") == today),
        "last_detection": {
            "common_name": last["common_name"], "scientific_name": last["scientific_name"],
            "confidence": round(last["confidence"], 4),
            "at": _local(last["ts"]).strftime("%Y-%m-%d %H:%M"),
        },
    }


def location_species(location_id: int, include_nonbird: bool = False) -> list[dict]:
    rows = _loc_rows(location_id, include_nonbird)
    return _aggregate_species(rows)


def location_activity(location_id: int, include_nonbird: bool = False) -> dict:
    rows = _loc_rows(location_id, include_nonbird)
    return _aggregate_activity(rows)


def _aggregate_species(rows: list[sqlite3.Row]) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in rows:
        sci = r["scientific_name"]
        a = agg.setdefault(sci, {
            "scientific_name": sci, "common_name": r["common_name"], "count": 0,
            "max_conf": 0.0, "_sum": 0.0, "_days": set(), "first_ts": r["ts"], "last_ts": r["ts"]})
        a["count"] += 1
        a["max_conf"] = max(a["max_conf"], r["confidence"])
        a["_sum"] += r["confidence"]
        a["_days"].add(_local(r["ts"]).strftime("%Y-%m-%d"))
        a["last_ts"] = r["ts"]
    out = []
    for a in agg.values():
        out.append({
            "scientific_name": a["scientific_name"], "common_name": a["common_name"],
            "count": a["count"], "max_conf": round(a["max_conf"], 4),
            "avg_conf": round(a["_sum"] / a["count"], 4), "days_seen": len(a["_days"]),
            "first_date": _local(a["first_ts"]).strftime("%Y-%m-%d"),
            "last_date": _local(a["last_ts"]).strftime("%Y-%m-%d"),
        })
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def _aggregate_activity(rows: list[sqlite3.Row]) -> dict:
    daily: dict[str, dict] = {}
    hourly = {h: 0 for h in range(24)}
    for r in rows:
        lt = _local(r["ts"])
        d = lt.strftime("%Y-%m-%d")
        day = daily.setdefault(d, {"date": d, "count": 0, "_species": set()})
        day["count"] += 1
        day["_species"].add(r["scientific_name"])
        hourly[lt.hour] += 1
    return {
        "daily": [{"date": d["date"], "count": d["count"], "species": len(d["_species"])}
                  for d in sorted(daily.values(), key=lambda x: x["date"])],
        "hourly": [{"hour": h, "count": hourly[h]} for h in range(24)],
    }


def bird_detail(sci: str, location_id: int | None = None,
                device: str | None = None, days: int = 0) -> dict:
    """Everything for one species' page: stats, per-day breakdown (each day with
    its own 24h histogram for the mini-graph), where + on what it was heard, and
    a recent list. Filterable by location, device, and date range; the location
    and device option lists are always the full set for the species."""
    q = ("SELECT ts, common_name, confidence, location_id, device_token, device_name "
         "FROM browser_detections WHERE scientific_name=?")
    params: list = [sci]
    if location_id:
        q += " AND location_id=?"
        params.append(location_id)
    if device:
        q += " AND device_token=?"
        params.append(device)
    with _conn() as c:
        rows = c.execute(q + " ORDER BY ts", params).fetchall()
        loc_names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM locations")}
        # The location/device option lists are always over ALL detections of the
        # species (so the filters always offer every place/device to switch to),
        # while the stats below respect the active filters.
        all_locs = c.execute(
            "SELECT location_id, COUNT(*) n FROM browser_detections "
            "WHERE scientific_name=? AND location_id IS NOT NULL GROUP BY location_id "
            "ORDER BY n DESC", (sci,)).fetchall()
        all_devs = c.execute(
            "SELECT device_token, MAX(device_name) name, COUNT(*) n FROM browser_detections "
            "WHERE scientific_name=? AND device_token IS NOT NULL AND device_token<>'' "
            "GROUP BY device_token ORDER BY n DESC", (sci,)).fetchall()

    # Date range: days<=0 is all-time; days=1 is today (local calendar day);
    # days=N is the last N local days.
    cutoff = None
    if days and days > 0:
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        cutoff = start.astimezone(timezone.utc)

    by_day: dict[str, dict] = {}
    hourly_overall = [0] * 24
    kept = []
    for r in rows:
        if cutoff is not None:
            dt = datetime.fromisoformat(r["ts"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < cutoff:
                continue
        kept.append(r)
        lt = _local(r["ts"])
        d = lt.strftime("%Y-%m-%d")
        day = by_day.setdefault(d, {"date": d, "count": 0, "hourly": [0] * 24})
        day["count"] += 1
        day["hourly"][lt.hour] += 1
        hourly_overall[lt.hour] += 1

    confs = [r["confidence"] for r in kept]
    common = (kept[-1]["common_name"] if kept else (rows[-1]["common_name"] if rows else sci))
    return {
        "scientific_name": sci, "common_name": common, "count": len(kept),
        "first_date": min(by_day) if by_day else None,
        "last_date": max(by_day) if by_day else None,
        "days_seen": len(by_day),
        "max_conf": round(max(confs), 4) if confs else 0,
        "avg_conf": round(sum(confs) / len(confs), 4) if confs else 0,
        "hourly": [{"hour": h, "count": hourly_overall[h]} for h in range(24)],
        "by_day": sorted(by_day.values(), key=lambda x: x["date"], reverse=True),
        "total_all": sum(L["n"] for L in all_locs),
        "locations": [{"id": L["location_id"], "name": loc_names.get(L["location_id"]), "count": L["n"]}
                      for L in all_locs],
        "devices": [{"token": D["device_token"], "name": D["name"], "count": D["n"]}
                    for D in all_devs],
        "recent": [{"at": _local(r["ts"]).strftime("%m-%d %H:%M:%S"),
                    "confidence": round(r["confidence"], 4),
                    "location_id": r["location_id"],
                    "location": loc_names.get(r["location_id"]),
                    "device": r["device_name"]} for r in kept[-50:][::-1]],
    }
