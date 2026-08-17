# Primitive agent roles, triage loop, and template catalog (2026-07-18)

> **STATUS: DESIGN, PARTIALLY BUILT.** This doc distills the research and
> synthesis from `agent-roles-research-2026-07-16.md` and
> `synthesis-agent-protocols-2026-07-16.md` into the actionable reference for
> implementation phases R0-R4.
> All file:line references point to the live tree as of 2026-07-16 unless noted.
>
> **Correction, 2026-08-17:** the line that used to be here ("No code has been
> written") was wrong and actively misleading. `role` is a live Postgres `CHECK`
> constraint (`backend/db/models.py:606`) and the write-set map this doc
> proposes is implemented at `backend/services/agent_authority.py:59`
> (`_ROLE_WRITES`). The rest of R0-R4 (triage loop, template catalog) has not
> been verified built or unbuilt by this pass. See
> `coherence-overhaul-2026-08-16.md` R1.

Owns the output deliverable for operator asks D1-D6 and E1-E5 from
`operator-asks-2026-07-16.md`. Companion to
`resource-access-protocol-2026-07-16.md` (A1-A12) and
`scopedness-and-escalation-2026-07-16.md` (B1-B4, C1-C4).

---

## 1. The core claim (read this first)

**A role is not a job title. A role is the set of writes an agent is permitted to
make.**

Everything else about an agent (what it reads, how it judges, when it activates)
is expressible through existing fields: `purpose`, definition body, `triggers`,
`project_slug`, `limits`. The role enum is short because the shell branches on it;
the body is unbounded because the model reads it.

This axis collapses what would otherwise be a dozen plausible roles into five.
The research (Part 2 of `agent-roles-research`) checked ant swarms, blackboard
systems, MetaGPT, CrewAI, LangGraph, Erlang supervision trees, and GitLab triage
automation, and found that every proposed role variant differing only in what it
reads or when it activates folds into an existing role plus a field. The five are
the residue after that reduction.

---

## 2. The five primitive roles

| Role | May write | Must not write | Current status |
| --- | --- | --- | --- |
| `maker` | working tree, commits, task transitions on its own claim | memory, other scopes' trees | exists (`ralph.sh`, `project_ralph.sh`) |
| `reviewer` | a verdict plus rejection reasons in `learnings.md` | the tree, tasks | exists (`reviewer.sh`, work-reviewer subagent) |
| `triager` | tasks (file, link, escalate) and a triage decision record | the tree, notes, verdicts | new (this design) |
| `keeper` | knowledge notes, `learnings.md`, standing files, skill proposals | the tree, tasks | exists as `memory-keeper` |
| `broker` | nothing (releases information only) | everything | reserved; designed in `resource-access-protocol-2026-07-16.md` |

`None` (unset) remains valid. Fourteen of fifteen current agent definitions have no
role, meaning they are not shell-dispatchable. That is a correct and deliberate state:
an agent's existence does not imply activation.

**The role enum is closed in code, free-form in the body.**

```python
ROLES = ("maker", "reviewer", "triager", "keeper", "broker")
```

`role: memory` (the current `memory-keeper` value) is a back-compat alias for
`keeper`. A `_coerce_role` validator, mirroring `_coerce_depth`
(`agents_registry.py:75-80`), rejects unknown values at write time rather than
silently disabling the agent.

### What is deliberately excluded

The following were considered and rejected. Each rejection is specific:

- **watcher/sentinel**: output authority is identical to `triager`. A watcher is a
  triager with an `event` trigger instead of `cron`. Making it a separate role
  would duplicate a field as a type.
- **janitor/gardener/librarian/curator**: all write memory and notes hygiene. That
  is `keeper`. They differ in name, not authority.
- **researcher**: a native read-only subagent inside a `maker`, already governed by
  `fanout` (`agents_registry.py:70`). Promoting it adds a Job, a scope, a definition
  file, and a handoff to buy nothing.
- **scribe/documenter**: writing docs is a `maker` output; writing notes is a `keeper`
  output. There is no third authority.
- **conductor/overseer as a role**: the conductor is deterministic code, roughly
  twenty lines of Python. An LLM conductor adds cost, nondeterminism, and a lossy
  hop to a job that is a `for` loop over a plan list. See section 4.
- **planner/architect**: the maker plans in-context; decomposition files sub-tasks
  with `depends_on`, not an agent-spawn. Three handoffs for nothing, against MAST's
  41.8% specification-failure rate.
- **critic/debater/voter ensembles**: verdict re-rolling, explicitly rejected in
  `research-findings-2026-07-15.md` section 6.4. Escalate; do not re-roll.

---

## 3. The architecture: blackboard with a declarative control shell

The honest name for what the system already is:

- **Blackboard**: the task store plus knowledge scopes. Every note, every task, every
  job is shared state visible to any authorized reader.
- **Knowledge sources**: the role runners, which do not know about each other.
- **Control shell**: `scripts/dispatch_triggers.sh`, deterministic Python and bash.

From blackboard research (arXiv 2510.01285), we keep the **decoupling**: the shell
must not know what a role does. Today it does, hardcoded at
`dispatch_triggers.sh:329` (`role == "memory"`). That single line is why five
configured agents never run and why the note-triage ant cannot be built today.

From blackboard research, we **reject** opportunistic self-selection. "Volunteering"
would mean an LLM call per agent per tick to answer "is there anything for me?",
almost always "no". Instead, each shell-dispatched role declares a cheap non-LLM
**ready predicate** (an mtime scan, a task query). The LLM runs only once the
predicate says there is work.

From ant colony research, we keep **one mechanism only: stigmergy**. Agents
coordinate by reading and writing shared state, never by messaging each other. A
note's existence is the signal that it needs triage. A task's `todo` status is the
signal that it needs a maker. No subscription, no queue, no inbox, no handoff
protocol. Response-threshold models, pheromone decay, and probabilistic recruitment
are rejected because they are population-scale statistics requiring thousands of
agents. This system has under ten.

### The shape

```
   operator / agents / apps
            |
            | write (no ceremony)
            v
   +-------------------------------+
   |         BLACKBOARD             |
   |  knowledge/<id>.md per scope  |  notes      (knowledge.py:92-111)
   |  .tasks.json per scope        |  work queue (tasks.py)
   |  jobs.jsonl                   |  history
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
   |  ROLE RUNNERS (scripts/roles/) |
   |  bash <role>_run.sh <scope>    |
   +-------------------------------+
```

Three invariants, each load-bearing:

1. **No agent addresses another agent.** A triager does not tell a maker anything; it
   writes a task, and a sweep later claims it. Stigmergy: the shared state is the
   only channel.
2. **The shell does not know what roles do.** It knows their ready predicate and their
   runner path. Adding a role is adding a file and a line; it does not require editing
   a live cron script.
3. **Activation is deterministic and cheap.** No LLM runs to decide whether an LLM
   should run.

### The role runner contract

Generalized from `memory_keeper_run.sh:10-21`, which already has the right shape.
Every shell-dispatched role obeys:

```
bash scripts/roles/<role>_run.sh <scope-key>            # one pass, writes
bash scripts/roles/<role>_run.sh <scope-key> --dry-run  # preview only
bash scripts/roles/<role>_run.sh                         # all active scopes
```

Scope keys: `workshop | project:<slug> | agent:<slug> | app:<name>`, matching
`knowledge.py:92-111` exactly. One vocabulary.

The dispatch table that replaces `dispatch_triggers.sh:323-345`:

```python
ROLE_RUNNERS = {
    "keeper":  ("scripts/roles/keeper_run.sh",  keeper_ready),
    "triager": ("scripts/roles/triager_run.sh", triager_ready),
    "broker":  None,   # demand-only, never shell-dispatched
}
```

`*_ready(scope_key) -> bool` is plain Python: an mtime scan against the watermark
for triager, a task-store query for keeper. Adding a role: write a runner, write a
predicate, add a line.

---

## 4. Cross-scope interaction: supervision tree, not a conductor

### No conductor role

The conductor is the control shell. `dispatch_triggers.sh` is deterministic code
with no LLM. `agents/workshop-overseer.md` is a ghost: it declares `runtime:
assistant` and a `loop` trigger, which at `dispatch_triggers.sh:323` falls to
`role != "memory"` at `:329` and prints `skip-assistant` at `:344` on every tick.
It has a `letta_agent_id` and has never executed through this path. Recommend
retiring it.

The overseer function (watch the whole workshop, notice what is unaddressed, file
tickets or escalate) is a `triager` at `workshop` scope. It is the same role with a
different scope key. That is the strongest evidence the taxonomy does not need a
sixth role.

### Scope nesting as supervision tree (OTP-style)

Scopes nest: `agent:<slug>` inside `project:<slug>` inside `workshop`. Escalation
walks up. Nothing reaches down into a child scope (that scope's own triager owns
it). Nothing reaches sideways. One direction of flow.

The five escalation levels, in order:

1. **Within a task**: a maker that cannot proceed sets `awaiting` with the question
   (`tasks.py:25`). The operator answers; the next tick proceeds.
2. **Across authority**: a triager that concludes code must change does not change
   code. It files a task. The ticket is the escalation.
3. **Across scope**: a triager at `project:x` that finds something workshop-wide
   files into `workshop` scope, or sets `awaiting`.
4. **On repeated failure**: `attempts` past `max_attempts` sets `parked`
   (`tasks.py:21-23,60-63`). OTP restart intensity: stop trying, propagate upward.
5. **On scope-wide failure**: the per-scope circuit breaker
   (`dispatch_triggers.sh:54,67`) shuts down the scope rather than burning budget.

Levels 4 and 5 exist today. Levels 1-3 are substrate plus the triager's filing rule.

### How roles interact across scopes

| Interaction | Mechanism |
| --- | --- |
| triager (project) files workshop-wide concern | files into `workshop` scope via task API |
| maker in project A needs something from project B | files a task in project B (if authorized); does not call B's agents |
| keeper consolidates across project scopes | the runner accepts multiple scope keys |
| broker is called by a maker or triager | in-band on request, never by the shell dispatcher |

No peer-to-peer messaging. No agent registry of "who is running". No leader
election. The task store and the note store are the entire coordination surface.

---

## 5. The note-driven triage loop (D1, D5)

The concrete walk-through of the note-to-ticket path. Both substrates exist; only
the wiring is missing.

**The constraint**: writing the note must be the WHOLE interaction. No tag, no
`@triage`, no special folder, no frontmatter flag. Every mechanism below is
invisible from the note-writing side.

### Step by step

**Step 1: note written.** Any path (the Notes UI, a chat capture, an agent writing
into its scope). Lands as `<scope>/knowledge/<id>.md` plus the `MEMORY.md` index
(`knowledge.py:92-111`).

**Step 2: detection, without an LLM.** The triager's ready predicate is a scan for
notes in the scope with `mtime` greater than the scope's triage watermark. Watermark
lives in `.runtime/trigger-state.json` (`dispatch_triggers.sh:53`) under key
`triage:<scope-key>`. The note's existence and its mtime are the signal. Nothing is
published, subscribed, or enqueued.

**Step 3: activation.** The shell's cron check fires; the ready predicate says yes;
the shell launches `scripts/roles/triager_run.sh <scope-key>` detached. Budget
admission (`usage_ledger.can_admit`, `dispatch_triggers.sh:331`) and
`MAX_AGENT_SWEEPS` (`:61`) apply unchanged.

**Step 4: the pass.** The runner gathers up to 20 notes (oldest first), plus the
scope's open tasks for linking, plus the agent body's rules. Deterministic rules run
first (no model call); the LLM handles the residue (one `claude -p` call per note).

**Step 5: the decision.** Each note gets exactly one of four outcomes, a closed set:

- `task`: file a Task with `source.kind=agent`, `source.ref=<job_id>`,
  `provenance=note:<scope>:<id>`.
- `link`: attach the note to an existing Task as evidence, when the note restates or
  refines known work.
- `escalate`: set `awaiting` on a Task, or file into the parent scope, when the note
  is outside what this scope can decide.
- `none`: a deliberate no-op, recorded with a reason.

The triager may not write code, may not write notes, may not issue verdicts, may not
answer questions on the operator's behalf. Output authority is the only gate that
matters; it cannot be bypassed by prompt injection because it is structural.

**Step 6: where decision state lives.**

- `provenance=note:<scope>:<id>` on the Task covers `task` and `link` decisions
  durably. It already exists as the dedupe key (`tasks.py:34`).
- `.runtime/triage-state.json` sidecar covers `none` decisions, which have no task
  to hang on, and carries `{ts, decision, reason, task_ids}` per note.
- Writing a `triaged: true` field into the note's frontmatter is explicitly rejected.
  It mutates the operator's note, violates the triager's output authority, and would
  make the triager re-trigger itself.

**Step 7: visibility.**

- The Task appears in Work (`frontend/src/pages/Work.tsx`) carrying `source.kind=agent`
  and `provenance=note:...`.
- Notes (`frontend/src/pages/Notes.tsx`) renders a triage state chip per note from
  the sidecar: pending / filed / linked / escalated / none.

**Step 8: watermark advance.** Over decided notes only, using the F16
commit-only-if-dispatched pattern already at `dispatch_triggers.sh:312-321,374-379`.
A crashed pass leaves the watermark where it was and retries next tick. A note that
hit the per-pass cap stays due.

### Ticket spam guards (four, in order of strength)

1. `none` is the **default** the triager's persona is pushed toward. A note is
   presumed to be a note, not a ticket request.
2. Per-pass filing cap (recommend 5 tasks per pass). The worst case is bounded.
3. The shipped embeddings-plus-LLM dedupe (`unified-agent-model-2026-07-15.md`,
   commit `833ee22`) converts a duplicate filing into a `link`.
4. Tasks filed from notes carry `provenance=note:...`, so the operator can filter and
   kill the source agent as a class.

**Loop safety by construction**: the triager cannot write notes, so it cannot change
its own trigger input, so it cannot re-fire itself. This is a structural guarantee,
not a prompt instruction.

---

## 6. Template-agent catalog

### Where templates live

`shared/agent-templates/<slug>.md`, tracked. Instances in `agents/` remain gitignored
(instances carry runtime state; `.gitignore:83`). Template format is identical to a
definition file (frontmatter plus body), so a template is readable, diffable, and
testable as the thing it produces.

Template-only frontmatter:

```yaml
template: note-triager
template_version: 1
description: 'Reviews new and updated notes in a scope, files or links tickets, escalates the rest'
```

Templates **fork** on adoption (not link). An adopted instance in `agents/` carries:

```yaml
template: note-triager@1
project_slug: <slug>
```

The fork is independent. Edits to the instance do not mutate the template. Template
improvements do not propagate automatically; the `template: <slug>@<version>` line
makes a future "your instance is based on v1, v3 is out" nudge possible without
building a sync engine.

Adoption is idempotent: adopting a group twice does not create two triagers. At most
one `triager` per (scope, role) pair is enforced at adopt time and on registry write.

### The three templates

Only shell-dispatched roles are templatable (`maker` and `reviewer` are invoked by
the loop, not the shell):

#### `note-triager`

- Role: `triager`
- Trigger: `cron`, daily at 09:00 local (operator can override)
- Purpose: the ant (D5). Reviews new and updated notes in the scope. Applies
  deterministic rules first, then one LLM call per residue note. Files tasks, links
  to existing tasks, escalates unresolvable notes, records `none` for deliberate
  no-ops. Does not write code, notes, or verdicts.
- Default body carries the four-decision closed set, the `none`-is-default posture,
  the per-pass caps (20 notes, 5 tasks), and the dedupe-to-link rule.
- Autonomy default: `scheduled`.

#### `inbox-watcher`

- Role: `triager`
- Trigger: `event` (feed-specific)
- Purpose: demonstrates that a watcher is a **configuration** of the triager role,
  not a separate role. Reads an external feed (email, finance event, webhook),
  converts items to tasks. Generalizes today's `email-triager` and `plaid-watcher`.
- Body carries the feed-specific rules. The rest is identical to `note-triager`.
- Autonomy default: `scheduled` with an event trigger.

#### `memory-keeper`

- Role: `keeper`
- Trigger: `cron`, every 6 hours
- Purpose: consolidates closed jobs and new learnings into the scope's knowledge
  store. Existing instance at `agents/memory-keeper.md` is the reference; this
  template makes it adoptable per-project.
- Outputs: itemized delta writes (ADD/UPDATE/RETIRE/NOOP) into `knowledge/`,
  `learnings.md`, and standing files. Skill drafts as proposals, never silent
  installs.
- Autonomy default: `scheduled`.

### Template groups

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

Two groups shipped, not a catalog:

| Group | Members | When to use |
| --- | --- | --- |
| `minimal` | none (zero agents) | default for any new project |
| `tended` | `note-triager` + `memory-keeper` | a project accumulating notes the operator wants kept warm |

**The default for a new project is zero agents.** The project loop (`project_ralph.sh`)
is not an agent; projects work without any definition file. A triager over an empty
notes folder is a scheduled no-op. The upgrade to `tended` is one click, available
exactly when it is warranted.

---

## 7. Implementation phases

### Phase R0: role enum (backend only, no behavior change)

Modify `backend/services/agents_registry.py`:
- Add `ROLES = ("maker","reviewer","triager","keeper","broker")` beside `DEPTHS`/`FANOUTS` (`:69-72`).
- Add `_coerce_role` beside `_coerce_depth` (`:75-80`). This one **rejects** unknown
  values rather than snapping, since silent correction is the bug.
- Back-compat alias: `role: memory` maps to `keeper`.
- Apply at parse (`:160`), `create` (`:393-407`), and `update` (`:417-470`).
- Rewrite the now-false comment at `:63-68`.

Modify `backend/routes/agents.py`:
- Surface the enum for the UI; 400 on an unknown role.

Verify: `agents/memory-keeper.md` still parses and dispatches identically; `role:
memoy` is rejected at write with a useful message; `role: null` is unaffected.

### Phase R1: role runner table (live cron, requires AUTONOMY_DISABLED bracket)

Add `scripts/roles/` directory.

Add `scripts/roles/keeper_run.sh`: thin alias to `scripts/memory_keeper_run.sh`,
preserving its contract verbatim (`memory_keeper_run.sh:10-21`).

Modify `scripts/dispatch_triggers.sh`:
- Replace the `role == "memory"` special case (`:329-342`) and the
  `skip-assistant` fallthrough (`:344`) with a `ROLE_RUNNERS` table plus per-role
  ready predicates.
- Replace the launcher if/elif (`:392-405`) with a table lookup, retaining `project`
  and `agent` as the loop-invoked maker paths.
- Unknown or unmapped role logs `skip-role` rather than `skip-assistant`.

Retire (or convert) `agents/workshop-overseer.md`. It has never run. The overseer
function is a workshop-scope `note-triager`, which is the same role with a different
scope key.

Verify: the memory-keeper dispatches exactly as before; an agent with an unknown role
logs `skip-role`; `MAX_AGENT_SWEEPS` and the budget gate still bound activation.

### Phase R2: the note-triager (D1, D5)

Add `backend/services/note_triage.py`:
- Since-watermark scan over `knowledge.scope_dir(scope_key)`.
- Open-task fetch for linking and dedupe.
- Four-decision apply step, writing through the existing task write path with
  `provenance=note:<scope>:<id>` and `source.kind=agent`.
- Sidecar decision record.
- Reuses the shipped embeddings-plus-LLM dedupe unchanged.

Add `scripts/roles/triager_run.sh`: the runner, honoring the role runner contract,
opening a Job, per-pass caps, watermark advance over decided notes only (F16 pattern).

Add `shared/agent-templates/note-triager.md`: the template, body with default
textual rules and `none`-is-default posture.

Modify `scripts/dispatch_triggers.sh`: register `triager` in the R1 table with its
ready predicate.

Verify: write a note, run a tick, exactly one task with `provenance=note:...`
appears. Second tick with no change: no second task. Edit the note: re-triaged,
links to the existing task. Bare URL note: decision `none`. Runner killed mid-pass:
watermark did not advance, next tick retries. Note file byte-identical before and
after (the output-authority constraint is a testable property).

### Phase R3: templates and groups (D2-D4)

Add `shared/agent-templates/` with the three templates and `groups/minimal.json` and
`groups/tended.json`.

Add `backend/services/agent_templates.py`: list templates, list groups, instantiate
(copy plus `project_slug` plus `template: <slug>@<version>` provenance), enforce
one-per-(scope, role).

Modify `backend/routes/agents.py`: `GET /agents/templates`, `GET
/agents/template-groups`, `POST /agents/adopt`.

Modify `frontend/src/pages/Agents.tsx`: an adopt affordance, edit-mode plus Save,
all four list states (loading/empty/error/overflow).

Verify: adopting `tended` creates two definitions with correct provenance; adopting
twice is idempotent; an adopted triager dispatches and triages that project's notes
only; editing an instance does not touch the template. `tsc --noEmit`,
`check_design.sh`.

### Phase R4: UI and condition handling (F4)

Modify `frontend/src/pages/Notes.tsx`: per-note triage `StatusPill` (pending / filed
/ linked / escalated / none) from the sidecar; link from a filed note to its task.

Modify `frontend/src/pages/Work.tsx`: `source.kind` filter and a "from notes" filter
on `provenance` prefix so note-spawned tasks are visible and dismissible as a class.

Modify `frontend/src/pages/AgentDetail.tsx`: role badge; role-conditional `depth` and
`fanout` (shown for `maker`, hidden otherwise); last pass and decision counts for
shell-dispatched roles.

Verify: every list ships all four states; a triager pass shows in Jobs with no new
surface; a failed pass shows as a failed Job and the note stays `pending`; widths at
1440/1024/768/390; `tsc --noEmit`, `check_design.sh`.

---

## 8. Condition handling reference

| Condition | Handling | Mechanism |
| --- | --- | --- |
| A role's pass fails | It is a Job; shows as failed. Watermark did not advance; next tick retries. | F16 pattern (`dispatch_triggers.sh:312-321`) |
| A role fails repeatedly | Per-scope circuit breaker trips. | `.runtime/circuit-breaker.json` |
| Triager re-triggers itself | Cannot: it may not write notes. | Output authority (structural) |
| Two roles want the same task | Claim fence rejects the loser. | `claim_token` (`tasks.py:28`) |
| Two triagers on one scope | Forbidden at adopt and registry write. | One-per-(scope, role) constraint |
| Triager spams tickets | `none` default, per-pass filing cap, dedupe-to-link, filter-and-kill. | Section 5 guards |
| Triager wrong about a note | Its output is a ticket, not an edit. Cost of a wrong ticket is one delete. | Output authority |
| Role unknown to the shell | `skip-role` in the log. | R1 |
| Budget exhausted | `skip-budget`; already implemented. | `usage_ledger.can_admit` |
| Agent has no role | Not dispatchable, skipped silently. Valid state. | `None` in enum |
| Note escalated to parent scope | Filed into parent scope or sets `awaiting`. | Scope nesting rule |

---

## 9. Coverage of operator asks

| Ask | Addressed in |
| --- | --- |
| D1: note left, just picked up | Sections 5, step 1-2; output-authority rejection of note mutation |
| D2: simple general worker job-template agents | Section 6, the three templates |
| D3: configurable and editable per project | Section 6, fork-not-link with cost stated |
| D4: default set plus template groups | Section 6, `minimal` and `tended` |
| D5: reviews notes, ensures addressed, tickets or escalates | Sections 3, 5 end-to-end |
| D6: "like ants having little jobs" | Section 3, ant metaphor rejected with reasons, stigmergy kept, small single-purpose workers honored |
| E1: other primitive roles | Section 2 |
| E2: deep research | `agent-roles-research-2026-07-16.md` Part 2 (cited here) |
| E3: distill to a core set and a system for interaction | Sections 2, 3 |
| E4: conductor/overseer, different scopes | Section 4 |
| E5: integrate with broader design | Sections 3-7; see also `synthesis-agent-protocols-2026-07-16.md` |

---

## 10. Open questions (recommended defaults)

1. **`role: memory` alias**: rename to `role: keeper` with back-compat alias?
   **Recommend yes.** Every other role states an authority; "memory" states a subject.
   One alias line, no migration.

2. **Retire `agents/workshop-overseer.md`?**
   **Recommend yes.** It has never run. The overseer function is a workshop-scope
   `note-triager`. A roster entry that cannot execute makes the roster lie.

3. **Closed `ROLES` enum?**
   **Recommend yes, with `None` valid.** It is already a de facto enum at
   `dispatch_triggers.sh:329`, just unvalidated. Closing it converts a silent behavior
   loss into a write-time error. Rules and judgment stay free-form in the body.

4. **`depth` and `fanout` on non-maker roles?**
   **Recommend: keep, scope to `maker` in the UI, document as maker-only.** They are
   inert on other roles; the migration cost is zero. Per-role caps live in the runner
   via the existing `limits` field.

5. **Default project agent set?**
   **Recommend: `minimal` (zero agents).** A triager over an empty notes folder is a
   scheduled no-op. `tended` is one click when warranted.

6. **Templates fork or link on adopt?**
   **Recommend: fork.** D3 demands per-project editing, and an edited link silently
   leaks to every other adopter. The cost (no auto-propagation of improvements) is
   real and accepted; re-adopting takes a minute.

7. **"denounced" in the operator's D5 sentence?**
   Read as "de-duped" and satisfied by the `link` decision plus the existing
   embeddings-plus-LLM dedupe path. Worth one word of operator confirmation if
   something else was meant.
