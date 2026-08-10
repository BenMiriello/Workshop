#!/usr/bin/env python3
"""Seed a fixture BirdNET-Go SQLite DB so the Workshop app, sanitizer, and event
bridge can be exercised before the hardware exists.

Creates a `notes` table matching BirdNET-Go's GORM model and fills it with
plausible detections across several weeks and species, with a few species whose
first appearance is recent (to exercise the recent-arrivals / first-seen paths).

  python3 seed_fixture_db.py                 # -> $BIRDNET_DB or default path
  python3 seed_fixture_db.py /tmp/test.db    # explicit path

The real BirdNET-Go will create/own this file on the Brain; this is ONLY a dev
fixture. It is overwritten if it already exists.
"""
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta

# Default output is a THROWAWAY path, never the live DB. A fixture must never be
# able to masquerade as real detections in the app. To test the UI against it,
# point the backend at this file explicitly: BIRDNET_DB=/tmp/birdnet-fixture.db
# Pass an explicit path as argv[1] only if you really mean to.
DB = sys.argv[1] if len(sys.argv) > 1 else "/tmp/birdnet-fixture.db"

# (scientific, common, species_code, base_confidence, calls_per_active_day, dawn_bias)
SPECIES = [
    ("Cardinalis cardinalis", "Northern Cardinal", "norcar", 0.82, 14, 0.7),
    ("Cyanocitta cristata", "Blue Jay", "blujay", 0.78, 10, 0.5),
    ("Zenaida macroura", "Mourning Dove", "moudov", 0.71, 8, 0.6),
    ("Poecile atricapillus", "Black-capped Chickadee", "bkcchi", 0.85, 12, 0.6),
    ("Turdus migratorius", "American Robin", "amerob", 0.80, 11, 0.8),
    ("Spinus tristis", "American Goldfinch", "amegfi", 0.76, 7, 0.4),
    ("Sitta carolinensis", "White-breasted Nuthatch", "whbnut", 0.74, 6, 0.5),
    ("Melospiza melodia", "Song Sparrow", "sonspa", 0.69, 9, 0.7),
    ("Dryobates pubescens", "Downy Woodpecker", "dowwoo", 0.77, 5, 0.5),
    ("Baeolophus bicolor", "Tufted Titmouse", "tuftit", 0.79, 8, 0.6),
    # Recent arrivals (first seen only in the last few days):
    ("Setophaga coronata", "Yellow-rumped Warbler", "yerwar", 0.68, 6, 0.6),
    ("Archilochus colubris", "Ruby-throated Hummingbird", "rthhum", 0.66, 4, 0.5),
]


def main():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB)
    # Column set + order mirrors BirdNET-Go's GORM Note model (snake_case).
    c.execute("""
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_node TEXT,
            date TEXT, time TEXT,
            begin_time DATETIME, end_time DATETIME,
            species_code TEXT, scientific_name TEXT, common_name TEXT,
            confidence REAL, latitude REAL, longitude REAL,
            threshold REAL, sensitivity REAL,
            clip_name TEXT, processing_time INTEGER, unlikely INTEGER DEFAULT 0
        )""")
    c.execute("CREATE INDEX idx_notes_date ON notes(date)")
    c.execute("CREATE INDEX idx_notes_sciname ON notes(scientific_name)")

    rng = random.Random(1707)  # deterministic fixture
    today = datetime.now().date()
    rows = []
    DAYS = 75
    # Fake but non-real coordinates to prove the sanitizer strips them.
    LAT, LON = 40.0000, -105.0000

    for d in range(DAYS, -1, -1):
        day = today - timedelta(days=d)
        ds = day.strftime("%Y-%m-%d")
        for sci, common, code, base, rate, dawn in SPECIES:
            # Recent arrivals: skip days older than their introduction.
            if code == "yerwar" and d > 5:
                continue
            if code == "rthhum" and d > 2:
                continue
            n = max(0, int(rng.gauss(rate, rate * 0.4)))
            for _ in range(n):
                # Diel pattern: most calls near dawn (5-9), tail through the day.
                if rng.random() < dawn:
                    hour = rng.choice([5, 6, 6, 7, 7, 8, 9])
                else:
                    hour = rng.randint(6, 19)
                minute, sec = rng.randint(0, 59), rng.randint(0, 59)
                ts = datetime(day.year, day.month, day.day, hour, minute, sec)
                conf = min(0.99, max(0.10, rng.gauss(base, 0.08)))
                clip = f"clips/{ds}/{code}_{hour:02d}{minute:02d}{sec:02d}.wav"
                rows.append((
                    "birdpi", ds, ts.strftime("%H:%M:%S"),
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                    (ts + timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S"),
                    code, sci, common, round(conf, 4), LAT, LON,
                    0.3, 1.0, clip, rng.randint(20, 120), 0,
                ))

    c.executemany(
        "INSERT INTO notes (source_node,date,time,begin_time,end_time,species_code,"
        "scientific_name,common_name,confidence,latitude,longitude,threshold,"
        "sensitivity,clip_name,processing_time,unlikely) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    sp = c.execute("SELECT COUNT(DISTINCT scientific_name) FROM notes").fetchone()[0]
    c.close()
    print(f"seeded {DB}: {n} detections across {sp} species over {DAYS} days")


if __name__ == "__main__":
    main()
