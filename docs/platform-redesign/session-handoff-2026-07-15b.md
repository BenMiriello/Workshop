# Handoff: chat / tickets / orchestrator wave (2026-07-15, session B)

Builds on the coherent-rebuild (Stages 0-9). This wave wired the autonomy
pipeline through to the product per the operator's direction: tickets are one
legible surface, chat scopes work into tickets and launches workers immediately,
every Job carries a receipt, and the general chat is an orchestrator that learns
preferences. Plan of record: `chat-tickets-orchestrator-plan-2026-07-15.md`
(Fable-reviewed). Companion plans (NOT yet built): `notes-system-plan-2026-07-15.md`,
`github-worktree-orchestration-plan-2026-07-15.md`.

## Shipped and live-verified (all on main)

- **P0 ground clearing** (`e0a545a`): deleted the legacy TODO.md writer swarm
  (`curriculum_tick`/`work_generator`/`work_verifier`, plus the `15 * * * *` cron
  line), `compact_todo.py`, `graduate_proposals.sh`, `letta_register.py`,
  `global_memory` ghost. Rewired the orphaned `/backlog` route to file a canonical
  task via `dedup_add`.
- **P1 Job receipt** (`80e4200`, refined `b140676`): every child Job now carries a
  structured `{kind:receipt, files, commits, baseline, head, diff_files,
  typecheck, design, iterations, tokens, summary}` evidence object. `reviewer.sh`
  emits the file list, `ralph.sh --receipt` writes the run summary, `project_ralph.sh`
  merges git facts and attaches it on every terminal path; the Task gets a
  receipt-ref note. `jobs.py finish` CLI now json-parses `--evidence` so it is a
  real object (also fixed the latent `awaiting_input` list bug). `files` comes from
  the committed diff, filtered of `.runtime/`/`.tasks.json`/`TODO.md`. JobDetail
  renders it. Verified live: a demo-x sweep produced a full receipt with the commit.
- **P2 Work surface** (`d7ae420`): one nav item "Work" with Tickets|Runs tabs.
  New `GET/POST/PATCH/DELETE /api/tasks` aggregates every scope (workshop root,
  projects, agent scopes, build agents) so parked to-do lists are finally visible
  and editable in one place; manual tickets dedupe via `dedup_add`. Ticket drawer
  shows description, evidence, and linked Runs with receipts. Jobs list extracted
  to `JobsFeed`; `/jobs` kept. Centralized the 7-status map in `lib/tasks.ts`,
  fixing ProjectDetail's broken StatusDot for in_review/parked/cancelled.
- **P3 chat above jobs** (`a30c3d6`): `project_ralph.sh --task-id` runs exactly one
  task. `POST /api/tasks/{scope}/{task_id}/run` opens a Job and launches a worker
  immediately (no ~20min cron wait), 409 on a busy tree. Chat spawn-task gains
  `run:true` which launches and server-appends a persistent `job-status` ui-card
  that polls to done and shows the receipt. New `task-proposal` card (File / File
  and run) lets chats scope work into tickets; build/project personas got the
  inline-vs-delegate triage rule. Work drawer gets Run now. Verified live:
  run-now opened a job + worker in seconds, 409 fired on the busy tree.
- **P4 orchestrator** (`a34139d`): the general chat is an orchestrator persona (no
  new name, no new runtime): scopes vague requests into tickets, asks clarifying
  questions, delegates via cards, interprets outcomes, still answers plain
  questions directly. `root_for_context(general)` resolves to the workshop root so
  the general chat owns the root queue. A `learning-note` fenced block (both
  blocking and streaming finalize paths) lands stated preferences in the learnings
  store deterministically; `task-patch` now forwards priority. Verified live:
  general==workshop root, note stripped + recorded + injected into memory_block.
- **Branches fix** (`a2c5752`): `worktrees.py` read a non-existent
  `<workshop>/projects.json`, so every project's Branches section raised "registry
  missing". Pointed at `.runtime/projects.json`. Branches now render.
- Rebuilt `frontend/dist` (public edge serves it from disk, live immediately),
  clearing the stale-build errors the operator saw.

## Known gaps filed to the workshop backlog (this session)

- Acceptance/reviewer should require COMMITTED work for a task to be `done`. Today
  a `test -f X` command acceptance passes on an uncommitted file, so a task can go
  done with an empty receipt (the receipt honestly shows nothing was committed).
- Receipt `summary` is empty when the worker exits nonzero but the reviewer passes
  (sentinel missed). Capture the final `.result` even then.

## Not built yet (planned, sequenced after this wave)

- Notes system (N0-N3): reuse the knowledge store, repoint the dead project Notes
  section, 3-tier scratch/notes/ADR policy. See `notes-system-plan-2026-07-15.md`.
- GitHub + worktree orchestration (G0-G3): project repo binding, gh bind/push/PR,
  worktree-per-task concurrency with rebase-before-merge. See
  `github-worktree-orchestration-plan-2026-07-15.md`. Hard veritusagent rejection.
- Both flag file overlaps with the ticket phases (ProjectDetail, appNav, App,
  chat_context, project_ralph); sequence per each doc.

## State notes

- Autonomy: `.runtime/AUTONOMY_DISABLED` is absent (live). Workshop scope stays
  manual (safety-classifier-gated from continuous self-editing). demo-x continuous.
- Verify without the authed UI: backend via py_compile + direct imports + curl
  401-vs-404; frontend via tsc + check_design + npm build. Restart:
  `bash scripts/restart_backend.sh --deferred`.
