# Workshop frontend — Design System law

**This file is binding for every change under `frontend/` (and the `mobile/` Expo app). Any UI work — by a human, by the cron/supervisor agents, or by an interactive Claude session — MUST follow it. New screens are designed this way from the start; they do not get a "we'll systematize it later" pass.**

The system is the Workshop Design System, packaged as **`@workshop/ui`**. It lives at **`packages/ui/`**, OUTSIDE this app: `frontend/` is one consumer of the kit, not its owner. It is already implemented:

- **Tokens** — `@workshop/ui/tokens.css` (CSS custom properties: color, space, type, radii, shadow, z, motion) and `@workshop/ui/tokens.js` (the same values as a JS object for the Expo app).
- **Primitives (CSS)** — `@workshop/ui/styles.css` (source: `packages/ui/src/styles/components.css`): every component as a plain, className-based rule (`.ws-*`). Zero inline styles, no Tailwind, no CSS-in-JS, no component library.
- **Primitives (React)** — `packages/ui/src/`: thin className wrappers over the component rules. **Import from `@workshop/ui` — never by relative path, and do not hand-write `.ws-*` markup when a primitive exists.** Two primitives with heavy dependencies sit behind their own subpaths: `@workshop/ui/map` and `@workshop/ui/markdown`.

Both stylesheets are imported in `src/main.tsx` **before** `styles.css`. `styles.css` is the shrinking legacy sheet; never add palette/theme variables to it.

**Editing the kit is a platform change, not an app change.** Read `packages/ui/CONTRIBUTING.md` first: the public surface is snapshotted in `packages/ui/api/ui.api.md`, a removed or renamed export is a MAJOR version bump plus a CHANGELOG migration note, and `check_design.sh` fails if the surface moved and the snapshot didn't. Out-of-repo apps consume this package and nothing here compiles them, so the version number and the changelog are the only warning they ever get. App-level work restyles by setting tokens; it does not edit primitives.

## The ten rules (non-negotiable)

1. **Use the primitives.** `Button, Field, Input, Select, Textarea, Card, PageHeader, SectionLabel, Stack, Row, Badge, StatusPill, StatusDot, Tag, Tabs, Toggle, Checkbox, Segmented, Modal, ConfirmDialog, Table/TableScroll, CopyButton, RevealField, StateBlock, Skeleton, ActionBar, Stat, ChartFrame, ChartTooltip` — all from `@workshop/ui` (the full list is `packages/ui/api/ui.api.md`). If you need something not covered, add a primitive to `packages/ui/src` + a `.ws-` rule to `components.css`, bump the kit's MINOR version and re-run `npm run api:snapshot`; don't one-off it inline.
2. **No inline hex colors. Ever.** No `color:"#16a34a"`, no `#ef4444`, no `background:"#fff"`. Color comes from tokens (`var(--accent)`, `var(--muted)`, …) or, far better, from a primitive. `scripts/check_design.sh` fails the build on raw hex in `.tsx`.
3. **Status = meaning, mapped to five kinds.** `ok | running | stopped | warn | danger` via `StatusPill` (standalone) or `StatusDot` (dense table rows). Never invent a status color. `running` is the only thing that animates. Most of a screen is neutral — color is a signal, not decoration.
4. **Categorical ≠ status.** To tell apart agents / sources / repos / chart series, use `Tag` with a `--cat-*` hue (`catFor(key)` gives a stable hue). Cool tones that can't be misread as ok/warn/danger.
5. **lucide icons only — never emoji.** 14px in section labels/meta, 16px in buttons, 1.8 stroke. Every icon names the thing beside it. `check_design.sh` fails on emoji in `.tsx`.
6. **Hierarchy from position, scale, weight, color — not borders.** `PageHeader` (one per screen) → status band → content grouped under `SectionLabel` (the uppercase motif). Cards float on shadow (light) / a faint ring (dark). 4px spacing grid (`--space-1..8`); use the steps, never invent `13px`.
7. **Editability model, not tap-to-edit.** Anything that writes config or money uses `useEditMode` + a sticky `ActionBar` (Save primary / Cancel ghost, "unsaved" pill while dirty). View prefs (theme, filters, reveal/hide, source `<select>`) commit **on change**. Config/money commit **on explicit Save**. See "When does a control commit?" below.
8. **Destructive actions get `ConfirmDialog`.** `danger` treatment + a dialog naming the consequence and that it can't be undone. Never bare red text, never one-tap delete.
9. **Every list/page ships four states.** loading (`Skeleton`), empty (`StateBlock` — always with an instructional hint, e.g. "POST to `/api/events/<source>` to populate"), error (`StateBlock variant="error"` with the failing endpoint + retry), overflow (paginate / `TableScroll`).
10. **Accessibility + motion.** WCAG AA contrast both themes; visible focus ring via `:focus-visible` (handled by `components.css`); dialogs trap focus + Esc + restore (handled by `Modal`); cursors are `default`/`pointer`/`text` only — never `not-allowed`/`wait`; everything respects `prefers-reduced-motion`.

11. **Copy: no em-dashes, ever. Direct, clear, concise.** No `—` in any string a
    user can read: labels, buttons, hints, empty and error states, tooltips,
    aria-labels, and any backend message that reaches the screen. Use a period,
    a comma, a colon, or parentheses; two short sentences beat one long one.
    Never substitute a hyphen or an en-dash. Then cut the padding: no cute or
    self-referential phrasing, no advice nobody asked for. Say what happened and
    what to do. Operator instruction, 2026-08-11, after the compact
    confirmation spent 117 characters saying one thing and closed by telling him
    to "continue where you left off". `check_design.sh` fails on an em-dash in a
    changed `.tsx` string, the same as it does on inline hex and emoji. Code
    comments are exempt; they are not copy.

## When does a control commit?

| Commit on… | Controls | Why |
|---|---|---|
| **change** (immediately) | theme, filters, reveal/hide, source `<select>`, segmented view switches | view preferences — cheap, reversible, no write |
| **submit** (explicit Save) | budget, coords, configs, prompts, cron, secrets | writes config or money — deliberate, not accidental |

## Page width — the shell is full-bleed, the content is capped, apps own their own

**Every regular Workshop page is the same width, and a page does not get to pick its own.** What changed on 2026-08-11 (operator-directed) is WHERE the cap sits and WHO is exempt.

There is one content token, `--page-max: 1200px` in `tokens.css`. It caps the **content**, not the shell:

- `.ws-app` (the shell) and the `.ws-topnav` inside it span the **full viewport**. The global toolbar is full-bleed on purpose.
- The cap lives on `.ws-app__body > main`, which stays the full-width scroller while its content is centred at `--page-max` via `padding-inline: max(--page-pad-x, (100% - --page-max)/2)`. Regular pages need no class and no wrapper; they are centred by that rule alone.
- The focused chat reads at the same measure: `--chat-max` is `var(--page-max)`, applied to `.chat-panel--full` (the chat's toolbar+log+composer as one column). Its shell is full-width like every other route; only that inner column is capped.

**Apps are the deliberate exception.** A route under `/apps/<name>` (and an app running in preview at `/projects/<slug>/run`) gets `.ws-app--appfull` on the shell, which drops the content cap: the app fills the viewport and **owns its own width and layout**. This is set in `App.tsx` from the pathname, next to `ws-app--chatfull`; those two are the only per-route width logic there is.

- An app built on the kit **opts back into** the standard measure by wrapping its content in **`.ws-page`** (`max-width: --page-max; margin-inline: auto`). The width is offered, not imposed.
- A custom app that styles itself (with the kit, partly, or not at all) keeps its own internal width logic. Nothing here constrains it.

Rules that still hold:

- **Do not reintroduce a per-page width list or a `--wide`/`--narrow`/`--measure` modifier for regular pages.** That is what produced 720/760/820/880/980/1040/1280/1680 at once. To change the page measure, change the one number.
- **Never add a `maxWidth` to a regular page wrapper.** `check_design.sh` fails on `maxWidth: >= 600` in `.tsx`. Small element caps (a 150px input, a 420px filter column) sit below the threshold and are fine. An app that owns its width does its capping through `.ws-page` or its own container, not an inline `maxWidth` on a Workshop page.
- **`.ws-app--appfull` is not "a wider page".** It is the absence of the page cap, handed to an app. Do not put it on a regular Workshop page to make that page wider; that is the per-page-width instinct wearing a new name.

## Theming — two axes, one generated color layer

Appearance is `data-theme` × `data-palette`, both on `<html>`, both applied by the pre-React bootstrap in `index.html` before first paint:

| attribute | values | storage | default | writer |
|---|---|---|---|---|
| `data-theme` | `light \| dark \| system` | `localStorage["ws-theme"]` | `dark` | Settings → Appearance (`useTheme()`) |
| `data-palette` | `violet \| cobalt \| teal \| plum \| sandstone` | `localStorage["ws-palette"]` | `violet` | Settings → Appearance (`usePalette()`) |

**Color is generated, not hand-written.** The model lives in `packages/ui/src/styles/palette.mjs` (OKLCh tonal ramp + five palette seeds + the pinned status tone table). `packages/ui/scripts/gen-tokens.mjs` emits three files from it — `palettes.css` (all ten schemes), `palettes.gen.ts` (the typed menu), `tokens.generated.js` (the same hexes for Expo). To change a color: **edit `palette.mjs`, then `npm run gen:tokens` from `packages/ui/`** (or `npm run gen:tokens` from the repo root). Editing a generated file is wasted work; `check_design.sh` fails on a stale one. `tokens.css` `@import`s `palettes.css`, so every entry point picks it up.

Rules the model enforces, checked by `packages/ui/src/styles/palette.test.ts` (54 assertions across all ten schemes, run by `npm test` in `packages/ui/` — NOT by the frontend's vitest run):

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

The `mobile/` app mirrors the same hierarchy and tokens via `@workshop/ui/tokens.js` (`makeTheme(scheme)`, `tokens`, `categorical`, `chartSeries`) — CSS variables don't reach RN, so import the object and build the same primitives in `StyleSheet`. Tabs become the bottom tab bar.

Wired 2026-08-11: `mobile/lib/api.ts` derives its `colors` from `makeTheme("dark")` and re-exports `tokens` for the scales. It previously hardcoded eight hexes from a theme that no longer exists (`#0b0f17`, a `#60a5fa` accent in none of the five palettes) — all eight values changed when it was connected, which is how far it had drifted. **Never add a literal hex to `mobile/`; import from `@workshop/ui/tokens.js`.**

Only the token subpath is importable there. The React primitives are react-dom and cannot render in RN, so `mobile/metro.config.js` maps `@workshop/ui/tokens.js` explicitly and throws a pointed error on any other `@workshop/ui` import rather than letting it fail as a confusing missing module.

## Before you commit UI

Run `bash scripts/check_design.sh` (from repo root) — it flags raw hex, emoji, and `not-allowed` cursors in `.tsx`, and confirms the foundation imports are intact. It is part of the completion gate; a UI change that fails it is **not** done. Also run `npm run typecheck` from the repo root.

**Never `npx tsc`.** `tsc` is a deprecated stub package on npm that npx resolves ahead of the local TypeScript binary; it prints "This is not the tsc command you are looking for" and **exits 0**, checking nothing. Use `npm run typecheck` (kit + frontend) from the repo root. Don't reach for the binary by path either: a root workspace install hoists typescript, so `frontend/node_modules/.bin/tsc` is present on some checkouts and absent on others.

**Tests: `cd frontend && npm test`** (vitest, `*.test.ts` beside the module). If you touched `packages/ui/`, also run `npm test --workspace @workshop/ui` — the palette contract tests live with the kit and the frontend run does not pick them up. `npm test` from the repo root runs both. There is no DOM/render harness and none is planned — the target is the PURE RULE behind a behaviour, extracted into its own module so it can be executed. `turnBatching` (when a queue of typed-ahead messages becomes a turn) is the pattern.

### Chat log order: a mid-turn message CLOSES the assistant's block

**An assistant block is a contiguous run of work, bounded by the messages that interrupted it.** A message sent while a reply is being written ends that block; work produced after the message starts a NEW block below it. A long turn the operator interrupted five times renders as six stretches of work with the five messages between them, each sitting where it actually landed.

The cut point is `meta.at_segment` on the message — the index into the reply's segment stream at which the server recorded it arriving, read off the LIVE run rather than its throttled snapshot. The rule is implemented once, in `src/components/chat/transcriptBlocks.ts`, and tested in `transcriptBlocks.test.ts`. Nothing else may arrange the transcript.

**Read this before "fixing" it back.** This file used to say the opposite — "append-only, in send order; a message typed while a reply is being written renders BELOW that reply; do not invent a scheme that splices a message into the middle of a reply". Both of the earlier rules came from real corrections, and neither was right:

1. The first scheme did splice, and put the message **above work that had already begun** — in the worst case above the working indicator, when nothing had streamed yet. Wrong: the work in flight was started without that message, so nothing about it can precede that work. That is what took five rounds of corrections and was deleted.
2. The replacement put the message **below the whole reply**. That fixed case 1 and broke the common case: the reply that finally answered five messages rendered above all five, which reads exactly like being ignored. The operator reported it in those words on 2026-08-09 and explicitly overrode the old rule: "when i send a new message, SUBSEQUENT system messages (any from once you've been able to see that message) should come AFTER in a new block."

Cutting at `at_segment` is neither. The message goes after what had been emitted and before what had not, which is the only placement that is true. Do not restore "renders below the whole reply", and do not reintroduce a splice that ignores `at_segment`.

Below the blocks, in order: messages queued for the next turn (not sent at all yet), then the voice overlay.

**A run in flight is always visible.** The working indicator is driven by whether the SERVER says a run is active (`active_run`), not by whether this tab holds an SSE socket. A turn whose stream dropped past its reconnect budget, one the operator stopped watching, and one started in another tab are all still running, and all still say so with an elapsed clock anchored to the run's real start. See `turnStatus.ts`.

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
   (Birds: `BIRDS_NAV` in `BirdsHeader.tsx`). To add a page: add a `<Route>` to that app's
   entry in `src/apps/registry.tsx` AND one entry to the section list. Never scatter
   one-off section links in page bodies as the way to reach a section (a contextual CTA
   is fine, but it is not the nav).

   **Routes do not live in `App.tsx`.** The shell has no list of apps: `src/apps/registry.tsx`
   is the one frontend file allowed to name one, and it supplies each app's routes, its
   breadcrumb nodes, its switcher group and any provider the shell must mount. `App.tsx`
   renders `appRoutes()` and knows nothing else. `src/apps/registry.test.tsx` renders the
   assembled tree through the real router, so a route that would blank the screen fails
   in CI rather than in the browser.
3. **Active section is highlighted.** The header marks the current section (`is-active`
   + `aria-current="page"`) so "you are here" is always clear.
4. **Home is always reachable** from the header (Birds: the bird icon → dashboard).

When you add or move a page and the nav doesn't update automatically from the section
list, that's the bug — fix the list, not the page.

## Migration status

The shell + token foundation are live. Pages are migrated to the primitives incrementally; when you touch a page that still uses legacy classes (`.card`, `.muted`, `.pill`, `.btn`, ad-hoc inline styles), migrate the part you touch to `ws-*`/primitives rather than extending the old idiom. As pages migrate, delete the now-dead rules they leave behind in `styles.css` (handoff step 9). The `birds` sub-app has its own established calm UI and is migrated last.
