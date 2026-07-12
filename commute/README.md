# commute (Workshop app)

Commute planning + live status. Schedule-driven; absorbs location signals
(OwnTracks / wearables → derived current leg) to score late-risk.

This app is **the source of truth for commute data** in Workshop. Other Workshop
apps may consume it via the standard backend routes — that consumption is an
app-level setting on the consumer, not a feature of this module.

> **Per-operator data lives in `.runtime/`, never here.** The real schedule,
> legs, targets, live-source registry, and generated notes (home/work locations,
> source credentials, the alert channel id) are instance state under
> `.runtime/commute-config/` and `.runtime/commute-notes/` (gitignored). This
> directory holds only generic structure + docs, so a fresh clone ships no one's
> addresses. See `docs/apps-are-pluggable.md`.

## Design decisions

| Q | Decision |
|---|---|
| Notification channel | A dedicated alert channel (id configured in instance state). Alerts go there. |
| Target arrival + flex | 15-min flex window. **Alert only when at risk of being LATE.** Never alert when running early. |
| Backup route research | Only in true breakdown / emergency. Not routine. |
| PWA vs Workshop page | Lives inside Workshop. Workshop owns the data; other apps consume via app-setting. |

## Layout

```
commute/                          — tracked: generic structure + docs only
├── README.md                     — this file
└── state/                        — runtime state dir (gitkept; contents gitignored)
    └── .gitkeep

.runtime/commute-config/          — instance state (gitignored):
├── schedule.json                 — the operator's stated schedule (morning + evening)
├── legs.json                     — discrete legs of each direction
├── targets.json                  — target arrivals + 15-min flex window
└── sources/registry.json         — live-data source registry (transit, traffic)

.runtime/commute-notes/           — LLM-written pattern observations as they accrue
```

## Config shape (per instance, under `.runtime/commute-config/`)

- **schedule.json** — the daily schedule: a morning and an evening block, each a
  home → work (and reverse) sequence of legs (drive → transit → walk), with
  named origin/destination places and target times.
- **legs.json** — discrete legs of each direction, referenced by the schedule.
- **targets.json** — arrival targets + a 15-min flex window + alert policy.
- **sources/registry.json** — the live-data source registry that a generic
  source-poller drives (transit GTFS-RT feeds, traffic).

## Notification policy

- Trigger: predicted arrival > target + 15 min flex, **and** current-leg
  confidence is high (not from stale location).
- Cooldown: never more than one late-risk alert per leg per day.
- Never: early-arrival pings, "you're on time" pings, low-confidence speculation.

## Reuse / Workshop conventions

- Schedule + legs use the same shape as a general "timed multi-step plan" so future
  Workshop apps (errands, travel) can reuse the type.
- Data sources go through a shared `sources/registry.json` shape so a generic
  source-poller component can drive transit, traffic, and future feeds uniformly.
- Notification dispatch should go through Workshop's notification layer when it
  lands (not a commute-private webhook), so policy and channel mapping stay central.
