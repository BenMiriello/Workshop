# Birds — Master Redesign Spec

> **Single source of truth for Claude Code.** This file integrates the three strands of the redesign worked out with the design team:
> 1. **Structure** — information architecture, navigation, page organization (web + mobile).
> 2. **Charts & data-viz** — every chart's encoding, presence-first.
> 3. **Aviary** — the ambient live view: what it is and **where it lives**.
>
> Companion files, all in this folder:
> - **`Charts Plan (Final).html`** — every decided chart, rendered live (visual reference).
> - **`CHARTS_SPEC.md`** — full per-chart build detail (the authority for §2; summarized here).
> - **`design_handoff_aviary/`** — the Aviary engine (`aviary-voxel.js`, `aviary.js`) + its README. **Reuse the engine; do not rebuild it.**
> - **`Birds App.html` + `app.js`** (via `Showcase.html`) — the interactive IA/layout prototype.
>
> This is a **design spec ported onto the real codebase** (`frontend/src/pages/apps/birds/*`) — not production code, not a full re-skin. Keep the existing component library, data layer, and routing; port *structure, encoding, and behavior*.

---

## 0. Two ideas govern everything

1. **Presence over chatter.** A bird that vocalizes constantly is not more significant than a quiet bird that's reliably around. The headline measure everywhere is **presence = the number of 15-minute windows a species was detected in** (≥1 detection counts once; no bonus for volume). Raw detections stay available but never dominate. This drives the Recent list, the day-over-day chart, the Atlas composition, and the Aviary's present-set.
2. **One question per surface.** Each page and each chart answers a single thing fast; everything else is layered or one tap away, never stacked on top.

---

## 1. Structure — IA & navigation

One responsive app, two chromes (unchanged from the structure prototype):

- **Web (≥920px):** persistent **left sidebar** — brand · primary nav (**Home · Birds · Forecast · Atlas**) · divider · manage (**Locations · Devices · Recordings · Settings**) · **Scope panel** (Location · Device · Time + Reset) · account pinned bottom. Multi-column content. A real web layout, not a widened mobile bar.
- **Mobile (<920px):** top filter bar (Location · Device · Time) + **bottom tabs** (Home · Birds · Forecast · Atlas · More). Recordings/Settings/Locations/Devices live under **More**.

**Global scope = Location · Device · Time.** Persistent, applied on every content page **including bird detail** (no local pickers anymore). Pickers = bottom sheet on mobile, centered modal on web.

**Defaults:** open scoped to the **home location + its always-on device** (Yard · Yard Mic) so live feed and "who's here now" are present immediately — never an ambiguous "all devices".

### Pages (decisions)
- **Home** = the settled truth for the current scope: **Live card** → **Recent** → **Activity** → **Forecast peek** → **Top birds** → at-a-glance stats. Confirmed-only; no status filter on Home, so nothing silently reshapes above the fold.
- **Recent** = presence, not chirp-count. Each species **once**, newest first, with latest time + **how many 15-min windows** it appeared in. Windowed 15m/1h/Today, paginated 10/page. Raw per-detection lists live on the bird page.
- **Birds** = the review surface. Confirmed / Suggested / Rejected segmented at top, default Confirmed, counts match list. The only place place-level status review happens.
- **Bird detail** — uses global scope (no local pickers). Confirmed activity chart; detection rows collapse to essentials, expand to per-event status + device; per-bird recording mode.
- **Forecast** — species-led: week chart + per-day detail + weather/light context (see §2).
- **Atlas** — embedding map for **labeling** (Overview ↔ By-species; listen + name-the-cluster) **plus** the new soundscape insight views (see §2).
- **Recordings** — storage meter + budget + per-clip pin/delete.
- **Settings** — account, detection accuracy, recording & Atlas budgets, non-bird display filter.
- **Locations & Devices** — minimal inline-edit lists (rename / address / set home / mode / share / remove). **No per-location/per-device dashboards** — viewing one = selecting it in the global scope.

### ⚠️ Placeholders in the structure prototype — do NOT regress the real app to match
- **Live histogram (Home live card):** the prototype's generic spectrogram is a **crude stand-in and worse than what exists.** Keep the **real production histogram** (slides left over time, per-species icon riding each detection). Adopt only the *container* changes: **Listen control + a single pulsing "Live" indicator inside the live-card header**, and **remove the duplicate top-bar recording pill + top-bar Listen button** (one recording affordance, not two).
- **Atlas map:** prototype's scatter of dots is fake — keep the real UMAP embedding; adopt the *organization* only.
- **Forecast copy/numbers, bird photos:** placeholders; wire to real model output / media.

---

## 2. Charts & data-viz  *(summary — full detail in `CHARTS_SPEC.md`, live reference in `Charts Plan (Final).html`)*

**Global chart conventions:** Lucide icons only · smoothed trend lines with **no point dots** · every series on a **labeled, color-matched axis** (nothing floats on a hidden scale) · plain labels ("Temperature" not "Weather", "species" not "spp") · the day/night **sky backdrop stays but is always paired with labeled Sunrise/Sunset markers** · descriptive copy, never prescriptive · forecasts drawn as a **range**, never false-precise.

| # | Surface | Component | Build |
|---|---|---|---|
| 2.1 | Activity · Times of day | `ActivityCard` | Bars = detections/hour + labeled sky backdrop + Sunrise/Sunset markers; Diversity (species/hr) and Temperature (°F) as toggleable overlays, **each on its own labeled axis**, no dots. *(Radial-clock & dawn-chorus-stream alternates explored, not selected.)* |
| 2.2 | Activity · Day over day | `ActivityCard` | One bar chart with a **Presence ⇄ Raw detections toggle**, default Presence, bars animate between states. (Replaces the rejected dotted overlay.) |
| 2.3 | Forecast · This week | `ForecastWeek` | Expected-detection bars; likely range as an **"I" bar** (capped top *and* bottom); species count above each bar = diversity read; weather/temp/rain in a gutter, off the activity axis. |
| 2.4 | Forecast · A day's shape | `ForecastWeek` / day detail | **Expected curve inside a shaded likely-range band**; rain on its own % axis behind it; sky backdrop + markers. (Band version chosen over plain bars.) |
| 2.5.1 | Atlas · soundscape | `Atlas` | **Presence ↔ chatter scatter** — x=presence, y=raw detections, dashed reference diagonal; over-talkers sit above, present-but-quiet below. (Chosen over composition bars.) |
| 2.5.2 | Atlas · soundscape | `Atlas` | **Call-type × hour heatmap** — rows=call type, cols=hour, intensity=frequency; reveals dawn chorus, contact hum, begging spike. |
| 2.5.3 | Atlas · soundscape | `Atlas` | **Acoustic-novelty timeline** — first appearances of species & call types over ~30 days; recent arrivals flagged. |

Prototyped as hand-built SVG (`charts-v2.js`, `charts-explore.js`) to pin encoding. In production reuse the app's `recharts` stack via `ui.tsx` (`CHART_SERIES`, `ChartTooltip`) where it expresses the spec cleanly; drop to custom SVG only for radial/heatmap/scatter/novelty forms. Respect `prefers-reduced-motion` (the day-over-day toggle reduces to an instant swap).

---

## 3. Aviary — the ambient live view

### What it is
A playful 2.5-D ("isometric voxel") scene of **who's in the yard right now**: each species heard in the rolling **last 15 minutes** flies in, pecks/hops while it keeps calling, and lifts off when it goes quiet. Tap a bird to identify it. It is an **alternate, ambient lens on the same live-detection data** the live card already uses — not a new data source.

The engine is **built and production-ready** (`design_handoff_aviary/`): `aviary-voxel.js` (renderer + `SPECIES` library) and `aviary.js` (scene engine + `window.Aviary` API). **Reuse them as-is; port only the chrome.** Wire to live data via:
```js
Aviary.simulate(false);                       // turn off the demo spawner
Aviary.setPresent(['house_finch', ...]);      // the rolling-15-min present-species set; engine reconciles in/out
```
Derive the present set from the same live source the live card polls (`LiveFeed.tsx` / `/live`): `is_bird` species heard within 15 min → map backend names to `AviaryVoxel.SPECIES` ids. Add unknown species to the `SPECIES` table (template + color pattern only — no geometry work).

### Where it lives  — **a lens on the live feed, not a nav destination**
The Aviary reads exactly the live card's data and is inherently **ambient**, so it belongs *with* the live feed, surfaced as an immersive view — **not** as a fifth primary nav item (nav stays Home · Birds · Forecast · Atlas).

1. **Primary entry — Home live card.** The live-card header gets a small **view control: `Histogram` (default) ⇄ `Aviary`**, plus an **expand-to-fullscreen** affordance. Histogram is the dense, settled read; Aviary is the ambient read of the same rolling window. Switching never changes scope or data — only the representation.
2. **Fullscreen / ambient ("cast") mode.** Expand opens the Aviary full-bleed with chrome minimized — designed to be **left running on a tablet, second screen, or TV**. Header keeps only: back/exit · location pill · live clock. Tap-to-exit. This is the Aviary's signature use.
3. **Quiet-yard empty state.** When nothing's been heard in 15 min, the Aviary shows a calm empty yard (a better idle than a flat-lined histogram) with a subtle "listening…" note.
4. **Scope coupling.** The Aviary requires a **live (always-on) device** in scope — same precondition as the live card. With the default Yard · Yard Mic scope it's always available; if scope has no live device, the view control is disabled with the existing "no always-on mic in scope" hint. Changing Location/Device repoints the Aviary like every other surface.
5. **Mobile.** Launched from the live card as a **fullscreen takeover** (not a bottom tab). The side panel ("In the yard now" list + scene controls) drops below the scene under 820px, per the engine's responsive layout.

> **Naming.** "Aviary" / "Live Aviary" is fine to keep — it reads as "your collection of birds, live." (If a change is ever wanted, "The Yard" / "Live Yard" maps more literally to the scene.) The name does not need to appear as its own nav label; it's the live card's expanded view.

### What to keep vs. adapt
- **Keep unchanged:** the voxel renderer, the `SPECIES` models, and the scene engine's behaviors (spawn/land/peck/hop/leave, spacing, tap-to-label, the `Aviary` API). This is the finished, hard part.
- **Adapt:** the surrounding chrome — rebuild the header/side-panel in the app's framework and tokens; keep the `#stage / #scenery / #field` scene structure the scripts expect (or rename and update the few `getElementById` calls in `aviary.js`). Fold the side panel's "In the yard now" list to share styling with Home's Recent list where sensible.

---

## 4. Build order (unified)

1. **Shell** — responsive sidebar (web) / top-bar + tabs (mobile); wire the persistent **Location · Device · Time** scope to existing state/query params.
2. **Home** — reorganize into the dashboard (§1); **integrate the real live histogram** into the new live-card header (Listen + Live inside; drop top-bar duplicates). Add the **`Histogram ⇄ Aviary` view control + expand** to the live card (§3).
3. **Recent** — presence list (15-min-window collapse, latest-time sort, pagination).
4. **Birds** — review surface (status segmented at top, Confirmed default).
5. **Bird detail** — global scope, expandable detection rows, per-bird recording mode.
6. **Charts** — implement §2 across `ActivityCard` / `ForecastWeek` over real model output: times-of-day (no dots), day-over-day presence toggle, week "I"-range, day-shape band.
7. **Atlas** — keep the embedding map for labeling; add the three soundscape insight views (§2.5).
8. **Aviary** — port the chrome around the reused engine; wire `Aviary.setPresent(...)` to the live source; fullscreen/cast mode + quiet-yard empty state (§3).
9. **Recordings & Settings**, then **Locations/Devices** minimal edit lists.

---

## 5. What changed from earlier rounds (correct prior code, don't re-add)
- "Weather" line → **Temperature**, labeled, own axis · diversity → **own labeled species/hr axis** (was floating) · unlabeled rainbow band → **labeled sky backdrop + Sunrise/Sunset markers** · trend lines → **no dots**.
- Day-over-day dotted detection line → **removed**, replaced by the **presence/detections toggle**.
- Week range → **"I" bar** (capped both ends), not top-only.
- "spp" → **"species"** · emoji/glyphs → **Lucide** · cute/prescriptive copy → **removed**.
- Aviary → positioned as the **live card's expanded ambient view**, not a standalone/extra nav section.
