# Autonomy task management: conversation-driven task state and self-continuing work

Status: design, 2026-07-13. SUPERSEDED-AND-EXTENDED 2026-07-13 by `orchestration-and-autonomy.md`, which adopts this doc's Task model, one-write-path reconcile, chat-emission lanes, dispatcher, Job linkage, and acceptance/awaiting_input as its spine (its phases O1-O4 + O7 = this doc's T1-T5) and adds the orchestration fan-out policy, the memory-consolidation organ, the config-preset cascade, and the where-it-applies decision. Read this doc for the detailed task-model and reconcile spec; read the orchestration doc for the unified design and the build order. Code-verified against the working tree on `main`. This is the deferred "task-management system" named in `implementation-plan.md`'s Explicitly deferred section, now designed. It supersedes the model half of `task-management-exploration.md` (whose research conclusions it adopts and concretizes) and is written in the same file-scoped style as `implementation-plan.md`.

The target capability, in the operator's terms: a conversation with an agent (builder or assistant) naturally produces and maintains a living task list, for the project or for the agent itself; the agent then keeps acting on that list autonomously, tracking status, checking items off, adding follow-ups, and updating as it learns, without the operator re-driving it. Discussion, then plan, then autonomous execution, then self-updating task state, continuing until done or blocked.

Legend: ADD (new file/symbol), MOD (modify), DEL (delete + call sites), MIG (on-disk migration).

---

## 1. Assessment: what exists, what is partial, what is missing

### 1.1 What exists and works

- **A real structured task store, per working tree.** `backend/services/tasks.py`: `.tasks.json` at any root is authoritative; `TODO.md` is regenerated from it on every write (atomic writes, owner grouping, parent nesting, markdown import/export). This is the right foundation and is kept.
- **A correct per-project loop.** `scripts/project_ralph.sh` claims tasks through `scripts/project_tasks.py` (`next-open`, `set-status`), marks `doing` before work, maps Ralph exit codes to `done`/`blocked`/released-`todo`, and opens/finishes a Job per sweep. It never sed-edits TODO.md. This is the model consumer.
- **A working completion gate.** `scripts/ralph.sh` re-fires until sentinel + `scripts/reviewer.sh` approval (diff non-empty, no new stubs, typecheck, design lint, pluggable-apps invariant), bounded by iteration and wall caps, with classifier-block detection. Proven in production (~1030 loop Jobs in `jobs.jsonl`).
- **The Job object and trigger vocabulary** (Phase 1 of the platform redesign, live): `backend/services/jobs.py` (append-only `jobs.jsonl`, collapse-by-id, CLI shim), `backend/services/triggers.py` (`manual|cron|event|loop`, loop carries `max_iterations` + `budget` + `completion_gate`), `AutonomyPanel.tsx` as the trigger editor, `POST /projects/{slug}/autonomy/run` for manual sweeps.
- **Task UI in two places.** `AgentTaskPanel` in `frontend/src/components/Chat.tsx` (build chats: list/add/status/delete against `/agents/build/{slug}/tasks`, refetched after every turn at Chat.tsx:657) and the Tasks section on ProjectDetail against `/projects/{slug}/tasks`.
- **The in-chat structured-action primitives to reuse.** The `ui-card` protocol (`chat_context.py:91`, `components/chat/UiCard.tsx`, action registry `uiCardActions.ts`, currently one known kind) and the budget `plan-patch` pattern (`app_personas.py:268`, applied server-side in the turn path, stripped from the stored reply, surfaced via `meta.notes` which already triggers a frontend refetch at Chat.tsx:652).

### 1.2 What is broken or missing, judged against the target

**1. Chat-to-tasks is freeform persona instruction, and it is structurally self-destroying.** Both build personas (`chat_context.py:653` and `:715`) instruct the model: "When you finish a task, update TODO.md (check items off, add follow-ups)." But TODO.md is a regenerated export of `.tasks.json` (`tasks.py:143-147`); any structured write (task panel click, `project_tasks.py set-status` from a sweep) regenerates TODO.md and silently destroys every edit the chat agent made to it. In the other direction, a `- [ ]` line the agent appends is invisible to `next-open` (which reads the store, not the file). The module docstring of `project_tasks.py` states the rule ("must not sed-edit TODO.md directly, that would drift") while the chat personas instruct exactly that. So today a discussion does NOT reliably produce a task list the loop will act on; it produces markdown edits that the store clobbers.

**2. Three divergent task representations at the workshop root.** The root loop (`cron_agent.sh:249-315`) greps and sed-edits root `TODO.md` directly using a bare `[ ]` line format; `work_generator.sh` appends bare `[ ]` lines with `gen:<sha>` suffixes; `services/todos.py` (the parser behind `tasks.py` migration) only matches `- [ ]` dash form, so root TODO.md content cannot even migrate into the store as-is; root `.tasks.json` exists and is empty (untouched since June 16) while root TODO.md carries the entire real queue. The root and project worlds run two different task systems with the same filenames. Also format-adjacent state markers (`[proposed]`, `[~] BLOCKED: ...`) exist only as prose conventions read by `graduate_proposals.sh` / `work_verifier.sh` / `autonomy_report.sh` / `curriculum_tick.sh` / `daily_todo_digest.sh`.

**3. Nothing dispatches project loops in the background.** `project_ralph.sh` has exactly one caller: the manual `POST /{slug}/autonomy/run` endpoint (`routes/projects.py:525`). The crontab runs `cron_agent.sh` (root queue only) plus commute/curriculum entries. A project with a `loop` trigger is never swept unattended; the AutonomyPanel copy ("a loop trigger makes it eligible for unattended background sweeps") is currently false in code. This is the single biggest gap against "will continue acting on it".

**4. Agent-scoped work exists as a store but has no consumer.** A builder's own tasks (`/agents/build/{slug}/tasks`, owner=slug, root = linked project dir else agent cwd, `routes/agents.py:344-356`) are written by the operator and the task panel, but no loop ever claims them: `cron_agent.sh` works the root queue, `project_ralph.sh` works a project queue with no owner filter. Registry-agent cron triggers (`agents/<slug>.md` frontmatter) also have no runner: `_update_subscriptions` (agents_registry.py:395) wires only `event` triggers into the cron event-drain; `supervisor.py` runs only Letta `continuous` agents. So "tasks for itself" is a UI drawer, not a capability, and assistants have no task scope at all.

**5. State does not flow back live.** The chat task panel refetches only at end-of-turn in that same chat; loop-made transitions (a sweep marking tasks done overnight) reach the UI only when the operator manually reloads ProjectDetail or when AutonomyPanel's poll happens to show Job counts. There is no task-level linkage to Jobs (Job has no `task_id`; Task has no `job_ids`), so "what ran against this task, and what happened" is unanswerable. `awaiting_input` exists in the Job status enum with zero emitters anywhere (verified by grep), so "stopped, needs the operator" has no signal.

**6. No discussion-to-act gesture.** After a planning conversation, the path to "a loop is now working this list" is: operator manually re-types the plan into the task panel (or trusts the doomed TODO.md edit), then presses Run now. Nothing lets the model propose tasks as structured objects, nothing lets one click queue them and start the loop.

**7. The Task model is thinner than the loop needs.** Current fields: id/text/status(todo,doing,done,blocked)/owner/parent/timestamps. No priority, no dependencies, no acceptance spec (goal-type tasks per the exploration doc), no evidence, no blocked reason, no provenance. `owner` conflates originator with delegate; in practice it functions as an assignee (agent slug or "operator") and the exploration doc already ruled that the human always owns, agents are delegates.

### 1.3 Direct answers to the assessment questions

- Does a discussion actually create + maintain a structured task list? **No.** It appends to a freeform export by persona instruction, which the structured store then clobbers.
- One task model shared between conversation, panel, and loop? **Half.** Project trees share `.tasks.json` between panel and loop correctly; the conversation writes to the wrong layer; the workshop root shares nothing.
- Does the loop update state as it works, reflecting live into UI? Loop updates state correctly (project scope). Reflection is manual-refresh only; no task-Job linkage; no awaiting-input signal.
- Can an agent maintain tasks for itself? Store yes (builders), consumer no, assistants no scope at all, agent cron triggers unexecuted.
- Clean path from "we talked about it" to "a loop is working the list"? **Missing entirely** (no structured emission, no background dispatch).
- Job intersection: sweep = one Job today; per-task executions are Jobs only in the root loop; no task linkage either way. The reserved per-project Task scope from the proposal is exactly `tasks.py` + this design.

---

## 2. Design

### 2.1 Core decisions

1. **One store, one write path.** `.tasks.json` per scope stays the single source of truth; `TODO.md` becomes a strictly regenerated export plus a tolerated hand-edit surface healed by an explicit reconcile step. Nothing (agent, script, or human tooling) is ever instructed to edit TODO.md as the way to change task state again.
2. **A scope is a directory.** Task scopes are: a project tree (`projects/<slug>/`), a builder agent's cwd (freestanding builders; project-linked builders resolve to the project, exactly as `_tasks_root` does today), an assistant agent's home (`.runtime/agent-scopes/<slug>/`, new), and the workshop root itself (the registered `self` project). This gives "for the project OR for itself" one mechanism: the operator's ask is satisfied by scope choice, not a second system. Ownership stays with the operator; `assignee` marks the delegate (Linear model, per the exploration doc).
3. **Agents write tasks through typed interfaces, not file edits.** Builders (Claude Code runtime, have Bash) use a task CLI. Assistants (data tier, no fs) emit a `task-patch` fenced block applied server-side in the turn path, exactly like the budget `plan-patch`. Both are direct writes: this is a single-operator system and the agent is a trusted delegate; no confirmation gate. The `ui-card` task-proposal is the affordance for "here is the plan we discussed, queue it and go", not an approval ritual.
4. **The dispatcher is the missing organ, and it is small.** One dispatch pass inside the existing 20-minute cron tick walks project loop triggers and agent cron/loop triggers and spawns the (already-built) sweep machinery. No new daemon.
5. **Job gains `task_id` and `parent_id` (additive).** A sweep is a parent Job; each task execution is a child Job bound to its task. Tasks accumulate `job_ids`. Interactive chat turns remain not-Jobs (locked decision 7); a chat that queues tasks and starts a loop creates the Job through the existing run endpoint.
6. **Completion authority stays split exactly as proven:** the worker never self-marks done; `reviewer.sh` gates code-shaped tasks in-loop; an `acceptance` field routes command-verified goals and operator-gated items (`in_review`); `awaiting_input` finally gets an emitter.
7. **Iteration caps, wall budgets, and the concurrency bound on background sweeps are correctness and cost bounds on unattended loops, allowed under the no-cruft rule.** Nothing else (no approvals, no audit trails) is added.

### 2.2 The canonical Task model

Extends `services/tasks.py` in place (same file, same store, version bump to 2, old records normalize forward losslessly).

```jsonc
Task {
  "id": "uuid4hex",
  "text": str,
  "status": "todo" | "doing" | "in_review" | "blocked" | "done" | "cancelled",
  "assignee": null | "agent:<slug>" | "operator",   // delegate; replaces `owner` semantics
  "parent": str | null,
  "priority": int,                 // default 0; higher first
  "depends_on": [task_id],         // gates readiness
  "acceptance": {                  // optional; absent = reviewer gate (default)
    "kind": "reviewer" | "command" | "operator",
    "spec": str | null             // shell command for kind=command
  },
  "max_iterations": int | null,    // per-task override of the ralph cap (goal tasks)
  "blocked_reason": str | null,
  "job_ids": [str],                // executions that worked this task
  "evidence": [ { "kind": str, "ref": str, "ts": iso } ],
  "source": { "kind": "operator" | "chat" | "agent" | "autogen", "ref": str | null },
  "provenance": str | null,        // stable dedupe key (work_generator gen:<sha> moves here)
  "created": iso, "updated": iso
}
```

Notes:
- **Statuses.** `in_review` (worker finished, gate is the operator) and `cancelled` are added; `backlog/ready` from the exploration doc are collapsed into `todo` + the readiness predicate (single operator does not need a two-stage backlog). Markdown export boxes: todo `[ ]`, doing `[~]`, in_review `[?]`, blocked `[!]`, done `[x]`, cancelled `[-]`, with the existing suffix-tag rendering for the non-binary states.
- **Readiness predicate** (replaces "first todo"): `status == todo AND every depends_on is done|cancelled`, ordered by `priority` desc then `created` asc. Exposed as `next_ready(root, assignee=None)`.
- **`owner` migration.** `_normalize` maps legacy `owner` to `assignee` (`"operator"` stays; an agent slug becomes `agent:<slug>`). The per-agent endpoints' owner-scoping becomes assignee-scoping with identical behavior. Export grouping switches from owner to assignee headings.
- **Goal-type tasks** need no discriminator: a "goal the agent works until met" is a task with `acceptance.kind = "command"` (tests/eval command) plus an optional `max_iterations` override. Explicit checklists and open goals share one lifecycle, as the exploration doc argued.
- **Concurrency.** `tasks.py` gains an `fcntl` flock around load-modify-persist (`.tasks.json.lock`), because the backend API and the shell CLI genuinely interleave writes to the same store during a sweep. One lock in one module covers every writer, since both go through `services.tasks`.

### 2.3 One source of truth, and the reconcile that ends the drift

`TODO.md` stays: it is git-friendly, greppable, readable in any editor, and it is what `live_context()` injects into every build turn. But its contract changes from "co-equal surface" to "export plus healed inbox":

- Every store write regenerates it (unchanged).
- `.tasks.json` records `export_sha` (hash of the last rendered export).
- ADD `tasks.reconcile(root)`: if the current TODO.md hash differs from `export_sha`, parse it (extend `todos.py` to also match bare `[ ]` lines and the `[~]`/`[!]`/`[?]`/`[-]` boxes): checkbox flips are applied to the matching store task (normalized-text match); unmatched new checklist lines are imported as new tasks with `source.kind = "chat"`/`"operator"` unknown-origin default and no destructive effect; lines missing from the file are ignored (the store wins on deletion; deletion happens through the API/CLI). Then re-render and update `export_sha`.
- `reconcile` runs at the top of `next_ready`, `list_tasks`, and `tree` (cheap: hash compare short-circuits). Result: a stray hand edit, or a legacy agent habit of editing TODO.md, is absorbed instead of destroyed; and the loop never misses a task added by any route.

This keeps exactly one authority while making the export safe to touch, which is the honest failure mode of humans-and-models sharing a markdown file.

### 2.4 How a conversation emits and maintains tasks

Three lanes, all writing to the same store, replacing the "update TODO.md" persona text in both build providers:

1. **Builder/project chats (Claude Code runtime): the task CLI.** Extend `scripts/project_tasks.py` with `add`, `note` (append evidence/note), `block` (status + reason), `list-json`, `next-ready` (keep `next-open` as alias), all assignee-aware. Persona text (`chat_context.py` ProjectContextProvider and BuildAgentContextProvider): "Task state lives in the structured store. To add or update tasks run `python3 ~/workshop/scripts/project_tasks.py <verb> ...` via Bash. Never edit TODO.md directly; it is a generated export." The agent managing its list mid-conversation is now a deterministic, verifiable act.
2. **Assistant/data chats: the `task-patch` block.** New fenced protocol, same seam as the budget plan-patch: the model emits
   ```
   ```task-patch
   {"scope": "project:<slug>" | "agent:<slug>",
    "add": [{"text": str, "priority"?: int, "depends_on"?: [id], "acceptance"?: {...}}],
    "update": [{"id": str, "status"?: str, "text"?: str, "blocked_reason"?: str}],
    "note": [{"id": str, "text": str}]}
   ```
   ```
   applied server-side in the turn finalize path (the provider-actions step at `build_streaming.py:513` and the equivalent in `send_message`), stripped from the stored reply, outcome appended to `meta.notes`. The scope defaults to the thread's context binding; a provider only applies patches against its own scope (a project chat writes its project store, an agent chat writes the agent scope): this is a correctness rule, not a permission system. `meta.notes` already drives the frontend refetch hook, so the panel updates the moment the turn lands.
3. **The explicit "queue it and go" gesture: a `task-proposal` ui-card.** New kind in `uiCardActions.ts` KNOWN_KINDS. When a discussion converges on a plan of discrete items, the model emits one card listing the proposed tasks (editable textarea-per-item fields, priority select), with actions `addTasks` (POST each to the scope's task endpoint) and `addTasksAndRun` (same, then `POST /projects/{slug}/autonomy/run`). This is the one-click bridge from "we talked about it" to "the loop is working it". Persona guidance rides the existing per-provider card instructions. For agent scopes the run action dispatches the agent sweep (2.5).

Voice and Discord paths inherit lane 2 for free (they ride `send_message`).

### 2.5 How the loop consumes and updates tasks

**Dispatch (the new organ).** ADD `scripts/dispatch_triggers.sh`, called by `cron_agent.sh` after pre-flight (Phase 0.5), bounded by the tick budget and the `AUTONOMY_DISABLED` flag it already inherits:

- **Project loop triggers:** for each project in `projects.json` whose triggers include `loop`, if `next_ready` is non-empty and no Job for that project is `running` (the existing `_run_state` logic, stale-guarded), spawn `project_ralph.sh <slug>` in the background. Cap: at most one background project sweep started per tick (cost bound; the per-project flock and Job check make it safe to raise later).
- **Agent cron/loop triggers:** for each registry agent (`agents_registry.list_agents`), for each `cron` trigger, fire when due against `.runtime/trigger-state.json` (`{key: last_fired_iso}`; due = a helper in `triggers.py`, croniter if importable, else a conservative minute-window match). `loop` triggers on agents behave like project loops: fire when the agent's scope has ready tasks. Dispatch by runtime: builder runtime runs the sweep machinery over its scope (below); assistant runtime opens a Job and runs one bounded data-tier turn (`llm_router` with the agent persona + its scope's TODO export + the task CLI unavailable, so its persona uses task-patch semantics via a direct `services.tasks` apply after the turn). Letta `continuous` agents stay on the supervisor unchanged.
- **Agent sweeps:** MOD `project_ralph.sh` to accept `--dir <path> --assignee agent:<slug>` in place of a project slug (a scope sweep is the general case; the project sweep becomes the parameterization it already almost is). Claims via `next_ready(root, assignee=...)` so an agent working "its own" tasks in a shared project store only claims what is assigned to it or unassigned.

**Per-task Jobs.** MOD `jobs.py`: `start()` gains `task_id` and `parent_id` (additive, CLI flags `--task-id`/`--parent-id`, `read()` gains a `task_id` filter). MOD `project_ralph.sh`: the sweep keeps its parent Job; each claimed task opens a child Job (`trigger=loop`, `task_id`, `parent_id`) finished with the ralph outcome; `set-status doing` records the child job id into the task's `job_ids`. Ralph iterations stay internal to `ralph.sh` (an iteration is not a Job; the bounded execution is the task attempt). This makes Jobs page, Job detail (addendum P5), and task history one linked graph.

**Status transitions, exactly:**

```
todo -(claim: sweep or operator)-> doing
doing -(ralph 0 + acceptance reviewer/command passed)-> done
doing -(ralph 0 + acceptance operator)-> in_review        [+ evidence]
doing -(ralph 3 classifier, or agent block via CLI)-> blocked [+ blocked_reason]
doing -(ralph 1/2/4: caps or startup)-> todo               [released, retried next sweep]
in_review -(operator)-> done | todo (with note)
blocked -(operator unblocks / dependency lands)-> todo
any -(operator)-> cancelled
```

`acceptance.kind = "command"`: after the sentinel, the sweep runs `spec` (bounded timeout) in the scope; pass admits done, fail attaches output as evidence and releases to todo (or blocked after `max_iterations` total attempts recorded on the task). `reviewer` is the default and is the current behavior verbatim. The worker prompt in `ralph.sh` swaps its "mark `[~] BLOCKED` in TODO.md" instructions for the task CLI (`block`, `add` for proposed sub-tasks), which also retires the `## Proposed` prose convention inside project scopes.

**Follow-ups.** The worker adds follow-up tasks mid-run via the CLI (`source.kind = "agent"`, `source.ref = <job_id>`). `work_generator.sh` keeps its root fuel role but writes through the CLI with `source.kind = "autogen"` and its `gen:<sha>` moving into `provenance` (dedupe becomes a store query instead of a grep).

**Stopping and awaiting input.** A sweep ends when: queue drained (Job `done`), budget/iteration cap (Job `done` with counts, remaining tasks released), classifier block (task blocked, sweep continues to next task, Job `done`), or **nothing ready but work outstanding**: if open tasks remain and every one is `blocked` or `in_review`, the sweep finishes its Job as `awaiting_input` with `evidence = [{task_id, status, blocked_reason}]`. That is the first real emitter of `awaiting_input`; Home's AttentionStrip already surfaces the status, and Job detail (P5) renders what is being waited on. Providing input is not a new modality: the operator unblocks/answers by editing the task (panel or chat), and the next tick proceeds. The loop trigger stays armed; a project in `awaiting_input` costs nothing until the operator acts (the dispatcher skips projects whose last Job is `awaiting_input` and whose task store has not changed since, via `export_sha`/mtime compare, so it does not re-open an identical sweep every 20 minutes).

### 2.6 Live task state back in the UI

- **Versioning:** task list responses (`/projects/{slug}/tasks`, `/agents/build/{slug}/tasks`, new agent-scope endpoint) include `version` (store file mtime-hash). `GET /{slug}/autonomy` adds `tasks_version` and queue counts `{ready, doing, blocked, in_review}`.
- **Panels:** generalize `AgentTaskPanel` into a scope-generic `TaskPanel` (component extraction, same drawer UX) mounted for `build:` (as now), `project:` chats (new: the store already lives there, the panel was arbitrarily build-only), and `agent:` chats. It polls `tasks_version` every 5s while the scope has a `running`/recent Job (AutonomyPanel already runs a 2.5s poll when running; reuse the cadence source) and refetches on turn end as today. Status select gains the new states; blocked rows show `blocked_reason`; in_review rows get one-click Approve (set done) since the operator IS the gate for those.
- **AutonomyPanel:** shows the queue counts next to the trigger list and renders `awaiting_input` as the warn state it already maps (AutonomyPanel.tsx:57), linking to the task panel.
- **Home:** the AttentionStrip picks up `awaiting_input` sweeps for free once they exist; add `in_review` task counts to the attention query so operator-gated work is one glance away.
- No SSE bus for tasks in this round: turn-end refetch + bounded polling while a sweep runs covers the real cases at single-operator scale; the events.jsonl seam is noted for later if polling ever chafes.

### 2.7 Reuse vs build

| Piece | Verdict |
|---|---|
| `services/tasks.py` store, atomic write, export render | REUSE, extend fields/statuses/lock/reconcile in place |
| `scripts/project_tasks.py` CLI | REUSE, add verbs |
| `scripts/ralph.sh` sentinel loop + `reviewer.sh` gate | REUSE unchanged (prompt text updated only) |
| `scripts/project_ralph.sh` sweep | REUSE, generalize to scope + child Jobs |
| `services/jobs.py`, `triggers.py` | REUSE, additive fields (`task_id`, `parent_id`, cron due helper) |
| ui-card primitive + action registry | REUSE, one new kind |
| plan-patch apply seam in the turn path | REUSE the pattern for `task-patch` |
| `AgentTaskPanel`, AutonomyPanel, AttentionStrip | REUSE, generalize/extend |
| Dispatcher (`dispatch_triggers.sh`) | BUILD (small) |
| Assistant agent scopes (`.runtime/agent-scopes/`) | BUILD (a directory convention plus one endpoint family) |
| Root-queue convergence (self project migration) | BUILD (migration + script rewires) |
| External tracker sync (GitHub/Jira/Linear shadow tasks) | DEFERRED, the `source`/`provenance` fields are the reserved seam per the exploration doc |
| Kanban/board UI | DEFERRED; list + drawer is enough now |

### 2.8 Interlocks with the rest of the plan

- Locked decisions preserved: interactive chat turns are still not Jobs; no promotion pipeline is reintroduced (the task-proposal card queues work, it does not convert chats); no multi-tenant guardrails.
- The review-addendum chat fixes (P1-P3: streamed turns everywhere, agent binding) should land first or alongside Phase T2: `task-patch` for data providers rides the shared prompt/finalize path those fixes consolidate, and assistant agents need the `agent:` binding to have a chat surface at all. T1 and T3 do not depend on them.
- Letta deferral untouched: nothing here reads or writes Letta; assistant sweeps go through `llm_router`.
- Store/Grant (Phase 4): task stores can later register as platform Stores of type `Task`; nothing in this design blocks or requires it.

---

## 3. Build plan

Global rules: same as `implementation-plan.md` (branch discipline, tsc + check_design.sh on frontend steps, no committed stubs, the bridge agent smoke check on backend restarts, no em-dashes/emoji). Any phase touching `cron_agent.sh`, `ralph.sh`, or `project_ralph.sh` is bracketed by the `AUTONOMY_DISABLED` pause. The crontab fires every 20 minutes against the live tree.

### Phase T1: canonical model, one write path, reconcile

**MOD**
- `backend/services/tasks.py`: statuses gain `in_review`/`cancelled`; fields per 2.2 (`assignee` normalizing legacy `owner`, `priority`, `depends_on`, `acceptance`, `max_iterations`, `blocked_reason`, `job_ids`, `evidence`, `source`, `provenance`); store `version: 2` (read v1, write v2); `next_ready()`; flock; `export_sha` + `reconcile()` wired into `list_tasks`/`tree`/`next_ready`; export boxes for the new states.
- `backend/services/todos.py`: parser also matches bare `[ ]` lines and the extended boxes (needed by reconcile and by the T5 migration).
- `scripts/project_tasks.py`: verbs `next-ready` (alias `next-open`), `add` (with `--assignee --priority --source --provenance --acceptance-kind --acceptance-spec`), `note`, `block`, `list-json`; `set-status` gains `--job-id` (appends to `job_ids`).
- `backend/routes/projects.py` + `backend/routes/agents.py` task endpoints: accept/return the new fields; agent endpoints scope by assignee instead of owner (behavior identical).
- `backend/services/chat_context.py`: BOTH build personas (`:653`, `:715`) drop "update TODO.md" and gain the task-CLI instruction; `live_context` unchanged (still injects the export).
- `scripts/ralph.sh` prompt text: BLOCKED/sub-task instructions point at the CLI instead of TODO.md prose.

**Verify:** unit-level: v1 store loads, normalizes, round-trips as v2; hand-edit TODO.md (flip a box, add a bare `[ ]` line), call `list_tasks`, confirm the flip applied and the line imported, export regenerated, nothing lost. Drive a build chat turn that adds a task via the CLI and confirm the panel shows it without any TODO.md hand-edit surviving incorrectly. `tsc` n/a; backend restart + the bridge agent smoke check.

### Phase T2: conversation emits tasks (task-patch, task-proposal card, generalized panel)

Sequenced with the addendum's P1-P3 (shared turn path + agent binding) where noted.

**ADD**
- `task-patch` protocol: constant in `chat_context.py` (appended to provider preambles whose scope has a task store); apply + strip in the turn finalize seam (`build_streaming.py` provider-actions step and `send_message`), writing through `services.tasks` against the thread's bound scope only; outcome into `meta.notes`.
- `.runtime/agent-scopes/<slug>/` convention + task endpoints for registry assistants (`GET/POST/PATCH/DELETE /agents/{slug}/tasks` via `agents_registry`, same handlers over the scope root).
- `task-proposal` ui-card kind: `uiCardActions.ts` (KNOWN_KINDS + `addTasks` / `addTasksAndRun` handlers posting to the scope's task endpoint, then the autonomy run endpoint), persona guidance in the build/project providers describing when to emit it.

**MOD**
- `frontend/src/components/Chat.tsx`: extract `AgentTaskPanel` into a scope-generic `TaskPanel` (`components/TaskPanel.tsx`), mounted for `build:`, `project:`, and `agent:` context keys; new statuses, blocked reasons, in_review Approve; `tasks_version` polling while the scope's last Job is running.
- `frontend/src/pages/ProjectDetail.tsx` Tasks section: new fields/states rendering (assignee tag, priority, blocked reason, in_review approve).

**Verify:** in a project chat, discuss a plan, receive a task-proposal card, click Add + run: tasks appear in the store, a manual Job opens, the sweep works them; an assistant chat emits a task-patch and the panel refreshes at turn end with the stored reply free of the fenced block; `agent:bogus` scopes 404. tsc + check_design.sh; widths sweep on the panel per the UI rule.

### Phase T3: the dispatcher and Job linkage (background continuation)

AUTONOMY_DISABLED bracket around the script edits.

**ADD**
- `scripts/dispatch_triggers.sh` per 2.5 (project loops, agent cron/loop, `.runtime/trigger-state.json`, one-background-sweep-per-tick cap, skip-unchanged-awaiting_input rule).
- `triggers.py`: `due(cron_expression, last_fired, now)` helper.

**MOD**
- `cron_agent.sh`: call the dispatcher after pre-flight (Phase 0.5), within the tick budget.
- `backend/services/jobs.py`: `task_id`/`parent_id` on `start()`, CLI flags, `read(task_id=)`.
- `scripts/project_ralph.sh`: `--dir/--assignee` generalization; child Job per claimed task; `set-status ... --job-id` wiring; `next-ready` claim.
- `backend/routes/projects.py` `/autonomy/log` and `frontend/src/pages/Jobs.tsx`/`JobDetail` (post-P5): render task binding when present.

**Verify:** set a loop trigger on a test project with two ready tasks, disable nothing, wait one tick (or run `cron_agent.sh` by hand): a parent loop Job plus per-task child Jobs appear with correct `task_id`/`parent_id`; tasks transit todo -> doing -> done; a second tick with an empty queue starts no sweep. Set a cron trigger on a builder agent with a task in its own scope; confirm the due-fire claims only its assignee's task. The bridge agent smoke check (backend restarted for jobs.py).

### Phase T4: gates, awaiting_input, follow-ups

**MOD**
- `scripts/project_ralph.sh`: honor `acceptance` (command runner with timeout + evidence attach; operator kind routes to `in_review`); per-task `max_iterations` override passed to `ralph.sh`; the nothing-ready-but-outstanding ending finishing the sweep Job as `awaiting_input` with the blocked/in_review evidence list.
- `scripts/work_generator.sh`: write through the CLI (`source=autogen`, `provenance` dedupe) instead of appending lines. (Root-scope effective after T5; the CLI targets the root store which T5 makes authoritative; sequence this edit with T5 if T5 has not landed.)
- `frontend/src/pages/Home.tsx`: attention includes in_review counts; awaiting_input cards link to the task panel/Job detail.

**Verify:** a task with `acceptance.kind=command` and a failing spec releases with evidence attached and blocks after its cap; an operator-gated task lands in in_review and Approve completes it; block every remaining task in a queue and confirm the next sweep Job ends `awaiting_input`, Home surfaces it, and the dispatcher does not re-fire until a task changes.

### Phase T5: root-queue convergence (the workshop root becomes a normal scope)

Highest-risk phase (live cron), last deliberately. AUTONOMY_DISABLED for the whole cutover.

**MIG**
- `scripts/migrate_root_tasks.py`: parse root `TODO.md` (bare boxes, `[proposed]`, `[~] BLOCKED: ...`) into the root `.tasks.json` (open items as tasks with correct statuses/reasons/provenance; `[proposed]` becomes `todo` with `source.kind=autogen` and a `proposed_until` note per current graduate semantics or is graduated at migration time if older than the threshold); `## Log` moves to `.runtime/worklog.md` (append target updated); old file archived as `TODO.md.migrated`; export regenerated.

**MOD**
- `cron_agent.sh` Phase 2: replace the grep/sed claim-and-mark logic with `project_tasks.py next-ready/set-status` against the root store (identical loop shape to `project_ralph.sh`); log/notify lines write to the worklog, not TODO.md.
- Repoint the root-TODO grepping scripts at the store/CLI: `graduate_proposals.sh`, `work_verifier.sh`, `curriculum_tick.sh` (threshold reads ready-count via `list-json`), `daily_todo_digest.sh`, `autonomy_report.sh`.

**Verify:** with autonomy paused: run the migration, confirm open-item parity (count and text) between `TODO.md.migrated` and the store; run one manual `cron_agent.sh`: it claims from the store, marks done via the gate, and TODO.md is regenerated (no sed writes: `grep -n "sed -i" cron_agent.sh` returns nothing task-related); re-enable and watch one live tick. Digest/report scripts produce sane output from the store.

---

## 4. Risk register

1. **Live cron racing a half-edited script** (T3, T5). Guard: AUTONOMY_DISABLED bracket, same discipline as the platform Phase 1 cutover; scripts are edited whole-commit.
2. **Reconcile mis-matching a hand-edited line** and duplicating a task. Guard: normalized-text exact match only; anything unmatched imports as NEW rather than mutating existing; reconcile never deletes. Worst case is a duplicate todo, visible and cheap to cancel.
3. **Store lock contention** between a sweep's CLI writes and the API. Guard: flock is held only across load-modify-persist (milliseconds); both writers share `services.tasks` so there is exactly one locking implementation.
4. **Dispatcher runaway** (a sweep every tick against a stuck queue). Guard: skip-unchanged rule on `awaiting_input`, the running-Job check, released-task retry being bounded by per-task attempt counts once T4's cap lands, and the one-sweep-per-tick cap.
5. **Root migration losing queue history.** Guard: `TODO.md.migrated` archived verbatim; parity check in the verify step; the Log becomes a plain append file rather than being parsed.
6. **task-patch applied against the wrong scope.** Guard: scope is derived from the thread binding server-side, never from the model's `scope` field alone (the field must match the binding or the patch is rejected with a note in `meta.notes`).
