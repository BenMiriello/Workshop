# Workshop — repo conventions

Personal-ops monorepo: `backend/` (FastAPI + Letta), `frontend/` (Vite + React + TS), `mobile/` (Expo), `apps/`, `shared/`, `scripts/`, `infra/`.

## Working agreement

- This is a single-operator system. **Do not add multi-tenant guardrails** — no allowlists, audit journals, mention-gating, rate-limiting, or confirmation prompts beyond what a single user needs. (See user-scope CLAUDE.md.)
- Autonomous work goes through the completion gate: `scripts/reviewer.sh` (or the work-reviewer subagent) must approve before any TODO item is marked `[x]`. The worker does not judge its own completion.
- No committed stubs: no new `TODO`/`FIXME`/`NotImplementedError`/`pass # stub`/`...` placeholders. Too big for one tick → mark the task `[~] BLOCKED` and propose sub-tasks.
- Ephemeral files (smoke tests, throwaway probes, scraped output, ad-hoc working notes) go in a `scratch/` or `tmp/` folder (anywhere in the tree — both are gitignored). Do NOT drop stray `test_*` / `tmp_*` files into source dirs. Per-project working trees (`projects/*`, `build-agents/`, `branches/`) are gitignored too — only the registries (`projects.json`, `build_agents.json`) and `projects/_templates/` are tracked. Durable docs belong in `docs/`.
- Commit your own work as you go; never leave it uncommitted. When a background agent's unrelated WIP is in the tree, stage only your files so commits stay isolated.

### A UI change is not delivered until the operator's origin serves it

Two origins serve this UI and they update differently. **Ask which one the
operator is on before saying a frontend change is done, and rebuild the built
one regardless:**

| origin | serves | updates by |
|---|---|---|
| `$WORKSHOP_LOCAL_ORIGIN` → `:5173` | Vite, from SOURCE | instantly, no build |
| `$WORKSHOP_PUBLIC_ORIGIN` → `:8002` | the built `frontend/dist` | **`bash scripts/build_workshop_public.sh`** |

(Both are set in `~/.config/workshop/instance.env`; `scripts/lib/instance.sh`
loads them. They are this deployment's, so they are not written down here.)

Default to running the build after any change under `frontend/`. It takes
seconds, the edge serves `dist` from disk so nothing restarts, and skipping it
is invisible to you and total to the operator.

**`check_design.sh` printing "the public bundle is STALE" means the operator may
be looking at old code. It is not noise. Never report a UI task complete over
it.** On 2026-08-09 a whole session of width fixes was correct in source, never
built, and the operator said three times that it wasn't done — while the stale
warning fired after every commit and went unread. Builds now hold a lock
(`build_workshop_public.sh`), so the warning is trustworthy: concurrent builds
used to race the stamp and make it fire unconditionally.

## Platform design (current: 2026-07-18)

The authoritative model lives in [`docs/platform-redesign/entity-model-2026-07-18.md`](docs/platform-redesign/entity-model-2026-07-18.md) (start there) with the phase docs riding on top of it: `autonomy-model-*`, `latitude-model-*`, `primitive-roles-*`, `chat-scope-design-*`, `chat-work-linkage-*`, `resource-access-interface-spec-*`, and the sequenced `final-plan-agent-generalize-and-bridge-fold-2026-07-16.md` + `coherence-migration-plan-2026-07-18.md`. Agents are keyed by `role` (closed enum: maker/reviewer/triager/keeper/broker), NOT by `runtime`. Apps and Projects are distinct linkable concepts. Read these before touching the agents/projects/chat/apps systems.

The earlier v1 design set at [`docs/workshop-v1/`](docs/workshop-v1/) and the 2026-07-11 redesign at [`docs/agent-chat-redesign/`](docs/agent-chat-redesign/) are HISTORICAL. Both carry supersession banners; do not update them and do not treat their assistant/builder axis as current.

## UI / frontend — Design System is mandatory

**Every change under `frontend/` and `mobile/` MUST follow [`frontend/CLAUDE.md`](frontend/CLAUDE.md)** — the Workshop Design System. In short: build from the `src/ui` primitives + `src/styles/tokens.css`; no inline hex colors; lucide icons only (never emoji); five status kinds; Edit-mode for config/money writes; `ConfirmDialog` for destructive actions; all four list states (loading/empty/error/overflow). Default theme is dark.

Before claiming a UI task done: `bash scripts/check_design.sh`, `cd frontend && npx tsc --noEmit`, and `cd frontend && npm test` (vitest; pure logic modules only — no DOM harness). The reviewer gate fails on `design_violations` (new inline hex / emoji / `not-allowed` in changed `.tsx`).

Design new screens to the system from the start — there is no "systematize it later" pass.
