# Handoff: Live Aviary — isometric voxel bird visualization

## Overview
The **Live Aviary** is a playful 2.5‑D ("isometric voxel" / Crossy‑Road‑style) view of the birds currently at a location. Each species the site's microphone has heard in the **rolling last 15 minutes** appears in an isometric yard: it **flies in** when first detected, **pecks and hops** while it keeps calling, and **lifts off** when it goes quiet. Tapping a bird identifies it. It's an alternate, ambient way to read the same live‑detection data the rest of the Birds app already has.

## ⚠️ Read this first — this is NOT a "recreate the HTML" handoff
Most of this bundle is **reusable, framework‑agnostic source you should keep and use as‑is**, not a visual mock to rebuild from scratch. Specifically:

| File | What it is | What to do with it |
|---|---|---|
| **`aviary-voxel.js`** | The voxel bird **renderer + species library**. Pure, dependency‑free. Builds every bird (and props) as 3‑D cuboids projected to 2:1 isometric SVG, with lighting, depth‑sorting, rounded edges, and poses (flap / peck). | **Use unchanged.** Add species to its `SPECIES` table; otherwise don't touch. |
| **`aviary.js`** | The **scene engine**: isometric lawn + props, spawn/land/peck/hop/leave behaviors, spacing logic, tap‑to‑label, the side panel, and a small **public API** (`window.Aviary`). | **Use the core unchanged.** Wire it to real data via the API (below). You may restyle the surrounding HTML/panel. |
| **`Aviary.html`** | A **reference shell** (header, scene container, side panel) that loads the two scripts and shows the standalone demo. | **Reference / adapt.** Recreate this chrome in your app's framework & design system; keep the `#stage / #scenery / #field` scene structure the scripts expect, or rename and update the few `getElementById` calls in `aviary.js`. |

In short: **port the chrome, reuse the engine.** Do not re‑implement the renderer or the bird models — that's the hard, finished part.

## Fidelity
**High‑fidelity.** Final colors, proportions, motion, and the voxel art system are done. The birds and scene are production‑ready as drawn.

---

## How it works (data → scene)

The standalone build runs a **random simulation** so the page is lively on its own. That simulation is a stand‑in. **The real behavior: a bird is present iff its species was heard in the last 15 minutes.** Replace the simulation with your live data through the public API:

```js
// 1. turn off the demo's random spawner
Aviary.simulate(false);

// 2. whenever the rolling-15-min set of heard species changes, hand it the
//    full current set. The engine flies in the newly-present species and
//    flies out the ones that have gone quiet.
Aviary.setPresent(['house_finch', 'lesser_goldfinch', 'dark_eyed_junco']);

// (optional) a single detection event -> one bird of that species flies in:
Aviary.heard('house_finch');

// list the species ids the renderer knows about:
Aviary.species();
```

- `setPresent(ids)` is the main seam. Call it on every update of the live window (e.g. each poll of `/live` or each detection push). It **reconciles**: spawns species in `ids` that aren't on screen, and triggers fly‑out for grounded species no longer in `ids`. Flocking species may show 1–3 individuals.
- `ids` are keys in `AviaryVoxel.SPECIES`. **Map your backend's species names → these ids.** If a heard species isn't in the table, add it (see "Adding species").
- Suggested integration point in this codebase: the bird pages already poll live detections (`LiveFeed.tsx` / the `/live` endpoint). Derive the set of `is_bird` species heard within 15 min and pass it to `Aviary.setPresent`.

### Adding species
Every bird is one of **8 body templates** × a **color pattern**. To add a species, append to `SPECIES` in `aviary-voxel.js`:
```js
spotted_towhee: { id:"spotted_towhee", common:"Spotted Towhee", sci:"Pipilo maculatus",
  template:"songbird", group:"Songbird", pattern:"hooded",
  colors:{ back:"#2a2730", belly:"#efe7da", wing:"#8a3f24", throat:"#b14a2b",
           cap:"#211f27", face:"#211f27", bill:"#2a2830", legs:"#9a8266" } },
```
- `template` ∈ `songbird · dove · corvid · raptor · hummingbird · waterfowl · shorebird · wading` (the major bird groups — controls the silhouette/proportions).
- `pattern` ∈ `plain · capped · hooded` (which regions take the accent color).
- `colors` is a simplified, identifiable read of the real plumage (`back`, `belly`, `wing`, `throat`/breast, `cap`/crown, `face`, `bill`, `legs`, optional `tailTip`).
No geometry work is needed — color + template is the whole encoding.

---

## Screens / Views

### View — Live Aviary (single screen)
- **Purpose:** ambient, at‑a‑glance "who's in the yard right now," scoped to the home location's always‑on mic.
- **Layout:** full‑height column. **Header** bar (back link · title · location pill · live clock) over a **two‑pane body**: the **scene** (flex‑grow, left) and a **side panel** (fixed `320px`, right). Below `820px` the panel drops under the scene (`56%` scene / rest panel).
- **Scene:** absolutely‑positioned layers inside `#stage`:
  - background: flat vertical blue‑sky gradient (`--sky-1 → --sky-2`). *No built‑up perspective sky — it's isometric, so it's just yard + a blue field behind.*
  - `<svg id="scenery">`: the isometric grass plot (rounded parallelogram top + soil‑thickness sides + faint mow stripes).
  - `<div id="field">`: all **actors** — birds, props (trees/bushes/feeder/birdbath), shadows, name tags, and the selection callout. Everything is z‑index‑sorted by screen‑Y so nearer actors overlap farther ones; birds correctly pass in front of / behind props.
  - `.stagetop` chip: "N in the yard · M species."
- **Side panel:**
  - **"In the yard now"** list — one row per present species (mini voxel thumbnail · common name · group · "now"/"×count"). Clicking a row selects/centers that bird.
  - **Scene controls** card — Playback play/pause, speed segmented control (Slow / 1× / Fast), Name‑tags toggle, "Call in a bird" button.
  - An explanatory note (dashed‑accent callout).

## Interactions & Behavior
- **Arrival:** spawn off‑lawn at altitude → ease to a **clear** ground spot (never on a prop or another bird) while flapping (wings hinge at the shoulder, legs hidden in flight) → land.
- **Grounded:** randomized actions at their own intervals — **idle**, **peck** (head pitches down ~60° about a pivot below the head, repeating dips), **hop** (short arc to a nearby clear tile), **turn**. Dwell ~7–18 s.
- **Departure:** lifts off and exits **in the direction it's facing** (birds only mirror left/right, so they leave toward the edge they face — they always fly out facing forward).
- **Tap a bird:** floating callout (common + scientific name, group chip, "heard now"), follows the bird; tap elsewhere or × to dismiss.
- **Spacing:** `clear(gx,gy)` keeps every landing/hop ≥ ~1.15 tiles from other birds and outside each prop's footprint radius — no clipping, no stacking.
- **Motion timing** scales with the speed control; all animation is `requestAnimationFrame`. Respects nothing device‑specific; pure DOM/SVG.

## State Management
Engine‑internal (in `aviary.js`): the `birds[]` array (each with `state` ∈ `in · ground · out`, world position `gx,gy,gz`, `face`, current `action`, timers), `selected` bird, `SIM`/`playing`/`SPEED` flags, and a cosmetic sim clock. **Your app only needs to feed it the present‑species set** via `Aviary.setPresent(...)`; everything else is handled internally.

## Design Tokens
Surrounding UI uses the Birds app's existing system (already in `Birds App.html`):
- **Fonts:** `Hanken Grotesk` (UI), `JetBrains Mono` (clock/numerics).
- **Accent:** `#7a57e0` (violet). Card `#fff` / border `#e7e7f0` / muted `#6a6879` (full light+dark token set in `Aviary.html` `:root`).
- **Sky:** light `#a3d2f4 → #cdebfb`; dark `#3a5168 → #2b3b49`.
- **Lawn:** top `#86bd6a`, soil sides `#5e8c46 / #699a52`.
- **Radii:** cards `14px`. **Shadows:** `--shadow-card`, `--shadow-md`.
The bird/prop palettes live in `aviary-voxel.js` (`SPECIES` + the prop builders).

## Rendering internals (for reference, not required reading)
- 2:1 isometric projection (`AX=12, AY=6.2, AZ=11`). One key light → each cube face is flat‑shaded by `dot(normal, light)`.
- Birds are part‑grouped cuboids (`body / head / wingN / wingF / tail / legs`) transformed in 3‑D each frame (flap = rotate wings about the shoulder fore‑aft axis; peck = pitch head about the neck). Faces are depth‑sorted (legs draw under the body) and drawn as **rounded** silhouettes + inset rounded faces; rounding scales per‑model so small birds aren't over‑rounded.
- `renderCubes()` reuses the same pipeline for scene props (trees/bushes/feeder/bath).

## Assets
None external. All graphics are generated at runtime from `aviary-voxel.js` (birds + props) and inline SVG/CSS (lawn, chrome). Fonts via Google Fonts. No images.

## Files
- `aviary-voxel.js` — voxel renderer + `SPECIES` library (keep as‑is; extend `SPECIES`).
- `aviary.js` — scene engine + `window.Aviary` public API (keep core; restyle chrome).
- `Aviary.html` — reference shell / standalone demo (port to your framework).
