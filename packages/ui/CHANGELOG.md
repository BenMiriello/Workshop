# Changelog — @workshop/ui

The Workshop Design System kit. This changelog is the grounded record that the
AI-assisted kit migration reads (docs/workshop-v1/03-ui-kit-and-marketplace.md
section 3): each major records what moved, was renamed, or was removed, so a
breaking upgrade is migrated against real notes, not guesswork.

Format: per-version sections, newest first from 0.2.0 on; the 1.x sections below
remain in the order they were written.

**Versioning is 0.x.** While the major is zero, npm treats the MINOR as the
breaking position: `^0.2.0` means `>=0.2.0 <0.3.0`. So a breaking change bumps
the minor (0.2 → 0.3) and a compatible one bumps the patch. See CONTRIBUTING.md.

## 0.2.0 — 2026-08-10 — the kit becomes a real package

**Renumbered from 1.x to 0.x.** The 1.0.0 / 1.1.0 / 1.1.1 sections below describe
a package that was never built and never published, and whose version field said
`1.0.0` the whole time the changelog was moving. A 1.x line advertises a
stability guarantee this kit has not earned yet. It restarts at 0.2.0, which is
lower than the numbers below on purpose: nothing ever consumed those, so there is
no upgrade path being broken, and 0.x tells a future consumer the truth about how
settled the API is. The 1.x history stays in this file because the migration
notes in it are real.

Until now `@workshop/ui` was a `package.json` sitting in `frontend/src/ui` with
no build, no artifact and no consumer that resolved it by name. This release
makes it an actual package. **The API of the primitives themselves is unchanged
except for `Markdown` (below); the breaking part is how you get at them.**

### Why this is a breaking release

Three things were true at once, and together they meant the version number said
nothing:

- `main` pointed at `index.ts`. Raw TypeScript, never built, never packed.
- `exports` mapped `./tokens.css` to `../styles/tokens.css`, a path **above the
  package root**, which `npm pack` silently drops. The package could not have
  worked if published.
- Every consumer, in-repo and out, resolved the kit through a bundler path alias
  into the source directory. Nothing compiled out-of-repo consumers, so a
  breaking change to a primitive produced no error anywhere.

That last one already bit. `Menu` changed from an `items` prop to `MenuItem`
children during 1.x with no version bump. In-repo call sites were updated in the
same commit so nothing here failed. Out-of-repo apps aliasing into the source
broke on their next build, against a pin that still read `^1`. If you are on a
1.x pin and using `Menu`, see the migration note below.

### Breaking: the package moved

`frontend/src/ui` and `frontend/src/styles` are now `packages/ui/src` and
`packages/ui/src/styles`. The web app is a consumer of the kit, not its owner.

Any relative import into the old location must become a package import:

```diff
-import { Button, Card } from "../../ui";
+import { Button, Card } from "@workshop/ui";

-import "./styles/tokens.css";
-import "./styles/components.css";
+import "@workshop/ui/tokens.css";
+import "@workshop/ui/styles.css";
```

Note `components.css` is exported as **`@workshop/ui/styles.css`**, not
`components.css`.

### Breaking: `Markdown` moved to its own subpath

```diff
-import { Markdown } from "@workshop/ui";
+import { Markdown } from "@workshop/ui/markdown";
```

`Markdown` pulls `react-markdown` and `remark-gfm`. Re-exporting it from the
core entry meant importing a `Button` dragged a markdown parser into the bundle.
Both libraries are now optional peer dependencies; if you don't import this
subpath you don't need them installed.

### Breaking: `Map` is a subpath, not a deep import

`Map` was never exported from the barrel; consumers reached in at
`.../ui/Map`. That deep path is gone. It is now a declared entry point:

```diff
-import { Map, type LngLat } from "../../ui/Map";
+import { Map, type LngLat } from "@workshop/ui/map";
```

`maplibre-gl`, `pmtiles` and `protomaps-themes-base` are optional peers for the
same reason as above.

### Breaking: internals are no longer reachable

The old alias mapped the package name onto a directory, so
`@workshop/ui/<any-internal-file>` resolved. The exports map now lists exactly
six entry points and nothing else resolves. If you were importing an internal
module, use the public export or open a ticket to promote it.

### Migration note carried forward from 1.x: `Menu`

`Menu` takes children, not an `items` array:

```diff
-<Menu items={[{ label: "Rename", onSelect: rename }]} />
+<Menu>
+  <MenuItem onSelect={rename}>Rename</MenuItem>
+</Menu>
```

`MenuItem`, `MenuSeparator` and `MenuLabel` are exported alongside `Menu`. This
change actually shipped in 1.x without a version bump; it is recorded here
because this is the first release where a consumer can act on it.

### Added: it is now publishable

- Real build. `npm run build` emits `dist/` with ESM, `.d.ts` declarations,
  source maps, and two self-contained stylesheets. `@import`s in `tokens.css`
  are inlined, so the shipped file resolves against nothing.
- `react`, `react-dom` and `lucide-react` are peer dependencies. The kit no
  longer bundles a second copy of React into a consumer.
- `files` limits the tarball to `dist/` plus the docs. `npm pack --dry-run`
  shows exactly what publishes.
- The whole package is self-contained: nothing in `exports` or in the source
  points outside the package root.

### Added: the API is snapshotted

`api/ui.api.md` records every public export of every entry point, generated by
`npm run api:snapshot`. `scripts/check_design.sh` fails when the surface changed
and the snapshot did not, so the silent-break failure mode above cannot repeat.
See CONTRIBUTING.md.

### Fixed: the version was wrong

`package.json` said `1.0.0` while this changelog had already reached `1.1.1`.
Worse, **1.1.0 was itself a breaking change shipped as a minor**: it moved the
canonical color source out of `tokens.css` into the generated `palettes.css`,
so anything importing `tokens.css` and expecting color declarations in it got
none. If you are pinned anywhere in 1.x, treat 1.1.0 as the real boundary.

### Unchanged

Token values, palette model, status semantics, the `.ws-*` class names, and the
props of every primitive other than the two relocations above. This is a
packaging release; it is not a redesign.

## 1.0.0 — 2026-06-14

Initial versioned baseline. Formalizes the existing `src/ui` + `src/styles`
foundation as a versioned package without changing any runtime behavior or token
values.

- Declared `@workshop/ui` as version 1.0.0. App manifests pin it as
  `kit: "@workshop/ui@^1"`. (Now `@workshop/ui@^0.2` — see 0.2.0 above.)
- Export surface is `src/ui/index.ts` (unchanged): the primitives plus `cx`,
  theme helpers, status/category helpers, chart helpers, and `useEditMode`. See
  that file for the full list.
- Tokens: `src/styles/tokens.css` is the single canonical source of truth for
  the web (CSS custom properties). The mobile token object is derived from it.
- Token values are unchanged from the pre-versioning state. This release is a
  metadata/packaging formalization only.

### Single-source tokens

`tokens.css` is canonical. The mobile token object is generated from it, so the
two stop drifting (they were hand-synced before).

- Generator: `scripts/gen-tokens.mjs` (run via `npm run gen:tokens` from
  `frontend/`). It parses the CSS custom properties out of `tokens.css` and
  emits `src/styles/tokens.generated.js` (the same shape as the hand-maintained
  `tokens.js`).
- The generator is idempotent and never modifies `tokens.css`.
- Flow to refresh mobile after a token change: edit `tokens.css`, run
  `gen:tokens`, then reconcile `tokens.generated.js` into `tokens.js` (the
  Expo-consumed module that also carries the usage docs + `makeTheme` helper).
  The generated file is the diff oracle; review it before promoting changes.

## 1.1.0 — configurable color schemes

Five palettes, each in light and dark. Appearance is now two independent axes:
`data-theme` (light/dark/system) × `data-palette` (violet/cobalt/teal/plum/
sandstone), both on `<html>`, both picked in Settings → Appearance.

**The canonical color source moved.** `tokens.css` no longer defines any color;
it owns the theme-independent scales and `@import`s the generated `palettes.css`.
The model is `src/styles/palette.mjs`:

- An OKLCh tonal ramp (M3-shaped: fixed tones read off a hue, neutrals from the
  same hue at near-zero chroma). The ramp tones were measured FROM the previous
  hand-tuned violet theme, so that look is now one instance of the model rather
  than a special case — `Violet` is still the default and its surface anchors are
  byte-identical.
- Five palette seeds (brand hue, neutral hue, two chroma scales). Four cool hues
  spaced across the arc that cannot be misread as status (197/258/290/334), plus
  Sandstone: warm neutrals with a cool indigo accent.
- A pinned status tone table. `ok`/`warn`/`danger` are identical in every
  palette, solved once against every scheme's surfaces.

Generator: `scripts/gen-tokens.mjs` now emits three files — `palettes.css`,
`palettes.gen.ts` (typed menu for the picker), `tokens.generated.js` (Expo). It
no longer parses CSS; it imports the model. `--check` mode runs inside
`scripts/check_design.sh`, so a stale generated file fails the gate.

New/changed API:

- `usePalette()`, `applyPalette()`, `getStoredPalette()`, `useResolvedMode()`,
  `type PaletteId` — from `ui/theme`.
- `PalettePicker` — the five-swatch radiogroup. Each swatch is a live preview
  (it carries `data-palette`/`data-theme`, so it renders that scheme's real
  tokens).
- `PALETTE_META`, `DEFAULT_PALETTE` re-exported from the generated list.
- `makeTheme(scheme, paletteId)` in `tokens.js` takes a second argument;
  `palettes`, `paletteMeta`, `defaultPalette` are exported. The old
  `palette.light/.dark` still resolves, to the default palette.
- `SettingsRow` accepts `hint`.
- New token `--scrim` (modal overlay; was a hardcoded rgba).

Contrast: every text token now clears AA on `--bg`, `--card` and `--bg-inset` in
all ten schemes, and every solid mark clears 3:1. Two values moved as a result —
the light-mode gold `--warn-solid` (was 2.63:1 on white) and the light `--accent`
(one step darker; the original failed AA on `--bg-inset`). Pinned in
`src/styles/palette.test.ts`.

`--accent-fg` is no longer always white. `.ws-btn--primary` spinners use it.

## 1.1.1 — color scheme refinement pass

Two fixes from the first round of real feedback:

- **Accent chroma pulled back, system-wide and per-hue.** The accent ran hot
  against the rest of the muted UI, most visibly filling the chat bubble
  (`.chat-msg.user .chat-bubble`, `styles.css`) at full strength. Base chroma
  dropped 0.199 → 0.175 (light) / 0.160 → 0.140 (dark) across all five
  palettes. On top of that, `Cobalt` and `Sandstone` (blue-family hues,
  250-270°) and `Plum` (magenta, 334°) got an additional per-palette
  `brandChroma` pull-back (0.74 / 0.78 / 0.86) — OKLCh is not perceptually
  uniform across hue, and a blue at the same numeric chroma as violet reads
  distinctly more vivid. `Teal` was untouched: sRGB's gamut is narrow enough
  there that a full-chroma request was already clipping to about half.
- **The Sandstone swatch didn't read as sandstone.** The picker's preview is a
  live render of the scheme's real (very low-chroma) surfaces, which is
  correct but made Sandstone indistinguishable from the cool schemes at chip
  size — the one bright element in the chip, the accent bar, is deliberately
  a cool brand hue even in a warm scheme. `identitySwatch()` in `palette.mjs`
  adds a second, moderate-chroma tone at the palette's NEUTRAL hue, rendered
  as a strip above the preview; used only in the picker, never in app UI.

`PaletteMeta` gained a `swatch: string` field. `palette.test.ts` gained a
seed-level assertion that the blue-family palettes carry a lower `brandChroma`
than Violet, so this can't silently regress.
