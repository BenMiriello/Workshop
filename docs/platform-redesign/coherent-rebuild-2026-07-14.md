# Coherent rebuild plan (2026-07-14)

This is the **canonical** plan. It supersedes the scattered O1-O7 notes as the
source of truth for sequencing. Those docs remain valid as detailed design
references; this doc decides the concept model, what is canonical vs retired,
and the staged migration order. Everything built from here follows this doc so
we stop bolting new layers onto old ones.

Author: assistant, from four parallel audits (jobs / agents-liveness /
autonomy-substrate / external research) run 2026-07-14. Operator: the operator.

Hard rules still apply: single-operator (no defensive cruft), no em-dashes, no
emoji in UI, lucide icons, src/ui primitives, the excluded work org separation.

---

## 0. Diagnosis: why it looks like "old system plus bolted-on new stuff"

Because that is literally what it is. **Three separate systems run at once, and
the one that matches the new plan is the only one doing nothing.**

1. **Legacy Letta/Ollama heartbeat supervisor** (`backend/services/supervisor.py`,
   the 2026-05-22 build). RUNNING now. It is what floods the Jobs feed: 1,939 of
   2,311 jobs are its `trigger:loop` heartbeats. Each carries a raw Letta agent
   UUID (`agent-a3b99f4b-...`, the "opaque string"), its chain-of-thought dumped
   into a freeform `evidence` field (the "evidence that makes no sense"), and 345
   `ollama_failed:` records because the local Ollama model server was down (the
   "errors that make no sense"). `exit_code` is never set.

2. **Legacy root `TODO.md` cron loop** (`cron_agent.sh`, pre-ticket-system). RUNNING
   now. A `grep`/`sed` loop over prose `TODO.md` that does the actual autonomous
   work today. It never touches the structured task store.

3. **The new structured ticket system (O1-O4)** (`tasks.py`, `task_scope.py`,
   `dispatch_triggers.sh`, `project_ralph.sh`). BUILT and wired into cron, but it
   **dispatches zero sweeps** every tick because (a) all 9 projects have
   `autonomy.mode = off`, so no scope gets a `loop` trigger, and (b) the workshop
   root is a deliberately inert "protected" scope pending a phase that never came.
   Its Job graph fields (`chat_id`, `task_id`, `parent_id`, `project_slug`) are
   populated on **0 of 2,311** jobs. It has, in practice, never run.

Everything the operator sees as "a mess" follows from this: the Jobs page shows
system #1's junk, the Agents page still speaks system #1's vocabulary, autonomous
work still flows through system #2, and system #3 (the plan) is scaffolding.

Two more concrete defects the audits pinned down:

- **Agent liveness is a stale flag, not reality.** `build_agents.json.state` is set
  to `"running"` on the first chat turn (`build_agents.ensure_session_id`) and
  **never reset to idle** when the turn finishes (`_finalize_turn` only calls
  `touch()`). So every builder that ever ran once reports "live" forever. 14 "live"
  = 14 that ran at least once; only 0-3 `claude -p` subprocesses actually exist.
- **The status triad is three unrelated axes shown as one row.** "running" (stale
  lifecycle flag) + "session live" (a session UUID string merely exists) + "preview
  stopped" (a real `os.kill` PID check). Two are stale booleans, one is real, so
  they contradict each other on screen.
- **Build agents do all work in-thread.** A build turn spawns one full-tool
  `claude -p` that edits files itself. It never enqueues into the task pool; it only
  records task status after the fact. So it is a monolithic worker, not an
  orchestrator, which is the opposite of the vision.

---

## 1. The decided concept model (pick the lane)

Confirmed by external research (OpenAI Assistants = Assistant/Thread/Run,
Temporal = Workflow/Run, background-agent products) and it matches the operator's
own framing. **Four nouns, plus one optional:**

- **Project** — the scoping AND quota boundary. Everything belongs to a project.
  The workshop itself is the `self` project (root scope), finally made real.
- **Agent** — a reusable persona/config: model, tools, skills, system prompt,
  learned memory. One concept with two uses: it is both *who you chat with* and
  *the runtime that works a ticket*. We do NOT split "chat agent" from "worker
  agent." An Agent can run in an interactive turn or a background sweep.
- **Chat** — a human-driven conversation, bound to an Agent (or to "general," no
  agent). Ephemeral in spirit. The operator steers here. Chats are NOT metered by
  the autonomy quota.
- **Task** — the autonomous unit of background work. Lives in the shared per-scope
  queue. Executed by an Agent inside a Project. **A Task must outlive the Chat that
  spawned it**, so it is self-contained: spawning a Task from a Chat snapshots the
  relevant context into the Task's description, and keeps only a one-way
  `provenance:{chat_id}` link for traceability. This is the load-bearing rule.
- **Run** (optional, operator-visible history) — one execution attempt of a Task.
  This is the `attempt` + `claim_token` + lease record from the self-healing design.

Relationships:

```
Project ─┬─ Chats   (human-driven, unmetered)
         ├─ Agents  (reusable configs / runtimes, carry learned skills+memory)
         └─ Tasks   (queued work, metered)  ──▶ Runs (attempts)

Chat ──spawns──▶ Task     (snapshot + one-way provenance; Task outlives Chat)
Task ──executed-by──▶ Agent ──in──▶ Project
```

**Naming migration:** the operator-facing verb becomes **Chat** and **Agent**, not
"build agent"/"builder." Backend module/route/store renames (`build_agents.py`,
`/agents/build/*`, `build:<slug>`) are larger and can be aliased rather than
renamed in one shot, but the UI must stop saying "builder."

**What becomes canonical vs retired:**

| Concern | Canonical (keep + finish) | Retire |
|---|---|---|
| Task queue | structured store `tasks.py` per scope | legacy root `TODO.md` grep/sed loop in `cron_agent.sh` |
| Background exec | `dispatch_triggers.sh` + `project_ralph.sh` + `ralph.sh` | Letta/Ollama heartbeat supervisor |
| Jobs feed | ticket-shaped Jobs from real sweeps | heartbeat junk jobs |
| Concept/naming | Project/Agent/Chat/Task/Run | "build agent"/"builder", Letta assistant creation flow |
| Model tier | Claude via `claude -p` (subscription) | Ollama local tiers (source of `ollama_failed`) |

---

## 2. Subsystem designs (from research + audits)

### 2a. Jobs as real tickets
- API enriches `agent_id` -> `agent_name` (join the agents registry; the supervisor
  already knows the name). UI shows the name, UUID demoted to tooltip.
- Persist a short human `title` + one-line `summary` at start/finish instead of
  dumping raw chain-of-thought.
- Add a real `error` field; render `status=="failed"` under a red "Error" section,
  not the generic "Evidence" pre. Set `exit_code`.
- Populate the `chat_id` link in live writers; show "Managing chat" on the card.
- Populate the parent/child + task graph for sweeps (already coded in
  `project_ralph.sh`, just never ran). Show parent sweep + worked Task.
- Translate triggers to operator language: loop -> "Autonomous", cron ->
  "Scheduled", event -> "Event", manual -> "Run now".

### 2b. Agents page tells the truth
- Liveness computed on read from real signals (a turn actually in flight:
  `build_streaming.TURNS` / `.runtime/turns/*/meta.json` status + `_proc_alive`),
  NOT the persisted "running" flag. Minimal first fix: reset `state->idle` in
  `_finalize_turn`. Better: drop the persisted "running" concept entirely.
- Split the status triad into three honest axes: Agent (idle/working), Session
  (valid/none via real `session_alive()`), Preview (up/down, already real).
- Remove old-system leakage: the live-Letta fallback roster injection
  (`routes/agents.py:103-131`) and the second "New agent" (Letta assistant)
  creation flow. One `runtime`-based model.

### 2c. Usage quota (rolling 5-hour windows, global + per-project)
Research recommendation, chosen for a file-backed cron system:

- **Accounting = sliding-window log.** After every worker run, append
  `{ts, project, tokens}` to `.runtime/usage.jsonl`. Window usage = sum of entries
  newer than `now - 5h`. Exact and directly reportable (each project's share is one
  sum). Capture tokens by switching `ralph.sh`/event `claude -p` calls to
  `--output-format json` (which carries usage) and writing on `jobs.finish`.
- **Allocation = weighted max-min progressive fill with HTB floor/ceil.** Config
  file with app-wide 5h cap `B_app`, and per-project `weight`, `floor` (guaranteed
  min), `ceil` (borrow limit). Each tick: satisfy floors, then distribute leftover
  in proportion to weight up to ceil. **Proportional scale-down falls out for
  free**: when demand exceeds the pool everyone converges below nominal, weighted,
  and we emit a notice. Single resource (tokens) so this is NOT DRF; do not build
  DRF.
- **Hook point:** the Python admission engine in `dispatch_triggers.sh:104-193`
  (replace the flat `MAX_*_SWEEPS=1` caps). Secondary intra-sweep check at
  `project_ralph.sh:211`. Rewrite the dead `services/budget.py` onto this ledger.
- **Chats are exempt.** Chat turns are not Jobs, so they are naturally unmetered;
  we simply never write chat turns to the usage ledger. Per-project share +
  app-wide totals surface in Settings (global) and ProjectDetail (per-project).

### 2d. Self-healing (never permanently blocked; recovers from disconnect/wait)
- **Lease + fencing token + reclaim sweep.** Each task claim sets `claim_token`
  (monotonic, bumped every claim), `owner`, `lease_until`. A worker finalizes only
  if its `claim_token` still matches (fences zombies). A reclaim sweep each tick
  returns `running` tasks whose lease expired AND whose pid is dead to `ready`
  (bump attempts), or `parked` at the attempt cap.
- **`waiting` status releases the claim.** A task blocked on operator approval is
  NOT `running`: it clears owner/lease, so it never freezes a dispatch slot, never
  counts as live, never gets reclaimed as a crash. Operator action flips it back to
  `ready`. This is the single most important detail for "the whole system doesn't
  freeze." (We partly have this via `awaiting_input` skip-unchanged; formalize it.)
- **Parked = dead-letter as a status**, not a subsystem (respects no-cruft). Poison
  pills land in `parked` with the last error; nothing loops forever, nothing is
  lost.
- **Circuit breaker at the dispatcher, separating env-failure from task-failure.**
  On a run of environmental failures (auth expired, quota wall, API 5xx), open the
  circuit: stop launching for an exponential cooldown and do NOT burn task
  `attempts` (the task did not fail, the environment did). Half-open probe closes
  it. This implements the operator's "handle API outages actively" rule.
- **Liveness watchdog cron** (mirrors `frontends_watchdog.sh`): probe backend
  `/health`, flag "0 dispatched despite ready work for N ticks," detect API-outage
  patterns, and recover/alert. Closes the "monitors itself and keeps itself
  running" requirement.

### 2e. Cross-chat shared context + dedup
- **Ambient digest.** Inject a scoped "ongoing work across the workspace" digest
  into chat context (open tasks/jobs/plans), gated by sensitivity + grants. Builder
  chats default to their own project scope; reach shared/other scopes when access
  allows. This is the grants model applied to task/knowledge context, so chats
  stop clashing and losing track.
- **Dedup = canonical hash + local-embedding cosine** (research: MinHash/SimHash
  degrade on short text; corpus is tiny so no LSH needed). Stage 1: canonicalize
  (lowercase, sort tokens, drop stopwords) + exact hash for trivial dupes. Stage 2:
  embed with the existing local birds `/embed` model, brute-force cosine vs open
  tasks in scope. Auto-decide (no prompts): cosine >= 0.90 merge (attach the new
  chat's provenance to the existing task, do not drop the signal), 0.80-0.90 create
  + link "related", < 0.80 distinct. Apply this on the chat `task-patch` path, which
  today passes no provenance at all.
- **Safe scoped read-only workshop-query tool** for the general chat: a scoped
  MCP over the knowledge/task digest, NOT a raw filesystem Read grant (which would
  expose `.runtime` secrets via egress).

### 2f. Learning from usage over time (global + per-agent + skills)
The operator wants the system to learn from his feedback, from what works vs fails,
and from its own experiments, at three levels. We already have seams: SKILL.md
skills (builder auto-discovery + assistant `use_skill`), the Letta per-scope memory
files, and `shared/learnings.md`. Make them a coherent subsystem:

- **Global learning** — a workspace-wide learnings store (evolve `shared/learnings.md`
  into structured entries) that captures cross-project lessons: recurring failure
  patterns, operator corrections that apply everywhere (the CLAUDE.md hard rules are
  the seed), verified approaches. Injected into every agent's context as guardrails.
  Written to when a reviewer/acceptance gate records a repeated failure, or the
  operator gives correcting feedback in a chat.
- **Per-agent learning** — each Agent accumulates its own memory + preferences
  scoped to itself (extend the per-scope Letta memory files): what this agent has
  tried in its project, what the operator told it specifically, its running notes.
  Persisted with the Agent, carried into both its chats and its sweeps.
- **Skills** — reusable, named capabilities (SKILL.md) an Agent can learn and
  accumulate. When an agent solves a novel sub-problem well, distill it into a skill
  the agent (and optionally the global pool) can reuse. Builders auto-discover
  skills; assistants load via `use_skill`. Track which skills each agent has.
- **The learning loop** — feedback (operator correction, acceptance-gate outcome,
  a successful/failed experiment) becomes a durable learning entry at the right
  scope (global / per-agent / new skill), which then shapes future context and task
  scoping. Learning is metered like other autonomous work but cheap. This is what
  makes the system improve rather than repeat mistakes across dropped chats.

---

## 3. Staged migration (dependency-ordered)

Each stage is a shippable increment. Stages 0-2 are safe/reversible and directly
kill the visible complaints. Stage 3 is the real migration (turn the pipeline on).
4-8 build the vision. Stage 9 (full Letta deletion) is last and needs a green
light. We dogfood: each stage becomes tickets in the new `self`/`platform` scope
and, once Stage 3 lands, is worked through the system itself.

- **Stage 0 — Stop the noise (safe, reversible).** Durably pause the Letta/Ollama
  heartbeat supervisor so it stops writing junk jobs (persisted flag the supervisor
  reads on start, so a restart does not un-pause it). Verified independent of
  the bridge agent. This alone cleans the Jobs feed. Keep code for now; deletion is Stage 9.
- **Stage 1 — Agents page tells the truth (safe).** Fix liveness (2b): reset state
  on finalize + compute liveness from real turn-in-flight; split the status triad;
  drop the Letta fallback roster + second creation flow.
- **Stage 2 — Jobs read like tickets (safe).** Implement 2a: name resolution,
  title/summary, error field + exit_code, trigger labels, chat link, parent/task
  graph surfacing.
- **Stage 3 — Turn the real pipeline on (the migration core).** Make the structured
  store canonical: un-protect the root `self` scope with a safe reconcile (the
  deferred "O7"); retire the legacy root `TODO.md` grep/sed loop in `cron_agent.sh`;
  set project autonomy modes so `dispatch_triggers.sh` actually dispatches; verify
  one real project sweep end-to-end producing a proper ticket-shaped Job graph.
- **Stage 4 — Concept/naming migration.** Rename builder->chat in the UI; add the
  chat "turn this into a background task" action (snapshot -> self-contained Task +
  provenance); route build-agent work through the queue (delegate, don't do
  everything in-thread).
- **Stage 5 — Usage quota + budgets (2c).** Usage ledger, rolling-5h quota service,
  weighted max-min admission in the dispatcher, config file, per-project share +
  app-wide totals in the UI.
- **Stage 6 — Self-healing hardening (2d).** Lease+fence+reclaim, formalize
  `waiting`-releases-claim, `parked` dead-letter, circuit breaker, liveness
  watchdog cron.
- **Stage 7 — Cross-chat shared context + dedup (2e).** Ambient scoped digest,
  dedup on task creation, scoped read-only workshop-query tool.
- **Stage 8 — Learning + skills (2f).** Global learnings store, per-agent memory,
  skill distillation, the feedback->learning loop.
- **Stage 9 — Retire Letta/Ollama fully (needs operator confirm).** After the
  structured store is canonical and the heartbeat has been paused a while with no
  regressions, delete the supervisor + Ollama tier + related routes/UI. Confirm
  the bridge agent independence once more (the only link is the write-only notify file).

---

## 4. Operational notes
- Cannot drive the authed UI (login wall). Verify via `tsc`, `check_design.sh`,
  backend scratch tests, curl 401-vs-404. Operator confirms anything purely visual.
- LIVE autonomous builders commit concurrently. `git status` + stage ONLY own
  files; never `git add -A`.
- Editing live autonomy scripts (`project_ralph.sh`, `dispatch_triggers.sh`,
  `cron_agent.sh`, `ralph.sh`) requires `touch .runtime/AUTONOMY_DISABLED` then rm.
- Backend restart interrupts in-flight chat turns (they reattach on boot). After a
  restart, check for orphaned streaming turns and nudge only ones we interrupted.
- Restart: `bash scripts/restart_backend.sh --deferred`.
- Backend Python: `PYTHONPATH=$WORKSHOP/backend .venv/bin/python`;
  source `~/.config/clawdbot/auth.env` for a token in scratch tests.

Key files by subsystem are listed in the four audit sections above and in
`autonomy-task-management.md`, `review-addendum.md`, `implementation-plan.md`.
