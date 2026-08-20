# Handoff notes

For an agent picking up the Workshop navigation redesign. Read this before touching the files.

Source repo: `BenMiriello/Workshop`, branch `main`. Nothing here is implemented in the repo yet.

---

## 1. What the user asked for

A redesign of the **chats page** and the **memory pages**, treated as two separate redesigns that
share the app's design language. Memory was described as a mess: undifferentiated cards, icons
sitting outside their containers, misaligned rows eating vertical space at the top.

The brief widened almost immediately into the real problem: **navigation and structure**. The user
wanted to think about the whole workshop in terms of project structure, nesting, and something like
a friendly file tree — and asked explicitly for research, named design patterns, and a series of
proposals to consider rather than one answer.

Constraints stated by the user:

- Design for **web and mobile** both.
- Keep the existing style toolkit and CSS. Additions and modifications are allowed; a new visual
  language is not.
- Focus on the **container and navigation**, not page interiors — but represent interior changes
  where navigation forces them.
- The individual chat page is out of scope apart from its navigation.
- Use ordinary design vocabulary to name patterns, so options can be compared as patterns.
- The product is a workshop for professionals that hides technical work. So: a tree you can navigate,
  but it must not read like Finder.

Later corrections, all of which are load-bearing:

- **No invented status affordances.** A "live" indicator was added to the page column header and the
  user rejected it flatly ("Don't invent or add bs"). Do not add status, health, counts, or activity
  chrome that the user did not ask for and the data does not clearly support.
- **Nothing raised on selection** in the mobile secondary toolbar. Selected page = weight plus a 2px
  underline on the bar's own edge. All labels stay on one baseline.
- **One search entry point per surface.** A search icon in the dock *and* a search field at the top
  of the page column was called out as duplication. The dock icon was removed.

## 2. Problems in the current app that motivated this

Read from the repo, not assumed:

- `frontend/src/components/appNav.tsx` renders one flat top bar of ~10 sections plus a collapsing
  breadcrumb. The two do overlapping work.
- Scope lives *inside* the page as a filter (`Chats`: All / Workshop / Projects / Apps / Agents, via
  `frontend/src/components/chat/chatScope.ts`). Leaving the page loses the scope, so scope is not
  addressable across sections.
- Back is ambiguous inside a chat: sometimes the previous chat, sometimes the chat list. Getting to
  "chats" requires hunting for the button.
- `frontend/src/pages/Memory.tsx` is three peer tabs (Notes / Vault / Documents) of same-weight
  cards, with no hierarchy and no sense of how much memory exists or where.
- There is no Files page anywhere.

## 3. The decided design

**Three columns on desktop: dock → page column → content.** No top toolbar.

- **Dock** (leftmost, 60px, icons only): Home, then app tiles, then agents, then Settings and the
  user avatar pinned to the bottom. App tiles fall back to the app's first letter in a rounded tile.
  The active entry carries a 3px violet marker on its left edge.
- **Page column** (252px): the app name with a **ref indicator** on the title line (branch / commit /
  working state, as a pill — not a tab), the search field, then the page list indented under the name.
- **Content**: the page.

Tapping the dock tile of the app you are already in **closes** the page column, so the dock behaves
like a dock. Tapping again reopens it.

**Files and Memory take over column two.** Tap `Files` while already on Files and the page-list rows
slide out while the tree slides in from the right, with the `Files` row rising to become a back
header. Back restores the page list, the content does not change (you are still in the editor), and
the `Files` row keeps a right chevron so you can get back to the tree. The transition is symmetric:
chevron in, back out.

**Memory is directory-shaped**, mechanically identical to Files. Notes are real files in real folders
that you open, edit, move, rename and delete. This replaces the three card tabs. The distinction that
governs this: **directories** (Files, Memory) are containment and support file operations;
**collections** (Chats, Work, Runs, Deploys) resolve by query and have no drag target, no rename, no
new-folder control.

**Mobile**: bar one is Home button, app chip (name + ref, tap to open the drawer), search. Below it a
**single** secondary toolbar of page names — the only page switcher on mobile. The app chip opens a
left overlay drawer listing **apps and agents only**, never pages. Files and Memory drill in place
with an `Up` control and a directory trail. `Up` is structural and belongs to the page; `Back` is
history and belongs to the browser. The existing hamburger keeps its current job: pinning the
secondary toolbar open or closed, persisted across navigation.

Rejected on mobile: mirroring the desktop's two columns inside the drawer. It puts two page switchers
on the smallest screen and costs an extra tap.

**Search** appears in exactly two places: the field at the top of the page column (⌘K from anywhere)
and, on mobile, the single button in bar one. The palette groups results by scope, this-app-first
then everywhere else, with a per-kind badge (CH / FI / ME / WK / AG) and an "Ask about …" row at the
top. Files results are new — search previously had no Files group.

## 4. Vocabulary — use these words

| Term | Meaning |
|---|---|
| **App** | The primary entity and the only container the user names. A *project* is the build view of an app, not a separate object. |
| **Agent** | App-shaped, no user-facing UI. Sits at the root when unattached, nests under its app when bound. |
| **Page** | Replaces "section". Chats, Files, Memory, Work, Agents, Deploys, Runs, Schedule, Settings. |
| **Home** | The root. Where chats start. There is deliberately **no** global Chats button in the dock. |
| **Dock** | The leftmost icon column. Column two is the **page column**. |
| **Scope** | **Gone from the UI.** Internal addressing term only. Never a visible label. |

The user's own reasoning, which produced this: workshop-scope plus sub-project-scopes is not really
nesting, it is one parent and a pile of siblings. An app is an app first and a project only when you
work on it. So "app" is the honest noun and "scope" was doing no work.

## 5. Page lists by kind

Replaces the flat `SECTIONS` map. List length itself signals what kind of thing you are in.

- **Home** — Chats · Work · Memory · Files · Apps · Agents · Deploys · Usage · Settings
- **App** — App · Chats · Files · Memory · Work · Agents · Deploys · Settings
- **Agent** — Overview · Chats · Files · Memory · Runs · Schedule · Settings

There is no Overview for an app because **the first page of an app is the app** — it opens running,
by default. Agents get an Overview because they have no UI to show.

Each app remembers its own last-viewed page. That is a local preference, not part of the URL.

## 6. Structural changes this implies in the codebase

Ordered roughly by cost:

1. **Router becomes `/<app>/<page>/<detail>`.** Today's flat global routes are rewritten. This is the
   largest single item in the shell.
2. A **Files page** on Home, apps and agents. Previously absent entirely.
3. A **ref indicator** on the title line.
4. **Containment nesting**, not depth-1 with everything parented to the root. Kind groups (Apps,
   Agents) survive only at the root; a bound agent nests under its app.
5. **Up from a detail targets the item's home**, not the app you happened to be browsing.
6. The in-page scope `Segmented` filter on Chats is **deleted** — the dock owns scope. `Filter` is the
   *widening* control and where saved views live. Saved views are never pinned into the dock.
7. Search gains a **Files** group with its own badge tint.
8. Memory's Notes / Vault / Documents tabs are **removed**; they become folders.

The dock and page column never contain a repo, and production is never a dock entry — environments
live on the Deploys page.

## 7. Design-system grounding — verify before you add anything

Everything in the build is grounded in the repo, not invented. If you add to it, ground it the same way.

- **Colors** from the violet dark block of `packages/ui/src/styles/palettes.css`:
  `--bg` `#0f0e17`, `--bg-inset` `#16151f`, `--card` `#191824`, `--card-hover` `#201f2d`,
  `--field-bg` `#14131d`, `--border` `#262534`, `--border-strong` `#36344a`,
  `--fg` `#ebebf3`, `--muted` `#9998ac`, `--faint` `#65647a`,
  `--accent` `#9b8aeb`, `--accent-hover` `#b1a5f1`, `--accent-soft` `rgba(155,138,235,0.16)`,
  plus `--ok-soft` / `--warn-soft` for search badges. Earlier drafts contained invented surfaces
  (`#131220`, `#0c0b13`, `#1c1b28`, `#4e4d61`, `#3f3e52`) — those were removed. Do not reintroduce
  off-token hexes.
- **Type, radii, controls** from `packages/ui/src/styles/tokens.css`: sizes on 12 / 13 / 14 / 15 / 17 /
  24 / 32; radii 6 / 9 / 14 / 999; control heights 28 / 32 / 38; mono meta at `--fs-xs`.
- **Fonts**: Hanken Grotesk for UI, JetBrains Mono for paths, refs, counts and code.
- **Icons: Lucide only.** The repo mandates it. An earlier draft used hand-drawn glyphs and they were
  replaced with real Lucide geometry at stroke width 2 (House, Search, Settings2, MessagesSquare,
  Folder, FileText, BookOpen for Memory, Briefcase, Bot, Rocket, AppWindow, LayoutGrid, Activity,
  CalendarDays, BarChart3, GitBranch, SlidersHorizontal, Sparkles, ArrowUp, chevrons). Do not
  hand-draw paths.

Reference files worth reading in the repo: `frontend/CLAUDE.md` (the design-system law),
`frontend/src/App.tsx`, `frontend/src/components/appNav.tsx`, `frontend/src/pages/Chats.tsx`,
`frontend/src/pages/Memory.tsx`, `frontend/src/components/chat/chatScope.ts`.

## 8. How the deliverable is built

`source/Workshop Shell.dc.html` is a Design Component: an HTML template plus a logic class, inline
styles only, no stylesheets or CSS classes. `support.js` is the runtime and is generated — never edit it.

State lives in the logic class. Desktop: `sel` (dock selection), `page`, `tree` (column-two takeover),
`search`. Mobile is deliberately independent so both can be demoed at once: `mSel`, `mPage`,
`mDrawer`, `mDir` (directory path, `null` = not browsing), `mDoc`, `mSearch`.

Data lives in flat consts at the top of the logic class: `APPS`, `PAGE_SETS`, `CHATS`, `MEM`,
`FILE_TREE`, `MEM_TREE`, `MFS` (the mobile folder map), `CODE`, `NOTE`, `IC` (Lucide paths).

Both desktop and mobile are interactive to the same depth: dock/drawer app switching, page switching,
the Files and Memory takeover or drill-down, opening a file or note into the editor, and the search
palette.

Sample content is deliberately specific (`atlas` / `ledger` / `finance` / `commute`, a `reviewer`
agent, real-sounding chat titles and note names). It reads as a real workspace on purpose. Replace it
with real data, don't pad it.

## 9. Open questions

- **The chats page interior and the memory page interior have not been designed on their own terms.**
  They are represented inside the shell only. This is the next piece of work and the reason the shell
  was built first.
- Whether the mobile secondary toolbar should scroll horizontally or truncate when the page list is
  long. Currently it scrolls.
- Where genuinely global pages (Usage, Settings) sit relative to the app model. Currently they are
  leaves of Home plus a pinned dock entry for Settings.
- Whether saved views ever earn a dock entry. Current answer is no.
- The user raised a **command-first layer** (`1e` in the proposals) as a possible addition on top of
  the shell. Recommended, not built.

---

# Session 2 — shell refinements

Picked up from the session-1 export. The design was already correct in structure; this session was
corrections and motion. Session-1 notes above still hold EXCEPT where contradicted below.

## Changes made

**Search, one entry point per surface**
- Desktop: the search field was removed from the page column. Search is a single icon button in the
  dock, directly under Home, above the app tiles. `⌘K` unchanged.
- Mobile: search is a single button in bar one and TOGGLES — tapping it again closes the panel. The
  panel opens directly below bar one and covers the page toolbar; it replaces page content only,
  never bar one.

**Desktop dock behaviour**
- Selecting an app opens it FULL SCREEN (page column closed). Tapping the same app's tile again
  opens the page column; tapping again closes it. Home and agents default to the column open.
- Switching to a DIFFERENT app never toggles the column.
- Each dock entry remembers its own page, tree state and column state. Revisiting an app restores
  exactly what you left. First visit uses the default above.
- The Settings dock button was removed. The user avatar (BM) is the only bottom entry and will own
  account + settings.

**Page-column motion**
- The column slides itself left, out under the dock, while content slides in behind it — it does not
  simply get covered. Reverse on open.
- Toggling the same app animates (300ms, `cubic-bezier(.32,.72,0,1)`). Switching apps HARD CUTS —
  no transition. This is deliberate: motion means "the same thing changed," not "you moved."
- Files/Memory takeover is one coupled slide: the whole page column (title row included) translates
  fully out to the left as the tree comes in from the right. No cross-fade, no shadow overlay. The
  tree panel stays mounted so the exit slides too.

**Mobile bar one — final form**
Left to right: app tile · app name + caret · search · avatar.
- The **app tile** (uppercase letter, or a house glyph at Workshop) opens the app menu. It mirrors
  the desktop dock tile. There is no separate Home button.
- The **app name + caret** toggles the page toolbar. Caret down = closed, up = open, animated.
  Tapping the name or the caret both work.
- The page toolbar defaults to OPEN on every page, including the app page. A manual toggle holds
  until you change app or page. (Earlier in the session this defaulted closed on the app page; the
  user reversed that — never default closed.)
- The hamburger is gone. Its job moved to the caret.

**Mobile app menu**
- The left slide-over drawer became a DROPDOWN anchored under the title (card surface, rises on
  open). Contents unchanged: a Workshop/Home row, Apps, Agents.
- Each row's ref is the BRANCH PICKER — a tappable mono pill with a chevron that does not switch
  apps. This is the only branch control on mobile. It is not in bar one and not a separate row.
- No Settings row; the avatar owns that.

**Mobile page toolbar styling**
- 40px tall, labels sit 2px off the bottom edge so the 2px underline reads as attached to the label.
- Shares bar one's surface (`--bg-inset`) with a `--border-strong` hairline below, so the two bars
  read as one toolbar block separated from content.
- Horizontal scroll only, scrollbar hidden (it previously showed a full-width horizontal scrollbar
  inside a too-short strip).
- Bar one now has its own surface and bottom hairline; previously it blended into content.

## Motion inventory

| What | Treatment |
|---|---|
| Page column open/close | width + translateX, 300ms `cubic-bezier(.32,.72,0,1)`, same-app only |
| App switch | none — hard cut |
| Files/Memory takeover | coupled slide, 340ms, column out left / tree in right |
| Mobile page toolbar | height + opacity, 240ms |
| Mobile caret | rotate 180°, 240ms |
| Mobile app menu | rise + fade, 200ms |
| Overlays (palette, backdrops) | fade 160ms, panel rise 200–220ms |

## Still open / recommended, NOT built

Reported to the user and awaiting direction:

1. **Agents should not be dock tiles.** They are currently dock-ranked and get a page column identical
   in shape to an app's, which defeats the "list length tells you what kind of thing you're in" idea.
   An agent with no UI also has to fake an `Overview` page. Proposal: one `Agents` entry at root that
   opens a LIST in content (name, bound app, last run, state); selecting one shows its runs and
   schedule as content-level tabs. Bound agents appear on their app's Agents page instead of at root.
2. **Home should not mirror an app.** Home's page list is the union of every page, which is why
   `Files` feels wrong there — it implies a workshop-wide filesystem that does not exist. Proposal:
   Chats, Work, Apps, Agents, Deploys, Usage. Files and Memory belong to an app.
3. **Drop `Overview`** for agents; `Runs` is the real landing view.
4. **Dock rule going forward:** apps only, plus Home at top and the user at the bottom. Anything else
   that wants a dock slot is really a page of Home.
5. **Branch pickers are shells** — the pill and the drawer row open nothing yet.
6. Files search group tint, per-app last-viewed page on desktop (done) vs mobile (not done),
   command-first layer (`1e`), and the chats/memory page interiors — all still as in session 1.

## Mobile alternatives considered and rejected this session

The user asked whether the final mobile system is standard. Recorded for the next agent:

- **Title + caret conventionally means "open a menu,"** not "expand the bar below." The chosen system
  uses it as a disclosure control. This is the one learnable thing in the design; it was accepted
  knowingly, with the caret carrying the state.
- **Rejected: title opens a page MENU instead of a tab bar.** Simpler and more predictable (both
  controls become dropdowns, no third state, no pinned preference), but it hides the map and costs a
  tap per page switch. Worth revisiting if page switching proves rare.
- **Rejected: bottom tab bar.** The real mobile standard, but page sets run 7–9 items against a
  practical cap of 5, so it needs a "More," and it costs permanent height.
- **Rejected: no control, bar auto-hides on scroll.** Zero chrome and fully predictable, but the state
  is not the user's to pin.
- **Rejected (session 1, still rejected): mirroring the desktop two columns inside the drawer.** Two
  page switchers on the smallest screen.

## Files in this export

- `Workshop Shell (standalone).html` — the shell, self-contained, opens offline. This is the design.
- `Navigation Proposals (standalone).html` — session-1 proposals document, unchanged.
- `source/Workshop Shell.dc.html` — editable source. Template + logic class, inline styles only.
- `source/support.js` — generated runtime. Never edit.
- `source/github.md` — repo association.
- `HANDOFF.md` — this file.
- `TRANSCRIPT-session-1.md`, `TRANSCRIPT-session-2.md`.

## Where state lives now (updated)

Desktop: `sel`, `page`, `tree`, `lastTree` (keeps the tree mounted for its exit slide), `search`,
`colTwo`, `animCol` (false = hard cut), plus `this.mem` — a per-app map of `{page, tree, colTwo}`.

Mobile: `mSel`, `mPage`, `mDrawer` (now the dropdown), `mDir`, `mDoc`, `mSearch`,
`mTabsOverride` (`null` = use the default, which is open).
