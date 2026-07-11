# Agent / chat / knowledge / project-builder redesign

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

- **Phase 1 (start here):** general interactive in-chat UI primitive layer; apply it to project
  creation as an editable card; build-with-chat general-blank-project mode; builder page cleanup
  (placeholders, template unification, copy fix); re-key conversion as a small add-on. Files:
  chat_context.py, ProjectNew.tsx, routes/projects.py, ChatPanel/Chat message renderer,
  projects/_templates/*.
- **Phase 2:** knowledge scopes + notes baseline + Letta-per-scope sync + `recall` + knowledge
  interface + knowledge.read/write grants. (What the future notes app plugs into.)
- **Phase 3:** context engine for assistants (cache reorder + threshold compaction + distillation).
  Depends on Phase 2.
- **Phase 4:** per-project autonomy (generalized Ralph). Independent, can follow Phase 1.
- **Phase 5:** skills (SKILL.md dirs, builder auto-discovery, assistant loader).
- **Phase 6:** voice modality toggle. Builder voice parked.

Ordering: 1 delivers the motivating UX; 2+3 build the knowledge/context core the notes app needs
before it is written; 4 pays down the Ralph gap; 5+6 additive, can interleave.
