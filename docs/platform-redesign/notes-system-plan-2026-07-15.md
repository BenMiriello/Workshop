# Notes system: one scoped store, one surface, reconciled with docs (2026-07-15)

Status: reviewed against shipped code (every file:line below was read by audit, not assumed).
Scope: ASK N. Systematize the notes agents and chats scatter across the tree into one
scoped store with a real UI, make the empty Project "Notes" section actually work, and
reconcile that with the `docs/` tree and the plan docs we have been writing haphazardly.
Companion to `chat-tickets-orchestrator-plan-2026-07-15.md` (the ticket plan); this doc
mirrors its shape and slots in AFTER that plan's Phases 0-4.

## Executive summary

1. Do not build a new note store. The scoped knowledge store (`services/knowledge.py`) already IS a scope-generic markdown note store with exactly the scopes the operator asked for (workshop, project, agent, app), a per-scope index, REST CRUD, and chat-context injection. Notes is to knowledge what the Work surface is to tasks: same substrate, a real cross-scope UI on top.
2. The operator's premise "the Project Notes section is unwritable" is factually stale: a working composer already posts to `INTERVENTIONS.md` (`ProjectDetail.tsx:976-1027`, `projects.py:416-432`). What is true and load-bearing: agents never write there, it is a dead-end append log that no context injection ever reads, and it is a different substrate from the knowledge notes agents actually read. That split is the real bug.
3. Decision: retire `INTERVENTIONS.md` as the "Notes" surface. The Project Notes section renders the project's knowledge scope (`project:<slug>`), so operator-written notes land exactly where agents already read, and agent-written notes finally appear to the operator. One place, one substrate, symmetric read/write.
4. Three tiers, made explicit (the ADR pattern): ephemeral scratch (`scratch/`, `tmp/`, gitignored, agent thinking, never durable), durable scoped Notes (the knowledge store, operator-facing, injected into context), and long-form documents/plans/decisions (committed in `docs/`, versioned, discoverable from the Notes UI). The tiers stop the "one write-only markdown nobody re-reads" failure the research and our own docs already diagnosed.
5. Reconcile docs, do not swallow them: plans stay committed markdown in `docs/` (git versioning and editor-openability are the point), but gain an ADR-style convention and a read-only index surfaced inside the Notes UI so the operator sees notes and documents from one place and agents can discover both.
6. The commit-or-not policy becomes a durable artifact agents actually consume: a workshop-scope note plus a `SKILL.md` (so builders auto-discover it and assistants can load it), not another prose file that rots unread.
7. Learnings (`services/learnings.py`) stays a distinct concept (deduped operator corrections and failure patterns, injected as guardrails). Notes are freeform scoped knowledge. Do not merge them; cross-link them in the UI.
8. Phases N0-N3 slot after ticket Phases 0-4. File overlaps with the ticket plan (ProjectDetail.tsx, appNav.tsx, App.tsx, chat_context.py) are called out per phase so the builder sequences correctly.

---

## Part 1: audit of current state (what exists, file:line)

### Six substrates, three-plus formats, split commit boundary

The "scattered notes" complaint is literally true. Distinct note-writing surfaces found, each landing in a different place and format:

1. Project `INTERVENTIONS.md`: append-only stamped lines `[iso] who: text`, `POST /api/projects/{slug}/interventions` (`backend/routes/projects.py:416-432`), read by `GET` (`projects.py:406-408`). File at `projects/<slug>/INTERVENTIONS.md`, gitignored (`.gitignore` `/projects/*`). Working composer at `frontend/src/pages/ProjectDetail.tsx:976-1027` (`postNote` `:502-521`, `parseNotes` `:134-145`, loads on mount `:455`). NOT injected into any agent context (grep found no consumer beyond these two routes).
2. Build-agent `INTERVENTIONS.md`: a separate mechanism. Each build-agent cwd gitignores `INTERVENTIONS.md` (`backend/services/build_agents.py:143`); unacked entries ARE injected into that agent's context (`frontend/src/pages/AgentBuild.tsx:977`). So the two `INTERVENTIONS.md` variants diverge: build-agent = injected, project = dead-end.
3. Knowledge notes: `MEMORY.md` index + one `<id>.md` per note, per scope. Scopes and roots (`backend/services/knowledge.py:92-111`): `workshop` -> `shared/knowledge/`, `project:<slug>` -> `projects/<slug>/knowledge/`, `app:<name>` -> `apps/<name>/knowledge/`, `agent:<id>` -> `.runtime/agents/<id>/knowledge/`. Injected into chat via `index_block` (`knowledge.py:198-212`, wired `routes/chat.py:406-407`) plus on-demand `search` (`chat.py:728`). REST CRUD at `routes/knowledge.py` (operator-gated). Committed for workshop/app scopes, gitignored for project/agent scopes.
4. Learnings: `.runtime/learnings/<scope>.jsonl` plus a `shared/learnings.md` mirror for the `global` scope (`backend/services/learnings.py:97-98,209-228`). Deduped (hash + token-set Jaccard >= 0.85, `:255-267`). Injected as guardrails into every chat and into ralph runs (`chat_context.py:466-492`). Two formats for the same data.
5. Notes-app vault: general-purpose markdown vault at `~/notes` (`backend/services/notes_vault.py:26-32`), full CRUD (`routes/notes.py`, prefix `/notes-app`), frontend at `/apps/notes` (`frontend/src/pages/apps/notes/Notes.tsx`). Out of the repo tree entirely, never committed, Obsidian-openable. Disjoint from the per-scope knowledge substrate that feeds chat.
6. Chat threads (`.runtime/users/<owner>/chat/*.json`, `chat_store.py`), skills (`SKILL.md` per scope, `skills.py:38-53`), and true strays: `projects/<slug>/NOTES.md` exists on disk with no route or UI, and `shared/global-memory.md` is another loose memory file.

Supporting context services with no storage of their own: `workspace_digest.py` renders an ambient "ongoing work" block from tasks (no persistence, `digest_for_context` `:106-155`); `memory_tree.py` resolves a persona memory-inheritance tree from `shared/memory-tree.yaml`.

### The `docs/` tree

`docs/` holds ~10 subdirs (`agent-chat-redesign/`, `auth-redesign/`, `platform-redesign/`, `voice-mode/`, `workshop-v1/`, `proposals/`, `archive/`, ...) plus ~25 loose top-level plan/handoff `.md` files. `docs/platform-redesign/` alone holds 13 files. There is no index, no dated-decision convention, and no in-app discovery: plans are findable only by knowing the path. This is the "haphazard" the operator named.

### Why the Project Notes section reads as broken

It is empty by absence of writes, not by a missing write path. `_read_text` returns `""` for a project with no `INTERVENTIONS.md`, `parseNotes("")` returns `[]`, the empty state renders. Agents never post to it (nothing in the loop calls the interventions endpoint), and nothing injects it back, so it has no reason to fill and no reason to be read. It is a UI with no participants.

---

## Part 2: research findings and what we adopt/reject

### The write-only-markdown failure mode (adopt the fix, we already half-diagnosed it)

The dominant failure of file-based agent memory: a single growing markdown file becomes write-only. Agents append but never re-read or reconcile; stale facts silently lie to the model after a refactor; every conversation pays the full attention cost of irrelevant rules; contradictory notes cohabit with equal weight and no salience or half-life (Zep, "Markdown is not agent memory", https://blog.getzep.com/markdown-is-not-agent-memory/; MatrixOrigin, https://www.matrixorigin.io/blog/markdown-agent-memoria; "The MEMORY.md Problem", https://dev.to/anajuliabit/the-memorymd-problem-why-local-files-fail-at-scale-58ae). Anthropic's own framing: every irrelevant token in the window reduces processing quality of the relevant ones (the attention-budget point, surfaced in the same corpus).

What we adopt: notes must be scoped and retrieved, not concatenated. The knowledge store already does this (per-scope index injected, full bodies loaded on demand via search, `chat.py:406,728`), which is exactly why we build ON it rather than on a fresh `NOTES.md`. What we reject: any single flat notes file per project (that is what `INTERVENTIONS.md` and the stray `NOTES.md` already are, and it is the thing to retire).

### ADR / decision-record pattern (adopt the tiering)

Architecture Decision Records give agents "a sustainable memory": plain text in the repo, versioned, reviewable, that preserves the judgment code cannot (Nexapp, https://www.nexapp.ca/en/blog/architecture-decision-records-adr; adr.github.io, https://adr.github.io/; joelparkerhenderson/architecture-decision-record, https://github.com/architecture-decision-record/architecture-decision-record). The durable-vs-ephemeral distinction is the useful part: architectural decisions are few and belong in versioned records; implementation-level notes are numerous and are the ones most commonly lost. Agents that search accepted/deprecated ADRs before touching persistence or API boundaries stop re-litigating settled decisions.

What we adopt: the three-tier split (scratch / scoped notes / committed decision records) and a dated ADR convention for `docs/decisions/`. What we reject: an ADR-writer agent or heavy tooling; a `docs/decisions/NNNN-title.md` convention plus an index is enough for a single operator.

---

## Part 3: the design

### Substrate: knowledge store IS the Notes store

Adopt `services/knowledge.py` as the canonical Notes substrate. Keep the module/store identity `knowledge` in code (exactly as the ticket plan kept `task` while the UI says "ticket"); the UI noun becomes "Notes". The scopes already map one-to-one to the operator's ask:

- per-project notes -> scope `project:<slug>` (`projects/<slug>/knowledge/`)
- per-agent notes -> scope `agent:<slug>` (`.runtime/agents/<slug>/knowledge/`)
- general workshop backup -> scope `workshop` (`shared/knowledge/`, committed)
- (existing, unchanged) app notes -> scope `app:<name>`

Data model per note is already right: `<id>.md` file, first-useful-line-derived title, a `MEMORY.md` index of `- [Title](id.md) - hook` lines, atomic writes (`knowledge.py:121-125,224-241`). No schema change. The only additions are a cross-scope aggregation read and a couple of UI-facing fields surfaced from the existing index.

Commit-or-not policy (the durable artifact): scope decides. `workshop` and `app` notes are committed (shared, cross-cutting, want history). `project` and `agent` notes stay gitignored under `projects/*` and `.runtime/` (instance-local, per the existing boundary; a fresh clone starts blank per the instances model). This matches what the store already does; we make it a stated rule, not an accident.

### Surface: one `/notes` page, mirroring `/work`

- New `frontend/src/pages/Notes.tsx` at route `/notes`: cross-scope note list with a scope filter (workshop, each project, each agent, apps) and search, plus an editor drawer (title = first line convention or an explicit title field mapped to the index hook, body = markdown textarea, scope selector, delete). Four list states per the design system, tokens not hex, lucide not emoji, edit-mode for writes per `frontend/CLAUDE.md`.
- Backend aggregation: new `GET /notes` cross-scope list (mirror `tasks_all.py` from ticket Phase 2), aggregating `knowledge.index_block`/listing across workshop root, every project, every agent scope, each returning notes tagged with their scope. Per-note read/write/delete reuse the existing `routes/knowledge.py` endpoints (no new per-note routes needed).
- Nav: add a `Notes` item (lucide `NotebookPen` or `FileText`) in `appNav.tsx` next to `Work`. Notes and Work are the two operator-facing scoped-store surfaces.

### The Project Notes section, fixed

Repoint the Project detail "Notes" section (`ProjectDetail.tsx:976-1027`) from `INTERVENTIONS.md` to the project's knowledge scope (`project:<slug>`): render its notes with a composer that calls the knowledge PUT route. Now operator writes and agent writes share one substrate, and the section fills because agents already write knowledge notes. Add a "View all in Notes" link to `/notes?scope=project:<slug>`.

Retire `INTERVENTIONS.md` as "Notes." Recommended default (proceed on it): drop the project interventions surface entirely rather than keep a parallel append-log; if the operator later wants a chronological operator-to-agent directive feed, a note tagged `directive` in the knowledge scope is injected into context and does the same job without a second substrate. Keep the two interventions routes alive but unlinked from the Notes UI for one release in case the operator wants the old feed back; delete in a follow-up once Notes is proven. The build-agent `INTERVENTIONS.md` injection (`build_agents.py:143`) is a different, working mechanism and is out of scope here; leave it.

### Per-agent notes on the Agent page

Surface the `agent:<slug>` knowledge scope on the agent detail/build page the same way (a Notes section with composer), so an agent's running notes are visible and editable where the operator manages that agent, and cross-linked to `/notes?scope=agent:<slug>`.

### Reconciling docs: index and convention, do not swallow

- Keep long-form plans, designs, and decisions as committed markdown in `docs/`. Git versioning, diff review, and editor-openability are the reasons they are documents and not notes.
- Add an ADR convention: `docs/decisions/NNNN-short-title.md` (dated, status: proposed/accepted/superseded), seeded from the hard rules in CLAUDE.md and the load-bearing verdicts already made in the platform-redesign plans. Agents consult accepted/superseded decisions before re-opening settled ground.
- Add a docs index: a generated `docs/INDEX.md` (or a small `GET /docs` route that walks `docs/` and returns title + path + first heading) surfaced as a read-only "Documents" view inside the Notes UI. The operator sees notes and documents from one place; agents get a discoverable map instead of needing to know paths. This is the "organized, not haphazard" the operator asked for, without moving files into a store that would lose their versioning.
- Fold the strays: migrate `projects/<slug>/NOTES.md` content into the project knowledge scope on first touch; move `shared/global-memory.md` into a `workshop` knowledge note or a decision record; leave `shared/learnings.md` (it is the intentional `global` learnings mirror, `learnings.py:209-228`).

### The commit-or-not policy as a consumed artifact (anti-write-only)

Write the tiering and "where does this note go" rule once, as (a) a `workshop`-scope knowledge note and (b) a `SKILL.md` under `shared/skills/notes-and-docs/` so builders auto-discover it (`skills.py:179-202` symlinks in-scope skills into the cwd) and assistants can `use_skill` it. Content: scratch to `scratch/`/`tmp/` (ephemeral, never durable), scoped facts and running notes to the knowledge Notes store at the right scope, decisions and plans to `docs/` (ADR convention), never a flat per-project `NOTES.md`. Because it is a note and a skill, it is injected and discoverable, not a prose file that rots unread. Add one persona paragraph in `chat_context.py` telling agents to record durable scoped facts as notes (not scratch) and to open a decision record for choices that will outlive the chat.

### Agents actually leaving notes

Two wirings close "agents never leave notes there":
1. Persona rule (above): the general and build/project personas gain a short "record durable findings as a scoped note" instruction, pointing at the knowledge write path they already have.
2. Finalize-path capture: mirror the ticket plan's `learning-note` capture. Let a chat emit a fenced `note` block (scope + title + body) applied in the same finalize path as `task-patch`/`learning-note`, calling `knowledge.put_note`. About 30 lines beside the existing task-patch application. This makes "leave a note" a deterministic act, not a hope that compaction catches it.

---

## Part 4: sequenced phases (after ticket Phases 0-4)

Global constraints (same as the ticket plan): no em-dashes, no emoji in UI (lucide only), `frontend/CLAUDE.md` primitives and tokens, backend restart only via `scripts/restart_backend.sh`, stage only your own files (never `git add -A`), autonomy stays paused (verify via `--force` paths and scratch tests).

### Phase N0: policy + strays + concept lock [PARALLEL-OK, backend/docs only]

- Write the commit-or-not policy: a `workshop` knowledge note (`knowledge.put_note`) and `shared/skills/notes-and-docs/SKILL.md`.
- Add the ADR convention: create `docs/decisions/` with a `0000-template.md` and seed 2-3 accepted records from the platform-redesign verdicts already made (task-store-is-canonical, notes-substrate-is-knowledge, github-is-per-project). Committed.
- Fold strays: migrate any `projects/*/NOTES.md` and `shared/global-memory.md` into knowledge notes / a decision record. Leave `shared/learnings.md`.
- Seam files: `docs/decisions/*` (new), `shared/skills/notes-and-docs/SKILL.md` (new), one `knowledge.put_note` call (scratch script). No product-code edits.
- Verification: the skill is symlinked into a build cwd (`skills.link_into_cwd` smoke); the workshop note appears in `shared/knowledge/MEMORY.md`; `docs/decisions/` renders; grep shows no remaining `projects/*/NOTES.md` or `shared/global-memory.md`.

### Phase N1: the Notes surface [PARALLEL-OK with N0; owns its files]

- Backend: new `backend/routes/notes_all.py` mounted at `/notes` (`main.py` one mount line; coordinate: ticket Phase 2 also edits `main.py` for `/tasks`, so sequence this after or merge the mount edits): `GET /notes?scope=` aggregates `knowledge` listings across workshop root + every project + every agent scope, each note tagged with its scope. Per-note CRUD reuses `routes/knowledge.py`.
- Frontend: new `frontend/src/pages/Notes.tsx` (`/notes` route in `App.tsx`; OVERLAPS ticket Phase 2 which adds `/work` to `App.tsx`, sequence after it), scope filter + search + editor drawer, four list states. Add `Notes` to `appNav.tsx` (OVERLAPS ticket Phase 2 which turns `Jobs` into `Work` in `appNav.tsx`, sequence after it).
- Verification: create/edit/delete a note in a project, agent, and workshop scope from the UI; confirm files land under the right root (`projects/<slug>/knowledge/`, `.runtime/agents/<slug>/knowledge/`, `shared/knowledge/`) and the scope's `MEMORY.md` index updates; cross-scope list shows all three; widths 1440/1024/768/390 with long titles and empty scopes; tsc + `scripts/check_design.sh`.

### Phase N2: Project + Agent Notes sections repointed [SEQUENTIAL after N1]

- `frontend/src/pages/ProjectDetail.tsx`: repoint the Notes section (`:976-1027`) from the interventions endpoint to the `project:<slug>` knowledge scope with a composer using the knowledge PUT route; add "View all in Notes". OVERLAPS ticket Phase 2 (which also edits ProjectDetail.tsx for the task status map); sequence after ticket Phase 2 and coordinate the file.
- Agent page: add the `agent:<slug>` Notes section with composer + link.
- Retire the interventions surface from the UI (leave the routes for one release).
- Verification: an agent-written knowledge note in a project shows up in that project's Notes section without any interventions write; operator posts a note there and it appears in `/notes` cross-scope and is injected into that project's chat context (check the injected `index_block`).

### Phase N3: docs bridge + agent note-writing wiring [SEQUENTIAL after N1; persona part after ticket Phase 4]

- Docs index: `GET /docs` route walking `docs/` (title + path + first heading), or a generated `docs/INDEX.md`; a read-only "Documents" view in `Notes.tsx`.
- Note capture: a fenced `note` block applied in the chat finalize path (mirror `task-patch`/`learning-note`) calling `knowledge.put_note`; about 30 lines in the finalize seam. OVERLAPS chat finalize code touched by ticket Phases 3-4; sequence after ticket Phase 4.
- Persona: one paragraph in `chat_context.py` (general + build/project personas) on recording durable scoped notes and opening decision records. OVERLAPS ticket Phase 4 persona edits to `chat_context.py`; sequence after it.
- Verification: a chat that says "remember that X" produces a scoped knowledge note (check the scope file and `/notes`); the Documents view lists `docs/` with working open links; the persona still answers plain questions without gratuitous note-writing.

### Parallelization and overlap summary

- N0 is docs/skills/scratch only, safe to run alongside N1.
- N1 owns `notes_all.py`, `Notes.tsx`, and edits `main.py`/`App.tsx`/`appNav.tsx` which ticket Phase 2 also edits: sequence N1 AFTER ticket Phase 2 (or have one agent own those three shared files across both features).
- N2 edits `ProjectDetail.tsx` which ticket Phase 2 also edits: sequence after ticket Phase 2.
- N3's finalize-capture and persona edits touch `chat_context.py` and the finalize seam that ticket Phases 3-4 own: sequence after ticket Phase 4.

---

## Part 5: explicitly not now / needs operator decision

- Physically moving `docs/` plans into a note store: rejected. Plans stay committed markdown; we index and cross-link them. Revisit only if the operator wants in-app editing of long-form docs.
- Merging the `~/notes` vault with the knowledge Notes store: rejected. The personal vault (`notes_vault.py`, `~/notes`) is a different purpose (out-of-tree, Obsidian-openable personal notes). Surface a link from the Notes UI; keep the stores separate.
- Merging learnings into notes: rejected. Learnings are deduped corrections/guardrails; notes are freeform scoped knowledge. Cross-link, do not fuse.
- Embedding-based note retrieval: deferred. Knowledge search is substring/token today (`knowledge.py:270-288`); the corpus is tiny. Add local-embedding recall only if substring recall proves insufficient (the dedup embedding path from `unified-agent-model` is the template if so).
- Deleting the interventions routes: deferred one release behind N2 (keep the escape hatch until Notes is proven), then delete.
- Operator decision (recommended default taken, proceed): retire the project interventions feed entirely rather than keep it as a parallel "Log." Default is retire; flip only if the operator wants a chronological directive feed distinct from notes.
- No allowlist/audit/rate-limit/confirmation-gate on note writes: single-operator system; operator and agents write the same way with no gate.
