# commute (Workshop app)

Commute planning + live status for benmiriello. Schedule-driven; will absorb location
signals later (Apple Health/wearables → custom tracker if needed).

This app is **the source of truth for commute data** in Workshop. Other Workshop apps
may consume it via the standard backend routes — that consumption is an app-level
setting on the consumer, not a feature of this module.

## Decisions (resolved 2026-05-21)

| Q | Decision |
|---|---|
| Notification channel | Dedicated Discord commute channel (chat_id `1507029806909362237`). Alerts go here. |
| Use this channel for alerts | Yes. |
| Target arrival + flex | Yes, 15-min flex window. **Alert only when at risk of being LATE.** Never alert when running early. |
| Backup route research | Only in true breakdown / emergency. Not routine. |
| PWA vs Workshop page | Lives inside Workshop. Workshop owns the data; other apps consume via app-setting. |

## Layout

```
commute/
├── README.md                  — this file
├── schedule.json              — operator's stated schedule (morning + evening)
├── legs.json                  — discrete legs of each direction
├── targets.json               — target arrivals + 15-min flex window
├── sources/                   — live-data source registry (Caltrain, Muni, traffic)
│   └── registry.json
├── state/                     — runtime state (last_known_position, current_leg, recent_delays)
│   └── .gitkeep
└── notes/                     — LLM-written pattern observations as they accrue
    └── .gitkeep
```

## Default schedule (from operator statement)

- **AM**: drive 1849 S Springer Rd → Mountain View Caltrain → Caltrain (express ~7:33) → SF 4th & King → T train → Chinatown → walk to 650 California Ave
- **PM**: reverse, departing work ~18:00

## Phases

- **Phase 0 (now)**: file scaffolding, decisions captured, schedule seeded.
- **Phase 1**: backend route `commute/` in FastAPI; surface schedule/legs/targets via API. Add Caltrain GTFS-RT poller. UI page in frontend.
- **Phase 2**: Muni GTFS-RT poller; Workshop notification surface → Discord webhook to commute channel for late-risk only.
- **Phase 3**: Location ingest (Health → Open Wearables → derived current_leg). Pattern learning notes accumulate in `notes/`.
- **Phase 4**: Backup route research engine (emergency-only).
- **Phase 5**: Map UI (later).

## Notification policy

- Trigger: predicted arrival > target + 15min flex, **and** current_leg confidence is high (not from stale location).
- Channel: Discord, chat_id `1507029806909362237`.
- Cooldown: never more than one late-risk alert per leg per day.
- Never: early-arrival pings, "you're on time" pings, low-confidence speculation.

## Reuse / Workshop conventions

- Schedule + legs use the same shape as a general "timed multi-step plan" so future
  Workshop apps (errands, travel) can reuse the type.
- Data sources go through a shared `sources/registry.json` shape so a generic
  source-poller component can drive Caltrain, Muni, traffic, future feeds uniformly.
- Notification dispatch should go through Workshop's notification layer when it lands
  (not a commute-private Discord webhook), so policy and channel mapping stay central.
