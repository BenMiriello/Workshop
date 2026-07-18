# Agent / chat / knowledge / project-builder redesign

> **Superseded on 2026-07-18** by
> [`docs/platform-redesign/entity-model-2026-07-18.md`](../platform-redesign/entity-model-2026-07-18.md)
> and the phase docs on top of it. Assistant/builder as a runtime distinction
> stays as plumbing but is no longer the agent-typing axis (role is). Letta was
> removed in the 2026-07-15 Stage 9 teardown. Kept for historical reference;
> do not update.

Status: PLAN ONLY (2026-07-11). Not built. Research + design pass. Operator has locked the core
decisions (below). Build starts at Phase 1 when the operator says go.

Full research proposal (external framework citations, longer reasoning) was produced by a Fable
pass; this doc is the reconciled, decision-locked version that supersedes it.

## Locked decisions (operator, 2026-07-11)

1. **Ontology adopted.** General chat = an agentless thread. Assistant and Builder are two *runtimes*
   of ONE Agent concept, not separate kinds. A chat converts to a project by re-keying its scope, not
   by mutating a type. ("data" dies from the UI; "Assistant" is the label; "Builder" stays.)
2. **Real memory engine now** = lean into Letta (already running), not files+grep, and not new infra.
   Letta is the engine (self-editing blocks + archival vector store); per-scope markdown files are the
   human-editable, git-tracked source of truth that syncs in. `recall(scope, query)` = semantic search
   over the scope's archival namespace. Sharing still governed by sensitivity + grants. NOT a new
   vector service unless the operator later says pgvector/Mem0/Zep specifically. Tension flagged:
   couples data-mode chat knowledge to Letta uptime; files-as-source-of-truth gives a file-index
   degrade path on outage. Operator confirmed: store BOTH plaintext AND vectors (plaintext =
   source/inspectable/git; vectors = recall). (Phase 2 concern, not Phase 1.)
3. **Context engine for data-mode assistants adopted** as designed: cache-stable prompt ordering,
   append-until-threshold + one compaction pass (not a sliding window), fact distillation into scope
   knowledge on compaction. Raw messages retained in thread JSON so compaction is reversible.
4. **Start at Phase 1**, refined: the real primitive is general interactive in-chat UI primitives any
   chat can use any time; project-creation-from-chat is one application of it; plus a "build with chat"
   general-agent-for-a-blank-project discuss-before-commit mode; the re-key conversion is a small
   single path, only on explicit user ask, NOT the centerpiece. Don't overbuild it.

## Concept model (6 concepts, 5 already in code)

- **Chat** = a thread (messages + scope). Stored `.runtime/users/<owner>/chat/<id>.json`.
- **Agent** = standing config. Runtimes: **Builder** (Claude Code over a cwd, `build_agents.json`) and
  **Assistant** (stateless provider loop, optionally Letta-backed for always-on). One concept, a
  `runtime` field, not two ontologies.
- **Scope** = where knowledge/context lives: `workshop` (global / user notes), `app:<name>`,
  `project:<slug>`, `agent:<id>`. Already encoded in `context_key`; promote to a named concept, keep
  `context_key` as the wire format. Document the mapping in chat_context.py; do not add a parallel
  scope field.
- **Skill** = portable instruction bundle. Adopt the SKILL.md Agent Skills open standard (folder =
  YAML frontmatter + body + optional scripts). Import/export = copy a folder.
- **Modality** = per-thread interaction option (text default, voice), orthogonal to the agent.
- **Autonomy mode** = off / scheduled / event-driven / continuous (exists). Unify across runtimes +
  add per-project loops.

Do NOT add workspace/crew/conductor/role as first-class objects; they fall out of the above.
A "role" = persona (assistant) or PROJECT.md/AGENTS.md (builder) + a set of skills.

## Chat <-> project/agent conversion

Thread identity = its message history; scope = `context_key`. Conversion = re-key
(`general` -> `project:<slug>`), history preserved, new provider layers PROJECT.md next turn. Add a
`converted_from` + timestamp provenance field. Collision risk: a `project:<slug>` thread may already
exist -> suffix the thread id, not the key. Minor path, only on explicit ask.

## Knowledge & context architecture

### Scopes (Letta-backed engine, files as source)
- `shared/knowledge/` (workshop scope = the user-notes baseline; subsumes `shared/global-memory.md`),
  `apps/<app>/knowledge/`, `projects/<slug>/knowledge/`, `.runtime/agents/<id>/knowledge/`.
- Each dir has a small `MEMORY.md` index (one line per note). Index is ambient (loaded into the
  stable prompt prefix); full notes load on demand.
- Files sync into Letta: one archival namespace per scope (extend `memory_tree.yaml`
  `archival_namespaces` + generalize `global_memory.py` sync). Letta blocks/archival are DERIVED;
  files are source of truth.
- **In-scope = ambient. Out-of-scope = intent**: `recall(scope, query)` = Letta archival semantic
  search over that scope's namespace (real vector search, the "real engine").

### Sharing (reuse sensitivity + grants; no new permission system)
- Add capabilities `knowledge.read` / `knowledge.write` to the existing vocabulary.
- `private` = none, `shareable` = select (needs a grants.json row `{from,to,capability,mode}`),
  `public` = all. Projects need a sensitivity field (projects.json already has one).
- This gates what one scope auto-pulls from another for context hygiene + token budget, NOT a security
  wall (operator stays default-permissive in permissions.py). Framing keeps it out of defensive-cruft
  territory. grants.json (currently empty) finally gets a real tenant.

### Notes-app-as-plugin
- Define one small `knowledge` interface (provider contract like ContextProvider):
  `list(scope) / search(scope,query) / get(id) / put(id,content)`. Default impl = the markdown dirs +
  Letta. A notes app (or Obsidian-vault adapter) implements the same interface over its own storage and
  registers as the scope's `knowledge.provider` in its app manifest. Chat never knows the difference.
- The line: **knowledge** = anything exposed through this interface (injectable/recallable, governed by
  knowledge.read/write). **App data** = everything else behind the app's own API (existing capability
  grants). An app exposes a slice of its data as knowledge by implementing the interface over it.

### Context management engine (provider-aware, cache-first)
- **Builders: don't manage context, manage files.** Claude Code already caches/compacts internally;
  harness rewriting fights the CLI + Anthropic caching. Rule: durable stuff in files, never write
  volatile state (timestamps) into PROJECT.md/AGENTS.md (invalidates the cached prefix).
- **Assistants (data mode, we own the prompt): obey the prefix rule.** Two current cache-hostile bugs:
  1. `live_context` sits mid-prompt and changes every turn -> nukes persona + everything after, every
     turn. Fix: stable-first layout (role preamble -> persona -> capability_note -> knowledge index,
     cache_control breakpoint here -> transcript -> LIVE CONTEXT as part of the final user turn).
  2. `MAX_HISTORY=24` sliding window shifts the prefix every turn. Replace with append-until-threshold
     (~60-70% of tier budget), then ONE compaction pass (summarize oldest half, keep recent verbatim).
     One invalidation per compaction vs one per turn. Summarizer runs on the cheap llm_router tier.
  3. Compaction also **distills durable facts into the scope knowledge file**, deduped against the
     index. This is the gradual "roll things out" behavior; Mem0-style write-path as one prompt.
- Rejected: per-turn summarization, moving chat into Letta archival, vector store for chat history.
- Anthropic-path accelerators if a call hits a supported model directly: server-side compaction beta
  (`compact_20260112`), context editing (`clear_tool_uses_20250919`), memory tool (`memory_20250818`).
  Client-side design must not depend on them (Ollama tier exists).

## Orchestration (scales down to a plain chat)

- L0 plain chat (agentless thread). L1 agent thread. L2 fan-out INSIDE a builder (Claude Code native
  subagents, free). L3 **conductor = per-project Ralph loop** (`ralph.sh` + `cron_agent.sh` with the
  hardcoded root TODO.md replaced by a scope param; fixes the known gap where project briefs never
  reach the autonomous loop). L4 swarm/graph DEFERRED (no payoff for one operator; nothing forecloses
  it).
- **Work queue = TODO.md per scope.** Conductor iterates projects.json entries with autonomy enabled.
- **Skills = SKILL.md standard.** `shared/skills/` + per-scope `skills/`. Builders: link scope skill
  dirs into cwd `.claude/skills/` (native discovery, free). Assistants: skill loader (descriptions in
  stable prefix + `use_skill(name)` action, ~50 lines in chat_context). Governed by scope + grants.
- A2A agent cards: skip until agents are exposed externally. MCP stays the tool surface.

## Modalities

- A modality is a per-thread option, orthogonal to the agent. Voice already proves it (voice.py never
  touches the model/threads; useVoiceSession calls the same data-mode /messages path).
- **Voice small step (no big build):** promote `/voice/:key` to a mic toggle in the chat toolbar that
  mounts the same VoiceSession in place, gated by `capability_for` to data-mode keys. Builder threads
  show the toggle disabled (plain tooltip). Builder voice (streaming TTS over SSE + barge-in) = a real
  project, PARKED.

## Project builder overhaul

- **Kill hardcoded placeholders** ("Noetic ..."). Title placeholder generic; Purpose placeholder an
  instruction not an example. Optional debounced cheap-tier "suggest a purpose line" after title.
- **One template source of truth:** backend `projects/_templates/<slug>/template.json` manifests;
  frontend renders `GET /api/projects/templates` and nothing hardcoded (delete the 7 frontend slugs,
  4 are dead). Stop overwriting the template PROJECT.md when a brief is given -> merge under `## Brief`.
- **Brief -> autonomous loop:** brief in PROJECT.md; optional on-create cheap-tier pass expands it into
  an editable project TODO.md (the queue); per-project `autonomy` flag drives generalized Ralph;
  Autonomy panel on project detail.
- **Create-from-chat = one use of the general in-chat UI primitive** (generalize plan-patch): model
  emits a `project-proposal` block, frontend renders an EDITABLE in-chat card with a Create button.
  Creation only on the user's click. Ship as the first SKILL.md skill.
- **General in-chat UI primitives (the real Phase 1 target):** editable cards / buttons / form blocks
  any chat can emit + render, not project-specific. Project creation is one application.
- **Build-with-chat mode:** a general chat pre-loaded with project-shaping skills/info where you
  develop the idea before committing; scope = a not-yet-created project; skills addable to any chat.
- Git import stays as-is (state `mounted`). Clone-existing / CLI creation cheap later once templates
  are data-driven.

## Phased plan

- **Phase 1 (BUILT + verified 2026-07-11, commit 37f1e39 on branch agent-chat-phase-1):** general interactive in-chat UI primitive layer; apply it to project
  creation as an editable card; build-with-chat general-blank-project mode; builder page cleanup
  (placeholders, template unification, copy fix); re-key conversion as a small add-on. Files:
  chat_context.py, ProjectNew.tsx, routes/projects.py, ChatPanel/Chat message renderer,
  projects/_templates/*.
- **Phase 2:** knowledge scopes + notes baseline + Letta-per-scope sync + `recall` + knowledge
  interface + knowledge.read/write grants. (What the future notes app plugs into.)
- **Phase 3 (BUILT + verified 2026-07-11, commit 13dbb34):** context engine for data-mode assistants
  in routes/chat.py. Cache-stable ordering (LIVE CONTEXT moved to the volatile tail), threshold
  compaction (append-only + fold oldest half into a running summary on the cheap tier, raw messages
  retained), and distillation (durable facts from folded turns merged into the scope `chat-distilled`
  note, recallable). Build mode untouched. Threshold: _COMPACT_TOKEN_THRESHOLD=6000 tokens of
  uncompacted tail. Distillation is ON by default; to quiet it, gate _distill or skip it per scope.
- **Phase 4 (BUILT + verified 2026-07-12):** per-project autonomy (generalized Ralph). `scripts/ralph.sh`
  gained `--dir <tree>` (default = workshop root) and scopes the reviewer to that tree via
  `REVIEWER_ROOT`; `scripts/reviewer.sh` inspects `REVIEWER_ROOT` (defaults to workshop) so it gates a
  PROJECT's own git repo. New `scripts/project_ralph.sh <slug>` = the per-project conductor: resolves the
  project dir + `autonomy.mode` from the registry, drives each open task (through the structured store via
  `scripts/project_tasks.py`, so `.tasks.json`/`TODO.md` never drift) with cwd INSIDE the project tree, so
  PROJECT.md/AGENTS.md/TODO.md finally reach the loop. Registry gains a per-project `autonomy` object
  (`mode` = off|manual|continuous). Backend `routes/projects.py`: GET/PUT `/{slug}/autonomy`, POST
  `/{slug}/autonomy/run` (manual runs pass `--force` to bypass the global cron off-switch; `off` still
  refuses; 409 if a sweep is already running), GET `/{slug}/autonomy/log`. Frontend `AutonomyPanel.tsx` on
  the project detail page (mode segmented, Run now, live status pill, last-sweep summary, sweep log modal).
  Verified end to end: a real sweep created a file per its task, the project-scoped reviewer passed, and the
  task was marked done in the store.
- **Phase 5 (BUILT + verified 2026-07-12):** skills (SKILL.md standard). `services/skills.py` = the store:
  per-scope `skills/` dirs (shared/skills, projects/<slug>/skills, apps/<name>/skills,
  .runtime/agents/<id>/skills) mirroring the knowledge scopes; `parse_skill_md` (frontmatter name/description
  + body, no YAML dep); `list/get/put/delete`; `available_for_scope` = own scope + workshop baseline
  (own wins on name collision); `descriptions_block` for the prompt prefix; `link_into_cwd` symlinks in-scope
  skills into `<cwd>/.claude/skills/<name>` (idempotent, never clobbers a hand-placed skill). Builder
  auto-discovery: `_link_build_skills(provider)` runs before every build turn (both the non-streaming and
  SSE paths in routes/chat.py) so Claude Code finds them natively (free). Assistant loader: data-mode prompts
  get a `=== SKILLS (available) ===` block + `USE_SKILL_PROTOCOL` in the cache-stable prefix (build mode is
  skipped); a `use_skill` fenced block triggers a single bounded expansion (`_expand_use_skill_sync` feeds
  the body back for one more model turn). API `routes/skills.py` (operator-gated, /api/skills): GET
  /{scope}/skills, GET /{scope}/available, GET/PUT/DELETE /{scope}/skills/{name}. Seeded a real workshop
  skill: `shared/skills/frontend-design-system`. Verified: unit tests (store + loader + non-clobber linking),
  full API round-trip, and a LIVE end-to-end chat turn where the assistant loaded the skill and its reply
  reflected the skill body (primitives / no-hex / edit-mode / the exact check commands).
- **Phase 6 (SATISFIED 2026-07-12 by the parallel voice work — supersedes this spec):** voice as a
  per-thread modality toggle. This is DONE and shipped, more completely than the narrower plan below.
  The shared chat composer (`frontend/src/components/Chat.tsx`, rendered by every chat surface:
  ChatDock / ChatPanel / BuildChatPanel / FocusedChat / AgentBuild) carries a single `AudioLines`
  "Voice mode" toggle that opens `/voice/:contextKey?thread=<id>` (VoiceMode + useVoiceSession, the same
  data-mode /messages path). It is the sole voice entry point (no duplicates), present on ALL chats.
  Divergence from the original spec, and it is the RIGHT call — keep it: (1) the toggle NAVIGATES to a
  full-screen voice surface rather than mounting in place, and (2) voice is enabled for BUILD chats too
  ("voice-driven building", commit 9fcd57a) instead of being disabled/parked. The plan's "mount in place,
  data-mode only, builder parked" is therefore obsolete. Verified 2026-07-12: the toggle renders + is
  visible in the composer across surfaces. Do NOT build a second/competing toggle — that would duplicate a
  live, actively-maintained system. Original (now-obsolete) spec: "promote /voice/:key to a mic toggle in
  the chat toolbar that mounts VoiceSession in place, gated to data-mode keys; builder voice parked."

Ordering: 1 delivers the motivating UX; 2+3 build the knowledge/context core the notes app needs
before it is written; 4 pays down the Ralph gap; 5+6 additive, can interleave.

## Phase 2 feasibility notes (Letta capability probe, 2026-07-11)

Verified against the running Letta server (v0.16.8) before building Phase 2:
- **Archives + passage search ARE supported.** `GET /v1/archives/` -> 200 (org-level archives are a
  first-class, attachable resource). `GET /v1/passages/search` -> 405 (path exists, wants POST), so
  the semantic-search surface exists. Per-scope archives + `recall` via search is therefore feasible.
- **Use the REST API, not the SDK.** `from letta_client import Letta` resolves to the LOCAL
  `backend/letta_client.py` (a httpx wrapper with no `Letta`), which shadows the installed
  `letta-client` 1.11.0 SDK on sys.path. The whole backend already talks to Letta over raw httpx
  `/v1` (global_memory.py, supervisor.py, letta_legacy_client.py); Phase 2 should do the same for
  archives/passages and NOT depend on the shadowed SDK. (Optionally rename the local module later to
  unshadow the SDK, but that is a separate cleanup, not a Phase 2 dependency.)
- **RESOLVED (2026-07-11):** the search-timeout was ONLY org-wide search (no archive_id), which our
  design never uses. Scope-scoped search (`archive_id` set) returns in ~2s including local embedding.
  The other gotcha: an archive MUST be created WITH an `embedding_config` or its passages are never
  embedded and search returns []. The server embeds via a LOCAL model (Ollama `nomic-embed-text`,
  768-dim) reachable from the Letta container, so real vectors need no external API. Full contract in
  phase-2-spec.md; write->embed->semantic-search loop verified end to end.
- Today: archival memory is per-agent, write/read only, NO search wired; memory-tree.yaml
  `archival_namespaces` are cosmetic labels with no Letta backing. Phase 2 gives them real backing
  (one archive per scope) and adds the search/recall path.
