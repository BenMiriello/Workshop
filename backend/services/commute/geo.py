"""Shared geometry + time helpers for the adaptive commute layer.

Kept dependency-free (stdlib only) so segmentation/places/matching can be unit
tested without the rest of the backend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Any, Iterable


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two lat/lon points."""
    R = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * asin(sqrt(a))


def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 string (with trailing Z or offset) to an aware UTC
    datetime. Returns None if unparseable. Epoch ints/floats also accepted."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Point:
    """One location ping, normalised. Immutable-ish value object."""

    __slots__ = ("ts", "lat", "lon", "speed", "accuracy", "raw")

    def __init__(self, ts: datetime, lat: float, lon: float,
                 speed: float | None = None, accuracy: float | None = None,
                 raw: dict[str, Any] | None = None) -> None:
        self.ts = ts
        self.lat = lat
        self.lon = lon
        self.speed = speed          # m/s, may be None (computed downstream)
        self.accuracy = accuracy    # m, may be None
        self.raw = raw or {}

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Point({self.ts.isoformat()}, {self.lat:.5f}, {self.lon:.5f}, v={self.speed})"


def to_points(records: Iterable[dict[str, Any]]) -> list[Point]:
    """Normalise raw location records (the location.jsonl schema: ts/lat/lng,
    optional speed_mps/accuracy_m) into time-sorted Points. Records missing a
    parseable ts or coords are dropped. Sorted ascending by time."""
    pts: list[Point] = []
    for r in records:
        ts = parse_ts(r.get("ts") or r.get("client_ts"))
        if ts is None:
            continue
        lat = r.get("lat")
        lon = r.get("lng", r.get("lon"))
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            continue
        speed = r.get("speed_mps")
        try:
            speed = float(speed) if speed is not None else None
        except (TypeError, ValueError):
            speed = None
        acc = r.get("accuracy_m")
        try:
            acc = float(acc) if acc is not None else None
        except (TypeError, ValueError):
            acc = None
        pts.append(Point(ts, lat, lon, speed, acc, r))
    pts.sort(key=lambda p: p.ts)
    return pts


def fill_speeds(points: list[Point]) -> None:
    """Fill missing per-point speed (m/s) from consecutive haversine/dt. The
    first point inherits the second's speed. Mutates in place. A device-reported
    speed (speed_mps) is trusted when present."""
    n = len(points)
    for i in range(n):
        if points[i].speed is not None:
            continue
        if i == 0:
            points[i].speed = 0.0
            continue
        dt = (points[i].ts - points[i - 1].ts).total_seconds()
        if dt <= 0:
            points[i].speed = points[i - 1].speed or 0.0
            continue
        d = haversine_m(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon)
        points[i].speed = d / dt
    # First point: borrow the second's, so a leading point isn't always 0.
    if n >= 2 and (points[0].raw.get("speed_mps") is None):
        points[0].speed = points[1].speed


def centroid(points: list[Point]) -> tuple[float, float]:
    """Mean lat/lon of a set of points (good enough at city scale)."""
    if not points:
        return (0.0, 0.0)
    return (sum(p.lat for p in points) / len(points),
            sum(p.lon for p in points) / len(points))
