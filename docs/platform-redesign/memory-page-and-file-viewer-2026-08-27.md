# Memory, the file viewer, and the structure underneath them

2026-08-27. Research, decisions, and the plan that follows from them.

Status: MODEL OF RECORD for the Memory page, its tree, and the shared
document viewer/editor. It supersedes, for these surfaces only:

- `shell-mockup-spec-2026-08-22.md` section 5 on disclosure triangles
  ("NO DISCLOSURE TRIANGLES") -- see D6.
- `file-viewer-and-editor-2026-08-22.md` section 4's split between a note
  (a record with an `ActionBar`) and a document (a file with a
  `DocumentBar`) -- see D7.
- `navigation-shell-2026-08-20.md` section 3.4's "arrival opens the tree",
  which was written before deep links into a document existed -- see D3.

Everything else in those documents stands.

---

## 0. What this is answering

The operator walked the Memory page on 2026-08-27 and listed what was wrong
with it, top to bottom. This document is the research behind that list, the
decisions, and the plan. His complaints, compressed to their claims:

1. A link to a note opens the tree over the page column, even though the
   note it linked to is already on screen.
2. The directory toolbar (`+ New`, `Filter`) is hand-built, mis-aligned,
   and duplicates controls the kit already has.
3. The tree's root mixes `development`, `role:reviewer` and `Documents`
   with no explanation of what any of them are.
4. Opening one note still draws the whole page header above it: search,
   scope, refresh, New note. None of that belongs over a document.
5. Library / Chats / Vault / Files are four unrelated things behind one
   switch, and nobody can say what separates them.
6. The Vault shows the operator's personal notes app inside the platform.
7. `N chunks indexed | <model>` at the bottom of every page is a
   developer readout on an operator surface.
8. Two different New controls, and the word for the thing they create
   changes depending on where you are standing.
9. The tree is missing the basics of a tree: a caret, and any sign of
   which folder you are in.
10. The editor is a nested scroller inside a scrolling page, and the title
    scrolls away above it.
11. There is no clean way to see and change a note's title.

---

## 1. Measured state

Everything in this section was read out of the tree on 2026-08-27. File and
line references are to that state.

### 1.1 The four tabs are four unrelated subsystems

Declared at `frontend/src/pages/Memory.tsx:173-179`. What each one actually
reaches:

- **Library** -- five routers (`routes/notes_all.py`, `docs_index.py`,
  `knowledge.py`, `files.py`, `memory.py`) over `shared/knowledge/**`,
  `docs/**` and the semantic index.
- **Chats** -- `GET /api/chat/threads`. The same list `/chats` already
  serves, which is a first-class place with its own page
  (`frontend/src/App.tsx:141`), its own nav rows
  (`components/shell/places.ts:188-201`) and its own dock tile.
- **Vault** -- `POST /api/stores/notes:vault/read`, which is the notes
  app's Markdown vault. That app has its own page at `/apps/notes`
  (`services/app_registry.py:506-511`).
- **Files** -- not `routes/files.py`. It is chat attachments,
  `GET /api/chat/attachments`, blobs under
  `.runtime/users/<id>/files/blobs/`.

Only Library touches the store the page is named after. Three of the four
tabs are a second door onto something that already has a front one.

### 1.2 The vault is hardcoded in platform source, and the purity gate cannot see it

`~/notes` is reachable from platform code in three places:

- `backend/services/permissions.py:86-87` -- `NOTES_VAULT_ROOT`, module
  level, `Path.home() / "notes"` behind a `WORKSHOP_NOTES_VAULT` override.
- `backend/services/stores.py:292` -- `declare("notes", "vault", ...)`,
  inside `register_builtin_stores()`, in the block whose own comment
  (`:280-282`) says "Everything app-specific is declared on that app's
  AppSpec and registered below, so a store exists exactly while the app
  that owns it is installed."
- `backend/services/notes_vault.py:34-35` -- the same default again.

It is operator-visible (`Memory.tsx:1381` prints the literal `~/notes` and
names Obsidian) and model-visible (`mcp/workshop_query_server.py:85`).

`scripts/check_no_app_names.py` reports zero findings for any of those
files. Confirmed by running it with `--json` and filtering: `imports` 0,
`literals` 0 for `notes_vault.py`, `permissions.py`, `stores.py` and
`Memory.tsx`. Three independent reasons: the watched alphabet is the eight
app ids from `roster()` and the literal here is `notes`, not `notes-app`;
`notes_vault.py` matches the path pattern that files a file as app CONTENT
and skips inspection; and `notes` is on the `_AMBIGUOUS` suppression list.

**This is an UNCOUNTED violation, not a counted one.** That is why the three
counts stayed clean while the operator kept reporting the same problem. Any
fix here that does not also teach the gate leaves the next person to
rediscover it.

`store-policies.json` does not exist in this tree, and the only client of the
`notes:vault` URI outside the backend is `Memory.tsx` itself
(`:107`, `:1328`, `:1358`, `:1438`).

### 1.3 `role:reviewer` is a whole scope kind for two hand-written files

- Produced by disk enumeration: `services/knowledge.py:215` adds one scope
  per directory under `shared/knowledge/roles/`.
- Parsed as a kind: `services/scope_tree.py:132`.
- Parentless on purpose: `scope_tree.py:267-268`, `return None  # the
  second axis, not a tree node`, repeated at `:297-298`; childless at
  `:483-484`; excluded from `TREE_KINDS` at `:89`.
- Renders at level 0 because a null parent is a root
  (`pages/memory/memoryTree.ts:87` via `childScopes(tree, null)`).
- On disk it is `shared/knowledge/roles/reviewer/`: `MEMORY.md` at 95
  bytes and `review-evidence.md` at 85 bytes, both committed once in
  `90029566` on 2026-07-31 and never written at runtime.

The operator's read ("some old thing left over") is close but not exact.
It is not dead -- it is a live second axis that the tree draws as if it
were a first-axis root, with no label saying so. The defect is the
rendering, not the concept.

### 1.4 `Documents` is not a scope

It is a synthetic UI node appended unconditionally as a root sibling of the
real scopes: `pages/memory/memoryTree.ts:35,45` and the `docsNode(...)`
append in `MemoryTree.tsx`'s root case. Its children are filesystem paths,
not scope/id pairs. So the root level mixes three address kinds -- a scope
key, a role key, and a repo-relative path -- distinguished only by an icon.

### 1.5 The word for one item is four words

`note` / `memory` / `knowledge` / `learning`, all live at once:

- directory `shared/knowledge/`, index file `MEMORY.md`, service
  `knowledge.py`
- four API prefixes: `/knowledge`, `/notes`, `/memory`, `/notes-app`
- page titled "Memory", default export named `Notes`, button "New note"
- and a SECOND store for the same concept that the page never mentions:
  `.runtime/learnings/*.jsonl`, written by `services/learnings.py` and read
  into every turn's prompt by `chat_context.py:1349,1375`.

No ADR freezes any of these, unlike `task`/`ticket`.

### 1.6 What actually breaks if things move

This is the constraint the operator named explicitly ("I'd rather leave it
if it's gonna break other stuff").

- **Backlinks** (`.runtime/backlinks.json`): rows are keyed by SOURCE ref.
  `services/backlinks.py` has exactly three mutators (`set_source`,
  `remove_source`, `remove_sources_with_prefix`), all source-keyed. Nothing
  anywhere rewrites a TARGET string. So a moved note or doc leaves every
  inbound edge stale until each source is independently rewritten.
- **Refs** (`services/refs.py`): `workshop:doc/<rel>` resolves against
  `paths.workshop_root()`, not `docs/` (`:132-144`), so moving a file under
  `docs/` silently kills every ref to it. And `extract_refs` turns
  `[[Title]]` into a note ref by TITLE LOOKUP at write time
  (`:190-208`), so **retitling a note silently drops every inbound wikilink
  the next time its source is rewritten.** This is a live correctness bug,
  not just a migration hazard, and it directly constrains complaint 11.
- **Semantic index**: 15,909 chunks keyed on filesystem paths.
- **`knowledge.all_scope_dirs()`** (`:161-227`) hardcodes the whole
  on-disk layout and is the single chokepoint for it.
- **No rename route exists** for a knowledge note or a doc. The only rename
  in the tree is the vault's own (`routes/notes.py:211`), which rewrites
  vault wikilinks and does not touch `backlinks.json`.

**Conclusion: nothing moves on disk in this pass.** Every structural
complaint is answered by changing how the existing structure is PRESENTED
and ADDRESSED, not by relocating bytes. The one exception is a bug fix
(retitle), which adds a rewrite rather than a move.

### 1.7 Why arriving at a note opens the tree over it

`components/shell/directoryEntry.ts`: `enterDirectory` returns
`{ pageId, open: true }` for any newly registered directory page. Arrival
always opens. That rule was written for an app place whose column starts
collapsed, where there is no row to tap twice. It predates deep links that
land directly on a document, and it has no way to know one happened.

### 1.8 Why the page header sits above an open document

`components/shell/pageChrome.ts`: `directoryChrome` returns
`{ header: true, controls: true, actionBar: false }` for ANY desktop
viewport, regardless of `documentOpen`. `documentOpen` is only consulted on
a phone. So on a desktop the "Memory" `PageHeader` plus its description,
the Segmented, and the Search / Refresh / New note row all render above
every open note and every open document.

### 1.9 The directory toolbar is hand-built

`Memory.tsx:533-550` and `pages/apps/tabs/AppFilesTab.tsx:523-540` both
render a raw `<button className="ws-dirtoolbar__new">` and a raw
`<input className="ws-dirtoolbar__filter">`. Styled at
`frontend/src/styles.css:3192-3221`: a 34px bar holding 28px controls, the
button with `padding: 0 8px` and no `display: flex`, so its `<Plus>` icon
sits on the text baseline rather than centred; the input with `border:
none` so the two controls in one row do not read as the same kind of thing.
Every complaint in item 2 is that CSS.

### 1.10 The document body is a nested scroller

`pages/memory/MemoryDocument.tsx:EDITOR_HEIGHT = "60vh"` on every surface
except the phone takeover. `packages/ui/src/styles/components.css:3463`
states the intent -- "It is NOT a scroller: the editor scrolls inside its
own height" -- but the page around it scrolls too, and `.ws-docbar` is not
sticky outside the takeover. So on a desktop there are two scrollers and
the title leaves the screen. `.ws-doc--full` already solves all of this and
is applied only when `takeover` is true, which is phone-only.

---

## 2. Decisions

### D1. Memory is one thing: the notes and documents this system knows

The Segmented goes. Memory is what was the Library tab, full width.

The other three tabs are not deleted as features; each one moves to the
place that already owns it:

- **Chats** -> `/chats`, which already exists and is a place. Nothing is
  built. `?tab=chats` redirects.
- **Vault** -> `/apps/notes`, the notes app's own page. `?tab=vault`
  redirects. This is the operator's stated scope violation and the fix is
  to stop drawing an app's private store on a platform page at all.
- **Files** (chat attachments) -> a new page at `/chats/files`, beside
  `/chats/archived`. An attachment is a thing you put in a chat; that is
  where you go to find it. The component moves unchanged. `?tab=files`
  and the existing `?tab=files&file=<id>` deep link from the attachment
  viewer both redirect, preserving `file=`.

Why not fold them into the tree as branches: three of the four resolve by
query, not by containment, and `navigation-shell` 3.5's rule ("a
collection is a page and never a tree") is correct and was not the problem.
The problem was putting four collections behind one switch on a page named
after one of them.

### D2. The word is `note`, the place is Memory, and `knowledge` and `learning` leave the vocabulary

Four words for one concept, and the operator has been reading all four.
Settled:

- **Memory** is the place. It is a page and a scope's store.
- **A note** is one item in it. "New note", "3 notes", "note type".
- **`knowledge`** survives only as a directory name and a module name.
  It appears in no user-visible string and no new API surface.
- **`learning`** leaves entirely as an operator-facing word. It already
  failed this test once (agent memory, development scope: "Do not name a
  concept after its provenance or after an internal mechanism"). The
  `.runtime/learnings` store stays where it is; what it holds is notes.

Applied in this pass: every user-visible string. Filed separately: the API
prefixes, the `Notes` default export, and folding the learnings store into
the note store, because those touch 28 frontend sites and four route
prefixes and are a migration rather than a rename.

### D3. Arrival opens the tree only when nothing is already open

`enterDirectory` gains a third argument. Memory passes false when the URL
carries `note`, `path` or `q`. Following a link to a note lands on the
note, with the page column left as it was.

This is complaint 1 exactly, and it is a four-line change to a pure
function with existing tests.

### D4. No page chrome above an open document, on any viewport

`directoryChrome` consults `documentOpen` on desktop as it already does on
a phone: `{ header: false, controls: false, actionBar: false }`. The
document's own bar carries everything it needs. This also fixes the same
defect on an app's Files tab, which shares the function.

### D5. The directory toolbar is built from the kit, and it is where search lives

One row above the tree, in the page column, holding:

- a search field (kit `Input`), which **filters the tree by label as you
  type** and **runs the full search on Enter**. That is one control for
  the cheap local narrowing and the expensive semantic query, and it is
  what lets the page's own Search / Refresh / New note row be deleted.
  The operator's own read: "filter is just search under a different name".
- **New note** (kit `Button`, icon-only, `FilePlus`).
- **Reload** (kit `Button`, icon-only, `RefreshCw`), at the right end.

Geometry: the bar grows from 34px to 40px so a 28px control is not touching
both edges, and the controls are the kit's, so they are centred because
every other control in the system is.

On refresh: it is not deleted, because an agent writes notes while the page
is open and the tree caches whole-index responses, so staleness is real and
the operator would otherwise have no way to see new work. It is demoted
from a labelled button in the content area to an icon on the tree's own
toolbar, and it is supplemented by an automatic reload when the tab has
been hidden for more than 30 seconds. Reloading is safe for open branches:
`FileTree` resets `levels` on an adapter change but never touches `open`.

### D6. The tree gets the two things a tree needs

Superseding `shell-mockup-spec-2026-08-22.md` section 5's "NO DISCLOSURE
TRIANGLES", at the operator's direct instruction on 2026-08-27:

- **A caret** to the left of every branch row, pointing right when closed
  and down when open. Leaf rows get an equal-width spacer so labels align.
- **The active path.** Every ancestor branch of the selected node is
  marked. Derived inside `FileTree` from the flattened rows, so it needs no
  new prop and cannot disagree with what is on screen.

Both land in `packages/ui` and therefore apply to the app Files tab too,
which is the point.

### D7. A note and a document are the same surface

Superseding `file-viewer-and-editor-2026-08-22.md` section 4's split. A
note gets the same `DocumentBar` a document gets, in a `title` variant that
renders the note's title in the sans face rather than a filename in mono.
The bar carries the title, the scope, the rendered/source toggle, Save and
Discard. Everything else (type, pinned, hook) moves into the body, visible
in edit mode.

The 2026-08-22 argument against this was that a note has no filename and
would print its title twice in mono. That is an argument against the mono
face, not against the shared bar, and the variant answers it. The operator
asked for one editor and one viewer across notes and files, and there is no
second thing a note needs.

Consequence: the title is editable in one obvious place, which is
complaint 11.

### D8. One scroller, and the bar stays

`.ws-doc--full` (sticky bar, editor sized to the viewport, one scroller)
stops being phone-only. A desktop document fills the content area the same
way. `EDITOR_HEIGHT`'s `60vh` survives only where a document is genuinely
one pane among others.

### D9. The root of the tree is grouped, and every group says what it is

`FileTreeNode` gains an optional `group`. `flattenTree` emits a group
header row when the group label changes within a level. Memory's root
becomes:

- **Scopes** -- `workshop`, `development`, and every `project:`/`app:`/
  `agent:` beneath them. The first axis.
- **Roles** -- `role:reviewer`. The second axis, now labelled as one
  instead of impersonating a root scope.
- **Documents** -- the committed `docs/` tree, labelled as the repo's
  documents rather than sitting nameless beside two scope kinds.

Nothing moves on disk and no address changes. This is 1.3 and 1.4 answered
by saying out loud what the three kinds are.

### D10. The index status footer leaves the operator's page

`N chunks indexed | <model>` is the semantic index's health. It moves to
`/settings/memory`, which is where the operator goes to ask how memory is
configured. Nothing else in the app prints an embedding model on a content
page.

### D11. Retitling a note rewrites the wikilinks that point at it

The bug in 1.6. `[[Title]]` resolves to a note id by title lookup at the
SOURCE's write time, so a retitle leaves every inbound `[[old title]]`
pointing at nothing, and the edge disappears silently the next time that
source is saved.

On retitle, the write path looks up the inbound sources (cheap: invert
`backlinks.json`, which is already in memory) and rewrites `[[old]]` to
`[[new]]` in each. Where a source cannot be rewritten, the retitle still
succeeds and reports what it could not update, because refusing to rename
is worse than a reported partial.

### D12. The purity gate learns about hardcoded home paths

`check_no_app_names.py` grew three independent blind spots that all hid the
same violation (1.2). A fourth count is added: **platform source may not
name a path under the user's home directory.** It is a narrow, mechanical
check (`Path.home() / "<literal>"`, `~/<literal>` in a user-visible string,
`os.path.expanduser`) with its own baseline, and it catches the class the
app-name alphabet structurally cannot.

---

## 3. Plan

Ordered. Each item is a filed ticket; ticket ids are recorded in section 4
as they are worked.

**Stage 1 -- the kit.** Caret and active path in `FileTree`/`FileDrill`
(D6); group rows in `fileTree.ts` + both renderers (D9); the `title`
variant on `DocumentBar` (D7); desktop `ws-doc--full` (D8).

**Stage 2 -- the shell.** `enterDirectory` arrival rule (D3);
`directoryChrome` desktop `documentOpen` (D4). Both are pure functions with
existing test files.

**Stage 3 -- the Memory page.** Delete the Segmented and three tabs, with
redirects (D1); rebuild the directory toolbar from the kit and move search
into it (D5); delete the page's own control row and the index footer (D5,
D10); group the root (D9); apply the vocabulary to every visible string
(D2).

**Stage 4 -- the document.** `NoteDocument` onto `DocumentBar` (D7); one
scroller on desktop (D8).

**Stage 5 -- the moves.** `/chats/files` for attachments (D1); the vault
store onto the notes-app `AppSpec` (D1, and 1.2's platform-source half);
the index footer into `/settings/memory` (D10).

**Stage 6 -- correctness and the gate.** Retitle rewrites wikilinks (D11);
the home-path check (D12).

**Filed, not built in this pass:** the API prefix rename, folding
`.runtime/learnings` into the note store, and any relocation of
`shared/knowledge/` or `docs/`. Each is a migration with the blast radius
measured in 1.6, and each is worth doing on its own with its own
verification, not folded into a UI pass.

## 4. Ticket log

Filled in as tickets are filed and closed.

Filed 2026-08-27, all in the `development` scope:

- MEM-1  `5130f5638dc148828dc3ceecc63fccc9`  caret + active path in `FileTree` (D6)
- MEM-2  `78374da228cc4054bb43e759cbcefd6a`  group rows in `fileTree.ts` (D9)
- MEM-3  `7ba5f93d387a48c68ddded15e4cfb6af`  `DocumentBar` title variant (D7)
- MEM-4  `0889d847f4494dea95fbb09cf39d1336`  arrival takeover rule (D3)
- MEM-5  `b9b11ab58bb54ca5845036bd35dc2cd9`  desktop chrome over a document (D4)
- MEM-6  `1feb0914934c4a05896765f1b1eb7662`  one page, three redirects (D1)
- MEM-7  `7b6af02264554b76a9a9b3ad6178d1b5`  directory toolbar + search (D5)
- MEM-8  `0860a18a35344553b3275afebf2c7bf2`  a note is a document (D7, D8)
- MEM-9  `de22a9936d6d46b29ca28cd6d41d5cf6`  attachments -> `/chats/files` (D1)
- MEM-10 `1afcbba57c884037bd7c4444c9074a6d`  vault store off platform source (D1)
- MEM-11 `8e8e46300577472190a809285acbc8ea`  index readout -> Settings (D10)
- MEM-12 `378dd09d321a4f588edc0c21a1172628`  retitle rewrites wikilinks (D11)
- MEM-13 `3732b1225fb6479d819567e5a370a7eb`  home-path purity check (D12)
