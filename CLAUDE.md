# Workshop — repo conventions

Personal-ops monorepo: `backend/` (FastAPI + Letta), `frontend/` (Vite + React + TS), `mobile/` (Expo), `apps/`, `shared/`, `scripts/`, `infra/`.

## Working agreement

- This is a single-operator system. **Do not add multi-tenant guardrails** — no allowlists, audit journals, mention-gating, rate-limiting, or confirmation prompts beyond what a single user needs. (See user-scope CLAUDE.md.)
- Autonomous work goes through the completion gate: `scripts/reviewer.sh` (or the work-reviewer subagent) must approve before any TODO item is marked `[x]`. The worker does not judge its own completion.
- No committed stubs: no new `TODO`/`FIXME`/`NotImplementedError`/`pass # stub`/`...` placeholders. Too big for one tick → mark the task `[~] BLOCKED` and propose sub-tasks.

## UI / frontend — Design System is mandatory

**Every change under `frontend/` and `mobile/` MUST follow [`frontend/CLAUDE.md`](frontend/CLAUDE.md)** — the Workshop Design System. In short: build from the `src/ui` primitives + `src/styles/tokens.css`; no inline hex colors; lucide icons only (never emoji); five status kinds; Edit-mode for config/money writes; `ConfirmDialog` for destructive actions; all four list states (loading/empty/error/overflow). Default theme is dark.

Before claiming a UI task done: `bash scripts/check_design.sh` and `cd frontend && npx tsc --noEmit`. The reviewer gate fails on `design_violations` (new inline hex / emoji / `not-allowed` in changed `.tsx`).

Design new screens to the system from the start — there is no "systematize it later" pass.
