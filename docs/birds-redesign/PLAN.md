# Birds Redesign — Plan of Action

> **Status: COMPLETE (2026-06-18).** All 8 phases shipped on branch `birds-redesign`,
> each `tsc` + `check_design` + production-build clean and visually verified.
> Backend restarted (presence-per-day `windows` field + `/atlas/insights`).
> Phase 8 (manage pages) needed no changes — already spec-compliant inline-edit
> lists, verified rendering in the new shell. The call-type×hour heatmap (6/5.2)
> stays hidden until call clusters are labeled (no fabricated data).

Derived from `BirdsRedesignCompleteHandoff.html` (the complete, self-contained handoff — the source of truth), cross-checked against `MASTER_SPEC.md` and `CHARTS_SPEC.md` (which are textual subsets of it) and the current code under `frontend/src/pages/apps/birds/`.

## Completeness check (the worry about a lost plan)

Nothing is lost. The complete handoff is the integrated superset: Part 1 structure/IA, Part 2 all six charts (rendered live), Part 3 Aviary placement, Part 4 build order. The only things the bundle carries that flat text can't are the *live interactive layout prototype* and the *live-rendered charts* — visual reference, not new decisions. Extracted readable copy: `HANDOFF.readable.txt`; extracted standalone HTML: `BirdsRedesignCompleteHandoff.extracted.html`.

Two "don't regress" details live ONLY in the handoff (§1.5), not in the `.md` summaries — carried into the global rules below.

## How to read the spec (literal vs. directional)

This is a **design spec ported onto the real codebase**, not a re-skin. Keep the existing component library, data layer, routing, recorder, UMAP, and Aviary engine. Where the spec is concrete (encodings, axis labels, toggles, placement) take it literally. Where it's directional or silent, keep what exists — absence in the spec is not a delete instruction unless it's an explicit decision ("removed", "do not ship", "not selected").

## Global rules / do-not-regress (apply to every phase)

- **Keep the real production histogram** = the scrolling spectrogram + per-species markers (`Spectrogram.tsx`, `recorder.tsx`). The prototype's plain spectrogram is the crude stand-in — ignore it.
- **Keep the real UMAP embedding** in Atlas (`Atlas.tsx` canvas), incl. Overview↔By-species, "new calls auto-join nearest group", zoom controls. Add insight views *alongside*, don't replace.
- **Keep the Aviary engine as-is** (`aviary/aviary-engine.js`, `aviary-voxel.js`, `species-na.js`). Port chrome only.
- **Diversity = Shannon H** in production charts (NOT species-count-per-hour, which is only the prototype's readable stand-in). Current `ActivityCard` already uses Shannon — keep it; just give it its own labeled axis.
- **Keep the chart engine's** All-days ↔ specific-day calendar scope, bar drill-down (tap a day → that day's hours), and `maxBarSize` (so 1–2-bar charts don't balloon).
- Single-bird activity chart **omits diversity** (meaningless for one species).
- Lucide icons only · no emoji · no inline hex (Workshop Design System is mandatory — `frontend/CLAUDE.md`).
- Trend lines smoothed, **no point dots** (current `dot={false}` + `monotone` already satisfies this).
- Every series on a **labeled, color-matched axis**; colors from `ui.tsx` tokens (`CHART_SERIES`), no new hues; temperature uses the existing warm/orange token.
- Forecasts drawn as a **range**, never false-precise.
- Respect `prefers-reduced-motion` (day-over-day toggle → instant swap).
- Plain labels: "Temperature" not "Weather", "species" not "spp".

## Current → target gaps (what actually changes)

| Area | Current | Target | Size |
|---|---|---|---|
| Nav | Single `BirdsHeader` inline link row: Home·Forecast·Atlas·Recordings·Settings | Web left sidebar + mobile top-bar/bottom-tabs; primary = Home·Birds·Forecast·Atlas; Manage group = Locations·Devices·Recordings·Settings; scope panel | Large |
| Scope | URL params, home-only; bird detail + others have local/none | One persistent Location·Device·Time scope on every page incl. bird detail; modal (web) / bottom-sheet (mobile) pickers | Large |
| Home | LivePanel/LiveFeed/YardLiveFeed/Aviary card + Dashboard(stats, NowCard, ActivityCard, species list) | Live card → Recent → Activity → Forecast peek → Top birds → stats; confirmed-only, no status filter | Medium |
| Recent | None (raw 8-item YardLiveFeed only) | Presence list: each species once, newest first, #15-min windows, 15m/1h/Today, 10/page | Medium |
| Birds review | Status machinery exists (`SpeciesRow` TrustStatus) but on home species list | Dedicated `/apps/birds/birds` page: Confirmed/Suggested/Rejected segmented top, default Confirmed | Small–Med |
| Live card chrome | Spectrogram + duplicate `RecorderBar` bottom pill + top Listen | Listen + single pulsing Live inside header; Histogram⇄Aviary toggle; expand; remove duplicate pill/Listen | Medium |
| Aviary | Wired to real live data already, but as a separate home card; no fullscreen/quiet state | Behind live-card Histogram⇄Aviary toggle; fullscreen/cast; quiet-yard empty state | Medium |
| Activity · Times of day | Bars + dawn/dusk markers + temp overlay; diversity on hidden axis; no sky backdrop | Add day/night sky backdrop (paired w/ Sunrise/Sunset markers); diversity → own labeled axis; rename Weather→Temperature | Medium |
| Activity · Day over day | Not present | New chart: Presence ⇄ Raw detections toggle (default Presence), animated | Medium |
| Forecast · Week | Bars + species line | Add "I" likely-range (capped both ends); species count above bars; weather gutter off-axis | Medium |
| Forecast · Day shape | Bars + diversity; rain in custom `TempSpark` | Expected curve inside shaded likely-range band; rain on own % axis; sky backdrop | Medium |
| Atlas insights | UMAP map only | Add 5.1 presence↔chatter scatter, 5.2 call-type×hour heatmap, 5.3 acoustic-novelty timeline | Large |

## Decisions (resolved by operator)

1. **Nav chrome = nested levels.** Build the *new* Birds nav system (web left-sidebar + mobile top-bar/bottom-tabs) by evolving `BirdsHeader`/`BIRDS_NAV` into the responsive chrome — single source of truth, no per-page nav.
   - **Embedded in Workshop:** the Workshop platform nav sits at the *outer* level (top); the new Birds nav nests *below* it.
   - **Standalone** (springer / birds.<your-domain>): the new Birds nav is the complete, self-sufficient chrome on its own (no Workshop nav present).
   - So the Birds shell must render correctly both with and without an enclosing Workshop chrome.
2. **Scope state location.** Birds-scoped React context (`BirdsScopeContext`) backed by URL params, consumed by every Birds page (incl. bird detail). Keeps the change inside the Birds app. Default = home location + always-on device (Yard · Yard Mic).

## Build order (mirrors handoff §4; each phase ends design-clean + tsc-clean + reviewer-gated)

1. **Responsive shell + scope** — sidebar (web) / top-bar+tabs (mobile) from `BirdsHeader`/`BIRDS_NAV`; `BirdsScopeContext` (Location·Device·Time) wired to URL params; pickers as modal/bottom-sheet. Default scope = home location + always-on device (Yard · Yard Mic).
2. **Home + live card** — reorder dashboard; live-card header gets Listen + single pulsing Live + expand; add Histogram⇄Aviary toggle; remove duplicate `RecorderBar` pill + top Listen. Keep the real spectrogram histogram.
3. **Recent (presence)** — 15-min-window collapse, latest-time sort, window switch (15m/1h/Today), pagination 10/page.
4. **Birds review + bird detail** — dedicated Birds page, status segmented top (Confirmed default), counts match list; bird detail switches local pickers → global scope; keep expandable rows + per-bird recording mode.
5. **Charts** — §2.1 sky backdrop + own-axis diversity; §2.2 new day-over-day presence toggle; §2.3 week "I"-range + species-above + weather gutter; §2.4 day-shape band + rain axis. Over real model output; keep calendar scope/drill-down/maxBarSize.
6. **Atlas** — keep map; add 5.1 scatter, 5.2 heatmap, 5.3 novelty (recharts where clean, custom SVG for scatter/heatmap/novelty).
7. **Aviary** — move under live-card toggle; fullscreen/cast mode; quiet-yard empty state. (Engine + live wiring already done.)
8. **Recordings, Settings, Locations/Devices** — confirm minimal inline-edit lists; no per-item dashboards (viewing one = selecting it in scope).

## Verification per phase

`bash scripts/check_design.sh` + `cd frontend && npx tsc --noEmit`; then the work-reviewer gate before marking any item done. After any birds frontend change, rebuild `dist-springer` (`vite build --config vite.springer.config.ts`) — the public site serves a prebuilt bundle. Check UI at ~1440/1024/768/390 with real content.
