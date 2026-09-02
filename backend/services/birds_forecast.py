"""Bird forecast engine. A layered model that degrades gracefully by data volume:

- Tier 0 (expert rules): the activity outlook lives in birds_analyze (_activity_outlook),
  driven by light phase + pressure trend + wind/rain. Works at zero data.
- Tier 1 (this module): empirical conditional rates. For the current context (light
  phase + coarse weather bucket) rank species by how often we've actually detected
  them in matching contexts, with empirical-Bayes shrinkage toward each species'
  overall share so thin cells don't produce nonsense. Plus a day-shape forecast
  (expected hourly profile vs today's actual) and per-species best-time.
- Calibration: every next-hour prediction is logged and later scored against what
  actually happened, so the engine measures its own accuracy. A species/feature set
  only earns a heavier model (Tier 2, later) once it beats the rules on held-out days.

Everything here is programmatic and inspectable. A trained model (Tier 2) and an LLM
explainer (Tier 3) slot in on top of these tables once there's volume to justify them;
they are deliberately NOT faked while data is thin.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

# From birds_db, not birds_analyze: these are the store primitives, and
# importing them from the analysis module made the two a cycle (2026-09-02).
from services.birds_db import _conn, _local, _shannon

# How the qualitative activity outlook nudges expected VOLUME (not the species mix):
# a falling-pressure "elevated" day runs busier, a wet "subdued" day quieter. Modest
# on purpose; it's a tilt on the historical baseline, not a fitted multiplier.
_LEVEL_MULT = {"elevated": 1.15, "normal": 1.0, "subdued": 0.8, "low": 0.5}

# Shrinkage strength: a context needs ~this many observations before its own rates
# outweigh the species' overall share. Keeps thin-data forecasts honest.
_ALPHA = 20.0
# Below this many detections in a context, relax the match (drop the weather bucket,
# then fall back to the overall prior) rather than rank on noise.
_MIN_CTX = 12


def _env():
    try:
        from services import environment as e
        return e
    except Exception:  # pragma: no cover
        return None


# ---- context features ------------------------------------------------------

def _temp_class(t):
    if t is None:
        return "?"
    return "cool" if t < 10 else "mild" if t < 20 else "warm"  # °C


def _wind_class(w):
    if w is None:
        return "?"
    return "calm" if w < 15 else "breezy" if w < 30 else "windy"  # km/h


def _sky_class(cloud, precip):
    if (precip or 0) >= 0.1:
        return "wet"
    if cloud is None:
        return "?"
    return "clear" if cloud < 30 else "cloudy"


def _weather_bucket(cond: dict | None) -> str:
    if not cond:
        return "unknown"
    return f"{_temp_class(cond.get('temp'))}/{_wind_class(cond.get('wind_speed'))}/{_sky_class(cond.get('cloud_total'), cond.get('precip'))}"


def _hour_floor_iso(ts_utc: datetime) -> str:
    return ts_utc.replace(minute=0, second=0, microsecond=0).isoformat()


def _load_env_context(location_id: int):
    """Conditions keyed by floored-hour ISO, and astro keyed by date, for joining
    each historical detection to the light + weather it happened under."""
    e = _env()
    conds: dict[str, dict] = {}
    astro: dict[str, dict] = {}
    if not e:
        return conds, astro
    rows = e.get_conditions(location_id, "2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    for r in rows:
        try:
            t = datetime.fromisoformat(r["ts_hour"])
        except (ValueError, TypeError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        conds[_hour_floor_iso(t)] = r
    return conds, astro


def _astro_for(location_id: int, date_str: str, cache: dict):
    if date_str in cache:
        return cache[date_str]
    e = _env()
    a = e.get_astro(location_id, date_str) if e else None
    cache[date_str] = a
    return a


def _detection_contexts(location_id: int):
    """One row per bird detection with its (species, light_phase, weather_bucket,
    local_hour). The join that the whole conditional model rests on."""
    from services.birds_db import _light_phase
    conds, _ = _load_env_context(location_id)
    acache: dict = {}
    out = []
    with _conn() as c:
        for r in c.execute(
            "SELECT scientific_name, common_name, ts FROM browser_detections "
            "WHERE location_id=? AND is_bird=1", (location_id,)):
            ts = r["ts"]
            try:
                tutc = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if tutc.tzinfo is None:
                tutc = tutc.replace(tzinfo=timezone.utc)
            lt = _local(ts)
            astro = _astro_for(location_id, lt.strftime("%Y-%m-%d"), acache)
            lp = _light_phase(astro, tutc)
            wb = _weather_bucket(conds.get(_hour_floor_iso(tutc)))
            out.append((r["scientific_name"], r["common_name"], lp, wb, lt.hour))
    return out


# ---- Tier 1: conditional species scoring -----------------------------------

def conditional_scores(location_id: int, light_phase: str, weather_bucket: str, k: int = 6) -> dict:
    """Rank species for the current context with empirical-Bayes shrinkage. Falls
    back from (light phase + weather) to (light phase) to (overall) as data thins,
    reporting which match level was used and how many observations backed it."""
    ctx = _detection_contexts(location_id)
    overall: Counter = Counter()
    names: dict = {}
    for sci, common, lp, wb, hour in ctx:
        overall[sci] += 1
        names[sci] = common
    n_total = sum(overall.values())
    if n_total == 0:
        return {"species": [], "n_context": 0, "match": "none", "confidence": "none"}

    def count_where(pred):
        ic: Counter = Counter()
        for sci, common, lp, wb, hour in ctx:
            if pred(lp, wb):
                ic[sci] += 1
        return ic

    in_ctx = count_where(lambda lp, wb: lp == light_phase and wb == weather_bucket)
    match = "light+weather"
    if sum(in_ctx.values()) < _MIN_CTX:
        in_ctx = count_where(lambda lp, wb: lp == light_phase)
        match = "light"
    if sum(in_ctx.values()) < _MIN_CTX:
        in_ctx = overall
        match = "overall"
    n_ctx = sum(in_ctx.values())

    scores = []
    for sci, tot in overall.items():
        prior = tot / n_total
        n = in_ctx.get(sci, 0)
        score = (n + _ALPHA * prior) / (n_ctx + _ALPHA)
        scores.append({"sci": sci, "common": names[sci], "score": round(score, 4), "n": n})
    scores.sort(key=lambda x: -x["score"])
    # Confidence reflects how much real signal (vs prior) backs this context.
    confidence = "established" if n_ctx >= 30 else "preliminary" if n_ctx >= 10 else "descriptive"
    return {"species": scores[:k], "n_context": n_ctx, "match": match, "confidence": confidence}


def day_shape(location_id: int, date_str: str) -> dict:
    """The 'today' forecast: expected detections per local hour (mean over the days
    we have) and today's actual so far, so the card can show the predicted shape
    with reality filling in over it."""
    per_day_hour: dict[str, Counter] = defaultdict(Counter)
    today_hour: Counter = Counter()
    with _conn() as c:
        for r in c.execute(
            "SELECT ts FROM browser_detections WHERE location_id=? AND is_bird=1", (location_id,)):
            lt = _local(r["ts"])
            d = lt.strftime("%Y-%m-%d")
            per_day_hour[d][lt.hour] += 1
            if d == date_str:
                today_hour[lt.hour] += 1
    ndays = len([d for d in per_day_hour if d != date_str]) or len(per_day_hour) or 1
    expected = []
    for h in range(24):
        tot = sum(per_day_hour[d][h] for d in per_day_hour if d != date_str)
        expected.append(round(tot / ndays, 2))
    if not any(expected):  # only today's data exists yet
        expected = [round(today_hour[h], 2) for h in range(24)]
    return {"expected": expected, "actual": [today_hour[h] for h in range(24)],
            "days": ndays, "peak_hours": [h for h in range(24) if expected[h] >= 0.6 * (max(expected) or 1) and expected[h] > 0]}


def best_time(location_id: int, sci: str) -> dict:
    """When/under what this species is most often heard: top hours + the light phase
    and weather bucket it favours. Descriptive, sample-size aware."""
    ctx = [row for row in _detection_contexts(location_id) if row[0] == sci]
    if not ctx:
        return {"sci": sci, "n": 0, "hours": [], "light": None, "weather": None}
    hours = Counter(r[4] for r in ctx)
    lps = Counter(r[2] for r in ctx)
    wbs = Counter(r[3] for r in ctx)
    return {"sci": sci, "n": len(ctx),
            "hours": [h for h, _ in hours.most_common(3)],
            "light": lps.most_common(1)[0][0], "weather": wbs.most_common(1)[0][0]}


# ---- calibration: the self-measuring loop ----------------------------------

def _ensure_log(c):
    c.execute("""CREATE TABLE IF NOT EXISTS forecast_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        location_id INTEGER, made_at TEXT, target_hour TEXT,
        predicted TEXT, match TEXT, n_context INTEGER,
        scored INTEGER DEFAULT 0, hit REAL, actual_n INTEGER, scored_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_fl_target ON forecast_log(location_id, target_hour)")


def log_prediction(location_id: int, target_hour_iso: str, predicted: list[str],
                   match: str, n_context: int) -> None:
    """Record the top-k species predicted for an upcoming local hour, once per hour
    per location (idempotent), so it can be scored after the hour passes."""
    import json
    with _conn() as c:
        _ensure_log(c)
        exists = c.execute("SELECT 1 FROM forecast_log WHERE location_id=? AND target_hour=?",
                           (location_id, target_hour_iso)).fetchone()
        if exists:
            return
        c.execute("INSERT INTO forecast_log(location_id,made_at,target_hour,predicted,match,n_context) "
                  "VALUES(?,?,?,?,?,?)",
                  (location_id, datetime.now(timezone.utc).isoformat(), target_hour_iso,
                   json.dumps(predicted), match, n_context))


def score_due(location_id: int | None = None) -> int:
    """Score every logged prediction whose target hour has passed: precision@k of the
    predicted species against species actually detected that local hour. Hours with
    no detections are skipped (nothing to predict), not counted as misses. Returns
    the number newly scored. Safe to run nightly."""
    import json
    now_local = datetime.now(timezone.utc).astimezone()
    scored = 0
    with _conn() as c:
        _ensure_log(c)
        q = "SELECT id, location_id, target_hour, predicted FROM forecast_log WHERE scored=0"
        params: tuple = ()
        if location_id is not None:
            q += " AND location_id=?"
            params = (location_id,)
        rows = c.execute(q, params).fetchall()
        for r in rows:
            try:
                th = datetime.fromisoformat(r["target_hour"])
            except (ValueError, TypeError):
                continue
            # only score once the target local hour is fully in the past
            if th.tzinfo is None:
                th = th.astimezone()
            if th + timedelta(hours=1) > now_local:
                continue
            day = th.strftime("%Y-%m-%d")
            hour = th.hour
            actual = set()
            for d in c.execute(
                "SELECT scientific_name, ts FROM browser_detections "
                "WHERE location_id=? AND is_bird=1", (r["location_id"],)):
                lt = _local(d["ts"])
                if lt.strftime("%Y-%m-%d") == day and lt.hour == hour:
                    actual.add(d["scientific_name"])
            if not actual:
                # nothing happened that hour; mark scored-but-null so we don't retry forever
                c.execute("UPDATE forecast_log SET scored=1, hit=NULL, actual_n=0, scored_at=? WHERE id=?",
                          (datetime.now(timezone.utc).isoformat(), r["id"]))
                scored += 1
                continue
            pred = json.loads(r["predicted"] or "[]")
            hit = (len([s for s in pred if s in actual]) / len(pred)) if pred else 0.0
            c.execute("UPDATE forecast_log SET scored=1, hit=?, actual_n=?, scored_at=? WHERE id=?",
                      (round(hit, 3), len(actual), datetime.now(timezone.utc).isoformat(), r["id"]))
            scored += 1
    return scored


def accuracy(location_id: int) -> dict:
    """Aggregate calibration: mean precision@k over scored hours that had activity,
    and how many such hours we've graded. This is the trust signal the UI shows."""
    with _conn() as c:
        _ensure_log(c)
        rows = c.execute(
            "SELECT hit FROM forecast_log WHERE location_id=? AND scored=1 AND hit IS NOT NULL",
            (location_id,)).fetchall()
    n = len(rows)
    mean = round(sum(r["hit"] for r in rows) / n, 3) if n else None
    return {"graded_hours": n, "precision_at_k": mean,
            "status": "measuring" if n < 20 else "established"}


def bird_profiles(location_id: int) -> dict:
    """The yard's typical bird day, learned from history: expected detections per
    hour (activity) and Shannon diversity per hour, plus per-day means. These are
    the bird-side of the forecast; weather only tilts the volume on top of them."""
    from collections import Counter, defaultdict
    per_day_hour: dict[str, Counter] = defaultdict(Counter)
    hour_species: dict[int, Counter] = defaultdict(Counter)
    day_species: dict[str, set] = defaultdict(set)
    with _conn() as c:
        for r in c.execute(
            "SELECT scientific_name, ts FROM browser_detections WHERE location_id=? AND is_bird=1",
            (location_id,)):
            lt = _local(r["ts"])
            d, h = lt.strftime("%Y-%m-%d"), lt.hour
            per_day_hour[d][h] += 1
            hour_species[h][r["scientific_name"]] += 1
            day_species[d].add(r["scientific_name"])
    ndays = len(per_day_hour) or 1
    activity = [round(sum(per_day_hour[d][h] for d in per_day_hour) / ndays, 2) for h in range(24)]
    diversity = [_shannon(hour_species[h]) for h in range(24)]
    mean_species = round(sum(len(s) for s in day_species.values()) / ndays, 1) if day_species else 0.0
    return {"activity": activity, "diversity": diversity,
            "mean_species_per_day": mean_species, "mean_detections_per_day": round(sum(activity), 1),
            "days": ndays, "peak_hours": [h for h in range(24)
                                          if activity[h] >= 0.6 * (max(activity) or 1) and activity[h] > 0]}


def _day_outlook(day_conds: list) -> dict:
    """A day-level activity outlook from that day's forecast weather. Dawn is always
    the day's peak, so this captures how the day as a whole tilts: rain or high wind
    suppress, a notable pressure fall lifts (pre-front foraging), strong high pressure
    reads as settled fair weather."""
    if not day_conds:
        return {"level": "normal", "reason": "Forecast not available yet for this day."}
    precip_tot = sum((r.get("precip") or 0) for r in day_conds)
    pprob_max = max((r.get("precip_prob") or 0) for r in day_conds)
    wind_max = max((r.get("wind_speed") or 0) for r in day_conds)
    ps = [r.get("pressure_msl") for r in day_conds if r.get("pressure_msl") is not None]
    p_trend = round(ps[-1] - ps[0], 1) if len(ps) >= 2 else None

    if precip_tot >= 1.0 or pprob_max >= 60:
        return {"level": "subdued", "reason": "Rain expected, which tends to quiet singing and foraging."}
    if wind_max >= 35:
        return {"level": "subdued", "reason": "Windy, which tends to suppress song and activity."}
    if p_trend is not None and p_trend <= -3.0:
        return {"level": "elevated", "reason": "Pressure falling through the day, so birds often forage actively ahead of the change."}
    if p_trend is not None and p_trend >= 3.0:
        return {"level": "normal", "reason": "Pressure rising into settled fair weather."}
    return {"level": "normal", "reason": "Typical activity expected for the conditions."}


def forecast_week(location_id: int, days: int = 7) -> dict:
    """A per-day forecast for the next `days` days: each day's light (sun/moon/day
    length), forecast weather summary, and a day-level activity outlook, plus the
    week's usual regulars and the shared expected daily shape. Honest about thin
    data: the per-day differences are real (weather, day length, moon), while the
    species list is the same residents until weather-conditioned history exists."""
    from services.birds_db import _light_phase
    e = _env()
    if not e:
        return {"days": [], "regulars": [], "expected_shape": None}
    loc = next((L for L in e._locations() if L["id"] == location_id), None)

    now_local = datetime.now(timezone.utc).astimezone()
    today = now_local.date()

    prof = bird_profiles(location_id)  # the yard's typical bird day (activity + diversity)

    # Group all available conditions by local date for quick per-day slicing.
    by_date: dict[str, list] = defaultdict(list)
    conds = e.get_conditions(location_id, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                             (datetime.now(timezone.utc) + timedelta(days=days + 1)).isoformat())
    for r in conds:
        by_date[_local(r["ts_hour"]).strftime("%Y-%m-%d")].append(r)

    out_days = []
    for i in range(days):
        d = today + timedelta(days=i)
        ds = d.isoformat()
        astro = e.astro_for(loc["lat"], loc["lon"], d) if loc else None
        dc = sorted(by_date.get(ds, []), key=lambda r: r["ts_hour"])
        temps = [r["temp"] for r in dc if r.get("temp") is not None]
        wcodes = [r["wx_code"] for r in dc if r.get("wx_code") is not None]
        hourly = [{"hour": _local(r["ts_hour"]).hour,
                   "temp_f": round(r["temp"] * 9 / 5 + 32) if r.get("temp") is not None else None,
                   "precip_prob": r.get("precip_prob"), "wx_code": r.get("wx_code")}
                  for r in dc]
        ol = _day_outlook(dc)
        out_days.append({
            "date": ds,
            "weekday": d.strftime("%a"),
            "is_today": i == 0,
            "astro": {"sunrise": astro and astro.get("sunrise"), "sunset": astro and astro.get("sunset"),
                      "day_length_min": astro and astro.get("day_length_min"),
                      "moon_name": astro and astro.get("moon_name"),
                      "moon_illum": astro and astro.get("moon_illum")} if astro else None,
            "weather": {
                "temp_hi_f": round(max(temps) * 9 / 5 + 32) if temps else None,
                "temp_lo_f": round(min(temps) * 9 / 5 + 32) if temps else None,
                "wx_code": Counter(wcodes).most_common(1)[0][0] if wcodes else None,
                "wind_max": round(max((r.get("wind_speed") or 0) for r in dc) * 0.621371) if dc else None,
                "precip_prob_max": max((r.get("precip_prob") or 0) for r in dc) if dc else None,
            },
            "outlook": ol,
            # Bird-side forecast for the day: the historical volume tilted by the
            # outlook, the resident species count, and the day's peak window.
            "expected_detections": round(prof["mean_detections_per_day"] * _LEVEL_MULT.get(ol["level"], 1.0)),
            "expected_species": prof["mean_species_per_day"],
            "peak_hours": prof["peak_hours"],
            "hourly": hourly,
            "has_weather": bool(dc),
        })

    # Week's usual regulars: the dawn conditional ranking (same residents most days
    # until weather-conditioned history can differentiate them).
    reg = conditional_scores(location_id, "dawn", "unknown", k=5)
    return {"days": out_days, "regulars": reg["species"],
            "regulars_confidence": reg["confidence"],
            "profiles": {"activity": prof["activity"], "diversity": prof["diversity"]},
            "mean_species_per_day": prof["mean_species_per_day"],
            "history_days": prof["days"]}


def forecast(location_id: int, light_phase: str, weather_bucket: str, hour: int,
             date_str: str) -> dict:
    """The Tier 1 bundle the API serves: conditional species ranking for now, the
    day-shape, and current calibration. Also logs this hour's prediction for later
    scoring (the self-measuring loop)."""
    scores = conditional_scores(location_id, light_phase, weather_bucket)
    shape = day_shape(location_id, date_str)
    acc = accuracy(location_id)
    # Log the prediction for the CURRENT local hour, once.
    now_local = datetime.now(timezone.utc).astimezone().replace(minute=0, second=0, microsecond=0)
    try:
        log_prediction(location_id, now_local.isoformat(),
                       [s["sci"] for s in scores["species"][:4]], scores["match"], scores["n_context"])
    except Exception:  # logging must never break the forecast
        pass
    return {"likely": scores["species"], "match": scores["match"],
            "confidence": scores["confidence"], "n_context": scores["n_context"],
            "day_shape": shape, "calibration": acc}
