# Workshop frontend — Design System law

**This file is binding for every change under `frontend/` (and the `mobile/` Expo app). Any UI work — by a human, by the cron/supervisor agents, or by an interactive Claude session — MUST follow it. New screens are designed this way from the start; they do not get a "we'll systematize it later" pass.**

The system is the Workshop Design System (handoff: `design_handoff_workshop_ds`). It is already implemented:

- **Tokens** — `src/styles/tokens.css` (CSS custom properties: color, space, type, radii, shadow, z, motion) and `src/styles/tokens.js` (the same values as a JS object for the Expo app).
- **Primitives (CSS)** — `src/styles/components.css`: every component as a plain, className-based rule (`.ws-*`). Zero inline styles, no Tailwind, no CSS-in-JS, no component library.
- **Primitives (React)** — `src/ui/`: thin className wrappers over `components.css`, exported from `src/ui/index.ts`. **Import from `../ui` — do not hand-write `.ws-*` markup when a primitive exists.**

Both CSS files are imported in `src/main.tsx` **before** `styles.css`. `styles.css` is the shrinking legacy sheet; never add palette/theme variables to it.

## The ten rules (non-negotiable)

1. **Use the primitives.** `Button, Field, Input, Select, Textarea, Card, PageHeader, SectionLabel, Stack, Row, Badge, StatusPill, StatusDot, Tag, Tabs, Toggle, Checkbox, Segmented, Modal, ConfirmDialog, Table/TableScroll, CopyButton, RevealField, StateBlock, Skeleton, ActionBar, Stat, ChartFrame, ChartTooltip` — all from `src/ui`. If you need something not covered, add a primitive to `src/ui` + a `.ws-` rule to `components.css`; don't one-off it inline.
2. **No inline hex colors. Ever.** No `color:"#16a34a"`, no `#ef4444`, no `background:"#fff"`. Color comes from tokens (`var(--accent)`, `var(--muted)`, …) or, far better, from a primitive. `scripts/check_design.sh` fails the build on raw hex in `.tsx`.
3. **Status = meaning, mapped to five kinds.** `ok | running | stopped | warn | danger` via `StatusPill` (standalone) or `StatusDot` (dense table rows). Never invent a status color. `running` is the only thing that animates. Most of a screen is neutral — color is a signal, not decoration.
4. **Categorical ≠ status.** To tell apart agents / sources / repos / chart series, use `Tag` with a `--cat-*` hue (`catFor(key)` gives a stable hue). Cool tones that can't be misread as ok/warn/danger.
5. **lucide icons only — never emoji.** 14px in section labels/meta, 16px in buttons, 1.8 stroke. Every icon names the thing beside it. `check_design.sh` fails on emoji in `.tsx`.
6. **Hierarchy from position, scale, weight, color — not borders.** `PageHeader` (one per screen) → status band → content grouped under `SectionLabel` (the uppercase motif). Cards float on shadow (light) / a faint ring (dark). 4px spacing grid (`--space-1..8`); use the steps, never invent `13px`.
7. **Editability model, not tap-to-edit.** Anything that writes config or money uses `useEditMode` + a sticky `ActionBar` (Save primary / Cancel ghost, "unsaved" pill while dirty). View prefs (theme, filters, reveal/hide, source `<select>`) commit **on change**. Config/money commit **on explicit Save**. See "When does a control commit?" below.
8. **Destructive actions get `ConfirmDialog`.** `danger` treatment + a dialog naming the consequence and that it can't be undone. Never bare red text, never one-tap delete.
9. **Every list/page ships four states.** loading (`Skeleton`), empty (`StateBlock` — always with an instructional hint, e.g. "POST to `/api/events/<source>` to populate"), error (`StateBlock variant="error"` with the failing endpoint + retry), overflow (paginate / `TableScroll`).
10. **Accessibility + motion.** WCAG AA contrast both themes; visible focus ring via `:focus-visible` (handled by `components.css`); dialogs trap focus + Esc + restore (handled by `Modal`); cursors are `default`/`pointer`/`text` only — never `not-allowed`/`wait`; everything respects `prefers-reduced-motion`.

## When does a control commit?

| Commit on… | Controls | Why |
|---|---|---|
| **change** (immediately) | theme, filters, reveal/hide, source `<select>`, segmented view switches | view preferences — cheap, reversible, no write |
| **submit** (explicit Save) | budget, coords, configs, prompts, cron, secrets | writes config or money — deliberate, not accidental |

## Page width — the shell owns it, a page never sets it

**A page does not choose its own width.** Width is three tokens in `tokens.css`, and nothing else may set a page-level cap:

| token | value | who applies it |
|---|---|---|
| `--page-max` | 1280px | `.ws-app` — **every route**, no exceptions to opt into |
| `--page-max-wide` | 1680px | `.ws-app--wide` — 3-pane workbench/IDE routes only (the builder). A layout constraint, not a preference. |
| `--measure` | 880px | `.ws-measure` — a reading/form column *inside* a page (the create forms) |

To narrow content, wrap it in `ws-measure`. **Never add a `maxWidth` to a page wrapper**, and never add a fourth width. `check_design.sh` fails on `maxWidth: >= 600` in `.tsx` for exactly this reason. Small element caps (a 150px number input, a 420px filter column) are fine and below the threshold.

This exists because the app previously decided width in three places at once — a three-tier shell picked by a hand-maintained route list in `App.tsx`, plus per-page inline caps of 720/760/820/880/1040 fighting it. Same app, seven widths. The route list is gone: a new page is the right width by default, with no list to forget to update.

The focused chat is a page like any other and takes `--page-max`. Only its *scroll* behaviour is special (`.ws-app--chatfull`: fixed + viewport-tall so the composer rides above the mobile keyboard) — that is not a licence to run full-bleed.

## Theming — two axes, one generated color layer

Appearance is `data-theme` × `data-palette`, both on `<html>`, both applied by the pre-React bootstrap in `index.html` before first paint:

| attribute | values | storage | default | writer |
|---|---|---|---|---|
| `data-theme` | `light \| dark \| system` | `localStorage["ws-theme"]` | `dark` | Settings → Appearance (`useTheme()`) |
| `data-palette` | `violet \| cobalt \| teal \| plum \| sandstone` | `localStorage["ws-palette"]` | `violet` | Settings → Appearance (`usePalette()`) |

**Color is generated, not hand-written.** The model lives in `src/styles/palette.mjs` (OKLCh tonal ramp + five palette seeds + the pinned status tone table). `scripts/gen-tokens.mjs` emits three files from it — `palettes.css` (all ten schemes), `palettes.gen.ts` (the typed menu), `tokens.generated.js` (the same hexes for Expo). To change a color: **edit `palette.mjs`, then `npm run gen:tokens`.** Editing a generated file is wasted work; `check_design.sh` fails on a stale one. `tokens.css` `@import`s `palettes.css`, so every entry point picks it up.

Rules the model enforces, checked by `src/styles/palette.test.ts` (53 assertions across all ten schemes):

- **One brand accent per scheme**, and it is not separately selectable — picking a palette picks the accent. (Multiple simultaneous accents were tried and dropped.)
- **`ok` / `warn` / `danger` are byte-identical in all five palettes.** Status is meaning; meaning does not move when someone restyles. Only `running` (the accent) and `stopped` (the neutral) follow the palette.
- **Every text token clears WCAG AA (4.5:1) on `--bg`, `--card` AND `--bg-inset`**; every solid mark clears 3:1. Accent tone is *solved* per hue, not fixed, because a teal at a given lightness is far lighter to the eye than a violet.
- **`--accent-fg` is not always white** — it is whichever end of the ramp contrasts more (teal light mode gets a near-black foreground). Never hardcode `#fff` on an accent fill.
- The `--cat-*` categorical suite is deliberately palette-INdependent: a tag color is an identity, so it must not change when the scheme does.
- **OKLCh is not perceptually uniform across hue — don't set `brandChroma: 1.0` on a new blue/magenta seed without checking.** A hue in the ~250-270° (blue) or ~320-345° (magenta) band reads distinctly more vivid than a violet at the identical numeric chroma; the contrast solver won't catch it, since it optimizes for legibility, not "does this look calm." `Cobalt`/`Sandstone` (blue-family) and `Plum` (magenta) carry a `brandChroma` pull-back for exactly this — see the comments in `palette.mjs`. `Teal` needed none: sRGB's gamut is naturally narrow there, so a full-chroma request already gets gamut-clipped to about half.
- **The Settings picker's swatch and the app's real tokens are two different colors, on purpose.** The live preview (the tiny card + accent bar) renders the scheme's actual, very-low-chroma neutrals — correct, but a warm scheme and a cool one can look the same in a 76px chip, since near-black/near-white don't show hue well and the one bright element (the accent bar) is deliberately a cool brand hue even in a warm scheme. `identitySwatch()` in `palette.mjs` supplies a second, moderate-chroma, mode-independent tone at the palette's NEUTRAL hue, rendered as a strip above the preview — that's what actually reads as "warm" or "cool" at a glance. Never used outside the picker.

Adding a palette: add a seed to `PALETTES` in `palette.mjs`, add the id to the `PALETTES` array in the `index.html` bootstrap, run `npm run gen:tokens`, run the tests. The Settings picker builds itself from the generated list.

## Charts (recharts)

Wrap in `ChartFrame` (applies `.ws-chart`; the bundled `.recharts-*` rules restyle recharts without touching JSX). Series colors come from `CHART_SERIES` (categorical, brand violet first — never status hues). Target/reference lines: `CHART_TARGET` (dashed slate). Tooltip: `ChartTooltip` (card/elevation language, never a raw white box). Pick the type by the question: line/area = trend, bar = compare categories (zero baseline), donut = part-to-whole (≤5 slices, total in center), `Stat` = a single number.

## Mobile (Expo)

The `mobile/` app mirrors the same hierarchy and tokens via `src/styles/tokens.js` (`makeTheme(scheme)`, `categorical`, `chartSeries`) — CSS variables don't reach RN, so import the object and build the same primitives in `StyleSheet`. Tabs become the bottom tab bar.

## Before you commit UI

Run `bash scripts/check_design.sh` (from repo root) — it flags raw hex, emoji, and `not-allowed` cursors in `.tsx`, and confirms the foundation imports are intact. It is part of the completion gate; a UI change that fails it is **not** done. Also run `cd frontend && npx tsc --noEmit`.

**Tests: `cd frontend && npm test`** (vitest, `*.test.ts` beside the module). There is no DOM/render harness and none is planned — the target is the PURE RULE behind a behaviour, extracted into its own module so it can be executed. `turnBatching` (when a queue of typed-ahead messages becomes a turn) is the pattern.

**Chat log order is append-only, in send order.** Persisted messages, then the live turn with its working indicator at the tail, then messages sent during that turn, then messages queued for the next one. A message typed while a reply is being written renders BELOW that reply: the work in flight was started without it. Do not invent a scheme that splices a message into the middle of a reply — one existed, took five rounds of corrections, and was deleted. When the ask is "the standard chat behaviour", that is the whole specification.

Note: `scripts/reviewer.sh` runs typecheck + design only; it does not run tests (backend or frontend). Run them yourself.

## App navigation (one header per app, one source of sections)

Every multi-page app under `pages/apps/<app>` MUST navigate through a single shared
header component, with its sections defined in ONE place and the current section
highlighted. This is non-negotiable: it is what keeps an app navigable and stops
sub-pages from feeling like they replaced the home page.

Rules:
1. **One header, every page.** Each app has exactly one header component (Birds: `BirdsHeader`).
   Every page of that app renders it as its top element. Do not hand-roll a per-page
   `<h2>` header or a bespoke toolbar.
2. **One source of sections.** The navigable sections live in a single exported list
   (Birds: `BIRDS_NAV` in `BirdsHeader.tsx`). To add a page: add a `<Route>` in `App.tsx`
   AND one entry to that list. Never scatter one-off section links in page bodies as the
   way to reach a section (a contextual CTA is fine, but it is not the nav).
3. **Active section is highlighted.** The header marks the current section (`is-active`
   + `aria-current="page"`) so "you are here" is always clear.
4. **Home is always reachable** from the header (Birds: the bird icon → dashboard).

When you add or move a page and the nav doesn't update automatically from the section
list, that's the bug — fix the list, not the page.

## Migration status

The shell + token foundation are live. Pages are migrated to the primitives incrementally; when you touch a page that still uses legacy classes (`.card`, `.muted`, `.pill`, `.btn`, ad-hoc inline styles), migrate the part you touch to `ws-*`/primitives rather than extending the old idiom. As pages migrate, delete the now-dead rules they leave behind in `styles.css` (handoff step 9). The `birds` sub-app has its own established calm UI and is migrated last.
