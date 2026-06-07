# Workshop App Platform — isolation, integration, location/map

Status: **proposal / for operator review** · Date: 2026-06-06 · Author: clawd-a (supervisor)

This is the plan for the architecture surfaced in the 2026-06-06 commute/location
session. It covers: per-app data isolation, admin-configured directional sharing,
the standalone-vs-integrated app model, "location as both an app and a tool," the
Protomaps PMTiles map, and the Commute in-workshop port. Nothing here is built yet
except where noted — it's the research + plan to review before we commit.

---

## 1. What you asked for

1. Apps must **not** auto-share data or access. Cross-app access is **opt-in**,
   **configured**, and **admin-only** — never granted by an app's own
   masini/bot/agent.
2. Sharing has **direction**: A→B, B→A, or both, decided per pair, per capability.
3. Every app should be buildable from **reusable primitives/templates** so it can
   run **standalone** (own host/port) *or* **integrated** in Workshop — even if many
   never go standalone.
4. A standalone app may embed chat, but when hosted/accessed separately it must be
   **hard-isolated** from other apps (the Birds app will be shared with friends; it
   must have zero path to Finance).
5. **Location** is both a viewable app *and* a capability other apps consume
   (Commute, Birds, …). Linkage between Location and consumers is admin-configured
   and directional.
6. Map of current + historical position (decided: **Protomaps PMTiles**).
7. Commute should be an **in-workshop app like the others**, not a separate URL.

> Note on the "no defensive cruft" rule: that rule forbids multi-tenant guardrails
> for the *single operator's own* use (allowlists/audit/mention-gating). This work
> is different and in-scope — it's real access control for **externally shared**
> apps (friends viewing Birds). Default-deny between apps is a correctness boundary,
> not single-user cruft.

---

## 2. Current state (and the bleed risk)

| App | Code | How it's served | Origin |
|---|---|---|---|
| Finance, Budget, Birds, Location, **Commute** | `frontend/src/pages/apps/*` | main Vite (`:5173` → TS Serve `:8443`) | **shared origin** |
| Wearables | external | `:8445` | own origin |

Backend: one FastAPI (`:8000` → `:8446`). Secure routers gate off-host callers via
`require_token`; **loopback is fully trusted** (`deps_auth.py`). There is **no
per-app authorization** — any in-workshop page can call any `/api/*` route. So today
Birds and Finance share an origin *and* an unrestricted backend. If Birds is exposed
to a friend on the shared origin, that friend's browser context can reach
`/api/finance/*`. **This is the core thing to fix before any external sharing.**

---

## 3. Design: app identity + capability grants

### 3.1 App manifest
Each app declares itself in one place (`apps.json`, backend-owned):

```jsonc
{
  "apps": {
    "finance":  { "title": "Finance",  "sensitivity": "private", "api_prefixes": ["/finance", "/budget", "/plaid"] },
    "location": { "title": "Location", "sensitivity": "private", "api_prefixes": ["/location"],
                  "provides": ["location.read"] },
    "commute":  { "title": "Commute",  "sensitivity": "private", "api_prefixes": ["/commute"],
                  "needs": ["location.read"] },
    "birds":    { "title": "Yard Birds","sensitivity": "shareable","api_prefixes": ["/birds"],
                  "provides": ["birds.read"], "needs": [] }
  }
}
```

- `api_prefixes` — the backend routes this app owns. Becomes the unit of authz.
- `provides` / `needs` — **capabilities** (typed, e.g. `location.read`), not raw routes.
- `sensitivity` — `private` (operator only) | `shareable` (may be exposed to guests).

### 3.2 Grants (the directional, admin-only part)
A separate `grants.json` (operator-edited only, in Settings → Platform):

```jsonc
{
  "grants": [
    { "from": "location", "to": "commute", "capability": "location.read", "mode": "read" }
  ]
}
```

- **Default-deny**: no grant ⇒ no access. Commute can read Location *only* because of
  this row. Removing it severs the link.
- **Directional**: this grants Commute→Location read; not the reverse. Bidirectional =
  two rows (or `"mode":"rw"` where it makes sense).
- **Admin-only**: `grants.json` is writable solely through an operator-authenticated
  Settings page. **No app, masini, or bot can mint a grant** — the write route requires
  the operator token and is never exposed to app-scoped callers. App-embedded chats get
  a read-only view of grants at most.

### 3.3 Enforcement (two layers)
1. **Backend authz middleware.** Every `/api/*` request resolves to an owning app via
   `api_prefixes`. The request must carry an **app scope** (which app's frontend it came
   from). A request scoped to `birds` hitting `/finance/*` is denied unless a grant says
   otherwise. Capability-typed routes (e.g. Commute reading location) check the grant
   table, not just the prefix. Loopback stays trusted for *operator* contexts (cron,
   masini) but app-origin requests are scoped.
2. **Origin isolation for shareable apps.** A `shareable` app exposed to guests must run
   on its **own origin** (own port / TS Serve mount), never the shared `:8443`. Same-origin
   is the leak; separate origin + scoped token is the fix. So Birds-for-friends = its own
   mount with a guest token that only unlocks `birds.*`.

---

## 4. Standalone vs integrated: one codebase, two mounts

Goal: write an app **once**, mount it either inside Workshop (a route) or standalone
(own Vite/host), with the same primitives and no copy-paste.

### 4.1 App package shape
```
apps/<name>/
  app.manifest.ts      # id, title, capabilities, routes — imported by both mounts
  src/                 # pages + components, built ONLY from shared primitives
  standalone.tsx       # entry for own-origin build (mounts <App/> + own ChatDock)
```
- **Shared primitives**: `frontend/src/ui` + `src/styles/tokens.*` already exist and are
  mandatory (`frontend/CLAUDE.md`). Promote them to a workspace package
  (`@workshop/ui`) so `apps/*` import the *same* components as the main frontend —
  today `apps/commute` re-implements styles inline, which is the divergence to kill.
- **Integrated mount**: main frontend lazy-imports `apps/<name>/src` at `/apps/<name>`.
- **Standalone mount**: `standalone.tsx` builds the same tree on its own origin.

### 4.2 Chat boundary
`ChatDock` + backend `chat_context.py` providers already exist. Rule: an app's chat may
access **only that app's** capabilities + whatever grants allow. A standalone Birds chat
can answer about birds, never about finance — enforced by the same scope/grant layer, so
the chat can't become a side-channel around isolation.

### 4.3 The Birds-for-friends path (concrete)
Birds is `shareable`. To share: build its standalone mount on its own origin with a guest
token scoped to `birds.read`. Finance/Location/Commute are `private` and never mounted on
that origin. Result: a friend's link reaches only `/api/birds/*`; there is no route, token,
or origin from which they can touch Finance.

---

## 5. Location: app + tool

Location is the first real capability **provider**.

- **As an app**: `frontend/src/pages/apps/Location.tsx` (exists) — viewer of own position +
  history. We add the map here.
- **As a tool**: it `provides: ["location.read"]`. Commute already reads
  `location.latest()` internally; under the new model that becomes a **granted**
  `location.read` consumption (Location→Commute), visible and revocable in Settings.

### 5.1 Map — Protomaps PMTiles (decided)
Why PMTiles: a single self-hosted basemap file, **no per-tile callouts to a third party**
— consistent with "all position data stays on the box." OSM/Carto tile CDNs would leak
*which area you're viewing* even though coords never leave; PMTiles avoids that.

Plan:
1. **Asset**: download a Bay Area extract from the Protomaps build service into
   `frontend/public/basemap/bayarea.pmtiles` (or serve via backend with HTTP range
   support). One file, refreshed manually/quarterly.
2. **Deps**: `maplibre-gl` + `pmtiles` (protocol shim). Wrap in a `Map` primitive added to
   `src/ui` (so other apps — Birds locations, future — reuse it; satisfies the
   "primitive, not one-off" rule).
3. **Render**: current-position marker + **breadcrumb polyline** from
   `/location/history` (endpoint already paginates). Time-range filter (today / 7d / all).
   Four list-states per the design system.
4. **Reuse**: Birds' per-location pages can mount the same `Map` primitive later.

Data layer is **already done** (`location.jsonl` append-only history + `/location/history`).
This is purely a rendering + asset task.

---

## 6. Commute → in-workshop port (first migration, do this first)

Today Commute is a separate origin (`:8444`) the launcher links out to. Two commute UIs
exist: the rich `apps/commute/CommutePages.tsx` (820 lines, just upgraded to the anchor
model) and a thin config-only `frontend/src/pages/Commute.tsx` that punts to the external app.

Target: Commute mounts at `/apps/commute` in the main frontend, design-system-compliant,
launcher card switched from external `href` to internal `to`. This is the **first instance**
of §4 (one codebase, integrated mount). Steps:
1. Promote `apps/commute/src` to import `@workshop/ui` primitives (replace inline styles +
   raw hex like `#16a34a`/`#f59e0b` → status tokens). `check_design.sh` must pass.
2. Lazy-route it at `/apps/commute`; redirect legacy `/commute` → `/apps/commute`.
3. Launcher: Commute card → internal route; keep `:8444` as the standalone mount (proves the
   two-mount model) or retire it.
4. Carry over the anchor-model schedule editor already written.

Estimate: focused half-day; it's a real design-system migration, not a copy. **Recommend
running this next, as its own pass**, rather than bundling it under other work.

---

## 7. Sequencing

- **Phase 0 (done this session)**: depart/arrival anchor model + tests; Location launcher
  card; frontend watchdog; Sources-tab bug fix.
- **Phase 1**: Commute in-workshop port (§6) + extract `@workshop/ui` workspace package.
- **Phase 2**: PMTiles `Map` primitive + Location map view (§5.1).
- **Phase 3**: app manifest + scope middleware + `grants.json` + Settings → Platform editor
  (§3). Default-deny turned on.
- **Phase 4**: Birds standalone mount on its own origin with guest scope (§4.3) — only after
  Phase 3 lands, since that's what makes external sharing safe.

Each phase is independently shippable and leaves the system working.

## 8. Open decisions for you
1. **Workspace tooling**: pnpm/npm workspaces for `@workshop/ui`, or a simpler path alias?
2. **App scope transport**: how an app-origin request proves its scope — signed cookie per
   mount vs. per-origin token. (Leaning per-origin token; simplest + matches §3.3.2.)
3. **Guest auth for shared Birds**: unguessable link, or a lightweight per-guest token you
   can revoke? (Revocable token recommended.)
4. **Keep or retire `:8444`** once Commute is integrated.
5. **Basemap scope**: Bay Area only, or wider (you travel)? Affects PMTiles file size.

---

## Status log

- **2026-06-06:** Phase 0 done. Phase 1 **complete** — Commute is fully integrated as a
  native Workshop page: its source moved to `frontend/src/pages/apps/commute/`, it uses
  the `@workshop/ui` primitives (PageHeader, Tabs, Stepper, AppGroupNav), and the
  standalone `:8444` PWA project (`apps/commute/`) + its `frontends_watchdog` entry +
  the `@commute` alias were **retired**. One codebase, one origin, like every other app.
  (If a phone-installable Commute PWA is wanted again, add it as a standalone mount of
  the same `frontend` tree rather than a forked project.)

### Phase 2 — PMTiles map: BLOCKED on operator-run setup (egress)

The map code is egress-free for me to write, but the two prerequisites are not:
the box's `egress-guard` hook blocks the shell from `registry.npmjs.org` and
`build.protomaps.com`. **Run these two from your own terminal**, then I'll write +
wire + verify the `Map` primitive and the Location-app map view:

```bash
# 1) Map libraries (frontend)
cd ~/workshop/frontend
npm install maplibre-gl pmtiles protomaps-themes-base

# 2) Bay Area basemap (single self-hosted file — no per-tile callouts).
#    Needs the go-pmtiles CLI: https://github.com/protomaps/go-pmtiles/releases
#    Extract a Peninsula+SF+East-Bay bbox from the daily planet build:
mkdir -p ~/workshop/frontend/public/basemap
pmtiles extract https://build.protomaps.com/$(date -u +%Y%m%d).pmtiles \
  ~/workshop/frontend/public/basemap/bayarea.pmtiles \
  --bbox=-122.6,37.2,-121.7,38.1
# (widen the bbox if you want more than the Bay Area — see open decision #5)
```

Once those exist, the implementation (mine, no egress needed) is:
- `frontend/src/ui/Map.tsx` — a `Map` primitive: registers the `pmtiles://` protocol,
  builds a MapLibre style from `protomaps-themes-base` pointed at the local
  `/basemap/bayarea.pmtiles`, exposes props for a current-position marker + a
  breadcrumb polyline + a time-range. Reused later by Birds location pages.
- `frontend/src/pages/apps/Location.tsx` — add the map above the existing history
  list, fed by `/location/latest` + `/location/history` (already exist); time-range
  filter (today / 7d / all). Four list-states.
- Glyphs (fonts): use protomaps' glyph CDN initially (generic font PBFs reveal no
  location); self-host later if we want zero external calls.
