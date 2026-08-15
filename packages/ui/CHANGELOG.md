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

## 0.5.11 — 2026-08-15 — a panel is not a menu

**Compatible (one name added).** `Popover`: a panel anchored to a trigger.

```tsx
<Popover trigger={<Button icon={<Ticket />} />} label="Work" side="bottom">
  ...chips, rows, an answer field, a Send button...
</Popover>
```

`role="dialog"`, `aria-label={label}`, and both controlled (`open` +
`onOpenChange`) and uncontrolled. Closes on Escape and on an outside
pointerdown, and on nothing else. Escape returns focus to the trigger; an
outside click deliberately does not, because a programmatic `.focus()` after a
mouse dismissal re-triggers the global `:focus-visible` ring and reads as a
border the app stuck on the control for no reason.

**Why `Menu` could not be used.** Menu is a list of choices, and two of its
behaviours are load-bearing there and wrong here. It owns arrow-key roving focus
over `[role="menuitem"]`, so it would take Up/Down away from a text field inside
the panel; and it closes on any click landing in a menuitem, because picking one
of a set of actions ends that interaction, whereas a click in a panel is work
being done. It is also `role="menu"`, which tells a screen reader the arrow keys
walk the contents. The first consumer is the chat's Work panel: filter chips,
ticket rows, an answer field and a Send button.

**Why not `Modal`.** A modal traps focus, dims the page and sits in the middle
of the viewport. This is attached to the button that opened it and has to flip
and re-align against every clipping ancestor, which is what `menuPlacement`
already solves. It does not trap focus and it autofocuses nothing: opening a
panel must not raise a phone keyboard or take the caret off the composer.

**Internal refactor, no surface change.** Menu's measuring layout effect and its
`clipBox` walk moved into `useAnchoredPlacement` (not exported), which both
primitives now call, so the flip rule has one implementation rather than two
copies of the arithmetic that has already been got wrong once. The pure half of
`clipBox` came out with it as `narrowClip` in `menuPlacement.ts`, with tests
beside `placeMenu`'s: it is the per-axis narrowing, which is the part that
matters (`overflow-x: hidden` with a visible y is the chat log exactly, and
collapsing the two axes into one test is the bug).

New CSS: `.ws-popover`, `.ws-popover__surface` and its four placement
modifiers, beside the `.ws-menu` rules. Card surface one elevation step below a
modal, roomier padding than a menu row, and the same clamped max-height with an
internal scroll, which the placement rule depends on: the surface is measured
after its own CSS has capped it.

## 0.5.10 — 2026-08-15 — a control is not a flex sibling of the card it decorates

**Compatible (fix, no exports changed).** Three `.ws-notes-grid` / `.ws-note-card`
CSS fixes, all found auditing a regression on the Memory page's Library list:

- `.ws-note-card__link` (the chat card's wrapping `<a>`) had no rule at all, so
  the browser's default blue-underlined anchor painted every descendant inside
  it, including plain-text timestamps. Now `color: inherit; text-decoration:
  none`.
- `.ws-note-card__footer .ws-tag` gets `white-space: nowrap`: a scope tag was
  wrapping onto a second line mid-word inside its own pill at card widths its
  label didn't fit. `Row wrap` on the footer already moves the whole tag to
  the next line as a unit when needed; this only stops it breaking internally.
- New `.ws-note-card__share`: an absolutely-positioned corner slot for a
  per-item control (Memory's chat-sharing badge) that must sit OUTSIDE a
  card's `Link` — a control nested inside the anchor, or added as a second
  flex child of `.ws-notes-grid > li`, either breaks operability (a button
  inside an `<a>`) or breaks layout (two flex children fighting the grid
  track's width, which is what actually shipped and squeezed every chat card
  to a third of its column with the control spilling into the next one).
  Absolutely-positioned children of a flex container are not flex items, so
  this costs the grid's single-child stretch rule nothing.

## 0.5.9 — 2026-08-14 — a bottom bar that is actually at the bottom

**Compatible (one name added).** `PageActionBar`: a page's primary action,
pinned to the bottom of the shell's content region.

It renders into the `OverlayHost` the shell already provides, so
`position: absolute; bottom: 0` is the bottom of that region BY CONSTRUCTION.
That is the point. The two attempts before it were sticky bars inside `<main>`,
which is the scroller, so where they landed depended on the scrollport's exact
box and the scroller's own padding; both left the bar hovering above the bottom
with the list scrolling through the strip underneath, and the second added a
page-level negative margin to compensate, which is the shape of a fix that is
guessing. There is nothing left to compensate for.

`--ws-pagebar-h` is a token because a bar outside the scroller cannot lengthen
the page, so the PAGE has to reserve exactly that much room at the end of its
content. One number, two readers.

## 0.5.8 — 2026-08-14 — a filter row, a board, a quiet tag, and a caret that stops running away

**Compatible (six names added, one optional prop, one CSS fix).**

New: `FilterBar` + `FilterChip` (search, one removable chip per active filter,
an add affordance, actions pinned right), and `Board` + `BoardColumn` +
`BoardCard` (fixed-width lanes in a horizontal scroller). Also `MenuCheckItem`,
a `role="menuitemcheckbox"` row: a menu closes itself when a `[role="menuitem"]`
is activated, because choosing one of a set of actions ends that interaction,
and ticking one of six statuses does not. Arrow keys reach both roles. Both come from the
tickets redesign, and both are built generic because the shapes recur: the
filter row is meant to replace the ad-hoc `Select` rows in Work, Memory and the
runs feed, and a board is a board.

`FilterBar` is a LAYOUT primitive, not a filter engine. What is shared between
those three pages is the row (how it wraps, which child absorbs slack, what a
chip looks like); what is not shared is where the options come from and what
picking one does. Owning that here would mean the kit learning what a scope is.

`Tag` takes `variant="neutral"`: hue in the dot only, pill neutral. For rows of
tags, where six tinted pills read as six competing signals while saying no more
than six dots do. The default is unchanged, so no caller moves.

**The fix.** `.ws-select` paints its chevron with `background-image` +
`background-position`, and every state rule set the `background` SHORTHAND,
which resets the longhands it omits. `:hover` therefore set
`background-image: none; background-position: 0% 0%` — and the transition
named the shorthand too, so the reset ANIMATED: the caret slid from the right
edge toward the left, vanished halfway (an image is a discrete animation type,
so it flips at 50%), and slid back on un-hover. `:focus` did the same, which is
why the caret stayed gone the whole time a select was open, and `[disabled]`
deleted it outright.

Reported 2026-08-14: "any time there's a dropdown it'll transform to the left
and then back to the right in a really weird way and it'll disappear." Every
state rule now sets `background-color` and the transition names the longhand.
Pinned in `frontend/src/styles.test.ts`, which parses the real sheet and fails
on a `background:` shorthand or a bare `background` transition in any rule whose
selector mentions `.ws-select`.

## 0.5.7 — 2026-08-14 — two surfaces, because one token had two opposite jobs

**Compatible (one token added, one token's value changed).** New `--bg-sunken`,
a recessed panel for content holding a separate context. `--bg-block` goes back
to a whisper: light 0.855 to 0.965 L, dark 0.365 to 0.27.

This is the fourth pass on these colours and the first that changes the SHAPE of
the problem rather than the number. Workshop's chat needs a grouped block of tool
calls to be soft (it sits inside a message and must not shout over it) and a
delegation to be unmistakable (it holds a whole separate context). Both demands
were being made of `--bg-block` alone, and in light mode they are unsatisfiable
together: the card is 0.995 and `--bg-inset` was 0.945, so soft-enough-for-the-card
landed ON the delegation, and clear-of-the-delegation was loud against the card.
The delegation was sitting in the only place the block could go.

Three passes moved the block further and further out, fixed the collision, and
produced the opposite complaint. Splitting the surfaces resolves it: the block is
a whisper (~1.09 against its card) and the panel is the deep step (~1.22 against
the block), which also matches what the two things ARE. A group is this turn's
own steps behind a count; a delegation is a separate context, and it carries a
border as well. Fill against frame, soft against deep.

`--bg-inset` is unchanged and still the page-level inset. The lesson worth
keeping: it is a PAGE surface, and reaching for it inside a card is what put two
unrelated things on the same colour.

Bounds are asserted for all sixteen scheme x modes: the block stays under 1.18
against its card, the panel clears 1.15 against the block, and `--muted` and
`--fg` clear AA on both.

## 0.5.6 — 2026-08-14 — `--bg-block` moves as far as the ink on it allows

**Compatible (one token's value changed).** Light 0.90 to 0.855 L, dark 0.30 to
0.365. Separation from `--bg-inset` goes from 1.14 to 1.32 in light and 1.31 to
1.68 in dark.

0.5.5 fixed a genuine collision (3/255 apart in every light scheme) and the
result still read as the same colour to the operator. The lesson is in the test
rather than the value: the old assertion was `>= 1.1`, which proves two surfaces
are not identical and proves nothing about whether anyone can tell them apart. A
measurement that disagrees with the eye is the measurement being asked the wrong
question. The bound is 1.25 now.

The new value is set by the INK, not by taste. A block carries text (`--muted`
rows), and `--bg-block` is deliberately not in `surfacesOf`, so text tones are
not solved against it; pushing the two surfaces further apart to satisfy the eye
would otherwise walk that text under AA. `--muted on --bg-block` is now asserted
at 4.5:1 for all sixteen scheme x modes and sits at ~4.6 worst case, so this is
close to as far as the surface can travel without the rows on it getting harder
to read.

## 0.5.5 — 2026-08-14 — `--bg-block`, a surface that is not on the same side in both modes

**Compatible (one token added).** New generated surface `--bg-block`: a block of
grouped content sitting on a card, guaranteed distinct from BOTH `--card` and
`--bg-inset` in every scheme and mode.

It exists because "distinct" cannot be expressed as a relative fill here.
Elevation is not symmetric across the two modes: `--bg-inset` is the lowest
surface in both (dark 0.205 L against a 0.235 card, light 0.945 against 0.995),
so a block drawn as "the card lifted toward `--fg`" moves away from `--bg-inset`
in dark and lands on top of it in light. The Workshop chat log did exactly that,
and its grouped-tool-call fill came out within 3/255 of the delegation fill in
all eight light schemes while sitting a comfortable 26/255 clear in all eight
dark ones. Correct everywhere it was checked, wrong in half the app.

`--bg-block` is authored per mode in `palette.mjs` (dark 0.30 L, above the card;
light 0.90 L, one clear step BELOW `--bg-inset` rather than between it and the
card) and `palette.test.ts` now asserts the separation from both neighbours for
all sixteen scheme x modes, so it cannot silently close again.

Use it for a run of related rows gathered into one block inside a card. It is not
a hover (`--card-hover`) and not a recessed panel (`--bg-inset`).

## 0.5.4 — 2026-08-14 — a menu stays inside the box that clips it

**Compatible (behaviour fix, no surface change).** `Menu` measured its popover
against the VIEWPORT and nothing else, so a menu inside a clipping scroller
opened outward and was sliced off at the scroller's edge while the viewport
still had room to spare.

The chat is exactly that geometry: `.chat-log` is `overflow-x: hidden` and the
column is capped at `--page-max` and centred, so on a desktop a menu opening
from a message bubble had hundreds of pixels of viewport to its right, never
flipped, and lost half of itself at the log's edge. Reported 2026-08-14.

- **The clip box is the viewport narrowed by every ancestor that clips**, asked
  per axis (`overflow-x: hidden` with a visible y is a real combination, and
  clipping one axis says nothing about the other).
- **`side` is now a preference, not a mandate.** It flips top/bottom on the same
  rule `align` already used: flip only when the preference is genuinely clipped
  and the other way fits. A menu opening upward from the first message in a
  scrolled-to-top list used to open into nothing.
- **Placement is projected, not read back.** It used to measure where the list
  currently sat, so a menu that had flipped measured its flipped position, saw
  no overflow, and stayed flipped even after the window grew back.
- **Re-measures on ancestor scroll**, not only on resize. Scrolling does not
  close a menu, so the side that fitted when it opened may not fit a moment
  later.

The arithmetic moved to `src/menuPlacement.ts` (internal, not exported) so it
can be executed by a test; the kit has no render harness, and this was a bug in
one comparison that nothing could reach.

## 0.5.3 — 2026-08-13 — a link is underlined, not tinted

**Compatible (styles only, no surface change).** Four colour corrections, all of
them a token that was saying the wrong thing:

- **`.ws-md a` no longer takes `--accent`.** `--accent` is an alias of `--you`,
  which fills the operator's own message bubble, so every link in every rendered
  markdown body was painted "this is yours". Links are now `color: inherit` with
  the underline carrying the affordance, and `--you` on hover, where the MINE
  tone actually belongs. Reported against the chat, 2026-08-13. The rationale is
  in `frontend/CLAUDE.md` under "A link is underlined, not tinted".
- **`.chat-voice-orb.is-speaking`** moves `--ok` → `--running`. Speaking is
  liveness, not an outcome, and it now matches its `is-listening` sibling.
- **`.chat-voice-orb.is-idle`** moves `--danger` → `--stopped`. Idle is not a
  failure; `--stopped` exists for exactly this and was unused.
- **`.chat-voice-now`** moves `--accent` → `--live`. The words being spoken right
  now are live, not owned.

Consumers who relied on links being accent-coloured should set `.ws-md a { color:
var(--you) }` locally, but read the CLAUDE.md entry first: the underline was
always doing the work.

## 0.5.2 — 2026-08-12 — a record page is not a list page

**Additive.** `PageHeader` gains `back`, `meta` and `variant`; new primitives
`DetailLayout`, `Props`/`Prop` and `Clamp`, with `.ws-page-header--entity`,
`.ws-page-header__back`, `.ws-page-header__meta`, `.ws-detail*`, `.ws-props*`
and `.ws-clamp*` in `components.css`. Nothing removed, nothing renamed: the
existing `PageHeader({title, sub, actions})` call is unchanged and stays the
default (`variant="page"`).

Every detail page in the app had hand-rolled the same header inside the `title`
slot, and it was wrong the same way four times:

- **A bare lucide arrow inside the `h1`.** An icon with no size rule inherits
  the h1's `font-size`, so the arrow rendered at 24px, and a router `Link`
  takes the MINE hue, so it was also the only coloured thing up there. The way
  OUT of a page outweighed the record on it. `back` renders a named link above
  the title in muted ink, per "a control is not content".
- **A page-name title carrying content.** `--fs-xl` (24px) is right for
  "Tickets" and wrong for one ticket's sentence, which wraps to three lines and
  becomes a banner. `variant="entity"` sets the title at reading size and lets
  it wrap.
- **A decorative entity icon.** A ticket glyph on the ticket route marks
  nothing: it is constant, and a constant is not a signal (same rule as hue).
- **`sub` as a chip rack.** Four `Badge`/`Tag`/`StatusPill` objects in a row,
  none of which the reader scans for, because there is exactly one record on
  the page. `meta` is one muted line of plain text with the status word keeping
  its tone.

`Props`/`Prop` exist because the rails were built out of `Field`, whose label is
13px `--fg` medium — correct beside an input, wrong beside a value that is only
read, so every rail looked like a disabled form. Its columns auto-fill: one
column in a 260px rail, two or three across a phone, with no media query at the
call site.

`DetailLayout` puts the rail FIRST in the DOM and moves it right at 900px, which
is what makes the narrow layout right. Stacking a rail after the main column
buried the properties under the comments, the runs and the activity feed;
reported 2026-08-12 as "you've HIDDEN properties down at the bottom".

`Clamp` cuts long body text to a height and reveals the toggle only when the
content actually overflows (it measures; a line count says nothing about how
tall a code block or an image is).

## 0.5.1 — 2026-08-12 — a failure beside content is a line, not a region

**Additive.** New primitive `Notice`, plus `.ws-notice*` in `components.css`.
Nothing removed, nothing renamed.

`StateBlock` is a REGION: centred, tall, dashed, sized to stand in for a list
that could not load. It was the kit's only error shape, so it also got used for
every failure that happened NEXT to content that was fine, and a one-sentence
problem rendered as a 120px panel in the middle of a working page. In the chat
that meant a dropped poll put a dashed slab over the composer; the operator's
words on 2026-08-12 were "huge and blocky", and roughly twenty call sites across
the app had the same shape.

`Notice` is the other half of rule 9: one line, an icon, an optional cause, an
optional endpoint, an optional action. No border, no fill, no centring. Use it
whenever the screen still has its content; keep `StateBlock` for the empty
region.

## 0.5.0 — 2026-08-12 — one list row, and status stops being a labelled chip

**Breaking (CSS).** `.ws-tickets` and `.ws-ticket-row*` are renamed
`.ws-list` and `.ws-listrow*`: the shape is used by the ticket list AND the run
feed now, so it is not a ticket thing. Removed `.ws-job-card*` and
`.ws-jobs-grid` with the card layout they styled. Added `.ws-listrow__mark`,
`.ws-listrow__scope` and `.ws-list__children`. No exported component changed.

**Why the chips went.** Operator, 2026-08-12: "why does all info have to be a
pill and all has a color dot". A ticket row carried a `StatusDot` with its label
and a coloured `Tag` for scope; a run card carried a `StatusPill`, an
`AgencyChip` repeating the trigger its `AgencyDot` had already marked, and a
child-runs button, over four bands. Two or three chip-shaped objects per row,
which is exactly how you stop any of them being scannable.

The rule now: **a chip is for something you scan a list FOR; plain text is for
something you read once you are already looking at the row.** So status is a
bare dot with no label (five states, learned once, and the accessible name is on
the wrapper), scope and trigger and duration are muted text, and the ticket
list drops the scope entirely when it is already filtered to one scope. The
status vocabulary is unchanged: same five kinds, same colours, same pulse on
`running`. Only the mark got smaller.

`.ws-listrow` is a two-column grid (mark, then content) so the same markup is
two lines on a phone and one line from 720px.

## 0.4.0 — 2026-08-12 — the shell can no longer scroll its own toolbar away

**Breaking (CSS).** Removed `.ws-board*`, `.ws-ticket-card*` and
`.ws-ticket-grid`: the board and the card grid they styled were deleted from the
tickets surface. Added `.ws-tickets`, `.ws-ticket-row*` and `.ws-tickets-bar*`.
No exported component changed, so the API snapshot is unmoved.

**The fix that matters.** `.ws-root` and `.ws-app` are the same element in
`App.tsx`, and the box-sizing reset read `.ws-root *` — descendants only. So the
shell itself was `content-box`, and `.ws-app`'s `height: 100dvh` plus
`padding-top: env(safe-area-inset-top)` resolved TALLER than the viewport
wherever the notch inset is non-zero. The document scrolled, and the global
toolbar scrolled off the top with it. It reproduced on phones and installed
PWAs only, so it read as a mobile layout bug rather than a broken invariant.

The reset now covers the root itself, and `.ws-app` adds `max-height: 100dvh`
and `overflow: hidden` so nothing inside can push the shell at all. `<main>`
remains the single scroller. `src/styles/shell.test.ts` pins all of it, and the
consuming app pins the matching `html, body { height: 100%; overflow: hidden }`.

**The ticket row.** The list was a `.ws-table` with `min-width: 520px` inside a
horizontal scroller, which on a phone is a narrow window onto a wide grid: you
scrolled sideways to read a title, and the columns that did fit were too tight
and wrapped. `.ws-ticket-row` is one row per ticket that never scrolls sideways
at any width: two lines below 720px, one line above it, title and meta both
clipped rather than wrapped.

## 0.3.2 — 2026-08-12 — two chevrons that never rotated

Patch. No API change; the snapshot is unmoved.

`.ctx-expand__chev` and `.mcp-name__chev` declared
`transition: transform var(--motion-fast) ease`. There is no `--motion-fast`
token in this system and there never has been. With no fallback the `var()`
is invalid at computed-value time, which invalidates the whole `transition`
declaration, so both chevrons snapped rather than rotating. Both now use
`--dur-fast` and `--ease-in-out`, the real tokens, which also means they are
finally zeroed by the reduced-motion block that rewrites `--dur-*`.

## 0.3.1 — 2026-08-12 — the focus ring is a shadow, so stop suppressing an outline

**Fix.** An open `Menu` popover sometimes drew a hairline highlight where its
elevation shadow should have been, and whether it did depended on whether the
last thing you touched was the keyboard or the mouse. Same click, same control,
different result.

`.ws-root :focus-visible` indicates focus with `box-shadow`, deliberately, since
an outline cannot follow a control's own radius. Two places tried to opt out of
it with `outline: none`, which does nothing to a box-shadow: the ring stayed,
and because `box-shadow` REPLACES rather than adds, the element also lost the
shadow it already had. That is why it read as a border rather than a highlight.

The ring is now scoped to `:focus-visible:not([tabindex="-1"])`. An element you
cannot tab to was never navigated to by keyboard, so a keyboard-navigation cue
on it is a contradiction; those elements (the `Menu` popover, `Stepper`'s
buttons) are focused programmatically only so arrow keys have a starting point,
and it is that programmatic focus whose `:focus-visible` match varies by input
modality. Outline is still reset for everything.

No API change. Consumers get the fix by upgrading; anything relying on a ring
around a `tabindex="-1"` element will lose it, which is the intent.

Also: `.ws-palette` now clears the ring with `box-shadow: none` rather than the
no-op outline, so keyboard-focusing a scheme no longer draws a rectangle around
the whole button in addition to the ring on its preview.

## 0.3.0 — 2026-08-11 — the agency axis: who did this, not just how it went

**Breaking.** The palette answered one question, *is it healthy* (the status
five). It now answers a second, which for a system that runs sweeps and crons is
the more important one: *was that me, or something moving without me.*

New axis, three tiers, each with a resting and an active (`-up`) tone:

| tier | meaning |
|---|---|
| `you` | your hand on it: typed, clicked, confirmed |
| `system` | machine work inside a conversation you are present for |
| `autonomous` | it moved with nobody watching |

Two rules the kit now enforces:

1. **Agency is FILLED, status is OUTLINE.** `StatusPill` lost its soft fill and
   took a hairline in its own tone; the new `AgencyChip` is a filled pill with
   no border. That pairing, not hue, is what separates the two vocabularies, so
   a hue coincidence in one scheme is survivable.
2. **One agency color per surface.** `data-agency` on any element binds the tier
   for everything inside it. Provenance beats mechanism: an autonomous agent
   calling a tool leaves the surface autonomous.

`agencyFor()` returns `AgencyTier | null`: **null when the record carries no
trigger**, so a caller renders no provenance mark rather than guessing one. The
autonomous tier says nobody was watching, and a false alarm on work the operator
did himself is worse than an absent mark. `discord` maps to `system`, not
autonomous: an agent you message is answering you, with you present.

New exports: `AgencyChip`, `AgencyDot`, `agencyFor`, `agencyProps`,
`AGENCY_LABEL`, `AgencyTier`, `resolvePaletteId`, `LEGACY_PALETTES`,
`PaletteGroup`. New CSS: `.ws-agency`, `.ws-agency-dot`, `.ws-agency-rail`,
`[data-agency]`, `.ws-palette-group`, `.ws-nav__count`, `@keyframes ws-ring`.

New tokens per scheme: `--you`, `--you-fg`, `--you-soft`, `--sys`, `--sys-soft`,
`--sys-up`, `--auto`, `--auto-soft`, `--auto-up`. `--accent`, `--accent-fg` and
`--accent-soft` are now aliases of the `you` family, so nothing that used them
had to change; the brand accent and "your hand on it" were always the same idea.

**`--running` is no longer one fixed color.** A run you started, a run inside
your chat and a run nothing asked for are three different facts, so `running`
resolves to the initiating tier's active tone (default `system`, re-pointed by
`data-agency`). It is also the one status that stays filled: `running` describes
an actor, not an outcome. Terminal states never split by tier.

### Migration

- **The five palettes are gone; there are eight schemes**, in two groups.
  Expressive (`lagoon`, `iris`, `cobalt`, `fern`, `clay`) spends three hues;
  quiet (`slate`, `linen`, `graphite`) spends one and separates the tiers by
  chroma. `PaletteId` changed values accordingly and the default is now `iris`.
  A stored id from before resolves through `resolvePaletteId()`
  (`violet`/`plum` → `iris`, `teal` → `lagoon`, `sandstone` → `linen`,
  `cobalt` kept its id and its 258 hue), so no saved preference is lost.
- **`PaletteMeta.swatch` is now `{ you, sys, auto }`**, not a single string: what
  a scheme IS is the relationship between its three tiers, so the picker shows
  all three. `PaletteMeta.group` is new.
- **Consumers styling a status chip against its old fill must re-check
  contrast** — the fill is gone, and the chip is now hairline-on-transparent.
- The brand accent is user-selectable now, reversing the old "one brand accent,
  not user-selectable" rule. Identity moved from the violet to the relationship
  between the tiers, which is the same in all eight schemes.

Color model rewritten around the handoff's generator (`palette.mjs`): eight
scheme seeds, the designer's stops, and per-scheme chroma/lightness trims. Dark
mode is byte-identical to the handoff; in light mode every colored TEXT tone is
walked to 4.5:1 against the surfaces and its own soft fill, which moves `warn`
and darkens `you` a step in several schemes. 144 contract assertions in
`palette.test.ts`.

## 0.2.5 — 2026-08-11 — ticket link rows, comments, board lanes, phone fixes

Compatible. CSS only, no export or prop change.

New rules: `.ws-ticket-link` (a linked ticket as a whole clickable row),
`.ws-comment` (one entry in a discussion thread), and `.ws-board` +
`.ws-board__lane/head/count/items/empty` (fixed-lane board that scrolls
horizontally rather than crushing its columns).

Two phone fixes at `max-width: 640px`. `.ws-ticket-card__pills` now wraps
instead of clipping: the `nowrap; overflow: hidden` that keeps a grid of cards
one height was chopping scope tags mid-word on a phone, where there is only one
card per row and the height argument does not apply. `.ws-segmented` now scrolls
horizontally instead of overflowing the viewport, and hides its sliding thumb
while scrolled (the thumb is absolutely positioned and stops tracking the
visible row), with the pressed state carrying the background itself.

## 0.2.4 — 2026-08-11 — MenuItem disabled

Compatible. Adds an optional `disabled` prop to `MenuItem`: renders a native
`disabled` button (no `onSelect`, no menu-close, cursor stays `default` per
rule 10) so a present-but-not-choosable row can still carry a `title` tooltip
explaining why. No export removed or renamed.

## 0.2.3 — 2026-08-11 — full-width shell, content at an intermediate measure, apps own their width

Compatible. No export or prop change. `--page-max` changes value, `--chat-max`
now tracks it, and two classes are added (`.ws-app--appfull`, `.ws-page`); no
token or class is removed or renamed.

What changed and why it matters to a consumer:

- `--page-max` is now `1200px` (was `1680px`), and it caps the CONTENT, not the
  shell. `.ws-app` and the `.ws-topnav` inside it span the full viewport; the cap
  moved onto `.ws-app__body > main`, centred via a `max(base, (100% - --page-max)/2)`
  inline padding. Net effect: the global toolbar is full-bleed, regular pages read
  in a centred column at the new measure.
- `--chat-max` is now `var(--page-max)` (was `900px`): the focused chat reads at
  the standard page width, and its shell is full-width like every other route
  (the chat's own toolbar+log+composer stay capped together on `.chat-panel--full`).
- `.ws-app--appfull` (new): put on the shell for an app route so its `<main>`
  drops the centred cap and the app fills the viewport. An app owns its own width.
- `.ws-page` (new): the opt-in container an app built on the kit wraps its content
  in to match the standard page measure, rather than the width being imposed.

Operator directive, 2026-08-11: one intermediate width for pages and chat, apps
uncapped and self-managed, the top toolbar full-bleed.

## 0.2.2 — 2026-08-11 — focus ring is keyboard-only on touch devices

Compatible. No export, prop, token, or class change; a CSS behaviour fix.

The `:focus-visible` accent ring is now suppressed where the primary pointer is
coarse (touch), via `@media (pointer: coarse) { .ws-root { --focus-ring: none } }`
next to the universal focus rule in `components.css`.

Why it matters to a consumer: iOS Safari matches `:focus-visible` after a plain
tap on a `<button>`, so the ring meant for keyboard navigation stuck on the last
control tapped (a menu trigger, most visibly) until focus moved, and did not on
Chrome/Android, which read as an inconsistent stuck "active" border on mobile.
Neutralising the `--focus-ring` token for the coarse-pointer subtree clears every
`:focus-visible` / `:focus-within` consumer at once. Desktop (fine pointer) keeps
the keyboard ring; text inputs are unaffected because their indicator uses
`:focus`, not this token.

## 0.2.1 — 2026-08-11 — `Markdown` is memoized

Compatible. Same export, same props, no call-site changes required.

`Markdown` is now wrapped in `React.memo`, and its `remarkPlugins` array is
hoisted to a module constant instead of being allocated inline on every render.

Why it matters to a consumer: markdown parsing is the most expensive thing this
kit does, and the component is typically rendered once per item in a list that
re-renders for reasons that have nothing to do with the markdown. In the
Workshop chat the composer's draft text is state on the component that also owns
the transcript, so every keystroke re-rendered every message. Measured against a
real 67-message / 310 KB thread, one pass of remark parse + mdast->hast is about
150 ms on a desktop core, which is 600-900 ms on a phone, per keystroke, scaling
linearly with thread length. Typing was unusable on mobile.

Both props are strings, and a given message's text is stable between renders, so
the shallow compare skips the parse entirely. The inline `[remarkGfm]` literal
mattered too: a new array identity every render defeats both this memo and
react-markdown's own internal caching.

If you were relying on `Markdown` being a plain function component (calling it
directly rather than rendering it, or attaching properties to it), it is now a
`MemoExoticComponent`. Rendering it as JSX is unchanged.

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
