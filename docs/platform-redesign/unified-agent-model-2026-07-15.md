# The unified Agent primitive (masini as an instance) — 2026-07-15

Operator direction (2026-07-15): named, scoped agents like masini should NOT be a
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

## Masini in this model

Masini is a SEPARATE process today (`~/clawdbot/`, its own systemd service, the
Discord bridge). It does not import the workshop backend (verified during Stage
9 scoping), so the workshop rebuild does not touch it and Stage 9 leaves it
running. Conceptually, though, masini IS an instance of the Agent primitive:

- slug: `masini`
- rules: its Discord-handler persona (`~/.claude/agents/discord-handler.md`)
- memory: its own learnings scope + the operator profile it re-loads
- trigger: inbound Discord message events (event) + a supervisor cron (schedule)
- autonomy: acts on its own schedule, takes actions for the operator
- tools: Discord, Plaid MCP, google-docs MCP, etc.

Masini stays useful but is not central. The goal is that if we ever fold masini
onto the workshop rails, it needs only its slug + persona + Discord trigger; the
queue, dispatch, budget, self-healing, and learning are all the shared pipeline.
Not a rebuild of masini right now — a statement of the target shape so the Agent
concept in the workshop is built general enough that masini-like assistants fall
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
