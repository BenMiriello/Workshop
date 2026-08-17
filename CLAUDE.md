# Workshop — repo conventions

Personal-ops monorepo: `backend/` (FastAPI + Letta), `frontend/` (Vite + React + TS), `mobile/` (Expo), `apps/`, `shared/`, `scripts/`, `infra/`.

## Working agreement

- This is a single-operator system. **Do not add multi-tenant guardrails** — no allowlists, audit journals, mention-gating, rate-limiting, or confirmation prompts beyond what a single user needs. (See user-scope CLAUDE.md.)
- Autonomous work goes through the completion gate: `scripts/reviewer.sh` (or the work-reviewer subagent) must approve before any TODO item is marked `[x]`. The worker does not judge its own completion.
- No committed stubs: no new `TODO`/`FIXME`/`NotImplementedError`/`pass # stub`/`...` placeholders. Too big for one tick → mark the task `[~] BLOCKED` and propose sub-tasks.
- Ephemeral files (smoke tests, throwaway probes, scraped output, ad-hoc working notes) go in a `scratch/` or `tmp/` folder (anywhere in the tree — both are gitignored). Do NOT drop stray `test_*` / `tmp_*` files into source dirs. Per-project working trees (`projects/*`, `build-agents/`, `branches/`) are gitignored too, and so are the registries: `.runtime/projects.json` and `.runtime/build_agents.json` are instance-local and NOT tracked. Only `projects/_templates/` is tracked. Durable docs belong in `docs/`.
- Commit your own work as you go; never leave it uncommitted. When a background agent's unrelated WIP is in the tree, stage only your files so commits stay isolated.

### Finished a change? Run `bash scripts/apply.sh`

That is the whole instruction. It compares what is running against what is on
disk and fixes only what drifted: the built bundle the operator's origin serves,
the backend, the Vite dev server, the public edge. It takes no arguments, it is
idempotent, and it is **safe to run at any time, from any agent, concurrently
with any other agent**. You do not need to coordinate and you do not need to ask.

`bash scripts/apply.sh --check` reports drift without changing anything.

Mechanics, costs and troubleshooting: [`docs/applying-changes.md`](docs/applying-changes.md).
That file is the ONLY current source for how to apply a change. Restart and
rebuild commands in dated handoff docs and under `docs/platform-redesign/` are
historical; several describe behaviour that has since been fixed or reversed.
Do not follow them and do not update them.

**A UI change is not delivered until the operator's origin serves it.** The
operator uses `$WORKSHOP_PUBLIC_ORIGIN`, the BUILT one (settled 2026-08-11; do
not ask which origin he is on, it reads as not knowing the deployment). So
`check_design.sh` printing **"the public bundle is STALE" means the operator may
be looking at old code. It is not noise. Never report a UI task complete over
it.** On 2026-08-09 a whole session of width fixes was correct in source, never
built, and the operator said three times that it wasn't done, while the stale
warning fired after every commit and went unread. `apply.sh` is what clears it.

## Platform design (current: 2026-07-18)

The authoritative model lives in [`docs/platform-redesign/entity-model-2026-07-18.md`](docs/platform-redesign/entity-model-2026-07-18.md) (start there) with the phase docs riding on top of it: `autonomy-model-*`, `latitude-model-*`, `primitive-roles-*`, `chat-scope-design-*`, `chat-work-linkage-*`, `resource-access-interface-spec-*`, and the sequenced `final-plan-agent-generalize-and-bridge-fold-2026-07-16.md` + `coherence-migration-plan-2026-07-18.md`. Agents are keyed by `role` (closed enum: maker/reviewer/triager/keeper/broker). The older `runtime` axis (builder/assistant) is being retired, but as of 2026-08-16 it is still the live dispatch key at `chat_context.py:2033` and still branches the UI at `AgentDetail.tsx:672`; `_role_for_runtime` bridges the two. Do not write new code against `runtime`, and do not assume it is already gone. Retiring it is thread T2 of [`docs/platform-redesign/coherence-overhaul-2026-08-16.md`](docs/platform-redesign/coherence-overhaul-2026-08-16.md). Apps and Projects are distinct linkable concepts. Read these before touching the agents/projects/chat/apps systems.

The earlier v1 design set at [`docs/workshop-v1/`](docs/workshop-v1/) and the 2026-07-11 redesign at [`docs/agent-chat-redesign/`](docs/agent-chat-redesign/) are HISTORICAL. Both carry supersession banners; do not update them and do not treat their assistant/builder axis as current.

## UI / frontend — Design System is mandatory

**Every change under `frontend/` and `mobile/` MUST follow [`frontend/CLAUDE.md`](frontend/CLAUDE.md)** — the Workshop Design System. In short: build from the `@workshop/ui` kit primitives at `packages/ui` (import by package name) + its tokens at `packages/ui/src/styles/tokens.css`; no inline hex colors; lucide icons only (never emoji); five status kinds; Edit-mode for config/money writes; `ConfirmDialog` for destructive actions; all four list states (loading/empty/error/overflow). Default theme is dark.

Before claiming a UI task done: `bash scripts/check_design.sh`, `npm run typecheck` (from the repo root), and `cd frontend && npm test` (vitest; pure logic modules only — no DOM harness). The reviewer gate fails on `design_violations` (new inline hex / emoji / `not-allowed` in changed `.tsx`).

**Do NOT typecheck with `npx tsc`.** `tsc` is also the name of a deprecated stub package on npm, and npx resolves that ahead of the local TypeScript binary: it prints "This is not the tsc command you are looking for" and **exits 0**, so it reports success having checked nothing. This repo shipped on that no-op gate for months. `npm run typecheck` runs the real binary over both the kit and the frontend (~8s). Use it rather than a path to the binary: a root workspace install hoists typescript to the root `node_modules`, so `frontend/node_modules/.bin/tsc` exists on some checkouts and not others.

Design new screens to the system from the start — there is no "systematize it later" pass.
