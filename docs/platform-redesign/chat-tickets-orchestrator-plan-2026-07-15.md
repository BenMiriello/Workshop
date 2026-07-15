# Chat, Tickets, and Orchestrator: reconciliation verdicts + implementation plan (2026-07-15)

Status: reviewed plan, grounded against shipped code (every file claim below was read, not assumed).
Scope: reconciles the operator's five asks with the shipped Task/Job/dispatch substrate.

## Executive summary

1. Task and Job stay separate records; the Task becomes the one primary surface ("ticket") and Jobs render inside it as the run log. D1 confirmed.
2. No Task schema change is needed for title/description: `text` is already multi-line-lossless (first line = title, continuation = body), and reconcile/export/dedup all key on it. D2 as proposed is rejected; ship it as a presentation convention instead.
3. The receipt gap is real and cheap to close: reviewer.sh already computes the changed-file list but throws it away; thread it through ralph.sh into Job evidence plus commits and a run summary. D3 confirmed, with a concrete seam.
4. Ship one nav surface, "Work", with two tabs: Tickets (primary, cross-scope list + drawer) and Runs (the existing Jobs feed, extracted into a component). /jobs stays as a route.
5. Chat-above-jobs is one missing flag and one missing card: a per-task run-now launcher (`project_ralph.sh --task-id` + a scope-generic run endpoint) and two ui-card kinds (`task-proposal`, `job-status`). The docs already designed `addTasks`/`addTasksAndRun`; this builds them.
6. Inline-vs-delegate triage is structural, not heuristic: general/assistant chats cannot mutate files (tool asymmetry already enforces delegation); build chats get a persona rule plus the proposal card, and the operator's click is the tiebreaker. No numeric threshold in code.
7. The orchestrator is a persona upgrade to the existing `general` provider plus the already-shipped learnings store. No new runtime loop, no new name (naming is operator-gated and deferred). D6 confirmed with one correction: `general` must gain the workshop-root task scope, which the orchestration doc already assigns it.
8. Clean the ground first: the curriculum/work-generator/verifier swarm and compact_todo.py actively fight the canonical store today (curriculum_tick greps `^\[ \]` which the new export never emits, so it always thinks the queue is empty and appends lines the store then destroys). Remove them before building on top.
9. Phases 0 (cleanup), 1 (receipt), 2 (tickets UI) can run as parallel subagents with disjoint file ownership; 3 (chat seam) and 4 (orchestrator) are sequential after them.
10. Explicitly not now: GitHub Issues sync, masini folding, clawd rename, board UI, any heavy learning conductor, and any multi-tenant guardrails.

---

## Part 1: pressure-test verdicts

### D1. Do not merge Task and Job records. Ticket = primary surface. VERDICT: RIGHT.

The shipped substrate is already one-to-many and already linked both ways: `Task.job_ids` (`backend/services/tasks.py:28`) and `Job.task_id` + `jobs.read(task_id=...)` (`backend/services/jobs.py:94,205`). `project_ralph.sh` opens a child Job per task attempt under a coordinator sweep Job (`scripts/project_ralph.sh:66-70,269`). Merging records would destroy the attempts model (a task retried 3 times is one ticket, three runs) and force the append-only JSONL job log and the flock-guarded task store into one write path that shell and backend both write concurrently. The Linear analogy is exactly right: issue = Task, activity feed = its Jobs.

What is underspecified in D1: "render Jobs inside the ticket" needs a scope-qualified ticket URL, because the task store is per-scope (`.tasks.json` per tree) while the Job log is global. The ticket detail route must carry the scope (`/work/:scope/:taskId` where scope is `project:slug`, `agent:slug`, or `workshop`). `task_scope.root_for_context` (`backend/services/task_scope.py:25`) already resolves all of these except `workshop`; see Phase 3 for the one-line addition.

### D2. Add `title` + `description` to the Task schema. VERDICT: WRONG, and the correct version is zero schema change.

The store already models this. `text` is explicitly multi-line-lossless: `render_markdown`/`_emit` write the first line as the checklist line and continuation lines indented under it (`tasks.py:484-499`), `_parse_export_blocks` re-gathers them (`tasks.py:351`), and reconcile matches tasks on the normalized FIRST line only (`_norm_text`, `tasks.py:343`). In other words: first line IS the title, remainder IS the description, and the entire reconcile/export/hand-edit-healing machinery is built around that invariant.

Adding separate `title`/`description` fields would create two sources of truth for the same string, force `render_markdown`, `_parse_export_blocks`, `_norm_text`, `task_dedup.canonical` (full-text embedding), `spawn_task`, `apply_task_patch`, and `project_tasks.py` to all agree on a join rule, and require a migration. All cost, no capability.

Ship instead: a frontend convention in one shared module (`frontend/src/lib/tasks.ts`): `title(task) = text.split("\n")[0]`, `description(task) = rest`; the drawer edits them as two fields and joins with `"\n"` on save through the existing `tasks.update(text=...)`. `effort` and `priority` already exist on the schema (`tasks.py:34,16`); expose them in the drawer, add nothing to the store.

One real pre-existing wart to accept (not fix now): two tasks with identical first lines collide in reconcile's `by_text` map (`tasks.py:420`, first wins). It predates this work and dedup makes it rare.

### D3. Persist the receipt (files/commits/summary) on the Job. VERDICT: RIGHT, and cheaper than the audit implies.

Confirmed gaps: `reviewer.sh` computes `CHANGED_ALL` (line 53) but emits only the count `diff_files` in its JSON (line 246); `ralph.sh` holds `last_reviewer_json` in memory and discards it on exit; `project_ralph.sh` success paths (lines 309, 321) finish the child Job with status + exit code only, `job_finish_child` cannot even pass evidence (line 78). `services/jobs.py.finish()` already accepts arbitrary `evidence` (line 131), and the awaiting_input path already uses it (`project_ralph.sh:392-393`), so the store needs no change at all.

The correct shape, honoring the docs' deliberate removal of raw reasoning from evidence: a STRUCTURED receipt, not chain-of-thought.

```
evidence = {
  "kind": "receipt",
  "files": [...],            // reviewer.sh CHANGED_ALL, capped ~200
  "commits": ["<sha>", ...], // git rev-list BASELINE..HEAD in the scope tree
  "baseline": "<sha>", "head": "<sha>",
  "diff_files": N, "typecheck": {...}, "design": "...",
  "iterations": N, "tokens": N,
  "summary": "<final passing iteration's .result text, clipped ~1200 chars>"
}
```

The `summary` costs nothing: the sentinel iteration's `--output-format json` envelope (`ITER_OUT`) already contains the model's final text in `.result`, and by the loop contract that text is the worker's own account of what it did. That satisfies "a log of what was done + reasoning" as a worklog without re-introducing raw thought dumps. Task-side: attach one evidence entry `{"kind": "receipt", "ref": "<child job id>"}` via the existing `tasks.update(evidence=...)` so the ticket timeline points at the Job rather than duplicating the payload.

One trap: ralph.sh must hand the receipt to project_ralph.sh via an explicit `--receipt <path>` argument, not a pid-derived filename (the parent does not know the child's `$$`, and `ITER_OUT` files are already pid-suffixed for concurrency reasons per review F1).

### D4. Tickets surface, Jobs demoted to runs feed. VERDICT: RIGHT. Recommendation: ONE nav item ("Work"), two tabs, both routes kept.

What exists: per-project task CRUD API (`backend/routes/projects.py:363-400`, PATCH limited to status+text), a task tree inside ProjectDetail only (`frontend/src/pages/ProjectDetail.tsx:239`), and a global Jobs feed + JobDetail (`frontend/src/pages/Jobs.tsx`, `JobDetail.tsx`). There is NO cross-scope task list and NO task detail view anywhere; agent-scope and workshop-root tickets are invisible in the UI today. That is precisely the operator's "stale to-do lists never acted on" complaint: work parked in `.runtime/agent-scopes/*/` and the root store has no surface.

Found while verifying: ProjectDetail's task UI only knows 4 of the 7 shipped statuses (`TaskStatus = "todo" | "doing" | "done" | "blocked"`, line 67). An `in_review`, `parked`, or `cancelled` task hits `TASK_STATUS_KIND[task.status]` as undefined and renders a broken StatusDot. This is a live bug the Tickets phase must fix by centralizing the status map (all 7 statuses onto the design system's five kinds).

Nav decision: the operator wants ONE concept. So: one nav item "Work" at `/work`, Segmented tabs `Tickets | Runs`. Tickets is the new cross-scope list + drawer. Runs is the existing Jobs list extracted into a `JobsFeed` component; `/jobs` and `/jobs/:id` remain as routes (deep links from Home, Agents, chat spawn-notes all point there; keep them working, JobDetail stays a full page). Do not name the new surface "Tickets" or "Issues" in code: the store, CLI, docs, and API all say Task; introducing a third noun is drift. UI copy can say "ticket" in prose; identifiers stay `task`.

### D5. Per-task run-now + inline job card + triage. VERDICT: RIGHT, and it is the designed-but-unbuilt piece of the orchestration doc.

Verified: the only synchronous launcher is `POST /projects/{slug}/autonomy/run` -> Popen `project_ralph.sh slug --force --job-id` (`backend/routes/projects.py:525-550`); it sweeps ALL ready tasks (`next_ready` loop, `project_ralph.sh:230-241`) and is not task-granular. `spawn-task` files a ticket via `dedup_add` and stops (`backend/routes/chat.py:241-290`); nothing chat-reachable calls `jobs.start`. The chat shows a static "Track it in Jobs" link (`frontend/src/components/Chat.tsx:1616`). Meanwhile `docs/platform-redesign/orchestration-and-autonomy.md:347,773` already specifies a `task-proposal` ui-card with `addTasks` / `addTasksAndRun` actions, and decision 5 explicitly blesses "a general chat picks a scope and uses addTasksAndRun as an on-demand invocation". So D5 is not new design; it is finishing O-phase work in the already-written direction. No shipped decision is fought.

Sub-pieces and their correct shapes:

- Task-granular run: add `--task-id <id>` to `project_ralph.sh` (claim exactly that id instead of `next-ready`, implies `--max-tasks 1`). Do NOT bypass the sweep script with a bespoke launcher: the sweep already owns claim/lease/fencing/acceptance/receipt/budget admission, and a second launcher would fork that logic. The existing per-tree flock (`project_ralph.sh:184`) gives the right serialization for free: a run-now against a tree mid-sweep cleanly aborts, surface that as a 409 like the existing endpoint does.
- Run endpoint must be scope-generic (project + agent scope + workshop root), since tickets are. Project scope maps to `project_ralph.sh <slug> --force`, agent/workshop map to the existing `--dir <path> --assignee` form.
- Inline job lifecycle: a `job-status` ui-card kind that polls `GET /api/jobs/{id}` (endpoint exists, `backend/routes/jobs.py:224`) while status is running, then renders done/failed + receipt highlights + a link to the Job. Persist it by having the server append an assistant message containing the card block via `chat_store.append_message` (exists, `backend/services/chat_store.py:130`) inside the spawn-and-run path, so it survives reload with zero new transport.
- Triage: see resolved sub-decision (a). It is persona + structure, not code.

One thing D5 gets slightly wrong: "immediately spawns a worker (don't wait ~20min for cron)" should not become "every filed ticket auto-runs". The card's two actions (file vs file-and-run) keep that an explicit choice per the doc's on-demand default, which is also what protects the paused-autonomy posture: `--force` marks a deliberate operator-initiated action and correctly bypasses `.runtime/AUTONOMY_DISABLED` exactly as the existing manual endpoint does.

### D6. Orchestrator = conversation-layer persona, not a runtime. VERDICT: RIGHT, with two corrections.

Right because: the docs explicitly rejected a heavy learning conductor ("ants, not a heavy conductor"; the memory-keeper became `services/learnings.py`, a service). The mechanical dispatcher (`scripts/dispatch_triggers.sh`, O3) is shipped and works; a second decision-making loop above it would be the rejected design back under a new name. And the substrate for a preference-learning persona already exists: `learnings.record_feedback` + `memory_block` (`backend/services/learnings.py:285,308`) are injected into both chat prompts and ralph runs (`scripts/ralph.sh:169-189`).

Correction 1: a persona alone does not satisfy ask 5 unless the general chat can actually FILE and RUN work. Today `task_scope.root_for_context("general")` returns None, so a general chat cannot hold a task-patch or a proposal scope-default. The orchestration doc (decision 5) already assigns the general chat `scope = workshop`. Implement that: `general` resolves to the workshop root store. That is the difference between "a chat that talks about work" and "a chat that owns a queue".

Correction 2: do not name it. The masini-folding / clawd-rename decision is deferred and operator-gated, and christening a new named agent now would preempt it. The upgrade goes into the existing `general` provider (`chat_context.py:_general`), whose display identity the operator already knows; the operator picks a name later if he wants one. If he asks for "a NAMED orchestrator with its own identity" after using it, that becomes a registry assistant creation (one operator action in the existing agents UI), not new engineering.

Does this satisfy the operator's ask, or does he need more? The ask decomposes as: learns preferences (learnings store, shipped, needs a chat-side capture affordance), broad scope (workshop-root task scope + cross-scope query via `mcp__workshop__query`, shipped), scopes/prioritizes (persona + task-proposal cards + priority field), surfaces questions (the `awaiting` payload + attention surfaces, shipped). The one thing a persona cannot do is act between conversations, and that is deliberately the dispatcher's job. If, after living with it, he wants proactive between-session behavior (e.g. a morning brief), that is the doc's memory-keeper/scheduled-assistant slot (O5/O6), a follow-on, not a blocker.

---

## Part 2: resolved sub-decisions

### (a) Inline-vs-delegate mechanism: structure + persona + operator click, no numeric heuristic

Three layers, none of which is a brittle threshold in code:

1. Structural (already true, keep it): general/assistant chats have no file tools (`_general.allowed_tools = ["mcp__workshop__query"]` + web, max_turns 8). Anything that mutates the tree CANNOT run inline there; delegation is the only path. This is the strongest and least brittle triage: capability asymmetry.
2. Persona rule for build/project chats (the "40-100 tools inline" surface, max_turns 60): "Answer questions and make single-file trivial edits inline while the operator is watching. For anything multi-step, multi-file, or that should survive this conversation, emit a task-proposal card instead of doing it inline." The model decides with full conversational context; there is no code path to get wrong. The `effort` field (s/m/l, already on the schema) is set by the model on the proposal as a hint, not consumed by a gate.
3. Operator arbitration: the `task-proposal` card's actions are the tiebreaker: `File ticket` (queue for the dispatcher), `File and run` (immediate worker), or dismiss (the model then just does it inline if asked). Wrong triage costs one click.

Explicitly do NOT reduce build-chat `max_turns` in this plan. The chat transport was hard-won (chat-overhaul + streams-for-all-providers memos both warn regressions here); shrink the inline budget only after the delegation path exists and the operator has voted with usage. Flag it as an operator-gated tuning knob at the end of Phase 4.

### (b) Tickets vs Jobs: one nav surface, two tabs, both routes kept

- Nav: replace the `Jobs` item (`frontend/src/components/appNav.tsx:363`) with `Work` -> `/work` (lucide `ListChecks` can carry over).
- `/work` = Tickets tab (default): cross-scope task list, filters (status, scope), Segmented status filter mirroring Jobs' pattern, row click opens the detail drawer. All four list states per the design system.
- Runs tab: the existing Jobs feed body extracted to `frontend/src/components/JobsFeed.tsx`, rendered by both `/work` (tab) and `/jobs` (kept page). `/jobs/:id` JobDetail is untouched as a page; ticket drawers link to it.
- Ticket drawer shows: title (first line, text input), description (rest, textarea), status (all 7, mapped to the five status kinds), priority, effort, assignee, scope (clickable to project/agent), evidence timeline, linked Jobs (`jobs.read(task_id=)`) with per-run receipt summaries, and Run now.
- ProjectDetail's task section keeps working but its status map moves to the shared `frontend/src/lib/tasks.ts` (fixing the 4-of-7 status bug); a "View in Work" link per task replaces any temptation to grow a second drawer there.

### (c) Orchestrator persistence: persona + learnings is enough; no new runtime

The "persistent" parts already exist as services: learnings (global + scoped, injected into every chat and ralph prompt), the ambient task digest in the chat prefix, knowledge scopes, and the dispatcher for between-session execution. A persistent orchestrator PROCESS would duplicate the dispatcher and re-litigate the rejected conductor. The only genuinely new wiring ask 5 needs: (1) the general chat gets the workshop-root task scope, (2) a lightweight chat-side preference capture (a `learning-note` fenced block applied in the finalize path, exactly like `task-patch`, ~30 lines mirroring `task_scope.apply_task_patch`), so "stop doing X" said in chat lands in `learnings` deterministically instead of hoping compaction catches it. The scheduled memory-keeper consolidation agent stays a documented follow-on (it is already designed as O5; do not build it in this plan).

### (d) Task schema additions: none

No new fields. Title/description = first-line convention (D2). `priority`, `effort`, `assignee`, `evidence`, `job_ids`, `source`, `provenance` already cover the ticket surface and the receipt. This keeps `dedup_add`, `reconcile`, `render_markdown`, `project_tasks.py`, and every shell writer byte-compatible. The only API gap is that the per-project PATCH route exposes just status+text; the new scope-generic routes expose the existing fields (priority, effort, assignee) that `tasks.update` already accepts (priority/assignee) plus a one-arg addition of `effort` to `tasks.update` kwargs (additive, does not touch the record shape; `_normalize` already carries it).

---

## Part 3: sequenced implementation plan

Ordering: stabilize the ground (0), then the backend receipt (1), then the ticket surface (2), then the chat seam (3), then the orchestrator (4). Phases 0, 1, 2 have disjoint file ownership and can run as parallel subagents; 3 and 4 are sequential. Global constraints for every phase: no em-dashes anywhere, no emoji in UI (lucide only), frontend obeys `frontend/CLAUDE.md` (src/ui primitives, tokens, five status kinds, four list states), backend restart only via `scripts/restart_backend.sh`, never `git add -A` (stage only your own files; other agents' WIP is in the tree), autonomy stays paused (all verification uses the `--force` manual paths, which legitimately bypass the flag).

### Phase 0: ground clearing (dead/parallel systems) [PARALLEL-OK with 1 and 2]

Kill everything that writes the retired TODO.md format or races the canonical store's export.

Files:
- crontab (clawd-a): remove the `15 * * * * .../curriculum_tick.sh` line. (Verified present; `grep -c '^\[ \]'` in curriculum_tick.sh:60 can never match the store's `- [ ]` export, so it fires the planner every hour into a queue nothing reads.)
- Delete `scripts/curriculum_tick.sh`, `scripts/work_generator.sh`, `scripts/work_verifier.sh` (retired-queue writers; the demote/generate jobs they did are superseded by the store's statuses and the dispatcher). Note in the commit message that a store-backed work generator (autogen source kind + dedup_add) is a later, separate build.
- `scripts/heartbeat.sh`: remove the compact_todo.py invocation block (lines ~66-72); delete `scripts/compact_todo.py`. The store's `_persist` regenerates TODO.md wholesale; the compactor is now a concurrent writer that mangles the export between persist and reconcile.
- `backend/routes/backlog.py`: rewire the write to `task_dedup.dedup_add(WORKSHOP_ROOT, text=..., source={"kind": "operator", "ref": f"backlog:{source}"})` and return the task record. Keeps the mounted `/backlog` seam (future `!todo` path) while killing the unparseable `## Inbox` format. Route shape and response field names may change; there are no callers (verified).
- Delete `backend/services/letta_register.py` (unimported, verified), `backend/services/__pycache__/global_memory.cpython-312.pyc` (stale artifact of a deleted module), `scripts/graduate_proposals.sh` (comment-only).
- Leave `services/todos.py` alone: `todos.parse` is a live dependency of `tasks.py:296`. Do not trim its dead CRUD in this plan (pure churn risk, zero operator value).
- Leave `scripts/daily_todo_digest.sh` / `todo_stats.sh` cron entries alone (read-only consumers of the export; they keep working).

Verification: `crontab -l` shows no curriculum line; `bash scripts/heartbeat.sh <agent> mode=test` succeeds and TODO.md is untouched afterwards; `curl -X POST .../backlog` files a task visible in `/home/clawd-a/workshop/.tasks.json` AND in the regenerated TODO.md export, then `python3 -c "...tasks.reconcile(root)"` is a no-op; `grep -rn "compact_todo\|work_generator\|curriculum_tick\|letta_register\|graduate_proposals" --include="*.py" --include="*.sh"` over scripts/ backend/ cron_agent.sh comes back clean; `scripts/restart_backend.sh` and a `/backlog` smoke.

### Phase 1: the receipt (work product on Job and Task) [PARALLEL-OK with 0 and 2]

Files:
- `scripts/reviewer.sh`: add `"files": [...]` (from `CHANGED_ALL`, capped 200) to the emitted JSON (extend the env-marshalled Python emitter at line 241).
- `scripts/ralph.sh`: new `--receipt <path>` arg. On every exit path, write `{exit_code, iterations, tokens, session_id, reviewer: <last_reviewer_json>, summary}` where `summary` is the `.result` text of the final iteration's envelope (clipped ~1200 chars), which on a PASS is the worker's own completion account. Best-effort, never blocks exit.
- `scripts/project_ralph.sh`: pass `--receipt "$RUNTIME/receipt-$CHILD_JOB.json"` to ralph; after the run compute `HEAD=$(git rev-parse HEAD)` and `commits=$(git rev-list --reverse $BASELINE..HEAD)` in `$DIR`; extend `job_finish_child` to accept an evidence JSON arg and attach the structured receipt (shape in D3 above) on ALL terminal paths (done, in_review, failed, blocked; a failed run's receipt is exactly what explains the failure); add one task evidence entry `{"kind": "receipt", "ref": "$CHILD_JOB"}` via `project_tasks.py note` (or a small `--kind receipt` variant) on the done/in_review paths. Delete the receipt file after attaching (it is transport, not storage).
- `frontend/src/pages/JobDetail.tsx`: render receipt evidence when present: files (list, overflow state), commits (short shas), typecheck/design chips, summary paragraph. This phase owns JobDetail.tsx exclusively (Phase 2 must not touch it).

Verification: on a scratch project with one trivial task, `curl -X POST .../projects/<slug>/autonomy/run`, wait, then `GET /api/jobs?project_slug=<slug>` shows the child Job carrying `evidence.files`, `commits`, `summary`; the task's `evidence` has the receipt ref and `job_ids` links back; JobDetail renders it; a deliberately failing task (bogus acceptance command) still gets a receipt on its failed child Job. `cd frontend && npx tsc --noEmit`, `bash scripts/check_design.sh`.

### Phase 2: the Work surface (tickets list + drawer) [PARALLEL-OK with 0 and 1; owns its files exclusively]

Backend:
- New `backend/routes/tasks_all.py`, mounted in `main.py` under `/tasks` with `_OPERATOR` deps (main.py edit is one line; coordinate: Phase 0 does not touch main.py, so no conflict):
  - `GET /tasks?status=&scope=`: aggregate across scopes: workshop root, every project in the registry, every `.runtime/agent-scopes/*`, every build agent root, via `task_scope`. Each task is returned with its `scope` key attached. `reconcile` short-circuits on sha so a sweep across scopes stays cheap.
  - `GET/PATCH/DELETE /tasks/{scope}/{task_id}` and `POST /tasks` (scope in body, creation through `dedup_add` so manual tickets dedupe like chat-filed ones). PATCH exposes status/text/priority/assignee/effort (add the additive `effort` kwarg to `tasks.update`).
  - `GET /tasks/{scope}/{task_id}/jobs`: `jobs.read(task_id=...)` join.
- `backend/services/task_scope.py`: `root_for_context("workshop")` (and `"general"`, see Phase 4) resolves to the workshop root. Two lines.

Frontend:
- New `frontend/src/lib/tasks.ts`: TaskStatus (all 7), status-to-kind map (five kinds: e.g. todo/parked=neutral-ish, doing=info, in_review=warn, blocked/cancelled=danger/muted, done=ok; pick within the five shipped kinds), title/description split+join helpers.
- New `frontend/src/pages/Work.tsx` (`/work` route in App.tsx): Segmented `Tickets | Runs`; Tickets list (filters, four list states, create button opening the same drawer empty); ticket drawer per sub-decision (b), including linked-runs section and a Run now button (wired in Phase 3; render disabled-with-hint until then, but do NOT use `not-allowed` cursor per design rules; simplest is to omit the button until Phase 3 lands if the phases ship apart).
- Extract `frontend/src/components/JobsFeed.tsx` from `Jobs.tsx`; `Jobs.tsx` becomes a thin page wrapper; Work.tsx Runs tab reuses it.
- `frontend/src/components/appNav.tsx`: `Jobs` item becomes `Work` -> `/work`; add `"/work": { label: "Work" }` to the breadcrumb map; keep `/jobs` routes working.
- `frontend/src/pages/ProjectDetail.tsx`: swap its local 4-status types/map for the shared `lib/tasks.ts` module (fixes the broken-StatusDot bug for in_review/parked/cancelled); add a per-task link to `/work` drawer.

Verification: tsc + check_design.sh; create/edit/status-change a ticket in each scope kind from the UI and confirm `.tasks.json` + TODO.md export update and reconcile round-trips; a task set to `in_review` via API renders correctly in BOTH Work and ProjectDetail; check ~1440/1024/768/390 plus in-between widths with real content (drawer, long titles, empty scopes); all four list states exercised (loading, empty, error via killed backend, overflow with 100+ tasks).

### Phase 3: chat above jobs (run-now + cards) [SEQUENTIAL after 1 and 2]

Files:
- `scripts/project_ralph.sh`: `--task-id <id>` (claim exactly that id, single iteration of the loop, skip `next-ready`; everything else, budget admission included, unchanged).
- `backend/routes/tasks_all.py`: `POST /tasks/{scope}/{task_id}/run`: open a Job (`trigger=manual`, `task_id` set), Popen the sweep with `--force --job-id <id> --task-id <id>` (project scope: `<slug>`; agent/workshop: `--dir <root> [--assignee agent:<slug>]`), 409 when a sweep already holds the tree (mirror `_run_state` / rely on the sweep's clean flock abort). Reuse the Popen pattern from `projects.py:542-548` (start_new_session, log to the scope's `.runtime/ralph.log`).
- `backend/routes/chat.py` `spawn_task`: optional `run: bool` on the body; when set and the dedup decision is not `merge`/`exact`, call the same launcher and include `job_id` in the response; server-appends an assistant message containing a ```ui-card``` `job-status` block via `chat_store.append_message` so the card persists in the thread.
- `frontend/src/components/chat/UiCard.tsx` + `uiCardActions.ts`: two new kinds.
  - `task-proposal`: fields scope (select over available scopes), text (textarea, title+description convention), priority, effort; actions `addTasks` (POST /tasks), `addTasksAndRun` (POST /tasks then run endpoint), dismiss. This is the card the orchestration doc line 773 already specifies.
  - `job-status`: given `job_id`, polls `GET /api/jobs/{id}` (~5s while running, stop on terminal), renders status pill, duration, then receipt highlights (files count, commits count, summary first line) and links to `/jobs/:id` and the ticket. Lucide icons only.
- `backend/services/chat_context.py`: describe `task-proposal` in the general persona and the build/project personas (the ui-card protocol requires kinds be described before use, `chat_context.py:81-146`); add the build-chat triage rule from sub-decision (a).
- `frontend/src/components/Chat.tsx`: the "Turn into task" control becomes two affordances, File ticket and File and run (both through `spawnTask` with the `run` flag); the static spawn-note ("Track it in Jobs", line 1616) links to the created ticket in `/work` and is superseded by the appended job-status card when `run` was used.
- Wire the Phase 2 drawer's Run now button to the run endpoint, with the 409 surfaced as a status message (not a confirm dialog).

Verification: from a project chat, ask for a multi-step change; model emits a task-proposal card; File and run creates ticket + Job within seconds (no cron wait), job-status card ticks to done, receipt visible in the ticket drawer and JobDetail; from the general chat (workshop scope), same flow against the root store; duplicate proposal dedupes (decision surfaced in the response message); run-now against a busy tree returns 409 and says so in chat; reload the thread mid-run and the persisted card resumes polling.

### Phase 4: orchestrator layer [SEQUENTIAL after 3]

Files:
- `backend/services/task_scope.py`: `root_for_context("general")` -> workshop root (per orchestration doc decision 5), so the general chat owns the root queue for task-patch and proposal defaults.
- `backend/services/chat_context.py` `_general`: persona upgrade, orchestrator duties without a new name: scope vague requests into well-defined tickets before acting; ask clarifying questions early and keep the conversation clean of tool sprawl; prefer task-proposal cards over inline work; use `mcp__workshop__query` for state; report and interpret job outcomes; when the operator expresses a preference or correction, capture it (below). Mention priorities: it may set/adjust ticket priority via task-patch when the operator states relative importance.
- Chat-side preference capture: a ```learning-note``` fenced block, applied in the same finalize path as `task-patch` (mirror `apply_task_patch`: parse, `learnings.record_feedback(text, scope=...)`, strip, note). ~30 lines in `task_scope.py` (or a sibling module) + the call next to the existing task-patch application + one persona paragraph telling the model when to emit it (explicit operator corrections and stated preferences only).
- End-of-phase, operator-gated knob (do not flip silently): once delegation is observed working, optionally lower build-chat `max_turns` from 60 (chat_context.py:673,743). Ship the persona rule first; measure by feel.

Verification: a general-chat conversation files, prioritizes, and runs a root-scope ticket end to end; saying "stop doing X" produces a learnings entry (check `learnings list` CLI / store file) and the next ralph run's injected block contains it (`.runtime/ralph.log` shows "injected learnings"); the general chat still answers plain questions without cards or ceremony (no regression to the clean-conversation requirement); restart via `scripts/restart_backend.sh`.

### Parallelization summary

- Phases 0, 1, 2 as three parallel subagents. File-ownership contract: P0 = crontab, deleted scripts, heartbeat.sh, backlog.py; P1 = reviewer.sh, ralph.sh, project_ralph.sh, JobDetail.tsx; P2 = tasks_all.py, main.py (one mount line), task_scope.py (two lines), lib/tasks.ts, Work.tsx, JobsFeed.tsx, Jobs.tsx, appNav.tsx, App.tsx, ProjectDetail.tsx. No file appears twice. Each stages only its own files (concurrent-writer git index race is a known hazard; never `git add -A`).
- Phase 3 sequential after 1+2 (touches project_ralph.sh again, tasks_all.py again, and the receipt/card interplay must exist).
- Phase 4 sequential after 3 (persona references the card kinds and scope mapping).

---

## Part 4: explicitly not now

- GitHub Issues sync: operator-deferred by name. Nothing in the ticket surface may assume or scaffold it (no `external_ref` field speculation).
- masini folding / clawd rename / naming the orchestrator: deferred, operator-gated (unified-agent-model doc). The orchestrator ships as the existing general identity.
- Board/kanban UI: docs say list + drawer, board deferred. The Work page is a list.
- Heavy learning conductor / any persistent orchestrator runtime: explicitly rejected in the orchestration doc; the dispatcher stays thin and mechanical.
- Memory-keeper scheduled consolidation agent (O5): designed, valuable, and NOT in this plan's critical path; build after the operator has lived with Phases 0-4.
- Task schema migration (title/description columns, richer fields): rejected in D2; revisit only if the first-line convention proves insufficient in practice.
- Reducing build-chat max_turns: operator-gated tuning after Phase 4, never a silent change.
- A store-backed replacement for the deleted work generator (autogen curriculum): separate future decision; deleting the dead one must not smuggle in a new one.
- Any allowlist/audit/rate-limit/confirmation-gate: single-operator system; correctness bounds (budgets, caps, flocks, fencing) already exist and suffice.
