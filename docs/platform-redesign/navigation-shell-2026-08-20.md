# The navigation shell

**Status:** decided in design, partially built. Written 2026-08-20 from three operator design
sessions (2026-08-18 to 2026-08-20) plus a measured read of the live tree.

**What this decides.** The container the whole app lives in: what the primary navigation is, what the
secondary navigation is, what a "place" is, how the two behave on a desktop and on a phone, how they
move, and where live work and search sit. It deliberately stops at the container. Page interiors
(the dashboard, the chats list, the memory browser, the inside of a chat) are named here only where
navigation forces a decision about them, and are designed separately.

**What it does not decide.** Where files physically live, what the durable storage substrate is, or
how the tree reconciles with object storage. That is
[`storage-and-structure-model-2026-08-19.md`](storage-and-structure-model-2026-08-19.md), which this
sits on top of. Where the two touch, this doc defers.

**Source material** is preserved verbatim in
[`navigation-shell-source/`](navigation-shell-source/README.md): three transcripts, the design
agent's own handoff, and the final mockup. Read those for exact wording. Read this for what is true.

---

## 1. The brief

From the operator, across the three sessions. These are constraints, not preferences.

1. **Design for web and phone together.** Neither is an afterthought and neither is a port.
2. **Keep the existing style toolkit.** Additions and modifications are allowed; a new visual language
   is not. The Workshop Design System (`frontend/CLAUDE.md`, `@workshop/ui`) is binding.
3. **Focus on the container and the navigation**, not page interiors. Represent an interior change
   only where the navigation forces it.
4. **The individual chat page is out of scope** apart from its navigation.
5. **Use ordinary design vocabulary** so options can be compared as named patterns rather than as
   one agent's taste.
6. **It is a workshop for professionals that hides the technical work.** So: a tree you can navigate,
   but it must not read like Finder.
7. **No invented status affordances.** A "live" indicator was added to the page-column header in
   session 1 and rejected flatly ("Don't invent or add bs"). Do not add status, health, counts, or
   activity chrome the operator did not ask for and the data does not clearly support.
8. **Nothing raised on selection** in the mobile secondary toolbar. Selected is weight plus a 2px
   underline on the bar's own edge. All labels stay on one baseline.
9. **One search entry point per surface.** A dock icon *and* a page-column field was called out as
   duplication.
10. **Never wire up a fake control.** Operator, 2026-08-20: *"never wire up a fake button or interface
    or page or feature or anything. If it doesn't work, don't build it. Flag it as something to
    discuss and figure out."* Some of the mockup is invented by the design agent and is not wanted;
    some is wanted and has to be built properly first. Section 8 sorts them.

The reasoning that produced the vocabulary is the operator's own, from session 1:

> workshop-scope plus sub-project-scopes is not really nesting, it is one parent and a pile of
> siblings. An app is an app first and a project only when you work on it.

So "app" became the honest noun and "scope" was found to be doing no work as a *label*. It remains
the internal addressing term.

---

## 2. Vocabulary

| term | meaning |
|---|---|
| **Place** | Anything the dock can point at: Home, an app, All chats, Activity. Introduced by this doc because the design sessions used "app" for both the dock entry and the installed feature module, and those turn out not to be the same thing (section 7.1). |
| **App** | An installed feature module. Finance, Birds, Notes. |
| **Agent** | App-shaped, no user-facing UI of its own. **Not a dock entry** (section 6.4). |
| **Page** | Replaces "section". A row in the page column: Chats, Memory, Tickets, Deploys, Settings. |
| **Home** | The workshop itself, as a place. Its first page is the Dashboard, and the Dashboard is what `/` opens. |
| **Dock** | The 60px icon rail, leftmost on desktop. Primary navigation. |
| **Page column** | Column two, 252px. Secondary navigation. Never called a sidebar. |
| **Scope** | Internal addressing only. **Never a visible label.** |

Two nouns are still unsettled and are in section 9: **Work vs Tickets**, and what the dock's unit is
once apps and projects stop being the same thing.

---

## 3. The shell — desktop

**Three columns: dock → page column → content. No top toolbar.**

### 3.1 Dock (60px)

Top to bottom, with a 24px hairline separator where marked:

```
  Home            house glyph
  All chats       message glyph
  Search          magnifier, opens the palette (also Cmd/Ctrl+K)
  ───────────
  <app tiles>     first letter in a rounded tile, one per installed app
  ───────────
  Activity        bot glyph, two independent signals (section 6.2)
  ⋮ (spacer)
  Avatar          account and settings. There is no Settings dock button.
```

Geometry, taken off the mockup: tiles 38x38 at radius 9, 8px gap, 12px vertical padding. The active
entry carries a **3px leading marker** on the dock's own left edge, 16px tall, radius `0 3px 3px 0`.

> The kit's `components.css` currently says "do not add a marker, rail or edge accent here". Read the
> full comment: it was written about the **horizontal** toolbar, where the operator called the marker
> decoration on 2026-08-11. On a vertical rail the marker is the conventional treatment and the
> objection does not transfer. Update that comment when the dock lands rather than leaving it
> contradicting the code.

### 3.2 Page column (252px)

- **Title line**: the place's name at 17px/600. A **ref pill** sits right-aligned on the same line
  where the place has a ref. (Whether anything in this system actually has a ref is section 8.4.)
- **Page list**: rows at 32px, indented 6px under the name, each with a 14px lucide icon, the label,
  and an optional count in mono. A page that opens a directory carries a right chevron.
- **The column is collapsible**, and collapsing is how the dock behaves like a dock.

### 3.3 The three dock rules

These were specified, then re-specified when the first build got them wrong. They are exact.

1. **Selecting a different place never toggles the column**, and the switch **hard cuts** with no
   animation. Motion means "the same thing changed", not "you moved".
2. **Tapping the tile of the place you are already in toggles the column.** An app opens *full
   screen* (column closed); tapping its tile again opens the column; tapping again closes it. Home,
   All chats and Activity default to the column **open**.
3. **Each place remembers its own state** — which page, whether the column is open, and whether a
   directory tree is showing. Revisiting a place restores exactly what you left. This is a local
   preference and is not in the URL.

### 3.4 The directory takeover

Tapping a directory page (Files, Memory) while already on it replaces the page column's contents with
a tree, in one coupled slide: the whole column, **title row included**, translates fully out to the
left while the tree comes in from the right. The row rises to become a back header. Coming back
restores the page list and **does not change the content** (you are still in the editor), and the row
keeps its right chevron so the tree is one tap away. The transition is symmetric: chevron in, back out.

Four corrections were needed to get this right and each is load-bearing:

- It is a **slide, not a cross-fade**.
- There is **no shadow overlay**. One was added and rejected.
- The tree panel **stays mounted** so its exit animates too.
- **Closing the column does not reset the tree** back to the page list.

### 3.5 What is a directory and what is a collection

The distinction that governs the whole tree idea:

- **Directories** (Files, Memory) are containment. They support file operations: open, edit, move,
  rename, delete, new folder.
- **Collections** (Chats, Tickets, Runs, Deploys) resolve by query. No drag target, no rename, no
  new-folder control.

A directory gets the takeover and the chevron. A collection is a page and never a tree.

---

## 4. The shell — phone

Two bars, then content. Final form after roughly a dozen iterations.

### 4.1 Bar one (52px)

Left to right: **app tile · place name + caret · search · avatar**.

- The **app tile** (uppercase letter, or a house glyph at Home) opens the place menu. It mirrors the
  desktop dock tile. There is no separate Home button; Home is a row inside the menu.
- The **place name plus an animated caret** toggles the page toolbar. Caret down is closed, up is
  open. Tapping the name or the caret both work.
- **Search** is one button and it **toggles**: tapping again closes the panel.
- The **avatar** owns account and settings.

Bar one has its own surface and a hairline below it, so it reads as separated from content. It had
neither at first and the operator called that out.

### 4.2 Page toolbar (40px)

A horizontally scrolling row of page names on the same surface as bar one, with a stronger hairline
below so the two bars read as one toolbar block.

- **Selected is weight plus a 2px underline on the bar's own bottom edge.** Nothing is raised. Labels
  sit 2px off the bottom so the underline reads as attached to the label.
- **Horizontal scroll only, scrollbar hidden.** It previously showed a full-width horizontal
  scrollbar inside a too-short strip.
- **It defaults to open on every page**, including an app's own page. A manual toggle holds until you
  change place or page. An earlier build defaulted it closed on the app page and the operator
  reversed that: *never default to closed.*

### 4.3 The place menu

A **dropdown anchored under the title** (card surface, rises on open), not a slide-over drawer. It
contains a Home row, All chats, Activity, then the apps. No Settings row; the avatar owns that.

Each row's ref is **the ref picker itself** — a tappable mono pill with a chevron that does *not*
switch places. This is the only ref control on the phone. It is not in bar one and it is not a
separate row. (Three iterations landed here; two intermediate versions put a ref pill in bar one and
both were rejected.)

### 4.4 Up is not Back

Files and Memory **drill in place** with an `Up` control and a directory trail, rather than taking
over a column.

> `Up` is structural and belongs to the page. `Back` is history and belongs to the browser.

### 4.5 Search on the phone

One button in bar one. The panel opens **directly below bar one**, covers the page toolbar, and
replaces page content only. **It never replaces bar one.** Its query field is the panel header.

---

## 5. Motion

The complete inventory. Anything not on this list does not animate.

| what | treatment |
|---|---|
| Page column open/close | width + translateX, **300ms** `cubic-bezier(.32,.72,0,1)`, **same place only** |
| Switching place | **none. Hard cut.** |
| Directory takeover | coupled slide, **340ms** `cubic-bezier(.32,.72,0,1)`, column out left / tree in right |
| Mobile page toolbar | height + opacity, **240ms** |
| Mobile caret | rotate 180deg, **240ms** |
| Mobile place menu | rise + fade, **200ms** |
| Overlays (palette, backdrops) | fade **160ms**, panel rise **200-220ms** |

The column does not simply get covered when it closes: it **slides itself left, out under the dock**,
while content slides in behind it. Reverse on open.

Everything respects `prefers-reduced-motion` (design system rule 10).

---

## 6. The root layer

Session 3. The question: what belongs in the dock, what belongs in Home's page list, and what was
missing from both.

### 6.1 Decided regardless of which proposal wins

- **The Dashboard is `/`.** Home gets a real landing page rather than opening on a list. It is the
  first row of Home's page list and it is what the root path resolves to.
- **Agents leave the dock** for a registry page. An agent has no UI, so as a dock place it has to fake
  an Overview page, and it gets a page column identical in shape to an app's, which defeats "the
  length of the list tells you what kind of thing you are in".
- **Work absorbs tickets under one noun** (which noun is still open, section 9.1).
- **Memory becomes a directory** with per-app folders, replacing the three card tabs.
- **Files leaves Home.** A workshop-wide Files page implies a workshop-wide filesystem that does not
  exist. Files belongs to an app.

### 6.2 Activity, and the four scopes

The operator asked whether the live/attention control is one button or two, and asked for the
standard terms. The answer that came back, and that this doc adopts:

| scope | what it is | how it is shown |
|---|---|---|
| **Inbox** | items addressed to you carrying a decision | **the only thing that legitimately produces a count** |
| **Monitor** | what is running right now | continuous state, so a dot or pulse, **never a number** |
| **Activity feed** | the past tense of the monitor | never badged |
| **Metering** | accounting over a period | belongs with the account |

**One button works**, because a count and a presence indicator are different visual channels and do
not collide. In the mockup: the bot glyph takes the live tone when anything is running, and an amber
count badge rides its top-right corner when something is waiting on you.

**The rule to adopt: a count only ever means "this many things are waiting on you."** Never running
processes, never log entries.

This lines up exactly with the design system's existing colour model (`frontend/CLAUDE.md`, "What
colour is for"): LIVE `--live` is the only hue that moves and answers "is this happening?", and an
unread count is already named there as a LIVE treatment. Use the tokens, not the mockup's hexes
(section 7.3).

**Activity is a place** with its own page set: All, Inbox, Running, Scheduled, History, filterable by
app. That was 3b, and it was recommended in combination with 3c's grouped Home list.

### 6.3 Usage

**Keep the Usage page where it is** (billing framing: "how much this month") and put the **live burn
rate on Activity** ("how fast right now"). Same data, two different questions.

Note that the session-3 reasoning said Usage "is currently in settings". It is not: `/usage` is its
own route and its own top-level section in the live app. The conclusion is unaffected but the premise
was wrong.

### 6.4 Page lists by kind

The length of the list is itself a signal of what kind of thing you are in.

- **Home** — Dashboard, Chats, Tickets, Memory, Apps, Agents, Deploys, Usage, Settings
- **App** — the app's own pages, whatever they are
- **All chats** — By day, By project, Everything, Archived
- **Activity** — All, Inbox, Running, Scheduled, History

### 6.5 The three root proposals

Recorded because the wireframes are lost and the reasoning is not:

- **3a, thin dock** — Dashboard and Activity are just pages of Home. One rule, no exceptions, ten rows.
- **3b, Activity as a dock place** with its own page set. Breaks "dock = apps only".
- **3c, Home's list grouped** (across-everything vs the workshop itself) plus a live strip at the
  dock's foot opening a peek panel over whatever you are in.

Recommended: **3c plus 3b's Activity page.** The peek panel is flagged as possibly one interaction
model too many, and is an open question (section 9.2).

---

## 7. Where the mockup and the live system disagree

Measured against the tree on 2026-08-20. Every one of these is a decision someone has to make; none
of them is a bug in the mockup.

### 7.1 An app is not a project

**This is the largest conflict and everything else is downstream of it.**

The mockup's dock holds `atlas`, `ledger`, `finance`, `commute`, each with a ref (`main`, `wip`,
`v2`) and each with the page set *App, Chats, Files, Memory, Work, Agents, Deploys, Settings*. Its
sample content is source files, branches, deploys and dead-letter tables. Those are **repositories you
build**. The handoff states the model outright: *"A project is the build view of an app, not a
separate object."*

That contradicts the ratified entity model, which this repo's `CLAUDE.md` states as law: **"Apps and
Projects are distinct linkable concepts."** And the live tree agrees with the entity model, not the
mockup:

- **Apps** are installed feature modules from `frontend/src/apps/registry.tsx`: `health`, `finance`,
  `budget`, `location`, `commute`, `notes-app`, `birds`. Only `birds` has more than one page. `health`
  has *no* page at all (a settings panel is its entire frontend). None has chats, memory, deploys,
  agents or source of its own.
- **Projects** (`/projects/:slug`, `ProjectDetail.tsx`) are exactly the thing the mockup drew: they
  have a repo, chats, build agents, stores and tickets, today rendered as sections of one long page.

**There is no Projects entry anywhere in the mockup.** Whatever is decided, that is a hole.

### 7.2 Home's page list

| current live section | mockup | note |
|---|---|---|
| Home | **Dashboard** | Dashboard replaces it and becomes `/` |
| Chats | Chats | |
| Projects | **absent** | see 7.1 |
| Apps | Apps | |
| Agents | Agents | mockup keeps the page, drops the dock entry. Correct. |
| Tickets (`/work`) | **Work** | naming conflict, section 9.1 |
| Deploys | Deploys | |
| Memory | Memory | becomes a directory |
| Usage | Usage | |
| Settings | Settings | |
| — | **Files** | does not exist, and session 3 removed it from Home anyway |

Live routes in neither list: `/runs`, `/stores`, `/marketplace`. Runs is arguably Activity's History
page. Stores and Marketplace need placing.

### 7.3 The palette

The handoff says its colours came from "the violet dark block of `packages/ui/src/styles/palettes.css`".
**No hex in the mockup appears anywhere in that file.** `--bg` is `#100d11`, not `#0f0e17`; the accent
is `--you: #d9a7e5`, not `#9b8aeb`.

Either the palette moved after the mockup was made (the agency model with `--you` / `--live` / `--sys`
landed 2026-08-18) or those values were never read from it. It does not matter which:

> **The mockup is a layout and behaviour spec. It is not a colour spec.** Every colour comes from a
> token, and the app has six palettes plus light and dark. Reproducing its hexes would break the
> palette picker and fail `check_design.sh` on rule 2.

The mapping used by the implementation:

| mockup | token |
|---|---|
| `#0f0e17` page background | `--bg` |
| `#16151f` page column | `--bg-inset` |
| `#191824` card | `--card` |
| `#201f2d` hover | `--card-hover` |
| `#14131d` field | `--field-bg` |
| `#262534` / `#36344a` | `--border` / `--border-strong` |
| `#ebebf3` / `#9998ac` / `#65647a` | `--fg` / `--muted` / `--faint` |
| `#9b8aeb` accent, `rgba(155,138,235,.16)` | `--accent` (= `--you`), `--accent-soft` |
| `#10b4bf` turquoise "running" | `--live` |
| `#e6b24a` amber count | `--warn` |
| `#62d29d` green "running" | also `--live`. The mockup used two different hues for one meaning. |
| `#e9889a` destructive | `--danger` |

### 7.4 The mockup is single-theme

It is dark only, and hard-codes a dark scrim and dark shadows. The real app ships light and dark
across six palettes and pins that in `styles.test.ts`. Every surface in the shell has to be checked in
light as well, and "it looks right in violet dark" is not a check.

---

## 8. Not backed by real data

Per the operator's blanket rule. Each of these is either a control the mockup invented, or a real
want with no working substrate underneath. **None of them gets wired to a stub.**

### 8.1 Files (invented for this system, and already withdrawn)

There is no Files page anywhere in the app, no file-browser backend, and no per-app source directory
that the UI can address. Session 3 removed Files from Home for a better reason than "it does not
exist": a workshop-wide Files page implies a workshop-wide filesystem, and there is not one.

**Do not build a Files page or a Files dock/page entry.** When files do arrive they arrive on the
substrate in
[`storage-and-structure-model-2026-08-19.md`](storage-and-structure-model-2026-08-19.md), and the
viewer/editor is an existing package, not something we write. Operator, 2026-08-20: *"when we get
into like the file viewer and file editor, we are gonna be looking for and finding other packages and
frameworks or tools to handle that or actually do that. We're not gonna build that all custom
ourselves."*

The *takeover interaction* (section 3.4) is still built, because Memory needs it.

### 8.2 The ref / branch pill

Every place in the mockup carries a ref (`main`, `wip`, `v2`) and both the desktop title line and the
mobile place menu offer it as a picker. The design agent's own handoff admits it: **"Branch pickers
are shells: the pill and the drawer row open nothing yet."**

In the live system a branch belongs to a **project's repo**, not to an app, and most places have no
repo at all. Home has no ref. Activity has no ref. Finance has no ref.

**Do not render a ref pill.** Revisit when 7.1 is settled: if the dock's unit becomes a project, a ref
picker on the title line is real and worth building.

### 8.3 The Activity count and the Activity page

The data is real. The HTTP surface is not.

- `services/inbox.py` has `list_items(chat_scope=None)` and `counts()`, both supporting a global read,
  and both used today via direct Python calls.
- `GET /inbox` **was deleted** on purpose (coherence-overhaul T5) because it had zero callers. The
  only HTTP surfaces are `POST /inbox/{id}/snooze` and `POST /inbox/{id}/resolve`.
- Per-chat work is served by `GET /api/chat/threads/{id}/work`, which already assembles exactly the
  right shape (inbox items plus tickets plus run state, with server-side counts) but is scoped to one
  thread.

So the Activity badge needs **one small new endpoint over an existing service**. That is honest work,
not a stub, and it is on the build list. Until it exists **the Activity dock entry does not render.**

The Activity page's five tabs are not equally real:

| tab | substrate |
|---|---|
| Inbox | real, once the endpoint exists |
| Running | real, `GET /jobs` plus `jobIsLive` |
| History | real, the same list filtered |
| All | real, the union |
| **Scheduled** | **no substrate confirmed.** There are agent triggers and cron, but nothing that returns "here is what will run and when". Do not build this tab until it does. |

### 8.4 Search

The palette in the mockup is fully specified: grouped results, this-app-first then everywhere else,
per-kind badges (CH / FI / ME / WK / AG), an "Ask about …" row at the top, and `Cmd+K`.

There is **no search endpoint and no command palette in the app today.** There is, however, a complete
and working search *service*: `services/semantic_index.py` does hybrid BM25 plus embeddings over
notes, docs, chats, vault and tickets; every chunk carries `kind`, `ref`, `title`, `scope` and `ts`;
and it degrades to lexical rather than raising when the embedder is unavailable. `services/refs.py`
already turns a ref into a destination.

So the palette is a **thin, honest wrapper over a working service**, and it is on the build list.
Until the endpoint exists **the Search dock entry does not render**, and `Cmd+K` is not bound.

The FI (Files) badge in the mockup's results is dropped along with Files.

### 8.5 The Dashboard composer and its context drawer

The mockup's dashboard has a composer pinned to the bottom which, as you type, raises an inverted
autocomplete drawer offering *the passages it would carry as context* and *the chats it could
continue*, each with its last exchange and your draft in place.

It is the most interesting thing in the mockup and none of it exists. It needs passage-level
retrieval scoped to a draft, a candidate-chat ranker, and a decision about how injected context
interacts with the prompt cache. The design agent parked all three for deep research and that is the
right call.

**Do not build the composer.** It is section 10.

### 8.6 Per-app Chats, Memory, Work, Agents, Deploys

The mockup gives every app that page set. No installed app has any of it (7.1). A page column offering
Finance a Deploys page is six fake rows.

**An app's page list is derived from the app's real routes.** Most apps have one page, which is
exactly why an app opens full screen with the column closed.

---

## 9. Open questions for the operator

Each of these blocks something real. None is answered here.

### 9.1 Work or Tickets

The design agent asked for this call directly and it is still open. The live app already went the
other way: the route is `/work`, the label is `Tickets`, and `appNav.tsx` says why, citing the entity
model: *"Work is a mass noun and could never name the individual item."* A note in the knowledge store
records "Work pages renamed to Tickets" as a decision. The mockup says Work.

Recommendation: **keep Tickets**, and treat the mockup's Work as pre-dating that decision.

### 9.2 The peek panel

3c put a live strip at the foot of the dock that opens a peek panel over whatever you are in. The
design agent flagged its own recommendation: is a hovering panel a third interaction model on top of
the column and the takeover? Adopting 3b's Activity page may make it redundant.

Recommendation: **not now.** Ship Activity as a place; see whether a peek is still wanted.

### 9.3 What is the dock's unit

Section 7.1. Three candidate answers:

- **(a) Apps only.** Faithful to the current entity model. Projects stay a page of Home. Cost: the
  mockup's per-app page set becomes a one-row column for most apps, and the thing you actually work in
  all day (a project) is two clicks deep.
- **(b) Projects only.** Faithful to the mockup's page set and its sample content. Cost: contradicts
  "Apps and Projects are distinct", and installed apps lose their dock presence.
- **(c) Both, in one dock, separated by a hairline.** Honest about the model. Cost: a longer dock, and
  a user has to learn which is which.

Recommendation: **(c)**, because the two really are different and pretending otherwise is what
produced the conflict. The shell is being built so that this is a change to one data function
(`places.ts`) and nothing else.

### 9.4 Chats-list picks (from session 5, unbuilt)

Three picks were awaiting the operator when the session ended, and the proposal artifact is lost:

- All chats above or below Search in the dock
- whether there is a participant filter
- whether chats can be pinned

These are page-interior questions. They do not block the shell.

### 9.5 Memory folder structure

Session 6b offered three folder structures (A / B / C) and the artifact is lost. The operator's own
framing, 2026-08-20: *"when it comes to the file structure of how we want to have the default
memories, files, organization structure work for chat or operator, have to research into that."*

Treat this as research, not as a lost pick. See section 10.

---

## 10. Parked for research

Named so they are not quietly built badly.

1. **Passage indexing.** What a retrievable unit is, how it is chunked, and how a passage is
   addressed. Prerequisite for 8.5.
2. **In-chat context suggestions.** The drawer in 8.5: ranking candidate passages and candidate chats
   against a live draft.
3. **Prompt-cache interaction.** Injected context that changes per keystroke against a cache-stable
   prefix. There is already a filed ticket on exactly this failure mode for the ambient digest.
4. **The default memory and file organisation** for a chat and for the operator (9.5).
5. **The file viewer and editor package.** Not written here. Evaluate existing packages.

---

## 11. Rejected, with reasons

Kept so they are not re-proposed.

**Mobile: mirroring the desktop's two columns inside the drawer.** Two page switchers on the smallest
screen, and an extra tap. Rejected session 1, still rejected.

**Mobile: a bottom tab bar.** The real mobile standard, but page sets run seven to nine items against
a practical cap of five, so it needs a "More", and it costs permanent height.

**Mobile: no control, bar auto-hides on scroll.** Zero chrome and fully predictable, but the state is
not the operator's to pin.

**Mobile: title opens a page menu instead of a tab bar.** Simpler and more predictable, but it hides
the map and costs a tap per page switch. Worth revisiting if page switching proves rare.

**A live indicator on the page-column header.** Invented, and rejected outright.

**A shadow under the sliding tree.** Invented; removed.

**A search field in the page column (desktop) or a second search entry point anywhere.** Duplication.

**Raising the selected label in the mobile page toolbar.** Explicitly rejected.

One knowing exception is recorded rather than resolved: **title plus caret conventionally means "open
a menu", not "expand the bar below".** It is the one learnable element in the design. It was accepted
with the caret carrying the state.

---

## 12. Build order

Each stage is shippable and none of them wires a control that does not work.

**Stage 1 — the container.** Dock, page column, mobile bars, motion, per-place memory. Places and
their page lists derived from real routes. No Search entry, no Activity entry, no ref pill, no Files,
**and no directory takeover** (section 3.4 has nothing to drive it until Memory becomes a tree, so
building the mechanism now would be unreachable code). Existing pages render unchanged inside it.
*This is the stage built on 2026-08-20.*

**Stage 2 — Search.** `GET /search` over `semantic_index`, then the palette and `Cmd+K`, then the dock
and bar-one entries appear.

**Stage 3 — Activity.** The global inbox endpoint, then the Activity place with All / Inbox / Running
/ History. Scheduled waits for a substrate. The dock entry appears with it.

**Stage 4 — Dashboard.** `/` stops rendering the old Home list and renders needs-you, continue and
running. Without the composer (8.5).

**Stage 5 — Memory as a directory.** The first real tree, and with it the directory takeover
(section 3.4) and the mobile drill-down with `Up` (section 4.4).

**Stage 6 — the page interiors**, one at a time: chats list, agents, memory, and last the inside of a
chat, which is the highest-traffic screen in the product and has never been designed.

Settle 9.3 before stage 4; it changes what the dock lists and therefore what the Dashboard summarises.
