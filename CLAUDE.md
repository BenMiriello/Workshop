# Workshop — repo conventions

Personal-ops monorepo: `backend/` (FastAPI + Letta), `frontend/` (Vite + React + TS), `mobile/` (Expo), `apps/`, `shared/`, `scripts/`, `infra/`.

## Working agreement

- This is a single-operator system. **Do not add multi-tenant guardrails** — no allowlists, audit journals, mention-gating, rate-limiting, or confirmation prompts beyond what a single user needs. (See user-scope CLAUDE.md.)
- Autonomous work goes through the completion gate: `scripts/reviewer.sh` (or the work-reviewer subagent) must approve before any TODO item is marked `[x]`. The worker does not judge its own completion.
- No committed stubs: no new `TODO`/`FIXME`/`NotImplementedError`/`pass # stub`/`...` placeholders. Too big for one tick → mark the task `[~] BLOCKED` and propose sub-tasks.
- Ephemeral files (smoke tests, throwaway probes, scraped output, ad-hoc working notes) go in a `scratch/` or `tmp/` folder (anywhere in the tree — both are gitignored). Do NOT drop stray `test_*` / `tmp_*` files into source dirs. Per-project working trees (`projects/*`, `build-agents/`, `branches/`) are gitignored too — only the registries (`projects.json`, `build_agents.json`) and `projects/_templates/` are tracked. Durable docs belong in `docs/`.
- Commit your own work as you go; never leave it uncommitted. When a background agent's unrelated WIP is in the tree, stage only your files so commits stay isolated.

## Platform design (v1)

The platform model, agent roles, chat, workbench/launcher, UI kit, marketplace, and the deployment/isolation/safety design live in [`docs/workshop-v1/`](docs/workshop-v1/) (start at `00-overview.md`). Apps and Projects are distinct, linkable concepts (neither requires the other); Deployment is its own concept. Read these before touching the agents/projects/chat/apps systems.

## UI / frontend — Design System is mandatory

**Every change under `frontend/` and `mobile/` MUST follow [`frontend/CLAUDE.md`](frontend/CLAUDE.md)** — the Workshop Design System. In short: build from the `src/ui` primitives + `src/styles/tokens.css`; no inline hex colors; lucide icons only (never emoji); five status kinds; Edit-mode for config/money writes; `ConfirmDialog` for destructive actions; all four list states (loading/empty/error/overflow). Default theme is dark.

Before claiming a UI task done: `bash scripts/check_design.sh` and `cd frontend && npx tsc --noEmit`. The reviewer gate fails on `design_violations` (new inline hex / emoji / `not-allowed` in changed `.tsx`).

Design new screens to the system from the start — there is no "systematize it later" pass.
