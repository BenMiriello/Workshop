# Orchestration and autonomy: chat-scoped background work, one task substrate, selective memory

Status: design, 2026-07-13. Code-verified against the working tree on `main`. This is the unified
design for the operator's vision of folding autonomy and task-management INTO agent orchestration: a
conversation (any chat that binds to a durable scope) can scope out work that then continues in the
background, delegating and fanning out when the task warrants, tracking itself through a task/notes
substrate that is simultaneously the operator's interface and the machine's work queue, while a
memory layer captures how the operator works and improves behavior over time. Depth, autonomy, and
distribution are configurable at the right granularity.

Written in the file-scoped style of `implementation-plan.md`. Field research is cited inline.

## Relationship to the other docs (read this first)

This doc **supersedes and extends `autonomy-task-management.md`**. That doc's phases T1 through T5 are
adopted wholesale and become the spine of this design's Phases O1, O2, O3, O4, and O7 respectively.
Everything in it is still correct; this doc adds four things it deliberately left out of scope:

1. The **orchestration model** on top of the task substrate: how a chat scopes and launches background
   work, when delegation and fan-out fire, and how results aggregate (its Section 2.5 dispatcher is the
   seed; this generalizes it into an orchestrator-worker model with an explicit fan-out policy).
2. The **memory / continual-learning layer** (selective, strategic remembering; learning how the
   operator works) reconciled with the existing Letta engine and per-scope markdown files. That doc
   touched `evidence` and `source`; this designs the consolidation organ.
3. The **configurability model**: where the depth / autonomy / distribution knobs live and their
   defaults, as a single preset cascade rather than five scattered switches.
4. **Where it applies** across chat kinds (builder / project / assistant / general / app) and where it
   does not.

Where the two docs describe the same mechanism (the canonical Task model, the one-write-path reconcile,
`task-patch` / `task-proposal`, the dispatcher, Job `task_id` linkage, acceptance gates,
`awaiting_input`, root convergence), this doc restates the decision compactly and points at the
detailed spec already written there rather than duplicating it. Where this doc changes that doc, it
says so explicitly under "Changes to autonomy-task-management.md" (Section 9).

It also reconciles with:

- `proposal.md` + `implementation-plan.md` (the Job / Store / Agent / Grant model, phases 1 through 5,
  all committed and live per `review-addendum.md`). This design is built entirely on those primitives
  and adds no new noun. Orchestration is expressed as Jobs bound to Tasks under Agents via Triggers; it
  reuses the Grant model for delegated authority; it introduces no Run/Session/Crew/Conductor object
  (proposal Section 4 explicitly rejected those).
- `agent-chat-redesign/plan.md` (orchestration L0 through L4, skills, knowledge scopes, per-project
  Ralph). Its L3 conductor is now the dispatcher; its L4 swarm stays deferred, and this doc says
  precisely why the field research vindicates that deferral.
- `review-addendum.md` P1 through P3 (streamed turns for every provider, `agent:<slug>` binding). Those
  are relevant because this design's conversation-emission lane rides the consolidated turn path, and
  because assistant agents needed a chat surface to be orchestration participants. Code check: the
  `agent:` provider (`AssistantAgentContextProvider`, `chat_context.py:734`, resolved at `:875`) is
  already built, so that dependency is satisfied.

Legend: ADD (new file/symbol), MOD (modify), DEL (delete + call sites), MIG (on-disk migration).

---

## 1. Assessment: what the current plans and code give toward the vision, and the gaps

Verified against the working tree, not commit messages.

### 1.1 What the substrate already gives (build on, do not rebuild)

- **The Job object and trigger vocabulary are live and correct.** `services/jobs.py` (append-only
  `jobs.jsonl`, collapse-by-id, `start()/finish()`, CLI shim, status enum including `awaiting_input`),
  `services/triggers.py` (`manual|cron|event|loop`, loop carries `max_iterations` + `budget` +
  `completion_gate`, plus a `dispatch()` that opens a Job for a firing trigger). `jobs.jsonl` holds
  ~2,500 records. This is the orchestration ledger; it needs `task_id`/`parent_id` and nothing else
  structural.
- **One Agent registry over markdown definitions is live.** `agents_registry.py` reads user-scope and
  project-scope `*.md`, merges builder runtime state, excludes the bridge agent per-channel agents, and
  regenerates `agent-subscriptions.json` on write. Definition frontmatter already carries `triggers`,
  `limits`, `capabilities`, `memory`, `project_slug`. This is where per-agent autonomy/depth knobs
  belong, and the slot exists.
- **A real per-scope task store.** `services/tasks.py`: `.tasks.json` authoritative, `TODO.md`
  regenerated on every write, atomic writes, owner grouping, parent nesting, markdown import/export.
  The right foundation, kept.
- **A proven completion loop.** `scripts/ralph.sh` (sentinel + `reviewer.sh` gate, iteration and wall
  caps, classifier-block detection) driven per-project by `scripts/project_ralph.sh` through
  `scripts/project_tasks.py` so `.tasks.json`/`TODO.md` never drift. This is the worker; it stays.
- **Knowledge scopes with a recall engine.** `knowledge.py` (per-scope `knowledge/` dirs, `MEMORY.md`
  index, Letta archival search behind `recall()`, plaintext degrade path, `register_knowledge_stores()`
  wrapping them as `Note` Stores). Skills (`skills.py`, SKILL.md standard, builder auto-link + assistant
  loader). The learnings ledger (`shared/learnings.md` via `log_learning.sh`). These are the raw
  materials of the memory layer; they are not yet organized into episodic/semantic/procedural roles and
  nothing consolidates them.
- **Streaming turns and agent binding.** Per `review-addendum.md` P1 through P3 (the highest-priority
  fixes): every provider streams, chat turns can run tools, and `agent:<slug>` binds a registry
  assistant to a chat. The conversation-emission lane this design needs rides that consolidated path.

### 1.2 The gaps against the vision, concretely

The gaps that `autonomy-task-management.md` already named (chat-to-tasks is self-destroying persona
text; three divergent root task representations; nothing dispatches project loops in the background;
agent-scoped work has no consumer; state does not flow back live; no discussion-to-act gesture; the
Task model is too thin; `awaiting_input` has zero emitters) are all real and this design inherits their
fixes. Beyond those, four gaps block the *orchestration + learning* half of the vision specifically:

1. **There is no orchestration model, only a single loop.** `project_ralph.sh` claims one task at a
   time, single-threaded, one project at a time. There is no notion of a coordinator scoping a piece of
   work into sub-tasks, no fan-out for parallelizable read work, no result aggregation, no cross-task or
   cross-project concurrency policy. `agent-chat-redesign/plan.md` named this "L4 swarm, deferred." The
   vision asks for delegation and networked/distributed continuation "if the task needs it," so the
   design must answer *when it needs it* and *what fires* rather than leaving a blank.
2. **Tasks are a store and a UI drawer, not the conducting substrate.** The task store is read by the
   loop and written by the panel, but it is not the thing that *decides what runs*. There is no
   ready-work predicate (dependency-aware), no atomic claim protecting against double-pickup by parallel
   workers, no first-class "discovered-from" provenance so agent-found work auto-files instead of
   vanishing, no durable "awaiting input" state carrying *what* is needed. The vision wants tasks/notes
   as "BOTH an interface AND a functional way to actually orchestrate and conduct work, make a work
   queue" - that requires these primitives, which the beads / Linear / GitHub-agents field all converge
   on (Section 3).
3. **Nothing learns.** `shared/learnings.md` accumulates failure patterns append-only (reviewer
   rejections write to it via `log_learning.sh`); the knowledge `chat-distilled` note gets facts folded
   in on compaction; Letta holds core-memory blocks. But nothing consolidates any of it, nothing prunes
   contradictions, nothing extracts "how the operator works and builds" from corrections, and nothing
   promotes a repeated procedure into a skill. There is no organ that makes the system "get better over
   time"; the raw episodic/semantic/procedural traces exist and rot.
4. **The depth/autonomy knobs are scattered and there is no default policy.** `AutonomyPanel` edits a
   project trigger list; agent definitions carry `limits`; `ralph.sh` has hardcoded caps; there is no
   effort/depth tier, no per-task override surfaced, and no single "how autonomous should this be"
   control. The vision explicitly asks for this to be configured "per project or per builder... you
   decide," which means a designed cascade with defaults, not five switches.

### 1.3 Direct answers to the vision's implied questions

- Can a conversation spin up scoped background work that continues autonomously? **Not today** (no
  dispatcher, no emission-to-loop bridge); designed here (Section 4 + 5).
- Does it delegate / fan out when the task warrants? **No** (single-threaded loop); designed here with
  an explicit fire-condition policy grounded in the cost evidence (Section 4.3).
- Is the task/notes substrate simultaneously interface and work queue? **Half** (store + panel, but no
  ready-predicate / claim / provenance / awaiting-input payload); completed here (Section 5).
- Does it learn how the operator works and improve? **No** (traces rot, nothing consolidates);
  designed here (Section 6).
- Are depth/autonomy/distribution configurable at the right granularity with sane defaults? **No**
  (scattered, no defaults); designed here as a preset cascade (Section 7).
- Where does it apply and not apply across chat kinds? **Undecided**; decided here (Section 8).

---

## 2. Core decisions (the six the operator asked for, stated once)

1. **Orchestration is orchestrator-worker, expressed as Jobs bound to Tasks, and nothing more exotic.**
   A chat scopes work by writing Tasks to a scope and arming (or one-click starting) a loop; a
   dispatcher fires a **sweep** (a coordinator Job) that claims ready tasks and drives each as a **child
   Job**. Delegation and fan-out happen in two disciplined forms only: (a) *read-parallel fan-out inside
   a single builder task* using Claude Code native subagents (free, context-isolated), and (b)
   *write-serial cross-task concurrency* across tasks with disjoint file ownership, bounded by
   concurrency caps. No peer-to-peer agent negotiation, no A2A inside one instance, no blackboard
   daemon. This is the field's settled answer (Section 3) and it is exactly `proposal.md`'s "contracts
   in the middle, agents at the edges" rule made operational.

2. **One task substrate is both the interface and the work queue.** `.tasks.json` per scope is the
   single source of truth; `TODO.md` is a regenerated projection plus a healed hand-edit inbox. The
   store gains the queue primitives (dependency-aware ready predicate, atomic claim, discovered-from
   provenance, an `awaiting_input` state carrying its payload). Every writer - operator panel click,
   chat emission, worker follow-up, dispatcher claim - goes through one locked write path. The operator
   sees and steers the same objects the machine conducts from. (This is `autonomy-task-management.md`'s
   Section 2, adopted, plus the beads/Linear primitives it lacked.)

3. **Memory is the CoALA trio mapped onto existing files, consolidated by a sleep-time organ, never on
   the hot path.** Episodic = Jobs + per-task evidence + a worklog; semantic = per-scope `knowledge/`
   (Letta-recall, files as source); procedural = `learnings.md` + skills + the agent/project standing
   instruction files. A **consolidation Job** (a registry agent on a slow trigger) does itemized delta
   updates (add / update / retire specific bullets) into the scope memos, learns operator preferences
   from corrections by rewriting standing instructions, and promotes repeated procedures into skills.
   Selective and strategic by construction: it decides what to write and what to retire; it never
   rewrites monolithically (context-collapse guard); it runs between sessions so it never invalidates a
   live prompt cache. Letta stays the recall engine; the bridge agent's Letta path is untouched.

4. **Depth / autonomy / distribution are one preset cascade with three levels of granularity.** A single
   `autonomy` preset (`off | on-demand | scheduled | continuous`) plus a `depth` effort tier
   (`quick | standard | deep`) live on the **scope** (project registry entry or agent definition
   frontmatter). A Task may override `max_iterations`, `acceptance`, and effort. A manual run may
   override anything for that one invocation. Defaults are conservative: assistant/general chats do not
   self-run in the background; project/builder scopes default to on-demand (armed by a one-click gesture,
   not standing). Presets expand to trigger + budget + fan-out bundles so the operator sets one knob.

5. **It applies to any chat bound to a durable scope with a working home; it does not turn the agentless
   general chat into a background loop.** Task *emission* is universal (any chat can propose tasks into a
   chosen scope). Background *execution* requires a scope that has a place for the work to live and be
   verified: a project tree, a builder agent's cwd, or a data-tier agent scope for non-code loops. The
   general chat (agentless, scope = workshop) and app docks (data-tier, scope = the app) can emit tasks
   and start a scope's loop, but are not themselves loop bodies. The workshop root is just the `self`
   project scope, so "operate on Workshop itself" is not a special case.

6. **Correctness bounds are allowed; multi-tenant cruft is not.** Iteration caps, wall/cost budgets, the
   concurrency bound on parallel sweeps, action-dedup and no-progress derailment tripwires, and the
   fresh-context completion gate are correctness and cost bounds on unattended loops - explicitly
   permitted by the no-cruft rule and demanded by the failure research (Section 3). No approval queues,
   no audit journals, no per-action confirmation, no allowlists.

---

## 3. Field research grounding (cited)

The three decisions that carry the most design risk (topology, substrate shape, memory shape) are each
backed by convergent primary sources.

### 3.1 Orchestration: read-parallel, write-serial, and bound everything

- **Orchestrator-worker is the production pattern, and it is expensive.** Anthropic, "How we built our
  multi-agent research system" (June 2025): a lead agent plans and spawns subagents each with its own
  context window and an explicit objective / output-format / boundary contract; it beat single-agent by
  90% on breadth-first research but **used ~15x the tokens of a chat** and struggled precisely where
  agents must share context - "coding" is called out by name. Anthropic, "Building Effective Agents"
  (Dec 2024): start simple, add agentic complexity "only when it demonstrably improves outcomes."
- **Writes must stay single-threaded.** Cognition, "Don't Build Multi-Agents" (June 2025): actions carry
  implicit decisions; parallel workers make conflicting unprescribed assumptions (their Flappy Bird
  example produced stylistically incompatible pieces). Rule: share full traces, keep mutation
  single-threaded. This is the write-serial half of decision 1.
- **Under equal compute, a single agent usually wins reasoning.** Tran & Kiela (Stanford, arXiv
  2604.02460, Apr 2026): with reasoning-token budgets held equal, a single agent matched or beat
  multi-agent on multi-hop reasoning; every inter-agent message is lossy compression (Data Processing
  Inequality). Multi-agent only wins when single-agent context utilization degrades or you genuinely
  spend more compute. "The Illusion of Multi-Agent Advantage" (arXiv 2606.13003, June 2026):
  auto-generated MAS underperformed CoT + self-consistency at up to 10x cost.
- **Most multi-agent failures are organizational, not capability.** MAST (Cemri et al., arXiv
  2503.13657): 14 failure modes in 3 buckets - specification, inter-agent misalignment, and
  verification/termination. Invest in task specs and independent verification, not more agents.
- **Bound unattended loops with layered stop conditions.** OpenAI Agents SDK `max_turns`; LangGraph
  `recursion_limit`; AutoGen `max_consecutive_auto_reply`; Claude Code `--max-turns` (with a known
  non-enforcement bug, issue #41143 - so enforce budget externally too). Devin uses per-session ACU
  budgets and sleep/wake escalation. Documented derailment: `npm install` run 100+ times; standard
  detectors are action-dedup (hash tool+args, break after k identical), no-progress detection, and
  wall/cost caps, layered. HumanLayer's "Brief History of Ralph": run the loop once on a cron overnight
  and merge small; "one small refactor every morning beats 50."

Design consequence: fan-out is justified only for breadth-first *read* work and is free when done as
Claude Code native subagents inside one task; cross-task parallelism is only safe across disjoint file
ownership and must be concurrency-bounded; the fresh-context reviewer gate and layered caps are
mandatory. The `agent-chat-redesign` L4-swarm deferral is vindicated - a personal single-operator
platform almost never clears the 15x-cost bar for cross-agent write parallelism.

### 3.2 Substrate: truth is a ledger, the notes file is a projection with a writeback parser

- **Beads (Steve Yegge, Oct 2025)** is the closest prior art: a git-backed, graph-structured issue
  tracker built *for coding agents*, where the killer primitive is `bd ready --json` (dependency-aware
  unblocked-work detection) plus `bd update --claim` (atomic assignee + in-progress so parallel agents
  do not collide). Its dependency edges include `discovered-from`, so agent-found work auto-files.
  Decisive datum: beads **migrated from git-JSONL-as-source to a versioned SQL store with JSONL as a
  regenerated export** because text-as-truth hit merge/scale limits (grep fails beyond ~500 issues /
  25k tokens). Yegge's own critique: markdown plans are "write-only memory"; agents create duplicate
  plan files and disown discovered problems.
- **Delegate, do not assign.** Linear for Agents (May 2025): issues are assigned to humans and
  *delegated* to agents; the human stays accountable. Agent sessions have an explicit *waiting for
  input* state and typed *elicitations* (structured asks to the human). GitHub Copilot coding agent:
  assignment is dispatch, work streams into a draft PR, and the agent **cannot merge its own PR** (the
  platform-enforced version of "the worker never closes its own gate").
- **Every mature system separates truth from view.** The human-editable surface (PR checklist, Linear
  feed, beads JSONL export, Jules/Codex plan) is a *projection* of the structured store, and human edits
  flow back through a structured channel, not by hand-editing the projection. The one small-scale
  counter-model that makes markdown-as-interface work is Obsidian AgentBoard: two-way sync between a
  board and a markdown file via a writeback parser. That is exactly the `TODO.md` reconcile in decision
  2 - the file is a safe-to-touch projection, not the store.
- **Ralph and completion gates.** Geoff Huntley's ralph is `while :; do cat PROMPT.md | AGENT; done`:
  fresh context per iteration, one task at a time, progress in files+git, completion enforced by
  backpressure (tests/build/lint), never self-report. The existing `ralph.sh` + `reviewer.sh` is a
  hardened instance of exactly this.

Design consequence: the store gets `next_ready` (dependency-aware), atomic claim, `discovered-from`
provenance, and a durable `awaiting_input` state with payload; `TODO.md` stays a projection healed by a
writeback reconcile; the operator's edits go through structured channels; the worker never self-gates.

### 3.3 Memory: CoALA trio, sleep-time consolidation, itemized deltas, hard-capped index

- **CoALA (arXiv 2309.02427)** is the taxonomy: working memory + long-term episodic (experiences),
  semantic (facts about world/user), procedural (code and the agent's own prompts/routines). Learning
  = deliberate internal write actions to these stores.
- **Consolidate off the hot path.** Letta sleep-time agents (2025) and Claude Code auto-memory
  "auto-dream" both converged on the same shape: the primary agent stays fast and cache-stable while a
  background pass rewrites memory between sessions. This also protects the prompt cache - any mid-session
  edit to an always-on block invalidates the entire cached prefix (Anthropic prompt-caching mechanics:
  exact-prefix, byte-change-before-a-breakpoint invalidates everything after).
- **Itemized deltas, not monolithic rewrites.** ACE (Stanford/SambaNova, arXiv 2510.04618) names the two
  failure modes of naive self-editing memory: *brevity bias* (summaries drop domain insight) and
  *context collapse* (iterative full rewrites erode detail). Fix: incremental delta updates with
  itemized bullets. mem0 (arXiv 2504.19413) formalizes the write path as a router that picks
  ADD / UPDATE / DELETE / NOOP against the top-k similar existing memories - deletion happens on
  contradiction, not by TTL. Zep/Graphiti (arXiv 2501.13956) keeps superseded facts with validity
  intervals ("used to prefer X, now Y") rather than destroying history.
- **Two-layer index with a hard size cap.** Claude Code loads only the first 200 lines of MEMORY.md;
  detail lives in linked topic files (progressive disclosure). Enforce the cap mechanically so the
  always-loaded layer cannot bloat. This is exactly the existing `MEMORY.md`-index-plus-notes shape in
  `knowledge.py`, uncapped today.
- **Preferences are procedural.** LangMem and ChatGPT memory both learn user style by *rewriting standing
  instructions* (confidence-scored preference sections), not by accumulating raw episodes. The strongest
  learned procedure is an executable artifact (Voyager skill library, Anthropic Skills) - promote
  repeated prose into a SKILL.md/script, which self-verifies where a memo cannot.

Design consequence: name the three stores over existing files; add a consolidation Job that does
mem0-style itemized deltas into capped scope memos, rewrites standing-instruction files from
corrections, and promotes repeated procedures into skills; run it on a slow trigger, never in a turn;
leave Letta as the recall index and never touch the bridge agent's path.

---

## 4. The orchestration model

### 4.1 The shape: coordinator sweeps, child-Job workers, one substrate

There is no new object. Orchestration is the existing nouns arranged:

```
  Chat (bound to a scope) --emits--> Tasks (.tasks.json in the scope)
                                       |
   arm/one-click "start" ----+        | next_ready (dep-aware) + atomic claim
                             v        v
                        Trigger (loop) --dispatch--> SWEEP  = coordinator Job
                                                       |  (parent_id root)
                                    per claimed task   v
                                                  child Job  --drives--> ralph.sh
                                                       |                 (fresh context,
                                          fan-out?     |                  reviewer gate)
                                          (read only)  v
                                             native subagents (free, context-isolated)
```

- A **sweep** is a coordinator Job (`trigger=loop`, `parent_id` unset). It does not itself reason; it
  claims ready tasks and spawns child Jobs, honoring the concurrency bound. This is the orchestrator.
- A **child Job** is one bounded task attempt (`trigger=loop`, `task_id` set, `parent_id` = sweep id).
  It runs `ralph.sh` in the scope's tree: fresh context each iteration, sentinel + `reviewer.sh` gate,
  caps. This is the worker. The task's `job_ids` accumulate its attempts.
- **Fan-out inside a worker** is Claude Code native subagents (the Task tool), which the worker already
  has for free: context-isolated, read-parallel exploration whose compact result returns to the
  worker's window. This is where "networked / distributed" work actually happens, and it is the cheap,
  safe form the research endorses. The design does not build a second fan-out mechanism.

This maps `agent-chat-redesign/plan.md`'s ladder exactly: L0 plain chat, L1 agent chat, L2 fan-out
inside a builder (native subagents), L3 conductor (the sweep/dispatcher), L4 swarm (still deferred, and
now justified in Section 3.1).

### 4.2 How a chat scopes and launches background work

Three lanes, all writing the one store, all already sketched in `autonomy-task-management.md` Section
2.4 (adopted verbatim; restated for the orchestration frame):

1. **Builder / project chats (Claude Code runtime):** the task CLI (`project_tasks.py` extended with
   `add`, `note`, `block`, `next-ready`, `list-json`, all assignee-aware). The model manages its list
   mid-conversation as deterministic Bash calls. Persona text drops "update TODO.md" and gains the CLI
   instruction.
2. **Assistant / data / app chats (data tier, no fs):** a `task-patch` fenced block applied server-side
   in the turn finalize seam (`build_streaming.py:513` provider-actions step and the `send_message`
   equivalent), scoped to the thread's binding, stripped from the stored reply, surfaced via
   `meta.notes` (which already drives the panel refetch at `Chat.tsx:669`). Same seam as the budget
   `plan-patch`.
3. **The explicit "queue it and go" gesture:** a `task-proposal` ui-card. When a discussion converges on
   a plan, the model emits one editable card listing proposed tasks (text / priority / dependency
   fields), with actions `addTasks` and `addTasksAndRun`. `addTasksAndRun` posts the tasks then calls
   `POST /projects/{slug}/autonomy/run` (or the agent-scope equivalent), which opens the first sweep.
   This is the one-click bridge from "we talked about it" to "the loop is working it," and it is the
   *scoping* act the vision describes.

Crucially, for a **general chat** (agentless, no durable home), the `task-proposal` card targets a
*chosen* scope (a project or agent picker on the card), because the general chat cannot host the work
itself (decision 5). Emission is universal; the card just makes the operator pick where the work lands.

### 4.3 When delegation and fan-out fire (the policy, not a guess)

The vision says work continues "in a networked / distributed fashion **if the task needs it**." The
research says exactly when it needs it. Encoded as a policy the worker persona and the dispatcher obey:

- **Default is a single worker with native subagents.** A task is driven by one child Job. Within it,
  the worker is instructed (persona + skill) to spawn native subagents for *read-parallel* subproblems
  it cannot spare context for or that are embarrassingly parallel (search across many files, verify N
  independent claims, gather from N sources). This is L2, free, and is the overwhelmingly common case.
- **Cross-task parallelism fires only when tasks have disjoint file ownership.** The dispatcher may run
  two child Jobs concurrently *only* when their tasks declare non-overlapping `touches` paths (a new
  optional Task field, Section 5) or bind to different scopes. Writes stay serial within any shared
  tree. Concurrency is bounded by a per-tick and per-scope cap (default: one sweep per project per tick,
  one child Job at a time within a sweep; raised only when disjointness is provable). This is the
  write-serial rule made mechanical.
- **Cross-scope distribution is just many sweeps.** "Networked / distributed" across projects is N
  independent sweeps (one per project scope), each single-threaded in its own tree, coordinated only by
  the shared dispatcher's concurrency budget. There is no cross-scope message bus and no peer discovery
  (proposal Section 4: "never as peers discovering each other").
- **Sub-task decomposition is a task-graph act, not an agent-spawn act.** When a coordinator (or the
  chat that scoped the work) decides a piece is too big, it *files sub-tasks with dependencies* into the
  store (beads model), and the ready predicate schedules them. Decomposition lives in the durable
  substrate, visible to the operator, not in an ephemeral in-context plan. This is how the substrate is
  "a functional way to actually orchestrate."

### 4.4 Result aggregation

- **Within a worker:** native subagent results return verbatim as tool results into the worker's
  window; the worker synthesizes and either completes the task (reviewer-gated) or files follow-up
  tasks. No platform machinery needed.
- **Across child Jobs in a sweep:** aggregation is the *task store state*, not an LLM synthesis step.
  The sweep's outcome is the set of task transitions it produced (done / in_review / blocked /
  released), recorded as `tasks_done` / `tasks_failed` on the coordinator Job and as `job_ids` +
  `evidence` on each task. The operator's view of "what the background work accomplished" is the task
  board diff plus the Job graph, which is inspectable and durable. This deliberately avoids the
  async-coordination complexity Anthropic flagged (state consistency, error propagation, result
  merging) by making the store the merge point.
- **Discovered work re-enters the queue.** A worker's follow-up task is filed with
  `source.kind=agent`, `source.ref=<job_id>`, and a `discovered_from` link to the task it arose from
  (beads `discovered-from`). It becomes ready per the predicate and is scheduled on a later sweep. This
  closes the "agents disown discovered problems" failure Yegge documented.

### 4.5 The dispatcher (the one new organ, small)

ADD `scripts/dispatch_triggers.sh`, called by `cron_agent.sh` after pre-flight, bounded by the tick
budget and `AUTONOMY_DISABLED`. This is `autonomy-task-management.md` Section 2.5's dispatcher, adopted,
with the orchestration policy of 4.3 added:

- **Project loop triggers:** for each project whose triggers include `loop`, if `next_ready` is
  non-empty and no sweep Job is `running` for it (stale-guarded), spawn `project_ralph.sh <slug>`.
  Concurrency cap: at most one background project sweep started per tick (cost bound; raise later behind
  disjointness proof).
- **Agent cron/loop triggers:** for each registry agent, fire due `cron` triggers against
  `.runtime/trigger-state.json`; `loop` triggers fire when the agent scope has ready tasks. Builder
  runtime runs the sweep machinery over its scope; assistant runtime opens a Job and runs one bounded
  data-tier turn (the non-code loop path - used by the memory-consolidation organ, Section 6). Letta
  `continuous` agents stay on the supervisor unchanged.
- **Skip rules (derailment and cost guards):** skip a project whose last sweep Job is `awaiting_input`
  and whose store is unchanged since (mtime/`export_sha` compare) - do not re-open an identical sweep
  every tick. Respect per-task attempt caps so a permanently-failing task is not retried forever.

No new daemon. The 20-minute cron tick is the clock; the dispatcher is one pass inside it.

---

## 5. The task/notes substrate: one source of truth, interface and work queue

This is `autonomy-task-management.md` Section 2 adopted in full, plus the beads/Linear queue primitives
its Section 1.2 gap analysis implied but its model did not fully carry. The canonical Task (extending
`services/tasks.py` in place, version bump to 2, lossless forward-normalization) is that doc's model
with three additions marked NEW:

```jsonc
Task {
  "id": "uuid4hex",
  "text": str,
  "status": "todo" | "doing" | "in_review" | "blocked" | "done" | "cancelled",
  "assignee": null | "agent:<slug>" | "operator",   // delegate; human stays owner (Linear rule)
  "parent": str | null,
  "priority": int,
  "depends_on": [task_id],          // gates readiness
  "touches": [path] | null,         // NEW: file/subtree ownership, for disjoint-parallel scheduling
  "discovered_from": task_id | null,// NEW: beads provenance for agent-filed follow-ups
  "acceptance": { "kind": "reviewer"|"command"|"operator", "spec": str|null },
  "max_iterations": int | null,
  "effort": null | "quick" | "standard" | "deep",   // NEW: per-task depth override (Section 7)
  "blocked_reason": str | null,
  "awaiting": null | { "question": str, "since": iso },  // the awaiting_input payload (elicitation)
  "job_ids": [str],
  "evidence": [ { "kind": str, "ref": str, "ts": iso } ],
  "source": { "kind": "operator"|"chat"|"agent"|"autogen", "ref": str|null },
  "provenance": str | null,
  "created": iso, "updated": iso
}
```

The mechanics are that doc's, unchanged and not re-derived here:

- **Statuses, boxes, and the readiness predicate** (`next_ready(root, assignee=None)` =
  `status==todo AND every depends_on is done|cancelled`, ordered by priority desc then created asc).
  This is the `bd ready` primitive.
- **Atomic claim** = the `todo -> doing` transition under the module's `fcntl` flock, recording the
  child job id into `job_ids`. This is `bd update --claim`; it is what makes cross-task concurrency safe.
- **One write path + reconcile.** `.tasks.json` authoritative; `TODO.md` regenerated; `export_sha`
  tracks the last render; `reconcile(root)` heals hand-edits (flip a box, add a bare line) back into the
  store on read, importing unmatched lines as new tasks, never deleting. This is the AgentBoard
  writeback-parser model and it is what lets the notes file be a genuine interface without being the
  store.
- **`awaiting` as a first-class elicitation.** The `awaiting` payload (NEW field carrying the
  question and since-timestamp) is what `awaiting_input` sweeps attach, so "the loop is blocked on you"
  says *what* it needs (Linear elicitation / Devin Required-from-User). The operator answers by editing
  the task (panel, chat, or a reply affordance); the next tick proceeds. No approval queue.
- **Completion authority stays split:** worker never self-marks done; `reviewer` gate is default;
  `command` acceptance runs a verifier; `operator` acceptance routes to `in_review`. GitHub Copilot's
  "cannot merge its own PR" is this same rule.

The substrate is the interface and the queue because the same objects are what the panel renders, what
the chat emits, what the operator hand-edits (via the healed projection), and what the dispatcher reads
to decide what runs. There is no second representation.

---

## 6. The memory / continual-learning layer

The organizing move is to *name* the three CoALA stores over files that already exist, then add one
consolidation organ. No new storage engine; Letta stays the recall index.

### 6.1 The three stores, mapped to existing files

| CoALA store | What it holds | Where it lives today | Read path |
|---|---|---|---|
| **Episodic** | what happened: sweeps, task attempts, evidence, reviewer verdicts | `jobs.jsonl` + Task `evidence` + a per-scope `.runtime/worklog.md` | Job graph, task history (not injected raw into prompts) |
| **Semantic** | facts about the operator, projects, apps, the world | per-scope `knowledge/` dirs + `MEMORY.md` index (Letta archival mirror) | ambient index in the stable prefix + `recall()` on demand |
| **Procedural** | how to work: learned build/test procedures, failure patterns, operator preferences, skills | `shared/learnings.md`, per-scope standing files (PROJECT.md/AGENTS.md, agent definition body), `skills/` | ambient in the prefix; skills auto-linked (builders) or loaded (assistants) |

This is not new infrastructure; it is a vocabulary that tells the consolidator *where each kind of
write goes*, which is the thing the current stack lacks (Section 1.2 gap 3).

### 6.2 The consolidation organ (selective, strategic, off the hot path)

ADD a registry assistant agent `memory-keeper` (user scope, `runtime: assistant`, a `loop` or slow
`cron` trigger, `memory: letta`), whose sweep is a **non-code data-tier Job** (the assistant-runtime
dispatch path from Section 4.5). It runs the mem0/ACE write path over recently-closed Jobs and folded
chat distillations for each active scope:

1. **Gather** the scope's new episodic material since its last consolidation watermark: closed child
   Jobs (outcomes, evidence, reviewer reasons), the `chat-distilled` facts Phase-3 compaction already
   produces, and new `learnings.md` entries.
2. **Route each candidate** with an ADD / UPDATE / RETIRE / NOOP decision against the top-k similar
   existing memos (Letta `recall` supplies the similar set). Semantic facts update the scope
   `knowledge/` note (Zep-style: a superseded preference is annotated "was X, now Y," not silently
   overwritten). Procedural insights update `learnings.md` or the standing instruction file.
   Corrections from the operator (Section 6.3) rewrite the preference section of the standing file.
3. **Itemized deltas only.** Every write is a bulleted add/update/retire against the specific line, never
   a monolithic rewrite of the file - the ACE context-collapse guard. The markdown file is the audit
   trail; the diff is inspectable.
4. **Enforce the index cap.** After writing, if a scope `MEMORY.md` index exceeds the hard line cap
   (adopt Claude Code's 200-line rule), the keeper demotes the least-recently-recalled entries into a
   linked topic note and trims the index. This keeps the always-loaded layer cache-stable and bounded.
5. **Promote procedures to skills.** When the same procedural pattern recurs N times in `learnings.md`
   (a repeated build/test recipe, a repeated fix), the keeper drafts a `SKILL.md` in the scope `skills/`
   dir (Voyager/Anthropic-Skills lineage: the executable artifact self-verifies where a memo cannot) and
   files a `task-proposal`-style note for the operator to confirm the skill. Skill *creation* is
   operator-confirmed because a skill changes future behavior broadly; memo updates are not (they are
   reversible, inspectable, and low-blast-radius).

Because the keeper runs between sessions and writes files (never live core-memory blocks mid-turn), it
never invalidates a running prompt cache, and it never races the primary loop for a scope (it holds the
same task-store flock for its writes, and it reads Jobs which are append-only).

### 6.3 Learning how the operator works and builds

The operator's `feedback_*` memory memos and the repeated-correction pattern in the global CLAUDE.md are
exactly procedural memory. The keeper learns preferences the LangMem/ChatGPT way - from corrections,
rewrite the standing instruction, do not accumulate episodes:

- A reviewer rejection or an operator correction in a chat (detected as a turn that negates or redirects
  the prior assistant action) becomes a candidate procedural delta. Repeated corrections raise a
  preference's confidence and promote it from a scope `learnings.md` bullet to the scope standing file
  (PROJECT.md / AGENTS.md / agent body) or, if broad, to `shared/global-memory.md` (the operator-context
  block already synced to Letta on every agent via `global_memory.py`).
- "How I build" accumulates as procedural entries scoped to a project (its AGENTS.md build/test recipe)
  and as skills; "how I work" (style, tone, no-em-dash, no-emoji, brevity) accumulates in the global
  operator-context. The system gets better because the next fresh-context worker reads a richer,
  deduplicated standing file - the compounding-late-value curve the Voyager skill-library result
  predicts.

### 6.4 Reconciliation with Letta and the deferral

- Letta stays the **recall engine** (semantic search over archival namespaces) and the operator-context
  block host, exactly as `agent-chat-redesign/plan.md` decision 2 and `proposal.md` locked decision 3
  require. The keeper *writes files*; the existing file->Letta sync (Phase 2 of the agent-chat redesign)
  carries them into archival for recall. Nothing here reads or writes Letta directly on the hot path.
- **The bridge agent's Letta path is frozen.** The keeper is a user-scope registry agent; per-channel Discord
  agents are excluded from the registry (`agents_registry.list_agents` exclusion). The keeper never
  touches `per_channel_agents.py`, `supervisor.py` Letta calls, `global_memory.py` internals beyond the
  existing `sync_value()` endpoint, or `knowledge.py` search internals. It is additive.
- When the deferred full-Letta-removal phase lands (`proposal.md` Section 9), the keeper's write path is
  unchanged (it writes files); only the recall backend swaps from Letta archival to the promoted
  plaintext index. The keeper is designed to survive that swap because it never depends on Letta being
  the store.

---

## 7. The autonomy / configurability model

### 7.1 Three levels of granularity, one preset

Knobs live at exactly three levels with an override cascade (invocation > task > scope), matching the
field's "per-scope standing config + per-agent frontmatter + per-invocation cap" convergence (Devin
knowledge/playbooks, Claude Code subagent frontmatter, run-time `max_turns`):

- **Scope level (the primary knob), on the project registry entry or agent-definition frontmatter:**
  - `autonomy`: `off | on-demand | scheduled | continuous` (one preset, expands to triggers + budget +
    concurrency, Section 7.2). Replaces the current bare `autonomy.mode`.
  - `depth`: `quick | standard | deep` effort tier (maps to `ralph.sh` `--max-iter` and the worker
    model/effort - quick = fast tier + low iter, deep = strong tier + high iter). This is the
    reasoning-effort knob the research names.
  - `fanout`: `off | read-only` (default `read-only`: native subagents allowed for read work; `off`
    forbids even that, for a tightly-bounded scope).
  - `limits`: `max_iterations`, `budget.max_wall_s`, `budget.max_cost` (cost cap via token accounting -
    a real stop condition per Section 3.1).
- **Task level (override):** `max_iterations`, `acceptance`, `effort`, `touches` (Section 5). A single
  task can be deeper or shallower than its scope default.
- **Invocation level (override):** a manual "Run now" may pass `--force`, a one-off iteration/wall cap,
  or a depth override for that sweep only.

### 7.2 Preset expansion and defaults

The preset is the operator-facing control; it expands deterministically:

| `autonomy` | Triggers | Dispatcher behavior | Default for |
|---|---|---|---|
| `off` | manual only | never background-swept | assistant + general + app chats |
| `on-demand` | manual only | swept only by an explicit "Run now" / `addTasksAndRun` | **project + builder scopes (default)** |
| `scheduled` | cron | swept on the cron cadence when ready work exists | opt-in per scope |
| `continuous` | loop | swept every tick when ready work exists, budget-bounded | opt-in per scope |

Defaults chosen conservatively (HumanLayer "once overnight beats 50," and the operator's single-user
posture):

- **Assistant / general / app chats default to `off`.** They emit tasks; they do not self-run. A general
  chat that wants work done picks a scope and uses `addTasksAndRun`, which is an `on-demand` invocation.
- **Project and builder scopes default to `on-demand`.** Arming continuous autonomy is a one-click
  change on the scope, never the default - the operator opts a scope into standing background work
  deliberately.
- **`depth` defaults to `standard`; `fanout` to `read-only`; concurrency to one sweep per scope per
  tick.** These are the safe middle the research supports.
- The `memory-keeper` is the one user-scope agent shipped at `scheduled` (a slow cron), because
  consolidation is the thing that should run unattended and is cheap and low-risk.

### 7.3 Why this is not cruft

Presets, caps, budgets, and the concurrency bound are correctness and cost bounds on unattended loops -
`proposal.md` Section 4 and CLAUDE.md both explicitly permit these. There are no approval queues, no
per-action confirmation, no allowlists, no audit journals. The one operator-confirmation in the whole
design (skill promotion, Section 6.2) is a model-emitted proposal card, the same affordance as task
proposals, not a gate on routine work.

---

## 8. Where it applies, and where it does not

| Chat kind | Scope / home | Emit tasks? | Be a background loop body? | Rationale |
|---|---|---|---|---|
| **Builder** (`build:<slug>`) | agent cwd (or linked project) | yes (task CLI) | **yes** | has a working tree + reviewer + fresh-context worker |
| **Project** (`project:<slug>`) | project tree | yes (task CLI) | **yes** | the canonical maker scope |
| **Assistant agent** (`agent:<slug>`) | agent scope dir | yes (task-patch) | **yes, non-code loops only** | data-tier scope; runs bounded data turns (e.g. the keeper), not `ralph.sh` |
| **App dock** (`app:<name>`) | the app's data | yes (task-patch into app scope) | no | data-tier, no code tree; work belongs to the app's own provider |
| **General** (`general`, agentless) | workshop (no home) | yes, into a **chosen** scope | no | no cwd, no place for work to persist or be verified |

The rule stated once: **task emission is universal; background execution requires a scope with a
durable home and a verification path.** A code loop requires a working tree (project or builder); a
non-code loop (memory consolidation, scheduled data summarization) requires a data-tier agent scope.
The general chat is a launcher and a proposer, never a loop body. The workshop root is the `self`
project, so editing Workshop itself is an ordinary project scope, not an exception.

This is the honest reading of "this applies not just to a 'builder' narrowly but to ANY chat, and you
decide where it applies or does not": the *capability to scope and launch* work reaches every chat; the
*capability to host a loop* reaches every chat that has somewhere to put the work.

---

## 9. Knock-on implications: what changes, what is superseded, what must move in lockstep

Nothing may be left out of date. The pieces this design changes:

1. **`autonomy-task-management.md` (T1-T5):** adopted as O1-O4 + O7. It is not obsolete; it is the spine.
   Its Task model gains three fields (`touches`, `discovered_from`, `effort`) and its `awaiting_input`
   ending gains a structured `awaiting` payload. Its dispatcher gains the fan-out/concurrency policy of
   Section 4.3. Update its status line to "superseded-and-extended by orchestration-and-autonomy.md."
2. **`AutonomyPanel.tsx`:** its trigger-list editor becomes the *preset* control (Section 7): an
   `autonomy` preset select + `depth` + `fanout` + `limits`, expanding to triggers under the hood
   (edit-mode + Save, config write). The raw trigger list stays as an "advanced" disclosure. The panel
   also shows the scope's queue counts (`ready / doing / in_review / blocked`) and any `awaiting` payload.
3. **`project_ralph.sh`:** generalized to `--dir <path> --assignee agent:<slug>` (scope sweep is the
   general case; project sweep is a parameterization), opens child Jobs per task, honors `acceptance`,
   `effort`, per-task `max_iterations`, and the `awaiting` ending. This is `autonomy-task-management.md`
   T3/T4, unchanged.
4. **`cron_agent.sh`:** calls `dispatch_triggers.sh` after pre-flight; its Phase-2 root worker converges
   onto the store (O7 = T5). Its `sed`-editing of root `TODO.md` is deleted.
5. **`work_generator.sh`:** writes autogen tasks through the CLI (`source.kind=autogen`, `provenance`
   dedupe) instead of appending bare lines; its `discovered_from` links unblocked follow-ups to their
   origin.
6. **The reviewer gate (`reviewer.sh` + work-reviewer subagent):** unchanged in mechanism; it is now the
   `acceptance.kind=reviewer` path and the mandatory backpressure per Section 3. Its rejection reasons
   feed the memory keeper (Section 6.2) as procedural candidates - a new *reader* of its output, no
   change to the gate.
7. **Knowledge / skills:** unchanged in mechanism; the keeper becomes a new *writer* into scope
   `knowledge/` and `skills/`, and the `MEMORY.md` index gains a hard line cap enforced by the keeper.
   `knowledge.py` search internals are frozen (the bridge agent rule).
8. **Chat transport:** the emission lanes ride the streamed turn path and `agent:` binding from
   `review-addendum.md` P1-P3 (already built). `task-patch` is a new provider-action alongside
   `plan-patch` in the same finalize seam. No transport change; a new action kind.
9. **Jobs / Job detail (`review-addendum.md` P5):** Jobs gain `task_id`/`parent_id`; the Job graph
   (sweep -> child Jobs -> tasks) becomes the aggregation view. Job detail renders task binding,
   evidence, and the `awaiting` payload. The orphaned Jobs page (P5/P6) becomes the work-queue surface
   (Section 10).
10. **The bridge agent's Letta path:** untouched. The keeper is a registry agent excluded from per-channel; it
    writes files, not Letta. Restated as a hard constraint in every phase that adds it.
11. **`agent-chat-redesign/plan.md` L4-swarm:** stays deferred; this doc supplies the justification
    (Section 3.1) so it is a decided deferral, not an open question.

Nothing else in the platform (Store/Grant, apps, deploy seam, voice, the auth work) is touched.

---

## 10. UI / UX: how the operator sees and steers this

Conforms to `frontend/CLAUDE.md` (primitives from `src/ui`, tokens, five status kinds, lucide only,
edit-mode for config writes, all four list states, one header). No em-dash, no emoji in copy.

### 10.1 The task/notes interface (the substrate made visible)

- **`TaskPanel`** (generalize `AgentTaskPanel` from `Chat.tsx`): mounted for `build:`, `project:`, and
  `agent:` chats (it is the same store in each). List + drawer, status `StatusPill`s across the five
  meanings, `blocked_reason` shown on blocked rows, `in_review` rows get a one-click Approve (operator is
  the gate), `awaiting` rows show the question inline with a reply field. Priority and dependency edit in
  edit-mode. Polls `tasks_version` while the scope has a running sweep; refetches on turn end (existing
  hook). This is the interface half of the substrate.
- **`task-proposal` ui-card** in the chat log: editable per-item fields, a scope picker (required for
  general chats), `Add` and `Add + run` actions. The discussion-to-work gesture.

### 10.2 The work-queue view (the queue made visible)

- **Jobs page becomes the work-queue surface** (de-orphaned per `review-addendum.md` P6, extended): a
  filterable list of sweeps and child Jobs with the sweep->child->task hierarchy expandable, status
  filters including `awaiting_input`, agent/project filters. A sweep row shows `tasks_done/failed` and
  drills into its child Jobs; a child Job row links to its task and its `JobDetail`.
- **`JobDetail`** (P5): trigger, binding (project/agent/chat links), timing, exit, tasks done/failed,
  evidence, and the `awaiting` payload with a reply affordance. The single place "what ran and what
  happened" is answerable - the aggregation view of Section 4.4.

### 10.3 Background-work visibility (Home / Agents / Chat)

- **Home:** the `AttentionStrip` already surfaces `awaiting_input` Jobs (`Home.tsx:126`); extend the
  attention query to include `in_review` task counts and running sweeps, so "background work needs you"
  and "background work is happening" are one glance. Recent-jobs section links to the work-queue view.
- **Chat surfaces:** while a scope has a running sweep, the chat toolbar shows a running `StatusPill`
  ("2 tasks in progress") linking to the work-queue view, so the operator scoping work from a chat sees
  it continue without leaving the thread. This is the "the work continues in the background" feedback the
  vision needs to feel real.
- **Agents / AgentDetail:** the roster shows each agent's autonomy preset badge and last sweep; detail
  shows its Job history (the P5 links) and, for the keeper, its consolidation log.

### 10.4 Configuring depth / autonomy (the knobs made visible)

- **`AutonomyPanel` as the preset control** (Section 9 item 2): one `autonomy` preset select, `depth`
  segmented, `fanout` toggle, `limits` fields, all edit-mode + Save (config write, never
  commit-on-change per the DS rule). Queue counts and `awaiting` inline. An "Advanced" disclosure reveals
  the raw expanded trigger list for the operator who wants it.
- **Per-task overrides** in the `TaskPanel` drawer: `effort`, `acceptance`, `max_iterations` on a task's
  detail, only shown in edit-mode.

Every new list/page ships the four states; the work-queue and JobDetail need real empty/error hints
(the failing endpoint named), per rule 9.

---

## 11. Phased, file-scoped build plan

Global rules: `implementation-plan.md`'s (branch discipline; `tsc` + `check_design.sh` on frontend
steps; no committed stubs; the bridge agent smoke check after any backend restart or touch to
`agents.py`/`supervisor.py`; `AUTONOMY_DISABLED` bracket around any edit to `cron_agent.sh`,
`ralph.sh`, or `project_ralph.sh`; no em-dash/emoji). The crontab fires every 20 minutes against the
live tree. Phases O1, O2, O3, O4, O7 are `autonomy-task-management.md`'s T1-T5 with the additions this
doc names; O5 and O6 are new.

### Phase O1: canonical model + queue primitives + reconcile (= T1 + beads primitives)

**MOD**
- `backend/services/tasks.py`: the T1 changes (statuses, `assignee`/`priority`/`depends_on`/
  `acceptance`/`max_iterations`/`blocked_reason`/`job_ids`/`evidence`/`source`/`provenance`, `version:2`,
  `next_ready`, flock, `export_sha`+`reconcile`) PLUS the three NEW fields (`touches`, `discovered_from`,
  `effort`) and the `awaiting` payload object. Atomic claim helper (`claim(root, task_id, job_id)`:
  todo->doing under flock, records job id).
- `backend/services/todos.py`: parser matches bare `[ ]` lines and the extended boxes (for reconcile +
  O7 migration).
- `scripts/project_tasks.py`: verbs `next-ready` (alias `next-open`), `add` (all fields incl.
  `--touches --discovered-from --effort`), `note`, `block`, `claim`, `list-json`; `set-status --job-id`.
- Task endpoints in `routes/projects.py` + `routes/agents.py`: new fields; assignee-scoping.
- `backend/services/chat_context.py`: both build personas drop "update TODO.md," gain the CLI
  instruction and the fan-out policy (Section 4.3) as persona guidance.
- `scripts/ralph.sh` prompt text: BLOCKED/sub-task instructions point at the CLI; sub-task decomposition
  files `depends_on`/`discovered_from` tasks (Section 4.3).

**Verify:** v1 store normalizes to v2 losslessly; hand-edit TODO.md (flip a box, add a line) heals into
the store on read with nothing lost; `next_ready` respects `depends_on`; `claim` is atomic under
concurrent CLI+API writes; a build chat adds a task via CLI and the panel shows it. Backend restart +
the bridge agent smoke check.

### Phase O2: conversation emits tasks (= T2; agent binding already built)

**ADD**
- `task-patch` protocol constant in `chat_context.py`; apply+strip in the `build_streaming.py:513`
  finalize seam and `send_message`, scoped to the thread binding, outcome to `meta.notes`.
- `.runtime/agent-scopes/<slug>/` convention + task endpoints for registry assistants.
- `task-proposal` ui-card kind in `uiCardActions.ts` (`addTasks` / `addTasksAndRun`, plus the **scope
  picker** for general chats), persona guidance in the providers.

**MOD**
- `frontend/src/components/Chat.tsx`: extract `AgentTaskPanel` -> `components/TaskPanel.tsx`, mounted for
  `build:`/`project:`/`agent:`; new statuses, blocked reasons, in_review Approve, `awaiting` reply,
  per-task override fields in edit-mode; `tasks_version` polling while a sweep runs.
- `frontend/src/pages/ProjectDetail.tsx` Tasks section: new fields/states.

Depends on `review-addendum.md` P1-P3 (streamed turns + `agent:` binding), which are built.

**Verify:** a project chat receives a task-proposal card, Add+run opens a sweep that works the tasks; an
assistant chat emits a task-patch and the panel refreshes with the stored reply free of the fence; a
general chat's card requires a scope pick; `agent:bogus` 404s. tsc + check_design.sh; widths sweep.

### Phase O3: dispatcher + Job graph + fan-out policy (= T3 + orchestration)

`AUTONOMY_DISABLED` bracket.

**ADD**
- `scripts/dispatch_triggers.sh` (Section 4.5): project loops, agent cron/loop, `.runtime/
  trigger-state.json`, one-sweep-per-scope-per-tick cap, disjoint-`touches` concurrency allowance,
  skip-unchanged-awaiting_input rule.
- `triggers.py`: `due(cron_expression, last_fired, now)` helper.

**MOD**
- `cron_agent.sh`: call the dispatcher after pre-flight, within the tick budget.
- `backend/services/jobs.py`: `task_id`/`parent_id` on `start()`, CLI flags, `read(task_id=)`.
- `scripts/project_ralph.sh`: `--dir/--assignee` generalization; coordinator Job + child Job per claimed
  task; `claim` wiring; native-subagent fan-out allowed per scope `fanout`.

**Verify:** a project with two disjoint-`touches` ready tasks and a loop trigger produces one coordinator
Job + two child Jobs with correct `task_id`/`parent_id`; two tasks touching the same path run serially;
an empty queue starts no sweep; an agent cron trigger claims only its assignee's tasks. The bridge agent smoke
check (jobs.py restart).

### Phase O4: gates, awaiting_input elicitation, discovered-from (= T4 + payload)

**MOD**
- `scripts/project_ralph.sh`: honor `acceptance` (command runner + evidence; operator->in_review);
  per-task `max_iterations`/`effort`; the nothing-ready-but-outstanding ending finishes the coordinator
  Job `awaiting_input` and writes the `awaiting` payload onto each blocked/in_review task.
- `scripts/work_generator.sh`: write through the CLI with `discovered_from` links.
- `frontend/src/pages/Home.tsx`: attention includes in_review counts and running sweeps; awaiting cards
  carry the question and link to JobDetail/TaskPanel.

**Verify:** a failing `command` acceptance releases with evidence and blocks after its cap; an
operator-gated task lands in_review and Approve completes it; blocking every task ends the next sweep
`awaiting_input` with a `awaiting` payload visible on Home; the dispatcher does not re-fire until a task
changes.

### Phase O5: the memory-consolidation organ (NEW)

No script edits to the live loops; this is a new agent + a new consolidation path. No the bridge agent touch.

**ADD**
- `agents/memory-keeper.md`: user-scope assistant definition, `scheduled` trigger (slow cron),
  `memory: letta`, persona = the mem0/ACE write-path instructions (ADD/UPDATE/RETIRE/NOOP, itemized
  deltas, index cap, Zep-style supersession, correction->preference promotion, skill promotion as an
  operator-confirmed proposal).
- `backend/services/memory_keeper.py`: the gather step (closed Jobs since watermark, `chat-distilled`
  facts, new `learnings.md` entries per scope), the top-k similar-memo fetch via `knowledge.recall`, the
  apply step (itemized delta writes into scope `knowledge/` + `learnings.md` + standing files, all
  through the existing knowledge/skills write APIs), and the index-cap enforcement. Pure additive; reads
  Jobs (append-only) and writes files under the scope task-store flock discipline.
- `backend/services/knowledge.py`: a hard line cap on `MEMORY.md` index writes (demote-to-topic-note when
  exceeded). Registration/index only; search internals frozen.

**MOD**
- `scripts/dispatch_triggers.sh`: the assistant-runtime dispatch path (already added in O3) runs the
  keeper's bounded data turn; the keeper is the first real consumer of that path.

**Verify:** run the keeper by hand over a scope with recent closed Jobs and a reviewer rejection: a
semantic fact lands as an itemized bullet in the scope note (a contradicting one is annotated superseded,
not overwritten); a repeated procedural pattern produces a skill-proposal the operator must confirm; the
index stays under the cap (demotion happened); recall returns the new fact. The bridge agent smoke check (a
registry agent was added; confirm per-channel agents remain excluded and the Discord round-trip works).

### Phase O6: config presets + the surfaces (NEW UI)

**MOD**
- Scope config: `projects.json` entries and agent frontmatter gain `autonomy` preset, `depth`, `fanout`,
  `limits`; `triggers.py` preset-expansion (`preset_to_triggers`, `triggers_to_preset`).
- `backend/routes/projects.py` + `routes/agents.py` autonomy endpoints: read/write the preset shape,
  return queue counts + `awaiting`.
- `frontend/src/components/AutonomyPanel.tsx`: preset control (Section 10.4), queue counts, advanced
  trigger disclosure.
- `frontend/src/pages/Jobs.tsx` -> the work-queue surface (sweep->child hierarchy, `awaiting_input`
  filter, agent/project filters); `frontend/src/pages/JobDetail.tsx` (P5) renders task binding, evidence,
  `awaiting` reply. De-orphan per P6 (nav + Home links).
- Chat toolbar running-sweep `StatusPill` linking to the work-queue view.

**Verify:** setting `continuous` + `deep` on a project expands to the right trigger+budget; the panel
shows queue counts and an `awaiting` payload; the work-queue view drills sweep->child->task; JobDetail
answers "what ran and what happened"; a chat scoping work shows the running-sweep pill. Full-width sweep
(1440/1024/768/390), tsc + check_design.sh.

### Phase O7: root-queue convergence (= T5)

Highest-risk (live cron), last. `AUTONOMY_DISABLED` for the whole cutover. This is
`autonomy-task-management.md` T5 verbatim (MIG `migrate_root_tasks.py`; `cron_agent.sh` Phase-2 store
convergence; repoint `graduate_proposals.sh`/`work_verifier.sh`/`curriculum_tick.sh`/
`daily_todo_digest.sh`/`autonomy_report.sh` at the store/CLI), with one addition: the root store is the
`self` project scope, so it participates in the dispatcher and the memory keeper like any other scope.

**Verify (T5's):** open-item parity between `TODO.md.migrated` and the store; one manual `cron_agent.sh`
claims from the store, gates, and regenerates TODO.md with no `sed` writes; digest/report scripts read
the store; then one live tick with autonomy re-enabled.

---

## 12. Risk register

1. **Cross-task parallelism corrupting a shared tree.** Guard: writes serial within a scope; concurrency
   only across disjoint `touches` or different scopes; one-sweep-per-scope-per-tick default; the
   write-serial rule is the research's hardest finding (Cognition).
2. **Runaway/derailing loops.** Guard: layered stop conditions (iteration + wall + cost caps),
   action-dedup and no-progress tripwires in `ralph.sh`, skip-unchanged-awaiting_input in the dispatcher,
   per-task attempt caps. External budget enforcement because declared caps have framework bugs (Claude
   Code #41143).
3. **Memory context-collapse or bloat.** Guard: itemized deltas only (ACE), supersession annotations not
   silent overwrite (Zep), hard index cap with demotion (Claude Code 200-line rule), consolidation off
   the hot path so it never invalidates a live cache. The markdown files are the audit trail; every
   keeper write is an inspectable diff.
4. **Reconcile duplicating a hand-edited line.** Guard (T1's): normalized-text exact match only;
   unmatched imports as NEW, never mutates; reconcile never deletes.
5. **Store lock contention** between sweeps, the keeper, and the API. Guard: one flock in
   `services.tasks`; the keeper reads append-only Jobs and holds the flock only for its file writes.
6. **The bridge agent Letta regression.** Guard: keeper is a registry agent excluded from per-channel; writes
   files not Letta; `knowledge.py` search internals and `supervisor.py`/`per_channel_agents.py`/
   `global_memory.py` internals frozen; the bridge agent smoke check after O5 and any backend restart.
7. **Live cron racing a half-edited script** (O3, O7). Guard: `AUTONOMY_DISABLED` bracket, whole-commit
   edits, the platform Phase-1 cutover discipline.

## References

- Anthropic, "Building Effective Agents" (2024); "How we built our multi-agent research system" (2025);
  "Effective context engineering for AI agents" (2025); prompt-caching and compaction docs.
- Cognition, "Don't Build Multi-Agents" (2025). MAST failure taxonomy (arXiv 2503.13657). Tran & Kiela,
  single- vs multi-agent under equal budgets (arXiv 2604.02460). "Illusion of Multi-Agent Advantage"
  (arXiv 2606.13003). LLMCompiler (arXiv 2312.04511). Blackboard-for-LLM revivals (arXiv 2507.01701,
  2510.01285).
- LangGraph (Send API, subgraphs, supervisor, interrupt); OpenAI Agents SDK (handoffs, agents-as-tools,
  max_turns); CrewAI (crews vs flows); AutoGen/AG2 (group chat, nested chats); Claude Code subagents +
  Agent SDK (context isolation, background subagents, resumability).
- Beads / `bd` (Steve Yegge, 2025; ready-work, atomic claim, discovered-from, JSONL->Dolt migration).
  Linear for Agents + Agent Interaction SDK (delegate model, awaiting-input, elicitations). GitHub
  Copilot coding agent (assignment-as-dispatch, cannot-merge-own-PR, Agents panel). Devin (playbooks,
  Required-from-User, ACU budgets, sleep/wake). Ralph (Huntley; HumanLayer "Brief History of Ralph").
  AGENTS.md; Cline Memory Bank; Obsidian AgentBoard. Zep "Markdown is not agent memory."
- CoALA (arXiv 2309.02427). MemGPT/Letta (memory blocks, sleep-time agents). mem0 (arXiv 2504.19413).
  Zep/Graphiti (arXiv 2501.13956). LangMem; ChatGPT memory design. ACE (arXiv 2510.04618). Generative
  Agents (2304.03442); Reflexion (2303.11366); Voyager (2305.16291). Anthropic Agent Skills. Claude Code
  auto-memory / MEMORY.md.
- Existing stack: `services/{jobs,triggers,tasks,agents_registry,knowledge,skills,global_memory,
  supervisor,chat_context,build_streaming}.py`; `scripts/{ralph,project_ralph,project_tasks,reviewer,
  work_generator,log_learning}.sh`; `cron_agent.sh`; `routes/{agents,projects,autonomy,chat}.py`.
- Prior design: `platform-redesign/{proposal,implementation-plan,task-management-exploration,
  review-addendum,autonomy-task-management}.md`; `agent-chat-redesign/plan.md`.
