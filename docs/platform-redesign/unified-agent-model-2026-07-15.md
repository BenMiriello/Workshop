# The unified Agent primitive (the bridge agent as an instance) — 2026-07-15

Operator direction (2026-07-15): named, scoped agents like the bridge agent should NOT be a
special subsystem. They should **emerge** from the basic agentic pipeline +
orchestration primitives with minimal extra config. A named agent is just a name
for a set of rules and memories that get triggered to run, can take autonomous
actions and decisions on their own schedule, and work on the operator's behalf.

## Definition

An Agent = these fields, nothing more:

- **slug / name** — its identity and its task scope key.
- **rules** — a persona / system prompt (its behavior).
- **memory** — a learnings scope (`services/learnings.py`) + notes vault entries.
- **triggers** — what makes it run: an event (a Discord message, a chat spawn, a
  file change) and/or a schedule (a cron tick). Triggers dispatch its scope.
- **autonomy mode** — manual | continuous (already modeled per scope).
- **tool grants** — which tools/MCP servers it may call.

Everything else it needs is SHARED pipeline, not per-agent code:

- task queue: `.tasks.json` per scope (`services/tasks.py`)
- dispatch + fan-out: `scripts/dispatch_triggers.sh`, `scripts/project_ralph.sh`
- execution: `scripts/ralph.sh` (`claude -p` + reviewer gate)
- budget/metering: `services/usage_ledger.py`
- self-healing: lease/fence/reclaim, parked, circuit breaker, watchdog
- learning: `services/learnings.py` (global + per-agent) + skill distillation
- shared context: the ambient scoped digest (`services/workspace_digest.py`)

So defining a new agent = pick a slug, write a persona, point a trigger at its
scope. Running it = when the trigger fires, dispatch the scope: claim ready
tasks, run them through the shared loop, bill the scope's budget, accrue the
scope's learnings. That is the entire delta over a bare project scope.

## The bridge agent in this model

The bridge agent is a SEPARATE process today (`<bridge-checkout>/`, its own systemd service, the
Discord bridge). It does not import the workshop backend (verified during Stage
9 scoping), so the workshop rebuild does not touch it and Stage 9 leaves it
running. Conceptually, though, the bridge agent IS an instance of the Agent primitive:

- slug: `the bridge agent`
- rules: its Discord-handler persona (`~/.claude/agents/discord-handler.md`)
- memory: its own learnings scope + the operator profile it re-loads
- trigger: inbound Discord message events (event) + a supervisor cron (schedule)
- autonomy: acts on its own schedule, takes actions for the operator
- tools: Discord, Plaid MCP, google-docs MCP, etc.

The bridge agent stays useful but is not central. The goal is that if we ever fold the bridge agent
onto the workshop rails, it needs only its slug + persona + Discord trigger; the
queue, dispatch, budget, self-healing, and learning are all the shared pipeline.
Not a rebuild of the bridge agent right now — a statement of the target shape so the Agent
concept in the workshop is built general enough that the bridge agent-like assistants fall
out of it with minimal config, rather than each being a bespoke Letta agent.

## Coordination model: ants, not a heavy conductor

Deliberately stigmergic (single-operator: keep it simple, no bottleneck):

- the shared task store + ambient digest IS the shared environment agents read
  and write (stigmergy) — agents coordinate through shared state.
- the dispatcher is a thin conductor: it admits (budget), meters, reclaims
  (self-healing), trips the breaker. It does NOT make every decision.
- agents largely self-manage: claim what is ready in their scope, dedup on
  creation, park what will not converge, accrue their own learnings.

A heavier central supervisor is explicitly avoided (bottleneck + multi-tenant
cruft). The light dispatcher + shared state is the coordination substrate.

## Why dedup is embeddings + LLM (two stages), not one or the other

Standard entity-resolution / retrieve-then-rerank shape:

- embeddings (bge-m3 via the local Ollama service) = cheap RECALL: narrow every
  open task in the scope down to the 1-2 plausibly-same candidates. O(n) LLM
  comparisons per task creation would not scale; the embedding cosine is the
  blocking filter.
- a local LLM = precise DECISION on just those candidates: same unit of work, or
  same area but different target? On merge, non-destructive (fold provenance into
  the survivor); on "no" or unreachable, downgrade to a linked "related" task so
  a false merge never silently drops work.

Shipped in `833ee22`. The LLM is the nuanced judge the operator expected; the
embeddings are what make it affordable to only ask it about real candidates.

## Relationship to the bridge agent, and our orchestration stack

Operator questions (2026-07-15): are we based on the bridge? what is similar/different?
do we use a framework to orchestrate agents?

**Not based on the bridge.** "The bridge" here is the bridge (a Discord <-> Claude
Code bridge with cron supervision, `<bridge-checkout>/`) running under the `the service user`
Linux user; the bridge agent runs on it. Workshop is a SEPARATE, standalone product: its
own FastAPI backend + React frontend + task/agent pipeline. What they share is
the HOST (`the service user` user), some infra conventions, and historically Letta (being
removed in Stage 9). Workshop is hosted ALONGSIDE the bridge, not built ON it. Keeping
Workshop on the `the service user` user is fine and is separable from the naming question.

**No agent-orchestration framework, on purpose.** No LangGraph / CrewAI / AutoGen
/ Temporal / Celery / message broker. The stack is hand-rolled:
- worker runtime: `claude -p` (Claude Code CLI, headless), no SDK wrapper.
- loop/orchestration: bash (`ralph.sh` worker loop, `project_ralph.sh` sweep,
  `dispatch_triggers.sh` dispatcher) + Python services (`tasks.py`, `jobs.py`,
  `usage_ledger.py`, `learnings.py`).
- state: plain JSON files (`.tasks.json`, `jobs.jsonl`) with `flock`; `cron` for
  scheduling.
Letta was the one framework dependency (agent memory); Stage 9 removes it. After
Stage 9 the platform has ~zero agent-framework dependency: a bespoke orchestrator
on the Claude CLI + shell + Python + files. This is deliberate: it matches the
ralph literature (favor a simple monolithic loop over jumping to distributed
multi-agent frameworks) and the single-operator no-cruft rule. If it outgrows
this, Temporal-style durable execution is the natural upgrade, not needed now.

## Deferred decision (operator to weigh in, AFTER the system is proven)

Not now; revisit once the rebuilt system is tested, proven, and more mature:
- Reevaluate the bridge's legacy name and its service branding. Hosting
  under the `the service user` user can probably stay; the NAME is what to reconsider.
- Fold a the bridge agent-form agent INTO this system, defined by our own Agent primitive
  (slug + persona + trigger, riding the shared pipeline), rather than as the
  separate bridge/Letta process it is today.
This is a discussion item that needs the operator; captured as a backlog task so
it is not lost, explicitly gated on "system tested and matured" first.
