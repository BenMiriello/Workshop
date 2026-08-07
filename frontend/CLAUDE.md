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

## Theming

`data-theme` on `<html>` is `light | dark | system`. **Default is `dark`** (set by the pre-React bootstrap in `index.html` and `getStoredTheme()`). The Settings theme switch is the only writer — it uses `useTheme()` and persists to `localStorage["ws-theme"]`. There is **one** brand accent (refined violet); it is not user-selectable. Differentiation comes from the `--cat-*` suite.

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
