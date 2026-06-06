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
import os
import sqlite3
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BROWSER_DB = Path(os.environ.get("BIRDNET_BROWSER_DB", "/home/clawd-a/birdnet/data/browser-stations.db"))
ANALYZER_URL = os.environ.get("BIRDNET_ANALYZER_URL", "http://127.0.0.1:8012")

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
    c.execute("""CREATE TABLE IF NOT EXISTS stations (
        token TEXT PRIMARY KEY, label TEXT, created_at TEXT, last_seen TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bd_station_ts ON browser_detections(station, ts)")
    # Migrate older rows that predate the is_bird column.
    cols = {r["name"] for r in c.execute("PRAGMA table_info(browser_detections)")}
    if "is_bird" not in cols:
        # No DEFAULT: existing rows become NULL so the backfill below reclassifies
        # them (a plain DEFAULT 1 would silently mark old noise rows as birds).
        c.execute("ALTER TABLE browser_detections ADD COLUMN is_bird INTEGER")
    pending = c.execute(
        "SELECT id, scientific_name, common_name FROM browser_detections WHERE is_bird IS NULL"
    ).fetchall()
    if pending:
        c.executemany("UPDATE browser_detections SET is_bird=? WHERE id=?",
                      [(1 if is_bird(r["scientific_name"], r["common_name"]) else 0, r["id"])
                       for r in pending])
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


def analyze_chunk(audio: bytes, content_type: str, station: str,
                  token: str = "", min_conf: float = 0.25) -> dict:
    """Transcode -> analyze -> store. Returns this chunk's detections."""
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

    ts = datetime.now(timezone.utc).isoformat()
    if detections:
        with _conn() as c:
            c.executemany(
                "INSERT INTO browser_detections(station,ts,scientific_name,common_name,"
                "confidence,start_s,end_s,device_token,is_bird) VALUES(?,?,?,?,?,?,?,?,?)",
                [(station, ts, d["scientific_name"], d["common_name"], d["confidence"],
                  d.get("start_s", 0.0), d.get("end_s", 0.0), token, 1 if d["is_bird"] else 0)
                 for d in detections],
            )
    if token and station:
        register_station(token, station)
    return {"ts": ts, "detections": detections}


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
