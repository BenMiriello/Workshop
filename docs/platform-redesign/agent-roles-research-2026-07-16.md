# Primitive agent roles and the system for their interaction (2026-07-16)

> **STATUS: PROPOSED. NOT BUILT.** No code written. The `triager` role does not exist.
> The dispatcher bug this report found (`dispatch_triggers.sh:329` hardcodes
> `role == "memory"`) is STILL LIVE: five trigger-carrying agents never run.

Status: design proposal. Owns asks **D1-D6** and **E1-E5** from
`operator-asks-2026-07-16.md`. Companion to `resource-access-protocol-2026-07-16.md`
(A1-A12) and `scopedness-and-escalation-2026-07-16.md` (B1-B4, C1-C4).

House style follows `notes-system-plan-2026-07-15.md`: audit, then research
adopt/reject, then design, then sequenced phases. Every claim about current code
carries `file:line` and was read on 2026-07-16. Research claims carry URLs.
Nothing here is implemented; this report is the only file written.

---

## Executive summary

1. **The `role` tag is not free-form in practice. It is already a closed enum of
   size one, hardcoded into the dispatcher.** `scripts/dispatch_triggers.sh:329`
   branches on `a.get("role") == "memory"`; every other assistant-runtime agent
   falls through to `skip-assistant` at `:344`. The operator's note-triage ant
   (D5) **cannot exist today**: it would be written, registered, and silently
   skipped forever. This single line is the load-bearing blocker for this whole
   thread, and generalizing it is Phase R1.

2. **The right axis for a role taxonomy is output authority, not job title.** The
   operator named this themselves ("a role that can only propose a ticket is very
   different from one that can edit code"). Building on authority rather than on
   SDLC phase (MetaGPT) or persona (CrewAI) collapses a dozen plausible roles into
   **five**: `maker`, `reviewer`, `triager`, `keeper`, `broker`. Three of the five
   already exist in some form. One (`broker`) is designed by the companion report.
   So the delta this report proposes is **one new role**: `triager`, the ant.

3. **The ant metaphor does not transfer, but stigmergy does.** Response-threshold
   models assume thousands of anonymous interchangeable agents whose activation is
   nearly free. We have under ten typed agents whose activation costs roughly 15x a
   chat turn. Probabilistic recruitment, pheromone decay, and emergent role
   differentiation are all rejected below with reasons. The one mechanism that
   earns its keep is stigmergy proper: **coordinate through shared state, never by
   agent-to-agent messaging.** That is already the accepted position
   (`unified-agent-model-2026-07-15.md`, "Coordination model: ants, not a heavy
   conductor"), and this report gives it a rigorous name.

4. **The honest name for the architecture we already have is a blackboard with a
   declarative control shell.** Task store plus knowledge scopes is the blackboard;
   role agents are knowledge sources; `dispatch_triggers.sh` is the control shell.
   We adopt the blackboard's decoupling (the shell must not know what each role
   does) and **reject** its opportunistic self-selection (which costs N LLM calls
   per tick to decide nothing).

5. **There is no conductor role, and that is the answer to E4.** The conductor is
   about twenty lines of deterministic Python. `agents/workshop-overseer.md` is a
   ghost: assistant runtime plus a loop trigger means it hits `skip-assistant`
   (`dispatch_triggers.sh:323,344`) on every tick. Overseer behavior that genuinely
   needs judgment is a `triager` at a higher scope. Scope nesting supplies
   escalation, borrowed from OTP supervision trees.

6. **The default agent set for a new project is zero agents.** The project loop is
   not an agent. This is the anti-cruft answer and it echoes the operator's own
   "0 or multiple agents" (A4). One optional group (`tended` = triager + keeper) is
   the one-click upgrade. Two groups shipped, not a catalog.

---

## Part 1: audit of current state (what exists, file:line)

### 1.1 The role tag: inert on paper, load-bearing in practice

`backend/services/agents_registry.py:63-68` documents the intent:

> `role` is a free-form tag (e.g. "memory", "builder", "researcher") that TYPES an
> agent without changing how it is assigned work.

That comment is now false. Three verified facts contradict it:

- **Only one of fifteen agent definitions sets a role.** A grep over `agents/`
  returns exactly one hit: `agents/memory-keeper.md:4` (`role: memory`). Every
  other definition (`workshop-overseer`, `email-triager`, `plaid-watcher`,
  `the bridge agent`, the builders, the probes) leaves it unset.
- **The dispatcher pattern-matches that one value.**
  `scripts/dispatch_triggers.sh:323-345`: if an agent's runtime is not `builder`,
  the only path to execution is `if a.get("role") == "memory"` (`:329`), which
  plans a `("memory", scope_key, assignee)` triple (`:336`). Everything else prints
  `skip-assistant` (`:344`) and is dropped.
- **There is no validation.** `agents_registry.py:75-80` defines `_coerce_depth`
  and `_coerce_fanout`, which snap bad values to safe defaults. There is **no
  `_coerce_role`**. `role` is parsed raw at `:160`, emitted raw at `:374-375`, and
  updated raw at `:455-460` (empty string clears it). So `role: memoy` is a valid
  write that silently makes the memory-keeper never run again.

The consequence for this thread is direct: a free-form tag that a shell script
branches on is not a free-form tag, it is an undeclared enum with a typo-shaped
failure mode. **D5's note-triage ant is unbuildable until `:329` becomes a table.**

### 1.2 The dispatcher is already shaped like a control shell

`scripts/dispatch_triggers.sh` builds a `plan` list of `(TYPE, SCOPE, ASSIGNEE)`
triples in Python (`:262, :293, :336, :372`), then a bash loop dispatches on TYPE
with an if/elif chain (`:383-405`):

- `project` launches `project_ralph.sh <slug>`
- `agent` launches `project_ralph.sh --dir <dir> --assignee agent:<slug>`
- `memory` launches `memory_keeper_run.sh <scope>`

This is exactly a control shell with a hardcoded three-entry dispatch table. The
generalization is small and mechanical.

Also present and reusable:

- `MAX_AGENT_SWEEPS = 1` (`:61`), a per-tick activation cap.
- `.runtime/trigger-state.json` (`:53`), the watermark store.
- `.runtime/circuit-breaker.json` (`:54`), per-scope backoff.
- The **F16 pattern** (`:312-321` and `:374-379`): fired cron keys are committed to
  state **only if the agent was actually dispatched**, so a gate-skipped trigger
  stays due rather than being silently lost. This is precisely the right precedent
  for a note-triage watermark, and it is already written.

### 1.3 The role runner contract already exists, for one role

`scripts/memory_keeper_run.sh:1-21` documents a contract another agent depends on
exactly:

```
bash scripts/memory_keeper_run.sh <scope-key>          # one pass, writes
bash scripts/memory_keeper_run.sh                      # every active scope
bash scripts/memory_keeper_run.sh <scope-key> --dry-run # preview, writes nothing
```

with scope keys `workshop | project:<slug> | agent:<slug> | app:<name>`. This is a
role runner. It is the prototype for the general shape and needs no redesign, only
a directory and a naming convention.

### 1.4 depth and fanout

`agents_registry.py:69-72`: `DEPTHS = ("quick","standard","deep")`,
`FANOUTS = ("off","read-only")`, defaults `standard` and `read-only`. Coerced at
`:75-80`, parsed at `:161-162`, and emitted on write only when non-default
(`:376-379`), which keeps fresh definitions clean.

Both are declared on **every** agent. Both mean something for **one** role. `depth`
maps to ralph iteration caps and model tier, which only a maker consumes. `fanout`
bounds native subagent use, which only a maker does. The shipped `memory-keeper.md`
proves the point: it sets `limits.max_iterations: 1` (`agents/memory-keeper.md:11`)
because a consolidation pass is one bounded read, and depth is meaningless for it.

### 1.5 Autonomy presets

`backend/services/triggers.py:235`: `PRESETS = ("off","on-demand","scheduled",
"continuous")`, with a documented expansion table at `:222-227` and back-compat
mapping to the legacy `autonomy.mode` at `:238-253`. Trigger kinds are
`manual|cron|event|loop` (`triggers.py:44`), with `loop` carrying `max_iterations`,
`budget`, and `completion_gate` so an unattended loop is bounded (`:14-22`).

This model is sound and this report changes none of it. It only asks which preset
each role defaults to.

### 1.6 The task substrate

`backend/services/tasks.py` docstring (lines 1-44) defines canonical Task v2. The
fields this report depends on already exist:

- `provenance: str | None` (`:34` in the docstring), described as the stable dedupe
  key. This is where a note link lives.
- `discovered_from: str | None` (`:35`), the beads-style provenance edge.
- `source: {"kind": "operator"|"chat"|"agent"|"autogen", "ref": str|None}` (`:33`).
- `claim_token: int | None` (`:28`), a monotonic fencing token bumped on every
  claim; plus `lease_until` (`:29`) and `claim_pid` (`:30`).
- `attempts` (`:27`) and `max_attempts` (`:21-23`), feeding `parked` as a
  dead-letter status (`:60-63`, `STATUSES` at `:61`).
- `awaiting: {"question": str, "since": iso}` (`:25`), the operator elicitation.

Nothing in this report requires a Task schema change. That is deliberate and
matches the accepted split in `chat-tickets-orchestrator-plan-2026-07-15.md`
(Task = ticket, Job = attempt, not merged).

### 1.7 The notes substrate

`backend/services/knowledge.py:92-111` (`scope_dir`) is the canonical Notes store
per accepted ADR `docs/decisions/0002-notes-substrate-is-knowledge.md`:

- `workshop` to `shared/knowledge/`
- `project:<slug>` to `projects/<slug>/knowledge/`
- `app:<name>` to `apps/<name>/knowledge/`
- `agent:<id>` to `.runtime/agents/<id>/knowledge/`

Per-note `<id>.md` plus a `MEMORY.md` index. Surfaces exist:
`frontend/src/pages/Notes.tsx`, cross-scope `backend/routes/notes_all.py` (router
at `:33`, `GET ""` at `:106-107`, `GET /scopes` at `:136-137`).

So the ant's input substrate is built. Only the pickup is missing.

### 1.8 Where agent definitions and templates live

- `agents/` is **gitignored** (`.gitignore:83`). It holds live instances with
  runtime state (`agents/workshop-overseer.md:16` carries a `letta_agent_id`).
  Correct as an instance directory, unusable as a template home.
- `projects/_templates/` is **tracked** and holds four project templates
  (`agent-tool`, `fastapi-capability-provider`, `integrated-sub-app`,
  `static-site`). `CLAUDE.md` states the rule: per-project working trees are
  gitignored, "only the registries (`projects.json`, `build_agents.json`) and
  `projects/_templates/` are tracked."
- `shared/skills/` is tracked and holds three skills (`frontend-design-system`,
  `git-worktree-workflow`, `notes-and-docs`).

The precedent is unambiguous: **instances gitignored, templates tracked under a
`_templates` or `shared/` sibling.** Agent templates should follow it rather than
invent a location.

### 1.9 The ghost

`agents/workshop-overseer.md` declares `runtime: assistant` (`:3`) and a `loop`
trigger (`:6-11`). Trace it through `dispatch_triggers.sh`: the loop trigger sets
`fire = True` (`:319-320`), then `runtime != "builder"` (`:323`), then
`role != "memory"` (`:329` fails, since it sets no role), then `skip-assistant`
(`:344`). **The always-on autonomous worker for the workshop has never run through
this path.** It is a definition file, not a behavior.

This is worth stating plainly because "we already have an overseer" is otherwise a
reasonable thing to believe from the file listing, and the E4 answer below depends
on it being false.

---

## Part 2: research findings, what we adopt and what we reject

### 2.1 Ants: the metaphor is a trap, one mechanism survives

**What the literature says.** In response-threshold models, each worker carries a
per-task threshold; a task emits a stimulus that grows while unserved; a worker
engages probabilistically when the stimulus exceeds its threshold. Low-threshold
individuals engage sooner, and the colony's allocation self-organizes without
central control, conscious global evaluation, or direct communication
([Task Allocation in Ant Colonies](https://link.springer.com/chapter/10.1007/978-3-662-45174-8_4);
[response-threshold verification study, PMC6586672](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6586672/);
[stochastic spatial model, arXiv 1601.01023](https://arxiv.org/pdf/1601.01023)).
Stigmergy is the coordination substrate: environmental modification enables
indirect cooperation without explicit agreement protocols or centralized
allocation.

**Where it fails to transfer, concretely.**

- **Population.** These models are statistical. Allocation emerges from the law of
  large numbers over thousands of draws. With three to seven agents there is no
  distribution to converge on; a probabilistic engage rule with N=3 is just noise
  in place of an assignment. Nothing is gained over `assignee=agent:<slug>`, which
  `tasks.py:18` already supports.
- **Cost per act.** An ant that engages and finds nothing has wasted a few seconds
  of ant. An agent that activates and finds nothing has spent tokens; Anthropic
  measured multi-agent systems at roughly **15x the tokens of a chat**
  ([multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)).
  Probabilistic over-recruitment, which is a feature in a colony (redundancy is
  robustness), is a straight cost bug here.
- **There is an operator.** The defining premise of the ant literature is the
  absence of a supervisor. This system has exactly one, he is the point of the
  system, and he wants to see what happened. Emergent allocation is
  anti-legibility.
- **Even in robotics, stigmergy has a density ceiling.** A Royal Society study with
  N > 300 real robots and simulations to N = 400 found that in spatially
  constrained high-density settings a simple stigmergic avoidance algorithm becomes
  first no better than, then *inferior to*, non-interacting random walkers, and
  concluded the assumption of robustness and scalability "may need to be
  re-examined" ([Testing the limits of pheromone stigmergy in high-density robot
  swarms](https://royalsocietypublishing.org/doi/10.1098/rsos.190225)). Prior
  demonstrations held only for N < 30. The literature is bounded on both ends, and
  we sit far below the lower bound.

**What we adopt: exactly one mechanism, stigmergy proper.** Agents coordinate by
reading and writing shared state, never by messaging each other. A note's existence
is the signal that it needs triage. A task's `todo` status is the signal that it
needs a maker. No subscription, no queue, no inbox, no handoff protocol. This is
already the accepted posture in `unified-agent-model-2026-07-15.md` ("the shared
task store + ambient digest IS the shared environment agents read and write") and
this research confirms it is the only part of the ant model that survives contact
with N < 10.

**What we reject:** response thresholds, stimulus accumulation, pheromone decay,
probabilistic recruitment, emergent role differentiation, and redundant
over-recruitment. All five are population-scale statistical devices with no
small-N analogue.

**What the operator actually meant.** `operator-asks-2026-07-16.md` Part 3 already
frames "like ants having little jobs" as "a preference for many small, cheap,
single-purpose workers over few large general ones," and explicitly permits a
report to conclude the model does not transfer provided it says so. This report
says so, and keeps the preference: the roles below are small and single-purpose,
and the largest single design choice (output authority as the axis) is what makes
them small.

### 2.2 Blackboard systems: the underrated fit, adopted with one amputation

The classic Hearsay-II architecture has three parts: **knowledge sources**
(specialists that do not know about each other), the **blackboard** (shared global
state holding the problem, partial solutions, and hypotheses), and the **control
shell** (decides which knowledge source acts next). It was built for ill-structured
problem domains with heterogeneous knowledge sources
([overview](https://rajatpandit.com/agentic-ai/the-blackboard-architecture/)).

The LLM-era revival is empirical, not nostalgic.
[arXiv 2510.01285](https://arxiv.org/abs/2510.01285) reports a blackboard
multi-agent system beating both RAG and the master-slave (orchestrator-worker)
paradigm by **13% to 57% relative** on end-to-end task success across KramaBench,
DSBench, and DA-Code, with up to 9% relative gain in discovery F1. The stated
reason is the one that matters to us: in master-slave, the coordinator must have
"full observability over sub-agents' knowledge and competencies," which does not
hold at scale; on a blackboard, agents "volunteer to respond based on their
capabilities" and the coordinator need not know each agent's expertise. See also
[arXiv 2507.01701](https://arxiv.org/abs/2507.01701).

**Adopt: the decoupling.** The control shell must not know what a role does. Today
it does, hardcoded, at `dispatch_triggers.sh:329`. That is precisely the
master-slave coupling the paper indicts, and it is why adding the note-triager
today would mean editing the dispatcher. After Phase R1 the shell knows only
"which roles report ready work," and a new role is a new file.

**Reject: opportunistic self-selection.** The volunteering half assumes activation
is cheap enough that every knowledge source can inspect every blackboard change.
For us, "volunteer" means an LLM call per agent per tick to answer "is there
anything for me?", almost always answered "no". At N agents and a 20-minute cron
that is N LLM calls per tick to decide nothing. We take the **declarative** half
instead: each role declares a cheap non-LLM **ready predicate** (a mtime scan, a
task query) that the shell evaluates in Python. The LLM runs only once the
predicate says there is work, and only on the work.

Call the resulting shape what it is: **a blackboard with a declarative control
shell.** Blackboard decoupling, deterministic activation.

### 2.3 Why multi-agent systems fail: the argument for fewer roles

- **MAST** ([arXiv 2503.13657](https://arxiv.org/html/2503.13657v1)): 14 failure
  modes over 150+ tasks and 5 frameworks. Specification and design failures
  **41.8%**, inter-agent misalignment **36.9%**, verification/termination
  **21.3%**. Prompt-level mitigations bought only 5% (AG2) to 14% (ChatDev); the
  authors call for structural fixes such as dedicated verifier roles and clearer
  topologies.
- **Cognition, "Don't Build Multi-Agents"** (2025): actions carry implicit
  decisions; parallel workers make conflicting unprescribed assumptions. Share full
  traces, keep mutation single-threaded.
- **Tran & Kiela** ([arXiv 2604.02460](https://arxiv.org/abs/2604.02460)): with
  reasoning budgets held equal, a single agent matched or beat multi-agent on
  multi-hop reasoning, because every inter-agent message is lossy compression.
- **Anthropic, "Building Effective Agents"**
  ([link](https://www.anthropic.com/engineering/building-effective-agents)): add
  agentic complexity "only when it demonstrably improves outcomes."

**Consequence adopted:** the top failure bucket is specification, not capability.
Every role added is a new specification surface and a new handoff, and each handoff
is lossy. Therefore the report's obligation is to **minimize the role count**, and
each role must be justified by an authority boundary that cannot be expressed as a
field on an existing role. This is the discipline that produces five instead of a
dozen.

Note the concurrence with `research-findings-2026-07-15.md`, which already adopted
MAST's structural-verifier recommendation and the fresh-context reviewer gate.
Nothing here re-opens that.

### 2.4 Named patterns: what we have, what we lack

Anthropic's pattern vocabulary
([Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)):
prompt chaining, **routing**, parallelization, **orchestrator-workers**,
**evaluator-optimizer**, and autonomous agents.

Mapped to the current stack:

| Pattern | Status here |
| --- | --- |
| Orchestrator-workers | Built. Sweep Job plus child Jobs (`orchestration-and-autonomy.md` section 4.1). |
| Evaluator-optimizer | Built. Worker plus `reviewer.sh` fresh-context gate. |
| Parallelization | Built and bounded. Native subagents, read-only, `fanout` knob. |
| Prompt chaining | Not needed. A maker chains in-context. |
| **Routing** | **Missing.** Nothing classifies an incoming item and sends it somewhere. |

Routing is exactly the gap the note-triage ant fills: an item arrives (a note),
something must classify it and decide where it goes. **The ant is a router.** That
reframing is the cleanest justification for the one new role, and it lands it in a
named, well-understood pattern rather than in a metaphor.

### 2.5 Role vocabularies in the wild: mostly rejected

- **MetaGPT** ([arXiv 2308.00352](https://arxiv.org/html/2308.00352v6)) encodes
  SOPs as prompt sequences over five roles: product manager, architect, project
  manager, engineer, QA engineer, in an assembly-line paradigm with strictly
  structured schema handovers.
  **Reject the role set.** It is a pipeline for generating a greenfield codebase
  from one prompt. Our unit of work is a Task against an existing tree, and our
  maker plans, writes, and tests in one context. Splitting that into four roles adds
  three lossy handoffs (Tran & Kiela) to buy nothing, and multiplies the
  specification surface that MAST says is already 41.8% of failures.
  **Adopt the structured-handover principle**, which we already implement: the Task
  fields (`acceptance`, `touches`, `effort`, `boundaries` per
  `research-findings-2026-07-15.md` recommendation 2.6) are the schema.
- **CrewAI** organizes agents around role-as-persona with goals and backstories
  ([comparison](https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen)).
  **Reject.** A role that is only a persona string has no trigger, no scope, and no
  output contract, so nothing can dispatch it or bound it. This is a precise
  description of what `role` is today (`agents_registry.py:65-68`) and of why it is
  inert. Reported failure mode is on point: with a poorly defined task an agent
  "repeatedly attempts the same step without progress," and the lack of visibility
  makes it hard to catch.
- **LangGraph** makes the topology explicit as a graph with conditional routing.
  **Reject the framework** (the stack is `claude -p` plus bash plus python plus
  json, per the brief), **adopt the principle**: the topology should be explicit and
  readable in one place. Our one place is the dispatch table proposed in R1.

### 2.6 Organizational analogues that fit N < 10 better than ants

- **Triage.** The strongest concrete precedent is not biological, it is
  operational. GitLab's triage automation labels, comments, assigns, closes stale
  items, and **generates new issues based on conditions**
  ([gitlab-triage](https://about.gitlab.com/blog/automating-agile-workflows-with-the-gitlab-triage-gem/)).
  GitHub's Dependabot auto-triage rules are **metadata-keyed rules** (CVE, CWE,
  scope, ecosystem, manifest path, package, severity) that dismiss, snooze, reopen,
  or open a PR
  ([changelog](https://github.blog/changelog/2024-06-26-dependabot-auto-triage-rules-are-generally-available/)).
  Stale-bot closes what nobody addressed after a warning window
  ([practice](https://nesbitt.io/2026/01/10/16-best-practices-for-reducing-dependabot-noise.html)).
  **Adopt all three shapes**: deterministic rules keyed on metadata first, LLM
  judgment only on the residue, and an explicit notion of unaddressed. This is the
  concrete skeleton of D5, and it is the reason the ant does not need to be
  probabilistic: real triage systems are rule-first.
- **Actor model / OTP supervision trees.** Erlang's supervisor watches a child,
  and on crash restarts it, restarts siblings, or **escalates to its own parent**;
  if restart intensity is exceeded (canonically more than 3 restarts in 5 seconds)
  the supervisor crashes itself and propagates upward
  ([Erlang supervisor principles](https://www.erlang.org/doc/system/sup_princ.html);
  [Adopting Erlang](https://adoptingerlang.org/docs/development/supervision_trees/)).
  "Let it crash" does not mean carelessness; it means **separating normal operation
  from error recovery**, with the crash always observed by a supervisor.
  **Adopt both.** (a) A role never handles its own failure; it escalates. (b) Scope
  nesting (agent inside project inside workshop) is a supervision tree, and
  escalation walks it upward. This is the E4 answer, and it is already half-built:
  `attempts` and `max_attempts` leading to `parked` (`tasks.py:21-23,27,60-63`) is
  restart intensity leading to escalation, and the per-scope circuit breaker
  (`dispatch_triggers.sh:54`) is the supervisor crashing itself.
- **Curator / gardener.** The playbook-curator pattern (a background agent that
  refines procedures from execution feedback rather than hand curation) is
  converged practice ([mem0 context-engineering
  guide](https://mem0.ai/blog/context-engineering-ai-agents-guide)), and is exactly
  what `memory-keeper` already is per `orchestration-and-autonomy.md` section 6.2.
  **Adopt nothing new**; the role already exists. This is a useful negative result:
  the gardener/janitor/curator/librarian roles that agent-ops writing enumerates
  separately all have the **same output authority** (memory and notes hygiene) and
  collapse into one role here.

---

## Part 3: the design, a core set of five roles

### 3.1 The axis: output authority

A role is **not** a job title, a persona, or a skill set. A role is **the set of
writes an agent is permitted to make.** Everything else about an agent (what it
knows, how it judges, what it reads) is already expressible in fields the registry
has: `purpose`, the definition body, `capabilities`, `triggers`, `project_slug`.

Why this axis and not another:

- It is the axis the operator named ("a role that can only propose a ticket is very
  different from one that can edit code").
- It is the only axis a **deterministic control shell can enforce**. The shell
  cannot check that an agent "thinks like an architect". It can check which runner
  it invokes and which write APIs that runner has.
- It is the axis that makes failure modes **structural rather than behavioral**.
  A triager that cannot write notes cannot re-trigger itself. That is a guarantee,
  not a prompt instruction, and MAST's finding (prompt-level mitigation buys 5-14%)
  says guarantees are what pay.
- It collapses the taxonomy. Every plausible role differing only in *what it reads*
  or *when it wakes* folds into an existing role plus a field.

### 3.2 The five

| Role | May write | Must not write | Status |
| --- | --- | --- | --- |
| `maker` | working tree, commits, task transitions on its own claim | memory, other scopes' trees | exists (`ralph.sh`, `project_ralph.sh`) |
| `reviewer` | a verdict, plus `learnings.md` rejection reasons | the tree, tasks | exists (`reviewer.sh`, work-reviewer subagent) |
| `triager` | tasks (file, link, escalate), a triage decision record | the tree, notes, verdicts | **NEW, this report** |
| `keeper` | knowledge notes, `learnings.md`, standing files, skill *proposals* | the tree, tasks | exists (`memory-keeper`) |
| `broker` | nothing (releases information only) | everything | NEW, designed in `resource-access-protocol-2026-07-16.md` |

A second, orthogonal distinction falls out of the audit and is worth naming because
it decides what is templatable:

- **Loop-invoked roles** (`maker`, `reviewer`) are called by the worker loop, not by
  the control shell. `ralph.sh` invokes the reviewer; the sweep invokes the maker.
  They are not dispatchable and therefore not agent-templatable.
- **Shell-dispatched roles** (`triager`, `keeper`, `broker`) are activated by the
  control shell against a scope, on a trigger, with a ready predicate.

Only shell-dispatched roles get templates. This is why Part 5 ships three templates
and not five.

### 3.3 Role specifications

Each spec answers the seven questions the brief demands: trigger, scope, inputs,
allowed outputs, autonomy default, termination/escalation, failure mode.

#### maker (exists, unchanged)

- **Trigger.** Demand (a claimed ready task). Fired by a sweep, never directly by
  the shell.
- **Scope.** A scope with a working tree and a verification path: `project:<slug>`
  or a builder's cwd. Per `orchestration-and-autonomy.md` section 8, this is the
  bar for hosting a loop.
- **Inputs.** One Task (`text`, `acceptance`, `touches`, `effort`,
  `max_attempts`), the tree, fresh context per iteration.
- **Outputs.** File writes and commits inside `touches`; task transitions on its own
  claim only (`claim_token` fence, `tasks.py:28`); follow-up tasks with
  `discovered_from` set; native read-only subagents if `fanout` allows.
- **Autonomy default.** Inherited from the scope preset; project and builder scopes
  default to `on-demand` (`triggers.py:222-227`).
- **Termination / escalation.** Reviewer approval plus green acceptance checks;
  else iterate; `attempts` past `max_attempts` sets `parked`; a question for the
  operator sets `awaiting`.
- **Failure mode.** Thrash. Already diagnosed and mitigated in
  `research-findings-2026-07-15.md` section 1 (in-run no-progress detection, hard
  iteration cap, dual-condition exit). Not re-opened here.
- **Knobs that apply.** `depth`, `fanout`. This is the only role for which they mean
  anything.

#### reviewer (exists, unchanged)

- **Trigger.** Demand (a maker claims completion).
- **Scope.** The task's scope.
- **Inputs.** Task plus acceptance criteria plus the diff plus executed check
  output. Explicitly **not** the worker's self-report
  (`research-findings-2026-07-15.md` section 6.1: it is persuasion, and
  self-preference bias means same-family models are receptive to it).
- **Outputs.** A structured per-criterion verdict; rejection reasons appended to
  `learnings.md`. Nothing else. It cannot fix what it rejects, by design.
- **Autonomy default.** Not applicable, it has no trigger of its own.
- **Termination / escalation.** One verdict per attempt. Never re-rolled for a pass
  (that converts a gate into a slot machine, per section 6.4 of the findings doc).
  Repeated rejection parks the task, which escalates to the operator.
- **Failure mode.** Leniency drift. Mitigated by operator spot-checks (findings 6.5).

#### triager (NEW, the ant, D5/D6)

- **Trigger.** `scheduled` (a cron cadence) by default, evaluated against a cheap
  ready predicate: are there notes in this scope modified since the watermark?
  Optionally `event` (this is what makes a "watcher" a configuration, not a role,
  see 3.4).
- **Scope.** Any knowledge scope: `workshop`, `project:<slug>`, `app:<name>`,
  `agent:<slug>` (`knowledge.py:92-111`). One triager per scope, maximum (3.5).
- **Inputs.** Notes in its scope modified since the watermark; the current open task
  list for that scope (for linking and dedupe); its own definition body, which
  carries the textual routing rules.
- **Allowed outputs.** Exactly four decisions per note, a closed set:
  - `task`: file a Task with `source.kind=agent`, `source.ref=<job_id>`,
    `provenance=note:<scope>:<id>`.
  - `link`: attach the note to an existing Task (append `evidence`), used when the
    note restates or refines known work.
  - `escalate`: set `awaiting` on a Task with the question, or file into the parent
    scope, when the note is outside what this scope can decide.
  - `none`: a deliberate no-op, recorded with a reason.
  It may **not** write code, may **not** write notes, may **not** issue verdicts,
  and may **not** answer a question on the operator's behalf.
- **Autonomy default.** `scheduled`. Not `continuous`: a note is not urgent, and
  HumanLayer's finding ("one small refactor every morning beats 50") applies as much
  to triage as to making. Not `on-demand`: that would violate D1, since the operator
  would have to press something.
- **Termination / escalation.** One pass per activation, bounded by a per-pass note
  cap (recommend 20) and a per-pass task-filing cap (recommend 5). Notes past the
  cap wait for the next tick. The watermark advances **only over notes actually
  decided**, reusing the F16 commit-only-if-dispatched pattern already at
  `dispatch_triggers.sh:312-321`. Anything it cannot decide becomes `escalate`,
  never a guess.
- **Failure mode.** **Ticket spam.** Notes are cheap to write and tasks are
  expensive to work; a triager that files one task per note converts a scratchpad
  into a backlog and destroys the operator's willingness to leave notes, which
  would defeat D1 entirely. Four guards, in order of strength:
  1. `none` is the **default** outcome the persona is pushed toward, not the
     exception. A note is presumed to be a note.
  2. The per-pass filing cap makes the worst case bounded rather than unbounded.
  3. The two-stage dedupe already designed and shipped for task creation
     (embeddings for recall, local LLM for the decision, non-destructive merge,
     `unified-agent-model-2026-07-15.md` "Why dedup is embeddings + LLM", commit
     `833ee22`) is reused unchanged. A triager filing a task that already exists
     becomes a `link`.
  4. Tasks filed from notes carry `provenance=note:...`, so the operator can see and
     filter them as a class and, if it is spamming, turn one agent off.
  A second failure mode, **self-retrigger** (the triager writes a note, which
  changes mtime, which re-fires it), **cannot occur**: the role may not write notes.
  That is the output-authority axis paying for itself.

#### keeper (exists as `memory-keeper`, reclassified)

- **Trigger.** `scheduled`, slow cron. Shipped as `0 */6 * * *`
  (`agents/memory-keeper.md:9`).
- **Scope.** Any scope. The runner already iterates all active scopes when given
  none (`memory_keeper_run.sh:14-16`).
- **Inputs.** Closed Jobs since a watermark, chat-distilled facts, new `learnings.md`
  entries.
- **Outputs.** Itemized delta writes (ADD/UPDATE/RETIRE/NOOP) into scope
  `knowledge/`, `learnings.md`, and standing files; index-cap demotion; skill
  drafts as **operator-confirmed proposals**, never as silent installs.
- **Autonomy default.** `scheduled`. Already so.
- **Termination / escalation.** One pass, `max_iterations: 1`
  (`agents/memory-keeper.md:11`), `max_wall_s: 900` (`:13`).
- **Failure mode.** Context collapse and index bloat. Already mitigated per
  `orchestration-and-autonomy.md` section 6.2 (itemized deltas, supersession
  annotations, hard index cap).
- **Change proposed here:** none to its behavior. Only that `role: memory` is
  renamed to `role: keeper` under the enum, with a back-compat alias, because
  "memory" names a subject and every other role names an authority.

#### broker (NEW, name reserved here, designed elsewhere)

- **Trigger.** Demand (an access request from another agent or app).
- **Scope.** A resource scope (a notes folder, a Store).
- **Inputs.** The request plus the resource plus the folder's topic allowlist.
- **Outputs.** Information, released verbatim, filtered, or redacted (A8). It writes
  **nothing**. It is the only role whose authority is purely negative.
- **Autonomy default.** `off` as a standing loop; it exists only on demand.
- **Termination / escalation.** Fail-closed: if no sufficiently capable model is
  available, no access (A7). A denial is a `blocked` flag the operator reviews (B1).
- **Failure mode.** Over-release. Owned by the companion report.

**Why it is listed here despite being designed elsewhere:** it is the test of the
primitive. The five-field shape (trigger, scope, inputs, output authority, failure)
was derived from D5 and E1, and the broker, which arrived from an unrelated thread
(A4's "0 or multiple agents within notes... read only, write only"), fits it without
modification. A taxonomy that only fits the cases it was derived from is a
description, not a primitive. This is the E5 integration evidence.

### 3.4 What is deliberately left out, and why

The anti-cruft rule is binding, so every rejection is named.

**Collapsed into an existing role (same output authority):**

- **watcher / sentinel** (email-triager, plaid-watcher). Reads an external feed and
  files a task or a note. Output authority is **identical to a triager**. The
  difference is trigger kind (`event` vs `cron`) and input source (feed vs scope),
  both of which are already fields (`triggers.py:44`,
  `agents_registry.py:84` `KNOWN_SOURCES`). A watcher is a **triager with an event
  trigger**. Making it a role would be duplicating a field as a type.
- **janitor / gardener / librarian / curator.** All write memory and notes hygiene.
  That is `keeper`. Agent-ops writing enumerates these separately because they are
  named after human jobs; by authority they are one.
- **researcher.** Reads widely, writes a summary into the caller's context. This is
  a **native subagent inside a maker**, which is free, context-isolated, and already
  governed by `fanout` (`agents_registry.py:70`). Promoting it to a registry agent
  would take a free mechanism and add a Job, a scope, a definition file, and a
  handoff to it. `orchestration-and-autonomy.md` section 4.3 already decided this.
- **scribe / documenter.** Writing docs is a maker output (a file in the tree);
  writing notes is a keeper output. There is no third authority.

**Rejected outright:**

- **conductor / overseer as an agent.** See 4.4. The conductor is deterministic
  code. An LLM conductor adds cost, nondeterminism, and a lossy hop
  (Tran & Kiela) to a job that is a Python `for` loop over a plan list
  (`dispatch_triggers.sh:383-405`).
- **planner / architect / product-manager** (the MetaGPT SOP set). The maker plans
  in-context; decomposition is a task-graph act, filing sub-tasks with `depends_on`,
  not an agent-spawn act (`orchestration-and-autonomy.md` section 4.3). Three
  handoffs bought for nothing, against MAST's 41.8%.
- **critic / debater / voter ensembles.** Verdict shopping. `research-findings-2026-07-15.md`
  section 6.4 already decided: escalate, do not re-roll. Anthropic found a single
  judge with an explicit rubric plus human spot-checks more reliable than multi-judge
  complexity at their scale.
- **negotiator / peer roles.** `proposal.md` section 4 rejected peer discovery
  ("never as peers discovering each other"). Stigmergy is the only channel.
- **dispatcher-as-role, scheduler-as-role.** These are the shell.

Count: five roles, four collapses, five outright rejections. The delta over today is
**one role**.

### 3.5 Committed taxonomy vs free-form (the direct answer)

The brief asks what becomes committed and what stays free-form. The line is:
**enum where code branches, free-form where the model reads.**

**Committed (a closed enum in `agents_registry.py`):**

```
ROLES = ("maker", "reviewer", "triager", "keeper", "broker")
```

plus `None`, meaning "not dispatchable", which must remain valid because fourteen of
fifteen current definitions are in that state and because A4's "0 agents" case
requires that an agent's existence not imply activation.

Why closed, when the current comment says free-form (`agents_registry.py:65-68`):
because `dispatch_triggers.sh:329` already branches on the value, so it is already
an enum, just an unvalidated one. A free-form string that a dispatcher
pattern-matches without a `_coerce_role` guard (contrast `_coerce_depth` at `:75`)
means `role: memoy` silently disables the keeper with no error anywhere. Closing the
enum converts a silent behavior loss into a write-time rejection. This is not
defensive cruft, it is the same validation `depth` and `fanout` already get; the
inconsistency is the bug.

**Free-form (unchanged):**

- `purpose`, `capabilities`, the definition **body**. The body is where a triager's
  routing rules live as prose (B3's "textual rules"), where a keeper's consolidation
  policy lives, where a broker's topic allowlist lives.
- The **rules** themselves. This matters: the role fixes *what authority*, the body
  fixes *what judgment*. Two triagers with the same authority and completely
  different rules is the normal case and is what makes the set small.

The rule stated once: **the enum is short because it is what the shell must
understand; the body is unbounded because it is what the model must understand.**
This also satisfies the operator's open-enumeration preference (`operator-asks`
Part 3, "or other" appearing four times): extensibility lives in the body and in
per-role rules, not in role proliferation.

### 3.6 Are depth and fanout the right knobs?

**They are the right knobs for `maker` and noise everywhere else.** Verified: both
are declared on every agent (`agents_registry.py:161-162`), both are consumed only
by sweep execution (`:67-68`), and the one shipped non-maker agent sets
`max_iterations: 1` (`agents/memory-keeper.md:11`) because depth is meaningless for
a single bounded pass.

Recommendation: **keep both, scope them, add nothing.**

- Keep, because the migration cost is zero and the defaults are already inert
  (`standard`, `read-only`, emitted only when non-default, `:376-379`).
- Scope, by making them role-conditional in the UI (show on `maker`, hide
  elsewhere) and documenting them as maker-only in the `:63-68` comment block. Do
  not remove them from the schema; a role can gain a use for them later.
- Add nothing. The temptation is a per-role knob set (a triager filing cap, a keeper
  index cap). Resist it: those are **role-runner constants**, not operator knobs.
  The operator's control is the autonomy preset and the role's rules. Two knobs and
  a preset is the whole surface.

The one gap is real but does not want a new knob: a triager's per-pass caps need to
live somewhere. Put them in the runner as defaults, overridable via the existing
`limits` field (`agents_registry.py:86-89`), which already carries `max_iterations`
and `budget` and is already free-form-ish per agent. No new field.

---

## Part 4: the system for interaction

### 4.1 The shape, stated once

```
   operator / agents / apps
            |
            | write (no ceremony)
            v
    +-------------------------------+
    |         BLACKBOARD            |
    |  knowledge/<id>.md per scope  |   <- notes      (knowledge.py:92-111)
    |  .tasks.json per scope        |   <- work queue (tasks.py)
    |  jobs.jsonl                   |   <- history
    +-------------------------------+
            ^                 |
            | writes          | cheap Python ready-predicates (no LLM)
            |                 v
    +-------------------------------+
    |   CONTROL SHELL (deterministic)|  dispatch_triggers.sh
    |   role -> (ready?, runner)     |  a table, not an if/elif
    +-------------------------------+
            |
            | activate one role, one scope, one pass
            v
    +-------------------------------+
    |  ROLE RUNNERS (scripts/roles/) |  triager_run.sh, keeper_run.sh, ...
    |  each: bash <r>_run.sh <scope> |  contract per memory_keeper_run.sh:10-21
    +-------------------------------+
```

Three properties, each load-bearing:

1. **No agent addresses another agent.** Ever. A triager does not tell a maker
   anything; it writes a task, and a sweep later claims it. This is the one adopted
   ant mechanism (2.1) and the one adopted Cognition rule (2.3).
2. **The shell does not know what roles do.** It knows their ready predicate and
   their runner path. This is the adopted blackboard decoupling (2.2).
3. **Activation is deterministic and cheap.** No LLM runs to decide whether an LLM
   should run. This is the rejected half of the blackboard (2.2).

### 4.2 The role runner contract

Generalize `memory_keeper_run.sh:10-21`, which already has exactly the right shape,
into a convention every shell-dispatched role obeys:

```
bash scripts/roles/<role>_run.sh <scope-key>             # one pass, for real
bash scripts/roles/<role>_run.sh <scope-key> --dry-run   # preview, writes nothing
bash scripts/roles/<role>_run.sh                          # all active scopes
```

- Scope keys are `workshop | project:<slug> | agent:<slug> | app:<name>`, matching
  `knowledge.py:92-111` and `memory_keeper_run.sh:21` exactly. One vocabulary.
- Exit 0 on success, nonzero on hard failure.
- The runner opens a Job (`services/jobs.py`), so every pass is visible in
  `Jobs.tsx` with no new surface.
- **For real by default, `--dry-run` opt-in.** This is already the keeper's choice
  (`memory_keeper_run.sh:11-13`, "Runs FOR REAL (writes) by default, so the
  dispatcher gets a real consolidation with no extra flag") and it is right: a
  dispatcher that must pass a flag to get work done is a footgun.

And the shell-side table that replaces `dispatch_triggers.sh:323-345`:

```python
ROLE_RUNNERS = {
    "keeper":  ("scripts/roles/keeper_run.sh",  keeper_ready),
    "triager": ("scripts/roles/triager_run.sh", triager_ready),
    "broker":  None,   # demand-only, never shell-dispatched
}
```

where `*_ready(scope_key) -> bool` is plain Python: a mtime scan against the
watermark, a task-store query. Adding a role becomes: write a runner, write a
predicate, add a line. Not: edit an if/elif in a live cron script.

The launcher at `:383-405` collapses from three hardcoded branches to one lookup,
with `project` and `agent` retained as the loop-invoked `maker` paths (they call
`project_ralph.sh`, not a role runner, because the maker is invoked by the sweep,
not the shell, per 3.2).

### 4.3 Escalation, not action (the OTP rule)

The single interaction rule that makes a small role set safe:

> **A role that reaches the edge of its authority escalates. It never acts across
> the edge, and it never handles its own failure.**

Concretely, in ascending order:

1. **Within a task**: a maker that cannot proceed sets `awaiting` with the question
   (`tasks.py:25`). The operator answers; the next tick proceeds. Not an approval
   queue, an elicitation.
2. **Across authority**: a triager that concludes code must change does not change
   code. It files a task. **The ticket is the escalation.** This is why "can only
   propose a ticket" is the operator's own instinct and it is correct.
3. **Across scope**: a triager at `project:x` that finds something workshop-wide
   files into `workshop` scope, or sets `awaiting`. Scopes nest:
   `agent:<slug>` inside `project:<slug>` inside `workshop`. This is the supervision
   tree.
4. **On repeated failure**: `attempts` past `max_attempts` sets `parked`
   (`tasks.py:21-23,60-63`), which is exactly OTP restart intensity: stop trying,
   propagate upward, let the level above decide. The level above is the operator.
5. **On scope-wide failure**: the per-scope circuit breaker
   (`dispatch_triggers.sh:54,67`) is the supervisor crashing itself rather than
   letting a broken child burn budget forever.

Levels 4 and 5 exist. Levels 1 and 2 exist as substrate. Level 3 is the only new
mechanism, and it is one field: which scope a triager files into.

### 4.4 Conductors, overseers, and scopes (E4, answered)

**There is no conductor agent, and there should not be one.**

- The **conductor is the control shell**: `dispatch_triggers.sh`, deterministic
  Python and bash, no LLM, no reasoning. `orchestration-and-autonomy.md` section 4.1
  already stated this ("a sweep is a coordinator Job. It does not itself reason").
  This report only observes that it therefore is not an agent and should not be
  modeled as one.
- **`agents/workshop-overseer.md` is a ghost** (1.9): assistant runtime plus loop
  trigger means it dies at `dispatch_triggers.sh:323` then `:344` on every tick. It
  has an assigned Letta agent id (`:16`) and no path to execution. Recommend
  retiring it. Keeping a definition file that cannot run is worse than having none:
  it makes the roster lie.
- **The overseer function that genuinely needs judgment is a triager at a higher
  scope.** "Watch over the whole workshop, notice what is not being addressed, make
  tickets or escalate" is D5 verbatim, at `workshop` scope instead of
  `project:<slug>` scope. So the overseer is not a sixth role; it is the same role
  with a different scope key. That is the strongest possible evidence that the
  scope field is doing real work and the taxonomy does not need to grow.

**How the levels interact**, stated as a rule rather than a diagram: a scope's
triager may file into its own scope or escalate to its parent. It may not reach
downward into a child scope, because a child scope's triager owns that. Nothing
reaches sideways. The result is a tree with one direction of flow, which is the
property that makes OTP supervision analyzable and would make this analyzable too.

### 4.5 The note-triage ant, end to end (D1 + D5)

The concrete walkthrough the brief demands. Note-capture and the task store both
already exist, so this is mostly wiring, and D1's constraint ("writing the note must
be the WHOLE interaction, no extra ceremony") is the design's hardest requirement,
not its softest.

**Step 1: the note is written.** Any path: `/notes` (`frontend/src/pages/Notes.tsx`),
a chat that captures a note, an agent writing into its scope. It lands as
`<scope>/knowledge/<id>.md` plus the `MEMORY.md` index (`knowledge.py:92-111`).

> **No tag. No `@triage`. No special folder. No frontmatter flag. No command.**
> If the operator must do anything beyond writing the note, D1 has failed. Every
> mechanism below is therefore constrained to be invisible from the note-writing
> side.

**Step 2: detection, without an LLM.** The triager's ready predicate is a scan for
notes in the scope with `mtime` greater than the scope's triage watermark. The
watermark lives in `.runtime/trigger-state.json`, which already exists and is
already the shell's state store (`dispatch_triggers.sh:53`), under a key shaped like
the existing cron keys (`:315`): `triage:<scope-key>`. This is the stigmergy: the
note's existence and its mtime **are** the signal. Nothing is published, subscribed,
or enqueued.

**Step 3: activation.** The shell's cron check fires; the ready predicate says yes;
the shell launches `scripts/roles/triager_run.sh <scope-key>` detached, exactly as it
launches the keeper today (`:401-403`). Budget admission
(`usage_ledger.can_admit`, `:331`) and `MAX_AGENT_SWEEPS` (`:61`) apply unchanged.

**Step 4: the pass.** The runner gathers up to N notes (recommend 20, oldest first),
plus the scope's open tasks for linking, plus the agent body's rules. Rules run
first, LLM second (the Dependabot precedent, 2.6): a rule that matches decides
without a model call. For the residue, one `claude -p` call decides per note.

**Step 5: the decision.** Each note gets exactly one of `task` / `link` /
`escalate` / `none`. This closed set is what makes D5's "ensure all notes are
addressed" **mechanically checkable** rather than aspirational: addressed means has
a decision.

**Step 6: where addressed-ness is recorded.** This is the one genuinely new piece of
state, and the choice matters more than it looks. Three options:

- (a) a `triaged: true` field written into the note's frontmatter,
- (b) a sidecar `.runtime/triage-state.json` mapping note id to
  `{ts, decision, reason, task_ids}`,
- (c) inferred from a Task whose `provenance == note:<scope>:<id>`.

**Recommend (c) plus (b), and explicitly reject (a).**
- (c) is durable and free: `provenance` already exists as the stable dedupe key
  (`tasks.py:34`), and it makes the note-to-task link survive anything.
- (b) is required because (c) cannot express `none`. "I read this and deliberately
  did nothing, because it is a link dump" is real state with no task to hang on.
- (a) is rejected because **it mutates the operator's note**. A triager that writes
  into a note is a triager that changes its own trigger input, re-fires itself, and
  puts machine bookkeeping into a human's file. It also contradicts the role's
  output authority (3.3: a triager may not write notes), and that contradiction is
  the tell. D1's no-ceremony rule cuts both ways: the note stays clean on the way in
  **and** on the way out.

**Step 7: visibility.** The task appears in `/work` (`frontend/src/pages/Work.tsx`)
carrying `source.kind=agent` and `provenance=note:...`. `Notes.tsx` renders a triage
state chip per note from the sidecar. The operator can see, at a glance, which notes
were picked up and what came of them, which is the feedback loop that makes leaving
notes feel worth doing.

**Step 8: the watermark advances**, over decided notes only, using the F16
commit-only-if-dispatched pattern already written at `dispatch_triggers.sh:312-321`
and `:374-379`. A pass that crashes leaves the watermark where it was and retries
next tick. A note that hit the per-pass cap stays due.

**Loop safety, by construction, not by prompt:** the triager cannot write notes, so
it cannot change its own trigger input, so it cannot re-fire itself. There is no
instruction to disobey.

### 4.6 How this maps onto the existing knobs (the direct answer)

| Existing thing | Verdict |
| --- | --- |
| `role` free-form (`agents_registry.py:65-68`) | **Becomes a closed enum.** It already is one, at `dispatch_triggers.sh:329`, just unvalidated. `None` stays valid. |
| `depth` (`agents_registry.py:69,161`) | **Keep, scope to `maker`.** Meaningless on other roles; hide in UI, document as maker-only. |
| `fanout` (`agents_registry.py:70,162`) | **Keep, scope to `maker`.** Same reasoning. A fanning-out triager is a bug. |
| Autonomy presets (`triggers.py:235`) | **Unchanged.** Roles get defaults: `triager` and `keeper` default `scheduled`, `broker` `off`, `maker` inherits the scope. |
| `limits` (`agents_registry.py:86-89`) | **Reused** for per-role runner caps. No new field. |
| Task schema (`tasks.py:1-44`) | **Unchanged.** `provenance`, `source`, `discovered_from`, `awaiting` cover everything. |
| `assignee` (`tasks.py:18`) | **Unchanged.** Role types the agent; assignment stays `agent:<slug>`. The original `:67` comment is right about this and stays right. |

The only schema change in this entire report is a validated enum on an existing
field, plus one sidecar JSON file. That is the anti-cruft test passing.

---

## Part 5: templates and the default set (D2-D4)

### 5.1 Where templates live

Constraint: `agents/` is gitignored (`.gitignore:83`) and correctly so, since
instances carry runtime state (`agents/workshop-overseer.md:16`). Precedent:
`projects/_templates/` is tracked (CLAUDE.md), `shared/skills/` is tracked.

**Proposal:** `shared/agent-templates/<slug>.md`, tracked, identical in format to an
agent definition (frontmatter plus body) so a template is readable, diffable, and
testable as the thing it produces. `shared/` is chosen over `agents/_templates/`
because `agents/` is ignored wholesale by a directory-level rule and carving an
exception into `.gitignore` is a trap for the next person.

Template-only frontmatter, three fields:

```yaml
template: note-triager        # the template's own slug
template_version: 1
description: 'Reviews new and updated notes in a scope, files or links tickets, escalates the rest'
```

### 5.2 Template groups (D4)

D4 is explicit that groups exist and that they carry "other settings", not just a
member list. So a group is not an array.

`shared/agent-templates/groups/<name>.json`:

```jsonc
{
  "name": "tended",
  "description": "A project that keeps its own notes and memory in order",
  "members": [
    { "template": "note-triager", "overrides": { "triggers": [{ "kind": "cron", "cron_expression": "0 9 * * *" }] } },
    { "template": "memory-keeper", "overrides": {} }
  ],
  "scope_defaults": { "autonomy": "on-demand", "depth": "standard" }
}
```

Named "template group", the operator's own words, deliberately not "crew" or "team"
or "squad". `proposal.md` section 4's no-new-noun rule applies, and CrewAI's "crew"
carries framework baggage we explicitly rejected (2.5).

### 5.3 Adopt, override, edit (D3)

A project adopts a group. The platform **instantiates copies** into `agents/`, each
with `project_slug: <slug>` in frontmatter (which `agents_registry` already supports
for project-scope definitions per the brief) plus a provenance line:

```yaml
template: note-triager@1
```

**Fork, not link.** The copy is independent. Justification: D3 says templates are
"configured **or also edited** for a project". An edited link is a template mutation
that silently leaks into every other project that adopted it, which is the worst
possible surprise. Forking makes the edit local and obvious.

**The cost, stated honestly:** template improvements do not propagate to instances.
The `template: <slug>@<version>` line makes a future "your instance is based on v1,
v3 is out" nudge possible. **Do not build sync now.** A propagation engine for a
system with one operator and maybe six agents is exactly the cruft the rules
forbid; the operator can re-adopt or hand-edit, and both take a minute.

Adoption is idempotent: adopting a group twice does not create two triagers. This is
enforced by the constraint in 5.4.

### 5.4 One shell-dispatched agent per (scope, role)

A hard constraint, validated at adopt time and on registry write: at most one
`triager` per scope, one `keeper` per scope. Two triagers on one scope would both
read the same notes past the same watermark and both file the same tasks, and the
dedupe path would then be doing damage control for a configuration error. This is
not a multi-tenant guardrail; it is a uniqueness constraint on a key, the same class
of thing as a slug being unique.

Makers are exempt: multiple makers on a scope is the concurrency case, already
governed by `touches` disjointness and `claim_token`
(`orchestration-and-autonomy.md` section 4.3).

### 5.5 The templates to ship

Only shell-dispatched roles are templatable (3.2), so three:

| Template | Role | Trigger | Purpose |
| --- | --- | --- | --- |
| `note-triager` | `triager` | `cron` daily | The ant (D5). Reviews new/updated notes, files or links tickets, escalates the rest. |
| `inbox-watcher` | `triager` | `event` | Demonstrates that a watcher is a **configuration**, not a role (3.4). Reads a feed, files tasks. Generalizes today's `email-triager` and `plaid-watcher`. |
| `memory-keeper` | `keeper` | `cron` 6-hourly | Already exists as an instance (`agents/memory-keeper.md`); promote it to a template so a project can adopt its own. |

No `maker` template (invoked by the loop, not the shell). No `reviewer` template
(same). No `broker` template until the companion report lands.

### 5.6 The default set for a brand-new project

**Recommendation: zero agents.**

A new project gets its autonomy preset (`on-demand`, `triggers.py:222-227`) and the
project loop, and the project loop **is not an agent**: it is `project_ralph.sh`
sweeping the scope. Nothing needs a definition file for a project to be worked.

Reasons:

1. **Anti-cruft.** Three agent definitions per new project, most of which the
   operator never looks at, most of which triage an empty notes folder, is a machine
   for generating noise and cost.
2. **A4 already blessed it.** "0 or multiple agents" starts the count at zero, and
   `operator-asks` Part 3 reads that as a real design signal: the resource must be
   fully functional with no resident agent.
3. **A triager over an empty scope is worse than nothing.** It is a scheduled job
   that wakes, scans, finds nothing, and logs. Its ready predicate saves the LLM
   call but not the existence.
4. **The upgrade is one click.** Adopting `tended` when a project starts
   accumulating notes takes a second and happens exactly when it is warranted.

**Two groups shipped, not a catalog:**

- `minimal` (the default): no agents.
- `tended`: `note-triager` plus `memory-keeper`, for a project the operator wants
  running warm.

If a third group is ever wanted, it will be obvious which one. Shipping five
speculative groups would be inventing operator needs, which is the thing the no-cruft
rule is actually about.

---

## Part 6: sequenced phases with file-level touch points

Global rules per the brief and `implementation-plan.md`: `AUTONOMY_DISABLED` bracket
around any edit to `cron_agent.sh` / `ralph.sh` / `project_ralph.sh` /
`dispatch_triggers.sh` (the crontab fires every 20 minutes against the live tree);
no committed stubs; `tsc --noEmit` plus `scripts/check_design.sh` on frontend steps;
the bridge agent smoke check after any backend restart; backend restart only via
`scripts/restart_backend.sh`; no em-dash, no emoji.

Legend: ADD (new), MOD (modify), DEL (delete plus call sites).

### Phase R0: the role enum [backend only, no behavior change]

**MOD**
- `backend/services/agents_registry.py`: add `ROLES = ("maker","reviewer","triager",
  "keeper","broker")` beside `DEPTHS`/`FANOUTS` (`:69-72`); add `_coerce_role`
  mirroring `_coerce_depth` (`:75-80`) but **rejecting** rather than snapping, since
  a silently-corrected role is the bug we are fixing; apply at parse (`:160`),
  `create` (`:393-407`), and `update` (`:417-470`). Accept `role: memory` as a
  back-compat alias for `keeper`. Rewrite the now-false comment at `:63-68`.
- `backend/routes/agents.py`: surface the enum for the UI; 400 on an unknown role.

**Verify:** `agents/memory-keeper.md` (`role: memory`) still parses and still
dispatches identically; `role: memoy` is rejected at write with a useful message;
`role: null` (fourteen of fifteen current defs) is unaffected; existing frontmatter
round-trips byte-identically where the role is unchanged. Backend restart plus
the bridge agent smoke check.

**Why first:** it is the only step with zero behavior change, and it makes every
later step's failure mode loud instead of silent.

### Phase R1: the role runner table [live cron, AUTONOMY_DISABLED bracket]

**ADD**
- `scripts/roles/` directory.
- `scripts/roles/keeper_run.sh`: a thin alias to the existing
  `scripts/memory_keeper_run.sh`, preserving its documented contract verbatim
  (`memory_keeper_run.sh:10-21`) because that file states "another agent depends on
  this EXACTLY".

**MOD**
- `scripts/dispatch_triggers.sh`: replace the `role == "memory"` special case
  (`:329-342`) and the `skip-assistant` fallthrough (`:344`) with a
  `ROLE_RUNNERS` table plus per-role ready predicates; replace the launcher if/elif
  (`:392-405`) with a table lookup, retaining `project` and `agent` as the
  loop-invoked maker paths. Unknown or unmapped role logs `skip-role` rather than
  `skip-assistant`, so the log says the true reason.

**DEL**
- `agents/workshop-overseer.md`: retire the ghost (1.9), or convert it to a
  `workshop`-scope `note-triager` in R2. Operator decision, Q3.

**Verify:** with no other change, the memory-keeper dispatches exactly as before
(same plan triple, same command line, same log); an agent with an unknown role logs
`skip-role`; an agent with `role: null` is skipped silently as today;
`MAX_AGENT_SWEEPS` (`:61`) and the budget gate (`:331`) still bound activation. Run
one manual tick with autonomy disabled, diff the dispatch log against a
pre-change tick.

**Why second:** this is the unblocking step. Nothing else in this report can be
built while `:329` is a hardcoded string compare.

### Phase R2: the note-triager [the ant, D1/D5]

**ADD**
- `backend/services/note_triage.py`: the since-watermark scan over
  `knowledge.scope_dir(scope_key)` (`knowledge.py:92-111`); the open-task fetch for
  linking; the four-decision apply step (`task` / `link` / `escalate` / `none`)
  writing through the existing task write path with `provenance=note:<scope>:<id>`
  and `source.kind=agent`; the sidecar decision record. Reuses the shipped
  embeddings-plus-LLM dedupe (`unified-agent-model-2026-07-15.md`, commit `833ee22`)
  unchanged, no new dedupe.
- `scripts/roles/triager_run.sh`: the runner, matching the contract in 4.2, opening
  a Job, honoring the per-pass note cap and filing cap, advancing the watermark over
  decided notes only per the F16 pattern (`dispatch_triggers.sh:312-321`).
- `shared/agent-templates/note-triager.md`: the template, body carrying the default
  textual rules and the `none`-is-default posture.
- `.runtime/triage-state.json`: the sidecar (created on demand, gitignored by
  `.runtime` convention).

**MOD**
- `scripts/dispatch_triggers.sh`: register `triager` in the R1 table with its ready
  predicate. One line plus a function.

**Verify:** write a note into a project knowledge scope; run one tick; exactly one
task appears with `provenance=note:<scope>:<id>` and `source.kind=agent`. Run a
second tick with no change: no second task, no re-read (watermark held). Edit the
note: it is re-triaged and `link`s to the existing task rather than filing a
duplicate. Write a note that is a bare URL: decision `none` with a reason, recorded
in the sidecar, no task. Kill the runner mid-pass: the watermark did not advance and
the next tick redoes it. Confirm the note file on disk is **byte-identical** before
and after triage (the (a)-rejection in 4.5 is a testable property, not a preference).

### Phase R3: templates and groups [D2-D4]

**ADD**
- `shared/agent-templates/` with `note-triager.md`, `inbox-watcher.md`,
  `memory-keeper.md`; `shared/agent-templates/groups/minimal.json` and
  `groups/tended.json`.
- `backend/services/agent_templates.py`: list templates, list groups, instantiate
  (copy plus `project_slug` plus `template: <slug>@<version>` provenance), enforce
  the one-per-(scope, role) constraint from 5.4.

**MOD**
- `backend/routes/agents.py`: `GET /agents/templates`, `GET /agents/template-groups`,
  `POST /agents/adopt` (body: template or group, plus target scope).
- `frontend/src/pages/Agents.tsx`: an adopt affordance listing templates and groups,
  edit-mode plus Save per the DS rule, all four list states.

**Verify:** adopting `tended` into a project creates two definitions in `agents/`
with `project_slug` set and correct provenance lines; adopting twice is idempotent
and does not create a second triager; an adopted triager dispatches on the next tick
and triages that project's notes only; editing an adopted instance does not touch
`shared/agent-templates/`. `tsc --noEmit`, `check_design.sh`, width sweep at
1440/1024/768/390.

### Phase R4: UI and condition handling [F4]

**MOD**
- `frontend/src/pages/Notes.tsx`: a per-note triage `StatusPill` sourced from the
  sidecar (pending / filed / linked / escalated / none), plus a link from a filed
  note to its task. Four list states; lucide icons only.
- `frontend/src/pages/Work.tsx`: filter by `source.kind` and a "from notes" filter on
  the `provenance` prefix, so note-spawned tasks are visible and dismissible as a
  class (the fourth ticket-spam guard, 3.3).
- `frontend/src/pages/AgentDetail.tsx`: role badge; role-conditional `depth` and
  `fanout` (shown for `maker`, hidden otherwise, per 3.6); last pass and decision
  counts for shell-dispatched roles.
- `frontend/src/components/AutonomyPanel.tsx`: unchanged mechanically; the preset
  select now shows the role's default.

**Verify:** every list ships loading/empty/error/overflow; a triager pass shows in
`Jobs.tsx` with no new surface; a failed pass shows as a failed Job and the note
stays `pending`; widths sweep; `tsc --noEmit` plus `check_design.sh`.

### Parallelization

R0 and R3's template **files** are parallel-safe (different files, no shared
symbols). R1 is sequential after R0 and is the bottleneck for R2. R2 is sequential
after R1. R4 is sequential after R2 and R3. R1 and R2 both touch
`dispatch_triggers.sh` and must not run concurrently against the live cron.

---

## Part 7: UI and condition handling

### 7.1 What the operator sees

- **Notes** (`Notes.tsx`): one chip per note. The whole feedback loop for D1 is this
  chip: leave a note, come back, see that something happened to it. Without it, "have
  it just be picked up" is unfalsifiable from the operator's chair.
- **Work** (`Work.tsx`): note-spawned tasks carry a visible provenance and are
  filterable as a group.
- **Agents** (`Agents.tsx`, `AgentDetail.tsx`): role badge, last pass, decisions
  made, adopt-from-template.
- **Jobs** (`Jobs.tsx`, `JobDetail.tsx`): every role pass is a Job already. No new
  surface is proposed, which is the point.
- **Home** (`Home.tsx`): the `AttentionStrip` already surfaces `awaiting`
  (`orchestration-and-autonomy.md` section 10.3), which is where an `escalate`
  decision lands. Nothing new.

Five surfaces touched, zero created.

### 7.2 Condition handling

| Condition | Handling | Mechanism |
| --- | --- | --- |
| A role's pass **fails** | It is a Job; it shows as failed. The watermark did not advance, so the next tick retries. | F16 pattern, `dispatch_triggers.sh:312-321` |
| A role **fails repeatedly** | Per-scope circuit breaker trips and backs the scope off. | `.runtime/circuit-breaker.json`, `:54,67` |
| A role **loops** | Cannot, for `triager`: it may not write notes, so it cannot alter its own trigger input. For `maker`: existing in-run thrash detection. | output authority (3.3); `research-findings-2026-07-15.md` section 1 |
| Two roles want the **same task** | The claim fence rejects the loser. | `claim_token`, `tasks.py:28` |
| Two triagers on **one scope** | Forbidden at adopt and at registry write. | 5.4 |
| A triager **spams tickets** | `none` is the default posture, per-pass filing cap, dedupe-to-`link`, and the operator can filter and kill the agent. | 3.3 |
| A triager is **wrong** about a note | Its output is a ticket, not an edit. Cost of a wrong ticket is one delete. | output authority (3.1) |
| A role is **unknown** to the shell | `skip-role` in the log, not silent. | R1 |
| A role's **budget** is exhausted | `skip-budget`, already implemented. | `usage_ledger.can_admit`, `:331` |
| An agent has **no role** | Not dispatchable, skipped silently. Valid state (fourteen of fifteen today). | 3.5 |

The pattern worth noticing: most conditions are handled by mechanisms that already
exist, and the two that are not (loop safety, triager uniqueness) are handled
structurally rather than by a runtime check. That is the dividend of choosing output
authority as the axis instead of job title.

---

## Part 8: coverage of the asks

| Ask | Where |
| --- | --- |
| **D1** note left in general or project, just picked up | 4.5, steps 1-2 and 6; the (a)-rejection; the R2 byte-identical test |
| **D2** simple basic general worker job-template agents | Part 5, 5.5 |
| **D3** configurable and editable per project | 5.3 (fork-not-link, with the cost stated) |
| **D4** a default set, plus template groups | 5.2, 5.6 (`minimal` and `tended`) |
| **D5** reviews notes, ensures addressed, tickets or escalates | 3.3 triager; 4.5 end to end; addressed-ness made checkable in step 5 |
| **D6** "like ants having little jobs" | 2.1: metaphor rejected with reasons, stigmergy adopted, the preference for small single-purpose workers honored |
| **E1** what are the other primitive roles | 3.2, 3.3 |
| **E2** deep research | Part 2, cited |
| **E3** distill to a core set and a system for interaction | Part 3 (five roles, 3.4 rejections) and Part 4 |
| **E4** conductor/overseer interaction, different scopes | 4.4 (no conductor role; overseer is a workshop-scope triager), 4.3 (scope nesting) |
| **E5** integrate with the broader design | 4.6, Part 6; the broker (3.3) as the cross-thread integration test |

---

## Part 9: open questions, each with a recommended default

1. **What is the default agent set for a new project?**
   **Recommend: `minimal`, zero agents.** The project loop is not an agent, A4 blessed
   zero, and a triager over an empty notes folder is a scheduled no-op. `tended` is
   one click away when a project earns it. (5.6)

2. **Closed role enum, or keep it free-form?**
   **Recommend: closed**, with `None` valid. It is already a de facto enum at
   `dispatch_triggers.sh:329`, just without the `_coerce_role` guard its siblings
   `depth` and `fanout` get (`agents_registry.py:75-80`). Closing it converts a silent
   behavior loss into a write-time error. Rules and judgment stay free-form in the
   body. (3.5)

3. **What happens to `agents/workshop-overseer.md`?**
   **Recommend: retire it.** It has never run through the dispatch path (`:323`
   then `:344`) and a roster entry that cannot execute makes the roster lie. If the
   overseer *function* is wanted, it is a `note-triager` at `workshop` scope, which is
   the same role with a different scope key. (1.9, 4.4)

4. **Are `depth` and `fanout` the right knobs?**
   **Recommend: keep both, scope both to `maker`, add none.** They are correct for the
   one role that consumes them and inert noise on the rest. Fix by hiding them in the
   UI and correcting the comment, not by migrating the schema. Per-role caps live in
   the runner and in the existing `limits` field. (3.6)

5. **Where does note triage state live?**
   **Recommend: task `provenance` plus a `.runtime` sidecar; never in the note file.**
   Provenance is durable and already exists; the sidecar carries `none`, which
   provenance cannot express; writing into the note mutates the operator's file,
   violates the triager's output authority, and would make the triager re-trigger
   itself. (4.5 step 6)

6. **Templates: fork or link on adopt?**
   **Recommend: fork**, with `template: <slug>@<version>` recorded and **no sync
   engine**. D3 demands per-project editing, and an edited link leaks. The cost
   (improvements do not propagate) is real and accepted; re-adopting takes a minute
   and a propagation engine for six agents is cruft. (5.3)

7. **Does `broker` belong in the enum before its report lands?**
   **Recommend: reserve the name in the enum, ship no runner.** It is demand-only, so
   the shell never dispatches it, so reserving it costs one string and prevents the
   companion report from having to re-open `agents_registry.py`. (3.3)

8. **Rename `role: memory` to `role: keeper`?**
   **Recommend: yes, with a back-compat alias.** Every other role name states an
   authority; "memory" states a subject. One alias line, no migration. (3.3)

9. **"denounced"** in the operator's D5 sentence ("review new or updated notes,
   denounced, and ensure all notes are addressed") remains an unresolved dictation
   artifact, as flagged in `operator-asks-2026-07-16.md` Part 3.
   **Recommend: read it as "de-duped"** and treat it as satisfied by the `link`
   decision and the existing embeddings-plus-LLM dedupe path (4.5 step 5, 3.3 guard
   3), which is the reading that requires building nothing extra. Worth one word of
   confirmation from the operator, since the alternative reading ("denoised") would
   also be satisfied by the `none` decision, and both are already covered.

---

## References

**Research (URLs).**
- Ant task allocation and response thresholds:
  [Task Allocation in Ant Colonies](https://link.springer.com/chapter/10.1007/978-3-662-45174-8_4);
  [response-threshold verification, PMC6586672](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6586672/);
  [stochastic spatial model of division of labour, arXiv 1601.01023](https://arxiv.org/pdf/1601.01023).
- Stigmergy limits: [Testing the limits of pheromone stigmergy in high-density robot
  swarms, Royal Society Open Science](https://royalsocietypublishing.org/doi/10.1098/rsos.190225).
- Blackboard: [LLM-Based Multi-Agent Blackboard System for Information Discovery in
  Data Science, arXiv 2510.01285](https://arxiv.org/abs/2510.01285);
  [Exploring Advanced LLM Multi-Agent Systems Based on Blackboard Architecture,
  arXiv 2507.01701](https://arxiv.org/abs/2507.01701);
  [The Blackboard Architecture: Solving the Agent 'Phone Game'](https://rajatpandit.com/agentic-ai/the-blackboard-architecture/).
- Patterns and failure: [Anthropic, Building Effective
  Agents](https://www.anthropic.com/engineering/building-effective-agents);
  [Anthropic, How we built our multi-agent research
  system](https://www.anthropic.com/engineering/multi-agent-research-system);
  [MAST, arXiv 2503.13657](https://arxiv.org/html/2503.13657v1);
  [Tran & Kiela, arXiv 2604.02460](https://arxiv.org/abs/2604.02460).
- Role vocabularies: [MetaGPT, arXiv 2308.00352](https://arxiv.org/html/2308.00352v6);
  [CrewAI vs LangGraph vs AutoGen](https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen).
- Triage precedent: [gitlab-triage](https://about.gitlab.com/blog/automating-agile-workflows-with-the-gitlab-triage-gem/);
  [Dependabot auto-triage rules](https://github.blog/changelog/2024-06-26-dependabot-auto-triage-rules-are-generally-available/);
  [Reducing Dependabot noise](https://nesbitt.io/2026/01/10/16-best-practices-for-reducing-dependabot-noise.html).
- Supervision: [Erlang Supervisor Behaviour](https://www.erlang.org/doc/system/sup_princ.html);
  [Adopting Erlang, supervision trees](https://adoptingerlang.org/docs/development/supervision_trees/).
- Curator/playbook: [mem0, context engineering for AI agents](https://mem0.ai/blog/context-engineering-ai-agents-guide).

**Code read on 2026-07-16.**
`backend/services/agents_registry.py` (:63-89, :160-162, :361-470);
`backend/services/triggers.py` (:14-44, :185-253);
`backend/services/tasks.py` (:1-70);
`backend/services/knowledge.py` (:88-115);
`backend/routes/notes_all.py` (:33, :106-137);
`scripts/dispatch_triggers.sh` (:29-67, :262-405);
`scripts/memory_keeper_run.sh` (:1-40);
`agents/memory-keeper.md`, `agents/workshop-overseer.md`, `agents/email-triager.md`;
`.gitignore:83`; `projects/_templates/`; `shared/skills/`; `CLAUDE.md`.

**Prior art reconciled with (not re-derived).**
`docs/platform-redesign/orchestration-and-autonomy.md` (O1-O7, sections 3, 4.1, 4.3,
6.2, 8, 10.3);
`docs/platform-redesign/unified-agent-model-2026-07-15.md` (Agent primitive, ants-not-
a-conductor, two-stage dedup, no framework);
`docs/platform-redesign/research-findings-2026-07-15.md` (sections 1, 2, 6);
`docs/platform-redesign/notes-system-plan-2026-07-15.md` (house style, N0-N3 shipped);
`docs/platform-redesign/chat-tickets-orchestrator-plan-2026-07-15.md` (Task=ticket,
Job=attempt);
`docs/decisions/0002-notes-substrate-is-knowledge.md` (accepted; not contradicted).
