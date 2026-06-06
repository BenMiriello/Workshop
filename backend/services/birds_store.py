"""Read layer over the BirdNET-Go SQLite database, plus the privacy sanitizer.

BirdNET-Go owns the database (the Brain writes it); this module only ever opens
it READ-ONLY (sqlite uri mode=ro). The Workshop backend runs as the same user
(clawd-a) that BirdNET-Go runs as, so it can read the file with no perms juggling.

The `notes` table is BirdNET-Go's GORM model (snake_case columns):
  id, source_node, date, time, begin_time, end_time, species_code,
  scientific_name, common_name, confidence, latitude, longitude, threshold,
  sensitivity, clip_name, processing_time, unlikely

PRIVACY BOUNDARY (non-negotiable, brief's hard rule):
  build_public_dataset() is the ONLY thing the public path ever sees. It emits
  species + counts + day-granular timestamps + confidence summaries + first-seen
  flags. It NEVER emits latitude, longitude, clip_name, or per-detection rows.
  Sanitize at the source: the public deploy gets only this object's JSON, so a
  bug in the public UI is physically incapable of leaking audio or location -
  that data was never sent.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from services.birds_analyze import nonbird_exclusion_sql

DB_PATH = Path(os.environ.get("BIRDNET_DB", "/home/clawd-a/birdnet/data/birdnet.db"))
CLIPS_DIR = Path(os.environ.get("BIRDNET_CLIPS", "/home/clawd-a/birdnet/clips"))


def _nb(bird_only: bool, has_where: bool) -> str:
    """Clause that drops non-bird rows (Engine, Human, frogs, ...) when the app's
    non-bird filter is on. The yard DB has no is_bird column, so we exclude by
    label using the shared definition in birds_analyze. `has_where` picks AND vs
    WHERE so it can be spliced into either query shape."""
    if not bird_only:
        return ""
    return f" {'AND' if has_where else 'WHERE'} NOT {nonbird_exclusion_sql()}"


class DatabaseMissing(Exception):
    """Raised when the BirdNET-Go DB does not exist yet (pre-hardware bring-up)."""


def db_present() -> bool:
    return DB_PATH.exists()


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise DatabaseMissing(str(DB_PATH))
    # Read-only. We are a consumer; BirdNET-Go is the sole writer.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _today() -> str:
    # BirdNET-Go stores local-time date strings; the backend shares the box's
    # local timezone (Pacific), so a naive local "today" matches the DB.
    return datetime.now().strftime("%Y-%m-%d")


def _season(month: int) -> str:
    # Meteorological seasons (northern hemisphere).
    return {12: "winter", 1: "winter", 2: "winter",
            3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer",
            9: "fall", 10: "fall", 11: "fall"}[month]


# --------------------------------------------------------------------------
# Private reads (full fidelity; tailnet-only routes gate these)
# --------------------------------------------------------------------------

def summary(bird_only: bool = False) -> dict:
    with _connect() as c:
        row = c.execute(
            "SELECT COUNT(*) AS total, "
            "COUNT(DISTINCT scientific_name) AS species, "
            "MIN(date) AS first_date, MAX(date) AS last_date "
            "FROM notes" + _nb(bird_only, False)
        ).fetchone()
        today = _today()
        today_row = c.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT scientific_name) AS sp "
            "FROM notes WHERE date = ?" + _nb(bird_only, True), (today,)
        ).fetchone()
        last = c.execute(
            "SELECT date, time, common_name, scientific_name, round(confidence,4) AS confidence "
            "FROM notes" + _nb(bird_only, False) + " ORDER BY date DESC, time DESC LIMIT 1"
        ).fetchone()
        active = c.execute(
            "SELECT COUNT(DISTINCT date) AS d FROM notes" + _nb(bird_only, False)).fetchone()
    return {
        "total_detections": row["total"] or 0,
        "species_count": row["species"] or 0,
        "first_date": row["first_date"],
        "last_date": row["last_date"],
        "active_days": active["d"] or 0,
        "today": {"detections": today_row["n"] or 0, "species": today_row["sp"] or 0, "date": today},
        "last_detection": dict(last) if last else None,
    }


def species_list(bird_only: bool = False) -> list[dict]:
    with _connect() as c:
        rows = c.execute(
            "SELECT scientific_name, "
            "MAX(common_name) AS common_name, MAX(species_code) AS species_code, "
            "COUNT(*) AS count, MIN(date) AS first_date, MAX(date) AS last_date, "
            "MAX(confidence) AS max_conf, AVG(confidence) AS avg_conf "
            "FROM notes" + _nb(bird_only, False) + " GROUP BY scientific_name ORDER BY count DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["max_conf"] = round(d["max_conf"] or 0, 4)
        d["avg_conf"] = round(d["avg_conf"] or 0, 4)
        out.append(d)
    return out


def detections(limit: int = 100, species: str | None = None,
               date: str | None = None, min_conf: float = 0.0,
               bird_only: bool = False) -> list[dict]:
    """Full per-detection rows INCLUDING clip_name (for private audio playback).
    Never call this from a public path."""
    q = ("SELECT id, date, time, begin_time, common_name, scientific_name, "
         "species_code, round(confidence,4) AS confidence, clip_name, source_node "
         "FROM notes WHERE confidence >= ?")
    args: list = [min_conf]
    # A specific species filter already pins the row to one label, so the
    # non-bird filter only matters for the unfiltered/by-date listing.
    if bird_only and not species:
        q += _nb(True, True)
    if species:
        q += " AND scientific_name = ?"
        args.append(species)
    if date:
        q += " AND date = ?"
        args.append(date)
    q += " ORDER BY date DESC, time DESC LIMIT ?"
    args.append(max(1, min(limit, 1000)))
    with _connect() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def recent_arrivals(days: int = 7, bird_only: bool = False) -> list[dict]:
    """Species whose FIRST-EVER detection falls within the last `days`. This is
    the 'new to the yard' list the assistant narrates."""
    with _connect() as c:
        rows = c.execute(
            "SELECT scientific_name, MAX(common_name) AS common_name, "
            "MAX(species_code) AS species_code, MIN(date) AS first_date, "
            "COUNT(*) AS count, MAX(confidence) AS max_conf "
            "FROM notes" + _nb(bird_only, False) + " GROUP BY scientific_name "
            "HAVING MIN(date) >= date('now', ?) "
            "ORDER BY first_date DESC, count DESC",
            (f"-{int(days)} days",),
        ).fetchall()
    return [dict(r) | {"max_conf": round(r["max_conf"] or 0, 4)} for r in rows]


def daily_counts(days: int = 90, bird_only: bool = False) -> list[dict]:
    with _connect() as c:
        rows = c.execute(
            "SELECT date, COUNT(*) AS count, COUNT(DISTINCT scientific_name) AS species "
            "FROM notes WHERE date >= date('now', ?)" + _nb(bird_only, True) +
            " GROUP BY date ORDER BY date",
            (f"-{int(days)} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def hourly_activity(days: int = 30, bird_only: bool = False) -> list[dict]:
    """Diel activity: detections bucketed by hour-of-day across the window.
    `time` is an HH:MM:SS string, so substr(time,1,2) is the hour."""
    with _connect() as c:
        rows = c.execute(
            "SELECT CAST(substr(time,1,2) AS INTEGER) AS hour, COUNT(*) AS count "
            "FROM notes WHERE date >= date('now', ?)" + _nb(bird_only, True) +
            " GROUP BY hour ORDER BY hour",
            (f"-{int(days)} days",),
        ).fetchall()
    by_hour = {r["hour"]: r["count"] for r in rows}
    return [{"hour": h, "count": by_hour.get(h, 0)} for h in range(24)]


def timeseries(species: str, days: int = 90) -> list[dict]:
    with _connect() as c:
        rows = c.execute(
            "SELECT date, COUNT(*) AS count FROM notes "
            "WHERE scientific_name = ? AND date >= date('now', ?) "
            "GROUP BY date ORDER BY date",
            (species, f"-{int(days)} days"),
        ).fetchall()
    return [dict(r) for r in rows]


def first_seen_flags(scientific_name: str, date: str) -> dict:
    """For the event bridge: is this the first time we have ever seen this
    species, and is it the first detection of it today? Computed against the DB
    the webhook's own row is already written into, so 'first today' counts >=1."""
    with _connect() as c:
        ever = c.execute(
            "SELECT MIN(date) AS first_date, COUNT(*) AS n FROM notes WHERE scientific_name = ?",
            (scientific_name,),
        ).fetchone()
        today = c.execute(
            "SELECT COUNT(*) AS n FROM notes WHERE scientific_name = ? AND date = ?",
            (scientific_name, date),
        ).fetchone()
    return {
        "first_ever": (ever["first_date"] == date) and (ever["n"] is not None),
        "first_today": (today["n"] or 0) <= 1,
        "total_for_species": ever["n"] or 0,
    }


def resolve_clip(note_id: int) -> Path | None:
    """Map a detection id to its on-disk WAV under CLIPS_DIR. Returns None if the
    row, the clip_name, or the file is absent. Resolves traversal so a poisoned
    clip_name value cannot escape the clips dir."""
    with _connect() as c:
        row = c.execute("SELECT clip_name FROM notes WHERE id = ?", (note_id,)).fetchone()
    if not row or not row["clip_name"]:
        return None
    name = row["clip_name"]
    p = Path(name)
    if not p.is_absolute():
        # clip_name may be bare filename or relative ("clips/.../x.wav"); strip a
        # leading "clips/" so it doesn't double up under CLIPS_DIR.
        rel = p
        if rel.parts and rel.parts[0] == CLIPS_DIR.name:
            rel = Path(*rel.parts[1:])
        p = CLIPS_DIR / rel
    p = p.resolve()
    try:
        p.relative_to(CLIPS_DIR.resolve())
    except ValueError:
        return None
    return p if p.exists() else None


# --------------------------------------------------------------------------
# PUBLIC sanitizer - the only thing that crosses the privacy boundary
# --------------------------------------------------------------------------

def build_public_dataset(generated_at: str, node_label: str = "Backyard station") -> dict:
    """Scrubbed aggregates for the public Cloudflare page. Contains ONLY:
    species identity, counts (per day/week/month/season + total), confidence
    summaries, and first-seen dates. NO latitude/longitude, NO clip paths, NO
    per-detection rows, NO sub-day timestamps.

    `generated_at` is passed in (scripts cannot call Date.now()); the caller
    stamps it.
    """
    if not db_present():
        return {
            "generated_at": generated_at, "node": node_label, "available": False,
            "totals": {"detections": 0, "species": 0}, "species": [],
            "daily": [], "weekly": [], "monthly": [], "seasonal": [], "hourly": [],
        }

    with _connect() as c:
        totals = c.execute(
            "SELECT COUNT(*) AS d, COUNT(DISTINCT scientific_name) AS s, "
            "MIN(date) AS first_date, MAX(date) AS last_date FROM notes"
        ).fetchone()

        species = c.execute(
            "SELECT scientific_name, MAX(common_name) AS common_name, "
            "MAX(species_code) AS species_code, COUNT(*) AS count, "
            "MIN(date) AS first_seen, MAX(date) AS last_seen, "
            "round(MAX(confidence),4) AS max_conf, round(AVG(confidence),4) AS avg_conf "
            "FROM notes GROUP BY scientific_name ORDER BY count DESC"
        ).fetchall()

        daily = c.execute(
            "SELECT date, COUNT(*) AS count, COUNT(DISTINCT scientific_name) AS species "
            "FROM notes GROUP BY date ORDER BY date"
        ).fetchall()

        # Day-of-week-independent diel pattern; safe aggregate, no location.
        hourly = c.execute(
            "SELECT CAST(substr(time,1,2) AS INTEGER) AS hour, COUNT(*) AS count "
            "FROM notes GROUP BY hour ORDER BY hour"
        ).fetchall()

        # Monthly via the YYYY-MM prefix of the date string.
        monthly = c.execute(
            "SELECT substr(date,1,7) AS month, COUNT(*) AS count, "
            "COUNT(DISTINCT scientific_name) AS species "
            "FROM notes GROUP BY month ORDER BY month"
        ).fetchall()

    # Weekly + seasonal rolled up in Python from daily (keeps SQL portable).
    weekly: dict[str, dict] = {}
    seasonal: dict[str, dict] = {}
    for r in daily:
        try:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        iso_year, iso_week, _ = dt.isocalendar()
        wk = f"{iso_year}-W{iso_week:02d}"
        weekly.setdefault(wk, {"week": wk, "count": 0})["count"] += r["count"]
        sk = f"{dt.year if dt.month != 12 else dt.year + 1}-{_season(dt.month)}"
        seasonal.setdefault(sk, {"season": sk, "count": 0})["count"] += r["count"]

    return {
        "generated_at": generated_at,
        "node": node_label,
        "available": True,
        "range": {"first_date": totals["first_date"], "last_date": totals["last_date"]},
        "totals": {"detections": totals["d"] or 0, "species": totals["s"] or 0},
        "species": [dict(r) for r in species],
        "daily": [dict(r) for r in daily],
        "weekly": sorted(weekly.values(), key=lambda x: x["week"]),
        "monthly": [dict(r) for r in monthly],
        "seasonal": sorted(seasonal.values(), key=lambda x: x["season"]),
        "hourly": [dict(r) for r in hourly],
    }
