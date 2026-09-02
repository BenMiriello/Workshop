"""This app's SQLite store and the primitives that read it.

SPLIT OUT OF birds_analyze.py on 2026-09-02 to break a real import cycle.
birds_forecast needed `_conn`, `_local`, `_shannon` and `_light_phase` from
birds_analyze and imported them at module scope; birds_analyze needed
`forecast` and `_weather_bucket` back from birds_forecast. Both directions
count, so the two modules were a 2-element strongly-connected component.

It was PRE-EXISTING and only became visible when the platform's app registry
stopped importing every app module: that shrank the backend's largest cycle
from 134 modules to 102 and left this pair standing on its own, moving
check_module_layout's scc_count from 1 to 2.

Nothing here changed behaviour. The schema, the DDL, the one-shot `_migrated`
flag and every helper are the same code, moved. birds_analyze re-imports all of
them, so every existing reference to `birds_analyze._conn` (and the rest) still
resolves; birds_forecast now imports them from here, and the arrow between the
two app modules points one way.
"""
from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from wsplatform import paths


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


BROWSER_DB = paths.env_path("BIRDNET_BROWSER_DB", "apps", "birds", "browser-stations.db")


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
    # Idempotent: only runs while the users table is empty, and only when this
    # install has said who the admin IS. No address is defaulted: a real one in
    # source would seed a stranger's database with the author's account.
    op_email = (os.environ.get("SPRINGER_ADMIN_EMAIL") or "").strip()
    if op_email and not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        op_name = os.environ.get("SPRINGER_ADMIN_NAME") or op_email.split("@")[0]
        c.execute("INSERT INTO users(id,email,name,role,created_at) VALUES(1,?,?,'admin',?)",
                  (op_email, op_name, _now()))
        c.execute("UPDATE devices SET owner_user_id=1 WHERE owner_user_id IS NULL")
        c.execute("UPDATE browser_detections SET owner_user_id=1 WHERE owner_user_id IS NULL")
    c.commit()
    _migrated = True
    return c


def _local(ts: str) -> datetime:
    """Parse a stored UTC ISO ts and convert to the box's local timezone (Pacific)
    so day/hour analytics line up with when the operator actually heard things."""
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _shannon(ctr: "Counter") -> float:
    """Shannon diversity H = -Σ p·ln p over species in this bucket. Higher = more
    species AND more even. 0 when one species dominates or only one is present."""
    tot = sum(ctr.values())
    if tot <= 0:
        return 0.0
    return round(-sum((n / tot) * math.log(n / tot) for n in ctr.values() if n), 3)


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
