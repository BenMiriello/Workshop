# Workshop frontend — Design System law

**This file is binding for every change under `frontend/` (and the `mobile/` Expo app). Any UI work — by a human, by the cron/supervisor agents, or by an interactive Claude session — MUST follow it. New screens are designed this way from the start; they do not get a "we'll systematize it later" pass.**

> **A UI change is not delivered until the built origin serves it.** The operator views the BUILT bundle, not the Vite dev server, so hot reload does not reach him. When you have finished editing, run **`bash scripts/apply.sh`** — it rebuilds the bundle if it is stale and does nothing if it is not. This applies to `packages/ui` too: the kit compiles *into* the bundle. See [`docs/applying-changes.md`](../docs/applying-changes.md).

The system is the Workshop Design System, packaged as **`@workshop/ui`**. It lives at **`packages/ui/`**, OUTSIDE this app: `frontend/` is one consumer of the kit, not its owner. It is already implemented:

- **Tokens** — `@workshop/ui/tokens.css` (CSS custom properties: color, space, type, radii, shadow, z, motion) and `@workshop/ui/tokens.js` (the same values as a JS object for the Expo app).
- **Primitives (CSS)** — `@workshop/ui/styles.css` (source: `packages/ui/src/styles/components.css`): every component as a plain, className-based rule (`.ws-*`). Zero inline styles, no Tailwind, no CSS-in-JS, no component library.
- **Primitives (React)** — `packages/ui/src/`: thin className wrappers over the component rules. **Import from `@workshop/ui` — never by relative path, and do not hand-write `.ws-*` markup when a primitive exists.** Two primitives with heavy dependencies sit behind their own subpaths: `@workshop/ui/map` and `@workshop/ui/markdown`.

Both stylesheets are imported in `src/main.tsx` **before** `styles.css`. `styles.css` is the shrinking legacy sheet; never add palette/theme variables to it.

**Editing the kit is a platform change, not an app change.** Read `packages/ui/CONTRIBUTING.md` first: the public surface is snapshotted in `packages/ui/api/ui.api.md`, a removed or renamed export is a MAJOR version bump plus a CHANGELOG migration note, and `check_design.sh` fails if the surface moved and the snapshot didn't. Out-of-repo apps consume this package and nothing here compiles them, so the version number and the changelog are the only warning they ever get. App-level work restyles by setting tokens; it does not edit primitives.

## The ten rules (non-negotiable)

1. **Use the primitives.** `Button, Field, Input, Select, Textarea, Card, PageHeader, SectionLabel, Stack, Row, Badge, StatusPill, StatusDot, AgencyChip, AgencyDot, Tag, Tabs, Toggle, Checkbox, Segmented, Modal, ConfirmDialog, Table/TableScroll, CopyButton, RevealField, StateBlock, Skeleton, ActionBar, Stat, ChartFrame, ChartTooltip` — all from `@workshop/ui` (the full list is `packages/ui/api/ui.api.md`). If you need something not covered, add a primitive to `packages/ui/src` + a `.ws-` rule to `components.css`, bump the kit's MINOR version and re-run `npm run api:snapshot`; don't one-off it inline.
2. **No inline hex colors. Ever.** No `color:"#16a34a"`, no `#ef4444`, no `background:"#fff"`. Color comes from tokens (`var(--accent)`, `var(--muted)`, …) or, far better, from a primitive. `scripts/check_design.sh` fails the build on raw hex in `.tsx`.
3. **Neutral by default; a hue must earn its place.** See "What color is for". Four hues, four questions: MINE (`--you`), LIVE (`--live`), ORIGIN (`--sys`, only where origins mix), OUTCOME (status, outlined). Everything else is `--fg`/`--muted`/`--faint`, and that is most of every screen. A control is not content: carets and icons are neutral at rest.
4. **Status = meaning, mapped to five kinds.** `ok | running | stopped | warn | danger` via `StatusPill` (standalone) or `StatusDot` (dense rows). Never invent a status color, and never put a status on a name: the state word carries it. `running` is the only filled, animated one, because it is liveness rather than an outcome. **Which one it is** (agents / sources / repos / chart series) is `Tag` with a `--cat-*` hue from `catFor(key)`. A run's `trigger` is origin, not categorical: `agencyFor()`, never `catFor()`.
5. **lucide icons only — never emoji.** 14px in section labels/meta, 16px in buttons, 1.8 stroke. Every icon names the thing beside it. `check_design.sh` fails on emoji in `.tsx`.
6. **Hierarchy from position, scale, weight, color — not borders.** `PageHeader` (one per screen) → status band → content grouped under `SectionLabel` (the uppercase motif). Cards float on shadow (light) / a faint ring (dark). 4px spacing grid (`--space-1..8`); use the steps, never invent `13px`.
7. **Editability model, not tap-to-edit.** Anything that writes config or money uses `useEditMode` + a sticky `ActionBar` (Save primary / Cancel ghost, "unsaved" pill while dirty). View prefs (theme, filters, reveal/hide, source `<select>`) commit **on change**. Config/money commit **on explicit Save**. See "When does a control commit?" below.
8. **Destructive actions get `ConfirmDialog`.** `danger` treatment + a dialog naming the consequence and that it can't be undone. Never bare red text, never one-tap delete.
9. **Every list/page ships four states.** loading (`Skeleton`), empty (`StateBlock` — always with an instructional hint, e.g. "POST to `/api/events/<source>` to populate"), error (`StateBlock variant="error"` with the failing endpoint + retry), overflow (paginate / `TableScroll`). **`StateBlock` is a REGION and belongs only where content is absent** — see "A region or a line" below; a failure beside content that is still on screen is a `Notice`.
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

## A region or a line: where a failure is said

**Two shapes, and the question that picks one is whether the content is still on
screen.** Not how bad it was, not whether it is an error at all.

| | `StateBlock` | `Notice` |
|---|---|---|
| what it is | a REGION: centred, tall, bordered | a LINE: icon, sentence, action |
| when | the content is **not there** — a list that failed to load, an empty page, a body that could not be read | the content **is** there and one thing went wrong beside it |
| examples | "Couldn't load agents" filling the page, an empty inbox | a save that was rejected, a form submit that failed, a dropped poll under a full transcript, a `<Select>` whose options would not load |

`StateBlock` used to be the only error shape the kit had, so it got used for
both, and a one-sentence problem rendered as a 120px dashed panel in the middle
of a working page. Reported 2026-08-12 against the chat, where a dropped poll
put one over the composer: "why is this message so huge and blocky... i hate
this format". It was in roughly twenty places.

The test: **would this space be empty if the failure had not happened?** Yes,
it is a region. No, it is a line.

Rules that fall out:

- **A dialog or a form never gets a region for a rejected submit.** The form is
  right there. `Notice` above the buttons.
- **A field-sized slot gets a line.** A dashed panel where a dropdown belongs is
  the shape saying the whole section died, which is not what happened.
- **Name the endpoint where naming it helps someone act** (`where`), leave it off
  where the only move is Retry. The stale-chat notice carried
  `/api/chat/threads/<40 hex chars>/events` and that was two thirds of its
  length.
- **A notice states an outcome, so it is neutral ink with one hue on the icon.**
  See "What color is for". Never a tinted panel.

## A record page is not a list page

**A page whose title is a NAME and a page whose title is CONTENT are different
shapes, and the kit now says so.** `PageHeader` takes `variant="entity"`, plus
`back` and `meta` slots, and `DetailLayout` arranges the body. Reported
2026-08-12 against the ticket page; every detail page in the app had the same
four faults, because the pattern was copy-pasted rather than shared.

| do | not |
|---|---|
| `back={<Link to="/work"><ChevronLeft/> Tickets</Link>}` | a bare `<ArrowLeft/>` inside the `title` slot |
| `variant="entity"` (17px, wraps) | the 24px page-name size on a sentence |
| nothing where a decorative icon was | a ticket glyph on the ticket route |
| `meta={...}` — one muted line of plain text | a rack of `Badge`/`Tag`/`StatusPill` in `sub` |
| `Props`/`Prop` for read-only fields | a column of `Field`s (form ink on read-only values) |
| `DetailLayout` | a `Row wrap` with a `flex: 0 0 260px` rail |

Why each one:

- **An unstyled icon in the `h1` inherits the h1.** `24px`, and a router `Link`
  also takes the MINE hue, so the way OUT of the page outweighed the record on
  it. Back is chrome: muted at rest, `--fg` on hover, and it NAMES its
  destination.
- **A constant is not a signal.** The same rule that governs hue governs icons.
  Every record on `/work/:scope/:id` is a ticket.
- **One record means nothing to scan for**, so nothing on a detail page earns
  chip treatment by default. The status word keeps its tone in the meta line and
  drops the pill. Two exceptions on the ticket page, both earned: the run-in-
  flight pill (LIVE) and the pass/fail check chips on a run (you scan those).
- **`DetailLayout` puts the rail FIRST in the DOM** and moves it right at 900px.
  A rail stacked after the main column lands under the comments, the runs and
  the activity feed, which is how the properties ended up "hidden at the bottom"
  on a phone. Do not re-order it back.
- **Long body text is clamped** (`Clamp`), so a page opens on its own controls
  rather than on paragraph nine of a distilled spec.

The list side of the same complaint: a row's status DOT is not enough on its
own. The word goes in the meta line as plain text ("to do · workshop · 2h"),
because "is this blocked or just queued" is what a ticket list is read for and a
colour answers that only for someone who has already learned the five hues.

Pinned in `frontend/src/styles.test.ts`.

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

## What color is for

**Read this before painting anything.** A hue is information only when it VARIES. Paint a category that is constant inside a view and you have not labelled anything, you have only tinted the view, and the reader correctly learns to ignore it.

That is not hypothetical: the first pass at this coloured "machine work", which is ~90% of a chat log, so it said nothing while landing on chevrons and icons at random. Five different jobs were being done by hue with no rule about which element carried it. The operator's read was "we have ZERO concept of what these colors mean", and he was right.

**Neutral ink is the language** (`--fg` / `--muted` / `--faint`). Most of every screen is neutral. Hue is an exception, and there are four:

| hue | question it answers | treatment | where |
|---|---|---|---|
| **MINE** `--you` | is this mine? | filled / tinted / ring | your bubble, primary action, checked control, selection, focus, active nav, **link hover** |
| **LIVE** `--live` | is this happening? | the ONLY hue that moves | working dots, a running row's clock, a run in flight, an unread count |
| **ORIGIN** `--sys` | whose work is this? | filled chip, **only where origins mix** | the runs feed, and nowhere else |
| **OUTCOME** status | how did it end? | outlined chip / one state word | non-default outcomes only |

### Three tests before spending a hue

1. **If this were neutral, what could the reader no longer tell apart, or stop noticing in time?** No answer, no hue.
2. **Does the thing it marks actually vary on this screen?** A constant is not a signal.
3. **Is something else already saying it?** Then do not say it twice. (A row's identity dot already says who, so its running chip must not also say who.)

### A link is underlined, not tinted

**This entry used to read "links" under MINE, and that was wrong.** `--accent` is
a literal alias of `--you`, and `--you` fills the operator's own message bubble,
so every link the assistant wrote was painted in the exact tone that means "you
typed this". Reported 2026-08-13: "it uses the user message color for links which
is wrong."

Apply the three tests and the answer falls out. If a link were neutral, could the
reader still tell it apart? **Yes, the underline says it**, at any size, in any
theme. So the hue was never carrying the information and does not earn a place.

- **At rest: `color: inherit` plus an underline** at ~45% opacity of the current
  ink. Inheriting is what makes the hard case free: a link inside a `--you`-filled
  bubble is legible with no special case, because it is written in whatever ink
  that surface already uses.
- **On hover: `--you`.** The *act of interacting* is genuinely yours, so the tone
  arrives on the pointer instead of sitting on the text permanently. On a `--you`
  fill it inverts to `--you-fg`, since the tier tone would vanish into it.
- **There is no `--link` token and there should not be one.** A hue that only
  ever restates an underline is a hue spent on nothing.

### A control is not content

Chevrons, carets, icons, and text buttons are affordances: **neutral at rest, `--fg` on hover, and the MINE tone only when the control is ON.** Never give a caret an identity colour. The icons under the chat composer are the canonical case: at rest they are all neutral, because none of them is telling you anything yet.

Likewise: **a name never carries a state.** `Write` is what a step is, `blocked` is how it went, and only the second is coloured.

### LIVE has two durations, and that is the whole idea

- **standing** — still going, or still unseen. Holds the tone throughout: the working dots, a running step's elapsed clock, a run in flight.
- **transient** — it just arrived. Flashes and settles to neutral within a second (`.ws-arrive`), so it says "look here" and then stops saying it.

One learnable sentence falls out: **if it is coloured, it is yours or it is live; if it stays coloured, it is still going.**

The flash is gated on the message's `ts` being newer than the moment the list mounted (`useArrivedAfterMount`). Never flash on first paint of a list, or every row lights at once and the signal is worth nothing. Your own messages never flash: what you just sent is not news to you. Under `prefers-reduced-motion` it lands settled, with no flash at all.

### Agency: recorded everywhere, painted almost nowhere

`data-agency` (`you | system | autonomous`) still records who a thing belongs to, and `agencyFor(trigger)` still maps the platform's `manual | cron | event | loop` vocabulary onto it. But **origin earns a hue only in a list where origins sit side by side and differ** — in practice, the runs feed. Inside one conversation every step has the same origin, so nothing there is painted for it.

Provenance that must stay visible without varying is carried by **shape**: the unattended chat post keeps a rail and an uppercase label permanently, and its tint only on arrival. A cron report from last week is a fact about the past; a bubble still lit up a week later teaches the reader that the colour means nothing.

**Mark the exception, not the default.** Only an agent that can start its own work wears a rail; the ordinary ask-and-it-runs agent gets nothing. Striping every card is how a roster ends up looking sorted by colour while saying nothing.

### Status is outline, and `running` is the one exception

A `StatusPill` is transparent with a hairline in its own tone; a filled chip means MINE or LIVE. `running` is filled because it is not an outcome at all, it is liveness — and it takes **one** live tone everywhere. It used to take the initiating tier's tone, which sounded right and was invisible in practice, because the identity dot beside it had already said who.

`ok` / `warn` / `danger` are byte-identical in all eight schemes: meaning does not move when someone restyles.

### The attributes

Appearance is `data-theme` × `data-palette`, both on `<html>`, both applied by the pre-React bootstrap in `index.html` before first paint:

| attribute | values | storage | default | writer |
|---|---|---|---|---|
| `data-theme` | `light \| dark \| system` | `localStorage["ws-theme"]` | `dark` | Settings → Appearance (`useTheme()`) |
| `data-palette` | `lagoon \| iris \| cobalt \| fern \| clay \| slate \| linen \| graphite` | `localStorage["ws-palette"]` | `iris` | Settings → Appearance (`usePalette()`) |

Eight schemes in two groups, and the groups are a real distinction the picker shows separately. **Expressive** (lagoon, iris, cobalt, fern, clay) spends three hues: `you` takes the base and the machine pair sits together across the wheel. **Quiet** (slate, linen, graphite) spends one hue and separates the tiers by **chroma ratio** instead. A stored id from before the agency model resolves through `resolvePaletteId()` rather than snapping to the default.

**Color is generated, not hand-written.** The model lives in `packages/ui/src/styles/palette.mjs` (eight scheme seeds + the designer's OKLCh stops + derived ladders + the pinned status tones). `packages/ui/scripts/gen-tokens.mjs` emits three files from it — `palettes.css` (all sixteen scheme×mode blocks), `palettes.gen.ts` (the typed menu), `tokens.generated.js` (the same hexes for Expo). To change a color: **edit `palette.mjs`, then `npm run gen:tokens` from `packages/ui/`** (or from the repo root). Editing a generated file is wasted work; `check_design.sh` fails on a stale one. `tokens.css` `@import`s `palettes.css`, so every entry point picks it up.

Rules the model enforces, checked by `packages/ui/src/styles/palette.test.ts` (144 assertions across all sixteen scheme×modes, run by `npm test` in `packages/ui/` — NOT by the frontend's vitest run):

- **The accent family IS the `you` tier.** `--accent`, `--accent-fg` and `--accent-soft` are emitted as `var()` aliases of `--you`, `--you-fg`, `--you-soft`. They are kept because half the app already used them and they were always the same idea; new code should say `--you` when it means the tier.
- **The brand accent IS user-selectable now**, which reverses this file's old "one brand accent, not user-selectable" rule. Identity moved from "the violet" to **the relationship between the three tiers**, and that relationship is the same in all eight schemes.
- **`ok` / `warn` / `danger` are byte-identical in all eight schemes.** Status is meaning; meaning does not move when someone restyles. `stopped` follows the neutral, and `running` follows the actor (above).
- **Every colored text tone clears WCAG AA (4.5:1) on `--bg`, `--card`, `--bg-inset` AND its own `-soft` fill**; every solid mark clears 3:1. The tone is *solved*, not fixed, because a teal at a given lightness is far lighter to the eye than a violet. This is the one place the designer's stop is not the final answer: it moves `warn` in light mode and darkens `you` a step in several light schemes, and nothing else. Dark mode is byte-identical to the handoff.
- **`--you-fg` is not always white.** It is the designer's paired foreground for that scheme, dark on a light tier tone. Never hardcode `#fff` on a `--you` fill.
- **The base block in `palettes.css` is unconditional (`:root`), and must stay that way.** It paints the default scheme for any document, including one carrying a `data-palette` value the stylesheet has never heard of. When it was `:root:not([data-palette])`, a tab holding a renamed id matched no block at all, every token resolved to nothing, and the whole app went white and grey. A color layer must never be able to leave the app unpainted.
- The `--cat-*` categorical suite stays palette-INdependent: a tag color is an identity, so it must not change when the scheme does. Eight schemes cover the wheel, so a cat hue WILL land near some scheme's agency hue; that is handled by treatment, exactly as status is. A categorical tag is a rounded rect with a dot in sans; an agency chip is a pill with no dot, in uppercase mono.
- **Light mode darkens hues near cyan/green** (`lightDrop()`): hues around 170° are intrinsically lighter at a given OKLCh L, so a fixed stop that clears 4.5:1 for vermilion will not for cyan.
- **Surfaces take the `you` hue at ~10% of accent chroma, trimmed per scheme by `bgC`.** Warm and green bases tint neutrals far more visibly than blue or violet ones at equal chroma, so Fern (0.45), Clay (0.55) and Graphite (0.25) trim harder. No contrast check can see this; it is a seed-level constraint, pinned in the tests.
- **The Settings picker's identity strip is not the app's tokens, on purpose.** The live preview renders the scheme's real, very-low-chroma neutrals — correct, but near-black and near-white do not show hue in a 76px chip. `identitySwatch()` supplies the scheme's three agency tones at moderate chroma, mode-independent, as a strip above the preview. What is being chosen is the relationship between the tiers, so all three are shown. Never used outside the picker.

Adding a scheme: add a seed to `SCHEMES` in `palette.mjs`, add the id to the `PALETTES` array in the `index.html` bootstrap, run `npm run gen:tokens`, run the tests. The picker builds itself from the generated list, into the right group.

## Charts (recharts)

Wrap in `ChartFrame` (applies `.ws-chart`; the bundled `.recharts-*` rules restyle recharts without touching JSX). Series colors come from `CHART_SERIES` (categorical, brand violet first — never status hues). Target/reference lines: `CHART_TARGET` (dashed slate). Tooltip: `ChartTooltip` (card/elevation language, never a raw white box). Pick the type by the question: line/area = trend, bar = compare categories (zero baseline), donut = part-to-whole (≤5 slices, total in center), `Stat` = a single number.

## Mobile (Expo)

The `mobile/` app mirrors the same hierarchy and tokens via `@workshop/ui/tokens.js` (`makeTheme(scheme)`, `tokens`, `categorical`, `chartSeries`) — CSS variables don't reach RN, so import the object and build the same primitives in `StyleSheet`. Tabs become the bottom tab bar.

Wired 2026-08-11: `mobile/lib/api.ts` derives its `colors` from `makeTheme("dark")` and re-exports `tokens` for the scales. It previously hardcoded eight hexes from a theme that no longer exists (`#0b0f17`, a `#60a5fa` accent in none of the five palettes) — all eight values changed when it was connected, which is how far it had drifted. **Never add a literal hex to `mobile/`; import from `@workshop/ui/tokens.js`.**

Only the token subpath is importable there. The React primitives are react-dom and cannot render in RN, so `mobile/metro.config.js` maps `@workshop/ui/tokens.js` explicitly and throws a pointed error on any other `@workshop/ui` import rather than letting it fail as a confusing missing module.

## API response types are GENERATED, not hand-written

`frontend/src/api/schema.gen.ts` is generated from the backend's own OpenAPI schema by `npm run gen:api-types` (repo root; no server needed, it imports the FastAPI app). It is committed, so a route contract changing shows up as a reviewable diff, and `python3 scripts/check_generated_client.py` fails when it has gone stale.

**Reach for it before you hand-write a response type.** Use `components["schemas"]["Name"]` for a model and `paths["/route"]["get"]["responses"][200]...` for a response body. `frontend/src/pages/apps/Location.tsx` is the worked example: its four hand-written record fields said `number | undefined` where the backend serialises `number | null`, on the one page whose whole job is reading them.

**A route that does not declare `response_model=` has no generated type**, only `unknown`. That is the backend declining to say what it returns, not a gap in the generator. Fix it there (`scripts/check_response_model.py` tracks the debt) rather than hand-writing another type here. A route CAN name a model and still be unnameable from here, when the model's own fields are `dict[str, Any]`: the generated field is then `Record<string, never>` and TypeScript will refuse even a cast. `Deploy.target` / `.source` / `.current` are the worked example, and that debt is `scripts/check_opaque_fields.py`'s. So do not assume a type you find is the real contract.

**This is gated, and the numbers are measured.** `python3 scripts/check_response_types.py` counts the RESPONSE BOUNDARIES that do not name a generated type - the point where a body is given a shape, which is the only place a claim about the wire is made. Pinned in `scripts/response_types_baseline.json`, wired into `reviewer.sh` in delta mode, and it may only go down. Measured 2026-09-01: **92 hand-written, 143 with no type at all, 120 of those two reading a COLLECTION** (down from 100 / 156 / 140 the same day). The older "~528" figure counted type DECLARATIONS, which is a different unit and not reproducible; a declaration is inert, a boundary is where the bug lives.

Convert the LIST reads first. A wrong shape on a detail page renders `undefined` and someone notices; a wrong shape on a list renders the empty state, which is indistinguishable from "there is nothing here" and is what hid the `Page[T]` rename for two weeks. `python3 scripts/check_response_types.py --list` prints them, list surfaces first, with the endpoint beside each.

**Never edit the generated file.** Change the route, run `npm run gen:api-types`, commit both.

## Look at it. In a browser. Before you say it is done

**`bash scripts/shoot.sh <path> [width] [height] [out.png]`** loads the running
built app in a real Chromium, screenshots it, and reports console errors, page
errors and failed requests. First use installs the browser and mints a session by
itself, so there is no setup step to remember. It defaults to a 390x800 phone
frame, because that is what the operator reviews on and where things break first.

The origin, the auth, the viewports and the browser are not in the script: this
tree declares them in `workshop.preview.json`, and any other app declares its own
in its manifest. [`docs/previewing-an-app.md`](../docs/previewing-an-app.md) has
the declaration shape and the per-stack defaults.

**Read the PNG.** Screenshotting and not looking is worse than not screenshotting,
because it produces a confident report about a screen nobody saw.

This is not optional polish, it is a gate, and it exists because of a measured
failure. On 2026-08-22 three surfaces were built to a written spec, passed
`npm run typecheck`, passed `check_design.sh`, passed 1,600 tests, and were
wrong: a file list with 290px of duplicated chrome above it (an h1 reading
"Files" directly under a tab reading "Files"), tab labels riding low because a
flex child was bottom-aligned, and a menu of entirely the wrong shape. Not one
of those is visible to a type checker or a unit test, and every one of them is
obvious in a screenshot. The operator saw all three within minutes of opening
the app and asked why an agent working on a React app could not simply look.

So: **for any change to what a page LOOKS like, shoot it at 390 wide and at 1440
wide before you report it done, and say what you saw.** A test proves a rule
holds. A screenshot is the only thing that proves the screen is right.

Two practical notes. The tool reads the BUILT origin, so run `bash
scripts/apply.sh` first or you will be looking at your previous change. And the
390px shot emulates touch, matching the app's own 900px WIDE breakpoint, so a
mobile-only regression shows up there and nowhere else.

## Before you commit UI

Run `bash scripts/check_design.sh` (from repo root) — it flags raw hex, emoji, and `not-allowed` cursors in `.tsx`, and confirms the foundation imports are intact. It is part of the completion gate; a UI change that fails it is **not** done. Also run `npm run typecheck` from the repo root.

**Never `npx tsc`.** `tsc` is a deprecated stub package on npm that npx resolves ahead of the local TypeScript binary; it prints "This is not the tsc command you are looking for" and **exits 0**, checking nothing. Use `npm run typecheck` (kit + frontend) from the repo root. Don't reach for the binary by path either: a root workspace install hoists typescript, so `frontend/node_modules/.bin/tsc` is present on some checkouts and absent on others.

**Tests: `cd frontend && npm test`** (vitest, `*.test.ts` beside the module). If you touched `packages/ui/`, also run `npm test --workspace @workshop/ui` — the palette contract tests live with the kit and the frontend run does not pick them up. `npm test` from the repo root runs both. There is no DOM/render harness and none is planned — the target is the PURE RULE behind a behaviour, extracted into its own module so it can be executed. `turnBatching` (when a queue of typed-ahead messages becomes a turn) is the pattern.

### Chat log order: a mid-turn message CLOSES the assistant's block

**An assistant block is a contiguous run of work, bounded by the messages that interrupted it.** A message sent while a reply is being written ends that block; work produced after the message starts a NEW block below it. A long turn the operator interrupted five times renders as six stretches of work with the five messages between them, each sitting where it actually landed.

**Where the cut is, is RECORDED, not derived.** The server writes the message's cut token (`meta.cut_id`) onto the segment it arrived after (`seg.cut_after`), or onto the reply's `cut_head` when the turn had emitted nothing yet, in the same call that seals the open text. The client matches tokens and does no arithmetic. The rule is implemented once, in `src/components/chat/transcriptBlocks.ts`, and tested in `transcriptBlocks.test.ts`. Nothing else may arrange the transcript.

It used to be an INDEX (`meta.at_unit`) resolved at render time against a segment list that had grown since, and an index cannot survive that: with two delegations running at once, the first one gaining steps slid the index into it and dropped the SECOND delegation below a message typed after it was launched. Measured 2026-08-12. `legacyPieces` still resolves the index, for the transcripts already on disk and for nothing else. Read [`docs/decisions/0018`](../docs/decisions/0018-transcript-order-is-recorded-not-reconstructed.md) before touching any of it.

**A cut lives on a segment, so a segment carrying one may not be dropped or replaced without carrying its cut forward** (`build_streaming.carry_cuts`). That is the one way this design rots silently.

**And the LIVE half of it arrives as a frame, so the wire is part of the rule.** The tab that is streaming builds its own copy of the segments from SSE events and never sees the server's list until the turn commits, so the recorded position reaches it only via the `cut` frame. That frame was pushed by the server and handled by the store's reducer from the day this shipped, with **no listener between them for a day and a half** — so the committed transcript obeyed the rule, the live one silently fell back to the index it replaced, and it read as a fix that would not hold. The vocabulary now has one owner (`chat/streamWire.ts`): `useTurnStream`'s handler table is a `Record` over the kinds declared consumed there (a missing handler does not compile), listeners are registered by iterating it (a handler cannot be left unsubscribed), and `streamWire.test.ts` reads the backend and fails when it emits a kind the client has never heard of. **Adding a server event means declaring it there first.**

**Read this before "fixing" it back.** This file used to say the opposite — "append-only, in send order; a message typed while a reply is being written renders BELOW that reply; do not invent a scheme that splices a message into the middle of a reply". Both of the earlier rules came from real corrections, and neither was right:

1. The first scheme did splice, and put the message **above work that had already begun** — in the worst case above the working indicator, when nothing had streamed yet. Wrong: the work in flight was started without that message, so nothing about it can precede that work. That is what took five rounds of corrections and was deleted.
2. The replacement put the message **below the whole reply**. That fixed case 1 and broke the common case: the reply that finally answered five messages rendered above all five, which reads exactly like being ignored. The operator reported it in those words on 2026-08-09 and explicitly overrode the old rule: "when i send a new message, SUBSEQUENT system messages (any from once you've been able to see that message) should come AFTER in a new block."

Cutting at `at_segment` is neither. The message goes after what had been emitted and before what had not, which is the only placement that is true. Do not restore "renders below the whole reply", and do not reintroduce a splice that ignores `at_segment`.

**And the turn's own start is the floor.** The cut says where a message sits among the work; it never licenses the message to sit above the turn that was already in flight. A turn announced itself with the working indicator before the operator typed, so the indicator stays above what he typed. This is the case `at_unit: 0` produces, and it is not an edge case: it is what every message sent in the first seconds of a turn looks like. `workingPieceIndex` in `transcriptBlocks.ts` is the whole rule (last stretch, or the first when the turn has emitted nothing, because an empty stretch renders as nothing and the message would float to the top of the turn). It is the only placement rule left there that is not "render them in recorded order". Reported 2026-08-12: "if it was already working my new message could not be BEFORE it since it wouldn't have seen it." Pinned in `transcriptBlocks.test.ts`.

Below the blocks, in order: messages queued for the next turn (not sent at all yet), then the voice overlay.

**A turn in flight is always visible.** The working indicator is driven by whether the SERVER says a turn is active (`active_turn`), not by whether this tab holds an SSE socket. A turn whose stream dropped past its reconnect budget, one the operator stopped watching, and one started in another tab are all still running, and all still say so with an elapsed clock anchored to the turn's real start. See `turnStatus.ts`.

Note: `scripts/reviewer.sh` runs the BACKEND suite as a ratchet when Python
changed (`check_backend_tests.py`, known failures pinned by id, a new one
fails the run). It still does not run the frontend's vitest, so run
`cd frontend && npm test` yourself.

## App navigation (one header per app, one source of sections)

Every multi-page app under `pages/apps/<app>` MUST navigate through a single shared
header component, with its sections defined in ONE place and the current section
highlighted. This is non-negotiable: it is what keeps an app navigable and stops
sub-pages from feeling like they replaced the home page.

Rules:
1. **One header, every page.** Each app has exactly one header component (e.g. `WidgetsHeader`).
   Every page of that app renders it as its top element. Do not hand-roll a per-page
   `<h2>` header or a bespoke toolbar.
2. **One source of sections.** The navigable sections live in a single exported list
   (e.g. `WIDGETS_NAV` in `WidgetsHeader.tsx`). To add a page: add a `<Route>` to that app's
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
4. **Home is always reachable** from the header (e.g. the app icon → dashboard).

When you add or move a page and the nav doesn't update automatically from the section
list, that's the bug — fix the list, not the page.

## Migration status

The shell + token foundation are live. Pages are migrated to the primitives incrementally; when you touch a page that still uses legacy classes (`.card`, `.muted`, `.pill`, `.btn`, ad-hoc inline styles), migrate the part you touch to `ws-*`/primitives rather than extending the old idiom. As pages migrate, delete the now-dead rules they leave behind in `styles.css` (handoff step 9). An app with its own established UI may be migrated last.
