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

**One surface it reports and never fixes: the database schema.** It compares the
tree's alembic head to the live `alembic_version`, and when the tree is ahead it
**declines the backend restart** and names the command to run.

**RUN THAT COMMAND. It is ordinary work, not an escalation.** This is a
development installation and a migration here is a migration in development: if
it is in the tree, apply it. Do not report the schema as "blocked" and hand the
command back, which reads as asking permission. The operator has corrected this
more than once, most recently on 2026-09-01: "in development you shouldn't treat
it like production, that's so fucking annoying to have you stopping short over
and over making me do extra work to remind you you can do things."

The reason apply.sh does not run alembic itself is ORDER, not danger. The
migration has to land BEFORE the restart, because a missing column fails at
query time and the import preflight cannot see it, which is how `15883681`
nearly took chat down on 2026-09-01. So the sequence is: run the migration, then
run apply.sh. Both are yours to run.

**A ZERO FROM THE DATABASE IS ALMOST CERTAINLY ROW-LEVEL SECURITY, NOT THE WRONG
DATABASE.** This paragraph used to say the opposite and it sent at least two
sessions down the wrong path, most recently on 2026-09-17, so read it before you
act on a count you did not expect.

`chat_thread`, `app`, `ticket` and the rest of the tenant tables carry `FORCE ROW
LEVEL SECURITY`, and the policy is
`org_id = NULLIF(current_setting('app.current_org', true), '')::uuid`.
`db.engine.SessionLocal()` sets no tenant, so `current_setting` is NULL, the
comparison is `org_id = NULL`, and **every table reads as empty**. That is
deliberate and pinned: `backend/tests/test_db_tenancy.py` has
`test_a_session_with_no_tenant_sees_nothing`, whose docstring says `admin_session`
"is not a back door".

So a raw `SessionLocal()` probe answering 0 is the policy working, not a
misconfiguration. Measured 2026-09-17 while the catalog held ~296 chat threads,
~491 tickets and 11 apps. **Pin an owner and go through the service layer:**

    import sys; sys.path.insert(0, "/workspace/backend")
    from services import ownership
    ownership.pin_operator()

For raw SQL use `tenancy.tenant_session(org_id)`, never a bare `SessionLocal()`.
The service layer already fails LOUDLY without an owner (`ownership.OwnerUnset`,
`tenancy.TenantUnresolved`), which is why no committed script or gate is affected
by this: `root_task_counts.sh`, `field_occupancy.py`, `migration_state.py` and
`check_fresh_clone.sh` were all re-run from a sandbox on 2026-09-17 and all
answered correctly. The exposure is entirely to ad-hoc one-off probes, which is
to say to agents, which is to say to you.

**And the topology is fine, so do not go looking for a second Postgres.**
`infra/postgres/docker-compose.yml` publishes `127.0.0.1:5440 -> 5432`, so a
server that reports its own port as 5432 while you connected on 5440 is the
EXPECTED reading and not evidence of a scratch instance. Confirmed the same day
by finding the backend's own pooled connections in `pg_stat_activity` from a
sandbox session, with a `backend_start` matching `/healthz` to the second.
`scoped_exec` does not rewrite the DSN and `db/engine.py` returns
`WORKSHOP_DATABASE_URL` verbatim.

What remains true from the old paragraph: `scripts/migration_state.py` asks the
RUNNING BACKEND for its revision first (`/healthz` reports it) and falls back to
connecting itself. Its drift messages name the source; **its `ok` line does not**,
so "ok" tells you nothing about which side answered. If that matters to you, ask
`/healthz` yourself.

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

### This tree IS the running app (relocated out of L2, 2026-08-27)

Until 2026-08-27 the seven facts below were a hardcoded `is_self` block inside
`chat_context.runtime_briefing()` — a paraphrase of this file, shipped as
PLATFORM prompt text to every installation, one clause of it addressing the
operator as "him". The briefing now states only the generalized version (a
self-hosting installation serves a built bundle, so finish editing and then run
its apply step) and points the agent at these rules, which `provider.persona()`
already injects as "Project-local agent rules". This is the authority; keep it
here.

- **There is no separate deployment.** Your edits change the app the operator is
  using right now. Don't pick ports or start servers — they are already running.
- **The apply step is `bash scripts/apply.sh`**, run once as your FINAL action
  after all editing. `--check` reports drift without acting. See the section
  above and [`docs/applying-changes.md`](docs/applying-changes.md).
- **Nothing reloads on its own.** Vite's hot reload covers only the `:5173` dev
  origin, which is not the origin the operator is on; the backend does not
  auto-reload at all. A restart is a pause, not a break: in-flight turns are
  adopted again on the way back up.
- **Built-in apps** live in `frontend/src/pages/apps/<app>`, with their backend
  logic in `backend/services/<app>`.
- **The `@workshop/ui` kit** at `packages/ui` is platform-owned and compiles
  INTO the bundle, so editing it needs a rebuild too. Import by package name;
  see [`packages/ui/CONTRIBUTING.md`](packages/ui/CONTRIBUTING.md) and
  [`frontend/CLAUDE.md`](frontend/CLAUDE.md).
- **Validate before claiming done:** `npm run typecheck` from the repo root plus
  `bash scripts/check_design.sh` for UI; a quick import check for backend.
- **Commits:** real repo, real history, unrelated work in progress. Never
  `git add -A`. Stage only the files you changed.

**How the operator likes to be talked to is L3, not platform text.** Four
personas asserted "a senior engineer who dislikes padding"; that is a fact about
one person and it now belongs in the operator's own custom instructions at
`/settings/instructions`. The platform still names the operator
(`WORKSHOP_OPERATOR_NAME`) and still asks every assistant to be direct and brief,
which is product doctrine and true for everyone.

### Before you plan anything: `python3 scripts/check_plan_landed.py`

It prints, per step, which parts of the plan this repo already agreed on are
actually in the tree. Run it FIRST, before writing a design, opening a doc, or
deciding what to work on. If it prints `FAIL` or `NOT YET`, that is your work,
and it is already specified: do not design it again.

This exists because of a measured failure, not a preference. `docs/platform-
redesign/threads-branches-and-pages-2026-08-29.md` section 7 is a numbered
five-step plan. Three consecutive sessions read it, agreed with it, summarised
it, and wrote further documents about it; none executed a step. On 2026-08-29,
2026-08-31 and 2026-09-01 the operator reported the same defects that plan
closes, in the same words. **A plan in a markdown file has no failure mode.** A
session can read it, feel oriented, and leave the tree unchanged, and afterwards
that tree is indistinguishable from one where the plan was done.

The second half matters as much. Section 7 named a prerequisite it called
"currently blocking": the `dev_only` migration unapplied, on one of six alembic
heads. By 2026-09-01 that was false, one head and the live database at it, and
nobody re-ran the command. **A stale blocker in a plan is a permanent excuse**,
and it is worse than no plan, because it makes inaction look like diligence. So
a pin in `scripts/plan_landed_baseline.json` may carry `blocked_on_check`, a
command whose success means the blocker is gone, and the checker fails with
`UNBLOCKED AND UNSTARTED` when it succeeds. That failure reaches `reviewer.sh`
as `planned_step_unblocked_and_unstarted`.

Same ratchet rules as every other gate here: a pin is never a pass, a pin that
starts passing is a failure, and `--quiet` (what the reviewer runs) forgives an
honest not-yet but not an expired excuse.

### Two things are being built here, and confusing them is the recurring defect

This checkout is **the operator's own installation** — his apps, his data, his
machine — and it is also **the baseline product**, what a stranger gets when
they clone the repo. Every few weeks an agent commits something true of the
first into the second, reports "app separation" done, and a later agent
rediscovers that it was not. Anything true of this INSTALLATION stays out of
tracked source; anything true of every installation is what the repo ships.
That is the same rule the prompt-layer section below applies to prompt text.

**`bash scripts/check_fresh_clone.sh` is the single executable authority on
which is which**, and it is deliberately not a document: it clones HEAD and
measures what a stranger actually receives — no app source, no personal data,
no operator identity, builds with the app source deleted, boots with an empty
catalog. Any assertion recorded as an expected failure in
`scripts/fresh_clone_baseline.json` prints `NOT YET`, never `PASS`, and never
makes the absolute run green. **No document, status line or agent summary may
claim app separation is done.** The claim is that command's exit code and
nothing else.

**As of 2026-09-16 that command exits 0**, all five assertions PASS, and
`expected_failures` is empty. RE-RUN IT RATHER THAN TRUSTING THIS PARAGRAPH, and
note that this is now the THIRD version of this sentence. The first said "it
fails today" and went stale. The second said "as of 2026-09-04 it exits 0" and
was false for eleven days — the command went red on 2026-09-05 and again on
2026-09-16, nobody ran it, and a paragraph that exists to warn about stale claims
was one. Nothing runs it on a schedule; that is ticket `0f2f3fa6`.

**AND KNOW WHAT THAT EXIT CODE DOES NOT COVER, because "the claim is that
command's exit code and nothing else" cuts both ways.** It clones HEAD and
inspects the resulting TREE, so it is structurally blind to four things measured
on 2026-09-16, all of which are real and none of which it can ever see:

- **Git history.** The operator's own `commute/*.json` (home and work addresses)
  and the Plaid MCP server are reachable from `origin/main` and five other
  remote branches. Deferred once on 2026-07-12 and unmentioned for nine weeks.
  Ticket `43dec530`, which also adds the history assertion.
- **Configuration.** 16 app-specific variables sit in the platform's own env
  files; all four `apps/*/app.env` are inert because the platform copy wins.
  Ticket `ed66df46`.
- **Prose.** ~350 lines of comments and docstrings in platform source name an
  app, plus `chat_context.py` shipping app names in L2 prompt text to every
  installation. No gate reads prose, and prose is what an agent reads when asked
  for an example.
- **The generated client.** `frontend/src/api/schema.gen.ts` carries ~124
  app-route lines, and `scripts/gen_openapi.py` deliberately forces the full
  catalog to mount, so it REGENERATES. A ratchet protects it from being fixed.

So the honest status is: a stranger's clone is clean, and the repository is not.

`python3 scripts/check_no_app_names.py` counts 23 platform lines naming an app
(`content`, `imports` and `homepath` are all 0). **All 23 are false positives** —
`budget` is a token budget, `health` is a readiness-probe field, `location` is an
iOS background mode, `notes` is a redirect — so 23 is this gate's FLOOR, not its
remaining work, and driving it to 0 means renaming platform fields. Meanwhile 483
lines across 131 tracked platform files name an app in a form the gate cannot
match. The number is not a status. The end state DELETES this script — see the
"stopgap" section below — so making the mistake impossible is worth more than any
count.

## Platform design (current: 2026-08-20)

The authoritative model lives in [`docs/platform-redesign/entity-model-2026-07-18.md`](docs/platform-redesign/entity-model-2026-07-18.md) (start there) with the phase docs riding on top of it: `autonomy-model-*`, `primitive-roles-*`, `chat-scope-design-*`, `chat-work-linkage-*`, `resource-access-interface-spec-*`, and the sequenced `final-plan-agent-generalize-and-bridge-fold-2026-07-16.md` + `coherence-migration-plan-2026-07-18.md`. Agents are keyed by `role` (closed enum: maker/reviewer/triager/keeper/broker). The older `runtime` axis (builder/assistant) is GONE (verified 2026-08-20): there is no `runtime` field (`agents_registry.py`, "THERE IS NO `runtime` FIELD"; `AgentDetail.tsx`, "typed by ROLE, there is no `runtime` field"), dispatch is by role (`isMaker`), and `_role_for_runtime` no longer exists in the tree. Do not write new code against `runtime`. `latitude` is GONE the same way (ticket `3b1c5432`, 2026-09-05): no field, no column, no `services/latitude.py`, no cascade, and `latitude-model-2026-07-18.md` carries a supersession-by-removal banner. It was set on 0 of 207 tickets, so nothing was ever governed by it; decision rights live in the ticket's Boundaries line. What survived is in `reviewer.sh`, where an applicable-but-unrun check now blocks for a decision UNCONDITIONALLY rather than only under the old `strict` setting. NOTE: `is_build_provider` in `chat_context.py` is a SEPARATE, still-live concept, not the retired axis: it means "this chat runs the full agentic toolset" (a maker/project chat) versus a narrow data chat. The `build` branch in `frontend/src/lib/scope.ts` is GONE (verified 2026-09-05): `kindLabel` handles `app`/`project`/`agent`/`role` only, and `scope.test.ts` asserts the deleted `build:` kind is neither recognised nor labelled. What remains is the legacy `/agents/build/{slug}` surface, **20 live routes** as of 2026-09-05 (26 before the six caller-less `todo`/`tasks` spellings were retired that day), 18 of which `frontend/src/pages/AgentBuild.tsx` still calls. Retiring the rest is a rewrite of that page onto `/files`, `/git`, `/projects` and `/base-apps/run`, not a route deletion; see `app-is-the-entity-2026-08-20.md` §5 for the target and count the decorators rather than trusting this sentence. Read the phase docs before touching the agents/projects/chat/apps systems.

**APP IS THE ENTITY, and Project is retired as one** (2026-08-20). The entity-model doc's App-vs-Project split is SUPERSEDED by [`docs/platform-redesign/app-is-the-entity-2026-08-20.md`](docs/platform-redesign/app-is-the-entity-2026-08-20.md): an app is the one container the user names, "project" is the build view of an app rather than a second object, a Deployment is a running instance of an app, and `scope` is internal addressing that never appears in the UI. That doc is the model of record. **Stage A of its §9 IS built** (verified 2026-09-05): there is one `app` table with a uuid surrogate and a renameable slug, `app_id` on `chat_thread` and `ticket`, and `/apps` plus its nested collections. The two files (`.runtime/projects.json`, `.runtime/installed-apps.json`) are still where an install and a project are WRITTEN, but they are no longer unlinked: `services/app_links.py` computes the merge, `db/app_backfill.reconcile` writes it into the table, and `services/apps.reconcile_from_registries` runs that on the `/apps` read and after every projects-registry write, so the table cannot silently fall behind. An app the registries stop naming is ARCHIVED, never deleted, because its chats and tickets are keyed to the row. Stages B-F are NOT built. Do not write new code against the app/project split, and do not add a third registry.

**No platform source file may name a specific app.** Measured by `python3 scripts/check_no_app_names.py`, which gates `reviewer.sh`; the three counts may only go down. Current work and the next stages: [`docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md`](docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md). Not a registry entry, not an `if app_id ==`, not an icon import, not a status probe, not a test fixture. An app declares what it is and the platform reads the declaration. This is currently violated in the two enumerating registries and about a dozen special cases, all inventoried in §6 of that doc; do not add to them.

**The check is a stopgap, and needing it is the defect.** Counting app names in
platform source only works while every agent keeps remembering not to write one,
which is policing, and the policing itself has failed repeatedly. Three holes in
it were found on 2026-09-03 alone: the app roster read one of three disagreeing
registries, so an entire app was never scanned and its name counted zero; that
roster's catalog source had silently gone dead two days earlier when the
enumerating tuple it parsed was removed; and the pattern set could not see a
constant NAMED for an app, which is how one app's vault root sat in
`services/permissions.py` — platform code that never read it — indefinitely.

The requirement, stated by the operator on 2026-09-03, is not a lower number. It
is that the mistake be **impossible rather than detected**: an app's agent has no
access to platform source, so it cannot hardcode an app name into a platform
file, and a platform agent has no app source to name. That is the point of the
separation, and it makes `check_no_app_names.py` redundant — the end state
DELETES that script rather than extending it. **Do not answer the next leak by
adding another pattern to it.** Whichever option §10 of
`app-is-the-entity-2026-08-20.md` settles on, the access boundary is part of the
deliverable: if the option cannot scope an app's agent to the app's own tree, it
is the wrong option.

**Know which prompt layer you are writing into.** The contract is [`docs/platform-redesign/agent-context-and-toolkit-2026-08-20.md`](docs/platform-redesign/agent-context-and-toolkit-2026-08-20.md) §"The four-layer contract", highest authority first: **L0** kernel (fixed, confidential, `chat_context.PLATFORM_KERNEL`), **L1** orientation manifest (generated, tool-centric, `services/orientation.py`), **L2** scope/app persona (`provider.persona()` plus the shared constants beside it), **L3** the operator's own custom instructions (`services/custom_instructions.py`, per owner per scope, edited at `/settings/instructions`). Plus one slot the contract does not name: **`AGENTS.md`** in a scope's own directory, read by `persona()` and injected as "Project-local agent rules".

L0 to L2 are PLATFORM text: every installation and every user gets them, so they may carry only generalized product doctrine. They may not carry a fact about one checkout (a doc path, a repo convention, a named person, an operator's habits) and may not assume the reader's pronouns. This is the same rule as "no platform source file may name a specific app", applied to prompt text instead of code. It is gated as of 2026-08-27 by `python3 scripts/check_prompt_layers.py` (declaration in `scripts/prompt_layer_rules.json`, floor pinned at ZERO in `scripts/prompt_layers_baseline.json`), which runs unconditionally in `reviewer.sh` and fails the run on `installation_fact_in_platform_prompt`. It detects gendered pronouns, `~/` and host paths, `.md` filenames and a declared vendor list in the prose string literals of L0/L1/L2 and the MCP tool schemas. Anything true of THIS repo rather than of Workshop goes in this file, in `AGENTS.md`, or in L3 — never in `chat_context.py`; anything true of this INSTALLATION rather than of every installation is declared in `backend/services/installation.py` and set in `~/.config/workshop/instance.env`. Worked example: the ticket convention below was written into L2 on 2026-08-22 and moved out on 2026-08-23.

**Writing a ticket:** [`docs/ticket-writing.md`](docs/ticket-writing.md). A ticket has two readers, the worker (which gets the body and nothing else) and the operator (opening it on a phone weeks later to decide something), and the four labels `ticket_format.py` enforces only ever served the first.

The earlier v1 design set at [`docs/workshop-v1/`](docs/workshop-v1/) and the 2026-07-11 redesign at [`docs/agent-chat-redesign/`](docs/agent-chat-redesign/) are HISTORICAL. Both carry supersession banners; do not update them and do not treat their assistant/builder axis as current.

## UI / frontend — Design System is mandatory

**Every change under `frontend/` and `mobile/` MUST follow [`frontend/CLAUDE.md`](frontend/CLAUDE.md)** — the Workshop Design System. In short: build from the `@workshop/ui` kit primitives at `packages/ui` (import by package name) + its tokens at `packages/ui/src/styles/tokens.css`; no inline hex colors; lucide icons only (never emoji); five status kinds; Edit-mode for config/money writes; `ConfirmDialog` for destructive actions; all four list states (loading/empty/error/overflow). Default theme is dark.

Before claiming a UI task done: `bash scripts/check_design.sh`, `npm run typecheck` (from the repo root), and `cd frontend && npm test` (vitest; pure logic modules only — no DOM harness). The reviewer gate fails on `design_violations` (new inline hex / emoji / `not-allowed` in changed `.tsx`).

**Do NOT typecheck with `npx tsc`.** `tsc` is also the name of a deprecated stub package on npm, and npx resolves that ahead of the local TypeScript binary: it prints "This is not the tsc command you are looking for" and **exits 0**, so it reports success having checked nothing. This repo shipped on that no-op gate for months. `npm run typecheck` runs the real binary over both the kit and the frontend (~8s). Use it rather than a path to the binary: a root workspace install hoists typescript to the root `node_modules`, so `frontend/node_modules/.bin/tsc` exists on some checkouts and not others.

Design new screens to the system from the start — there is no "systematize it later" pass.
