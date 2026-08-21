# The core work loop — from-first-principles design (2026-08-21)

Status: **Proposal for Thread 1.** Written at the operator's direction to go back to
the drawing board on the core work-orchestration system, ground it in our own design
corpus plus fresh external research, and only then map it onto what is already built.
Nothing here is ratified yet. It reconciles the 2026-07 design set
(`entity-model-2026-07-18`, `primitive-roles-*`, `autonomy-model-*`, `latitude-model-*`,
`synthesis-agent-protocols-*`, `chat-work-linkage-*`, `chat-scope-design-*`,
`agent-interaction-model-2026-07-31`) against the built-but-mostly-off state of the code.

The operator's ask: a system that can **organize, identify, file, create, scope, refine,
execute, and review tickets**, running agents in the background, where he can put a chat
"into the right mode" to enable it and then **monitor and guide it stage by stage**,
testing repeatedly on real backlog work. Explicitly: reconsider everything we built,
including the Ralph loop; keep what serves the design, rework or drop what does not.

---

## 0. The one empirical fact to design around

Multi-agent LLM systems fail in production at 41–86% rates, and the causes are structural,
not model quality. The MAST taxonomy (NeurIPS 2025, 1,600+ traces across 7 frameworks)
buckets them: **specification / system-design ~42%, inter-agent coordination ~37%,
verification gaps ~21%.** ~80% of failures are bad specs and handoffs, not a missing agent.
Anthropic's own multi-agent system cost ~15x a single chat and only won on genuinely
parallel breadth-first work; Microsoft's Azure SRE team reversed a move to specialized
multi-agent because handoffs hurt reliability.

**Consequence: we do not build a swarm.** We build a **staged pipeline with a single active
worker per stage, a durable checkpointed ticket store as the source of truth, and
human-visible gates at stage boundaries.** Parallel fan-out is reserved strictly for
independent read/research work (like reading the backlog), never for the execution loop.

This directly answers "reconsider Ralph": the Ralph pattern (ruthless simplicity +
frequent context resets + a strong verification gate) is a **documented reliability
strategy**, not a limitation to escape. We keep the worker and the fresh-context reviewer.
The redesign is the layer around them, not them.

---

## 1. Target model

Five load-bearing choices, each backed by both our prior design and the external research:

1. **Tickets are the entities; the loop is an explicit stage machine over a durable store.**
   A ticket advances through typed stages: `intake → scoped → ready → executing → in_review
   → done` (with `blocked` / `parked` as off-ramps). State lives in the store, never only in
   an agent's context. Every stage transition is a checkpoint the operator can inspect, edit,
   and resume from. This is the LangGraph shape (durable execution + human-in-the-loop
   interrupt + streaming), applied over the task store we already have. We do not need the
   library; we need the shape.

2. **Four active responsibilities, mapped onto our existing role enum.**
   Research converges on a minimal effective set: **triager (intake), planner (scope),
   worker (execute), verifier (review).** Our enum `maker / reviewer / triager / keeper /
   broker` already anticipates this: worker = **maker**, verifier = **reviewer**, intake =
   **triager**; keeper (memory) and broker (cross-scope access) are orthogonal and out of the
   critical path. **Planner is not a separate background agent** — decomposition happens in
   the main chat with the operator (our own prior stance: "scoping happens in the main thread,
   not inside a run"), which is also where the operator gets to steer. So the background
   agents are exactly three: triager, worker, reviewer. Do **not** add domain-specialist
   workers — specialization multiplies the handoff failures that sank the Azure SRE system.

3. **Specs are structured, not prose.** The single biggest MAST fix. Every ticket carries
   Objective / Output / Start-from / Boundaries / **machine-checkable acceptance criteria** as
   fields (we already file the first four; acceptance criteria is the gap). Prose specs are
   where role ambiguity and duplicated work are born.

4. **A chat "mode" = how far work may auto-advance and which gates are armed.**
   This is the reframing that dissolves the current confusion (see §4). "Monitor closely" arms
   every transition gate; "run it overnight" gates only irreversible actions. The mode is not a
   personality; it is the set of interrupts.

5. **Termination is layered and mandatory on every loop.** Goal-confirmed-by-verifier **and**
   a hard iteration cap **and** a token/wall budget **and** no-progress detection that
   escalates to the operator rather than grinding. The absence of any one of these is the
   single most expensive documented failure mode.

---

## 2. The stages, concretely

- **intake** — a request (a chat message flagged "file this", or a note left on the
  blackboard) is turned into a filed, deduped, classified ticket. Owner: **triager**.
  Dedup happens *here*, against the existing store, before filing — not after execution.
- **scoped** — the ticket gets Objective/Output/Start-from/Boundaries/acceptance-criteria and
  a scope. Owner: the **main chat** (operator + assistant), because this is the highest-ROI
  step and the one the operator most wants to steer. A triager may draft; the operator ratifies.
- **ready** — dependencies satisfied and the ticket is classified executable. A cheap
  non-LLM predicate (we already have `ticket_readiness.ok` + `execution=="code"`), not a
  judgement call.
- **executing** — one **worker** (the Ralph loop) claims one ready ticket, works it, commits.
  Layered termination applies.
- **in_review** — a fresh-context **reviewer** checks the committed diff against the ticket's
  acceptance criteria. It never sees the worker's reasoning (self-preference bias is real and
  stronger in more capable models). Deterministic checks (tests / typecheck / design / stub
  scan / `apply.sh --check`) run first and feed *results* to any LLM judgment layer.
- **done / blocked / parked** — done on verifier pass; blocked when the worker hits an
  unspecified irreversible decision (it asks, it does not grind); parked on no-progress.

The operator's monitoring surface is the **ticket moving through these stages** (the per-app
Tickets/Runs column just built), not a chat-transcript firehose. Intervention is: open the
checkpoint, edit the state (e.g. rewrite a mis-scoped ticket), resume from there.

---

## 3. What we KEEP / REWORK / REMOVE / ADD

Grounded in the audit of the built state (drivers off; worker/gate/lease/track-back data model
solid).

**KEEP (strongest pieces, do not rebuild):**
- The **Ralph worker** (`ralph.sh` + `project_ralph.sh`): sentinel-gated re-fire loop with
  lease/heartbeat/fencing, per-run token + wall budget, and no-progress/thrash parking. This
  is the reliability core the research endorses.
- The **deterministic reviewer gate** (`reviewer.sh`): fresh-context, no self-approval,
  machine-checkable. The single most defensible thing we've built.
- The **task store**: status model, `next_ready`, `depends_on`, atomic claim with fencing.
- The **track-back data model** (`chat_work.thread_work` matching tickets to a chat).

**REWORK:**
- **Chat modes** — today `direct/orchestrated/auto` are prompt-text only; they do not change
  tools or actually dispatch. Reframe them as gate-arming / auto-advance depth (§4) and wire
  them to real behavior.
- **Autonomy vocabulary** — reconcile the doubled vocabulary: the ratified enum
  `reactive/supervised/autonomous` (ADR 0008) vs the older trigger presets
  `off/on-demand/scheduled/continuous`. Pick one axis for "how it behaves" and one for
  "when it wakes"; stop conflating.
- **Chat-filing path** — `spawn_task` files a `todo` but does not wake a runner and cannot set
  `execution`/readiness, so the ticket is unattended-ineligible by default. Make filing set the
  fields that make a ticket dispatchable (or explicitly not).
- **Track-back delivery** — currently poll-only; add push (SSE/notification) on
  done/failed/blocked so "your ticket finished" reaches the chat live.

**REMOVE / RETIRE (once replaced, not before):**
- The pretense that the three chat modes do anything mechanical. Either they gate real
  transitions or they go.
- Any second/duplicate status or ordering source the reconciliation surfaces (the corpus flags
  several: Run-vs-Session naming, superseded App/Project split). Out of Thread 1's critical
  path but noted.

**ADD (the actual new work):**
- **A resident driver + scheduler.** The wake-file plumbing exists on `/api/tasks` but nothing
  consumes it and the chat path doesn't write it; the cron dispatcher is commented off and no
  runner is resident. This is the missing muscle: something that picks up a ready ticket and
  runs the worker, under the operator's armed gates.
- **The execution boundary** (§5) — the reason nothing writes unattended today.
- **A triager** as a real intake stage (dedup + classify + file), the one genuinely new role.
- **Acceptance criteria** as a first-class ticket field the reviewer checks.

---

## 4. Modes, reframed (the conflation to name out loud)

The operator's sense that modes are opaque is correct, and the root cause is that **two
orthogonal things were collapsed into one control:**

- **Chat filing behavior** — does *this chat* file work automatically, offer cards, or stay a
  plain chat? (the `direct / orchestrated / auto` axis; prompt-level; per-chat is reasonable.)
- **Scope execution behavior** — does *this scope's* filed work get executed in the background,
  and how far can it advance before it needs the operator? (the autonomy/preset axis;
  driver-level; belongs to the scope/project, not the individual chat.)

"Put the chat in the right mode to enable the background process" is really **two settings**:
the chat files work (filing mode) **and** the scope is armed to execute it (execution mode).
Keeping them separate is what makes the settings hierarchy legible: filing is a chat choice,
execution is a scope choice, and the global autonomy override sits above both.

---

## 5. The blocker: unattended writes do not work today

This is the thing that gates "have the agents actually do the work," and it is not the task
store or the gates — those are fine. It is the execution boundary:

- `ralph.sh` invokes `claude -p` with **no `--permission-mode`**, and the loop has, per its own
  code comment, **never successfully written a file unattended** — the CLI silently auto-denies
  every gated tool call. The one-line fix (`--permission-mode bypassPermissions`) is
  deliberately left un-applied, pending the SEC-A1 execution-boundary decision.
- Meanwhile, bwrap scoped confinement is already **default-on and proven** (each turn runs
  confined to its scope root), and the uid-layer (`WORKSHOP_TURN_USER`) is set up but off.

**Recommendation:** the safe near-term path is to enable unattended writes for the worker
**under the existing bwrap confinement + a scoped worktree**, attended-verified once by the
operator on a single low-stakes real ticket, rather than waiting on the full uid-layer. That is
the smallest change that turns the loop from "files a todo that sits forever" into "an agent
actually does the work," and it stays inside a confinement boundary we already trust. The
uid-layer remains the durable answer and can land after.

This is a real gate with an attended-verification requirement — it is the first thing to cross,
and it is the operator's to green-light.

---

## 6. Path to Thread 1 working and monitorable

The goal of Thread 1 is not the whole vision. It is: **close the loop for real backlog tickets,
under stage-by-stage observation, and learn from watching it.** Staged so each step is testable
before the next:

- **S1 — Reconcile the model on paper.** Lock the stage machine, the three background roles,
  the two-axis mode reframing, and the acceptance-criteria field. (This doc is the draft; it
  needs the operator's ratification on the forks in §7.)
- **S2 — Cross the execution boundary.** Enable unattended worker writes under bwrap + worktree;
  attended-verify on one low-stakes ticket. Nothing else proceeds until an agent has actually
  written and committed a file unattended and passed the gate.
- **S3 — Stand up one resident driver + arm one scope.** A single supervised runner that picks a
  ready ticket and runs the worker to `in_review`, with the operator as the final gate. Wire the
  chat-filing path to set dispatchable fields and to wake the runner. Add push track-back so the
  originating chat sees completion live.
- **S4 — Run real backlog tickets under observation.** Deliberately choose tickets **not** about
  the pipeline itself (the operator's explicit ask), let the armed scope work them, and watch:
  is the scoping good, is the volume right, is the visibility enough, are the gates in the right
  places, does it duplicate work? Iterate the settings and the surface from what we see.
- **S5 — Dogfood the intake/triager on the backlog.** Let a triager agent read the recent chats
  and the imported design session and propose deduped, scoped tickets — and measure it: does it
  create duplicates, re-read the same things needlessly, miss nuance? This is both a real
  deliverable (Thread 2) and the hardest test of intake quality.

S4 and S5 are where we find out whether the design is right, which is the whole point of doing
them on real work under the operator's eye rather than designing further in the abstract.

---

## 7. Decisions needing operator ratification before we build

1. **Execution boundary (§5):** green-light enabling unattended worker writes under bwrap +
   worktree with a single attended verification, or hold for the full uid-layer first?
2. **Roles:** accept the three-background-role model (triager / worker / reviewer) with
   **decomposition staying in the main chat**, rather than a separate planner agent?
3. **Modes (§4):** accept the two-axis split — filing is a per-chat setting, execution is a
   per-scope setting, global override above both — as the settings hierarchy?
4. **Scheduler shape (S3):** one supervised resident driver landing everything at `in_review`
   for the operator as final gate, to start — before any "autonomous to done" mode?

None of these are filed as tickets yet, per the operator's instruction to plan first.

---

## 8. Design consideration: a task carrying its own fitness check

Operator direction (2026-08-21): a task should be able to determine whether it can actually
do the thing, before or as it works it. If a worker realizes mid-task that it cannot complete
the ticket as specified, it must not grind or silently half-do it. It decides, by judgment
scaled to the task and the agent, between a small closed set of outcomes:

- **do the prerequisite it discovered** — only when that prerequisite is itself in-scope,
  reversible, and clearly within the ticket's boundaries. Otherwise not.
- **file the discovered work as a separate ticket** — the default when the prerequisite is a
  distinct piece of work (e.g. "this needs an investigation first, then a fix"). The operator's
  own example: investigate/diagnose vs fix should usually be separate tickets, each able to say
  whether it can do its part.
- **block and escalate / mark as needed** — when the ticket is mis-scoped or needs a decision,
  raise the block rather than improvise.

This is the intersection of three things we already have designed: the **latitude** floor
(what a worker may decide when the ticket is silent), the **blocked-and-escalate** flow (how it
surfaces a block), and the **triager** (who turns a discovered-need into a filed ticket). The
new requirement is the up-front self-assessment: a ticket should carry, or the worker should
produce at claim time, an explicit "can I do this as written?" check whose failure routes into
one of the outcomes above instead of into a failed run. This belongs in the execute stage's
contract and should be specified when S3/S4 land, not invented ad hoc per run.

## Sources (external research)

- MAST — Why Do Multi-Agent LLM Systems Fail? (arXiv:2503.13657, NeurIPS 2025)
- Anthropic — How we built our multi-agent research system
- LangGraph human-in-the-loop / interrupt + checkpointing docs
- Self-Preference Bias in LLM-as-a-Judge (arXiv:2410.21819; EMNLP 2025)
- The Agent That Spent $47K on Itself — autonomous-loop postmortem
- Ralph loops make agentic coding reliable with ruthless context resets (LinearB)
- Task decomposition strategies for planning in LLM agents (BRICS)
