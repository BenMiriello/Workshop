# Platform redesign: implementation plan

Status: build plan, 2026-07-12, reviewed and finalized. Authoritative build sequence. Written to the locked decisions in `proposal.md` section 0 and grounded in the codebase inventory.

Legend: ADD (new file/symbol), MOD (modify), DEL (delete + all call sites), MIG (on-disk migration).

Global rules for the build:
- One migration branch (`redesign/platform-core`). Commit per shippable step. Preserve the unrelated voice-mode WIP already in the tree; stage only redesign files.
- Every frontend step: `cd frontend && npx tsc --noEmit` and `bash scripts/check_design.sh` must pass. src/ui primitives, tokens (no hex), lucide (no emoji), five status kinds, edit-mode for writes, all four list states on new pages.
- No committed stubs. Too big for one step, mark `[~] BLOCKED` and sub-divide, do not leave placeholders.
- Single-user: no allowlists/audit/confirm-gates. Iteration caps and budgets on unattended loops are correctness bounds, allowed.
- Do not touch the bridge agent's Letta path in this migration. That means: no changes to `per_channel_agents.py`, `global_memory.py`, the `/per-channel/*` and `/global-memory/*` endpoints, the `_letta_get/_letta_post/_letta_patch` helpers and block/archival reads-writes in `supervisor.py`, `letta_register.py`, `letta_tools/*`, or the search internals of `knowledge.py`. Letta stays the assistant memory engine; full removal is the deferred next phase.
- Live-system cutover guard: before modifying `cron_agent.sh`, `scripts/ralph.sh`, or `scripts/project_ralph.sh`, set the global pause (`POST /agents/autonomy/disable`, flag file `AUTONOMY_DISABLED`). Re-enable only after the phase's verify steps pass. The crontab keeps firing every 20 minutes against the live tree; never leave those scripts half-edited with autonomy on.
- Backend restarts go through `scripts/restart_backend.sh` (systemd user service, env overlays intact).
- The bridge agent smoke check (run after every phase that touches `backend/routes/agents.py`, `supervisor.py`, or restarts the backend): `GET /agents/per-channel/list` returns the existing mappings; send one Discord message on the chat bridge and confirm a reply; confirm the supervisor heartbeat file for the supervisor agent updates within its interval.
- No em-dashes, no emojis, anywhere (docs, code strings, UI copy).

Naming is locked: **Job** (not Run), **Store**, **Agent**, **Grant**, markdown agent definitions. No chat-to-project promotion, no blank-builder, interactive chat turns are not Jobs.

---

## Phase 1: Job object + one autonomy vocabulary

Goal: one record type for every bounded execution, one trigger vocabulary. Phase 1 unifies the record and the vocabulary; the backing store for triggers moves to agent definition files only in Phase 2 (definitions do not exist yet, so Phase 1 adapts the existing stores).

**ADD**
- `backend/services/jobs.py`: the Job store. Record `{id, agent_id, project_slug?, chat_id?, trigger, status, started_at, ended_at?, exit_code?, tasks_done?, tasks_failed?, evidence?, budget?}`. Status enum `queued | running | awaiting_input | done | failed | cancelled`. Append-only log `.runtime/jobs.jsonl` + an index helper for tail/filter by agent/project/status. Writers call `jobs.start()/finish()`. Include a CLI shim (`python -m backend.services.jobs start|finish ...`) for shell writers.
- `backend/services/triggers.py`: the unified trigger vocabulary. Trigger kinds `manual | cron | event | loop`; `loop` carries `max_iterations` + `budget` + a completion-gate reference. Phase 1 backing stores, via adapters: project triggers live in the project registry (replacing `autonomy.mode`), agent triggers are READ from existing Letta metadata (`autonomy_mode`, `cron_expression`, `event_sources`) and presented in trigger shape. Dispatch entrypoint creates a Job for a firing trigger. Phase 2 swaps the agent-side backing store to definition files; the adapter seam is designed for that swap.
- `backend/routes/jobs.py`: `GET /jobs` (list/filter by agent_id, project_slug, status, limit), `GET /jobs/{id}` (detail). Mounted as a new top-level router.

**MOD**
- `backend/routes/agent_runs.py`: becomes a thin alias, `GET /agents/runs` re-serving `/jobs` output in the old shape. It MUST stay mounted before `agents.router` (main.py:111, the mount-order comment: `/agents/runs` wins over `/agents/{agent_id}` only because of this ordering). Do not delete or reorder until Phase 5.
- `scripts/cron_agent.sh`: each dispatched `scripts/ralph.sh` task execution emits one Job (trigger `cron` or `loop`) via the jobs CLI shim; the event-drain path (Phase 1 of the script) emits Jobs with trigger `event` instead of appending `agent-runs-completed.jsonl`. STOP appending the per-tick aggregate to `agent-runs.jsonl` (lines 317-320). Do NOT touch `.runtime/agent-runs-pending.jsonl`: it is the event queue the script consumes (lines 102-206), not a run ledger; the queue mechanics stay exactly as they are.
- `scripts/project_ralph.sh`: emit a Job (`trigger=loop`, `project_slug` set) instead of writing `<projectdir>/.runtime/ralph-run.json`. Run-state is a Job in `running` status; `finish()` on exit.
- `backend/services/supervisor.py`: ADDITIVE ONLY. Wrap each heartbeat work cycle in `jobs.start()/finish()`. Keep writing `.runtime/heartbeats/{id}.json` for liveness. Zero changes to the Letta calls (persona/core-memory/blocks/archival reads and writes, `letta_tools` registration): those lines are the the bridge agent-adjacent always-on runtime.
- `backend/routes/projects.py` (autonomy endpoints ~442-549): one-time translate of `autonomy.mode {off,manual,continuous}` in the project registry to a `triggers: [...]` list (`off` = manual only; `continuous` = one `loop` trigger). `GET /{slug}/autonomy` returns the trigger view; `PUT /{slug}/autonomy` writes the trigger list; `POST /{slug}/autonomy/run` creates a manual Job; `GET /{slug}/autonomy/log` reads Jobs filtered by `project_slug`. Delete the bespoke `ralph-run.json` reads.
- `backend/routes/autonomy.py`: keep the global pause switch (`AUTONOMY_DISABLED`), supervisor pause/resume, and Ollama/GPU endpoints unchanged; status endpoint reports from Jobs. No new enum.
- `backend/routes/agents.py`: READ-SIDE ONLY in this phase. Expose the Letta-metadata autonomy fields as a `triggers` list in list/detail responses (via the triggers.py adapter) so the UI can move. Do NOT remove the metadata fields, do NOT stop `_update_subscriptions` writing `.runtime/agent-subscriptions.json` (cron event-drain and `event_dispatch.py` both read that file). Repoint the `GET /agents/{agent_id}/runs` merge at line ~816 to read `jobs.jsonl` + heartbeats instead of the old files (same response path, new source).
- `scripts/autonomy_report.sh`: recompute its tick/event stats from `.runtime/jobs.jsonl` instead of `agent-runs.jsonl` + `agent-runs-completed.jsonl`.
- `frontend/src/pages/Runs.tsx` -> `frontend/src/pages/Jobs.tsx`: reads `/api/jobs`, renders Job cards (agent, trigger, project binding, status kind, duration). All four list states. Update the import in `frontend/src/pages/Activity.tsx` (Activity itself survives until Phase 5).
- `frontend/src/components/AutonomyPanel.tsx`: `Mode` enum deleted; renders the project trigger list (add/remove cron, event, loop-with-cap) as the single autonomy control, writing via `PUT /{slug}/autonomy`. Edit-mode + explicit Save (config write).

**MIG**
- One-time script `scripts/migrate_jobs.py`:
  - Import `.runtime/agent-runs-completed.jsonl` records as Jobs (they carry `agent_id`; trigger `event`).
  - `.runtime/agent-runs.jsonl` is a per-tick AGGREGATE with no `agent_id`; it cannot be represented as Jobs. Do not import; rename to `agent-runs.jsonl.migrated` and keep for history.
  - Rename `agent-runs-completed.jsonl` to `.migrated` after import.
  - LEAVE `agent-runs-pending.jsonl` alone (live queue).
  - Per-project `ralph-run.json`: import only if a run is genuinely live (pid alive), else skip; remove the files after.
  - `heartbeats/*.json` stay (liveness files, not Jobs).
  - Run the migration with autonomy paused; nothing else may be appending during the cutover.

**Verify:** with autonomy paused: run migration, restart backend, `GET /jobs` and `GET /agents/runs` (alias) both return the imported records; `POST /{slug}/autonomy/run` creates a manual Job visible on the Jobs page with correct binding; AutonomyPanel adds and removes a loop trigger and the registry reflects it. Re-enable autonomy, wait one cron tick (or run `cron_agent.sh` by hand), confirm one Job per dispatched task and no writes to the old files. The bridge agent smoke check (supervisor.py and agents.py were touched). tsc + check_design.sh.

---

## Phase 2: One Agent registry + surface (Letta kept as memory engine)

Goal: one Agent concept, one registry, one config surface, `runtime` as a field. No Assistants/Builders conceptual split. Letta remains the assistant runtime's memory engine (removal is the deferred phase).

**ADD**
- `backend/services/agents_registry.py`: the unified Agent registry over markdown definition files. Definition = frontmatter (`name, runtime: builder|assistant, purpose, capabilities: [grant refs], triggers: [...], limits: {max_iterations, budget}, memory: letta`) + body (persona/instructions). User scope `~/workshop/agents/<slug>.md`; project scope `projects/<slug>/agents/<name>.md`. API: `list_agents` (both scopes, both runtimes), `get`, `create`, `update` (writes the file), `retire`. Definitions are DECLARATIVE ONLY: mutable runtime state (`state`, `claude_session`, `last_active`) does NOT go in frontmatter; it stays in `.runtime/build_agents.json` (or a renamed `.runtime/agent-state.json`) keyed by slug.
- EXCLUSION RULE, enforced in `list_agents`: the bridge agent per-channel Discord agents (created by `per_channel_agents.py`, identifiable by their per-channel metadata/naming) are NOT registry members. They get no definition files, are not listed in the roster (or at most surface as a read-only count), and are never created/updated/retired through the registry.

**MOD**
- `backend/services/build_agents.py`: keep the execution mechanics (cwd resolution, `claude -p` session handling, `ensure_session_id`, `reset_session`, `touch`) and the runtime-state file; the registry-of-record role (name/purpose/kind) moves to `agents_registry`. `create_blank` deleted in Phase 3.
- `backend/routes/agents.py`: list/detail endpoints return unified agents (both runtimes) from `agents_registry`. Keep the Letta-backed memory/budget/spinoff endpoints working unchanged for `runtime: assistant` agents. Keep ALL of `/agents/build*` execution endpoints (lines ~100-554) working: `AgentBuild.tsx` makes ~30 calls against them. Keep `/per-channel/*` and `/global-memory/*` endpoints byte-for-byte untouched. Add `GET /agents/{id}/jobs` sourced from `jobs.py` (declare static segments before the `/{agent_id}` catch-all; FastAPI matches in declaration order, same trick as `/agents/runs`). Trigger WRITES now go to definition frontmatter; on every trigger write, regenerate `.runtime/agent-subscriptions.json` from definitions (the file and its readers, cron event-drain + `event_dispatch.py`, stay; only the writer moves). After this lands, the Letta-metadata autonomy fields stop being written; leave existing metadata in place on Letta rather than mass-deleting it.
- `backend/services/triggers.py`: swap the agent-side adapter from Letta metadata to definition frontmatter.
- `frontend/src/pages/Agents.tsx`: remove the `Kind = all|data|build` segmentation as the primary axis; one roster, `runtime` is a badge (`Tag`, categorical, not status). Card shows runtime, purpose, trigger summary, last Job, next scheduled. Fix the dead `/agents/new` link with a real create route/flow.
- `frontend/src/pages/Settings.tsx` line ~881: the second dead `/agents/new` link ("create from blank") gets the same fix (and its inline `style={{color}}` goes; use a primitive).
- `frontend/src/pages/AgentConfig.tsx` + config half of `AgentBuild.tsx` -> one `AgentDetail.tsx`: the definition (editable, edit-mode + Save), triggers + limits, grant set, Job history filtered to this agent. `AgentBuild.tsx` keeps only its chat/work pane.
- `frontend/src/App.tsx` router: `/agents` (roster), `/agents/new` (create), `/agents/:slug` (detail), `/agents/:slug/chat` (build chat pane for builder runtime). Keep `/agents/build/:slug` as a redirect to `/agents/:slug/chat` (deep links, mobile shell). Update `appNav.tsx` crumbs for the new paths.

**MIG**
- `scripts/migrate_agent_defs.py`: generate markdown definition files from `.runtime/build_agents.json` (runtime: builder; project scope where `project_slug` is set, else user scope) and from the Letta agent list EXCLUDING per-channel agents and the supervisor's internal plumbing (runtime: assistant; persona body from Letta persona block; triggers from metadata). Keep `.runtime/build_agents.json` in place as the runtime-state file (it stops being the registry of record but is not deleted).

**Verify:** a builder and an assistant appear in one roster with runtime badges; editing a definition writes the markdown file; a trigger set on a definition drives a Phase-1 Job on schedule; `.runtime/agent-subscriptions.json` regenerates and the cron event-drain still dispatches; `/agents/build/:slug` redirects; `/agents/new` works from both former dead links. The bridge agent smoke check (agents.py touched): per-channel list intact, Discord round-trip works, per-channel agents are absent from the roster. tsc + check_design.sh.

---

## Phase 3: Chat loose coupling; delete blank-builder (no promotion pipeline)

Goal: chat and project are independent, loosely-coupled nouns. No conversion pipeline, no promotion gesture, no blank-builder. Chats are discoverable. (Locked decision 2: do not build any promote-to-project action.)

**DEL**
- `frontend/src/lib/blankBuilder.ts` (`createBlankBuilder`) and its only importer usage in `frontend/src/pages/ProjectNew.tsx` (the "start with chat" button + `startWithChat`, lines ~67-77, 144-152).
- `backend/routes/agents.py` `POST /build/blank` (lines ~127-131) and `backend/services/build_agents.py` `create_blank()` (lines ~183-207).
- `backend/services/chat_context.py` `NewProjectContextProvider` (~595-656) and its `new-project` entry in `_PLATFORM_PROVIDERS` (~821); update the module docstring that names the flow.

**MOD**
- `frontend/src/pages/ProjectNew.tsx`: a single direct "create project" form (name, template). No chat path.
- `backend/services/chat_store.py`: a thread with `context_key = general` persists indefinitely and can be re-bound to `project:<slug>` / `app:<name>` / `agent:<slug>` via the existing rescope path (`POST /threads/{id}/rescope`): a binding change, not a conversion. Stop writing `converted_from/converted_at`; tolerate them on read (existing thread files carry them).
- `backend/routes/chat.py`: ADD `GET /chat/threads/all` (cross-context list: id, context_key, title, agent, updated_at). Declare it BEFORE the existing `GET /threads/{thread_id}` (line ~169), or `all` is captured as a thread_id. Existing scoped `GET /threads?context=` stays for in-context lists.
- `frontend/src/pages/Chats.tsx` (ADD): the discoverable thread list across all contexts, search + filter by context/agent/recency. Router `/chats`. Four list states; no promote action anywhere.

**MIG**
- Tiny script or startup shim: rescope any existing threads with `context_key = new-project` to `general` so no thread points at a deleted provider.

**Verify:** `grep -r "blankBuilder\|create_blank\|new-project" backend/ frontend/src/` returns no live references (docs excluded); a general chat survives restart and rescopes to a project without content change; Chats page lists threads across contexts and search works; ProjectNew still creates a project directly. tsc + check_design.sh.

---

## Phase 4: Store + Grant model

Goal: the data/capability primitive. Apps own Stores; grants are scoped, per-invocation capability tokens; no hardcoded domain concept.

Symbol disambiguation, load-bearing: `chat_context.capability_for()` (line ~880) is a TOOLS decision for chat providers; `permissions.assistant_capabilities()` is the authz concept. They are different. Phase 4 adds a new `check()` in `permissions.py`; do not rename, merge, or repurpose `chat_context.capability_for` here. Keep the signatures of `assistant_capabilities` (called by `chat_context.ContextProvider.capability_note`, ~541) and `can_recall` (called by `knowledge.recall`) working unchanged; the store layer is additive.

**ADD**
- `backend/services/stores.py`: the Store abstraction. `Store {uri: workshop://store/<app>/<name>, type?: str, owner_app, path}`. Registry of declared stores (from app manifests). Ops: `declare`, `list` (optionally by `type`), `export(uri)` (mandatory portable dump). Wrapping is REGISTRATION, not relocation: existing `.runtime/` files (budget.json, commute/*, plaid-items.json, knowledge dirs, secrets) stay where they are with their modes intact (plaid-items.json stays 0600).
- `backend/services/permissions.py` extensions: resource-scoped grants `{from, to, store_uri|subtree, verbs:[read,write,append], caveats:{expiry?, filter?}}`, `mint_grant`, `attenuate`, `revoke`, and `check(token, store_uri, verb)` enforced per provider invocation. Keep `capability_permitted`/`authorize`/`load_grants`/`validate_grants` for the app-level layer; `validate_grants` must still accept the existing grants.json shape (currently empty, so migration risk is low, but do not break the loader).
- `backend/routes/grants.py`: a real grants router (`GET/PUT /grants`, `POST /grants/mint`, `POST /grants/revoke`, `GET /stores?type=`). The grant endpoints currently live in `base_apps.py` (Grants.tsx hits `GET/PUT /api/base-apps/grants`): keep `/base-apps/grants` as a thin alias until Phase 5; `/base-apps/manifests` stays in base_apps (it is not a grants concern).
- `frontend/src/components/GrantPicker.tsx`: broker/picker. When an app or agent needs "a Store of type X", present matching stores, user picks, mint scoped grant. This is the interop mechanism, not a confirmation gate; no per-action re-prompting.

**MOD**
- `backend/apps.json` / `services/manifests`: `provides` entries become declared Stores with optional `type`; `needs` become grant requests resolved through the broker.
- `backend/services/knowledge.py`: knowledge scopes register as platform-owned Stores accessed via the grant mechanics (closes the knowledge.read/write TODO). Registry wrapper ONLY: do not touch `search`, `put_note`, `delete_note`, `_plaintext_search`, `_letta`, `_archive_id`, or any Letta archive/passage internals. The semantic index stays Letta-backed until the deferred Letta-removal phase.
- Per-app MCP provider surface (`AppSpec.mcp` in `app_registry.py`, dataclass ~line 68; merged by `installed_mcp_servers()` ~442): formalize as the provider interface, add `export` as a required op in the manifest. Only Plaid is wired today; establish the pattern there.
- `frontend/src/pages/settings/Grants.tsx`: point at `/api/grants`, render store-scoped grants with verbs + caveats.

**Verify:** an agent Job requesting "read on a Store of type Note" gets a scoped token via the picker and the provider checks the token per call; `export(uri)` on the budget store produces a portable file; revoking a grant blocks the next call; the old `/base-apps/grants` alias still answers; `knowledge.search` behavior is byte-identical before/after (same query, same results); chat capability notes and `can_recall` behavior unchanged. tsc + check_design.sh.

---

## Phase 5: Surfaces (home hub, retire Activity, settings split)

Goal: the top-level views implied by the new model. Depends on 1-4.

**ADD**
- `frontend/src/pages/Home.tsx`: the hub. Recent chats, active/recent Jobs, pinned apps + projects, items needing attention (Job in `awaiting_input`). Becomes `/`; `/apps` stays alive as the launcher page (a nav destination, no longer the root).
- A standard app-settings slot in the app frame so each app renders its own user-facing settings inside the app (consistent with the one-header-per-app rule in frontend/CLAUDE.md).

**MOD**
- `frontend/src/App.tsx` + `frontend/src/components/appNav.tsx`: nav destinations become Home, Chats, Projects, Apps, Agents, Settings. Update `NODES` (lines ~32-52), breadcrumb resolution, and the width classes keyed by pathname (App.tsx ~57-75) for the new routes.
- `frontend/src/pages/Settings.tsx`: SPLIT. Platform/user settings stay (Account, Devices, Appearance, BaseApps, System, grants overview). App-specific sections (LocationSection, HealthSection, CommuteSection, FinanceSection) MOVE into their apps via the app-settings slot; move the existing section components, do not rewrite them. `AutonomySection` (lines ~1018-1304): the global pause + Ollama/GPU controls move to the Jobs/trigger overview under Agents; they must remain reachable (the `AUTONOMY_DISABLED` switch is operationally load-bearing).
- Project detail page: gains the project-config surface (state, sensitivity, project-scoped agent definitions + triggers = the autonomy view, grants the project holds, knowledge, deploy seam).
- Settings > System panel: mount the existing `Events.tsx` and `Services.tsx` components (move, do not rewrite).

**DEL**
- `frontend/src/pages/Activity.tsx` + the `/services`, `/events`, `/runs` redirects (App.tsx ~128-130) + `NODES["/activity"]` (appNav.tsx ~49).
- Backend `GET /agents/runs` alias (`backend/routes/agent_runs.py` + its mount at main.py:111): first `grep -r "agents/runs" frontend/src/ scripts/ backend/` to confirm zero remaining callers, then remove the router and its mount. The mount-order constraint dies with it.
- `/base-apps/grants` alias in `base_apps.py`: same grep-then-remove discipline (`grep -r "base-apps/grants"`).

**Verify:** Home renders as the operational hub at `/`; Activity is gone with its content re-homed and old URLs handled (redirect or 404 by choice, but not a blank shell); an app shows its own settings; Settings no longer carries per-app config; project detail owns project config; the global autonomy pause is reachable and works; the two removed aliases have zero callers. Full-width sweep per the UI rule (~1440/1024/768/390). The bridge agent smoke check after the final backend restart. tsc + check_design.sh.

---

## Risk register

The highest-risk steps and the guard for each:

1. **Breaking the the bridge agent Discord bridge via the Letta path.** Phases 1-2 touch `supervisor.py` and `routes/agents.py`, both of which sit next to load-bearing Letta code (per-channel agents, global memory, heartbeat runtime). Guard: the global rule above (frozen file list), additive-only edits in supervisor.py, per-channel agents excluded from the unified registry, and the the bridge agent smoke check after every phase that touches agents.py/supervisor.py or restarts the backend.
2. **`/agents/runs` route shadowing.** The alias only resolves because `agent_runs.router` is mounted before `agents.router` (main.py:111); reordering or premature deletion makes `/agents/runs` fall into `GET /agents/{agent_id}` with `agent_id="runs"`. Guard: alias kept, mount order untouched, removal only in Phase 5 after a zero-caller grep.
3. **Cron writing to a half-migrated ledger.** The crontab fires `cron_agent.sh` every 20 minutes against the live tree, and `agent-runs-pending.jsonl` is a live consumed queue, not a ledger. Guard: `AUTONOMY_DISABLED` pause bracketing the Phase 1 script edits and migration; the pending queue is explicitly excluded from the migration; old ledgers are renamed `.migrated`, never deleted, and every reader (`agents.py:816`, `autonomy_report.sh`) is repointed in the same phase.
4. **Knowledge search silently degrading.** `knowledge.py` semantic search is Letta-backed; Phase 4 wraps knowledge scopes as Stores. Guard: registry wrapper only, search/note internals frozen, and a before/after identical-results check in the Phase 4 verify. Promotion of the plaintext index stays in the deferred Letta-removal phase.
5. **Event-driven agents going deaf.** `.runtime/agent-subscriptions.json` is read by both the cron event-drain and `event_dispatch.py`. Phase 1 must not stop `_update_subscriptions`; Phase 2 moves only the writer (regenerated from definition frontmatter), never the file or its readers. Guard: explicit in both phases; Phase 2 verify includes an event-drain dispatch.
6. **Grants endpoint breakage.** Grants are served from `base_apps.py`, not a grants router; Grants.tsx points at `/api/base-apps/grants`. Guard: new router ships with the `/base-apps/grants` alias intact through Phase 4; frontend repointed in the same phase; alias removed in Phase 5 only after a zero-caller grep.

---

## Explicitly deferred (own efforts, not this build)

- **Full Letta retirement**: swap assistant memory to a Store of type `Memory`, promote the plaintext knowledge index to primary, migrate the bridge agent per-channel agents off Letta. Dedicated phase, tested against the chat bridge in isolation. This is next.
- **Task-management system**: per `task-management-exploration.md`. The Job object and per-project Task scope are reserved here; the task model itself is a separate build. (That doc's "promote to task" gesture is part of the deferred design and is NOT built in this round; locked decision 2 removes promotion from this migration entirely.)
  UPDATE 2026-07-13: now fully designed in `autonomy-task-management.md` (assessment of the current chat/TODO.md/.tasks.json/loop drift, canonical Task model with project AND agent scopes, task-patch + task-proposal card emission from chat, the background trigger dispatcher, Job task_id/parent_id linkage, acceptance gates + awaiting_input, and a five-phase file-scoped build plan T1-T5). Ready to schedule; T2 sequences with the review-addendum chat fixes P1-P3.
  UPDATE 2026-07-13 (b): superseded-and-extended by `orchestration-and-autonomy.md`, the unified design folding autonomy + task-management INTO agent orchestration (orchestrator-worker sweeps over the Job substrate with read-parallel/write-serial fan-out policy; the task store as both interface and dependency-aware work queue with atomic claim + discovered-from + awaiting-input elicitation; a sleep-time memory-consolidation organ giving episodic/semantic/procedural learning over existing files + Letta recall; an autonomy/depth/fanout preset cascade with per-scope/task/invocation granularity; where-it-applies across chat kinds; a seven-phase plan O1-O7 that adopts T1-T5 as its spine and adds the memory organ + config surfaces). Build from that doc; T1-T5 = O1-O4 + O7.
- **Hosting / versioning / deploy / marketplace** deep design.
- **Multi-tenant / commercial** architecture.

## Build order note

Phases 1 and 2 are the load-bearing unifications and run first, in order (Phase 1's trigger adapters are designed for Phase 2's backing-store swap; do not interleave). Phase 3 (deletions) follows immediately. Phase 4 (Store/Grant) is independent of 3 and can be built in parallel by a separate worker on the same branch ONLY with file-level separation: Phase 4 owns `permissions.py`, `stores.py`, `knowledge.py` (registration only), `base_apps.py`, `apps.json`, `Grants.tsx`; Phase 3 owns `chat_context.py`, `chat_store.py`, `chat.py`, `ProjectNew.tsx`, `blankBuilder.ts`. No shared files, so no coordination beyond the branch. Phase 5 depends on 1-4. Each phase leaves the system coherent and shippable, and each phase's verify includes the the bridge agent smoke check where flagged.
