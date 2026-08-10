# Commute v2 — adaptive, self-learning commute intelligence

Status: APPROVED to build fully (operator, 2026-06-13). Implement all of it, then commit.
This doc is the durable spec — if context is lost mid-build, resume from here.

## Prime directive: ZERO hardcoding of the user's commute

Nothing about *how the operator commutes* is written into config or code. The system
LEARNS, from observed phone location + the live transit feeds:
- his significant **places** (home, work, anywhere he dwells) — by dwell clustering
- the **modes** he uses each leg (walk / drive / rail / bus / mixed) — by speed/heading + corridor/vehicle matching
- the **stops** he boards/alights — by where mode transitions happen near transit
- the **trains/vehicles** he actually catches — by matching his track to 511 VehiclePositions → trip/route
- the **transfers** he makes or misses — by arrival-vs-next-departure slack
- his **timing patterns** — depart-vs-target, leg-duration p50/p90, walking speed, just-miss rate, "express fades near the end → miss the connection → +35 min" chains, day-of-week differences
- his typical **drive time** — observed from journeys (adaptive even without Google traffic)

The ONLY configured things are genuine *preferences/goals*, not facts that can be observed:
the anchor goal ("be at work by 08:45", tolerance/flex). Everything else is derived and
refined continuously. `legs.json` stops being hand-authored; it becomes a *learned journey
profile* per (direction, day-type) that the system maintains.

## Reasoning model: Claude (operator choice)

The reasoning/interjection step uses **Claude**, under the subscription
`CLAUDE_CODE_OAUTH_TOKEN` (NEVER `ANTHROPIC_API_KEY` — hard rule). Mechanism: shell to the
`claude` CLI in headless print mode (`claude -p --output-format json`, token sourced from
`~/.config/clawdbot/auth.env`) OR a relay route that uses the OAuth token — pick whichever
the existing `/relay` supports for cloud at build time (inspect `routes/relay*`). Fallback
ladder: **Claude → local qwen2.5:32b (existing `/relay/local`) → deterministic template.**
Reasoner gets STRUCTURED EVIDENCE + data-quality notes; emits the message contract below.

## The communication contract (predictable shape, adaptive content)

One canonical message object, used by BOTH Discord and the app. This resolves
"predictable but adaptive": the SHAPE is fixed; the CONTENT is reasoned.

```
Message {
  id, ts, kind: "brief"|"alert"|"insight"|"nudge",
  severity: "ok"|"info"|"caution"|"urgent",
  headline,        # one line — the single most important thing
  detail,          # 1-2 lines: concrete times / route / what to do
  reasoning,       # WHY — the adaptive bit (shown for caution/urgent; collapsible in app)
  confidence: "high"|"medium"|"low",   # driven by data quality / coverage
  action?,         # optional imperative, e.g. "leave 2 min earlier"
  evidence,        # structured: leg actuals, live delays, transfer slack, location freshness, pattern stats used
  journey_ref?,    # link to the journey record this concerns
}
```
- Discord: headline + detail, plus reasoning when severity ≥ caution. (Same feel as today.)
- App: same object rendered as a feed (top card + expandable evidence + paginated history).

## Data spine (build order — the keystone)

### 1. Trip segmentation — `backend/services/commute/segment.py`
Input: `location.jsonl` over a window. Output: ordered legs
`{mode, start_ts, end_ts, start_place, end_place, dist_m, avg_speed, peak_speed, path[]}`.
Algorithm (heuristic, explainable — no ML needed):
- **Stay vs move**: dwell clustering — consecutive pings within R≈80 m for ≥ T≈4 min = a stay;
  otherwise a move. Stays bound the trip and become place candidates.
- **Mode within a move**: segment by speed/heading signature — walk (<2 m/s), road/transit
  (higher). Distinguish **rail vs road** by (a) matching the sub-track to a 511 VehiclePosition
  track that is co-located in space+time, and/or (b) corridor straightness/speed profile.
  A drive→park→walk→train→walk chain segments naturally; mixed modes are first-class.
- Handle gaps/stale pings explicitly → emit `data_quality` per leg (coverage %, max gap).

### 2. Place learning — `backend/services/commute/places.py`
Cluster stays across all history → recurring places. Auto-label by pattern (home = night
dwell; work = weekday-daytime dwell). Persist `places.json` (id, centroid, radius, label,
auto/over­ridden, stats). User can rename in UI. No coords hardcoded.

### 3. Vehicle-position history — extend `poller.py`
Currently VehiclePositions are fetched but only the latest snapshot is kept. Persist a
ROLLING history (`.runtime/commute/vehicles.jsonl`, trimmed to ~last 6 h) so a finished
leg can be matched to the vehicle that ran it. Use the consolidated **RG** feed (one request
covers all agencies) to conserve rate budget.

### 4. Transit matching — `backend/services/commute/transit_match.py`
Given a rail/bus leg, find the VehiclePosition track best matching it in space+time →
`trip_id`, `route_id` (route id is in the feed; no GTFS needed to name the route). Boarding
stop = nearest stop to leg start; alighting = nearest to leg end. GTFS static (stops/routes/
shapes) is OPTIONAL enrichment for human-readable stop names — fetch+cache from 511 GTFS if
egress allows (`gtfs.py`), degrade gracefully to "stop near {place}" if not.

### 5. StopMonitoring (per-stop predictions) — extend `poller.py`
Add a SIRI **StopMonitoring** poll for the LEARNED boarding stops (fed from places/matching,
NOT hardcoded). Gives expected departure at *his* stop — the right signal for "will he make
it." VehicleMonitoring optional for "train is N stops out." Poll only during active windows.

### 6. Transfer modeling — in `scoring.py` / `journeys.py`
Replace the 0-gap assumption. For each leg boundary at a transfer stop: connection slack =
(predicted/actual departure of next leg) − (arrival of this leg). Flag tight/likely-miss.
This is what enables "miss the 7:33 by a minute → express fades → miss the T → +35 min."

### 7. Journey records — `backend/services/commute/journeys.py` → `.runtime/commute/journeys.jsonl`
One record per commute, built incrementally during the window and finalized after:
`{date, direction, schedule_id, depart_detected_ts, legs[](segmented+matched), transfers[]
(made/missed/slack), arrive_ts, total_min, target vs actual, delays_encountered,
data_quality{coverage, gaps, stale}, partial:bool}`. The empirical truth everything learns from.
Don't finalize a confident journey when coverage < threshold → mark `partial`.

### 8. Patterns/stats — `backend/services/commute/patterns.py`
Derived from journeys (all empirical, queryable): depart-vs-target distribution, per-leg
duration p50/p90 by day-type, walking speed, drive-time distribution, transfer success rate,
just-miss frequency, "express fade" detection, recurring failure chains. Feeds the reasoner.

### 9. Reasoner — `backend/services/commute/reason.py` (replaces/extends `llm.py`)
Claude (ladder above). Input = live brief + relevant patterns + data_quality. Output = the
message contract. System prompt enforces: be concise; reason WITH uncertainty (say "signal
unclear" rather than inventing a conclusion from thin data); only surface what's worth saying;
prefer actionable. Validate JSON against the contract; fallback on any failure.

### 10. Message store — `backend/services/commute/messages.py` → `.runtime/commute/messages.jsonl`
Append canonical messages; `GET /commute/messages?limit&offset`. The existing alert-log
becomes a subset; migrate dispatch to write the canonical object.

## Surfaces

### Discord (keep current cadence + delivery)
`commute_tick.sh` unchanged in spirit: poll → reason → render the message object → send /queue
to `the notify queue`. Reports keep coming through Discord as they do now.

### App "Now / Suggestions" feed — `frontend/src/pages/apps/commute/*`
New primary surface (the current Alerts tab is just a dispatch table). Reuse design-system
primitives + birds patterns (`NowCard` hierarchy, `SpeciesRow` chevron-expand):
- **Top card**: latest/most-important message — big headline + severity `StatusPill` + detail +
  reasoning, expandable (chevron) to show `evidence`.
- **History**: paginated (`Table`/`TableScroll`, `?limit&offset`), each row expandable.
- Live commute stays on Status; this feed is the message stream. Four states via `StateBlock`.
- Optional: wire existing `ChatPanel` with a "commute" context provider → ask "why did I miss
  the train Tuesday?" against journey records. (Build if time; not required for v1.)

### Drive leg + Google Maps (Phase 4, optional layer)
Drive time is LEARNED from journeys already (adaptive without Google). If/when the operator
sets a Google key, add live traffic-aware duration as an enrichment, not a dependency.
Reads `GOOGLE_MAPS_API_KEY` from `~/.config/clawdbot/google-maps.env` (file-based, 0600).

## Rate-limit plan (511: 60 req / 3600 s)
Use RG consolidated feeds (tripupdates/vehiclepositions/servicealerts = 3 req per poll). During
active windows poll every 5 min (12/hr × 3 = 36/hr) + StopMonitoring (~12/hr) ≈ 48/hr < 60.
Outside windows, back off hard. GTFS static fetched at most daily.

## Auth / secrets
- Claude: `CLAUDE_CODE_OAUTH_TOKEN` via `~/.config/clawdbot/auth.env`. NEVER `ANTHROPIC_API_KEY`.
- 511: `WORKSHOP_511_API_KEY` (already in backend env).
- Google Maps: `~/.config/clawdbot/google-maps.env` (operator sets up; see setup notes).
- Journey/location data stays in `.runtime/` (private). No new external sharing.

## Testing
- Unit: segmentation on synthetic tracks (walk→drive→rail→walk), transfer slack, patterns math,
  message-contract validation, reasoner fallback ladder, journey finalize/partial.
- Replay: feed recorded `location.jsonl` + a `vehicles.jsonl` fixture → assert journeys + matched
  trains. Backend `pytest` green; frontend `tsc --noEmit` + `check_design.sh` clean.
- Live smoke: one poll cycle; render a message in the app; one Discord dispatch via queue.

## Single-user note
No multi-tenant guardrails. This is the operator's private commute. (See user-scope CLAUDE.md.)
