"""Empirical patterns derived from journey history — the learned model.

Everything here is computed from observed journeys, never configured: how the
user actually moves (per-mode leg minutes, walking speed, drive time), how
punctual they are vs their target, and how reliable their transfers are
(tight/long-wait/missed-connection signatures). The reasoner consumes
`summarize_for_reasoner()` as structured evidence.
"""
from __future__ import annotations

from typing import Any

from . import journeys as journeys_mod


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return round(s[0], 1)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return round(s[lo] * (1 - frac) + s[hi] * frac, 1)


def _stats(values: list[float]) -> dict[str, Any]:
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "p50": _pct(vals, 0.5), "p90": _pct(vals, 0.9),
            "min": round(min(vals), 1), "max": round(max(vals), 1),
            "mean": round(sum(vals) / len(vals), 1)}


def compute(window: int = 60) -> dict[str, Any]:
    """Aggregate the last `window` journeys into per-direction empirical stats."""
    js = [j for j in journeys_mod.recent(window) if not j.get("partial")]
    out: dict[str, Any] = {"journeys_used": len(js), "directions": {}}
    by_dir: dict[str, list[dict[str, Any]]] = {}
    for j in js:
        by_dir.setdefault(j.get("direction") or "unknown", []).append(j)

    for direction, group in by_dir.items():
        totals = [j["total_min"] for j in group if j.get("total_min") is not None]
        late = [j["late_by_min"] for j in group if j.get("late_by_min") is not None]
        # per-mode leg minutes
        mode_min: dict[str, list[float]] = {}
        walk_speeds: list[float] = []
        for j in group:
            for leg in j.get("legs", []):
                mode_min.setdefault(leg["mode"], []).append(leg.get("minutes", 0))
                if leg["mode"] == "walk" and leg.get("avg_speed_mps"):
                    walk_speeds.append(leg["avg_speed_mps"])
        # transfer reliability
        n_transfers = tight = long_wait = 0
        for j in group:
            for tr in j.get("transfers", []):
                if tr["from_mode"] in ("rail", "transit") or tr["to_mode"] in ("rail", "transit"):
                    n_transfers += 1
                    tight += 1 if tr.get("tight") else 0
                    long_wait += 1 if tr.get("long_wait") else 0
        out["directions"][direction] = {
            "n": len(group),
            "total_min": _stats(totals),
            "late_by_min": _stats(late),
            "leg_minutes_by_mode": {m: _stats(v) for m, v in mode_min.items()},
            "walk_speed_mps": _stats(walk_speeds),
            "transfers": {
                "observed": n_transfers,
                "tight": tight,
                "long_wait": long_wait,
                "miss_rate": round(long_wait / n_transfers, 2) if n_transfers else None,
            },
        }
    return out


def summarize_for_reasoner(direction: str | None) -> dict[str, Any]:
    """Compact evidence block for one direction (or all). Designed to be small
    enough to drop straight into the reasoner prompt."""
    allstats = compute()
    dirs = allstats["directions"]
    chosen = dirs.get(direction) if direction else None
    recent_js = journeys_mod.recent(5)
    anomalies = []
    for j in recent_js:
        for tr in j.get("transfers", []):
            if tr.get("long_wait"):
                anomalies.append({
                    "date": j.get("date"), "at": tr.get("at_place"),
                    "wait_min": round(tr["wait_s"] / 60.0, 1),
                    "note": f"long wait ({round(tr['wait_s']/60)}m) transferring {tr['from_mode']}→{tr['to_mode']}",
                })
        if j.get("late_by_min") is not None and j["late_by_min"] > 5:
            anomalies.append({"date": j.get("date"),
                              "note": f"arrived {j['late_by_min']}m past target"})
    return {
        "journeys_used": allstats["journeys_used"],
        "direction": direction,
        "stats": chosen,
        "all_directions": dirs if not chosen else None,
        "recent_anomalies": anomalies[:6],
        "recent_journeys": [
            {"date": j.get("date"), "direction": j.get("direction"),
             "depart_local": j.get("depart_local"), "arrive_local": j.get("arrive_local"),
             "total_min": j.get("total_min"), "late_by_min": j.get("late_by_min"),
             "modes": [leg["mode"] for leg in j.get("legs", [])],
             "partial": j.get("partial")}
            for j in recent_js
        ],
    }
