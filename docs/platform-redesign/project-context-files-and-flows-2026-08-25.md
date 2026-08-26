# Project-context files and flows: a proposal

Status: proposal, 2026-08-25. Extends
[project-context-files-audit-2026-08-25.md](project-context-files-audit-2026-08-25.md)
(the audit and the field standard) with a concrete data model, the create/import/
update flows, and how to stay portable. Prompted by the operator asking what the
SYSTEM is (not his local files), how these files get created and updated, whether
they live in the file list or in memory, and how to avoid baking in a magic
always-loaded filename that confuses on upgrade. Constraint throughout: design to
the generalized standard, not to one person's workflow, and keep it importable and
portable (few clear files, no custom junk a person has to strip to port away).

---

## 0. Decisions taken (operator, 2026-08-25) and what shipped

The operator decided these after reading the proposal below; where they differ
from the proposal body, the decisions win and the body is kept as reasoning.

- **Manifest is a MEMORY note, not a repo file** (reverses proposal 2b). It lives
  in the scope's memory as a reserved, pinned "Manifest" note, read and edited on
  the Memory page. SHIPPED: `knowledge.MANIFEST_NOTE_ID` + `write_manifest` /
  `read_manifest`; personas point at it; `live_context` no longer injects
  PROJECT.md.
- **Decisions have one home, in memory** (a decision-typed note), not
  `docs/decisions`. SHIPPED: the note block takes `"type": "decision"`;
  NOTE_PROTOCOL updated.
- **AGENTS.md loads, with aliases** (proposal 2a, approved). SHIPPED: `_agent_rules`
  reads AGENTS.md then CLAUDE.md / .cursorrules / copilot-instructions, closing the
  CLAUDE.md-invisible gap.
- **New-project button creates and opens a chat**, no form (reverses proposal 6 /
  decision 4). SHIPPED: the dock "+" creates a blank project and navigates to its
  chat.
- **Clear out dead code, but rehome what is still needed** (esp. settings). PARTLY
  DONE: removed the dead `invalidateAppList`. The larger removal (legacy project
  trio, inert app-entity write API) and the settings rehoming are deferred because
  they are entangled with the in-progress project refactor and settings needs a new
  home first. See section 5 and the closing note.

Follow-up owed: the new-project scaffold still writes a vestigial PROJECT.md file;
it should seed a Manifest note instead. Small, not done this pass.

---

## 1. Two research facts that decide the shape

- **Instructions are a solved, portable problem.** AGENTS.md is the cross-vendor
  standard for "what an agent should read to work in this repo" (Linux Foundation,
  read by 30+ tools). We already read it into the persona.
- **Agent DEFINITIONS are not portable.** There is no AGENTS.md-equivalent for
  "here is an agent (its role, tools, model, prompt) I can move between systems."
  The closest to a de-facto format is Claude Code's `.claude/agents/*.md`
  (YAML frontmatter + Markdown system prompt), which is the most widely produced
  complete definition file but is officially single-vendor. A2A's `agent-card.json`
  is a discovery/interop descriptor (how to CALL a running agent), not a build spec.
  CrewAI/Pydantic AgentSpec are declarative YAML but framework-locked.

Consequence: adopt the portable standard where one exists (instructions), and for
agent definitions keep our OWN internal model and provide import/export ADAPTERS to
the de-facto format, rather than adopting a foreign schema as native.

## 2. The data model: three artifact classes, each a plain file

The confusion today is that PROJECT.md tries to be two of these at once, and a
third (agent definitions) is tangled in. Split them cleanly.

### (a) Instructions: how to work here
- **File:** `AGENTS.md` at the scope root. The one always-loaded rules file.
- **Lives in:** the FILE LIST (the repo/tree). It travels with the code and is
  portable as-is.
- **Written by:** either. Some people write it by hand, some have the agent draft
  and update it. The platform must not assume one. It is an ordinary editable file.
- **Compatibility:** read `AGENTS.md` as primary, and INGEST `CLAUDE.md`,
  `.cursorrules`, `.github/copilot-instructions.md` as aliases when present, so an
  imported repo works with no conversion. On our side, the platform's own repo can
  keep `CLAUDE.md` as an alias/symlink of `AGENTS.md` so there is one source, not
  two (today `CLAUDE.md` is never even loaded by the chat backend, only by the CLI's
  own discovery, which is a real gap).

### (b) Manifest: what this is and where to start
- **File:** an OPTIONAL document (the thing currently called PROJECT.md; a project
  may rename it, e.g. MANIFEST.md, without the platform caring).
- **Lives in:** the FILE LIST (the repo/tree), NOT in memory/notes. It is a
  document that should travel with the code and be portable, and it is human- or
  agent-editable like any file. Putting it in the memory store would make it
  non-portable and hide it from anyone who clones the repo.
- **Written by:** either. Often human-seeded ("here is the goal and the plan"),
  then agent-updated. Neither is required.
- **THE KEY RULE (the operator's worry, addressed):** the platform must NOT hardcode
  a magic filename that is "always injected." A scope DESIGNATES which file, if any,
  is its manifest, as a small per-scope setting ("load this file as orientation"),
  defaulting to none. So there is no MANIFEST.md that is secretly special across the
  whole system, nothing to be confused about on upgrade, and no always-on file a
  user did not ask for. The manifest is just a file the scope points at, and the
  pointer is data, not a constant in the code.
- **Required?** No. On import of a repo with none, nothing breaks. On a fresh
  project we may offer to scaffold one, never require it.

### (c) Agent definitions: what an agent IS
- **Model:** our own internal schema (the existing role-keyed agents registry),
  which is where an agent's role, tools, model, and persona live.
- **Lives in:** the platform (the agent registry), with import/export adapters:
  IMPORT from `.claude/agents/*.md` (the de-facto file), EXPORT to the same, and
  optionally emit an A2A `agent-card.json` for interop. We do not store agents as a
  foreign format natively; we translate at the edges so we can import as-is and let
  people port away.
- **Written by:** created on demand by the operator or an agent, NOT at project
  creation. Making an agent is not a first step; it is something you do when you
  need a named worker or role.

### The clean line for "file list vs memory"
- **Portable documents that travel with the code:** AGENTS.md and the manifest.
  These are the FILE LIST.
- **Agent-accumulated memory (our system's stores):** working-state, knowledge
  notes + MEMORY.md, learnings. These are MEMORY, some committed per scope, some
  instance-local. The agent writes these; they are not the portable files.
- So the answer to "is AGENTS.md in the file list or memories": file list. "Is the
  manifest in notes/memories": no, file list. Memory is the separate, agent-written
  tier, and keeping that line sharp is most of the fix.

### Decisions get ONE home
Today a decision can land in PROJECT.md (unenforced instruction), a `docs/decisions/`
ADR (versioned, status/supersede), or a `decision`-typed note. Pick ADRs as the
durable record and drop the "append a decision note to PROJECT.md" instruction, or
keep a lightweight running log in the manifest and stop pointing at three places.
Three homes is the actual mess.

## 3. Authorship and protection (agnostic, no lock)

- Any of these files may be human- or agent-written. Do not enforce "human only" on
  any of them.
- Protection is by author-separation plus git-diff review, the field default: the
  portable files (AGENTS.md, manifest) are edited as files and reviewed as diffs;
  agent-accumulated learning goes to the memory stores, not silently into the human
  files. No permission-gate lock (it cuts against this system's single-operator,
  no-defensive-cruft stance).
- One optional refinement if wanted: an Aider-style "read-only to the agent" flag on
  a specific file the user wants the agent to consult but never overwrite. That is a
  per-file property, not a system-wide lock, and only if a real need appears.

## 4. Usage flows

- **Fresh project.** Create the scope, scaffold an `AGENTS.md` (standard) and
  optionally a manifest (offer, never require), then drop the operator into a CHAT
  in the scope. Do not create agents at this step.
- **Import an existing repo.** Read whatever context files are already there
  (AGENTS.md, or CLAUDE.md/.cursorrules/copilot-instructions as aliases) as-is.
  Require none of our own; add an AGENTS.md only if the operator asks.
- **Update.** The portable files change like any file (editor / file list, by human
  or agent). Memory changes through the memory stores (a note, a learning, the
  compaction-written working-state). The manifest updates as a normal document.
- **Make an agent.** A separate, on-demand action inside a project, defined by our
  schema, importable from and exportable to `.claude/agents/*.md`.

## 5. Findings from the current UI (the operator's dead-code worry)

Audited 2026-08-25. Real and worth acting on, separately from the above:

- **No consolidated project/app settings page.** Project settings (rename via
  `PATCH /api/projects/{slug}`, connect-github, autonomy, delete via retire) are
  scattered inline in the 72KB `ProjectDetail.tsx`; the app entity has no Settings
  tab at all (`places.ts` app tabs are App/Chats/Agents/Memory/Files/Tickets/Runs/
  Deploys).
- **Inert app-entity write API (high confidence dead):** `POST /api/apps`,
  `PATCH /api/apps/{id}`, `DELETE /api/apps/{id}` in `routes/apps.py` have zero
  frontend callers; the GET endpoints ARE used by the per-app tabs. `invalidateAppList()`
  in `frontend/src/lib/apps.ts` is exported with zero callers, and its comment
  describes UI that does not exist.
- **Legacy project entity, live but slated for retirement:** `Projects.tsx`,
  `ProjectNew.tsx`, `ProjectDetail.tsx` are the pre-"app is the entity" model per
  `places.ts` and `app-is-the-entity-2026-08-20.md`, still fully routed. Not dead,
  but the retire-target. The only working create path today runs through them.

These belong to the app-is-the-entity migration; this doc records them so the
cleanup is tracked rather than left to accrete.

## 6. The new-project button (built this pass, minimal)

A dashed "+" tile at the end of the desktop dock rail (`Dock.tsx`,
`ws-rail__tile--add`), same 38px footprint as an app tile, that opens the create
flow (`/projects/new`). Honest limitation, stated in the code comment: the rail
lists a FIXED set of apps from the frontend registry, not a live projects/apps
feed, so a newly created project appears on the Projects page and in its own chat,
NOT as a new rail tile, until the rail is backed by the registry (part of the
app-is-the-entity migration). Building a "+" that created an invisible tile would be
the half-built UI to avoid; this is the coherent minimal version.

## 7. Open decisions for the operator

1. Manifest: keep the PROJECT.md name or rename to MANIFEST.md, and confirm it is a
   scope-designated file (not a hardcoded always-loaded name). Recommended:
   designated, defaulting to none.
2. Decisions: one home. ADRs (recommended) or a running log in the manifest.
3. CLAUDE.md: make the chat backend load AGENTS.md consistently and treat CLAUDE.md
   as an alias, closing the "CLAUDE.md is invisible to data-mode chats" gap.
4. New-project target: open the create form (current), or create-then-open-chat.
   The latter is cleaner but depends on decision 1 and on the app-entity create path.
5. Dead code: schedule removal of the inert app-entity write API +
   `invalidateAppList`, and the eventual retirement of the legacy project trio, as
   part of the app-is-the-entity migration.
