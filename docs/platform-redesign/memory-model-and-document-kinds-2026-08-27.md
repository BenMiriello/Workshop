# The memory model: what kinds of thing we write, and where the rules live

- Date: 2026-08-27
- Status: **MODEL PROPOSAL.** Everything marked UNDECIDED below is an operator
  call and must not be built against yet. Everything else is measured.
- Ownership: this doc owns the MODEL (what kinds of document exist, what the
  folders mean, how the set is configured, which stores exist). The Memory page,
  the file viewer and the tree UI are owned by a separate thread and are out of
  scope here. Coordinate through this doc, not by editing the same files.
- Builds on: `memory-and-documentation-model-2026-08-14.md` (the tree, reach,
  governance, "write generously, inject almost nothing"),
  `file-viewer-and-editor-2026-08-22.md` sec 6 (a note is a scope and an id,
  never a path), `agent-context-and-toolkit-2026-08-20.md` (the four prompt
  layers), `tools-are-declared-2026-08-23.md` (the record shape for a declared,
  ownable thing).

---

## 1. The problem

An agent deciding where to write something has no rule to follow. Nothing in any
prompt layer names a document kind, a folder, or a note type. So the location is
pattern-matched off the existing tree, and the tree is now inconsistent enough
that pattern-matching produces more inconsistency.

Underneath that is a naming problem the operator raised directly: "decision"
reads like a claim to be final, in the way naming a file `final` does. And under
THAT is a structural problem: the same concept has more than one home, and one
of those homes is invisible.

---

## 2. What exists today (measured 2026-08-27)

### 2.1 Three stores, not two

**Notes** (`shared/knowledge/<scope>/`, `services/knowledge.py`). Flat: one file
per note, no folders inside a scope, a `MEMORY.md` index per scope. Four types
in `_NOTE_TYPES`: `decision`, `fact`, `reference`, `preference`. Type drives
ranking in the injected digest (`_TYPE_RANK`: preference and decision outrank
fact and reference) and display. It does not drive access and does not drive
location.

**Docs** (`docs/`). Foldered, untyped. 52 files loose at the top level, 139 in
`platform-redesign/`, 31 in `decisions/`, plus 38 across nine other
subdirectories. Folder names are ad hoc by topic. No folder states its own
purpose anywhere; there is exactly one `README.md` under `docs/`, in a vendored
source directory.

**Learnings** (`.runtime/learnings/*.jsonl`, `services/learnings.py`). 51 live
records plus 33 archived. This store is injected into every agent's context by
`chat_context.learnings_context` and rendered into the SessionStart hook by
`memory_block(GLOBAL)`. It has no page, no viewer, and no place in the Memory
tree. It is the one store that reaches every prompt and that the operator cannot
see or prune.

### 2.2 The learnings scope grammar does not match the others

Measured record scopes: `workshop` 32, `global` 12, `development` 7.

- `global` is accepted by `learnings._valid_scope` and by nothing else. It is
  not a value `knowledge._parse_scope` or `scope_tree.parse` will take.
- `.runtime/learnings/project-development.jsonl` and `project-workshop.jsonl`
  exist and hold **zero** records. They are the residue of a second spelling
  (`project:<slug>`, flattened to a filename) that was written once and is not
  what any live record uses.

So the mismatch is real but narrow: one extra scope word that no other parser
accepts, and two empty files proving a second spelling was tried.

### 2.3 The notes store is committed to the platform repo

`git ls-files shared/knowledge` returns **124 tracked files**, with 17 more
untracked. Nothing in `.gitignore` covers the directory. Anyone who clones this
repository gets one operator's accumulated notes.

This contradicts the code beside it. `knowledge._shared_knowledge_root` is
already per-owner, and its docstring gives the reason: one directory meant one
person's notes and house style were read into every other account's turns. The
store was made per-owner; the git tracking is the leftover that was not.

### 2.4 "Decision" has two homes and the two standing rules contradict

`memory-and-documentation-model-2026-08-14.md` sec 5.3: a durable decision
"still belongs in `docs/decisions/` as an ADR; the note points at it."

The later directive recorded on ticket `9318323f`: decisions get one home, in
notes and memory. That ticket is blocked on the conflict.

Both rules are live. `docs/decisions/` holds 31 ADRs. `type=decision` notes are
built and ranked. Neither rule has been retired.

---

## 3. The model

### 3.1 One principle

Name a thing by what its reader needs from it, keep the set small enough that
each kind fits on one line, and give every kind a lifecycle so nothing has to
pretend to be final.

### 3.2 On the word "decision"

The objection is right, and the fix is not a rename. A file named `final` is bad
because it has no lifecycle; an ADR carries a status (`proposed`, `accepted`,
`superseded by X`) and the superseded record stays readable, which is the whole
point of the form.

But the stronger reading is the operator's: **a plan is the better unit.** Most
of what gets written here as a decision is the tail of an argument, and the
argument is what a later reader actually needs.

**Proposed collapse.** There is no third kind of document called a decision.

- A choice statable in a sentence is a **note**. Already built (`type=decision`).
- A choice that needs a page of argument is a **plan**, and the plan ends with
  what was chosen and what was rejected.

This dissolves 2.4 rather than adjudicating it. UNDECIDED (see 6.2).

### 3.3 The default set

Four folders and one store.

- `docs/research/` - what we found out. Measurements, inventories, sources. No
  proposal in it.
- `docs/plans/` - what we intend to do and why, including what was chosen and
  what was rejected. Carries a status; supersedable.
- `docs/design/` - how a thing is meant to work. Product and technical together.
- `docs/reference/` - how a thing works now, for someone using it. Current,
  descriptive, no history.
- **Notes** (memory) - anything that does not need a page. One fact, one note.

Technical and product design are merged into one `design/` because in this repo
the same document routinely does both (the entity model doc is both), and
splitting them forces a false choice on every write. UNDECIDED (see 6.1).

**Not adopted: Diataxis** (tutorials / how-to / reference / explanation). It is a
framework for user-facing product documentation; this set is internal
engineering writing. What is taken from it is the single rule that a document
serves one need and a document doing two jobs should be split. Importing its
folder names would be structure for its own sake.

### 3.4 What happens to the learnings store

**It does not survive as a separate store.** A learning is a preference or a
correction, which is a note type that already exists. Keeping it separate costs
three things that are all live problems: a second scope grammar (2.2), a second
write path with its own dedup and quality rules, and a body of text that reaches
every prompt while being invisible on every surface.

The last of those is the decisive one. There is a standing correction on record
against exposing a concept the operator cannot see and prune, and against naming
a concept after its internal mechanism. "Learnings" fails both.

Proposed: learnings become `type=preference` notes in the scope they already
name, `global` maps to the governance layer (`read_layers` element 0, today
`workshop`), the two empty `project-*.jsonl` files are deleted, and
`learnings_context` is retired in favour of the existing ranked digest, which
already has a token budget and already declares what it omitted.

This is a migration of 51 live records with a reconciliation path
(`services/learnings.py` already implements NOOP / UPDATE / SUPERSEDE / ADD), so
it is tractable, but it is not small. UNDECIDED (see 6.4).

### 3.5 What happens to git tracking

A user's notes are data, not source. They should not ship in the platform
repository, and a fresh clone should not carry one person's memory.

Proposed: `shared/knowledge/` stops being tracked by this repo. What stays
tracked is what ships as product: the default folder map (code), and documents
under `docs/` that are genuinely platform documentation.

Git remains the right history and backup mechanism for notes; the question is
only which repository. UNDECIDED (see 6.5), because "gitignore it here and give
it its own repo" and "gitignore it here and back it up another way" are
different answers with different costs, and losing the existing history is not
acceptable in either.

---

## 4. Configuration

The folder set is a default, not a law. Different installations and different
projects should be able to differ, and anyone who does not care should never
have to think about it.

**Shape, following `services/memory_policy.py` exactly** (which is the working
precedent in this tree): a default map in code, an override file that is
normally absent, resolved narrowest-wins, read once and cached against
(size, mtime). Reading a missing file is not an error and never creates one.

Each entry is a path and one line saying what goes there. Nothing else. A
folder's definition is one sentence because that is all a writer needs at the
moment of writing.

---

## 5. Where the guidance is surfaced

Three tiers. Only the first two are proposed for now.

1. **Configuration, not prose.** The map above. It is the source of truth and it
   is not a prompt string.
2. **Two lines in L1 orientation**, naming the store and the verb, never the
   taxonomy: that memory and docs exist, that a note is one fact, and that the
   folder set is readable on demand. Nothing more rides in the prompt.
3. **The definitions arrive at the moment of writing**, returned by the tool
   (`where_is`, or a small guide verb), not injected every turn.

The reason tier 3 is a tool response and not prompt text is measured:
`index_block` runs on a 500-token budget across all read layers with a 40 per
cent floor reserved for governance. Folder definitions injected every turn would
compete with the notes themselves for that budget, in every chat, forever.

This is the same reach-by-tool rule L1 already runs on, and it matches the
operator's own framing: loaded when the agent is reaching for the notes
directory, not baked into the prompt.

---

## 6. Open decisions (operator)

1. One `design/`, or split technical and product design?
2. Adopt the collapse in 3.2, retiring "decision" as a document kind for new
   writing while leaving the 31 existing ADRs in place? This is the call that
   clears ticket `9318323f`.
3. Should a note carry a status (`current`, `superseded by X`) so it can be
   retired without deletion? It has a type today but no lifecycle, which is the
   same gap that makes "decision" read as a claim to be final.
4. Fold the learnings store into notes as 3.4 proposes, or keep it separate and
   only fix its scope grammar?
5. Untrack `shared/knowledge/` from this repo, and if so, where does its history
   go?
6. Is the config per installation only, or also per app and project?

---

## 7. Explicitly not now

- **No sweep of the 191 existing documents.** The map says where NEW writing
  goes. Existing folders keep working and `docs/decisions/` keeps its records.
- **No UI.** The Memory page, the viewer and the tree belong to another thread.
- **No new note types.** Four is enough; the gap is lifecycle (6.3), not kinds.
