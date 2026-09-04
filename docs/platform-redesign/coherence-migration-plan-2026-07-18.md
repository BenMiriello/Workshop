# Coherence migration plan (2026-07-18)

- Status: accepted
- Author: coherence auditor (spawned by operator, 2026-07-18)
- Supersedes: nothing (folds into existing plans)
- Owns: the port-or-remove sweep for the pre-2026-07-16 model surface

This doc is not a new redesign. It is the concrete migration between the
2026-06-22 model that is still shipped on the frontend and the 2026-07-18 model
that is authoritative in `entity-model-2026-07-18.md`, `autonomy-model-2026-07-18.md`,
`latitude-model-2026-07-18.md`, and `primitive-roles-2026-07-18.md`. Every unit
below is folded into an existing plan (`final-plan-agent-generalize-and-bridge-fold-2026-07-16.md`
phases P0-P4 / PM, or one of the 2026-07-18 model docs). Nothing here is orphan.

---

## 1. Executive summary

- The backend has largely landed the design. Role dispatch table, closed role
  enum, latitude, widened `awaiting`, Review tab data path, mediated broker,
  triager runner, templates + fork, `touches` enforcement, and PM the bridge agent
  build-only fold are all shipped (commits `58e8631` through `63a7e65`).
- The frontend has NOT been migrated. The `/agents` page, agent create flow,
  agent detail page, and Chat toolbar still model an agent as
  `runtime: builder | assistant` (Agents.tsx:492-497, AgentDetail.tsx:223-226,
  App.tsx:118, Chats.tsx:53). The operator complaint is verified.
- Autonomy is doubly wrong. The doc calls for per-agent `reactive/supervised/autonomous`
  with a JSON overlay at `.runtime/autonomy-override.json` (autonomy-model §1.1,
  §1.2). What ships is per-project preset `off/on-demand/scheduled/continuous`
  (AutonomyPanel.tsx:36) and a boolean flag file at `.runtime/AUTONOMY_DISABLED`
  (dispatch_triggers.sh:28, backend/routes/autonomy.py:37) toggled from THREE
  duplicate places (Agents.tsx:510-520, Work.tsx:674-753, AutonomyControls.tsx:206).
- Chat is not a first-class entity yet. `taskScopeFor(contextKey)`
  (Chat.tsx:73-83) infers scope from the contextKey PREFIX; there is no stored
  Chat with `scope`/`scope_locked`, no `chat_states` sidecar, no per-thread
  `mode: orchestrated|direct`. All four chat-work-linkage bugs are unfixed
  (card duplication, per-kind auto-run, in-chat related-work, event ping).
- Legacy agent defs on disk that should be removed or ported:
  `workshop-builder.md`, `workshop-builder-2.md`, `blank-project-1.md`,
  `blank-project-2.md`, `probe-free.md`, `probe-git.md`, `smoke-fix.md`,
  `test1234.md`, `test2.md`. Ghost agents `plaid-watcher.md` and
  `email-triager.md` still route to `_dispatch_assistant_loop` which prints
  `skip-assistant` (dispatch_triggers.sh:493-495): they subscribe to events but
  no runner exists, so they cannot execute.
- The bridge agent fold is intentionally half-done: build ported, live surface not
  flipped. `the-bridge.service` is still `active (running)` (systemctl --user)
  out of `<bridge-checkout>/` (dir present with `run.sh`, `plaid-mcp/`, `CLAUDE.md`).
  `agents/the bridge agent.md` is INERT by design (self-declared, line 27-32 of the def).
  This is correct per memory `project_agent_protocols_2026_07_16`. Not a bug;
  called out so it does not read as one.
- `docs/workshop-v1/00-overview.md` still describes the shipped-but-legacy
  model (assistant vs builder, project workbench). It disagrees silently with
  the 2026-07-18 series. Needs a supersession banner and the entity-model doc
  linked as the current authority.
- `letta_tools/__init__.py:38-49` binds ten tools to `workshop-overseer` which
  is a ghost: the def file was already removed (`ls agents/` shows none), but
  the tool registry still tries to attach at startup. Dead binding.

---

## 2. Verified ground truth

Every claim below is anchored to file:line as of `HEAD` at commit `1e80ed0`.
Read, not assumed.

### 2.1 Backend has migrated (mostly)

| Concern | Evidence | Verdict |
| --- | --- | --- |
| Closed role enum + validation | `agents_registry.py:118` `ROLES`, `:159` `_validate_role`, `:118` ROLE_ALIASES `memory -> keeper` | D2 FIXED |
| Role dispatch table | `dispatch_triggers.sh:398-406` `ROLE_HANDLERS`, `:499-506` fallback path with `skip-no-triggers` | D1 FIXED |
| `touches` enforcement | `tasks.py:536-580` `_check_touches`/`enforce_touches`; `:481` `lane_gaps` widening | D3 FIXED |
| `attenuate()` wired | `dispatch_grants.py:96` in dispatch grants; `broker.py:349` in mediated verdicts | D4 PARTIALLY FIXED (entity-model doc note at §8.5 D4 is stale) |
| Latitude field | `agents_registry.py:106-142` LATITUDES + coerce + validate; `tasks.py:34` field | SHIPPED (E1) |
| Widened `awaiting` | `tasks.py:346-380` `_normalize_awaiting`; six new fields | SHIPPED (E3) |
| Review tab data path | `backend/routes/tasks_all.py:488-520` `/review` endpoint with five verbs | SHIPPED (E4 data) |
| Triager runner + templates | `agents/_templates/{note-triager,inbox-watcher,memory-keeper}.md`; `dispatch_triggers.sh:461-475` `_dispatch_triager` | SHIPPED (P4/R2/R3) |
| Fork-not-link templates | `agents_registry.py:739-779` `fork_from_template`; template stamp `template: <slug>@<version>` | SHIPPED (P4) |
| Singleton roles | `agents_registry.py:85` `SINGLETON_ROLES = ("triager","keeper")`; `:510` `_assert_role_unique` | SHIPPED |
| PM.1/PM.2/PM.3/PM.4 bridge fold (build only) | commits `63a7e65`, `ed9c203`, `ad47ca0`; `agents/the bridge agent.md` present INERT; `backend/mcp/plaid/` in-tree | SHIPPED |

### 2.2 Frontend has NOT migrated

| Surface | Evidence | State |
| --- | --- | --- |
| Agent axis is still `runtime: builder|assistant` | `Agents.tsx:492-497,534,552`; `AgentDetail.tsx:223-226,606-622,656-665,790,830`; `Agents.tsx:82-88,90-208` "New builder" modal | UNMIGRATED |
| "Builders/Assistants" split as primary UI | `Agents.tsx:492-521`; `Chats.tsx:53-55` filter | UNMIGRATED |
| `/agents/new` create-runtime picker | `AgentDetail.tsx:223-320` `CreateRuntime = "assistant"|"builder"` | UNMIGRATED |
| Per-agent preset autonomy control | `AutonomyPanel.tsx:36,102-107` `Preset = "off"|"on-demand"|"scheduled"|"continuous"` | CONFLICTING with autonomy-model §1.1 (`reactive/supervised/autonomous`) |
| Global-toggle duplication (three places) | `Agents.tsx:510-520`; `Work.tsx:674-753` `AutonomyTab`; `AutonomyControls.tsx:206` | UNMIGRATED |
| Global-toggle mechanism | `.runtime/AUTONOMY_DISABLED` flag file at `backend/routes/autonomy.py:37`, `scripts/dispatch_triggers.sh:28`, `scripts/project_ralph.sh:33`, `scripts/queue_runner.sh:61` | CONFLICTING with autonomy-model §1.2 (`.runtime/autonomy-override.json` with `{engaged, reason, engaged_at, engaged_by}`) |
| Card-duplication bug (chat-work-linkage §2.1) | `Chat.tsx:1102-1211` toolbar area drives spawn+run; no `card_states` sidecar | UNFIXED |
| No per-thread mode | `chat_store.py` has `set_thread_model` but no `set_thread_mode` | UNFIXED (work-placement doc §"Modes") |
| No Chat entity with scope_locked | `Chat.tsx:73-83` `taskScopeFor` infers from prefix; no stored scope | UNFIXED (chat-scope-design §2.1) |
| No clarifying-question card kind | `grep -rn "clarifying-question" backend/ frontend/src/` returns nothing | UNFIXED (clarifying-question-flow doc) |
| Chat toolbar redesign | `Chat.tsx:1102-1211`; provider select + thread select still stacked with no separator | UNFIXED (chat-toolbar-audit-redesign doc) |
| Job/run dual status | `JobDetail.tsx` renders both `job.status` and `job.task_status` (per run-status-view §2.1) | UNFIXED (run-status-view doc) |
| Rescope UI (backend supports POST /threads/{id}/rescope) | no calls from any component under `frontend/src/` (grep confirms) | UNFIXED |

### 2.3 Agent definitions on disk

`ls -la agents/` output:

- `blank-project-1.md`, `blank-project-2.md` — 344 bytes each, test scaffolds. DELETE.
- `probe-free.md`, `probe-git.md`, `smoke-fix.md`, `test1234.md`, `test2.md` — probe/smoke test defs. DELETE.
- `workshop-builder.md`, `workshop-builder-2.md` — `runtime: builder`, `triggers: []`, purpose empty, body carries a STALE 2026-06-22 project plan ("Agents & Chat overhaul") with a no-longer-accurate decision list. Never dispatched (`skip-no-triggers`). RETIRE (rename purpose or delete; a live legacy overhaul plan misleads readers).
- `plaid-watcher.md` — `runtime: assistant`, `triggers: [loop]`, no `role`. `_handler_for` (dispatch_triggers.sh:508-516) returns `_dispatch_assistant_loop` which prints `skip-assistant`. Ghost. Either add `role: triager` (matching the `inbox-watcher` template shape) or retire.
- `email-triager.md` — `runtime: assistant`, `triggers: [event, gmail]`, no `role`. Event fires but there is no assistant loop runner, so the wakeup is a no-op. Same choice: promote to `role: triager` or retire.
- `the bridge agent.md` — `role: triager`, `trigger: discord`, empty channels. Self-declared INERT (lines 24-31). Live surface is `the-bridge.service` (verified: `Active: active (running)` since 2026-07-14). NO ACTION. This is the intended intermediate state per PM plan.
- `the bridge agent-commute.md` — trigger `manual`, self-declared INERT. NO ACTION.
- `memory-keeper.md` — `role: memory` (alias `keeper`), cron `0 */6 * * *`. Works.
- `work-reviewer.md` — subagent-format def (Claude Code subagent, has `description` + `tools: ['*']`). Not a registry agent. Zero call sites per system-audit-2026-07-18 headline. Move to `~/.claude/agents/` (subagent spec) OR delete; keeping it in `agents/` reads as a registry member it is not.
- `agents/_templates/{note-triager,inbox-watcher,memory-keeper}.md` + `groups/{minimal,tended}.json` — correct location, correct shape. NO ACTION.

### 2.4 Scripts + dispatch

- `dispatch_triggers.sh:398-406` `ROLE_HANDLERS = {keeper, triager, reviewer, maker}` — coherent with primitive-roles §3.
- `dispatch_triggers.sh:493-495` `_dispatch_assistant_loop` prints `skip-assistant` — deliberate, the "still-unbuilt data-tier turn runner" per its own comment. This is correct until an assistant runtime is genuinely built.
- `dispatch_triggers.sh:499-506` `_handler_for` fallback: `builder -> maker`, `assistant -> assistant_loop`. So an agent's `role` is the primary axis; runtime is fallback.
- No `scripts/roles/` directory (`ls scripts/roles/` errors). Roles are dispatched inline in `dispatch_triggers.sh` handlers, not via the per-role runners in primitive-roles §3.5. That is a documentation-vs-code mismatch, not a bug: the handlers ARE the runners. Update primitive-roles doc §3.5 to point at `dispatch_triggers.sh` handlers, or extract them into `scripts/roles/*_run.sh` for readability. Low-priority.

### 2.5 Docs mismatch

- `docs/workshop-v1/00-overview.md` opens with "This is the master plan for Workshop v1" and describes "agent roles (assistant vs builder)". This is the legacy model. It needs a supersession banner pointing at `entity-model-2026-07-18.md`.
- Root `CLAUDE.md` line under `## Platform design (v1)` still names `docs/workshop-v1/` as the authoritative platform doc set. Needs to be reworded to name `docs/platform-redesign/entity-model-2026-07-18.md` as the current authority with `workshop-v1/` as historical.
- `docs/agent-chat-redesign/plan.md` is a 2026-07-12 redesign predating this series. Should carry a `Superseded by: docs/platform-redesign/entity-model-2026-07-18.md` header.
- `letta_tools/__init__.py:38-49` binds ten tools to `workshop-overseer`. No `workshop-overseer.md` exists in `agents/`. Dead bindings.

### 2.6 Concurrency implication (from system-audit-2026-07-18)

The system-audit doc identifies four serialization points making concurrent execution impossible today. The autonomy-model requires per-agent autonomy, but if the queue can only run one worker at a time, per-agent autonomy is a boolean-per-worker under a bottleneck. Flag: unless concurrency is unblocked, the frontend's per-agent autonomy control will misinform the operator ("this agent runs when it decides to" is true only for the currently-holding-the-lock agent). Slot as an implication of the migration, not part of it.

---

## 3. The coherence gap (subsystem sweep)

| Subsystem | Current shipped state | Target per 2026-07-18 docs | Gap |
| --- | --- | --- | --- |
| `/agents` page | Builders/Assistants split; runtime tag on every card; "New builder" modal alongside "New agent" | One list of Agents keyed by `role`; runtime is metadata; one create flow | CONFLICTING |
| `/agents/new` | Segmented `Runtime: assistant|builder` picker (AgentDetail.tsx:225-226) | Pick role + scope; runtime is derived from role (maker+project_slug -> builder handler) | CONFLICTING |
| `/agents/:slug` (detail) | `isBuilder ? "Instructions" : "Persona"`; runtime tag; `/agents/:slug/chat` route for builders | Role + latitude + autonomy + grants + linked chats/tickets/runs; ONE definition surface | CONFLICTING |
| `/agents/:slug/chat` (build workbench) | 3-pane IDE with files/editor/preview/diff (AgentBuild.tsx) | Preserve as a `role: maker` runtime UI, but stop being the ONLY affordance for the concept | PARTIAL (preserve but demote) |
| Chat.tsx | contextKey-prefix scope inference (line 73-83); no per-thread `mode`; no `card_states` sidecar; card duplication on File-and-run | Stored Chat with `scope` + `scope_locked`; per-thread `mode: orchestrated|direct`; `card_states` sidecar; File-and-run collapses card in place | MISSING |
| Chat toolbar | Provider + thread selects rendered flush with no separator; File-ticket button labeled by icon alone; List/Play button pair reads as a media player | Redesign per chat-toolbar-audit-redesign; consolidate to labeled controls | MISSING |
| AutonomyPanel | Preset `off/on-demand/scheduled/continuous` per project or per agent | `reactive/supervised/autonomous` per agent; project scope holds no autonomy (project is a scope, not an agent); global override reads `.runtime/autonomy-override.json` | CONFLICTING |
| Global autonomy toggle | Duplicated in Agents.tsx:510-520 + Work.tsx:674-753 + AutonomyControls.tsx:206; writes `.runtime/AUTONOMY_DISABLED` flag file | ONE surface at workshop scope (Settings); JSON overlay at `.runtime/autonomy-override.json`; visually distinct from per-agent | CONFLICTING |
| TaskPanel | Consumes widened `awaiting` object (TaskPanel.tsx:104 reads `t.latitude`, TaskPanel.tsx:106-108 acceptance kind/spec/max_attempts, TaskPanel.tsx:109 depends_on) | Same, plus surface the six operator response verbs in the block card | PARTIAL (missing verbs UI in TaskPanel; Work.tsx Review tab has them) |
| Work.tsx | Tickets / Runs / Review / Autonomy tabs (Work.tsx:59) | Same tabs; Autonomy tab moves its content to Settings > Autonomy per autonomy-model §5; Work stays Tickets/Runs/Review | STRUCTURAL DRIFT |
| JobDetail.tsx | Dual status (`job.status` + `job.task_status`); "Evidence" raw JSON dump | Single canonical status; kill Evidence; add "edit in chat" + "new chat from run" actions; live activity band while running | MISSING |
| Chats.tsx | Segmented filter with "Builders / Assistants" as first-class kinds | Filter by scope kind (workshop / project / app / agent); role, not runtime | CONFLICTING |
| `agents/` on disk | 8 legacy/probe/blank defs; 2 ghosts (plaid-watcher, email-triager); workshop-builder-{1,2} carry stale plan bodies | Prune legacy; either template-adopt or delete ghosts; retire workshop-builder-{1,2} | STALE |
| letta_tools bindings | Ten tools bound to nonexistent `workshop-overseer` | Rebind to `the bridge agent` (triager) or delete | STALE |
| The bridge agent live surface | `the-bridge.service` runs out of `<bridge-checkout>/`; `agents/the bridge agent.md` INERT | Deliberate intermediate state; flip is Phase PM.5 (out of scope here) | PRESERVE |
| `docs/workshop-v1/` | Master plan header, describes builder/assistant | Supersession banner pointing at entity-model | STALE |
| CLAUDE.md (root) | Points at `docs/workshop-v1/` as master | Point at `docs/platform-redesign/entity-model-2026-07-18.md`; keep workshop-v1 as historical | STALE |
| Concurrency | Single-threaded queue (system-audit) | Per-agent autonomy implies concurrent scopes | BLOCKS full per-agent value; note; not this plan's fix |

---

## 4. Migration units

Each unit is scoped to be independently mergeable. `Files:` names every file
that changes. `Depends on:` lists prior units. `Folds into:` cites the plan
this belongs to.

### C1. Retire legacy agent defs and ghost bindings

- **Files**: `agents/blank-project-1.md`, `agents/blank-project-2.md`, `agents/probe-free.md`, `agents/probe-git.md`, `agents/smoke-fix.md`, `agents/test1234.md`, `agents/test2.md` (delete). `agents/workshop-builder.md`, `agents/workshop-builder-2.md` (delete OR replace body with a placeholder that names the current plan; body currently reads as the master 2026-06-22 overhaul plan). `agents/work-reviewer.md` (move to `~/.claude/agents/` if the subagent is still in use; otherwise delete). `backend/services/letta_tools/__init__.py:38-49` (drop `workshop-overseer` bindings; if the bridge agent gets the tools instead, rebind to `the bridge agent`).
- **Change**: pure deletion / demotion. Zero behavior change (all targets have `triggers: []` or reference a nonexistent agent).
- **Risk**: none. Verify by grep that no code references the retired slugs.
- **Depends on**: none.
- **Folds into**: `primitive-roles-2026-07-18.md` §4 (retire workshop-overseer) and §2 "the residue after that reduction."

### C2. Promote or retire plaid-watcher and email-triager

- **Files**: `agents/plaid-watcher.md`, `agents/email-triager.md`.
- **Change**: pick one for each: (a) add `role: triager` and rewire the definition body to match the `inbox-watcher` template shape (this makes them a real triager over a feed; primitive-roles §6.3.2), or (b) delete.
- **Risk**: they are already inert; either choice is safe.
- **Depends on**: none.
- **Folds into**: `primitive-roles-2026-07-18.md` §6.3 (template groups; inbox-watcher).

### C3. Introduce autonomy override at .runtime/autonomy-override.json

- **Files**: `backend/services/autonomy_override.py` (new; single file, ~40 lines). `backend/routes/autonomy.py` (add read/write endpoints backed by the new module). `scripts/dispatch_triggers.sh` (line 28 read the JSON overlay; keep the AUTONOMY_DISABLED flag as a compat second-check with a scripted-usage comment). `scripts/project_ralph.sh:33` (same). `scripts/queue_runner.sh:61` (same).
- **Change**: implement the schema from autonomy-model §1.2 verbatim. `engaged: true` short-circuits dispatch. File absent = clear. Fail-closed on corrupt JSON (autonomy-model §6 "Override file is corrupt").
- **Risk**: low. The overlay is a new file; keeping the flag file readable for one release preserves compat while scripts are migrated.
- **Depends on**: none.
- **Folds into**: `autonomy-model-2026-07-18.md` §1.2, §2, §4, §8.

### C4. Add per-agent autonomy field (reactive/supervised/autonomous)

- **Files**: `backend/services/agents_registry.py` (add `AUTONOMY = ("reactive","supervised","autonomous")`, `DEFAULT_AUTONOMY = "reactive"`, `_coerce_autonomy`, `_validate_autonomy`; add `autonomy` to `_record_from_def`, `_build_fm`, `create`, `update`; emit only when non-default per the existing convention). `backend/routes/agents.py` (surface field on read/write; validate 400 on unknown value). `backend/services/triggers.py` (add `effective_autonomy(agent_slug)` per autonomy-model §4).
- **Change**: additive backend field; no dispatch behavior change yet. Reactive is the default so existing agents continue to run per their triggers via C5, not their new `autonomy`.
- **Risk**: low. Reading a definition without the field returns the default.
- **Depends on**: none (parallel with C3).
- **Folds into**: `entity-model-2026-07-18.md` §6.2 and `autonomy-model-2026-07-18.md` §1.1.

### C5. Wire per-agent autonomy into dispatch

- **Files**: `scripts/dispatch_triggers.sh` (around the `for a in agents:` loop at line 517-539): before firing, call `effective_autonomy(slug)`; if `reactive`, skip. Log `skip-reactive` for observability.
- **Change**: enforce autonomy at dispatch. Existing agents (default `reactive`) will not run in the background until the operator promotes them, matching the doc's opt-in default.
- **Risk**: medium — flips a default. Ship together with a migration note: the operator explicitly promotes agents they want running (which today are `the bridge agent`, `memory-keeper`, and the two triager templates once forked). Alternative safer path: default `supervised` for agents already carrying triggers, `reactive` for those with `triggers: []`.
- **Depends on**: C4.
- **Folds into**: `autonomy-model-2026-07-18.md` §4, §7.

### C6. Delete duplicate global autonomy toggles; keep ONE toggle + ONE status pill

- **Files**: `frontend/src/pages/Agents.tsx:489-524,601-606` (remove the at-a-glance strip's autonomy toggle and the trailing `<AutonomyControls />`; replace with a small READ-ONLY status pill on the /agents PageHeader per operator direction 2026-07-18). `frontend/src/pages/Work.tsx:59,63,673-753` (remove the Autonomy tab; Work is Tickets/Runs/Review only). `frontend/src/components/AutonomyControls.tsx` (retire this component OR move its single responsibility into Settings). `frontend/src/pages/Settings.tsx` (mount the one canonical override TOGGLE here; use the `.runtime/autonomy-override.json` endpoint from C3; visually distinct StatusPill).
- **Change**: two surfaces total. Settings owns the toggle. /agents header shows the state (view-only pill), so an operator on the roster sees at a glance whether the workshop is quiet or live without opening Settings.
- **Risk**: low. The scripts still respect the compat flag until C3 lands the JSON path.
- **Depends on**: C3.
- **Folds into**: `autonomy-model-2026-07-18.md` §5.

### C7. Rewrite AutonomyPanel around per-agent reactive/supervised/autonomous

- **Files**: `frontend/src/components/AutonomyPanel.tsx` (replace `Preset` type with the three-value autonomy enum; remove `off / on-demand / scheduled / continuous`; keep depth + latitude + limits + advanced triggers). `frontend/src/pages/ProjectDetail.tsx` (project page carries NO autonomy widget; project is a scope, not an agent per autonomy-model §1.1 cascade note). `frontend/src/pages/AgentDetail.tsx` (mount AutonomyPanel with `scope="agent"`; project scope loses this affordance). Backend `PUT /api/projects/{slug}/autonomy` deprecates; the project registry keeps only cron/limits for its own loop, not "autonomy" as a mode.
- **Change**: primary user-facing wording flips from "how a scope decides to work its queue" to "how this agent behaves when triggered".
- **Risk**: high — this is the visible operator complaint. Ship with a migration note listing the value mapping: `off -> reactive`; `on-demand -> reactive`; `scheduled -> supervised`; `continuous -> autonomous` (approximation only; operator will re-set explicitly).
- **Depends on**: C4, C6.
- **Folds into**: `autonomy-model-2026-07-18.md` §1.1, §5; `entity-model-2026-07-18.md` §6.2.

### C8. Rewire Agents.tsx around role and scope, not runtime

- **Files**: `frontend/src/pages/Agents.tsx` (drop `builders`/`assistants` split at line 492-495; render ONE roster grouped by `role`, empty role = "no role"; drop "New builder" modal at line 90-208; keep "New agent" and "From template"). `frontend/src/lib/agentTriggers.ts:17` (`AgentRuntime` type stays as backend metadata but stops being the display axis). `frontend/src/lib/chat.ts:61` (`ProviderRole` type: rename to something honest OR keep for provider taxonomy).
- **Change**: primary display axis flips from runtime to role. Cards show `role`, `latitude`, `autonomy` badges (already partially there at Agents.tsx:555-570). Roster action changes from "New assistant / New builder" to "New agent" + optional "For project" chip.
- **Risk**: medium. The route `/agents/:slug/chat` continues to work for maker-role agents linked to a project (that IS the workbench). The migration is: keep the workbench, stop making it the raison d'etre.
- **Depends on**: C4 (`role` and `autonomy` are surfaced).
- **Folds into**: `entity-model-2026-07-18.md` §6.1 (role enum), §6.3 (zero-agent default); operator ask A4 and D2.

### C9. Rewrite /agents/new to pick role, not runtime

- **Files**: `frontend/src/pages/AgentDetail.tsx:223-320` `CreateAgent` (replace `CreateRuntime` picker with role picker: `maker / reviewer / triager / keeper / broker / none`; add "For project" select; runtime is derived: role=maker + project_slug -> `builder` runtime; anything else -> `assistant` runtime; broker cannot be created interactively per primitive-roles §2 table).
- **Change**: the create form matches the entity model's Agent fields (§6.2).
- **Risk**: medium. Backend `POST /api/agents/` already accepts `role`; the frontend just stops offering the runtime toggle.
- **Depends on**: C4, C8.
- **Folds into**: `entity-model-2026-07-18.md` §6; operator ask A4.

### C10. AgentDetail: role + latitude + autonomy first, runtime as metadata

- **Files**: `frontend/src/pages/AgentDetail.tsx:606-830` (top header shows Role + Latitude + Autonomy chips; runtime moves to a small "runs as: builder|assistant" line under the fold). Section labels swap: "Definition" stays; "Instructions" vs "Persona" unifies to "Definition body" for both (the label difference tracked the runtime split). Add an "Involved in" section listing linked Chats, Tickets, Runs (per operator complaint verbatim: "being able to link/navigate to what chats and tasks and other things theyre involved in or doing"). Backend `GET /api/agents/{slug}` should return `linked_chat_ids`, `linked_ticket_ids`, `linked_run_ids` (denormalized from the task store + chat store).
- **Change**: satisfies the operator's stated shape ("defining roles and prompts and scope and capabilities"). Runtime demoted from headline to plumbing.
- **Risk**: medium. The linked-entities queries need scope-aware indexes; use existing `GET /api/tasks?scope=agent:<slug>` + `GET /api/jobs?agent_id=...`.
- **Depends on**: C4, C8, C9.
- **Folds into**: `entity-model-2026-07-18.md` §6, §7.3 (card linking).

### C11. Chat entity: store scope + scope_locked; add mode field

- **Files**: `backend/services/chat_store.py` (`Thread` gains `scope: str`, `scope_locked: bool`, `mode: str = "orchestrated"`; migrations back-fill `scope` from the existing `context_key` by lifting the prefix). `backend/services/chat_context.py:1004-1005` (feed `mode` into the tool allowlist choice per work-placement doc). `POST /api/chat/threads/{id}/rescope` already exists at `backend/scratch/test_phase1_api.py:122` reference; extend it to enforce the scope_locked warning path. Frontend `Chat.tsx` (add scope indicator to the toolbar; add re-scope confirmation dialog when the operator asks to change scope while runs are live).
- **Change**: closes chat-scope-design gaps 1, 3.
- **Risk**: medium. The store migration is additive; existing threads keep working with `scope_locked = false` and mode `orchestrated`.
- **Depends on**: none.
- **Folds into**: `chat-scope-design-2026-07-18.md` §2.

### C12. Card lifecycle: card_states sidecar (fix duplication)

- **Files**: `backend/services/chat_store.py` (per-thread `card_states.json` sidecar). `backend/routes/chat.py` (add `PATCH /chat/{contextKey}/thread/{threadId}/card-states/{cardId}`; stop appending the separate `job-status` message from the /run endpoint per chat-work-linkage §2.2). `frontend/src/components/chat/UiCard.tsx` (accept `initialCardState`; render tracking phase without re-offering File/File-and-run).
- **Change**: eliminates the operator-facing card duplication bug.
- **Risk**: low. Sidecar is additive; backward-compat is "no sidecar entry = old behavior".
- **Depends on**: C11 (uses the thread scope).
- **Folds into**: `chat-work-linkage-2026-07-18.md` §2.

### C13. Chat toolbar redesign

- **Files**: `frontend/src/components/Chat.tsx:1102-1211` toolbar block. Replace raw `<select>` with `ws-select` primitive; label the provider and thread selects; consolidate File-ticket + Run into labeled controls; remove the mystery-icon Play/List pair.
- **Change**: matches chat-toolbar-audit-redesign findings.
- **Risk**: low. Contains no data-model change.
- **Depends on**: none (parallel with C11/C12).
- **Folds into**: `chat-toolbar-audit-redesign-2026-07-18.md`.

### C14. JobDetail run-status view redesign

- **Files**: `frontend/src/pages/JobDetail.tsx` (kill the `task_status` tag; replace with a "linked ticket" navigation; add live activity band while running; drop the "Evidence" raw JSON section; add "Edit in original chat" and "New chat from run" actions when a chat_origin is known).
- **Change**: matches run-status-view §3.
- **Risk**: low.
- **Depends on**: C11 (chat_origin available on runs).
- **Folds into**: `run-status-view-2026-07-18.md`.

### C15. Clarifying-question card and Block card

- **Files**: `frontend/src/components/chat/UiCard.tsx` (new card kinds: `clarifying-question`, `block`). Backend chat_context ticket-maker emits card of kind `clarifying-question` when Phase 1 needs input. `backend/routes/chat.py` handler for the six operator response verbs (approve-once / grant-standing / amend-rule / rescope-task / reject / defer) routes to `POST /api/tasks/{scope}/{id}/review`.
- **Change**: closes clarifying-question-flow + blocked-and-escalate visibility inside chats.
- **Risk**: medium.
- **Depends on**: C11, C12.
- **Folds into**: `clarifying-question-flow-2026-07-18.md`, `blocked-and-escalate-flow-2026-07-18.md`.

### C16. TaskPanel: surface the six operator verbs on blocked tickets

- **Files**: `frontend/src/components/TaskPanel.tsx` (blocked rows show a verb selector instead of the current bare "awaiting" pill). Reuse the Work.tsx `/review` endpoint. Drawer shows the widened `awaiting` structure with `why`/`unblocks_if`/`proposal`.
- **Change**: makes the widened awaiting shape actually visible where the operator lives.
- **Risk**: low.
- **Depends on**: C15 (verb wiring exists on backend if not already).
- **Folds into**: `entity-model-2026-07-18.md` §4.3, `blocked-and-escalate-flow-2026-07-18.md` §3.

### C17. Chats.tsx: filter by scope kind, not builder/assistant

- **Files**: `frontend/src/pages/Chats.tsx:29,53-56` (`contextLabel` and filter). Replace `Builders/Assistants` with scope kinds: `Workshop / Project / App / Agent`. Keep runtime as metadata on the card, not a filter axis.
- **Change**: chat discovery matches the entity model.
- **Risk**: low.
- **Depends on**: C8.
- **Folds into**: `entity-model-2026-07-18.md` §7.1, `chat-scope-design-2026-07-18.md` §2.1.

### C18. Docs coherence: supersession banners + CLAUDE.md pointer

- **Files**: `docs/workshop-v1/00-overview.md` (add a banner: "Superseded on 2026-07-18 by docs/platform-redesign/entity-model-2026-07-18.md. Preserved for historical reference; do not update."). `docs/agent-chat-redesign/plan.md` (same). Root `CLAUDE.md` (rewrite the "Platform design (v1)" section header to name entity-model as the current authority; move workshop-v1 to "historical").
- **Change**: contributors and future sessions read the right doc first.
- **Risk**: none.
- **Depends on**: none.
- **Folds into**: `entity-model-2026-07-18.md` §9 (integration map).

### C19. Cleanup dead letta_tools bindings

- **Files**: `backend/services/letta_tools/__init__.py:37-51` (drop the `workshop-overseer` list from every entry; if `the bridge agent` should inherit `wkshp_*` tools, add it as the sole target for those; if not, delete the tools too since their only home was overseer).
- **Change**: removes bindings to a nonexistent agent.
- **Risk**: none (dead code today).
- **Depends on**: none.
- **Folds into**: `primitive-roles-2026-07-18.md` §4 (retire overseer).

### C20. Ticket dependency edges + cycle detection (visible in UI)

- **Files**: `backend/services/tasks.py` (already has `depends_on: list[str]`; add `relates_to: list[str]` field; derived `blocks` computed from inverse of depends_on; cycle detection on every mutation). `frontend/src/components/TaskPanel.tsx` (surface `depends_on` in edit; already partial at TaskPanel.tsx:109). `frontend/src/pages/Work.tsx` (in the ticket drawer, show blocking + depends_on visually).
- **Change**: matches entity-model §4.2, §4.4.
- **Risk**: low. Additive.
- **Depends on**: none.
- **Folds into**: `entity-model-2026-07-18.md` §4.4.

### C21. (implication, not a unit) Concurrency unblock

- **Files**: TBD via system-audit follow-through.
- **Change**: unlocks the semantics of per-agent autonomy under load.
- **Note**: not this plan's fix. The concurrency serialization is called out in `system-audit-2026-07-18.md` and should be its own phase. C4-C7 remain useful without it: per-agent autonomy is still the correct model even when only one runs at a time, because the operator's mental model no longer requires "the workshop is autonomous" as a single truth-value.

---

## 5. Sequencing

Two phases. Phase A restores coherence in the surface the operator hit; phase B
finishes the model.

**Phase A — user-facing coherence (unblock the operator complaint)**

1. C1 (retire legacy defs)
2. C19 (dead letta_tools)
3. C4 (per-agent autonomy field)
4. C3 (autonomy override JSON)
5. C6 (delete duplicate toggles; keep one on Settings)
6. C7 (rewrite AutonomyPanel)
7. C8 (Agents page around role + scope)
8. C9 (agent-new picks role)
9. C10 (AgentDetail role-first + involved-in)
10. C13 (chat toolbar redesign — visible next to Agents complaint)
11. C18 (docs banners)

Phase A shipped, the /agents page reads coherent with the docs and the "double
chat-name field" toolbar defect is gone. Autonomy is on Settings only, JSON
overlay engaged by default (leave off per operator's expressed preference).

**Phase B — deeper alignment**

12. C11 (Chat entity: scope + mode)
13. C12 (card_states sidecar; fix duplication)
14. C14 (JobDetail redesign)
15. C15 (clarifying-question + block cards)
16. C16 (TaskPanel surfaces verbs)
17. C17 (Chats filter by scope kind)
18. C20 (ticket edges visible)
19. C2 (ghost triagers: promote or delete)
20. C5 (enforce per-agent autonomy at dispatch)

C5 comes last on purpose: promoting the field to gate dispatch flips a default,
so it ships after the surface is correct and the operator has set the fields
they want.

**Concurrency (C21)** is out of scope; slot as its own phase after B.

**The bridge agent live flip (PM.5)** is out of scope; deliberate intermediate state.

---

## 6. Fold-in map

| Unit | Belongs to |
| --- | --- |
| C1 | `primitive-roles-2026-07-18.md` §4 (retire overseer), §2 (residue) |
| C2 | `primitive-roles-2026-07-18.md` §6.3 (templates), or delete-if-dead |
| C3 | `autonomy-model-2026-07-18.md` §1.2, §2 |
| C4 | `entity-model-2026-07-18.md` §6.2; `autonomy-model-2026-07-18.md` §1.1 |
| C5 | `autonomy-model-2026-07-18.md` §4 |
| C6 | `autonomy-model-2026-07-18.md` §5 |
| C7 | `autonomy-model-2026-07-18.md` §5; `entity-model-2026-07-18.md` §6.2 |
| C8 | `entity-model-2026-07-18.md` §6; `final-plan-2026-07-16.md` P4 |
| C9 | `entity-model-2026-07-18.md` §6 |
| C10 | `entity-model-2026-07-18.md` §6, §7.3 |
| C11 | `chat-scope-design-2026-07-18.md` §2 |
| C12 | `chat-work-linkage-2026-07-18.md` §2 |
| C13 | `chat-toolbar-audit-redesign-2026-07-18.md` |
| C14 | `run-status-view-2026-07-18.md` §3 |
| C15 | `clarifying-question-flow-2026-07-18.md`; `blocked-and-escalate-flow-2026-07-18.md` |
| C16 | `entity-model-2026-07-18.md` §4.3; `blocked-and-escalate-flow-2026-07-18.md` §3 |
| C17 | `chat-scope-design-2026-07-18.md` §2.1 |
| C18 | `entity-model-2026-07-18.md` §9 |
| C19 | `primitive-roles-2026-07-18.md` §4 |
| C20 | `entity-model-2026-07-18.md` §4.4 |

---

## 7. Build-through: what has to exist for this to feel like one system

The migration in section 4 makes the surface truthful. This section names the
scaffolding still missing for the operator to actually navigate the graph, act
on things in place, and understand the system without leaving the surface. Each
build item is a B-unit with the same shape as a C-unit. B-units sequence into
the existing Phase A / Phase B where the operator surface would otherwise ship
half-connected, or into a new Phase C when they logically follow the migration.

### 7.1 Connective tissue (per-entity cross-links)

The entity model defines the nouns; the operator experience is the graph they
form. Today Home.tsx binds a run to a `project_slug | agent_id | chat_id`
(Home.tsx:41-45), and that is roughly the whole cross-link surface. The rest of
the graph is invisible.

The required cross-links, per entity:

- **Agent -> Chats it participates in** (list, most recent first, link to /chat/agent:slug)
- **Agent -> Tickets it owns or is assigned** (open first, then done)
- **Agent -> Runs it executed** (grouped under the Ticket they attempted)
- **Agent -> Blocks it raised** (`raised_by: agent-slug`, entity-model §4.3)
- **Agent -> Grants it holds and Stores those touch** (already partially wired: AgentDetail.tsx references `ResourceGrant` at :47-59 and permissions surfaces it; verify the Stores those grants cover are shown as clickable)
- **Agent -> Templates it forked from** (`template: <slug>@<version>` frontmatter, agents_registry.py:265)
- **Ticket -> originating Chat** (currently: `source.ref` field exists on tasks; not surfaced in UI beyond a bare id)
- **Ticket -> owning Agent (assignee)** (partially wired at TaskPanel.tsx:105)
- **Ticket -> Runs attempted** (partially at Work.tsx:600-604 in the ticket drawer)
- **Ticket -> Blocks hit** (the widened `awaiting` on this ticket + historical blocks in evidence)
- **Ticket -> dependency edges** (`depends_on` / `blocks` derived / `relates_to`; visual today at TaskPanel.tsx:109 is comma-separated text only)
- **Chat -> Tickets filed from it** (chat-work-linkage §3, in-chat related-work summary; missing)
- **Chat -> Runs spawned from it** (missing)
- **Chat -> Scope** (missing until C11 lands)
- **Scope -> Stores, Agents, Chats, Tickets active there** (a scope page does not exist)
- **Store -> Grants outstanding + recent adjudications** (partial in `/settings/grants` at Grants.tsx:50, no adjudication history)
- **Grant -> Agent + Store + verbs + tier** (partial in `/settings/grants`)
- **Block -> Ticket, Run, Chat, Agent that raised it, and the six resolution verbs** (Review tab at Work.tsx:756-909 has this)

### 7.2 Where operator lands (Home and per-scope roster)

Home.tsx today is a section-list (Chats, Jobs, Apps, Projects). Operator
direction 2026-07-18: Home stays simple. Three bands (Apps, Chats, Projects),
four items each, most-recently-active first. No attention band, no override
pill, no budget bar, no upcoming triggers. The signal is "what am I working on
or near recently", not "system status dashboard".

The autonomy override STATUS pill (read-only visibility) moves to the
workshop-level Agents page header, next to the count of agents. The one
editable toggle stays on Settings per C6.

A workshop-level Agents view lists every Agent across every scope. A project's
Agents section (inside ProjectDetail.tsx) lists only that project's agents.
These need to compose so the operator understands "this Agent is defined at
workshop scope; it is instantiated in this project via a template fork; both
records are the same entity".

### 7.3 Role-shaped detail views

Section 6 of the entity model closes the role enum. Section 4 (C10) puts role
badges on the AgentDetail header. But a `triager`, `keeper`, `broker`,
`reviewer`, and `maker` all do different work: giving them one uniform detail
page hides the affordances specific to what each does. The primitive-roles doc
implies role-shaped surfaces (§5 for triager inbox; §6.3.3 for keeper cadence;
resource-access-interface-spec for broker verdicts).

### 7.4 Template mechanism (kept modal-only) and Store settings inside App / Project

Operator direction 2026-07-18: agents are primitives for unique roles OR
app/task-specific instances, not something spun up daily. A browsable Templates
gallery is deferred; the FromTemplateModal at Agents.tsx:232-385 stays as the
one-click adopt path.

Stores are not a distinct enough concept to warrant a page. A Store is the
data-face of an App or a Project (mostly 1:1). Store configuration (tier,
grants, allowlists, adjudications) lives INSIDE the App detail and Project
detail views. The workshop-level notes vault (`$WORKSHOP_NOTES_VAULT`) is the exception: it
belongs to the operator, not to any App or Project, and remains its own Store
surfaced through the Notes app.

### 7.5 The Block card, the Trigger editor, and the graph

Three surface-specific checks:
- The Block review card in Work.tsx renders the awaiting fields it knew about
  before the widening at entity-model §4.3. Post-widening (`kind`,
  `unblocks_if`, `options`, `default`, `proposal`, `dedupe_key`, `deadline`)
  needs every field to have a place on the card.
- The Trigger editor exists at AgentDetail.tsx:87-215 and now covers the
  `discord` kind (commit 38df367). Confirm it validates cron syntax, event
  sources, and discord channels.
- A graph view for scopes / grants / dependencies would be earned only if the
  graph is dense enough for a visual to beat a list. In this single-operator
  system it likely is not. Cross off explicitly.

---

### B1. Cross-link fields on backend entity reads

- **Files**: `backend/routes/agents.py` (add `linked_chat_ids`, `linked_ticket_ids`, `linked_run_ids`, `raised_block_ids` to `GET /api/agents/{slug}` response). `backend/routes/tasks_all.py` (add `chat_origin_id`, `run_ids` derived, `blocked_by_run_id` to `GET /api/tasks/{scope}/{id}`). `backend/routes/chat.py` (add `linked_ticket_ids`, `linked_run_ids` to `GET /api/chat/threads/{id}` response). `backend/services/chat_store.py` (denormalize on `spawn_task` and `/run`).
- **Change**: computed fields, no schema migration. Each is a query over the existing task store / jobs feed / chat store.
- **Risk**: low. Additive.
- **Depends on**: none.
- **Folds into**: `entity-model-2026-07-18.md` §7.3 (card linking) and §6.
- **Enables**: B2, B3, B4, B9.

### B2. Agent detail "Involved in" section

- **Files**: `frontend/src/pages/AgentDetail.tsx` (add a section under Definition rendering linked Chats + Tickets + Runs + Blocks + Grants + Store handles, each a real navigation link).
- **Change**: matches operator's verbatim ask ("being able to link/navigate to what chats and tasks and other things theyre involved in or doing").
- **Note**: this expands the "Involved in" seed in C10. C10 introduces the section; B2 fills it out. Ship as one PR if convenient.
- **Risk**: low.
- **Depends on**: B1, C10.
- **Folds into**: `entity-model-2026-07-18.md` §6, §7.3.

### B3. Ticket drawer cross-links and edge visualization

- **Files**: `frontend/src/pages/Work.tsx` (the ticket drawer around Work.tsx:600, currently shows only Runs). Add: "Filed from chat" link (from `chat_origin_id`), "Owned by agent" link (from `assignee`), "Depends on" pill list (linked tickets, each opens its drawer), "Blocks" pill list (derived from inverse), "Relates to" pill list, "Blocks currently open on this ticket" (widened awaiting summary).
- **Change**: turns the ticket drawer into a real graph node.
- **Risk**: low.
- **Depends on**: B1, C20.
- **Folds into**: `entity-model-2026-07-18.md` §4.2, §4.4.

### B4. Chat header cross-links and inline edit

- **Files**: `frontend/src/components/Chat.tsx` toolbar (around Chat.tsx:1102-1211). Add a compact "Scope: project:foo" indicator with a re-scope button (uses the existing POST /threads/{id}/rescope). Add an "In this chat" summary drawer showing linked Tickets and Runs, updated after each turn. Title becomes inline-editable in place.
- **Change**: chat becomes a graph node, not just a stream of messages.
- **Risk**: low.
- **Depends on**: B1, C11, C13.
- **Folds into**: `chat-scope-design-2026-07-18.md` §2, `chat-work-linkage-2026-07-18.md` §3.

### B5. Zero-state design across Agents, Work, Chats, Home

- **Files**: `frontend/src/pages/Agents.tsx` (current empty state at :526-530 is a bare `StateBlock` referencing "New builder"; replace with a From-Template call to action naming `note-triager` and `memory-keeper` as one-click adopts via the existing FromTemplateModal, plus a "start blank" secondary). `frontend/src/pages/Work.tsx` (empty state matches, current at :186-192). `frontend/src/pages/Chats.tsx` (empty state). `frontend/src/pages/Home.tsx` (empty state: "No Apps / Chats / Projects yet" with `[Create]` buttons per band; nothing more).
- **Change**: implement the zero-agent default from entity-model §6.3 as a real UX. Home stays deliberately spartan.
- **Risk**: low.
- **Depends on**: C8 (Agents rewired around role), C11 (Chat entity) is helpful but not required.
- **Folds into**: `entity-model-2026-07-18.md` §6.3 (zero-agent default); `primitive-roles-2026-07-18.md` §6.3 (`minimal` group).

### B6. Role-shaped Agent detail switching

- **Files**: `frontend/src/pages/AgentDetail.tsx` (dispatch the body of the detail page on `role`). Five role-shaped sub-panels:
  - `triager`: recent notes-in / decisions-out (the four decisions from primitive-roles §5.5 with counts + last decision timestamp). Data source: `.runtime/triage-state.json` sidecar (primitive-roles §5.5 Step 6).
  - `keeper`: the Store this keeper maintains + last consolidation timestamp + delta counts (ADD/UPDATE/RETIRE/NOOP from primitive-roles §6.3.3). Data source: memory_keeper output.
  - `broker`: recent verdicts (allow/narrow/deny), allowlists it adjudicates against. Data source: `broker.py` logs.
  - `maker`: tickets in flight + recent commits (already partial via AgentBuild.tsx workbench; surface a compact "recent runs" strip on the plain detail page too).
  - `reviewer`: the review queue this reviewer sees. Data source: same as the Work.tsx Review tab, filtered.
  - `None` (no role): generic body only.
- **Change**: each role's page tells the operator what the agent DOES, not just what it is.
- **Risk**: medium. Requires small backend read endpoints per role.
- **Depends on**: B1, C10.
- **Folds into**: `primitive-roles-2026-07-18.md` §5 (triager loop), §6.3 (templates); `resource-access-interface-spec-2026-07-18.md` (broker); `entity-model-2026-07-18.md` §6.1 (roles).

### B7. Inline editability audit across Agent detail

- **Files**: `frontend/src/pages/AgentDetail.tsx`. Confirm inline edit-mode (edit / save / cancel per design system) for: `name`, `purpose`, `role`, `latitude`, `autonomy`, `depth`, `fanout`, `triggers`, `limits`, `grants`, `project_slug`, `retired`, body. Already-built pieces: TriggerEditor at :87-215 (triggers); definition body at :656-665. Missing: role select, latitude Segmented (partially present in AutonomyPanel but not on the agent's own header), autonomy Segmented, grant add/revoke inline (currently `/settings/grants` is the only place).
- **Change**: turn the operator's core interaction into one page.
- **Risk**: low. Backend already accepts all these fields on PUT /agents.
- **Depends on**: C4, C7, C10.
- **Folds into**: `entity-model-2026-07-18.md` §6.2.

### B8. Inline editability audit across Ticket detail

- **Files**: `frontend/src/components/TaskPanel.tsx` and the Work.tsx ticket drawer. Confirm inline edit-mode for: `text` (title + body), `status`, `priority`, `latitude`, `effort`, `touches`, `depends_on`, `blocks` (view-only, derived), `relates_to`, `assignee`, `scope` (re-scope), `acceptance`, `max_attempts`. Already-built: TaskPanel.tsx:97-110 EditDraft, TaskPanel.tsx:104 latitude, :106-108 acceptance. Missing: `touches` field editor, `relates_to`, scope re-scope.
- **Change**: matches operator's ask that config be actionable in place.
- **Risk**: low.
- **Depends on**: C20, B3.
- **Folds into**: `entity-model-2026-07-18.md` §4.2.

### B9. Home.tsx redesign around the unified model

- **Files**: `frontend/src/pages/Home.tsx` (currently 434 lines; likely most of it goes). Rewrite to three horizontal bands, four items each, most-recent-first: **Apps** (top 4 by recency), **Chats** (top 4 by recency), **Projects** (top 4 by recency). Each card links to the entity's detail. That is all Home does.
- **Not on Home**: no autonomy override pill (moves to /agents workshop-view header per §7.2 and C6), no budget bar, no attention band, no upcoming triggers, no recent runs section, no recent promotions, no review-needed band. Cut all of it. The top nav carries "what exists"; Home carries "what I was near recently".
- **Ordering**: recency first (`last_active` for Apps, `last_active_at` for Chats, `last_active` for Projects). Frequency-weighting deferred; simplest thing that could work.
- **Change**: Home becomes an at-a-glance shortcut surface, not a status dashboard.
- **Risk**: medium (visible change to the first surface, but the new shape is far simpler than the old).
- **Depends on**: none from the migration proper (B1 is not required since Home no longer surfaces cross-links).
- **Folds into**: `entity-model-2026-07-18.md` §2 (scope addressing shows through: each band's items live in their own scope).

### B10. Workshop-scope vs project-scope Agents composition

- **Files**: `frontend/src/pages/ProjectDetail.tsx` (add an Agents section listing agents linked to this project; the workshop-level roster stays at /agents). `frontend/src/pages/Agents.tsx` (add scope-kind filter chips: Workshop / Project / Agent; clicking a project-scoped agent links to its project's Agents section).
- **Change**: turns the roster into a scoped view that composes.
- **Risk**: low.
- **Depends on**: C8.
- **Folds into**: `entity-model-2026-07-18.md` §2 (scope addressing).

### B11. Store settings surface inside App detail and Project detail

- **Files**: `frontend/src/pages/Apps.tsx` and `frontend/src/pages/ProjectDetail.tsx` (add a "Storage and access" section per detail view). Each section reads `GET /api/stores` filtered to the host's URIs and exposes: Store URI, tier (open / gated / mediated), Grants outstanding on it (agent slug + verb set + scope; source `backend/services/permissions.py:100-108` Identity + Grant), the mediated broker allowlist entries when tier is `mediated` (source `backend/services/broker.py`), and the recent adjudications (source: broker verdict log). Inline edit for tier + allowlist + individual grant revoke. The workshop-level notes vault (`$WORKSHOP_NOTES_VAULT`) stays reachable through the Notes app since it belongs to the operator, not to any App or Project.
- **Change**: Store configuration lands where the operator already reasons about the app or project. No separate `/stores` page.
- **Backend**: verify Store registry location. `backend/services/stores.py` is the current home per the notes-and-docs audit. A small `GET /api/broker/verdicts?store=<uri>` may be added for the adjudication list, small self-contained endpoint reading `broker.py`'s log.
- **Risk**: low. Reuses existing `/api/stores`, permissions, broker paths.
- **Depends on**: none directly.
- **Folds into**: `resource-access-interface-spec-2026-07-18.md`; `entity-model-2026-07-18.md` §8.3 (store tiers).
- **Note**: assumes Store is ~1:1 with an App or Project (see "What to preserve" for the record of this design observation, and Open Questions for whether entity-model should mint a formal `Store.host_app | Store.host_project` field).

### B12. Block review card completeness

- **Files**: `frontend/src/pages/Work.tsx` around Work.tsx:756-909 Review tab. Audit each awaiting field: `kind` (rendered as a Tag), `why` (paragraph), `unblocks_if` (label + text), `options` (button row when non-empty), `default` (fine-print "auto-resolves to X at deadline" when latitude allows), `proposal` (structured pill "grant write on store://X" plus one-click Grant-standing button), `dedupe_key` (silently used for grouping), `deadline` (relative time), `raised_by` (linked to Agent or Run).
- **Change**: the widened awaiting shape becomes actionable at a glance, not decoded.
- **Risk**: low.
- **Depends on**: C4 (autonomy chip) helpful; C15 (block card also in chat) parallel.
- **Folds into**: `blocked-and-escalate-flow-2026-07-18.md` §3; `entity-model-2026-07-18.md` §4.3.

### B13. Trigger authoring completeness

- **Files**: `frontend/src/pages/AgentDetail.tsx:87-215` TriggerEditor. Verify: cron field validates 5-field cron (currently free-text at :98). Event field validates against KNOWN_SOURCES (currently comma-split at :666-674). Discord field: channels editable + validated (commit 38df367 wired this; confirm at :74). Loop field: max_iterations positive (already validated at :684).
- **Change**: what already exists gets its edges tightened.
- **Note**: "already built" for the surface; this unit is verification + narrow fills.
- **Risk**: low.
- **Depends on**: none.
- **Folds into**: `entity-model-2026-07-18.md` §6.2 (triggers field); `autonomy-model-2026-07-18.md` §1.1.

### B14. Graph view — considered and rejected

- **Change**: not built. In a single-operator system with under 20 agents, a few dozen stores, and O(100) live tickets, a list per relation reads faster than a graph render. If the population grows past a threshold the operator names, revisit.
- **Depends on**: nothing.
- **Folds into**: this doc's rejected list.

---

### 7.6 Build-through sequencing

The B-units slot into the existing phases. Rule of thumb: a B-unit lands in the
phase where its C-unit dependency lands, or one phase later. N-units from
section 8 fold in below where they belong.

**Phase A additions** (before or with the coherence-restoring migration):

- B1 (cross-link backend fields) lands with C10 to make Involved-in real.
- B5 (zero-states) lands with C8, C9 to make the rewired /agents page teach.
- B13 (trigger authoring completeness) lands anytime; near-zero cost.
- N1 (add vault-read-only ADR 0010) — documentation only.
- N2 (retire `shared/global-memory.md`) — nothing reads it.
- N3 (retire `shared/memory-tree.yaml` + prune `memory_tree.py`) — Letta-era dead code.
- N4 (prune ghost `.runtime/agents/` dirs) — pure cleanup; overlaps with C1's spirit.
- N7 (clean stray `projects/demo-x/` probes) — housekeeping.

**Phase B additions** (with the deeper alignment):

- B2 (Agent Involved-in filled out) with C10.
- B3 (Ticket cross-links) with C20.
- B4 (Chat cross-links + inline edit) with C11, C13.
- B7 (Agent inline edit audit) with C7.
- B8 (Ticket inline edit audit) with B3.
- B12 (Block card completeness) with C15.
- N5 (design-check lint against flat `NOTES.md` / `INTERVENTIONS.md` drift).
- N6 (fold duplicate `notes-and-docs` policy source; optional).

**Phase C (after the migration is coherent):**

- B6 (role-shaped Agent detail views).
- B9 (Home.tsx redesign — three bands).
- B10 (workshop vs project agents composition).
- B11 (Store settings inside App and Project detail).

Phase C is the "make it feel like one system" phase; the migration must complete
first so the phase C surfaces do not layer on top of a still-changing spine.

---

## 8. Notes and documentation: per-scope protocol audit

Two surprises from the audit before the detail:

- **The three-tier per-scope protocol is already designed and mostly wired.** The gap is agent USAGE, not design. `.runtime/agents/<slug>/knowledge/` dirs exist for every agent but are all empty (see G1 below). The finalize `note` capture path is in place (`chat_context.py:225-239`); zero agents have ever used it.
- **F3 (learnings scope-key global fallback) is already fixed** at `ralph.sh:358-360` (bare slug -> `project:<slug>`) and `project_ralph.sh:860` (explicit `--learn-scope`). The latitude-model doc's E0 prerequisite ("F3 must be fixed before rules mint into learnings") no longer applies. Anywhere the P0 or E-series sequencing carries F3 as an open prereq, drop it.

The operator asked whether there is a standardized per-scope protocol for
notes, learnings, decisions, and design docs, and whether agents are polluting
the personal `$WORKSHOP_NOTES_VAULT` vault. Short answer: the protocol is already largely
built (three tiers, per-scope layout, ADR convention, mediated-only vault
access), and no code path writes agent-internal notes into `$WORKSHOP_NOTES_VAULT`. The
remaining work is cleanup, one missing ADR, and a lint-level guard so a flat
`NOTES.md` cannot creep back in.

### 8.1 Current mechanisms (row per surface)

| # | Surface | Purpose | Scope-awareness | Writer | Reader | Evidence |
| - | ------- | ------- | --------------- | ------ | ------ | -------- |
| 1 | Knowledge notes (`services/knowledge.py`) | Per-scope Markdown notes with `MEMORY.md` index; source of truth for scoped facts | Per-scope: `workshop`->`shared/knowledge/`, `project:<slug>`->`projects/<slug>/knowledge/`, `app:<name>`->`apps/<name>/knowledge/`, `agent:<id>`->`.runtime/agents/<id>/knowledge/` | Operator (UI, `PUT /api/knowledge/{scope}/notes/{id}`) + agents (fenced `note` block, chat_context.py:225-239 finalize path) | Chat prompt (`index_block`, injected in build_streaming) + on-demand search | knowledge.py:92-111, 300-325; chat_context.py:225 |
| 2 | Consolidated memory | Memory-keeper deltas per scope (mem0/ACE-shape append-only bullets) | Per-scope, stored as knowledge note `consolidated-memory` | memory-keeper runner only | Same as (1) via index | memory_keeper.py:49-54, 417-459 |
| 3 | Chat-distilled | Curated bullets seeded from chat compaction; keeper reads these on gather | Per-scope, knowledge note `chat-distilled` | Chat compaction pipeline (finalize) | memory-keeper only | memory_keeper.py:57, 279-289 |
| 4 | Skill-proposals | Keeper drafts proposed SKILL.md bodies for operator confirmation | Per-scope, knowledge notes `skill-proposal-<hash>` | memory-keeper only | Operator via Review tab / Notes UI | memory_keeper.py:528-580 |
| 5 | Learnings store (`services/learnings.py`) | Deduped one-line lessons + provenance, injected as guardrails | Per-scope JSONL at `.runtime/learnings/<safe-scope>.jsonl`; `global` also mirrors to `shared/learnings.md` (git-tracked) | log_learning.sh, ralph.sh:280,768; reviewer path in `tasks_all.py:572`; memory-keeper procedural pass (memory_keeper.py:490-504) | Injected into every scope's chat + into ralph runs | learnings.py:96-121, 293-313 |
| 6 | Legacy `shared/learnings.md` | One-way mirror of `global` scope so the SessionStart hook keeps seeing lessons | Global only (single file) | `_mirror_to_md` on record; migrated in on first read | SessionStart hook + humans | learnings.py:294-313 |
| 7 | ADR decisions | Long-lived, versioned architecture decisions | Repo-wide `docs/decisions/NNNN-*.md`; 10 present (0000 template + 0001-0009) | Operator | Agents (fenced ADR advice, chat_context.py:238) + humans | notes-system-plan-2026-07-15.md:120-121; on-disk |
| 8 | Design docs | Long-form plans, redesigns, handoffs | Repo-wide `docs/` (workshop-v1, platform-redesign, voice-mode, etc.) | Operator (mostly) + coherence-audit subagents | Operator + agents on request | on-disk |
| 9 | Notes-and-docs policy | The one-hard-rule tiering doc | Workshop scope note + SKILL | Operator | Any agent that loads the SKILL; builders auto-discover | shared/knowledge/notes-and-docs-policy.md; shared/skills/notes-and-docs/SKILL.md |
| 10 | Personal notes vault | The operator's own thinking | Filesystem tree at `$WORKSHOP_NOTES_VAULT` (default `$WORKSHOP_NOTES_VAULT`) | Operator only (via `/notes-app` HTTP routes) | Operator; agents ONLY via mediated broker | notes_vault.py:26-32; stores.py:261; store-policies.json (vault entry) |
| 11 | Task store | Tasks/tickets carrying prose bodies (which themselves accrete context) | Root `.tasks.json` + `TODO.md`; per-project `projects/<slug>/.tasks.json`+`TODO.md` | Everyone (operator, agents, dispatchers) | Everyone | on-disk; system-audit-2026-07-18.md flagged this |
| 12 | Job evidence | Per-run receipts, verdicts, effort, tokens | Single append-only `.runtime/jobs.jsonl`, filtered by scope on read | jobs.py writers | memory-keeper (`_scope_jobs`), Work surface | memory_keeper.py:210-235; `.runtime/jobs.jsonl` |
| 13 | Chat transcripts | Message history per thread | Per-owner under `.runtime/users/<owner_id>/chat/<thread_id>.json` (NOT per-scope) | chat_store on turn commit | Chat UI + chat compaction | chat_store.py:20-24 |
| 14 | Scratch/tmp | Ephemeral thinking, probes, throwaway output | `scratch/` and `tmp/` at any level (all gitignored per `.gitignore`) | Anyone | Anyone; nothing durable reads it | CLAUDE.md; on-disk scratch/, tmp/, backend/scratch/ |
| 15 | Agent def files | Static definition (role, triggers, memory pointer) | `agents/<slug>.md` (fork-not-link templates land in `agents/`) | Operator via UI or `_templates/` fork | Registry loader | agents/the bridge agent.md, agents/memory-keeper.md; agents_registry.py |
| 16 | Legacy `shared/global-memory.md` | Old Letta operator-context block | Global (single file) | Nothing writes it now (Letta retired) | Nothing reads it now | on-disk; Stage 9 handoff notes Letta was removed |
| 17 | Legacy `shared/memory-tree.yaml` | Old persona memory-inheritance tree | Global | Nothing writes it now | `memory_tree.py` still parses if present | shared/memory-tree.yaml (mtime 2026-05-21) |
| 18 | Meta-reject markers | Task-filter substrings, NOT a notes surface | Repo-root helper | Operator | Letta workshop tool, `work_generator.sh`, `graduate_proposals.sh` | shared/meta_reject.py:3-16 |
| 19 | Commute notes | App-specific note artifact | `.runtime/commute-notes/` (per operator, not committed) | Commute app | Commute app | check_pluggable_apps.sh:118-119 |

### 8.2 The notes-vault leak check

Verdict: **NO**. Under the shipped code, no ambient agent execution writes to
`$WORKSHOP_NOTES_VAULT`. Evidence:

- `notes_vault.create_note` / `update_note` / `delete_note` are called from exactly two places: the HTTP routes at `routes/notes.py:84-122` (mounted under `/notes-app`, operator-authenticated) and the vault-adapter write door at `stores.py:687-700`.
- The write door refuses to write knowledge scopes (`stores.py:687-692`) so it can only target the vault Store, and only when the caller carries a matching grant (`_grant_for(uri, verb, token)`, stores.py:679-682). The operator identity is trivially permitted; an `agent` identity has no ambient authority (permissions.py:100-108).
- `grants.json` and `resource-grants.json` are both empty (`{"grants": []}`), so no agent identity currently holds any grant.
- `store-policies.json` explicitly caps `workshop://store/notes/vault` at `verbs_max: ["read","list","search"]`, `requesters_allow: ["agent:*"]`, `response_modes: ["filtered","redacted"]`, tier `mediated`. The prose rule states `"No verb writes, ever, through this record."` So even if an operator later minted a write grant against the vault, the policy layer would reject it before the broker was asked.
- memory-keeper writes ONLY through `knowledge.put_note` (memory_keeper.py:455-458, 569-573) into the per-scope knowledge dirs, NEVER into `notes_vault`.
- The workshop-side notes-app project is a DIFFERENT surface: `projects/notes-app/` is a project that builds an app, not a write target for agent notes.

The design keeps the door closed at three layers (permissions, policy, adapter refusal for knowledge scopes) and there is no code path that would open it accidentally.

### 8.3 Coherence gaps found

- **G1. Per-agent knowledge dirs are ghosts.** Every subdir under `.runtime/agents/` exists but is empty (`ls -R .runtime/agents/` shows only `knowledge/` with no children). Includes ghosts (`doesnotexist/`, `undefined/`) and legacy templates (`blank-project-1/`, `notes-app-builder/`, `workshop-builder-2/`, `workshop-builder-3/`, `birds-builder-2/`, `budget-builder-1/`). No agent has ever actually written a scoped note through the finalize `note` block. Plumbing is wired; usage is zero. Empirically confirms the operator worry that agents are not writing notes.
- **G2. `shared/global-memory.md` is a Letta artifact left behind.** Letta was removed in the 2026-07-15 Stage 9 teardown; the file still sits at `shared/global-memory.md` (mtime 2026-07-15). Nothing writes it now; nothing that ships reads it. Contains an outdated operator profile duplicated by user CLAUDE.md.
- **G3. `shared/memory-tree.yaml` is another Letta artifact.** `memory_tree.py` still imports and parses it, but the persona-inheritance flow it was designed for is gone with Letta. Dead code path.
- **G4. Chat transcripts are per-owner, not per-scope.** They live at `.runtime/users/<owner_id>/chat/<thread_id>.json` (chat_store.py:20-24). The `context_key` names the scope, but retrieval by scope means scanning all threads. Not blocking (chat-distilled + consolidated-memory already handle scope-aware retention). Kept as-is.
- **G5. Duplicate policy: workshop knowledge note vs. SKILL.** Same content lives at `shared/knowledge/notes-and-docs-policy.md` and `shared/skills/notes-and-docs/SKILL.md`. Notes-system-plan §N0 said this was intentional (indexed + discoverable), but they can and will drift.
- **G6. No ADR pinning "vault is read-only to agents".** The rule is enforced in three code layers and stated in `store-policies.json` prose, but there is no `docs/decisions/00NN-notes-vault-read-only-to-agents.md`. A decision this load-bearing deserves a record.
- **G7. Stray receipt-probe.** `projects/demo-x/receipt-probe.txt` and a couple of `SMOKE_A.md` / `REGRESS.md` under `projects/demo-x/` are old probes that should be scratch, not sibling files to the project tree.
- **G8. F3 (learnings scope fallback) is FIXED.** `ralph.sh:358-360` maps bare slugs to `project:<slug>` and `project_ralph.sh:860` passes an explicit `--learn-scope`. Recording here for the migration plan.
- **G9. Nothing enforces the tiering.** An agent could still `Write` a `notes.md` at repo root and no lint stops it. The rule is a persona paragraph and a SKILL; not a mechanical guard.

### 8.4 Target-state protocol (compact restatement)

The protocol is already designed and largely built. Restating so it can be pointed at:

- **Ephemeral scratch** -> `scratch/` or `tmp/` anywhere in the tree. Gitignored. Never a source of truth.
- **Durable scoped facts** (learnings, findings, consolidated memory, chat distillations, skill proposals) -> knowledge Notes substrate. Per-scope directory, `MEMORY.md` index + `<id>.md` per note. Written via `knowledge.put_note`. Layout:
  - `workshop` -> `shared/knowledge/` (git-tracked)
  - `project:<slug>` -> `projects/<slug>/knowledge/` (gitignored)
  - `app:<name>` -> `apps/<name>/knowledge/` (git-tracked)
  - `agent:<slug>` -> `.runtime/agents/<slug>/knowledge/` (gitignored)
- **Deduped rules and guardrails** -> `services/learnings.py` at `.runtime/learnings/<safe-scope>.jsonl`. Same scope keys. `global` mirrors `shared/learnings.md`. Fenced `learning-note` block is the agent capture path.
- **Run receipts and evidence** -> `.runtime/jobs.jsonl` (single append-only, filtered by scope on read).
- **Chat transcripts** -> `.runtime/users/<owner>/chat/<thread>.json`. Not per-scope on disk; per-thread with a `context_key` naming the scope. Kept as-is.
- **Long-form plans and design docs** -> `docs/`. Committed. Discoverable via Notes UI Documents view (Phase N3 of notes-system-plan).
- **Architecture decisions** -> `docs/decisions/NNNN-short-title.md`. 10 records seeded.
- **Personal notes vault** (`$WORKSHOP_NOTES_VAULT`) -> OPERATOR-ONLY WRITE, read-only to agents via mediated broker. No ambient grants; write verbs capped at the policy level; adapter refuses to serve knowledge-scope writes through the vault door.
- **What is NOT a notes surface (do not add one)**: flat per-scope `NOTES.md`; project-level `INTERVENTIONS.md` as a "notes" store; repo-root `learnings.md` that is not a scope mirror; Letta-era `global-memory.md`.

Each per-scope knowledge dir is declared as a Store of type `Note` at startup (`knowledge.register_knowledge_stores`, knowledge.py:412-447), so agents reach it through the same grants mechanics as any other Store. Substrate stays flat markdown; Store is the addressing/permission layer on top.

### 8.5 N-unit migration units

Ordered by risk. All small, all reversible.

**N1. Add the vault-read-only ADR.** Create `docs/decisions/0010-notes-vault-is-read-only-to-agents.md` recording: the three-layer enforcement (identity default-permissive, empty grants file, policy `verbs_max`, adapter refusal), the mediated-broker path for agent recall, and the failure mode (an operator-minted grant, blocked by store-policies.json prose rule). Cites permissions.py:100-108, stores.py:261, store-policies.json (vault entry), stores.py:687-700. Risk: none. Depends on: none. Folds into: `final-plan-agent-generalize-and-bridge-fold-2026-07-16.md` P4 operator-review folder OR freestanding.

**N2. Retire `shared/global-memory.md`.** Move any still-relevant lines into a workshop-scope knowledge note or a new ADR documenting the retired Letta wiring; delete the file. Cites Stage 9 Letta teardown. Risk: low (nothing reads it). Depends on: N1 optional.

**N3. Retire `shared/memory-tree.yaml` and prune `memory_tree.py` if unused.** Check for remaining `memory_tree` imports; if none, delete both. Cites `shared/memory-tree.yaml` and `services/memory_tree.py`. Risk: low. Depends on: none.

**N4. Prune ghost per-agent dirs under `.runtime/agents/`.** Delete `doesnotexist/`, `undefined/`, and any dir whose slug does not correspond to a live agent def (`agents_registry.list_agents()`). Idempotent one-shot cleanup script under `scripts/`; safe because the dirs are gitignored and empty. Cites `.runtime/agents/`. Risk: none. Overlaps in spirit with C1 (agents/*.md legacy cleanup) but targets a different tree.

**N5. Add a design-check lint rule against flat notes surfaces.** Extend `scripts/check_design.sh` (or `scripts/check_pluggable_apps.sh`) to fail if the tree grows a new file matching `^NOTES\.md$` at repo root or per-project, or a new `INTERVENTIONS.md` outside `build-agents/`. Mechanical enforcement for G9. Cites `scripts/check_pluggable_apps.sh:118-119` as the pattern. Risk: low.

**N6. Fold the duplicate `notes-and-docs` policy to one authoring source.** Pick one of `shared/knowledge/notes-and-docs-policy.md` or `shared/skills/notes-and-docs/SKILL.md` as the source; generate the other from it in a pre-commit hook OR add a "keep in sync" header. Risk: none. Optional.

**N7. Clean stray `projects/demo-x/receipt-probe.txt` and probe files.** Move `receipt-probe.txt`, `SMOKE_A.md`, `REGRESS.md` under `demo-x/` into `demo-x/scratch/` (already exists) or delete. Risk: none. Depends on: none.

### 8.6 Testing plan (notes-and-docs)

Light smoke checks; each is one command or one probe.

1. **Fresh-scope layout.** Create a project via `POST /projects` with slug `probe-notes`, then confirm `projects/probe-notes/knowledge/` is created lazily on first `PUT /api/knowledge/project:probe-notes/notes/test` and contains `MEMORY.md` + `test.md`. Delete the project; nothing left over.
2. **Fenced-note capture round trip.** POST a chat message to the `project:probe-notes` scope containing a `note` fenced block. Verify `projects/probe-notes/knowledge/<id>.md` exists with the body and `MEMORY.md` lists it; verify the reply does NOT contain the fenced block (finalize path strips it).
3. **Learnings scope isolation (F3 regression).** From a fresh scope, `python -m services.learnings record "probe rule" --scope project:probe-notes`. Verify `.runtime/learnings/project-probe-notes.jsonl` gains an entry and `.runtime/learnings/global.jsonl` does NOT.
4. **Vault write blocked to agents.** With the empty `grants.json`, simulate an agent-identity call to the store write door with a `notes:vault` URI and `verb=write`. Expect `AccessDenied("no_grant", ...)` before the adapter is reached (stores.py:679). With a hand-added grant: the policy rejects `write` because it is not in `verbs_max` (stores.py:674-678).
5. **Memory-keeper writes only to knowledge scopes.** Run `python -m services.memory_keeper workshop --dry-run`. Verify no vault files change (compare `$WORKSHOP_NOTES_VAULT` mtimes) and any deltas would land only in `shared/knowledge/consolidated-memory.md`.
6. **Index-cap under load.** Push 250 tiny notes into a scope. Verify `MEMORY.md` stays under 200 lines and older notes moved to `archive/` under `memory-archive` catalog (knowledge.py:177-236).
7. **Design-check lint.** After N5 lands: create `projects/probe-notes/NOTES.md`, run `bash scripts/check_design.sh`, expect failure with a clear message pointing at the tiering policy.

### 8.7 Open questions (notes and docs)

- **OQ-N1.** Should chat transcripts also be scope-partitioned on disk, or is the per-owner + `context_key` shape fine? Current answer: fine; nothing in the design requires per-scope transcript files.
- **OQ-N2.** Are ADRs 0001-0009 the intended stopping point for now, or should coherence-plan units that land decisions (e.g. the vault-read-only posture in N1) mint additional records? Recommend: yes, one per load-bearing invariant that survives the current sweep.
- **OQ-N3.** Keep the duplicate `notes-and-docs` policy in two places (workshop knowledge note + SKILL) as N6 leaves it, or generate one from the other with a pre-commit hook? Low cost either way.

---

## 9. What to REMOVE

Distinct list, with justification. All are safe removals.

- `agents/blank-project-1.md`, `agents/blank-project-2.md`, `agents/probe-free.md`, `agents/probe-git.md`, `agents/smoke-fix.md`, `agents/test1234.md`, `agents/test2.md` — bare probe/smoke defs, `triggers: []`, never dispatched. Test scaffolds committed by accident.
- `agents/workshop-builder.md`, `agents/workshop-builder-2.md` — carry a live-looking but stale 2026-06-22 plan body; `triggers: []`; misleads readers into thinking that overhaul is current.
- `agents/work-reviewer.md` — Claude Code subagent format, not a registry member; belongs at `~/.claude/agents/work-reviewer.md`. System-audit reports zero call sites in the reviewer chain (deterministic diff-linter is what actually runs).
- The at-a-glance autonomy toggle in `Agents.tsx:510-520`.
- The Autonomy tab in `Work.tsx:59,63,673-753` (the same toggle a third time).
- The `AutonomyControls.tsx` component (or demoted to a Settings mount).
- The `builder`/`assistant` axis as the primary display in Agents.tsx and Chats.tsx (KEEP the runtime field on the Agent record; REMOVE its use as a UI category).
- `letta_tools/__init__.py:38-49` `workshop-overseer` bindings.
- The card-appended `job-status` message in `backend/routes/chat.py` /run endpoint after C12 lands (see chat-work-linkage §2.2).
- The dual `job.status` + `job.task_status` pills in JobDetail.tsx.
- The `New builder` modal in `Agents.tsx:90-208` (replaced by the reworked `/agents/new` in C9).

DO NOT REMOVE:

- The `FromTemplateModal` at `Agents.tsx:232-385`. Per operator direction 2026-07-18: agents are primitives for unique roles or app/task-specific instances, not something the operator spins up daily. The modal covers one-click adopt from the zero-state and from the /agents header. A full browsable Templates gallery (previously B11) is DEFERRED, easy to add later when there are more than a handful of templates.
- `AgentBuild.tsx` — the builder workbench is legitimate as the maker-runtime UI for a project-scoped `role: maker` agent. It is not the concept, it is A concept UI. Keep it, stop making it the front door.
- The `builder` runtime field on Agent — deriving `builder` from `role: maker + project_slug` in the create form is fine, and the field remains valid metadata for dispatch (dispatch_triggers.sh:508-516 uses it).
- The `.runtime/AUTONOMY_DISABLED` flag file — keep it readable during transition; the scripts fall back to it if the JSON overlay is absent. Removable after one release.
- `the-bridge.service` and `<bridge-checkout>/` — deliberate intermediate state per PM plan.
- `agents/the bridge agent.md` — inspection surface for the flip.
- `agents/the bridge agent-commute.md` — persona for on-demand commute chats.
- `agents/memory-keeper.md` — works.
- `agents/_templates/` — correct location.

---

## 10. What to preserve

Called out so silence is not oversight.

- The role enum + `_validate_role` + `SINGLETON_ROLES` (agents_registry.py) — coherent and correct.
- The dispatch role handlers (dispatch_triggers.sh) — coherent and correct.
- The template + fork model (agents_registry.py:739-819) — coherent with primitive-roles §6.
- The widened `awaiting` shape (tasks.py:346-380) — matches entity-model §4.3.
- The Review tab in Work.tsx (Work.tsx:756-909) — the operator-facing block loop.
- The latitude cascade (services/latitude.py + agents_registry.py:106-142 + tasks.py) — matches latitude-model.
- `touches` enforcement + `lane_gaps` widening (tasks.py:481, 536-580) — the recent queue-repair work.
- The bridge fold-in build (PM.1-PM.4 commits) — do NOT accidentally revert while cleaning up.
- The Plaid MCP in-tree at `backend/mcp/plaid/` (PM.4).
- `agents/memory-keeper.md`, `_templates/*`, all `groups/*.json`.
- All existing backend `/api/agents/from-template`, `/adopt-group`, `/templates` endpoints.

DESIGN OBSERVATIONS TO KEEP DURABLE:

- **Store is ~1:1 with an App or Project.** Operator observation 2026-07-18: a Store is the data-face of an app or a project, so its settings (tier, grants, allowlists, adjudications) belong INSIDE the App or Project detail view (B11), not on a separate `/stores` page. The workshop-level notes vault (`$WORKSHOP_NOTES_VAULT`) is the exception because it belongs to the operator, not to any App or Project. Whether `entity-model-2026-07-18.md` should mint a formal `Store.host_app | Store.host_project` field is Open Question 5 (an operator decision surface, not a mandate to add).

---

## 11. Testing plan

Light smoke per unit. The autonomy override is left ENGAGED at the end so the
operator's "leave it off" preference is honored.

- **C1, C2, C19**: `python -c "from services import agents_registry; print(len(agents_registry.list_agents()))"` returns a smaller number; no ImportError from letta_tools startup.
- **C3**: `echo '{"engaged":true,"reason":"migration","engaged_at":"2026-07-18T00:00:00Z","engaged_by":"operator"}' > .runtime/autonomy-override.json`; `bash scripts/dispatch_triggers.sh` prints `override engaged; dispatching nothing`; deleting the file restores normal behavior.
- **C4, C5**: `POST /api/agents/` with `{role:"maker", autonomy:"reactive"}` returns 200; the record round-trips through `list_agents()`. An agent with `autonomy:"reactive"` and a cron trigger prints `skip-reactive` in dispatch.log next tick.
- **C6, C7, C8, C9, C10**: `cd frontend && npx tsc --noEmit`; `bash scripts/check_design.sh`; manual: `/agents` shows one list, no builder/assistant split; `/agents/new` picks role; `/agents/:slug` shows role/latitude/autonomy chips + involved-in section.
- **C11, C12**: create a chat, file-and-run a task, reload; the task-proposal card renders as tracking (no re-offer); reload again; the `card_states` sidecar persists.
- **C13**: chat toolbar has labeled controls; no double chat-name field; visual check at 1440 / 1024 / 768 / 390.
- **C14**: JobDetail shows one status pill; live activity band appears while a run is running; "Edit in chat" opens the originating thread.
- **C15, C16**: raise a Block from a run; verify the block card appears in the originating chat AND on Work Review; either surface resolves it.
- **C17**: Chats page filter kinds are Workshop / Project / App / Agent.
- **C18**: contributor reads `docs/workshop-v1/00-overview.md`, sees banner, follows the pointer.
- **C20**: creating a ticket cycle via `PATCH /api/tasks/{scope}/{id}` returns 400.

### 11.1 Navigability smoke tests

The unit tests above prove each change. The navigability tests prove the graph
is actually connected. Every hop below must work; any missing hop is a B-unit
that did not land.

- **Agent to Ticket to Run to Chat, and back.** From /agents pick any agent; on
  its detail page (B2) an Involved-in section lists a Ticket; click through to
  the Ticket drawer in Work (B3); the drawer names the Chat that filed it;
  clicking opens the Chat (B4); the Chat's "In this chat" summary lists the
  same Ticket. Every step is a real link, no back button, no "how did I get
  here" moment.
- **Block card to Ticket to unblock.** From the /agents header attention pill or a Work Review link, land on Work Review (existing); the block card (B12) shows `why`, `unblocks_if`, `proposal`; the Ticket link opens the drawer; edit `latitude` inline (B8) from `strict` to `standard`; apply the approve-once verb; block clears; the Ticket returns to `todo`.
- **Store to Grant to Agent (inside App / Project).** From an App or Project detail, open the "Storage and access" section (B11); the vault Store lists grants; click a grant, land on the Agent detail (B2); the agent's Involved-in shows the same Store back.
- **Template to instance to Ticket.** From the /agents zero-state FromTemplateModal (B5), fork `note-triager` into a project; land on the new Agent's detail (B6, triager shape); its inbox shows "0 decided, 0 pending"; add a note in Notes; next tick the triager files a Ticket; the Ticket's drawer names the note as the source (existing `provenance=note:...` field).
- **Agents header autonomy pill to Settings.** From the /agents PageHeader the override state pill (C6) is "on" or "off"; click, land on Settings, toggle, land back at /agents with the pill flipped. No third surface involved.

Any hop that returns a 404, a bare id, or an "open in a new tab" hack is a
build unit that has not landed. File it, do not paper over.

### 11.2 Final step: engage the override

```bash
cat > $WORKSHOP/.runtime/autonomy-override.json <<EOF
{"engaged":true,"reason":"post-migration; operator will re-enable when ready","engaged_at":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","engaged_by":"operator"}
EOF
```

---

## 12. Rejected / out of scope

- **A new "workshop-v2" doc tree.** The 2026-07-18 series is the current tree; a v2 rename would duplicate. Instead, banner v1 as superseded (C18).
- **Removing the `builder` runtime concept.** Considered. Rejected because the workbench (files/editor/preview/diff) is coherent as the `role: maker + project_slug` UI. The migration is to demote the runtime as the display axis, not to eliminate the record.
- **Renaming `task` and `job` code identifiers to `ticket` and `run`.** Entity-model §9.5 explicitly preserves ADR 0001. Do not litigate.
- **A separate assistant-loop runner** (dispatch_triggers.sh:493 `_dispatch_assistant_loop`). Deferred; the "still-unbuilt data-tier turn runner" note is accurate. When plaid-watcher / email-triager get promoted (C2), they become triagers using the existing triager runner; that closes the gap without an assistant runtime.
- **Flipping the bridge agent live.** PM.5. Operator has explicitly framed the current inert state as intended.
- **Concurrency unblock.** System-audit's own phase; C21 above is the placeholder.
- **A rules-engine DSL (Rego / Cedar).** entity-model §9.6 settles: `mint_grant + check` is the policy engine. Do not add.

---

## 13. Open questions for operator

Kept minimal. Items the operator answered on 2026-07-18 are removed; the
remaining set is what still needs a call.

1. **C2**: promote plaid-watcher and email-triager to `role: triager` using the `inbox-watcher` template shape, or delete both? Default recommendation: promote (they carry the operator's stated intent). Delete is safe if the intent is stale.
2. **C5** (default flip): when per-agent autonomy gates dispatch, existing agents with declared triggers should default to `supervised` (they were running before, don't surprise the operator by making them silent) or `reactive` (safer; operator promotes explicitly)? Default recommendation: `supervised` for agents shipping today with non-empty `triggers`, `reactive` for anything with `triggers: []`.
3. **CLAUDE.md rewrite in C18**: name entity-model as the current authority and demote workshop-v1 to historical? Default recommendation: yes.
4. **C19**: do the `wkshp_*` and `local_*` and `cloud_*` letta_tools stay in the codebase (rebound to the bridge agent), or come out with workshop-overseer? Default recommendation: keep, rebind to the bridge agent as the sole target (the bridge agent already has plaid + google-docs + discord capabilities per its def).
5. **Store host_app / host_project field on entity-model**: given the operator observation that a Store is ~1:1 with an App or Project (§10 What to preserve, B11), should `entity-model-2026-07-18.md` §8.3 gain a formal `Store.host_app | Store.host_project` field so B11 has a real key to fetch by, rather than a URI-prefix convention? Default recommendation: add the field, small mutation, one migration in `backend/services/stores.py`. This is a decision surface, not a mandate; nothing in this plan enacts it.
6. **N-series ADR minting (OQ-N2)**: should coherence-plan units that land decisions (e.g. the vault-read-only posture in N1) mint additional ADRs at `docs/decisions/`? Default recommendation: yes, one per load-bearing invariant that survives the current sweep.
