# The Dashboard (2026-08-25)

Status: BUILT 2026-08-26 (commits 3aaeecf3, b9fa2078, 780bc8ae). Source of
record for Home's first page. Section 6 records what the build changed about
this document's own answers, because three of them were wrong.

Supersedes nothing. It sits UNDER
[`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md) (which owns
the shell around it) and beside
[`shell-mockup-spec-2026-08-22.md`](shell-mockup-spec-2026-08-22.md) (which owns
the dock, page column and phone chrome). Where they disagree with this file
about the dashboard's own content, this file wins; where this file touches the
shell, they win.

Source material: `Workshop Shell.dc-2.html`, the operator's Claude-design
mockup, brought into this chat 2026-08-25. It is the SAME artifact class as
`navigation-shell-source/workshop-shell-mockup.dc.html`, and the lesson from
2026-08-22 applies: a mockup that needs a design-tool runtime to render goes
unread, and three surfaces were then built from prose that carries intent while
the mockup carried geometry. So the geometry is extracted here, in text, before
anything is built.

## 0. Scope

IN: the four dashboard cards and the data behind them.

OUT, by the operator's own instruction 2026-08-25: the composer at the foot of
the page, its inverted autocomplete drawer, the suggested-context band, the
chat-candidate radio list, the scope/branch/model chips and the "add context"
picker. All of that is the input system and is a later stage. The mockup draws
it; do not build it. The page ends at the bottom of the card grid.

ALSO OUT, and named so nobody infers them from the mockup: the dock's Activity
button and its five pages, the All-chats day/project/flat/archived views, chat
archiving and the phone's swipe-to-archive, and the search palette. Those are
either already built elsewhere or are their own work.

## 1. Precedence

Same three rules the shell spec settled, restated because they are what keeps a
mockup from smuggling a second design system in:

1. A design doc wins on WHAT to build.
2. This spec wins on how it looks and how it moves.
3. **Tokens always win on colour.** Every hex in the mockup is the violet-dark
   palette rendered flat. Not one of them may be typed into a `.tsx` or a
   `.css` file. The mapping is section 7.

## 2. Where the page lives

`/` is already Home's first page and is already named "Dashboard" in the page
column (`components/shell/places.ts`, the `dashboard` row). The route exists,
the nav row exists, and `pages/Home.tsx` is what renders there today.

So this is a REPLACEMENT of `Home.tsx`'s body, not a new route, not a new place
and not a new nav row. The three bands Home carries now (Apps / Recent chats /
Projects, from the 2026-07-18 direction) are superseded by the four cards below.

## 3. Layout

**Desktop.** One scroll region, centred, `padding: 22px 24px 26px`
(`--space-5` / `--space-5` / `--space-6` is close enough and is what a token
build gives), `scrollbar-gutter: stable` so the grid does not shift when the
scrollbar appears.

Inside it, one grid:

- `grid-template-columns: repeat(auto-fit, minmax(340px, 1fr))`
- `grid-auto-rows: clamp(320px, calc((100vh - 190px) / 2), 520px)` — every card
  the same height, sized from the viewport. The 900px content cap over a 340px
  column minimum means the grid is always two columns and therefore always two
  rows, so half the screen (less the chrome) is one card. **Superseded 2026-08-26**:
  this was a flat `236px`, which left a third of a tall screen empty under the
  grid
- `gap: 14px`
- `align-content: start`
- `max-width: 900px`

The mockup exposes those four as tunable props (`cardMinWidth` 260-460,
`cardHeight` 180-320, `contentMaxWidth` 700-1200, `appTileSize` 34-64). They are
NOT props in the product. Ship the defaults as constants; a knob whose only
consumer is a mockup is a knob nobody will ever turn.

EQUAL ROWS ARE THE POINT; CLIPPING WAS NOT. Every card is the same height, which
is what makes a row of them read as a grid. What is inside a card is a different
question, and the first cut got it wrong: each card clipped to a whole number of
rows (two to four) and named the remainder on its "→" link, so on an instance
with 113 chats and 9 waiting items the dashboard showed four and three.

**Superseded 2026-08-26, on the operator's correction: a card shows every row it
was given and SCROLLS.** The mockup drew a handful of rows because a mockup has
to draw something; that was read as a rule and it was not one. The card body is
the scroller (`overflow-y: auto`, `scrollbar-gutter: stable`, scroll chaining
left on so a phone reader is not trapped), and `rowCaps`/`fitRows` and the
per-width row arithmetic behind them are deleted. A "→" link names an overflow
only when the SERVER capped the read, never because the card ran out of pixels.

`auto-fit` means the grid is one column at a narrow width and two at a wide one
with no breakpoint list.

**Phone.** The same four cards stacked in source order, page padding
`12px 12px 16px`, gap `11px`, each card `padding: 14px; max-height: 300px` over
`grid-auto-rows: auto` — a card SIZES TO ITS CONTENT here and the cap only stops
a long list from becoming the whole page. Nothing lines up in a single column,
so equal heights buy nothing and cost screen: at a flat height the apps card
padded 200px of empty box under six tiles. No page header: the shell's bar one already says where you are,
and a `PageHeader` reading "Dashboard" under a bar reading "Workshop" is exactly
the ~290px of duplicated chrome the 2026-08-23 screenshot pass removed from
Files and Memory.

**Card chrome**, identical for all four: `--card` background, 1px `--border`,
`--radius-lg`-ish 12px, padding `15px 17px`, column flex, gap 11-12px.
A card head is one row: a mono uppercase `--fs-xs` label with `.08em` tracking,
an optional count beside it, and a right-aligned muted "→" link.

## 4. The four cards

### 4.1 Apps

Head: `APPS`. No count, no link.

Body: a wrapping row of tiles, `gap: 14px 16px`, aligned to the start. A tile is
a 46px rounded square (`--bg-inset` fill, 1px `--border`, 12px radius) over a
12px name, ellipsised. The glyph is the app's declared icon or a monogram.

Every installed app is drawn, and the card scrolls if they do not all fit.
**Superseded 2026-08-26**: this paginated eight at a time behind a row of dots,
which was the short card's constraint wearing a control. Tapping a tile opens
that app.

THE LIST IS THE DOCK'S LIST. `apps/registry.appPlaces()` — the same function
`useShell` builds dock tiles from. Not `/api/apps`, not a second catalog. The
dock and this card disagreeing about which apps exist is the failure this rule
prevents.

### 4.2 Needs you

Head: `NEEDS YOU`, a count, and `inbox →`.

The head carries a count and nothing else. **Superseded 2026-08-26**: the border
and the label went `--warn` while anything was waiting, which on a real instance
is always, so it was a permanent yellow box rather than an alarm. A hue that
does not vary is not information (`frontend/CLAUDE.md`, "What color is for").
Empty, the card reads "Nothing waiting on you."

A row is an inset block (`--bg-inset`, 1px `--border`, 8px radius, padding
9px 10px) with two lines:

- line one: who is asking (14px, semibold), a right-aligned app/scope chip
  (mono `--fs-xs`, `--accent-soft` fill, 4px radius), and a relative time
- line two: what is being asked, one line, ellipsised

Rows cap at what fits (3 at the default height). Tapping a row goes to the thing
that is asking. Tapping `inbox →` goes to the full list.

NOT AN ACTION SURFACE HERE. The mockup's Activity page expands a row in place
and offers Approve / Reject / Send. The dashboard card only shows and links.
Answering already has a home (the chat's work panel and the ticket page), and a
third place to answer is the duplication `deciding-on-work-from-a-chat-2026-08-22`
was written to stop.

### 4.3 Pick up where you left off

Head: `PICK UP WHERE YOU LEFT OFF` and `chats →`.

A row is ONE line, not a card: title (14px, `max-width: 52%`, ellipsised), then
three 3px pulsing dots in `--running` if that chat has a turn in flight, then
the tail of its last message (13px, `--faint`, ellipsised, absorbing the
remaining width), then right-aligned: a 15px context ring, its percentage in
mono `--fs-xs`, and a relative time in a 26px right-aligned column.

The ring is the composer's context meter at a smaller size: same 18-unit
viewBox, same r=7 arc, same `stroke-dasharray` maths, same rounding. Reuse
`contextReading`/`contextLabel` rather than restating either.

Four rows at the default height. Order is the chat list's own order (when the
operator last spoke), so this card and `/chats` cannot disagree.

### 4.4 Running now

Head: `RUNNING NOW`, a count, and `activity →`.

A row is one line: the runner's name (14px), what it is doing (13px, `--faint`,
ellipsised), and a right-aligned relative time. Empty: "Nothing running."

Footer, separated by a 1px `--border` rule: the window's spend on the left and
`usage →` on the right. See section 6 gap 4 — the mockup says `$4.12 today` and
this system has no dollars.

Three rows at the default height.

## 5. Data

One module, `frontend/src/lib/dashboard.ts`, owning the four reads and the four
row shapes. Every field below is traced to a live endpoint or is listed in
section 6 as a gap with a decided answer.

| Card | Read | Fields used |
|---|---|---|
| Apps | `appPlaces()` (no fetch) | `id`, `name`, `home`, `icon` |
| Needs you | `GET /api/inbox` (gap 3) | `kind`, `agent`, `scope`, `title`, `created_at`, target id |
| Continue | `GET /api/chat/threads` | `id`, `context_key`, `title`, `last_message`, `last_user_at`, `running` (gap 1), `context_tokens` + `context_window` (gap 2) |
| Running | `GET /api/jobs?status=running` | `title`, `summary`/`task_text`, `started_at`, `agent_name` |
| Spend | `GET /api/usage?window=5h` | `app_used`, `app_total` |

`/api/jobs` and `/api/tasks` both answer in the `Page` envelope (`items` +
`page`), and `page.total` is null on jobs by design — a null total is not zero
(ADR 0023 clause 1).

## 6. What is missing, and the answer for each

Five gaps. None is worked around in the client.

**Gap 1 — the list row does not say whether a chat is running.** THIS WAS
WRITTEN UP AS A MISSING FACT AND IT IS NOT ONE: the fact exists, the open chat
already renders it (`chat_log.active_turn`, carried on the per-thread sync
payload), and what was missing was only a place for it on the LIST row, so a
surface showing many chats had to make one request per chat. Calling that a gap
overstated it, and the operator said so.

BUILT DIFFERENTLY FROM WHAT THIS SECTION FIRST SAID, twice over. Mirroring
`active_turn_id` into Postgres is wrong: `chat_log._LIST_RELEVANT_META_FIELDS`
deliberately keeps per-turn bookkeeping off the database round trip, and that
key is rewritten hundreds of times while a turn runs. And putting the
derivation in both row builders is a fact derived twice, which is a fact that
can disagree. So it is `chat_store.stamp_running`, applied by the route over
whichever list source answered: one implementation, both paths, no mirror
change. Measured over the real store: 134 threads in 2.8ms.

**Gap 2 — context fullness is a per-thread endpoint.** `GET
/chat/threads/{id}/context` exists, but four rows would mean four calls, and
that endpoint's own preferred source is `thread.context_tokens`, which is
already in meta AND is already a mirrored Postgres column. **Add
`context_tokens` and `context_window` to the summary row**, and render with the
existing pure `contextReading`. The window table (`_CONTEXT_WINDOW` +
`_window_for`, including the rule that a measurement wider than the table
corrects the table) currently lives inside `routes/chat.py`; it moves to a
service so the route and the summary row read ONE table. Do not copy it.

**Gap 3 — there is no global "what needs me".** The only assembler is
`chat_work.thread_work(thread_id)`, which is per-chat, and `GET /inbox` was
deleted in the 2026-08-16 sweep for having zero callers. It has a caller now.
**Extract the scope-independent half as `chat_work.needs_you()` and re-add `GET
/api/inbox`** returning `{items, counts}`: stored rows from
`inbox.list_items()` (which already treats `chat_scope=None` as the global
inbox) plus ticket-derived rows through the same `_needs_you_row`, merged by the
same `inbox.sort_items`. No new store, no second definition of "waiting", and
the same de-duplication rule (a stored row naming a ticket does not surface).

**Gap 4 — there is no money in this system.** The ledger counts TOKENS
(`services/usage_ledger`), jobs carry `tokens`/`tokens_own`, and there is no
price table anywhere in the tree. `$4.12 today` cannot be rendered truthfully.
**The footer shows tokens against the window the ledger actually defines**, via
the existing `lib/usage.ts` and its `formatTokens`. It says `5h` rather than
`today`, because a rolling five-hour ceiling is the thing that exists and
"today" is a different measurement. If the operator wants dollars, that is a
price table and its own decision.

**Gap 5 — three of the four "→" links point at a place that is not built.** The
mockup's Activity place (All / Inbox / Running / Scheduled / History) does not
exist. Existing destinations are used instead, and this is the one point where
the dashboard deliberately differs from the mockup:

- `inbox →` → `/tickets` narrowed to what is waiting (the list already parses
  its filters from the URL)
- `chats →` → `/chats`
- `activity →` → `/runs`
- `usage →` → `/usage`

When an Activity place is built, these move. Nothing else about the card
changes.

**What else the build changed.** Two of this document's answers did not survive
contact with the running page, and both changed toward saying more:

* "Running now" shows runs AND chats with a turn in flight. Jobs alone printed
  "Nothing running" on an instance with two live turns, which is the kind of
  confident wrong answer that teaches an operator to stop reading a card. The
  mockup had it right (its own list carries a streaming chat) and section 4.4
  originally did not.
* The row counts were a decision, then they stopped being one. They started as
  constants, became per-width arithmetic (`rowCaps`: three needs-you blocks fit
  the 236px desktop card, two fit the 210px phone card, and a fixed three
  half-drew the last one on a phone), and on 2026-08-26 were deleted outright:
  the card scrolls, so nothing decides how many rows the operator may see. The
  arithmetic was a careful answer to a question that should not have been asked.

## 7. Colour

Every hex in the mockup maps to a token. This table is the whole translation;
after it, no hex survives.

- `#0f0e17` page → `--bg`
- `#16151f`, `#191824` card / panel → `--card`
- `#14131d` inset well, tiles, rows → `--bg-inset`
- `#262534` hairline → `--border`
- `#36344a` stronger hairline, hover → `--border-strong`
- `#ebebf3` text → `--fg`
- `#9998ac` secondary → `--muted`
- `#65647a`, `#4a4860` tertiary → `--faint`
- `#9b8aeb` accent → `--accent`; `#b1a5f1`/`#c5bef6` → `--accent-hover`
- `rgba(155,138,235,0.16)` chip fill → `--accent-soft`
- `#e6b24a` needs-you → `--warn`
- `#62d29d` → `--ok`; `#e9889a` → `--danger`
- `#10b4bf` the "autonomous agent turquoise" → `--running`, and where the row is
  about WHO acted rather than what state it is in, the kit's `agency` axis
  (`AgencyChip`/`AgencyDot`, tier `autonomous`) already IS this concept

Motion: the mockup's `wsPulse 1.7s`, `rowIn 200ms` and `fadeIn 200ms` are
`--dur-base` / `--dur-slow` on `--ease-out`. `dot1/dot2/dot3` is the kit's
existing typing-dot animation; do not add a second one.

## 8. Components and files

```
pages/Home.tsx                     the page: header-less grid of four cards
components/dashboard/DashboardGrid.tsx   the grid + the shared card shell
components/dashboard/AppsCard.tsx
components/dashboard/NeedsYouCard.tsx
components/dashboard/ContinueCard.tsx
components/dashboard/RunningCard.tsx
lib/dashboard.ts                   the four reads + the four row models (pure)
lib/dashboard.test.ts              tests for the pure part
```

Built from kit primitives: `Card`, `Row`, `Stack`, `SectionLabel`, `StateBlock`
(the empty and error states), `Skeleton` (loading), `StatusDot`, `AgencyChip`,
`Tag`, `timeAgo`, `usePoll`, `shortHttpError`. `AppGlyph` from `lib/appIcon` for
the tiles. The context ring is lifted from `components/chat/ContextMeter` rather
than redrawn.

The equal-height card with a scrolling row list is the one genuinely new shape.
It is ONE component (the card shell in `DashboardCard.tsx`) that all four cards
use, so "how a card scrolls", "how its states read" and "how the head is laid
out" are answered once rather than four times.

## 9. State

**There is no centralised data layer to plug into, and the dashboard must not
invent a fifth pattern.** Measured: no Redux, no Zustand, no TanStack Query, no
SWR anywhere in the workspace. What exists is `usePoll` (33 call sites),
`withTransientRetry`, `shortHttpError`, and per-domain modules in `lib/` that
own a shape and a fetch (`usage.ts`, `jobs.ts`, `tasks.ts`, `apps.ts`). Most
pages hand-roll `fetch` + `useState` + `useEffect` directly, `Home.tsx`
included. See [`client-state-management-2026-08-25.md`](client-state-management-2026-08-25.md)
for the audit and the staged recommendation.

So the dashboard follows the existing idiom exactly:

- `lib/dashboard.ts` owns the four fetches and every derivation. Pure functions
  where the logic is real (which rows fit, what the ring reads, how a row is
  labelled) so it is testable without a DOM, which is this repo's rule.
- One `usePoll` per card, each with its own interval — running work moves fast,
  the app list does not. Independent reads, so one slow or failed endpoint
  degrades ONE card into its error state instead of blanking the page.
- All four states per card: loading (`Skeleton`), empty (`StateBlock` with the
  card's own sentence), error (`StateBlock` naming the endpoint), and overflow
  (the "→" link, which is what the cap means).
- No client cache, no cross-card store, nothing in `localStorage`. Four
  independent server reads is the whole state model.

If the query layer in the state doc is adopted later, these four reads are the
easiest possible migration: they are plain GETs with no streaming, no
pagination and no optimistic write. That is a reason to keep them boring now,
not a reason to pilot a library here.

## 10. Verification

The 2026-08-23 rule stands and is not optional: typecheck, `check_design.sh`
and the test suite were all green over a phone layout that put the one column
that mattered off the right edge of the screen. **A test proves a rule holds;
only a screenshot proves the screen is right.**

IT EARNED ITS KEEP AGAIN. Four rounds of `scripts/shoot.sh` at 1440x900 and
390x800 found seven defects that every gate had passed: blocks half-drawn at
the clip edge and then clipped at the descenders, titles ellipsizing at a
quarter of the row because a flex-basis was `auto`, the Apps card cutting its
second row of tile names in half on a phone, chat tails squeezed to one letter,
overflow counts computed off an arbitrary fetch cap, "Nothing running" beside
two running chats, and a row printing its scope twice. See commit 780bc8ae.

A NOTE ON HOW THE SHOTS WERE TAKEN, because it is reusable. `apply.sh`
correctly defers the backend restart while a turn is in flight, so new routes
do not serve until the turn ends -- which would have meant shipping this
unverified. Instead: run the new backend on a spare port, serve `frontend/dist`
with a ~40-line proxy that strips `/api` the way the edge does, and point the
shooter at it with `WORKSHOP_PREVIEW_ORIGIN`. Real code, real browser, live
backend untouched.

Design gates: `bash scripts/check_design.sh`, `npm run typecheck` from the repo
root, `cd frontend && npm test`.

## 11. Staging

1. **Backend reads** — gaps 1, 2 and 3. DONE, commit 3aaeecf3.
2. **The page** — the grid, the card shell, the four cards, both widths. DONE,
   commits b9fa2078 and 780bc8ae.
3. Later, and NOT part of this: the composer and its context system; an Activity
   place, which is where gap 5's links want to point.
