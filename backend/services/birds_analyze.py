"""Browser mic-test bridge: take an audio chunk from the web app, run it through
the resident BirdNET analyzer service, store + return detections.

This is the "test without the always-on mic" path. Detections land in a SEPARATE
backend-owned SQLite (never the BirdNET-Go yard DB) under a named test station,
so test audio never merges into the yard species list and is never published.

The heavy ML runs in the isolated analyzer service (services/../birdnet/analyzer);
this module only does ffmpeg transcode + a localhost HTTP call + storage.
"""
from __future__ import annotations

import contextvars
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_minus(seconds: float) -> str:
    """UTC ISO timestamp `seconds` in the past — comparable (lexicographically) to
    stored `ts` values, which are also UTC ISO from _now()."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()

BROWSER_DB = Path(os.environ.get("BIRDNET_BROWSER_DB", "/home/clawd-a/birdnet/data/browser-stations.db"))
ANALYZER_URL = os.environ.get("BIRDNET_ANALYZER_URL", "http://127.0.0.1:8012")

# Saved-clip storage for the browser path (the always-on yard path keeps its own
# clips under BirdNET-Go). App-managed, tailnet-private, NEVER published.
CLIPS_DIR = Path(os.environ.get("BIRDNET_CLIPS_DIR", "/home/clawd-a/birdnet/data/clips"))
_MB = 1024 * 1024

# Soundscape Atlas: a 1024-d BirdNET feature embedding is captured for every
# STORED detection (the penultimate layer; encodes acoustic similarity so its
# clusters separate call types within a species). A SEPARATE, independent clip
# pool keeps a few audio exemplars per cluster so each cluster can be hand-labelled
# by ear - this runs on its own toggle, alongside or instead of the per-species
# recordings above. See docs/birds-atlas.md.
ATLAS_CLIPS_DIR = Path(os.environ.get("BIRDNET_ATLAS_CLIPS_DIR", "/home/clawd-a/birdnet/data/atlas-clips"))
EMBED_MODEL = "birdnet-global-6k-v2.4"
# The call-type vocabulary the operator can assign to a cluster. 'other' is the
# catch-all for a real call type that's none of the named ones. There is no
# 'unknown' label: an unlabelled cluster is simply unnamed, and an acoustic outlier
# is left ungrouped (cluster_id NULL) rather than bucketed into a fake group.
CALL_TYPES = ("song", "contact", "alarm", "flight", "begging", "other")
_ATLAS_DEFAULTS = {"atlas_clip_storage_mb": 256, "atlas_exemplars_per_cluster": 8}
# A detection farther than this (cosine distance) from every labelled centroid is
# tagged 'unknown' rather than forced into the nearest cluster. Tunable per-install.
_ATLAS_UNKNOWN_DIST_DEFAULT = 0.18
# ~3s MP3 @ 64k mono. Used only for "is there room for one more?" banner checks.
_AVG_CLIP_BYTES = 24 * 1024
_REC_DEFAULTS = {"rec_storage_mb": 1024, "rec_when_full": "evict_old", "rec_warn_ratio": 0.9}
# Global save behaviour: do we keep the clip of a (bird) detection by default?
# The mic always analyzes; this is purely about KEEPING audio. Off by default -
# storage is finite. Per-species 'keep'/'never' override this (see save_mode).
_REC_SAVE_DEFAULT = False

# Same species at the same location within this many seconds is treated as one
# continuing detection (the live UI sends overlapping 3s windows every 1s).
DEDUP_S = 2.5

# --- Smart-detection defaults (overridable per-install via app_settings) ------
# These are the knobs that stop BirdNET reporting birds it never heard. See
# docs/birdnet-accuracy.md for the why and the sources.
#   min_conf   acoustic confidence floor. BirdNET-Pi ships 0.7, BirdNET-Go 0.8;
#              0.5 is a sane lower bound for a noisy phone mic. NOT calibrated to
#              probability - it is a precision/recall knob.
#   sf_thresh  range-filter cutoff: a species must clear this eBird occurrence
#              frequency for its place+week or it is impossible-here and dropped.
#              Kept LOW so genuinely-rare local visitors survive (the classic
#              trap is setting it high and losing owls).
#   new_species_hits  a never-before-confirmed species must be heard this many
#              times within CONFIRM_WINDOW_S before it is trusted/stored. Defeats
#              one-off spurious hits (BirdNET-Go "deep detection" idea, adapted
#              to the 1s live cadence). Known residents store immediately.
#   prior_floor  display-only: hide species whose occurrence is below this.
_SMART_DEFAULTS = {
    "min_conf": 0.5,
    "sensitivity": 1.0,
    "sf_thresh": 0.01,
    "new_species_hits": 3,
    "prior_floor": 0.0,
    # Trust radius (km): a manual confirmation auto-confirms the same species
    # within this distance (the "patch" size). Generous by default; it's a labor
    # knob, not a correctness one (suggested-by-default handles correctness).
    "trust_radius_km": 5.0,
}
CONFIRM_WINDOW_S = 15.0
# How long a species stays flagged "New" at a location: 24h from its first
# detection there. Shared (data-derived, not per-person) and self-expiring.
NEW_WINDOW_S = 24 * 3600

# Rolling in-process buffer for new-species consensus: (location_id, sci) ->
# [recent detection ts]. The backend is a single uvicorn worker, so module state
# persists across requests; it resets on restart (acceptable - confirmation just
# re-warms). Pruned to CONFIRM_WINDOW_S on each touch.
_recent_hits: dict = {}


def smart_settings() -> dict:
    """Effective detection settings: app_settings overrides over the defaults."""
    s = dict(_SMART_DEFAULTS)
    for k in s:
        v = get_setting(f"smart_{k}")
        if v is None:
            continue
        try:
            s[k] = type(_SMART_DEFAULTS[k])(v)
        except (TypeError, ValueError):
            pass
    return s


_SMART_BOUNDS = {
    "min_conf": (0.05, 0.99), "sensitivity": (0.75, 1.25),
    "sf_thresh": (0.0001, 0.5), "new_species_hits": (1, 10),
    "trust_radius_km": (0.1, 100.0),
    "prior_floor": (0.0, 1.0),
}


def update_smart_settings(patch: dict) -> dict:
    """Validate + persist detection-tuning overrides; returns the effective set."""
    for k, raw in (patch or {}).items():
        if k not in _SMART_DEFAULTS or raw is None:
            continue
        try:
            v = type(_SMART_DEFAULTS[k])(raw)
        except (TypeError, ValueError):
            continue
        lo, hi = _SMART_BOUNDS[k]
        v = max(lo, min(hi, v))
        set_setting(f"smart_{k}", str(v))
    return smart_settings()

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
    """SQL fragment appended to a WHERE that already has a condition. Applies the
    display filters every read shares:
      - non-birds hidden unless include_nonbird (existing behavior);
      - out-of-range birds hidden (in_range=0, set by a retroactive range
        re-score; NULL = never scored against a place, left visible);
      - birds below the live confidence floor hidden. The floor is a SETTING, so
        it applies retroactively AND updates instantly when changed - a detection
        stored earlier at a lower floor disappears as soon as you raise it, no
        recompute needed. New detections are already gated at store time.
    The confidence floor never touches non-birds (they keep their own toggle)."""
    base = "" if include_nonbird else " AND is_bird=1"
    floor = float(smart_settings().get("min_conf", 0.0))
    return (base + " AND (in_range IS NULL OR in_range=1)"
            + f" AND (is_bird=0 OR confidence >= {floor})")


def _status_clause(status: str | None) -> str:
    """Filter by trust status. Default (None) = confirmed only, the clean view.
    'all' = no status filter; explicit suggested/rejected for those tabs."""
    if status == "all":
        return ""
    if status == "_visible":
        # Everything actually heard (not a dismissed false positive). Used by the
        # charts/stats, which are NOT subject to the confirmed/suggested tab filter.
        return " AND status!='rejected'"
    s = status or "confirmed"
    if s not in ("confirmed", "suggested", "rejected"):
        s = "confirmed"
    return f" AND status='{s}'"


def nonbird_exclusion_sql(sci: str = "scientific_name", common: str = "common_name") -> str:
    """SQL boolean that is TRUE when a row is a NON-bird, mirroring is_bird().
    For DBs without an is_bird column (the yard BirdNET-Go DB is read-only and
    owned by BirdNET-Go), so non-birds can be filtered by label. The genera are
    hardcoded constants (plain alpha), so inlining them as literals is safe."""
    genera = ",".join(f"'{g}'" for g in sorted(_NON_AVIAN_GENERA))
    return (f"({sci} = {common} OR "
            f"substr({sci},1,instr({sci}||' ',' ')-1) IN ({genera}))")


_migrated = False  # schema migration runs once per process, not every connect


def _conn() -> sqlite3.Connection:
    global _migrated
    BROWSER_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(BROWSER_DB, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    if _migrated:
        return c  # fast path: no per-connect DDL/writes (and no lock risk)
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
    # --- Accounts + friend model (Springer Birds sharing) -------------------
    # A user is a person with an account. role: admin (the operator, issues
    # invites out-of-app) | member. In the private Workshop app there is exactly
    # one user (the operator, id 1, backfilled below) and everything is theirs;
    # the standalone app adds the second person. Devices + detections carry an
    # owner_user_id so "whose bird is this" is answerable, and a device's `shared`
    # flag opts its detections into friend visibility.
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE, name TEXT, role TEXT DEFAULT 'member', created_at TEXT,
        password_hash TEXT)""")
    # Standalone-app login sessions (the private Workshop app does not use these;
    # it is tailnet-gated). token is a 256-bit secret; expires_at bounds it. The
    # cookie is HttpOnly+Secure+SameSite=Strict. password_hash on users is a
    # stdlib scrypt digest ("scrypt$n$r$p$salt_hex$hash_hex"); no external dep.
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT, expires_at TEXT,
        user_agent TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    # One-time invite tokens (admin-minted, out of app). Redeeming one creates the
    # member's account (by email) and lets them set their own password — no admin
    # ever handles their credential. used_at marks it consumed.
    c.execute("""CREATE TABLE IF NOT EXISTS invites (
        token TEXT PRIMARY KEY, email TEXT, name TEXT,
        created_at TEXT, expires_at TEXT, used_at TEXT)""")
    # Live broadcast: a shared device, WHILE listening, pushes its rolling
    # spectrogram columns + "now hearing" here (one row per device, overwritten).
    # Viewers who can see the device poll it to watch in near-real-time. Stored in
    # the shared DB (not process memory) so it works across the tailnet app and the
    # standalone app. Rows older than LIVE_TTL_S are treated as offline.
    c.execute("""CREATE TABLE IF NOT EXISTS live_feed (
        device_token TEXT PRIMARY KEY, ts TEXT, location_id INTEGER,
        owner_user_id INTEGER, device_name TEXT, payload TEXT)""")
    # Friendship is MUTUAL and symmetric. We store one row per pair with the
    # smaller id as user_a (canonical), so a pair can't be duplicated. status:
    # accepted = both see each other's SHARED detections; pending = requested,
    # not yet visible. The operator can wire requests/accepts in the standalone
    # app; admin can also just create accepted rows directly for the two trusted
    # users. Visibility is symmetric: there is no "follower" direction.
    c.execute("""CREATE TABLE IF NOT EXISTS friendships (
        user_a INTEGER, user_b INTEGER, status TEXT DEFAULT 'accepted',
        created_at TEXT, PRIMARY KEY (user_a, user_b))""")
    # Trust anchors for the suggested/confirmed model. One row per (species, point
    # you acted at). trust: none = just tracked (a suggestion has appeared here);
    # auto = auto-confirm within radius_m; mute = auto-reject within radius_m.
    # reviewed: has the user acknowledged this species' first appearance here
    # (tapped in or decided) - drives the "new arrival" badge + top-pin.
    # auto_locked: user explicitly turned auto-confirm off, so a later manual
    # confirm must NOT silently re-arm it (the "only the first time" rule).
    c.execute("""CREATE TABLE IF NOT EXISTS species_area (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scientific_name TEXT, common_name TEXT,
        lat REAL, lon REAL, radius_m REAL,
        trust TEXT DEFAULT 'none', reviewed INTEGER DEFAULT 0,
        auto_locked INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sa_sci ON species_area(scientific_name)")

    # Saved audio clips. One row per recording, one file on disk under CLIPS_DIR.
    # det_id links back to the detection it was captured from (informational).
    # pinned = "save this recording": protected from auto-eviction.
    c.execute("""CREATE TABLE IF NOT EXISTS recordings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        det_id INTEGER, scientific_name TEXT, common_name TEXT,
        location_id INTEGER, device_token TEXT, ts TEXT,
        path TEXT, bytes INTEGER, duration_s REAL, confidence REAL,
        pinned INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rec_sci ON recordings(scientific_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_rec_ts ON recordings(ts)")
    # Per-species save preference (keyed by scientific_name), as one tri-state:
    #   default = follow the global "save recordings" setting
    #   keep    = always save this bird's clips AND never auto-evict them
    #   never   = never save this bird's clips, even when global save is on
    # The mic always analyzes; this only controls whether the AUDIO is kept.
    c.execute("""CREATE TABLE IF NOT EXISTS species_recording (
        scientific_name TEXT PRIMARY KEY,
        save_mode TEXT DEFAULT 'default')""")
    # Migrate the earlier two-flag schema (record/keep_all) -> save_mode.
    sr_cols = {r["name"] for r in c.execute("PRAGMA table_info(species_recording)")}
    if "save_mode" not in sr_cols:
        c.execute("ALTER TABLE species_recording ADD COLUMN save_mode TEXT DEFAULT 'default'")
    if "keep_all" in sr_cols:  # collapse old flags into the tri-state, then drop them
        c.execute("UPDATE species_recording SET save_mode="
                  "CASE WHEN keep_all=1 THEN 'keep' WHEN record=1 THEN 'default' ELSE 'never' END "
                  "WHERE save_mode IS NULL OR save_mode='default'")
        # SQLite can't DROP COLUMN on old versions; leaving the dead columns is harmless.

    # Soundscape Atlas tables ------------------------------------------------
    # One 1024-d feature embedding per stored detection (the durable acoustic
    # fingerprint - we keep this, not the audio). cluster_id/call_type/ct_dist are
    # the call-type tag: set live by nearest-centroid, then made authoritative by
    # the batch cluster build (services/../analyzer/atlas_build.py).
    c.execute("""CREATE TABLE IF NOT EXISTS detection_embedding (
        det_id INTEGER PRIMARY KEY,
        vec BLOB, dim INTEGER, model TEXT,
        cluster_id INTEGER, call_type TEXT, ct_dist REAL,
        embedded_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_de_cluster ON detection_embedding(cluster_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_de_calltype ON detection_embedding(call_type)")
    # A cluster of acoustically-similar detections. scope='species' clusters are
    # the call-type groups (one HDBSCAN run per species); scope='global' are the
    # overview-map blobs (mostly one per species). label is the hand-assigned
    # call type; centroid (mean embedding) drives live nearest-centroid tagging
    # and carrying a label forward across rebuilds.
    c.execute("""CREATE TABLE IF NOT EXISTS atlas_cluster (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT, species TEXT, hdbscan_label INTEGER,
        label TEXT, size INTEGER, centroid BLOB, color TEXT,
        created_at TEXT, updated_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ac_scope ON atlas_cluster(scope, species)")
    # 2D/3D projection coordinates for the viz, one row per (detection, scope).
    # Written by the batch build only (live tagging can't project a single point).
    c.execute("""CREATE TABLE IF NOT EXISTS atlas_point (
        det_id INTEGER, scope TEXT,
        x REAL, y REAL, z REAL, cluster_id INTEGER,
        PRIMARY KEY (det_id, scope))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ap_scope ON atlas_point(scope, cluster_id)")
    # Independent exemplar-clip pool: a few playable clips per cluster so each can
    # be labelled by ear. Own toggle (atlas_clip_save) + own budget, separate from
    # the recordings table. Collected FIFO live, pruned to per-cluster exemplars by
    # the batch build (exemplar=1 protected, the rest evictable).
    c.execute("""CREATE TABLE IF NOT EXISTS atlas_clip (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        det_id INTEGER, scientific_name TEXT, cluster_id INTEGER,
        ts TEXT, path TEXT, bytes INTEGER, confidence REAL,
        exemplar INTEGER DEFAULT 0)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_atc_sci ON atlas_clip(scientific_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_atc_cluster ON atlas_clip(cluster_id)")

    # Devices gained a user_agent so a device can be identified ("Chrome on
    # macOS") beyond its name - answers "what/whose device is this".
    dev_cols = {r["name"] for r in c.execute("PRAGMA table_info(devices)")}
    if "user_agent" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN user_agent TEXT")
    # Removal is a SOFT archive, never a hard delete: deleted_at flags a device or
    # location as removed-from-lists while its row AND all its detections persist
    # indefinitely (no time-based purge anywhere), so it can be restored later.
    if "deleted_at" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN deleted_at TEXT")
    # Ownership + sharing. owner_user_id: who this device belongs to (NULL until
    # an account claims it; backfilled to the operator below). shared: 0 = private
    # to its owner, 1 = its detections are visible to the owner's accepted friends.
    # The always-on Pi is just a device with shared=1 - no special source.
    if "owner_user_id" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN owner_user_id INTEGER")
    if "shared" not in dev_cols:
        c.execute("ALTER TABLE devices ADD COLUMN shared INTEGER DEFAULT 0")
    # password_hash arrived after the users table (standalone login); add it to
    # pre-existing users tables.
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
        user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "password_hash" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    loc_cols = {r["name"] for r in c.execute("PRAGMA table_info(locations)")}
    if "deleted_at" not in loc_cols:
        c.execute("ALTER TABLE locations ADD COLUMN deleted_at TEXT")

    cols = {r["name"] for r in c.execute("PRAGMA table_info(browser_detections)")}
    # is_bird: no DEFAULT so existing rows become NULL and the backfill below
    # reclassifies them (a plain DEFAULT 1 would mark old noise rows as birds).
    if "is_bird" not in cols:
        c.execute("ALTER TABLE browser_detections ADD COLUMN is_bird INTEGER")
    for col, ddl in (("device_name", "TEXT"), ("location_id", "INTEGER"),
                     ("lat", "REAL"), ("lon", "REAL"),
                     # Range-model outputs + consensus state. occurrence = eBird
                     # likelihood here/this-week (0..1, NULL if location unknown
                     # at detection time); in_range = passed the range filter;
                     # confirmed defaults 1 so pre-existing history still counts.
                     ("occurrence", "REAL"), ("in_range", "INTEGER"),
                     ("confirmed", "INTEGER DEFAULT 1"),
                     # Trust model: status = suggested|confirmed|rejected (no
                     # DEFAULT so existing rows backfill to 'suggested' below);
                     # auto = was this status set automatically (anchor) vs by hand.
                     ("status", "TEXT"), ("auto", "INTEGER DEFAULT 0"),
                     # owner_user_id: denormalized from the device at insert time
                     # so visibility filtering is a single join-free predicate.
                     # NULL = legacy; backfilled to the operator below.
                     ("owner_user_id", "INTEGER"),
                     # clip_name: reference to the WAV on the always-on capture
                     # node (BirdNET-Go writes the file; the webhook sends its
                     # name). The browser path keeps audio in the recordings
                     # table instead, so this is NULL for browser detections.
                     ("clip_name", "TEXT")):
        if col not in cols:
            c.execute(f"ALTER TABLE browser_detections ADD COLUMN {col} {ddl}")
    # Existing detections predate the trust model: surface them as suggestions so
    # the operator curates from scratch (nothing is auto-trusted).
    c.execute("UPDATE browser_detections SET status='suggested' WHERE status IS NULL")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bd_status ON browser_detections(status)")

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

    # One-time: seed a tracking anchor per existing suggested species so the
    # pre-trust-model backlog surfaces as new arrivals to review (the operator
    # curates from scratch). Read the radius from this same connection to avoid
    # re-entering _conn() during migration. Only runs when species_area is empty.
    if not c.execute("SELECT 1 FROM species_area LIMIT 1").fetchone():
        rv = c.execute("SELECT value FROM app_settings WHERE key='smart_trust_radius_km'").fetchone()
        try:
            radius_m = float(rv["value"]) * 1000.0 if rv else 5000.0
        except (TypeError, ValueError):
            radius_m = 5000.0
        now = _now()
        seen: set = set()
        for r in c.execute(
                "SELECT bd.scientific_name, bd.common_name, "
                "COALESCE(bd.lat, loc.lat) AS lat, COALESCE(bd.lon, loc.lon) AS lon "
                "FROM browser_detections bd LEFT JOIN locations loc ON loc.id=bd.location_id "
                "WHERE bd.is_bird=1 AND bd.status='suggested' "
                "AND (bd.in_range IS NULL OR bd.in_range=1) "
                "ORDER BY bd.id DESC").fetchall():
            if r["scientific_name"] in seen or r["lat"] is None or r["lon"] is None:
                continue
            seen.add(r["scientific_name"])
            c.execute(
                "INSERT INTO species_area(scientific_name,common_name,lat,lon,radius_m,"
                "trust,reviewed,auto_locked,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'none',0,0,?,?)",
                (r["scientific_name"], r["common_name"], r["lat"], r["lon"], radius_m, now, now))

    # Seed the operator account (id 1, admin) and backfill ownership of all
    # pre-account devices + detections to it - this history is the operator's.
    # Idempotent: only runs while the users table is empty. The operator's email
    # comes from the environment so it is not hardcoded in source.
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        op_email = os.environ.get("SPRINGER_ADMIN_EMAIL", "benmiriello@gmail.com")
        op_name = os.environ.get("SPRINGER_ADMIN_NAME", "Ben")
        c.execute("INSERT INTO users(id,email,name,role,created_at) VALUES(1,?,?,'admin',?)",
                  (op_email, op_name, _now()))
        c.execute("UPDATE devices SET owner_user_id=1 WHERE owner_user_id IS NULL")
        c.execute("UPDATE browser_detections SET owner_user_id=1 WHERE owner_user_id IS NULL")
    c.commit()
    _migrated = True
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


def _week_48(dt: datetime) -> int:
    """BirdNET range model wants week-of-year on a 48-week (4-per-month) scale."""
    return max(1, min(48, (dt.month - 1) * 4 + min(3, (dt.day - 1) // 7) + 1))


def _call_analyzer(wav_path: str, min_conf: float, lat: float | None = None,
                   lon: float | None = None, sensitivity: float = 1.0,
                   sf_thresh: float = 0.01) -> list[dict]:
    payload = {"path": wav_path, "min_conf": min_conf,
               "sensitivity": sensitivity, "sf_thresh": sf_thresh}
    if lat is not None and lon is not None:
        payload.update(lat=lat, lon=lon,
                       week=_week_48(datetime.now().astimezone()))
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{ANALYZER_URL}/analyze", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("detections", [])


def _call_embed(wav_path: str) -> list[float] | None:
    """Ask the analyzer for the 1024-d feature embedding of this chunk. Our chunks
    are 3s so there is one vector; if several windows come back we take the first.
    Returns None on any failure - embedding is an enrichment, never blocks storage."""
    try:
        body = json.dumps({"path": wav_path}).encode()
        req = urllib.request.Request(f"{ANALYZER_URL}/embed", data=body,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            embs = json.loads(r.read()).get("embeddings", [])
        return embs[0]["vec"] if embs else None
    except Exception:  # noqa: BLE001
        return None


def _pack_vec(vec) -> bytes:
    """Pack a float vector to raw little-endian float32 bytes (no numpy in the
    lean backend venv). _unpack_vec is the inverse; the batch job reads these."""
    import array
    a = array.array("f", vec)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _unpack_vec(blob: bytes) -> list[float]:
    import array
    a = array.array("f")
    a.frombytes(blob)
    if sys.byteorder == "big":
        a.byteswap()
    return list(a)


def _effective_coords(c: sqlite3.Connection, token: str, mode: str,
                      lat: float | None, lon: float | None,
                      location_id: int | None) -> tuple[float | None, float | None]:
    """Coordinates to feed the range filter, resolved BEFORE analysis and
    WITHOUT creating anything. Client GPS wins; otherwise fall back to the
    pinned/explicit location, then the device's last known location. Returns
    (None, None) when we truly have no place - the range filter then stays off
    and detections come back range-unknown (never wrongly dropped)."""
    if lat is not None and lon is not None:
        return lat, lon
    lid = location_id
    if lid is None and mode == "fixed" and token:
        dev = c.execute("SELECT fixed_location_id FROM devices WHERE token=?",
                        (token,)).fetchone()
        lid = dev["fixed_location_id"] if dev else None
    if lid is None and token:
        last = c.execute(
            "SELECT location_id FROM browser_detections WHERE device_token=? "
            "AND location_id IS NOT NULL ORDER BY id DESC LIMIT 1", (token,)).fetchone()
        lid = last["location_id"] if last else None
    if lid is not None:
        row = c.execute("SELECT lat, lon FROM locations WHERE id=?", (lid,)).fetchone()
        if row and row["lat"] is not None and row["lon"] is not None:
            return row["lat"], row["lon"]
    return None, None


def _occurrence_map_remote(lat: float, lon: float, week: int) -> dict:
    """Fetch the eBird occurrence map for a place/week from the analyzer (which
    owns the range model). Used to re-score already-stored detections."""
    url = f"{ANALYZER_URL}/species?lat={lat}&lon={lon}&week={week}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read()).get("occurrence", {})


def recompute_history(sf_thresh: float | None = None) -> dict:
    """Apply the range model to already-stored bird detections. We can't re-run
    audio (clips aren't kept for the browser path), but each detection carries
    its place + time, so we look up the species' eBird occurrence there/then and
    set occurrence + in_range. Birds the model says can't be here get in_range=0,
    which _bird_clause hides everywhere - so the over-detected impossible species
    drop out of every stat/list/chart. Reversible: nothing is deleted, and a
    later recompute (or a Lenient threshold) can bring a row back. Non-birds and
    detections with no resolvable coordinates are left untouched."""
    from collections import defaultdict
    cfg = smart_settings()
    sf = cfg["sf_thresh"] if sf_thresh is None else sf_thresh
    updated = 0
    flagged = 0
    species_hidden: set = set()
    with _conn() as c:
        rows = c.execute(
            "SELECT bd.id, bd.scientific_name, bd.common_name, bd.ts, "
            "COALESCE(bd.lat, loc.lat) AS lat, COALESCE(bd.lon, loc.lon) AS lon "
            "FROM browser_detections bd LEFT JOIN locations loc ON loc.id = bd.location_id "
            "WHERE bd.is_bird = 1").fetchall()
        groups: dict = defaultdict(list)
        skipped_no_coords = 0
        for r in rows:
            if r["lat"] is None or r["lon"] is None:
                skipped_no_coords += 1
                continue
            wk = _week_48(_local(r["ts"]))
            groups[(round(r["lat"], 2), round(r["lon"], 2), wk)].append(r)

        for (lat, lon, wk), grp in groups.items():
            occ = _occurrence_map_remote(lat, lon, wk)
            for r in grp:
                label = f'{r["scientific_name"]}_{r["common_name"]}'
                o = float(occ.get(label, 0.0))
                in_range = 1 if o >= sf else 0
                c.execute("UPDATE browser_detections SET occurrence=?, in_range=? WHERE id=?",
                          (round(o, 4), in_range, r["id"]))
                updated += 1
                if not in_range:
                    flagged += 1
                    species_hidden.add(r["common_name"])
    return {"rescored": updated, "hidden": flagged,
            "skipped_no_coords": skipped_no_coords,
            "species_hidden": sorted(species_hidden)}


# --- Trust model: suggested / confirmed / rejected --------------------------
# A species you have never vouched for near a point is "suggested"; confirming it
# once arms an auto-confirm anchor that trusts it within trust_radius. See
# docs/birdnet-accuracy.md. Correctness comes from suggested-by-default (a never-
# confirmed species never enters the Confirmed view); the radius only controls
# how much re-approval real birds need.

def trust_radius_m() -> float:
    return float(smart_settings().get("trust_radius_km", 5.0)) * 1000.0


def _radius_m(c: sqlite3.Connection) -> float:
    """Trust radius read via the ACTIVE connection (no nested _conn -> no lock)."""
    row = c.execute("SELECT value FROM app_settings WHERE key='smart_trust_radius_km'").fetchone()
    try:
        return float(row["value"]) * 1000.0 if row and row["value"] is not None else 5000.0
    except (TypeError, ValueError):
        return 5000.0


def _covering(c: sqlite3.Connection, sci: str, lat: float, lon: float) -> list[sqlite3.Row]:
    """species_area anchors for this species whose radius covers the point."""
    if lat is None or lon is None:
        return []
    out = []
    for r in c.execute("SELECT * FROM species_area WHERE scientific_name=?", (sci,)):
        if r["lat"] is None or r["lon"] is None:
            continue
        if _haversine_m(lat, lon, r["lat"], r["lon"]) <= (r["radius_m"] or _radius_m(c)):
            out.append(r)
    return out


def _ensure_area(c: sqlite3.Connection, sci: str, common: str, lat: float, lon: float,
                 trust: str = "none") -> sqlite3.Row | None:
    """Return the anchor covering this point (any trust), creating a tracking one
    if none exists. Used so a first suggestion is tracked as a new arrival."""
    cov = _covering(c, sci, lat, lon)
    if cov:
        return cov[0]
    if lat is None or lon is None:
        return None
    now = _now()
    cur = c.execute(
        "INSERT INTO species_area(scientific_name,common_name,lat,lon,radius_m,trust,"
        "reviewed,auto_locked,created_at,updated_at) VALUES(?,?,?,?,?,?,0,0,?,?)",
        (sci, common, lat, lon, _radius_m(c), trust, now, now))
    return c.execute("SELECT * FROM species_area WHERE id=?", (cur.lastrowid,)).fetchone()


def _classify(c: sqlite3.Connection, sci: str, common: str,
              lat: float | None, lon: float | None) -> tuple[str, int]:
    """(status, auto) for a fresh detection, by its trust anchors."""
    if lat is None or lon is None:
        return "suggested", 0
    cov = _covering(c, sci, lat, lon)
    if any(r["trust"] == "mute" for r in cov):
        return "rejected", 1
    if any(r["trust"] == "auto" for r in cov):
        return "confirmed", 1
    if not cov:
        _ensure_area(c, sci, common, lat, lon, trust="none")
    return "suggested", 0


def _det_point(c: sqlite3.Connection, det_id: int) -> sqlite3.Row | None:
    return c.execute(
        "SELECT bd.id, bd.scientific_name, bd.common_name, "
        "COALESCE(bd.lat, loc.lat) AS lat, COALESCE(bd.lon, loc.lon) AS lon "
        "FROM browser_detections bd LEFT JOIN locations loc ON loc.id=bd.location_id "
        "WHERE bd.id=?", (det_id,)).fetchone()


def _retro(c: sqlite3.Connection, sci: str, lat: float, lon: float,
           from_status: str, to_status: str, auto: int) -> int:
    """Move this species' nearby detections (within trust radius of the point)
    from one status to another. Used so confirming/muting once sweeps the backlog."""
    if lat is None or lon is None:
        return 0
    R = _radius_m(c)
    n = 0
    rows = c.execute(
        "SELECT bd.id, COALESCE(bd.lat, loc.lat) AS lat, COALESCE(bd.lon, loc.lon) AS lon "
        "FROM browser_detections bd LEFT JOIN locations loc ON loc.id=bd.location_id "
        "WHERE bd.scientific_name=? AND bd.status=?", (sci, from_status)).fetchall()
    for r in rows:
        if r["lat"] is None or r["lon"] is None:
            continue
        if _haversine_m(lat, lon, r["lat"], r["lon"]) <= R:
            c.execute("UPDATE browser_detections SET status=?, auto=? WHERE id=?",
                      (to_status, auto, r["id"]))
            n += 1
    return n


def _touch_area(c: sqlite3.Connection, sci: str, common: str, lat: float, lon: float,
                **fields) -> None:
    """Set fields on the anchor covering the point (creating one if needed)."""
    row = _ensure_area(c, sci, common, lat, lon)
    if not row:
        return
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
        c.execute(f"UPDATE species_area SET {sets} WHERE id=?",
                  (*fields.values(), _now(), row["id"]))


def confirm_detections(ids: list[int]) -> dict:
    """Confirm events. The first confirm of a species in an area arms auto-confirm
    (unless the user previously locked it off) and sweeps that species' nearby
    suggestions to confirmed."""
    confirmed = 0
    swept = 0
    with _conn() as c:
        for det_id in ids:
            p = _det_point(c, det_id)
            if not p:
                continue
            c.execute("UPDATE browser_detections SET status='confirmed', auto=0 WHERE id=?", (det_id,))
            confirmed += 1
            sci, common, lat, lon = p["scientific_name"], p["common_name"], p["lat"], p["lon"]
            cov = _covering(c, sci, lat, lon)
            locked = any(r["auto_locked"] for r in cov)
            armed = any(r["trust"] == "auto" for r in cov)
            # Mark reviewed regardless (a decision is an acknowledgement).
            _touch_area(c, sci, common, lat, lon, reviewed=1)
            if not armed and not locked:
                _touch_area(c, sci, common, lat, lon, trust="auto", reviewed=1)
                swept += _retro(c, sci, lat, lon, "suggested", "confirmed", 1)
    return {"confirmed": confirmed, "also_confirmed_nearby": swept}


def reject_detections(ids: list[int]) -> dict:
    """Reject specific events. Does not change the species' trust."""
    with _conn() as c:
        n = 0
        for det_id in ids:
            p = _det_point(c, det_id)
            c.execute("UPDATE browser_detections SET status='rejected', auto=0 WHERE id=?", (det_id,))
            if p:
                _touch_area(c, p["scientific_name"], p["common_name"], p["lat"], p["lon"], reviewed=1)
            n += 1
    return {"rejected": n}


def set_detection_status(det_id: int, status: str) -> dict:
    if status not in ("suggested", "confirmed", "rejected"):
        return {"error": "bad status"}
    if status == "confirmed":
        return confirm_detections([det_id])
    with _conn() as c:
        c.execute("UPDATE browser_detections SET status=?, auto=0 WHERE id=?", (status, det_id))
    return {"status": status}


def mute_species(sci: str, lat: float, lon: float, common: str = "") -> dict:
    """Stop suggesting this species near here, and reject its nearby suggestions."""
    with _conn() as c:
        _touch_area(c, sci, common, lat, lon, trust="mute", reviewed=1, auto_locked=1)
        swept = _retro(c, sci, lat, lon, "suggested", "rejected", 1)
    return {"muted": sci, "rejected_nearby": swept}


def unmute_species(sci: str, lat: float, lon: float, common: str = "") -> dict:
    """Reverse a mute: clear the mute anchor near here and move its auto-rejected
    detections back to suggested so the species can be reconsidered."""
    with _conn() as c:
        for r in _covering(c, sci, lat, lon):
            if r["trust"] == "mute":
                c.execute("UPDATE species_area SET trust='none', auto_locked=0, updated_at=? WHERE id=?",
                          (_now(), r["id"]))
        restored = _retro(c, sci, lat, lon, "rejected", "suggested", 0)
    return {"unmuted": sci, "restored": restored}


def set_species_auto(sci: str, lat: float, lon: float, on: bool, common: str = "") -> dict:
    """Toggle auto-confirm for a species near here. Off locks it from re-arming on
    a later manual confirm; on sweeps nearby suggestions to confirmed."""
    with _conn() as c:
        if on:
            _touch_area(c, sci, common, lat, lon, trust="auto", reviewed=1, auto_locked=0)
            swept = _retro(c, sci, lat, lon, "suggested", "confirmed", 1)
            return {"auto": True, "also_confirmed_nearby": swept}
        _touch_area(c, sci, common, lat, lon, trust="none", auto_locked=1, reviewed=1)
        return {"auto": False}


def mark_species_reviewed(sci: str, lat: float | None, lon: float | None,
                          common: str = "") -> dict:
    """Acknowledge a species' first appearance (tapped in) so it un-pins / leaves
    the new-arrival badge, without changing its status."""
    with _conn() as c:
        if lat is None or lon is None:
            c.execute("UPDATE species_area SET reviewed=1, updated_at=? "
                      "WHERE scientific_name=? AND reviewed=0", (_now(), sci))
        else:
            _touch_area(c, sci, common, lat, lon, reviewed=1)
    return {"reviewed": sci}


def _new_arrival_scinames(c: sqlite3.Connection, include_nonbird: bool = False) -> set:
    """Species first heard anywhere within the last 24h that still have a VISIBLE
    suggested detection (respects the live floor/range). 'New' is a pure time
    window — first detection < 24h ago — shared and self-expiring, not tied to
    whether anyone has looked at it."""
    cutoff = _now_minus(NEW_WINDOW_S)
    vis_sql, vis_args = _visible_clause(None)
    rows = c.execute(
        "SELECT scientific_name FROM browser_detections"
        " WHERE status='suggested'" + _bird_clause(include_nonbird) + vis_sql +
        " GROUP BY scientific_name HAVING MIN(ts) >= ?", (*vis_args, cutoff)).fetchall()
    return {r["scientific_name"] for r in rows}


def species_events(sci: str, location_id: int | None = None,
                   status: str | None = None, limit: int = 50) -> list[dict]:
    """Individual detections for one species (for the inline row expansion).
    Respects the same visibility filters as the lists, plus the tab's status."""
    with _conn() as c:
        vis_sql, vis_args = _visible_clause(None)
        q = ("SELECT id, ts, round(confidence,4) AS confidence, device_name, status, location_id "
             "FROM browser_detections WHERE scientific_name=?" + _bird_clause(False)
             + _status_clause(status) + vis_sql)
        params: list = [sci, *vis_args]
        if location_id:
            q += " AND location_id=?"
            params.append(location_id)
        params.append(max(1, min(limit, 200)))
        rows = c.execute(q + " ORDER BY id DESC LIMIT ?", params).fetchall()
        loc_names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM locations")}
        rec_map = {r["det_id"]: r["id"] for r in c.execute(
            "SELECT id, det_id FROM recordings WHERE scientific_name=?", (sci,)).fetchall()}
    return [{"id": r["id"], "at": _fmt_dt(r["ts"], with_secs=True),
             "confidence": r["confidence"], "device": r["device_name"],
             "status": r["status"], "location": loc_names.get(r["location_id"]),
             "recording_id": rec_map.get(r["id"])}
            for r in rows]


def new_arrival_count() -> int:
    """Species whose first local appearance is still unreviewed (the badge)."""
    with _conn() as c:
        return len(_new_arrival_scinames(c))


def suggestions(limit: int = 50, offset: int = 0, include_nonbird: bool = False) -> dict:
    """Suggested detections, newest first, but with unreviewed first-time arrivals
    PINNED to the top (regardless of age) until acknowledged. Returns the page +
    the new-arrival badge count."""
    with _conn() as c:
        # Species that are still unreviewed new arrivals -> their rows pin first.
        pinned = _new_arrival_scinames(c, include_nonbird)
        vis_sql, vis_args = _visible_clause(None)
        rows = c.execute(
            "SELECT id, ts, scientific_name, common_name, round(confidence,4) AS confidence, "
            "occurrence, location_id, device_name FROM browser_detections "
            "WHERE status='suggested'" + _bird_clause(include_nonbird) + vis_sql +
            " ORDER BY id DESC", vis_args).fetchall()
        items = [dict(r) for r in rows]
        for it in items:
            it["is_new"] = it["scientific_name"] in pinned
        items.sort(key=lambda x: (0 if x["is_new"] else 1))  # stable: pinned first
        page = items[offset:offset + max(1, min(limit, 200))]
        return {"items": page, "total": len(items), "new_count": len(pinned)}


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
                "WHERE lat IS NOT NULL AND lon IS NOT NULL AND deleted_at IS NULL").fetchall():
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
    """Transcode -> smart-detect -> attribute to a (device, location) -> store.

    Hard pre-filters (see docs/birdnet-accuracy.md) decide what is even eligible:
      1. range filter - resolved coords + week, so only locally-plausible species
         are considered; impossible ones are dropped, never stored.
      2. confidence floor - smart_settings()["min_conf"].
    What survives is stored with a TRUST STATUS (see the trust model section):
      - covered by a mute anchor   -> rejected (auto), not stored;
      - covered by an auto anchor   -> confirmed (auto);
      - otherwise                   -> suggested (and a 'new arrival' anchor is
        tracked so the UI can pin/badge its first appearance).
    Detections still RIDE BACK in the response flagged so the UI can show what was
    heard and why. Deduped on the 1s cadence so a continuing song is one row.
    Returns this chunk's detections plus the resolved location."""
    cfg = smart_settings()
    min_conf = max(min_conf, cfg["min_conf"])
    dev_name = (device_name or station or "").strip()
    ts = _now()
    now_epoch = datetime.fromisoformat(ts).timestamp()
    loc_id: int | None = None
    loc_name = station

    wav = _transcode_to_wav(audio, content_type)
    src_mp3: str | None = None
    try:
      with _conn() as c:
        # Keep the device row fresh (so a just-started device appears even before
        # its first detection). NOTE: mode is a device SETTING owned by the
        # Devices form (upsert_device) — it is set only on first insert here, and
        # never overwritten by the audio stream. The recording tab posts whatever
        # mode is in its own localStorage every 1s; if we wrote it on every chunk,
        # a still-running device would stomp any mode change made from another
        # screen within a second (the "fixed reverts to adaptive on reload" bug).
        owner_uid = None
        if token:
            # viewer is set when running under a standalone session (springer_app);
            # None on the tailnet Workshop app. In a session we CLAIM the device for
            # the logged-in user so their detections attribute + scope to them, and
            # refuse to write as a device someone else already owns (anti-spoof).
            viewer = _resolve_viewer(None)
            existing = c.execute("SELECT owner_user_id FROM devices WHERE token=?", (token,)).fetchone()
            if viewer is not None and existing and existing["owner_user_id"] not in (None, viewer):
                raise PermissionError("device belongs to another account")
            # Only PERSIST a device that has a real, user-given name (a blank/placeholder
            # name is an unnamed/ephemeral session: attribute by token, don't clutter
            # the device list). EXCEPTION: under a session we always persist + own it,
            # else the user could not see their own detections (visibility is device-based).
            real_name = dev_name if dev_name and dev_name not in ("New device", "Untitled device") else ""
            if viewer is not None:
                c.execute(
                    "INSERT INTO devices(token,name,mode,owner_user_id,created_at,last_seen) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET "
                    "name=COALESCE(NULLIF(excluded.name,''),devices.name), "
                    "owner_user_id=COALESCE(devices.owner_user_id,excluded.owner_user_id), "
                    "last_seen=excluded.last_seen",
                    (token, real_name or "Phone", mode, viewer, ts, ts))
            elif real_name:
                c.execute(
                    "INSERT INTO devices(token,name,mode,created_at,last_seen) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(token) DO UPDATE SET "
                    "name=COALESCE(NULLIF(excluded.name,''),devices.name), "
                    "last_seen=excluded.last_seen",
                    (token, real_name, mode, ts, ts))
            else:
                c.execute("UPDATE devices SET last_seen=? WHERE token=?", (ts, token))
            orow = c.execute("SELECT owner_user_id FROM devices WHERE token=?", (token,)).fetchone()
            owner_uid = orow["owner_user_id"] if orow else viewer
        # Resolve coordinates for the range filter BEFORE analysis (read-only;
        # creates nothing). No place -> filter stays off, range comes back unknown.
        eff_lat, eff_lon = _effective_coords(c, token, mode, lat, lon, location_id)
        detections = _call_analyzer(wav, min_conf, lat=eff_lat, lon=eff_lon,
                                    sensitivity=cfg["sensitivity"],
                                    sf_thresh=cfg["sf_thresh"])

        for d in detections:
            d["is_bird"] = is_bird(d.get("scientific_name"), d.get("common_name"))
            d["stored"] = False

        # Attribute to a real place (may create one at a GPS point). Empty when an
        # adaptive device has no GPS and no prior place -> needs_location.
        if detections or lat is not None:
            loc_id, loc_name = resolve_location(c, token, dev_name, mode, lat, lon, address, location_id)
        if loc_id is None:
            loc_name = None

        if detections and loc_id is not None:
            to_store = []
            for d in detections:
                sci = d["scientific_name"]
                if d["is_bird"]:
                    # Hard range gate: drop species the model says can't be here.
                    # in_range is None when the filter was off (no coords).
                    if d.get("in_range") is False:
                        d["filtered"] = "out_of_range"
                        continue
                    # Trust status from anchors near this point.
                    status, auto = _classify(c, sci, d.get("common_name"), eff_lat, eff_lon)
                else:
                    status, auto = "suggested", 0  # noise classes aren't trusted/curated
                if status == "rejected":
                    # Muted species here: don't store, just report it was muted.
                    d["status"] = "rejected"
                    d["filtered"] = "muted"
                    continue
                # Dedup the 1s overlapping cadence so a continuing song is one row.
                prev = c.execute(
                    "SELECT ts FROM browser_detections WHERE location_id=? AND scientific_name=? "
                    "ORDER BY id DESC LIMIT 1", (loc_id, sci)).fetchone()
                if prev:
                    try:
                        if (datetime.fromisoformat(ts) -
                                datetime.fromisoformat(prev["ts"])).total_seconds() < DEDUP_S:
                            d["filtered"] = "dedup"
                            continue
                    except ValueError:
                        pass
                d["status"] = status
                d["auto"] = auto
                d["stored"] = True
                to_store.append(d)
            if to_store:
                # Insert each stored row individually so we can capture its id and
                # attach a recording. Saving a clip is gated on the SAME store
                # decision, plus is_bird (the non-bird filter applies to saving
                # too), plus the per-species save mode resolved against the global
                # "save recordings" setting. Encode the analyzed WAV to MP3 once
                # per chunk, then copy a clip per savable detection.
                rec_cfg = rec_settings()
                atlas_cfg = atlas_settings()
                modes = _save_modes(c)
                want = lambda d: d["is_bird"] and _should_save(d["scientific_name"], modes, rec_cfg["rec_save"])
                # The Atlas exemplar pool keeps a clip of every stored bird while its
                # own toggle is on - independent of the recordings save setting.
                atlas_want = lambda d: atlas_cfg["atlas_clip_save"] and d["is_bird"]
                # One MP3 per chunk, reused for both pools (recordings + Atlas).
                src_size = 0
                if any(want(d) or atlas_want(d) for d in to_store):
                    src_mp3 = wav + ".rec.mp3"
                    src_size = _encode_clip(wav, src_mp3)
                    if src_size == 0:
                        src_mp3 = None
                # One 1024-d embedding per chunk (the acoustic fingerprint), attached
                # to every stored row. None when the analyzer can't embed - storage
                # is never blocked on it.
                vec = _call_embed(wav)
                for d in to_store:
                    cur = c.execute(
                        "INSERT INTO browser_detections(station,ts,scientific_name,common_name,"
                        "confidence,start_s,end_s,device_token,is_bird,device_name,location_id,lat,lon,"
                        "occurrence,in_range,confirmed,status,auto,owner_user_id) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (loc_name, ts, d["scientific_name"], d["common_name"], d["confidence"],
                         d.get("start_s", 0.0), d.get("end_s", 0.0), token, 1 if d["is_bird"] else 0,
                         dev_name, loc_id, eff_lat, eff_lon,
                         d.get("occurrence"), None if d.get("in_range") is None else int(d["in_range"]),
                         1, d["status"], d["auto"], owner_uid))
                    det_id = cur.lastrowid
                    if vec is not None:
                        d["call_type"] = _store_embedding(
                            c, det_id, d["scientific_name"], vec,
                            atlas_cfg["atlas_unknown_dist"], ts)
                    if src_mp3 and want(d):
                        if _capture_clip(c, d, det_id, src_mp3, src_size,
                                         loc_id, token, ts, rec_cfg):
                            d["recorded"] = True
                    if src_mp3 and atlas_want(d):
                        _capture_atlas_clip(c, d, det_id, src_mp3, src_size, ts, atlas_cfg)
        # Mirror into the legacy stations table so old /station/* keeps resolving.
        if token and dev_name:
            c.execute(
                "INSERT INTO stations(token,label,created_at,last_seen) VALUES(?,?,?,?) "
                "ON CONFLICT(token) DO UPDATE SET label=excluded.label, last_seen=excluded.last_seen",
                (token, dev_name, ts, ts))
    finally:
        for f in (src_mp3, wav):
            if f:
                try:
                    os.unlink(f)
                except OSError:
                    pass
    # "needs_location" = we heard something but have nowhere real to file it
    # (adaptive device with no GPS and no prior place). The UI prompts the user
    # to pin an address; the live detections still ride back so they see them.
    needs_location = loc_id is None and bool(detections)
    return {"ts": ts, "detections": detections, "location": loc_name,
            "location_id": loc_id, "needs_location": needs_location}


def recent(station: str, limit: int = 100, include_nonbird: bool = False) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT ts, scientific_name, common_name, round(confidence,4) AS confidence, "
            "is_bird, occurrence, in_range FROM browser_detections WHERE station=?"
            + _bird_clause(include_nonbird) +
            " ORDER BY id DESC LIMIT ?",
            (station, max(1, min(limit, 500))),
        ).fetchall()
    return [dict(r) for r in rows]


# The always-on yard mic's device token (the bridge posts under this). Detections
# from it ARE the yard's data in the unified store, so this is the scope for the
# yard read path that replaced the retired BirdNET-Go birdnet.db reader.
YARD_DEVICE_TOKEN = os.environ.get("BIRDNET_YARD_DEVICE", "yard-pi-springer-01")


def yard_detections(limit: int = 100, min_conf: float = 0.0,
                    include_nonbird: bool = False) -> list[dict]:
    """Recent detections from the always-on yard mic (unified store), shaped like
    the old birds_store.detections() the UI expects: the stored UTC ts split into
    local date + time. Drives the Live Aviary, the yard live feed, and any other
    recent-detections reader."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, ts, scientific_name, common_name, round(confidence,4) AS confidence, "
            "is_bird, clip_name FROM browser_detections WHERE device_token=? AND confidence>=?"
            + _bird_clause(include_nonbird) +
            " ORDER BY id DESC LIMIT ?",
            (YARD_DEVICE_TOKEN, min_conf, max(1, min(limit, 1000))),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        dt = _parse_ts(d.pop("ts"))
        d["date"] = dt.strftime("%Y-%m-%d") if dt else None
        d["time"] = dt.strftime("%H:%M:%S") if dt else None
        out.append(d)
    return out


def _yard_bird_rows(c, include_nonbird: bool = False):
    return c.execute(
        "SELECT id, ts, scientific_name, common_name, round(confidence,4) AS confidence "
        "FROM browser_detections WHERE device_token=?" + _bird_clause(include_nonbird) +
        " ORDER BY id DESC", (YARD_DEVICE_TOKEN,)).fetchall()


def yard_summary(include_nonbird: bool = False) -> dict:
    """Always-on yard totals (unified store), in the shape the home dashboard's yard
    view expects. Replaces birds_store.summary() / the retired birdnet.db."""
    today = _local(_now()).strftime("%Y-%m-%d")
    with _conn() as c:
        rows = _yard_bird_rows(c, include_nonbird)
    dates, species, today_sp = set(), set(), set()
    today_n = 0
    last_dt = None
    for r in rows:
        dt = _parse_ts(r["ts"])
        d = dt.strftime("%Y-%m-%d") if dt else None
        if d:
            dates.add(d)
        if r["scientific_name"]:
            species.add(r["scientific_name"])
        if d == today:
            today_n += 1
            today_sp.add(r["scientific_name"])
        if last_dt is None and dt is not None:
            last_dt = (dt, r)   # rows are id DESC, so the first parseable is the latest
    first_date = min(dates) if dates else None
    last = None
    if last_dt:
        dt, r = last_dt
        last = {"date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M:%S"),
                "common_name": r["common_name"], "scientific_name": r["scientific_name"],
                "confidence": r["confidence"]}
    return {
        "total_detections": len(rows), "species_count": len(species),
        "first_date": first_date, "last_date": max(dates) if dates else None,
        "active_days": len(dates),
        "today": {"detections": today_n, "species": len(today_sp), "date": today},
        "last_detection": last,
    }


def yard_species(include_nonbird: bool = False) -> list[dict]:
    """Per-species rollup for the yard, count-desc — shape of birds_store.species_list()."""
    with _conn() as c:
        rows = _yard_bird_rows(c, include_nonbird)
    agg: dict = {}
    for r in rows:
        sci = r["scientific_name"]
        if not sci:
            continue
        a = agg.get(sci)
        dt = _parse_ts(r["ts"])
        d = dt.strftime("%Y-%m-%d") if dt else None
        conf = r["confidence"] or 0
        if not a:
            agg[sci] = {"scientific_name": sci, "common_name": r["common_name"],
                        "species_code": None, "count": 1, "first_date": d, "last_date": d,
                        "max_conf": conf, "_sum": conf}
        else:
            a["count"] += 1; a["_sum"] += conf
            a["max_conf"] = max(a["max_conf"], conf)
            if d and (a["first_date"] is None or d < a["first_date"]):
                a["first_date"] = d
            if d and (a["last_date"] is None or d > a["last_date"]):
                a["last_date"] = d
    out = []
    for a in agg.values():
        a["max_conf"] = round(a["max_conf"], 4)
        a["avg_conf"] = round(a.pop("_sum") / a["count"], 4)
        out.append(a)
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def yard_activity_daily(days: int = 90, include_nonbird: bool = False) -> list[dict]:
    cutoff = (_local(_now()).date() - timedelta(days=int(days)))
    with _conn() as c:
        rows = _yard_bird_rows(c, include_nonbird)
    by_day: dict = {}
    for r in rows:
        dt = _parse_ts(r["ts"])
        if not dt or dt.date() < cutoff:
            continue
        d = dt.strftime("%Y-%m-%d")
        b = by_day.setdefault(d, {"date": d, "count": 0, "_sp": set(), "_win": set()})
        b["count"] += 1; b["_sp"].add(r["scientific_name"])
        # Presence = distinct 15-min windows with >=1 detection (0..95 within a day).
        b["_win"].add(dt.hour * 4 + dt.minute // 15)
    out = [{"date": d, "count": b["count"], "species": len(b["_sp"]), "windows": len(b["_win"])}
           for d, b in sorted(by_day.items())]
    return out


def yard_activity_hourly(days: int = 30, include_nonbird: bool = False) -> list[dict]:
    cutoff = (_local(_now()).date() - timedelta(days=int(days)))
    by_hour = {h: 0 for h in range(24)}
    with _conn() as c:
        rows = _yard_bird_rows(c, include_nonbird)
    for r in rows:
        dt = _parse_ts(r["ts"])
        if not dt or dt.date() < cutoff:
            continue
        by_hour[dt.hour] += 1
    return [{"hour": h, "count": by_hour[h]} for h in range(24)]


def yard_first_seen(sci: str, date: str) -> dict:
    """Event-bridge enrichment: is this the species' first-ever yard detection, and
    its first today? Unified-store version of birds_store.first_seen_flags()."""
    with _conn() as c:
        rows = c.execute(
            "SELECT ts FROM browser_detections WHERE device_token=? AND scientific_name=?",
            (YARD_DEVICE_TOKEN, sci)).fetchall()
    dates = [d.strftime("%Y-%m-%d") for d in (_parse_ts(r["ts"]) for r in rows) if d]
    today_n = sum(1 for d in dates if d == date)
    return {"first_ever": bool(dates) and min(dates) == date,
            "first_today": today_n <= 1,
            "total_for_species": len(rows)}


def _parse_ts(ts: str | None) -> datetime | None:
    """Stored ISO ts -> tz-aware datetime (local), or None if missing/unparseable.
    Used for the time-windowed 'New' badge."""
    if not ts:
        return None
    try:
        return _local(ts)
    except (TypeError, ValueError):
        return None


def _local(ts: str) -> datetime:
    """Parse a stored UTC ISO ts and convert to the box's local timezone (Pacific)
    so day/hour analytics line up with when the operator actually heard things."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _fmt_dt(ts: str, with_secs: bool = False) -> str:
    """Local 12-hour display string for the UI, e.g. 'Jun 8, 10:55 PM'. The app
    shows 12-hour am/pm times everywhere; grouping keys keep using %Y-%m-%d."""
    lt = _local(ts)
    t = lt.strftime("%-I:%M:%S %p") if with_secs else lt.strftime("%-I:%M %p")
    return f"{lt.strftime('%b')} {lt.day}, {t}"


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
            "at": _fmt_dt(last["ts"]),
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
            "first_ts": a["first_ts"],
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
        "recent": [{"at": _fmt_dt(r["ts"], with_secs=True),
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


# --------------------------------------------------------------------------
# Recordings: save the audio clip of a detection, under per-bird rules and a
# hard storage budget. See docs/birds-recordings-plan.md.
# --------------------------------------------------------------------------

def rec_settings() -> dict:
    """Effective recording settings: app_settings overrides over the defaults."""
    s = dict(_REC_DEFAULTS)
    mb = get_setting("rec_storage_mb")
    if mb:
        try:
            s["rec_storage_mb"] = max(10, min(100000, int(float(mb))))
        except (TypeError, ValueError):
            pass
    wf = get_setting("rec_when_full")
    if wf in ("evict_old", "stop"):
        s["rec_when_full"] = wf
    wr = get_setting("rec_warn_ratio")
    if wr:
        try:
            s["rec_warn_ratio"] = max(0.5, min(0.99, float(wr)))
        except (TypeError, ValueError):
            pass
    s["rec_save"] = (get_setting("rec_save") or ("1" if _REC_SAVE_DEFAULT else "0")) == "1"
    return s


def update_rec_settings(patch: dict) -> dict:
    """Validate + persist recording-budget overrides; returns the effective set."""
    if patch.get("rec_storage_mb") is not None:
        try:
            set_setting("rec_storage_mb", str(max(10, min(100000, int(float(patch["rec_storage_mb"]))))))
        except (TypeError, ValueError):
            pass
    if patch.get("rec_when_full") in ("evict_old", "stop"):
        set_setting("rec_when_full", patch["rec_when_full"])
    if patch.get("rec_save") is not None:
        set_setting("rec_save", "1" if patch["rec_save"] else "0")
    return rec_settings()


_SAVE_MODES = ("default", "keep", "never")


def get_species_save_mode(sci: str) -> str:
    with _conn() as c:
        r = c.execute("SELECT save_mode FROM species_recording WHERE scientific_name=?",
                      (sci,)).fetchone()
    return r["save_mode"] if r and r["save_mode"] in _SAVE_MODES else "default"


def set_species_save_mode(sci: str, mode: str) -> dict:
    """Set a bird's save preference: 'default' (follow global), 'keep' (always
    save + never auto-evict), or 'never'. A 'default' row is dropped so the
    species simply follows the global setting with no per-bird override."""
    if mode not in _SAVE_MODES:
        mode = "default"
    with _conn() as c:
        if mode == "default":
            c.execute("DELETE FROM species_recording WHERE scientific_name=?", (sci,))
        else:
            c.execute("INSERT INTO species_recording(scientific_name,save_mode) VALUES(?,?) "
                      "ON CONFLICT(scientific_name) DO UPDATE SET save_mode=excluded.save_mode",
                      (sci, mode))
    return {"save_mode": mode}


def _save_modes(c: sqlite3.Connection) -> dict:
    return {r["scientific_name"]: r["save_mode"] for r in c.execute(
        "SELECT scientific_name, save_mode FROM species_recording")
        if r["save_mode"] in _SAVE_MODES}


def _should_save(sci: str, modes: dict, global_save: bool) -> bool:
    m = modes.get(sci, "default")
    if m == "keep":
        return True
    if m == "never":
        return False
    return global_save


def _keepall_species(c: sqlite3.Connection) -> set:
    return {s for s, m in _save_modes(c).items() if m == "keep"}


def _slug(s: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in (s or "")).strip("-").lower()
    return out[:48] or "x"


def _encode_clip(wav_path: str, dest: str) -> int:
    """Encode the analyzed WAV to MP3 64k mono at dest; return bytes (0 on fail)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", wav_path, "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", dest],
        capture_output=True)
    p = Path(dest)
    if proc.returncode != 0 or not p.exists():
        return 0
    return p.stat().st_size


def _delete_clip_file(rel_path: str) -> None:
    try:
        (CLIPS_DIR / rel_path).unlink()
    except OSError:
        pass


def _used_bytes(c: sqlite3.Connection) -> int:
    return int(c.execute("SELECT COALESCE(SUM(bytes),0) b FROM recordings").fetchone()["b"])


def _protected_bytes(c: sqlite3.Connection) -> int:
    keep = _keepall_species(c)
    total = 0
    for r in c.execute("SELECT scientific_name, bytes, pinned FROM recordings"):
        if r["pinned"] or r["scientific_name"] in keep:
            total += r["bytes"] or 0
    return total


def _evict_for(c: sqlite3.Connection, need: int, cap: int) -> bool:
    """Free space so used+need <= cap by max-min fair ("water-filling") eviction:
    each step removes one clip from whichever species currently holds the most
    evictable clips, taking that species' oldest clip; ties for tallest are broken
    by globally-oldest clip. This equalizes clip counts from the top down, so an
    abundant species is trimmed before a rare one regardless of clip age, while
    age still orders removal within a species and breaks ties between equally
    abundant ones. Pinned clips and keep_all species are protected. Returns True
    if there is now room, False if protected clips alone fill the budget."""
    keep = _keepall_species(c)
    used = _used_bytes(c)
    if used + need <= cap:
        return True
    counts = {}  # species -> remaining evictable clip count
    for r in c.execute("SELECT scientific_name AS sci, COUNT(*) AS n "
                       "FROM recordings WHERE pinned=0 GROUP BY scientific_name"):
        if r["sci"] not in keep:
            counts[r["sci"]] = r["n"]
    while used + need > cap and counts:
        top = max(counts.values())
        tied = [s for s, n in counts.items() if n == top]
        ph = ",".join("?" * len(tied))
        row = c.execute(
            f"SELECT id, path, bytes, scientific_name AS sci FROM recordings "
            f"WHERE pinned=0 AND scientific_name IN ({ph}) "
            f"ORDER BY ts ASC, id ASC LIMIT 1", tied).fetchone()
        if row is None:
            break
        _delete_clip_file(row["path"])
        c.execute("DELETE FROM recordings WHERE id=?", (row["id"],))
        used -= row["bytes"] or 0
        counts[row["sci"]] -= 1
        if counts[row["sci"]] <= 0:
            del counts[row["sci"]]
    return used + need <= cap


def _capture_clip(c: sqlite3.Connection, d: dict, det_id: int, src_mp3: str,
                  size: int, loc_id: int, token: str, ts: str, cfg: dict) -> bool:
    """Admit one clip under the budget, copy it into place, insert its row.
    Returns False (and marks a skip) if storage couldn't admit it."""
    cap = cfg["rec_storage_mb"] * _MB
    if _used_bytes(c) + size > cap:
        if cfg["rec_when_full"] == "stop" or not _evict_for(c, size, cap):
            set_setting("rec_last_skip_ts", ts)
            return False
    sci = d["scientific_name"]
    rel = f"{loc_id}/{_slug(ts)}_{_slug(sci)}_{det_id}.mp3"
    dest = CLIPS_DIR / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_mp3, dest)
    except OSError:
        return False
    b = dest.stat().st_size
    c.execute("INSERT INTO recordings(det_id,scientific_name,common_name,location_id,"
              "device_token,ts,path,bytes,duration_s,confidence,pinned) "
              "VALUES(?,?,?,?,?,?,?,?,?,?,0)",
              (det_id, sci, d.get("common_name"), loc_id, token, ts, rel, b, 3.0, d["confidence"]))
    return True


# --------------------------------------------------------------------------
# Soundscape Atlas: embedding capture, live nearest-centroid call-type tagging,
# and the independent exemplar-clip pool. The batch cluster build lives in
# services/../birdnet/analyzer/atlas_build.py (heavy ML kept out of this venv).
# --------------------------------------------------------------------------

def atlas_settings() -> dict:
    s = dict(_ATLAS_DEFAULTS)
    mb = get_setting("atlas_clip_storage_mb")
    if mb:
        try:
            s["atlas_clip_storage_mb"] = max(10, min(100000, int(float(mb))))
        except (TypeError, ValueError):
            pass
    ex = get_setting("atlas_exemplars_per_cluster")
    if ex:
        try:
            s["atlas_exemplars_per_cluster"] = max(1, min(64, int(float(ex))))
        except (TypeError, ValueError):
            pass
    ud = get_setting("atlas_unknown_dist")
    try:
        s["atlas_unknown_dist"] = float(ud) if ud else _ATLAS_UNKNOWN_DIST_DEFAULT
    except (TypeError, ValueError):
        s["atlas_unknown_dist"] = _ATLAS_UNKNOWN_DIST_DEFAULT
    s["atlas_clip_save"] = (get_setting("atlas_clip_save") or "0") == "1"
    return s


def update_atlas_settings(patch: dict) -> dict:
    if patch.get("atlas_clip_storage_mb") is not None:
        try:
            set_setting("atlas_clip_storage_mb",
                        str(max(10, min(100000, int(float(patch["atlas_clip_storage_mb"]))))))
        except (TypeError, ValueError):
            pass
    if patch.get("atlas_exemplars_per_cluster") is not None:
        try:
            set_setting("atlas_exemplars_per_cluster",
                        str(max(1, min(64, int(float(patch["atlas_exemplars_per_cluster"]))))))
        except (TypeError, ValueError):
            pass
    if patch.get("atlas_unknown_dist") is not None:
        try:
            set_setting("atlas_unknown_dist", str(max(0.0, min(2.0, float(patch["atlas_unknown_dist"])))))
        except (TypeError, ValueError):
            pass
    if patch.get("atlas_clip_save") is not None:
        set_setting("atlas_clip_save", "1" if patch["atlas_clip_save"] else "0")
    return atlas_settings()


# Per-process centroid cache for live nearest-centroid tagging. Invalidated by the
# atlas_version setting, which the batch build bumps after each rebuild. Each entry
# is (cluster_id, label, unit_vec, inv_norm) grouped by species.
_centroids_cache: dict = {}
_centroids_version = -1


def _cosine_dist(vec: list[float], unit: list[float], inv_norm_a: float) -> float:
    """1 - cosine similarity, where `unit` is the already-unit-normalised centroid
    and inv_norm_a normalises `vec`. Pure-python (no numpy in this venv); 1024-d
    over a handful of clusters is negligible."""
    dot = 0.0
    for x, y in zip(vec, unit):
        dot += x * y
    return 1.0 - dot * inv_norm_a


def _species_centroids(c: sqlite3.Connection) -> dict:
    """{scientific_name: [(cluster_id, label, unit_centroid, 1.0)]} for live
    tagging, cached and refreshed when the batch build bumps atlas_version."""
    global _centroids_cache, _centroids_version
    row = c.execute("SELECT value FROM app_settings WHERE key='atlas_version'").fetchone()
    ver = int(row["value"]) if row and (row["value"] or "").isdigit() else 0
    if ver == _centroids_version and _centroids_cache:
        return _centroids_cache
    out: dict = {}
    for r in c.execute("SELECT id, species, label, centroid FROM atlas_cluster "
                       "WHERE scope='species' AND centroid IS NOT NULL"):
        vec = _unpack_vec(r["centroid"])
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        unit = [x / norm for x in vec]
        out.setdefault(r["species"], []).append((r["id"], r["label"], unit))
    _centroids_cache = out
    _centroids_version = ver
    return out


def _live_tag(c: sqlite3.Connection, sci: str, vec: list[float],
              unknown_dist: float) -> tuple[int | None, str | None, float | None]:
    """Assign a freshly-embedded detection to its species' nearest call-type
    cluster. Beyond unknown_dist from every centroid the detection is an acoustic
    outlier -> left ungrouped (cluster_id NULL, no call_type), the same way the
    batch build treats outliers; the next rebuild re-projects and may place it.
    Within range it adopts the nearest cluster (call_type = that cluster's hand
    label, None while unlabelled)."""
    cents = _species_centroids(c).get(sci)
    if not cents:
        return None, None, None
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    inv = 1.0 / norm
    best_id, best_label, best_dist = None, None, 9.9
    for cid, label, unit in cents:
        d = _cosine_dist(vec, unit, inv)
        if d < best_dist:
            best_id, best_label, best_dist = cid, label, d
    if best_dist > unknown_dist:
        return None, None, round(best_dist, 4)
    return best_id, best_label, round(best_dist, 4)


def _store_embedding(c: sqlite3.Connection, det_id: int, sci: str,
                     vec: list[float], unknown_dist: float, ts: str) -> str | None:
    """Persist a detection's embedding + its live call-type tag; return the tag."""
    cid, ctype, dist = _live_tag(c, sci, vec, unknown_dist)
    c.execute("INSERT OR REPLACE INTO detection_embedding(det_id,vec,dim,model,"
              "cluster_id,call_type,ct_dist,embedded_at) VALUES(?,?,?,?,?,?,?,?)",
              (det_id, _pack_vec(vec), len(vec), EMBED_MODEL, cid, ctype, dist, ts))
    return ctype


def _atlas_used_bytes(c: sqlite3.Connection) -> int:
    return int(c.execute("SELECT COALESCE(SUM(bytes),0) b FROM atlas_clip").fetchone()["b"])


def _atlas_evict_fifo(c: sqlite3.Connection, need: int, cap: int) -> bool:
    """Free space in the Atlas pool by deleting oldest NON-exemplar clips first.
    Exemplars (chosen per-cluster by the last build) are protected. Returns True if
    there is room afterwards."""
    used = _atlas_used_bytes(c)
    if used + need <= cap:
        return True
    for r in c.execute("SELECT id, path, bytes FROM atlas_clip WHERE exemplar=0 "
                       "ORDER BY ts ASC, id ASC"):
        if used + need <= cap:
            break
        _delete_atlas_clip_file(r["path"])
        c.execute("DELETE FROM atlas_clip WHERE id=?", (r["id"],))
        used -= r["bytes"] or 0
    return used + need <= cap


def _delete_atlas_clip_file(rel_path: str) -> None:
    try:
        (ATLAS_CLIPS_DIR / rel_path).unlink()
    except OSError:
        pass


def _capture_atlas_clip(c: sqlite3.Connection, d: dict, det_id: int, src_mp3: str,
                        size: int, ts: str, cfg: dict) -> bool:
    """Admit one clip into the independent Atlas exemplar pool (FIFO under its own
    budget). Collected broadly now; the batch build later keeps only the N nearest
    each cluster centroid and evicts the rest."""
    cap = cfg["atlas_clip_storage_mb"] * _MB
    if _atlas_used_bytes(c) + size > cap and not _atlas_evict_fifo(c, size, cap):
        return False
    sci = d["scientific_name"]
    rel = f"{_slug(sci)}/{_slug(ts)}_{det_id}.mp3"
    dest = ATLAS_CLIPS_DIR / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_mp3, dest)
    except OSError:
        return False
    b = dest.stat().st_size
    c.execute("INSERT INTO atlas_clip(det_id,scientific_name,cluster_id,ts,path,bytes,"
              "confidence,exemplar) VALUES(?,?,?,?,?,?,?,0)",
              (det_id, sci, None, ts, rel, b, d["confidence"]))
    return True


def _bump_atlas_version(c: sqlite3.Connection) -> None:
    row = c.execute("SELECT value FROM app_settings WHERE key='atlas_version'").fetchone()
    nextv = (int(row["value"]) + 1) if row and (row["value"] or "").isdigit() else 1
    c.execute("INSERT INTO app_settings(key,value) VALUES('atlas_version',?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(nextv),))


# Resolve the analyzer-venv interpreter + the batch build script (heavy ML there,
# not in this venv). __file__ = backend/services/birds_analyze.py.
_WORKSHOP_ROOT = Path(__file__).resolve().parent.parent.parent
ATLAS_BUILD_PY = _WORKSHOP_ROOT / "birdnet" / "analyzer" / "atlas_build.py"
ATLAS_VENV_PY = _WORKSHOP_ROOT / "birdnet" / "analyzer" / ".venv" / "bin" / "python"


def atlas_status() -> dict:
    cfg = atlas_settings()
    with _conn() as c:
        n_emb = c.execute("SELECT COUNT(*) n FROM detection_embedding WHERE vec IS NOT NULL").fetchone()["n"]
        n_clustered = c.execute("SELECT COUNT(*) n FROM atlas_point WHERE scope='species'").fetchone()["n"]
        n_clusters = c.execute("SELECT COUNT(*) n FROM atlas_cluster WHERE scope='species'").fetchone()["n"]
        n_labeled = c.execute("SELECT COUNT(*) n FROM atlas_cluster WHERE scope='species' "
                              "AND label IS NOT NULL AND label<>''").fetchone()["n"]
        clip_used = _atlas_used_bytes(c)
        clip_count = c.execute("SELECT COUNT(*) n FROM atlas_clip").fetchone()["n"]
    ver = get_setting("atlas_version")
    last_summary = get_setting("atlas_last_summary")
    return {"built": bool(ver and ver != "0"),
            "version": int(ver) if ver and ver.isdigit() else 0,
            "embedded": n_emb, "clustered": n_clustered,
            "clusters": n_clusters, "labeled_clusters": n_labeled,
            "last_build": get_setting("atlas_last_build"),
            "build_started": get_setting("atlas_build_started"),
            "last_summary": json.loads(last_summary) if last_summary else None,
            "clip_save": cfg["atlas_clip_save"],
            "clip_used_bytes": clip_used, "clip_cap_bytes": cfg["atlas_clip_storage_mb"] * _MB,
            "clip_count": clip_count, "settings": cfg}


def atlas_species() -> list[dict]:
    """Every bird species we have embeddings for, most-detected first. Species past
    the call-type threshold carry real clusters (clusters>0); the rest are still
    selectable and render their points from the global projection, so the By-species
    view is never blank. `n` is embedded detections; `clusters`/`labeled` count
    per-species call-type clusters (0 until the species has enough data to split)."""
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT bd.scientific_name AS scientific_name, "
            "(SELECT common_name FROM browser_detections WHERE scientific_name=bd.scientific_name "
            " AND common_name IS NOT NULL LIMIT 1) AS common_name, "
            "COUNT(*) AS n, "
            "(SELECT COUNT(*) FROM atlas_cluster ac WHERE ac.scope='species' "
            " AND ac.species=bd.scientific_name) AS clusters, "
            "(SELECT COUNT(*) FROM atlas_cluster ac WHERE ac.scope='species' "
            " AND ac.species=bd.scientific_name AND ac.label IS NOT NULL AND ac.label<>'') AS labeled "
            "FROM detection_embedding de JOIN browser_detections bd ON bd.id=de.det_id "
            "WHERE de.vec IS NOT NULL AND bd.is_bird=1 "
            "GROUP BY bd.scientific_name ORDER BY n DESC")]


def atlas_points(scope: str, species: str | None = None,
                 location_id: int | None = None, limit: int = 8000) -> dict:
    """Projection points + cluster list for the map. scope='global' is the
    species-coloured overview; scope='species' is one bird's call-type map.

    Fallback: a species below the call-type threshold has no scope='species'
    projection. Rather than show an empty map, we render that species' points from
    the GLOBAL projection (its sub-region of the shared acoustic space) and return
    `has_species_map: False` so the UI can say call-types are still collecting."""
    scope = "species" if scope == "species" else "global"
    has_species_map = True
    with _conn() as c:
        point_scope = scope
        if scope == "species" and species:
            built = c.execute(
                "SELECT 1 FROM atlas_point ap JOIN browser_detections bd ON bd.id=ap.det_id "
                "WHERE ap.scope='species' AND bd.scientific_name=? LIMIT 1", (species,)).fetchone()
            has_species_map = built is not None
            point_scope = "species" if has_species_map else "global"

        where = "ap.scope=?"
        params: list = [point_scope]
        if scope == "species" and species:
            where += " AND bd.scientific_name=?"
            params.append(species)
        if location_id:
            where += " AND bd.location_id=?"
            params.append(location_id)
        rows = c.execute(
            "SELECT ap.det_id, round(ap.x,4) x, round(ap.y,4) y, "
            # In fallback we borrow global coords, so don't surface a global cluster_id
            # as if it were a call-type cluster — null it out for species scope.
            f"{'NULL' if not has_species_map else 'ap.cluster_id'} cluster_id, "
            "bd.scientific_name sci, bd.common_name common, bd.ts, "
            "round(bd.confidence,3) confidence, de.call_type, "
            "(SELECT r.id FROM recordings r WHERE r.det_id=ap.det_id ORDER BY r.id LIMIT 1) rec_id, "
            "EXISTS(SELECT 1 FROM atlas_clip ac WHERE ac.det_id=ap.det_id) has_clip, "
            "EXISTS(SELECT 1 FROM recordings r WHERE r.det_id=ap.det_id) has_rec "
            "FROM atlas_point ap JOIN browser_detections bd ON bd.id=ap.det_id "
            "LEFT JOIN detection_embedding de ON de.det_id=ap.det_id "
            f"WHERE {where} LIMIT ?", (*params, max(1, min(limit, 20000)))).fetchall()
        cparams: list = [scope]
        cwhere = "scope=?"
        if scope == "species" and species:
            cwhere += " AND species=?"
            cparams.append(species)
        clusters = [dict(r) for r in c.execute(
            "SELECT id, species, label, size FROM atlas_cluster "
            f"WHERE {cwhere} ORDER BY size DESC", tuple(cparams))]
    return {"scope": scope, "species": species, "has_species_map": has_species_map,
            "points": [dict(r) for r in rows], "clusters": clusters,
            "call_types": list(CALL_TYPES)}


def atlas_insights(days: int = 30, include_nonbird: bool = False) -> dict:
    """Soundscape insight aggregates over the always-on yard feed:
      - scatter: per species, presence (distinct 15-min windows) vs raw detections,
        so over-talkers (high detections, low presence) separate from the present-
        but-quiet. This is exactly what the 15-minute window corrects for.
      - heatmap: call-type x hour-of-day frequency (from the embedding labels).
      - novelty: first appearance (date) of each species within the window.
    """
    cutoff_date = _local(_now()).date() - timedelta(days=int(days))
    with _conn() as c:
        rows = _yard_bird_rows(c, include_nonbird)
        ct_rows = c.execute(
            "SELECT bd.ts AS ts, de.call_type AS call_type "
            "FROM detection_embedding de JOIN browser_detections bd ON bd.id=de.det_id "
            "WHERE bd.device_token=? AND de.call_type IS NOT NULL", (YARD_DEVICE_TOKEN,)).fetchall()

    per_sp: dict[str, dict] = {}
    first_ever: dict[str, datetime] = {}
    for r in rows:
        dt = _parse_ts(r["ts"])
        if not dt:
            continue
        sci = r["scientific_name"]
        if not sci:
            continue
        fe = first_ever.get(sci)
        if fe is None or dt < fe:
            first_ever[sci] = dt
        if dt.date() < cutoff_date:
            continue
        a = per_sp.setdefault(sci, {"sci": sci, "common": r["common_name"], "detections": 0, "_win": set()})
        a["detections"] += 1
        a["_win"].add(f'{dt:%Y-%m-%d}:{dt.hour * 4 + dt.minute // 15}')
    scatter = [{"sci": a["sci"], "common": a["common"], "detections": a["detections"],
                "presence": len(a["_win"])} for a in per_sp.values()]
    scatter.sort(key=lambda x: x["detections"], reverse=True)

    novelty = [{"date": f"{dt:%Y-%m-%d}", "sci": sci,
                "common": per_sp.get(sci, {}).get("common", sci), "kind": "species"}
               for sci, dt in first_ever.items() if dt.date() >= cutoff_date]
    novelty.sort(key=lambda x: x["date"])

    heat = {ct: [0] * 24 for ct in CALL_TYPES}
    for r in ct_rows:
        dt = _parse_ts(r["ts"])
        ct = r["call_type"]
        if not dt or ct not in heat or dt.date() < cutoff_date:
            continue
        heat[ct][dt.hour] += 1
    heatmap = {"call_types": list(CALL_TYPES),
               "rows": [{"call_type": ct, "hours": heat[ct]} for ct in CALL_TYPES]}

    return {"days": int(days), "scatter": scatter, "heatmap": heatmap, "novelty": novelty}


def atlas_cluster_clips(cluster_id: int, limit: int = 16) -> dict:
    """Playable exemplars for labelling a cluster by ear: the dedicated Atlas pool
    (exemplars first) plus any clips the recordings system already saved for this
    cluster's members."""
    with _conn() as c:
        meta = c.execute("SELECT id, scope, species, label, size FROM atlas_cluster "
                         "WHERE id=?", (cluster_id,)).fetchone()
        atlas = c.execute(
            "SELECT ac.id, ac.det_id, ac.ts, round(ac.confidence,3) confidence, "
            "bd.common_name common FROM atlas_clip ac "
            "LEFT JOIN browser_detections bd ON bd.id=ac.det_id "
            "WHERE ac.cluster_id=? ORDER BY ac.exemplar DESC, ac.id LIMIT ?",
            (cluster_id, limit)).fetchall()
        recs = c.execute(
            "SELECT r.id, r.det_id, r.ts, round(r.confidence,3) confidence, r.common_name common "
            "FROM recordings r JOIN detection_embedding de ON de.det_id=r.det_id "
            "WHERE de.cluster_id=? LIMIT ?", (cluster_id, limit)).fetchall()
    return {"cluster": dict(meta) if meta else None,
            "atlas_clips": [dict(r) for r in atlas],
            "recordings": [dict(r) for r in recs]}


def atlas_clip_path(clip_id: int) -> Path | None:
    with _conn() as c:
        r = c.execute("SELECT path FROM atlas_clip WHERE id=?", (clip_id,)).fetchone()
    if not r:
        return None
    p = ATLAS_CLIPS_DIR / r["path"]
    return p if p.exists() else None


def set_cluster_label(cluster_id: int, label: str | None) -> dict:
    """Hand-label a cluster's call type. Propagates to its member detections at
    once (so over-time views update immediately) and bumps atlas_version so live
    nearest-centroid tagging serves the new label without waiting for a rebuild."""
    norm = (label or "").strip().lower() or None
    if norm is not None and norm not in CALL_TYPES:
        raise ValueError(f"unknown call type: {label}")
    with _conn() as c:
        row = c.execute("SELECT id FROM atlas_cluster WHERE id=?", (cluster_id,)).fetchone()
        if not row:
            raise KeyError("cluster not found")
        c.execute("UPDATE atlas_cluster SET label=?, updated_at=? WHERE id=?",
                  (norm, _now(), cluster_id))
        c.execute("UPDATE detection_embedding SET call_type=? WHERE cluster_id=?",
                  (norm, cluster_id))
        _bump_atlas_version(c)
    return {"id": cluster_id, "label": norm}


def trigger_atlas_build() -> dict:
    """Kick off the batch cluster build (analyzer venv, detached). Returns at once;
    progress is observable via atlas_status (version/last_build bump on finish)."""
    if not ATLAS_VENV_PY.exists() or not ATLAS_BUILD_PY.exists():
        return {"started": False, "error": "atlas builder not installed"}
    env = dict(os.environ, BIRDNET_BROWSER_DB=str(BROWSER_DB),
               BIRDNET_ATLAS_CLIPS_DIR=str(ATLAS_CLIPS_DIR))
    logf = open("/tmp/atlas-build.log", "ab")
    subprocess.Popen([str(ATLAS_VENV_PY), str(ATLAS_BUILD_PY), "--db", str(BROWSER_DB)],
                     stdout=logf, stderr=logf, env=env, cwd=str(ATLAS_BUILD_PY.parent),
                     start_new_session=True)
    set_setting("atlas_build_started", _now())
    return {"started": True}


# Below this many embedded detections the build refuses (mirrors atlas_build.py's
# MIN_SPECIES_N) — no point triggering it. Once built, only rebuild when at least
# this many NEW embeddings have landed, so the heavy UMAP job runs on real change.
_ATLAS_MIN_BUILD = 20
_ATLAS_REBUILD_DELTA = 20


def maybe_auto_rebuild(min_new: int = _ATLAS_REBUILD_DELTA) -> dict:
    """Self-maintenance: trigger a rebuild when there's enough new acoustic data
    to be worth the cost. Called nightly from the backend lifespan loop. Embeddings
    are captured live as detections are stored; this is what keeps the projected map
    (and any per-species call-type clusters) current without a manual Rebuild."""
    st = atlas_status()
    embedded = st["embedded"]
    if embedded < _ATLAS_MIN_BUILD:
        return {"triggered": False, "reason": f"only {embedded} embedded; need >= {_ATLAS_MIN_BUILD}"}
    base = get_setting("atlas_autobuild_embedded")
    base = int(base) if base and base.isdigit() else 0
    if st["built"] and embedded - base < min_new:
        return {"triggered": False, "reason": f"only {embedded - base} new since last auto-build (< {min_new})"}
    res = trigger_atlas_build()
    if res.get("started"):
        set_setting("atlas_autobuild_embedded", str(embedded))
    return {"triggered": bool(res.get("started")), "embedded": embedded, "new": embedded - base}


def recordings_status() -> dict:
    """Usage + the homepage banner state. 'ok' renders nothing; 'warn' is near
    full; 'error' means saving is failing (stop-mode full, or evict-mode where
    pinned + 'keep' clips alone fill the budget so nothing can be freed)."""
    cfg = rec_settings()
    cap = cfg["rec_storage_mb"] * _MB
    with _conn() as c:
        used = _used_bytes(c)
        protected = _protected_bytes(c)
        count = c.execute("SELECT COUNT(*) n FROM recordings").fetchone()["n"]
        # Saving is active if the global default is on, or any species is 'keep'.
        any_record = cfg["rec_save"] or c.execute(
            "SELECT 1 FROM species_recording WHERE save_mode='keep' LIMIT 1").fetchone() is not None
    last_skip = get_setting("rec_last_skip_ts")
    recent_skip = False
    if last_skip:
        try:
            recent_skip = (datetime.now(timezone.utc)
                           - datetime.fromisoformat(last_skip)).total_seconds() < 600
        except ValueError:
            pass
    avg = _AVG_CLIP_BYTES
    no_room = used + avg > cap
    evict_stuck = (cap - protected) < avg
    warn = used >= cfg["rec_warn_ratio"] * cap or protected >= cfg["rec_warn_ratio"] * cap
    state, message = "ok", ""
    is_error = any_record and (
        (cfg["rec_when_full"] == "stop" and no_room)
        or (cfg["rec_when_full"] == "evict_old" and evict_stuck and no_room)
        or recent_skip)
    if is_error:
        state = "error"
        message = ("Recording paused — storage is full. Raise the limit or remove some recordings."
                   if cfg["rec_when_full"] == "stop"
                   else "Can't free space — saved recordings fill the budget. Unpin some or raise the limit.")
    elif warn and (any_record or used > 0):
        state = "warn"
        message = "Recording storage is almost full."
    return {"state": state, "message": message, "used_bytes": used, "cap_bytes": cap,
            "protected_bytes": protected, "count": count,
            "when_full": cfg["rec_when_full"], "storage_mb": cfg["rec_storage_mb"]}


def list_recordings(sci: str | None = None, location_id: int | None = None,
                    pinned: bool | None = None, limit: int = 200, offset: int = 0) -> dict:
    with _conn() as c:
        keepset = _keepall_species(c)
        where = " WHERE 1=1"
        p: list = []
        vis_sql, vis_args = _visible_clause(None, col="r.device_token")
        where += vis_sql; p += vis_args
        if sci:
            where += " AND r.scientific_name=?"; p.append(sci)
        if location_id:
            where += " AND r.location_id=?"; p.append(location_id)
        if pinned is not None:
            where += " AND r.pinned=?"; p.append(1 if pinned else 0)
        total = c.execute("SELECT COUNT(*) n FROM recordings r" + where, p).fetchone()["n"]
        rows = c.execute(
            "SELECT r.*, l.name AS location_name FROM recordings r "
            "LEFT JOIN locations l ON l.id=r.location_id" + where
            + " ORDER BY r.ts DESC LIMIT ? OFFSET ?", p + [limit, offset]).fetchall()
    items = [{"id": r["id"], "det_id": r["det_id"],
              "scientific_name": r["scientific_name"], "common_name": r["common_name"],
              "location_id": r["location_id"], "location": r["location_name"],
              "ts": r["ts"], "at": _fmt_dt(r["ts"], with_secs=True),
              "bytes": r["bytes"], "duration_s": r["duration_s"], "confidence": r["confidence"],
              "pinned": bool(r["pinned"]),
              "protected": bool(r["pinned"]) or r["scientific_name"] in keepset} for r in rows]
    return {"items": items, "total": total}


def recording_path(rec_id: int) -> Path | None:
    toks = visible_tokens(_resolve_viewer(None))  # request-scoped viewer
    with _conn() as c:
        r = c.execute("SELECT path, device_token FROM recordings WHERE id=?", (rec_id,)).fetchone()
    if not r:
        return None
    if toks is not None and r["device_token"] not in toks:
        return None  # not the viewer's clip to play
    p = CLIPS_DIR / r["path"]
    return p if p.exists() else None


def pin_recording(rec_id: int, pinned: bool) -> dict:
    with _conn() as c:
        c.execute("UPDATE recordings SET pinned=? WHERE id=?", (1 if pinned else 0, rec_id))
    return {"ok": True, "id": rec_id, "pinned": bool(pinned)}


def delete_recording(rec_id: int) -> dict:
    with _conn() as c:
        r = c.execute("SELECT path FROM recordings WHERE id=?", (rec_id,)).fetchone()
        if r:
            _delete_clip_file(r["path"])
            c.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
    return {"ok": True}


def clear_recordings(sci: str) -> dict:
    n = 0
    with _conn() as c:
        for r in c.execute("SELECT id, path FROM recordings WHERE scientific_name=?", (sci,)).fetchall():
            _delete_clip_file(r["path"]); n += 1
        c.execute("DELETE FROM recordings WHERE scientific_name=?", (sci,))
    return {"ok": True, "deleted": n}


def reconcile_recordings() -> dict:
    """Re-sync the DB to disk: drop rows whose file is gone, fix size drift, and
    remove orphan files with no row. Keeps the usage meter honest."""
    fixed = orphans = 0
    with _conn() as c:
        known: set = set()
        for r in c.execute("SELECT id, path, bytes FROM recordings").fetchall():
            p = CLIPS_DIR / r["path"]
            if not p.exists():
                c.execute("DELETE FROM recordings WHERE id=?", (r["id"],)); fixed += 1; continue
            known.add(p.resolve())
            sz = p.stat().st_size
            if sz != (r["bytes"] or 0):
                c.execute("UPDATE recordings SET bytes=? WHERE id=?", (sz, r["id"])); fixed += 1
        if CLIPS_DIR.exists():
            for f in CLIPS_DIR.rglob("*.mp3"):
                if f.resolve() not in known:
                    try:
                        f.unlink(); orphans += 1
                    except OSError:
                        pass
    return {"ok": True, "rows_fixed": fixed, "orphans_removed": orphans}


# --------------------------------------------------------------------------
# Accounts + friend model. In the private Workshop app there is one user (the
# operator, id 1) and viewer_id is always None -> "see everything". The
# standalone Springer Birds app passes the session user's id as viewer_id, which
# narrows reads to that person's own devices plus their accepted friends' SHARED
# devices. Visibility is a per-device token set, so it honors the CURRENT `shared`
# flag (not the value at insert time) and per-device sharing within one owner.
# --------------------------------------------------------------------------
OPERATOR_USER_ID = 1


def list_users() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,email,name,role,created_at FROM users ORDER BY id")]


def get_user(user_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT id,email,name,role,created_at FROM users WHERE id=?",
                      (user_id,)).fetchone()
    return dict(r) if r else None


def upsert_user(email: str, name: str | None = None, role: str | None = None) -> dict:
    """Create a user by email, or update an existing one's name/role. The
    standalone app calls this to auto-create a member row on first authenticated
    visit; the admin uses it to set names/roles."""
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("email required")
    with _conn() as c:
        row = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if row:
            sets, args = [], []
            if name is not None:
                sets.append("name=?"); args.append(name.strip())
            if role in ("admin", "member"):
                sets.append("role=?"); args.append(role)
            if sets:
                args.append(row["id"])
                c.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", args)
            uid = row["id"]
        else:
            cur = c.execute(
                "INSERT INTO users(email,name,role,created_at) VALUES(?,?,?,?)",
                (email, (name or "").strip() or None,
                 role if role in ("admin", "member") else "member", _now()))
            uid = cur.lastrowid
        c.commit()
    return get_user(uid)


def _friend_ids(c: sqlite3.Connection, user_id: int) -> set[int]:
    """Accepted, mutual friends of user_id (symmetric - direction does not matter)."""
    out: set[int] = set()
    for r in c.execute(
            "SELECT user_a,user_b FROM friendships WHERE status='accepted' "
            "AND (user_a=? OR user_b=?)", (user_id, user_id)):
        out.add(r["user_b"] if r["user_a"] == user_id else r["user_a"])
    return out


def list_friends(user_id: int) -> list[dict]:
    with _conn() as c:
        ids = _friend_ids(c, user_id)
        pending = {}
        for r in c.execute(
                "SELECT user_a,user_b,status FROM friendships WHERE user_a=? OR user_b=?",
                (user_id, user_id)):
            other = r["user_b"] if r["user_a"] == user_id else r["user_a"]
            pending[other] = r["status"]
        users = {u["id"]: u for u in list_users()}
    return [{**users.get(i, {"id": i}), "friend_status": pending.get(i, "accepted")}
            for i in pending]


def set_friendship(user_a: int, user_b: int, status: str = "accepted") -> dict:
    """Create/update a mutual friendship. Stored once per pair (smaller id first)."""
    if int(user_a) == int(user_b):
        raise ValueError("cannot friend yourself")
    lo, hi = sorted((int(user_a), int(user_b)))
    status = status if status in ("accepted", "pending") else "accepted"
    with _conn() as c:
        c.execute("INSERT INTO friendships(user_a,user_b,status,created_at) VALUES(?,?,?,?) "
                  "ON CONFLICT(user_a,user_b) DO UPDATE SET status=excluded.status",
                  (lo, hi, status, _now()))
        c.commit()
    return {"user_a": lo, "user_b": hi, "status": status}


def remove_friendship(user_a: int, user_b: int) -> dict:
    lo, hi = sorted((int(user_a), int(user_b)))
    with _conn() as c:
        n = c.execute("DELETE FROM friendships WHERE user_a=? AND user_b=?", (lo, hi)).rowcount
        c.commit()
    return {"removed": n}


def visible_tokens(viewer_id: int | None) -> list[str] | None:
    """Device tokens whose detections `viewer_id` may see: every device they own
    (shared or not) plus accepted friends' devices flagged shared=1. Returns None
    when viewer_id is None (private app/operator) meaning 'no restriction'."""
    if viewer_id is None:
        return None
    with _conn() as c:
        mine = {r["token"] for r in c.execute(
            "SELECT token FROM devices WHERE owner_user_id=?", (viewer_id,))}
        friends = _friend_ids(c, viewer_id)
        if friends:
            qs = ",".join("?" * len(friends))
            mine |= {r["token"] for r in c.execute(
                f"SELECT token FROM devices WHERE shared=1 AND owner_user_id IN ({qs})",
                tuple(friends))}
    return list(mine)


# Request-scoped viewer. The standalone app (springer_app) sets this per request
# from the authenticated session; it is AUTHORITATIVE and a client cannot widen it
# by passing a viewer_id query arg. The private tailnet app never sets it, so the
# operator keeps seeing everything. Lives in a contextvar so it is correct under
# concurrent requests without threading a param through every call site.
_viewer_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "springer_viewer_id", default=None)


def set_request_viewer(user_id: int | None) -> None:
    """Pin the visibility viewer for the current request (springer_app calls this
    from its auth dependency). None clears it (= operator/tailnet, see all)."""
    _viewer_ctx.set(user_id)


def _resolve_viewer(explicit: int | None) -> int | None:
    """The effective viewer, server-pinned first so a client cannot widen scope:
      1. the springer request-scoped viewer (`set_request_viewer`), if set;
      2. else the main-backend data owner pinned by `current_user`
         (`ownership.owner_or_none()`) — this is what scopes `/birds/*` reads on
         the workshop backend to the authenticated user, instead of defaulting to
         see-all. It wins over `explicit` so `?viewer_id=` cannot override it;
      3. else the explicit arg (public springer pre-auth, background loops that
         set no owner) — and finally None (unrestricted)."""
    cv = _viewer_ctx.get()
    if cv is not None:
        return cv
    from services import ownership  # lazy: match this module's import-cycle policy
    owner = ownership.owner_or_none()
    return owner if owner is not None else explicit


def _is_admin(viewer_id: int | None = None) -> bool:
    """True for the operator / private tailnet app (no request-scoped viewer) or a
    logged-in user whose role is 'admin'. Members are never admins. Gates the
    removed-items lists + restore: only an admin may see or undo a soft-delete.
    Resolves the request-scoped viewer first, so a member cannot pass ?viewer_id
    to elevate."""
    vid = _resolve_viewer(viewer_id)
    if vid is None:
        return True
    u = get_user(vid)
    return bool(u and u.get("role") == "admin")


def _assert_device_owner(c: sqlite3.Connection, token: str) -> None:
    """In a member session, refuse to mutate a device owned by someone else.
    No-op on the tailnet app (no viewer) and for admins."""
    viewer = _resolve_viewer(None)
    if viewer is None or _is_admin(viewer):
        return
    row = c.execute("SELECT owner_user_id FROM devices WHERE token=?", (token,)).fetchone()
    if row and row["owner_user_id"] not in (None, viewer):
        raise PermissionError("device belongs to another account")


def _visible_clause(viewer_id: int | None, col: str = "device_token") -> tuple[str, list]:
    """SQL fragment (' AND <col> IN (...)') + args restricting rows to the
    viewer's visible device tokens. Empty when unrestricted; a never-true clause
    when the viewer can see nothing. Resolves the request-scoped viewer first."""
    toks = visible_tokens(_resolve_viewer(viewer_id))
    if toks is None:
        return "", []
    if not toks:
        return " AND 1=0", []
    qs = ",".join("?" * len(toks))
    return f" AND {col} IN ({qs})", list(toks)


# --------------------------------------------------------------------------
# Live broadcast. A shared device, while listening, calls publish_live() ~1/s
# with its rolling spectrogram columns + current detections; viewers who can see
# that device read it via live_feeds(). Near-real-time (poll-based), shared-DB
# backed so it crosses the tailnet/standalone process boundary.
# --------------------------------------------------------------------------
LIVE_TTL_S = 12


def publish_live(token: str, cols: list, hearing: list) -> dict:
    """Store the latest live frame for a (shared, listening) device. Location +
    owner + name are resolved from the device row so viewers can label the feed."""
    token = (token or "").strip()
    if not token:
        return {"ok": False}
    with _conn() as c:
        d = c.execute("SELECT owner_user_id,name,fixed_location_id,shared FROM devices WHERE token=?",
                      (token,)).fetchone()
        if not d or not d["shared"]:
            return {"ok": False, "reason": "device not shared"}  # only shared devices broadcast
        loc = d["fixed_location_id"]
        if not loc:
            r = c.execute("SELECT location_id FROM browser_detections WHERE device_token=? "
                          "AND location_id IS NOT NULL ORDER BY id DESC LIMIT 1", (token,)).fetchone()
            loc = r["location_id"] if r else None
        payload = json.dumps({"cols": cols, "hearing": hearing}, separators=(",", ":"))
        c.execute(
            "INSERT INTO live_feed(device_token,ts,location_id,owner_user_id,device_name,payload) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(device_token) DO UPDATE SET "
            "ts=excluded.ts, location_id=excluded.location_id, owner_user_id=excluded.owner_user_id, "
            "device_name=excluded.device_name, payload=excluded.payload",
            (token, _now(), loc, d["owner_user_id"], d["name"], payload))
        c.commit()
    return {"ok": True}


def live_feeds(location_id: int | None = None) -> dict:
    """Active live feeds (last LIVE_TTL_S) the request viewer may see, optionally
    scoped to one location. Each carries its spectrogram columns + now-hearing."""
    cutoff = _now_minus(LIVE_TTL_S)
    toks = visible_tokens(_resolve_viewer(None))
    with _conn() as c:
        rows = c.execute(
            "SELECT device_token,ts,location_id,owner_user_id,device_name,payload "
            "FROM live_feed WHERE ts>=?", (cutoff,)).fetchall()
        locs = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM locations")}
        names = {u["id"]: (u["name"] or u["email"].split("@")[0]).split(" ")[0] for u in list_users()}
    out = []
    for r in rows:
        if toks is not None and r["device_token"] not in toks:
            continue
        if location_id is not None and r["location_id"] != location_id:
            continue
        try:
            payload = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        out.append({
            "device_token": r["device_token"], "device": r["device_name"],
            "location_id": r["location_id"], "location": locs.get(r["location_id"]),
            "owner_user_id": r["owner_user_id"], "owner": names.get(r["owner_user_id"]),
            "cols": payload.get("cols", []), "hearing": payload.get("hearing", []),
        })
    return {"feeds": out}


def list_devices(archived: bool = False, viewer_id: int | None = None) -> list[dict]:
    """Active devices by default; archived=True lists only soft-removed ones (for
    the restore path). Detection counts/locations are computed either way."""
    # Removed devices are an admin-only concern: a non-admin viewer never sees the
    # "removed" list (and so can't restore from it). Active list is unchanged.
    if archived and not _is_admin(viewer_id):
        return []
    vis = visible_tokens(_resolve_viewer(viewer_id))  # None = unrestricted (operator/private app)
    with _conn() as c:
        flt = "deleted_at IS NOT NULL" if archived else "deleted_at IS NULL"
        devs = c.execute(
            "SELECT token,name,mode,fixed_location_id,created_at,last_seen,user_agent,"
            "deleted_at,owner_user_id,shared "
            f"FROM devices WHERE {flt} ORDER BY last_seen DESC").fetchall()
        if vis is not None:
            allowed = set(vis)
            devs = [d for d in devs if d["token"] in allowed]
        locs = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM locations")}
        counts = {r["device_token"]: r["n"] for r in c.execute(
            "SELECT device_token, COUNT(*) n FROM browser_detections "
            "WHERE device_token IS NOT NULL GROUP BY device_token")}
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
                "created_at": d["created_at"], "last_seen": d["last_seen"],
                "user_agent": d["user_agent"], "detections": counts.get(d["token"], 0),
                "deleted_at": d["deleted_at"],
                "owner_user_id": d["owner_user_id"], "shared": bool(d["shared"]),
            })
    return out


def get_device(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as c:
        d = c.execute(
            "SELECT token,name,mode,fixed_location_id,created_at,last_seen,owner_user_id,shared "
            "FROM devices WHERE token=?", (token,)).fetchone()
        if not d:
            return None
        loc = None
        if d["fixed_location_id"]:
            lr = c.execute("SELECT name FROM locations WHERE id=?", (d["fixed_location_id"],)).fetchone()
            loc = lr["name"] if lr else None
    return {"token": d["token"], "name": d["name"], "mode": d["mode"] or "adaptive",
            "fixed_location_id": d["fixed_location_id"], "fixed_location": loc,
            "last_seen": d["last_seen"],
            "owner_user_id": d["owner_user_id"], "shared": bool(d["shared"])}


def upsert_device(token: str, name: str | None = None, mode: str | None = None,
                  fixed_location_id: int | None = None, user_agent: str | None = None,
                  update_only: bool = False, shared: bool | None = None,
                  owner_user_id: int | None = None) -> dict | None:
    """Create or update a device. Only non-None fields are written, so the
    settings form can patch one field at a time. update_only=True never creates a
    row (used by the on-load UA touch, so just viewing a page can't register a
    phantom device) - it only refreshes an already-registered device.

    `shared` opts the device's detections into friend visibility; `owner_user_id`
    claims the device for an account (the standalone app sets it from the session
    on first use, which is also what closes the 'any token can post as any device'
    gap)."""
    if not token:
        return None
    now = _now()
    ua = (user_agent or "").strip()
    with _conn() as c:
        exists = c.execute("SELECT 1 FROM devices WHERE token=?", (token,)).fetchone()
        if not exists:
            if update_only:
                return None
            real_name = (name or "").strip()
            if real_name in ("", "New device", "Untitled device") and owner_user_id is None:
                # No real name and no account claiming it → an unnamed/ephemeral
                # device (a fresh browser pressing Listen, an automated test). Do
                # NOT persist a placeholder row; its detections are still
                # attributed by token. It becomes a real device the moment it is
                # named or claimed by an account.
                return None
            c.execute(
                "INSERT INTO devices(token,name,mode,fixed_location_id,created_at,last_seen,"
                "user_agent,shared,owner_user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (token, real_name,
                 mode or "adaptive", fixed_location_id, now, now, ua or None,
                 1 if shared else 0, owner_user_id))
        else:
            _assert_device_owner(c, token)  # member can't edit someone else's device
            sets, args = [], []
            if name is not None:
                sets.append("name=?"); args.append(name.strip())
            if mode is not None:
                sets.append("mode=?"); args.append(mode)
            if fixed_location_id is not None:
                sets.append("fixed_location_id=?"); args.append(fixed_location_id or None)
            if ua:
                sets.append("user_agent=?"); args.append(ua)
            if shared is not None:
                sets.append("shared=?"); args.append(1 if shared else 0)
            if owner_user_id is not None:
                sets.append("owner_user_id=?"); args.append(owner_user_id)
            sets.append("last_seen=?"); args.append(now)
            args.append(token)
            c.execute(f"UPDATE devices SET {','.join(sets)} WHERE token=?", args)
    return get_device(token)


def delete_device(token: str) -> dict:
    """SOFT-remove a device: flag it deleted so it drops out of the device lists
    and filters, but keep the row AND all its detections (which stay attributed to
    their location, provenance intact). Reversible via restore_device - nothing is
    ever hard-deleted or purged by age."""
    if not token:
        return {"ok": False, "deleted": 0}
    with _conn() as c:
        _assert_device_owner(c, token)  # member can't remove someone else's device
        n = c.execute("UPDATE devices SET deleted_at=? WHERE token=? AND deleted_at IS NULL",
                      (_now(), token)).rowcount
    return {"ok": True, "deleted": n}


def restore_device(token: str) -> dict:
    """Bring a soft-removed device back into the active lists with all its data.
    Admin-only: only the operator/admin may undo a removal."""
    if not _is_admin():
        return {"ok": False, "restored": 0, "error": "forbidden"}
    if not token:
        return {"ok": False, "restored": 0}
    with _conn() as c:
        n = c.execute("UPDATE devices SET deleted_at=NULL WHERE token=?", (token,)).rowcount
    return {"ok": True, "restored": n}


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
                    "WHERE id<>? AND lat IS NOT NULL AND lon IS NOT NULL AND deleted_at IS NULL", (location_id,)).fetchall():
                d = _haversine_m(me["lat"], me["lon"], other["lat"], other["lon"])
                if d <= max(me["radius_m"] or 40, other["radius_m"] or 40):
                    c.execute("UPDATE browser_detections SET location_id=? WHERE location_id=?", (location_id, other["id"]))
                    c.execute("UPDATE devices SET fixed_location_id=? WHERE fixed_location_id=?", (location_id, other["id"]))
                    c.execute("DELETE FROM locations WHERE id=?", (other["id"],))
    return get_location(location_id)


def merge_locations(src_id: int, dst_id: int) -> dict | None:
    """Fold one location into another: move its detections and any devices pinned
    to it, repoint the home setting if needed, then delete it. Used to reconcile
    places that cannot auto-merge by distance - e.g. the coordinate-less
    'Untitled' locations left by the pre-GPS migration. Detections keep their
    device_token/device_name, so provenance is preserved."""
    if src_id == dst_id:
        return None
    with _conn() as c:
        dst = c.execute("SELECT id,name FROM locations WHERE id=?", (dst_id,)).fetchone()
        if not dst:
            return None
        if not c.execute("SELECT 1 FROM locations WHERE id=?", (src_id,)).fetchone():
            return None
        c.execute("UPDATE browser_detections SET location_id=?, station=? WHERE location_id=?",
                  (dst_id, dst["name"], src_id))
        c.execute("UPDATE devices SET fixed_location_id=? WHERE fixed_location_id=?", (dst_id, src_id))
        c.execute("DELETE FROM locations WHERE id=?", (src_id,))
        hs = c.execute("SELECT value FROM app_settings WHERE key='home_source'").fetchone()
        if hs and hs["value"] == f"loc:{src_id}":
            c.execute("UPDATE app_settings SET value=? WHERE key='home_source'", (f"loc:{dst_id}",))
    return get_location(dst_id)


# --------------------------------------------------------------------------
# Always-on (BirdNET-Go) ingestion. The capture node POSTs each detection to
# /birds/webhook; we land it in the SAME unified store the browser path writes
# to, attributed to one 'yard' device. This is what makes the always-on station
# "just another device" - one read path, no separate yard source. Metadata only:
# the WAV clip stays on the capture node (clip_name is kept as a reference).
# --------------------------------------------------------------------------
YARD_TOKEN = "yard"


def _yard_location(c: sqlite3.Connection) -> tuple[int | None, str | None]:
    """The always-on station's place. Its pinned location if it has one, else a
    'Yard' location pinned to BIRDNET_LATITUDE/LONGITUDE from the environment (so
    the range filter and map work), else a coordinate-less 'Yard' to pin later."""
    dev = c.execute("SELECT fixed_location_id FROM devices WHERE token=?", (YARD_TOKEN,)).fetchone()
    if dev and dev["fixed_location_id"]:
        row = c.execute("SELECT id,name FROM locations WHERE id=?", (dev["fixed_location_id"],)).fetchone()
        if row:
            return row["id"], row["name"]
    row = c.execute("SELECT id,name FROM locations WHERE name='Yard' AND deleted_at IS NULL").fetchone()
    if row:
        return row["id"], row["name"]
    lat = lon = None
    try:
        lat = float(os.environ["BIRDNET_LATITUDE"])
        lon = float(os.environ["BIRDNET_LONGITUDE"])
    except (KeyError, ValueError):
        pass
    cur = c.execute("INSERT INTO locations(name,lat,lon,created_at) VALUES('Yard',?,?,?)",
                    (lat, lon, _now()))
    return cur.lastrowid, "Yard"


def ingest_yard_detection(payload: dict) -> dict:
    """Store one always-on detection (BirdNET-Go webhook body) into the unified
    browser_detections store as the 'yard' device. Mirrors the browser path's
    trust + dedup so the always-on behaves identically to any other device."""
    sci = (payload.get("scientific_name") or "").strip()
    common = (payload.get("common_name") or "").strip()
    if not sci:
        return {"stored": False, "reason": "no scientific_name"}
    try:
        conf = float(payload.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    # Build a UTC ts from the webhook's local date+time (the box shares the tz).
    date = (payload.get("date") or "").strip()
    tm = (payload.get("time") or "").strip()
    try:
        ts = (datetime.strptime(f"{date} {tm}", "%Y-%m-%d %H:%M:%S")
              .astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    except ValueError:
        ts = _now()
    clip = (payload.get("clip_name") or "").strip() or None
    bird = is_bird(sci, common)
    with _conn() as c:
        c.execute(
            "INSERT INTO devices(token,name,mode,shared,owner_user_id,created_at,last_seen) "
            "VALUES(?,?,'fixed',1,?,?,?) ON CONFLICT(token) DO UPDATE SET last_seen=excluded.last_seen",
            (YARD_TOKEN, "Yard (always-on)", OPERATOR_USER_ID, ts, ts))
        loc_id, loc_name = _yard_location(c)
        c.execute("UPDATE devices SET fixed_location_id=COALESCE(fixed_location_id,?) WHERE token=?",
                  (loc_id, YARD_TOKEN))
        loc = c.execute("SELECT lat,lon FROM locations WHERE id=?", (loc_id,)).fetchone() if loc_id else None
        llat = loc["lat"] if loc else None
        llon = loc["lon"] if loc else None
        if bird and llat is not None and llon is not None:
            status, auto = _classify(c, sci, common, llat, llon)
        else:
            status, auto = "suggested", 0
        if status == "rejected":
            return {"stored": False, "reason": "muted"}
        prev = c.execute(
            "SELECT ts FROM browser_detections WHERE location_id=? AND scientific_name=? "
            "ORDER BY id DESC LIMIT 1", (loc_id, sci)).fetchone()
        if prev:
            try:
                if abs((datetime.fromisoformat(ts) -
                        datetime.fromisoformat(prev["ts"])).total_seconds()) < DEDUP_S:
                    return {"stored": False, "reason": "dedup"}
            except ValueError:
                pass
        cur = c.execute(
            "INSERT INTO browser_detections(station,ts,scientific_name,common_name,confidence,"
            "start_s,end_s,device_token,is_bird,device_name,location_id,lat,lon,occurrence,in_range,"
            "confirmed,status,auto,owner_user_id,clip_name) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (loc_name, ts, sci, common, conf, 0.0, 0.0, YARD_TOKEN, 1 if bird else 0,
             "Yard (always-on)", loc_id, llat, llon, None, None, 1, status, auto,
             OPERATOR_USER_ID, clip))
        det_id = cur.lastrowid
        c.commit()
    return {"stored": True, "id": det_id, "location_id": loc_id, "status": status, "is_bird": bird}


def get_location(location_id: int) -> dict | None:
    with _conn() as c:
        L = c.execute("SELECT id,name,lat,lon,address,radius_m,created_at,deleted_at FROM locations "
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
            "devices": devs, "deleted_at": L["deleted_at"]}


def delete_location(location_id: int) -> dict:
    """SOFT-remove a location: flag it deleted so it leaves the place lists, the
    source selector, and the pin targets - but keep the row AND every detection
    filed under it. Reversible via restore_location; never purged by age."""
    with _conn() as c:
        n = c.execute("UPDATE locations SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                      (_now(), location_id)).rowcount
    return {"ok": True, "deleted": n}


def restore_location(location_id: int) -> dict:
    """Bring a soft-removed location back with all its detections. Admin-only:
    only the operator/admin may undo a removal."""
    if not _is_admin():
        return {"ok": False, "restored": 0, "error": "forbidden"}
    with _conn() as c:
        n = c.execute("UPDATE locations SET deleted_at=NULL WHERE id=?", (location_id,)).rowcount
    return {"ok": True, "restored": n}


def _location_devices(c: sqlite3.Connection, location_id: int) -> list[str]:
    seen = [r["device_name"] for r in c.execute(
        "SELECT DISTINCT device_name FROM browser_detections "
        "WHERE location_id=? AND device_name IS NOT NULL AND device_name<>''",
        (location_id,)).fetchall()]
    fixed = [r["name"] for r in c.execute(
        "SELECT name FROM devices WHERE fixed_location_id=?", (location_id,)).fetchall()]
    return sorted({*seen, *fixed})


def list_locations(include_nonbird: bool = False, archived: bool = False,
                   viewer_id: int | None = None) -> dict:
    # Visibility: for a scoped viewer, a location's stats only count detections
    # they may see, and a place with nothing visible is hidden entirely (so a
    # non-friend never even learns the address exists). The operator (viewer_id
    # None) sees every location, including empty ones.
    # Removed locations are admin-only: a non-admin viewer never sees the "removed"
    # list (and so can't restore from it). Active list is unchanged.
    if archived and not _is_admin(viewer_id):
        return {"locations": [], "home": get_setting("home_source")}
    rv = _resolve_viewer(viewer_id)
    vis_sql, vis_args = _visible_clause(viewer_id)
    with _conn() as c:
        flt = "deleted_at IS NOT NULL" if archived else "deleted_at IS NULL"
        locs = c.execute(f"SELECT id,name,lat,lon,address,radius_m FROM locations WHERE {flt}").fetchall()
        out = []
        for L in locs:
            agg = c.execute(
                "SELECT COUNT(*) d, COUNT(DISTINCT scientific_name) s, MAX(ts) last "
                "FROM browser_detections WHERE location_id=?" + _bird_clause(include_nonbird) + vis_sql,
                (L["id"], *vis_args)).fetchone()
            if rv is not None and not (agg["d"] or 0):
                continue  # nothing visible here for this viewer -> hide the place
            out.append({
                "id": L["id"], "name": L["name"], "lat": L["lat"], "lon": L["lon"],
                "address": L["address"],
                "detections": agg["d"] or 0, "species": agg["s"] or 0, "last_ts": agg["last"],
                "devices": _location_devices(c, L["id"]),
            })
    out.sort(key=lambda x: (x["last_ts"] or ""), reverse=True)
    return {"locations": out, "home": get_setting("home_source")}


def _loc_rows(location_id: int, include_nonbird: bool = False,
              status: str | None = None, device: str | None = None) -> list[sqlite3.Row]:
    vis_sql, vis_args = _visible_clause(None)  # request-scoped viewer enforced here
    q = ("SELECT ts, scientific_name, common_name, confidence "
         "FROM browser_detections WHERE location_id=?" + _bird_clause(include_nonbird)
         + _status_clause(status) + vis_sql)
    p: list = [location_id, *vis_args]
    if device:
        q += " AND device_token=?"
        p.append(device)
    with _conn() as c:
        return c.execute(q + " ORDER BY ts", p).fetchall()


def location_summary(location_id: int, include_nonbird: bool = False,
                     status: str | None = None, device: str | None = None) -> dict:
    # Stats reflect everything actually heard here (confirmed + suggested), never
    # the list's status tab - dismissed false positives are the only exclusion.
    loc = get_location(location_id)
    rows = _loc_rows(location_id, include_nonbird, "_visible", device)
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
            "at": _fmt_dt(last["ts"]),
        },
    }


def _species_trust(c: sqlite3.Connection, location_id: int,
                   include_nonbird: bool, device: str | None = None) -> dict[str, str]:
    """Each species heard at this location mapped to ONE trust status, so a species
    appears in exactly one bucket (never in both Confirmed and Suggested at once):
      rejected  - a mute anchor covers the place, or every detection was rejected;
      confirmed - auto-confirm is armed, or it has any confirmed detection here;
      suggested - heard, but nothing vouched for it yet.
    """
    loc = get_location(location_id)
    lat = loc["lat"] if loc else None
    lon = loc["lon"] if loc else None
    muted: set[str] = set()
    auto: set[str] = set()
    if lat is not None and lon is not None:
        for r in c.execute("SELECT scientific_name, lat, lon, radius_m, trust FROM species_area"):
            if r["lat"] is None or r["lon"] is None:
                continue
            if _haversine_m(lat, lon, r["lat"], r["lon"]) <= (r["radius_m"] or _radius_m(c)):
                if r["trust"] == "mute":
                    muted.add(r["scientific_name"])
                elif r["trust"] == "auto":
                    auto.add(r["scientific_name"])
    seen_q = ("SELECT scientific_name, "
              "SUM(status='confirmed') AS conf, SUM(status!='rejected') AS vis "
              "FROM browser_detections WHERE location_id=?" + _bird_clause(include_nonbird))
    seen_p: list = [location_id]
    if device:
        seen_q += " AND device_token=?"
        seen_p.append(device)
    seen = c.execute(seen_q + " GROUP BY scientific_name", seen_p).fetchall()
    out: dict[str, str] = {}
    for r in seen:
        sci = r["scientific_name"]
        if sci in muted:
            out[sci] = "rejected"
        elif sci in auto or (r["conf"] or 0) > 0:
            out[sci] = "confirmed"
        elif (r["vis"] or 0) == 0:
            out[sci] = "rejected"   # heard, but every detection was dismissed
        else:
            out[sci] = "suggested"
    return out


def location_species(location_id: int, include_nonbird: bool = False,
                     status: str | None = None, device: str | None = None) -> list[dict]:
    """Species heard at a location, each in exactly one trust bucket. `status`:
    None/'active' = confirmed + suggested (the default working view), or one of
    confirmed/suggested/rejected, or 'all'."""
    want = status if status in ("confirmed", "suggested", "rejected", "all") else "active"
    new_cutoff = datetime.now().astimezone() - timedelta(seconds=NEW_WINDOW_S)
    with _conn() as c:
        trust = _species_trust(c, location_id, include_nonbird, device)
        auto_set = {r["scientific_name"] for r in c.execute(
            "SELECT DISTINCT scientific_name FROM species_area WHERE trust='auto'").fetchall()}
        # Rejected species count every detection (so the rejected tab shows the
        # full tally); confirmed/suggested species count only what's still standing.
        rejected_scis = {s for s, st in trust.items() if st == "rejected"}
        rows_q = ("SELECT ts, scientific_name, common_name, confidence, status "
                  "FROM browser_detections WHERE location_id=?" + _bird_clause(include_nonbird))
        rows_p: list = [location_id]
        if device:
            rows_q += " AND device_token=?"
            rows_p.append(device)
        rows = c.execute(rows_q + " ORDER BY ts", rows_p).fetchall()
    keep = [r for r in rows
            if r["scientific_name"] in rejected_scis or r["status"] != "rejected"]
    species = _aggregate_species(keep)
    for s in species:
        sci = s["scientific_name"]
        s["status"] = trust.get(sci, "suggested")
        # "New" is purely a time window: a species first heard at this location
        # within the last 24h. It is shared (derived from the detection data, not
        # per-person), starts the moment the bird first appears, and expires on its
        # own 24h later — no acknowledgement, no click, nothing the user does
        # changes it. Only badge it while it is still suggested (un-triaged).
        first_dt = _parse_ts(s.get("first_ts"))
        s["is_new"] = bool(first_dt and first_dt >= new_cutoff and s["status"] == "suggested")
        s["auto"] = sci in auto_set
        s.pop("first_ts", None)
    if want != "all":
        if want == "active":
            species = [s for s in species if s["status"] != "rejected"]
        else:
            species = [s for s in species if s["status"] == want]
    # Stable order: most-heard first. Status and "new" are shown as badges, NOT
    # used as sort keys, so confirming or expanding a row never makes it jump
    # position. (Things needing a look are found via the Suggested tab.)
    species.sort(key=lambda s: (-s["count"], s["scientific_name"]))
    return species


def location_activity(location_id: int, include_nonbird: bool = False,
                      status: str | None = None, device: str | None = None) -> dict:
    # Activity charts show everything heard, not the list's status tab.
    rows = _loc_rows(location_id, include_nonbird, "_visible", device)
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
            "first_ts": a["first_ts"],
            "last_date": _local(a["last_ts"]).strftime("%Y-%m-%d"),
        })
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


def _shannon(ctr: "Counter") -> float:
    """Shannon diversity H = -Σ p·ln p over species in this bucket. Higher = more
    species AND more even. 0 when one species dominates or only one is present."""
    tot = sum(ctr.values())
    if tot <= 0:
        return 0.0
    return round(-sum((n / tot) * math.log(n / tot) for n in ctr.values() if n), 3)


def _aggregate_activity(rows: list[sqlite3.Row]) -> dict:
    from collections import Counter
    daily: dict[str, dict] = {}
    hourly = {h: 0 for h in range(24)}
    hourly_ctr: list[Counter] = [Counter() for _ in range(24)]  # all-days per-hour species counts
    for r in rows:
        lt = _local(r["ts"])
        d = lt.strftime("%Y-%m-%d")
        sp = r["scientific_name"]
        day = daily.setdefault(d, {"date": d, "count": 0, "_species": set(), "_win": set(),
                                   "hourly": [0] * 24, "_hctr": [Counter() for _ in range(24)]})
        day["count"] += 1
        day["_species"].add(sp)
        # Presence = distinct 15-min windows with >=1 detection (0..95 within a day).
        day["_win"].add(lt.hour * 4 + lt.minute // 15)
        day["hourly"][lt.hour] += 1
        day["_hctr"][lt.hour][sp] += 1
        hourly[lt.hour] += 1
        hourly_ctr[lt.hour][sp] += 1
    return {
        # Each day carries its own 24h histogram so the "Times of day" view can
        # show a specific day; the top-level hourly is the all-days aggregate.
        # Per hour: `species` = distinct-species richness, `shannon` = evenness-
        # weighted diversity (the day-chart's Diversity overlay).
        "daily": [{"date": d["date"], "count": d["count"], "species": len(d["_species"]),
                   "windows": len(d["_win"]),
                   "hourly": d["hourly"],
                   "hourly_species": [len(c) for c in d["_hctr"]],
                   "hourly_shannon": [_shannon(c) for c in d["_hctr"]]}
                  for d in sorted(daily.values(), key=lambda x: x["date"])],
        "hourly": [{"hour": h, "count": hourly[h], "species": len(hourly_ctr[h]),
                    "shannon": _shannon(hourly_ctr[h])} for h in range(24)],
    }


def _parse_utc(iso) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _light_phase(astro: dict | None, now_utc: datetime) -> str:
    """Coarse light phase from the sun times: night / dawn / midday / dusk / day.
    Birds are most vocal at dawn (the chorus) and again near dusk; quiet at night
    and in the midday lull — this drives the activity outlook's baseline."""
    if not astro:
        return "day"
    cd, sr = _parse_utc(astro.get("civil_dawn")), _parse_utc(astro.get("sunrise"))
    ss, cdu = _parse_utc(astro.get("sunset")), _parse_utc(astro.get("civil_dusk"))
    sn = _parse_utc(astro.get("solar_noon"))
    if cd and cdu and (now_utc < cd or now_utc > cdu):
        return "night"
    if cd and sr and cd <= now_utc <= sr + timedelta(hours=1.5):
        return "dawn"
    if ss and cdu and ss - timedelta(hours=1) <= now_utc <= cdu:
        return "dusk"
    if sn and abs((now_utc - sn).total_seconds()) <= 2 * 3600:
        return "midday"
    return "day"


def _pressure_delta(weather: dict | None, recent: list, hours: int) -> float | None:
    """hPa change over the last `hours`: current pressure minus the reading from
    ~`hours` ago (nearest within 90 min). Negative = falling = front likely
    incoming, the strongest documented driver of a pre-front activity bump."""
    if not weather or weather.get("pressure_msl") is None:
        return None
    anchor = _parse_utc(weather.get("ts_hour"))
    if anchor is None:
        return None
    target = anchor - timedelta(hours=hours)
    best, bestd = None, 5400.0  # 90-min tolerance
    for r in recent:
        p, t = r.get("pressure_msl"), _parse_utc(r.get("ts_hour"))
        if p is None or t is None:
            continue
        d = abs((t - target).total_seconds())
        if d <= bestd:
            best, bestd = p, d
    return None if best is None else round(weather["pressure_msl"] - best, 1)


def _activity_outlook(weather: dict | None, recent: list, future: list,
                      astro: dict | None, now_utc: datetime) -> dict:
    """A qualitative bird-activity outlook from current + incoming conditions, using
    documented general patterns (NOT a model fit to our thin history): dawn/dusk
    peaks, midday/night lulls, falling pressure → pre-front foraging, high wind and
    rain suppress song. Framed as tendencies, never a guarantee."""
    phase = _light_phase(astro, now_utc)
    dp3 = _pressure_delta(weather, recent, 3)
    dp6 = _pressure_delta(weather, recent, 6)
    wind = (weather or {}).get("wind_speed") or 0      # km/h
    gust = (weather or {}).get("wind_gust") or 0
    precip = (weather or {}).get("precip") or 0        # mm
    windy = wind >= 25 or gust >= 40
    raining = precip >= 0.1
    falling = (dp3 is not None and dp3 <= -1.0) or (dp6 is not None and dp6 <= -2.5)

    # Precedence: suppressors (rain, wind, night) first, then boosters (dawn,
    # falling pressure, dusk), then the midday lull, else normal.
    if raining:
        level, reason = "subdued", "Rain tends to quiet singing and foraging."
    elif windy:
        level, reason = "subdued", "Gusty wind suppresses song and activity."
    elif phase == "night":
        level, reason = "low", "Night, when most songbirds are roosting."
    elif phase == "dawn":
        level = "elevated"
        reason = "Dawn chorus, the day's peak singing" + (", boosted by falling pressure." if falling else ".")
    elif falling:
        level, reason = "elevated", "Pressure is falling, so birds often forage actively ahead of a change."
    elif phase == "dusk":
        level, reason = "elevated", "Dusk, a secondary activity peak."
    elif phase == "midday":
        level, reason = "subdued", "Midday lull, when activity usually dips at the warmest hours."
    else:
        level, reason = "normal", "Typical activity for the conditions."

    incoming = None
    if weather and weather.get("pressure_msl") is not None and future:
        fps = [r["pressure_msl"] for r in future if r.get("pressure_msl") is not None]
        fpp = [r.get("precip_prob") or 0 for r in future]
        if fps and (weather["pressure_msl"] - min(fps)) >= 3.0:
            incoming = "Pressure drops over the next several hours, so a change is moving in. Activity often rises just before it."
        elif fpp and max(fpp) >= 60:
            incoming = "Rain is likely later, so expect a quieter spell once it arrives."

    return {"level": level, "reason": reason, "incoming": incoming, "phase": phase,
            "pressure_hpa": round(weather["pressure_msl"], 1) if weather and weather.get("pressure_msl") is not None else None,
            "pressure_trend_3h": dp3,
            "wind_mph": round(wind * 0.621371) if wind else 0}


def birds_now(location_id: int) -> dict:
    """The 'Right now' snapshot for a location: light (sun/moon/twilight), current
    weather, today's early bird, and a descriptive what's-likely-now list.

    The forecast is deliberately descriptive, not predictive: 'often heard around
    this hour' = species ranked by how often we've actually detected them in this
    hour-of-day bucket (±1h). With little history it's weak and says so (n shown);
    it sharpens as detections accumulate. No baseline/pattern claim is made."""
    try:
        from services import environment as _env  # lazy: avoid import cycle at load
    except Exception:  # pragma: no cover
        _env = None
    now = datetime.now(timezone.utc)
    local_now = now.astimezone()
    today = local_now.strftime("%Y-%m-%d")
    hour = local_now.hour

    astro = _env.get_astro(location_id, today) if _env else None
    weather, recent, future = None, [], []
    if _env:
        # A window straddling now: recent hours feed the pressure trend, the next
        # ~13h feed the incoming-change note.
        conds = _env.get_conditions(
            location_id, (now - timedelta(hours=8)).isoformat(), (now + timedelta(hours=13)).isoformat())
        for r in conds:
            t = _parse_utc(r.get("ts_hour"))
            (recent if (t and t <= now) else future).append(r)
        weather = recent[-1] if recent else (conds[0] if conds else None)
    forecast = _activity_outlook(weather, recent, future, astro, now)

    dawn_utc = None
    if astro and astro.get("civil_dawn"):
        try:
            dawn_utc = datetime.fromisoformat(astro["civil_dawn"])
        except ValueError:
            dawn_utc = None

    # Today's early bird: first bird heard today, after civil dawn if we know it
    # (so a pre-dawn owl/noise doesn't steal the title).
    early = None
    with _conn() as c:
        for r in c.execute(
            "SELECT scientific_name, common_name, ts FROM browser_detections "
            "WHERE location_id=? AND is_bird=1 ORDER BY ts", (location_id,)):
            lt = _local(r["ts"])
            if lt.strftime("%Y-%m-%d") != today:
                continue
            tutc = datetime.fromisoformat(r["ts"])
            if tutc.tzinfo is None:
                tutc = tutc.replace(tzinfo=timezone.utc)
            if dawn_utc is None or tutc >= dawn_utc:
                early = {"sci": r["scientific_name"], "common": r["common_name"], "ts": r["ts"]}
                break

    # Tier 1: conditional species ranking for the current context + day-shape +
    # calibration (and log this hour's prediction for later scoring).
    try:
        from services import birds_forecast as _fc  # lazy: avoid import cycle at load
        wbucket = _fc._weather_bucket(weather)
        tier1 = _fc.forecast(location_id, forecast["phase"], wbucket, hour, today)
    except Exception:  # forecast enrichment must never break the snapshot
        tier1 = {"likely": [], "match": "none", "confidence": "none", "n_context": 0,
                 "day_shape": None, "calibration": {"graded_hours": 0, "precision_at_k": None, "status": "measuring"}}

    return {"hour": hour, "today": today, "astro": astro, "weather": weather,
            "forecast": forecast, "early_bird": early,
            "likely": tier1["likely"], "likely_match": tier1["match"],
            "likely_confidence": tier1["confidence"], "likely_n": tier1["n_context"],
            "day_shape": tier1["day_shape"], "calibration": tier1["calibration"]}


def bird_detail(sci: str, location_id: int | None = None,
                device: str | None = None, days: int = 0,
                status: str | None = None) -> dict:
    """Everything for one species' page: stats, per-day breakdown (each day with
    its own 24h histogram for the mini-graph), where + on what it was heard, and
    a recent list. Filterable by location, device, date range, and trust status;
    the location and device option lists are always the full set for the species."""
    # Fetch every detection of the species (range + confidence floor only), then
    # split in Python: the charts/stats use everything HEARD here (non-rejected,
    # NOT subject to the status tab), while the detection list below is filtered by
    # the page's own status control. `device`/`location_id` scope both.
    flt = _bird_clause(False)
    vis_sql, vis_args = _visible_clause(None)  # request-scoped viewer enforced on every query below
    q = ("SELECT id, ts, common_name, confidence, location_id, device_token, device_name, "
         "occurrence, in_range, status FROM browser_detections WHERE scientific_name=?" + flt + vis_sql)
    params: list = [sci, *vis_args]
    if location_id:
        q += " AND location_id=?"
        params.append(location_id)
    if device:
        q += " AND device_token=?"
        params.append(device)
    with _conn() as c:
        rows = c.execute(q + " ORDER BY ts", params).fetchall()
        loc_names = {r["id"]: r["name"] for r in c.execute("SELECT id,name FROM locations")}
        # Option lists span every place/device the species was HEARD (non-rejected),
        # so switching filters always offers them all.
        all_locs = c.execute(
            "SELECT location_id, COUNT(*) n FROM browser_detections "
            "WHERE scientific_name=? AND location_id IS NOT NULL AND status!='rejected'" + flt + vis_sql +
            " GROUP BY location_id ORDER BY n DESC", (sci, *vis_args)).fetchall()
        all_devs = c.execute(
            "SELECT device_token, MAX(device_name) name, COUNT(*) n FROM browser_detections "
            "WHERE scientific_name=? AND device_token IS NOT NULL AND device_token<>'' AND status!='rejected'" + flt + vis_sql +
            " GROUP BY device_token ORDER BY n DESC", (sci, *vis_args)).fetchall()
        rec_map = {r["det_id"]: r["id"] for r in c.execute(
            "SELECT id, det_id FROM recordings WHERE scientific_name=?", (sci,)).fetchall()}
        sr = c.execute("SELECT save_mode FROM species_recording WHERE scientific_name=?",
                       (sci,)).fetchone()

    # Date range: days<=0 is all-time; days=1 is today (local calendar day);
    # days=N is the last N local days.
    cutoff = None
    if days and days > 0:
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        cutoff = start.astimezone(timezone.utc)

    def _after_cutoff(r: sqlite3.Row) -> bool:
        if cutoff is None:
            return True
        dt = datetime.fromisoformat(r["ts"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff

    in_range = [r for r in rows if _after_cutoff(r)]
    # Charts + stats: everything heard (drop dismissed false positives only).
    heard = [r for r in in_range if r["status"] != "rejected"]
    # Detection list: filtered by the page's status control (default = all).
    if status in ("confirmed", "suggested", "rejected"):
        listed = [r for r in in_range if r["status"] == status]
    else:
        listed = in_range

    by_day: dict[str, dict] = {}
    hourly_overall = [0] * 24
    for r in heard:
        lt = _local(r["ts"])
        d = lt.strftime("%Y-%m-%d")
        day = by_day.setdefault(d, {"date": d, "count": 0, "hourly": [0] * 24})
        day["count"] += 1
        day["hourly"][lt.hour] += 1
        hourly_overall[lt.hour] += 1

    confs = [r["confidence"] for r in heard]
    occs = [r["occurrence"] for r in heard if r["occurrence"] is not None]
    common = (heard[-1]["common_name"] if heard else (rows[-1]["common_name"] if rows else sci))
    return {
        "scientific_name": sci, "common_name": common, "count": len(heard),
        "first_date": min(by_day) if by_day else None,
        "last_date": max(by_day) if by_day else None,
        "days_seen": len(by_day),
        "max_conf": round(max(confs), 4) if confs else 0,
        "avg_conf": round(sum(confs) / len(confs), 4) if confs else 0,
        # eBird occurrence prior (likelihood here/this-week); null if every kept
        # detection predates location-aware analysis.
        "occurrence": round(max(occs), 4) if occs else None,
        "hourly": [{"hour": h, "count": hourly_overall[h]} for h in range(24)],
        "by_day": sorted(by_day.values(), key=lambda x: x["date"], reverse=True),
        "total_all": sum(L["n"] for L in all_locs),
        "locations": [{"id": L["location_id"], "name": loc_names.get(L["location_id"]), "count": L["n"]}
                      for L in all_locs],
        "devices": [{"token": D["device_token"], "name": D["name"], "count": D["n"]}
                    for D in all_devs],
        "save_mode": sr["save_mode"] if (sr and sr["save_mode"] in _SAVE_MODES) else "default",
        "clip_count": len(rec_map),
        "list_count": len(listed),
        "recent": [{"id": r["id"], "at": _fmt_dt(r["ts"], with_secs=True),
                    "confidence": round(r["confidence"], 4),
                    "occurrence": r["occurrence"], "status": r["status"],
                    "location_id": r["location_id"],
                    "location": loc_names.get(r["location_id"]),
                    "device_token": r["device_token"],
                    "recording_id": rec_map.get(r["id"]),
                    "device": r["device_name"]} for r in listed[-100:][::-1]],
    }
