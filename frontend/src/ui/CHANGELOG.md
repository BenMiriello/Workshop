# Changelog — @workshop/ui

The Workshop Design System kit. This changelog is the grounded record that the
AI-assisted kit migration reads (docs/workshop-v1/03-ui-kit-and-marketplace.md
section 3): each major records what moved, was renamed, or was removed, so a
breaking upgrade is migrated against real notes, not guesswork.

Format: per-version sections. Compatible upgrades (same major) just bump the
version. Breaking upgrades start a new major and list the migration notes here.

## 1.0.0 — 2026-06-14

Initial versioned baseline. Formalizes the existing `src/ui` + `src/styles`
foundation as a versioned package without changing any runtime behavior or token
values.

- Declared `@workshop/ui` as version 1.0.0. App manifests pin it as
  `kit: "@workshop/ui@^1"`.
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
