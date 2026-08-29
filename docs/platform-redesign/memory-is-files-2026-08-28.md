# Memory is files

**Status: proposal, 2026-08-28.** Replaces the note-form model on the Memory page
with the one the operator specified months ago and has now restated three times: a
file tree, a file viewer, a file editor. It supersedes the note-shaped parts of
[`what-a-memory-is-2026-08-23.md`](what-a-memory-is-2026-08-23.md) and
[`finding-and-surfacing-memory-2026-08-23.md`](finding-and-surfacing-memory-2026-08-23.md),
and it is built on the editor already specified in
[`file-viewer-and-editor-2026-08-22.md`](file-viewer-and-editor-2026-08-22.md),
which is CodeMirror 6 and is already installed.

---

## 1. The record

Operator, 2026-08-28, in a note he wrote on the Memory page believing it had
failed to save:

> "The memory page makes no sense. I gave very clear instructions about it being
> files and a file viewer/editor and having a normal file viewer for memories and
> for files (like source code files, different page but shares a great deal with
> memories) and folder tree viewer. Yet i still see this form for creating a note
> which directly and flagrantly contradicts that design direction... the whole idea
> and structure is wrong."

> "I fucking hate that you have to pick a 'type' for a memory... remove all of that.
> idk what 'pinned' is i dont need that remove that all entirely. dont just delete
> the ui delete the entire feature/data."

> "having 'scopes' in the folder tree navigator makes no sense... should be memories
> in workshop root, and in development app, and in each other app. all different
> file trees."

> "Stop putting explainer text all over the place. the application itself is not a
> place for you to leave notes to me!"

> "when I say to solve things globally i mean it."

**The note saved.** It is on disk at
`shared/knowledge/development/aug-28-user-note-mtdq4q5f.md`, 4,084 bytes, complete,
written at 18:50. He watched the button spin and concluded it had failed. That is
the most damaging bug on this page and it is not a save bug: it is a bug in saying
so.

---

## 2. What is actually wrong, measured

Every item below was checked in the tree today, not inferred.

1. **The spinner never clears on success.** `MemoryDocument.tsx:545` sets
   `saving` true; the success path calls `onCreated(...)` and never
   `setSaving(false)` (the only `setSaving(false)` is in the `catch`). It relies
   entirely on the parent unmounting the component. The PUT itself answers in 6ms,
   verified against the live backend.
2. **The name field refills itself.** `MemoryDocument.tsx:567` passes
   `name={title || "Untitled note"}` as the DocumentBar's *value*. Clear the field
   and the fallback becomes the value again, so the text cannot be emptied. It is a
   placeholder written into the wrong prop.
3. **A memory has a `type` and a `pinned` flag.** `knowledge.py:829`
   `_NOTE_TYPES = ("decision", "fact", "reference", "preference")` with a rank
   table at `:680`, and `pinned` parsed and written in `_with_meta`. Both are
   asked of the operator at write time, which is the wrong moment to ask anybody
   anything, and neither survives this proposal.
4. **The tree is a tree of scopes.** `Memory.tsx` navigates by
   `selected.kind === "scope"`, so the first level of the navigator is an internal
   addressing concept the operator has said repeatedly must never appear in the UI
   (`app-is-the-entity-2026-08-20.md`: "scope is internal addressing that never
   appears in the UI").
5. **Creating is a form, not a file.** A "Goes in" scope selector, a type picker, a
   pinned toggle, a hook field. None of this is how anyone creates a file anywhere.
6. **Reload is a button.** `Memory.tsx:6` and `AppFilesTab.tsx:611`. He said
   clicking to get updates was wrong; the response was to move the button. The
   button is not the fix and was never the complaint.
7. **The page explains itself.** "Pinned notes, then preferences and decisions,
   rank first in what gets read into every chat in this scope." "Write the note in
   markdown." This is developer commentary shipped as product copy, which is the
   same defect as the drift banner deleted this morning, and it has now been
   reported at least three times.
8. **The focus ring is two rings.** `palettes.css:72` and its eleven siblings:
   `--focus-ring: 0 0 0 2px var(--bg), 0 0 0 4px rgba(accent)`. A spacer ring plus
   an accent ring. On any element that also has a border, that is three lines. And
   `components.css:1434` gives `.ws-input:focus` a *changed border colour plus* a
   3px soft ring of its own. Both are global rules and neither was changed; what
   was changed was one component.

---

## 3. The model

> **A memory is a file. The Memory page is a file browser. There is no note
> object, no note type, no pinned flag, and no scope in the navigator.**

Everything the note model carried that is genuinely needed is already expressible
as a file: the title is the filename, the body is the content, the location is the
folder. What the note model added on top of that (type, pinned, hook, a scope
selector at write time) is removed, data included.

### 3.1 The trees

Not one tree of scopes. **Separate trees, one per place that owns memories**, which
is what the operator asked for:

    Workshop            the root: memories about this installation and its owner
    Development         memories about building Workshop itself
    <each app>          memories belonging to that app

Which tree you are in is chosen the way any app chooses a place, not by a node
inside the navigator. The word "scope" appears nowhere.

### 3.2 Inside a tree

Two top folders, and this is a rule already established and not being invented
here:

    system/     platform-owned, a light starting structure, seeded on install
    user/       the operator's, empty at first, his to organise however he likes

`system/` starts with a small folder set and no more. `user/` starts empty. Nothing
auto-files anything into `user/`.

### 3.3 The viewer and the editor

The kit already has both, and they are already CodeMirror 6: `packages/ui/Editor.tsx`
plus `DocumentBar`, with `@codemirror/lang-markdown`, `language-data`, `search` and
`commands` all installed (`frontend/package.json:17-23`). The source-file page
(`AppFilesTab`) already uses them. **Memory uses the same components, with the same
behaviour, and gets syntax highlighting and search for free.** The answer to "did we
not already do this" is yes, and the Memory page routed around it.

The two pages differ only in which tree they are pointed at. They are not two
implementations.

---

## 4. Creating a memory is creating a file

The normal convention, which every file browser in the world already teaches:

- **New** creates a file in the folder currently open. It appears in the tree
  immediately, with its name in an inline rename state, exactly as a desktop file
  manager does.
- The name is the filename. The placeholder is a placeholder and never becomes the
  value; clearing the field leaves it clear.
- The body opens in the editor. There is no second "content" field, no hook, no
  type, no pinned, no destination selector, because the destination is the folder
  you were standing in.
- **Save** writes the file, clears its own busy state, and the tree shows the file
  where it landed. That is the confirmation. Nothing spins after the write returns.
- Moving a memory is moving a file. Renaming is renaming a file, and the rename
  already has to repoint refs, which the doc-rename fix landed today.

No modal. No form. No "Goes in".

---

## 5. What gets deleted, data included

The operator was explicit that this is not a UI change:

- **`note_type`**: the `_NOTE_TYPES` tuple, `_TYPE_RANK`, the parser, the writer,
  the picker, and the stored value in every note file's metadata comment.
- **`pinned`**: same list. Both its ranking behaviour and its data.
- **The scope node** in the navigator, and the "Goes in" selector.
- **The Reload buttons** on Memory and on the app Files tab.
- **The explainer strings** named in §2.7, and any sibling of them on the page.

Ranking still has to answer "what gets read into a prompt first". It answers it
from the standing-note mechanism (`reach: always`), which already exists and is
being consolidated under ticket 81198145, not from a type the operator has to pick.

---

## 6. Fresh without asking

Reload disappears, and the data stays current because writes invalidate what they
touched. A write to a file invalidates that tree; the tree re-reads. This is the
TanStack Query invalidation the rest of the app already does, applied here, which
is what "fix it" meant the first time.

---

## 7. Two global rules, stated as global rules

**The focus ring is ONE line.** `--focus-ring` becomes a single ring in every
palette, and `.ws-input:focus` stops drawing a second treatment beside it. One
decision, applied in the token and in the base input rule, so nothing needs a
per-component patch ever again. Twelve palette definitions and one component rule;
no component-level focus overrides survive this change.

**The application is not a place to leave notes for the operator.** No explainer
text under a control, no sentence describing how a feature ranks, no parenthetical
teaching the format. If a thing needs explaining to be usable, it is designed
wrong; fix the design. This is the same rule as the drift banner and the same rule
as "no app copy addressed to a developer", and it has now cost three reports.

---

## 8. What to build, in order

1. **Stop lying about saves** (§2.1) and **stop refilling the name** (§2.2). Two
   small fixes, both live defects, both independent of the redesign.
2. **The global focus ring** (§7). One token, twelve palettes, one input rule.
3. **Delete type and pinned**, data included (§5), with a migration that strips
   them from existing note files.
4. **The trees** (§3.1, §3.2): per-place trees, `system/` and `user/`, no scope node.
5. **Memory renders through the kit editor** (§3.3), sharing the source-file
   components rather than duplicating them.
6. **Create is a file, not a form** (§4).
7. **Reload disappears; writes invalidate** (§6).
8. **The explainer strings go** (§7), and a gate keeps them out, in the shape of
   the prompt-layer checker that already exists.

1, 2 and 8 are same-day. 3 through 7 are the redesign and want one chat, in order,
because each one moves the same files.

---

## 9. Not in this document

**Development should be its own project, not the home scope.** Operator, same note:
"we need a development project. development on workshop needs to go in a project.
only in development env/mode of course. nothing of the development app/code/
capability should ever serve in production." That touches scope resolution, the app
registry, prompt layers, and what ships in a production build. It is a separate
research and design effort and gets its own doc, not a paragraph here.

---

# Investigation, 2026-08-28 (later): five findings, three of which change §3 and §5

Measured on the tree after the proposal above was written, at the operator's
instruction to look further before building. Where this section and the proposal
disagree, this section is what was counted.

## A. The on-disk layout is ALREADY per-place. §3.1 is mostly a presentation change.

    shared/knowledge/*.md                96 notes   the Workshop root
    shared/knowledge/development/*.md    49 notes   Development
    projects/<slug>/knowledge/*.md        2 + 2     per project (commute, development)
    apps/<name>/knowledge/                          per app, present, empty here
    shared/knowledge/roles/                         empty

So "memories in workshop root, and in development app, and in each other app, all
different file trees" is already true of the DISK. The scope node in the navigator
is a UI invention layered on top of a layout that already has the shape the
operator asked for. That makes the tree work smaller and safer than §3.1 implied:
the presentation changes, the files do not move.

## B. `note_type` and `pinned` have no consumers outside two files.

Grepped across `backend/services`, `backend/routes` and `backend/mcp`: every other
hit for "pinned" is ownership-pinning or a UI comment. The only real consumers are
`knowledge.py` itself and `standing.py`. So the deletion in §5 is contained.

What they actually do: `_index_rank_key` (`knowledge.py:731`) orders notes for a
digest bounded by `DEFAULT_INDEX_TOKEN_BUDGET = 800`, pinned first, then type, then
recency. Deleting both leaves that ordering as recency, which is a real loss of
signal and needs the successor named in the next finding.

## C. The successor already landed, today.

`reach` (`_NOTE_REACH = ("index", "always", "search")`, default `index`) is the
axis that replaces type and pinned, and it is better than both because it states
what a note DOES rather than what kind of thing it is:

    always    injected verbatim into every prompt in its scope
    index     appears in the bounded digest
    search    findable, never automatic

It shipped in `a4bda009` ("a note carries how far it reaches into a prompt") and
`services/standing.py` is built on it. So §5's claim that ranking "answers from the
standing-note mechanism" is not a promise, it is a description of code that exists.

**One coupling to sequence:** `standing.py` writes notes with
`note_type="preference"` in three places (lines 406, 418, 429), and that file is
still uncommitted work in flight under ticket 81198145. The type deletion has to
land after it, or on top of it, not beside it.

## D. The system/user split does not carve this corpus. It needs the operator.

Author markers across all 145 notes:

    derived:claude-sonnet-5    ~62    written by a summarizer
    self                       ~35    written by an agent in a chat
    (no marker)                ~13    older, unattributed
    operator                     1    his note from today

A split on authorship would put roughly 144 notes in `system/` and 1 in `user/`.
That is not a folder structure, it is a relabelling. **The memory store is almost
entirely machine-written notes about the machine**, which is worth saying plainly
because it changes what the Memory page is FOR. §3.2 stands as the target shape,
but the rule that assigns existing notes to a side is an open question and is
listed as such below.

## E. The Workshop root is full of development notes, which is the memory-store
version of §9.

Sampling the 96 root notes by title: "a flex item with default min-width silently
defers", "agents must come from explicit declarations", "chat composer input
retention fixed via clearNow", "a lazy route blanks permanently on a stale chunk".
These are engineering findings about building Workshop, sitting in the root scope
that is supposed to hold memories about this installation and its owner.

So the operator's instinct that development belongs in its own project is visible
here too, independently of the code: the highest-traffic memory scope in the system
is a development log filed under the user's own root. Whatever the development-as-
a-project design concludes, this corpus moves with it.

## Consequences for the build order in §8

- Step 4 (the trees) is smaller than written: presentation plus the `system`/`user`
  presentation, with no file movement for the per-place part.
- Step 3 (delete type and pinned) must sequence AFTER the standing-note fold lands,
  because that file writes a type.
- A new step, before either: decide what `system/` and `user/` mean given finding D.

## Open, and the operator's

1. **Given that 144 of 145 memories are machine-written, what is `user/` for?** Is
   it where he writes, with everything the system learned in `system/`? Or is the
   split about SUBJECT (memories about him and his preferences versus memories about
   the code), which would cut the corpus very differently and cannot be derived from
   the author field?
2. **Do the 96 development notes in the Workshop root move to Development now, or
   with the development-as-a-project migration?** They are misfiled either way.
