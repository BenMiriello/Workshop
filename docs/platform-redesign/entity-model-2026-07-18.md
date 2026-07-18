# Entity model and canonical vocabulary (2026-07-18)

- Status: accepted
- Supersedes: concept-schema-2026-07-16.md (see Integration map, section 9)
- Depends on: ADR 0001 (task store), ADR 0002 (knowledge store), ADR 0004 (agent identity), ADR 0005 (policy rules), ADR 0006 (learning mechanism)

This document is the single authoritative reference for every entity in the Workshop
platform. All subsequent design tickets, ADRs, and implementation work build from
the vocabulary and schemas defined here. When this doc and a prior 2026-07-16 report
disagree, THIS doc wins.

---

## 1. Canonical vocabulary

One word per concept. Aliases are noted; code identifiers that differ from the
canonical name are called out explicitly.

| Concept | Canonical name | Code identifier | Aliases to retire |
| --- | --- | --- | --- |
| Attended conversation | **Chat** | `chat` | session, conversation, thread |
| Durable work item | **Ticket** | `task` (per ADR 0001) | task, issue, to-do |
| Single execution attempt | **Run** | `job` (legacy) | job, attempt, execution |
| Typed dispatchable worker | **Agent** | `agent` | assistant (except in user-facing prose) |
| Named storage layer | **Store** | `store` | vault, repository, resource |
| Scoped execution boundary | **Scope** | `scope` | project scope, agent scope |
| Permission token | **Grant** | `grant` | permission, capability, access token |
| Escalation request | **Block** | `awaiting` (field name) | pending, paused, waiting |

### Disambiguation rules

**Chat vs Scope**: A Chat lives inside a Scope; it is not the scope itself. Multiple
Chats can exist in the same Scope simultaneously. Scope is the access-control
boundary; Chat is the conversational surface.

**Ticket vs Run**: A Ticket is the durable record of what needs to happen; a Run is
one attempt to make it happen. A Ticket with no Runs has never been executed. A
Ticket with multiple Runs was retried. Code keeps `task` / `job` as the noun pair
(ADR 0001) because renaming would break imports; the UI says "ticket" / "run".

**Agent vs Assistant**: Agent is the system concept (typed, role-bearing, grant-
holding, dispatchable). "Assistant" is acceptable in user-facing prose where it reads
more naturally, but never in code identifiers, schema fields, or design docs.

**Block vs Error**: A Block is a deliberate pause awaiting operator input. An Error is
an unexpected failure. Blocks surface through the `awaiting` field; errors surface
through the Run's status field. They are not the same state.

---

## 2. Scope addressing

Every entity lives inside a Scope. Scope is a string address that doubles as the
access-control boundary.

```
workshop                    -- top-level, one per installation
project:<slug>              -- a named project workspace
app:<name>                  -- a running application instance
agent:<slug>                -- an agent's private context
```

A Chat, Ticket, Run, or Agent belongs to exactly one Scope. Cross-scope access
requires an explicit Grant. The Scope string is the first argument to every Store
read/write (see `backend/services/knowledge.py` for the current implementation).

Sub-scopes inherit downward: an entity in `project:foo` is not visible to
`project:bar` unless a Grant says otherwise. An entity in `workshop` is visible
(subject to its Store tier) to all projects under it.

---

## 3. Identity kinds

Three kinds of identity can hold a Grant (per ADR 0004):

| Kind | Authority model | Examples |
| --- | --- | --- |
| **Operator** | Always-allow; no Grant required | benmiriello (the installation owner) |
| **Guest** | Gated; must present a valid Grant | External integrations, webhook callers |
| **Agent** | Zero ambient authority; must present a Grant for every Store access | Every named Agent in the registry |

Agents have zero ambient authority by default. An Agent that needs to read a Store
must be given a Grant scoped to exactly what it needs. The `attenuate()` function
in `permissions.py:647` narrows Grants macaroon-style; it currently has zero call
sites (live defect D4) but the mechanism is correct. All new code that creates or
narrows Grants must go through this function once it is wired.

---

## 4. Ticket entity

Code noun: `task`. Defined in `backend/services/tasks.py`.

### 4.1 State machine

```
todo --> doing --> in_review --> done
  |        |           |
  |        v           v
  |     blocked      cancelled
  |        |
  |        v
  +---> parked
```

Transitions:
- `todo -> doing`: worker picks up the ticket
- `doing -> in_review`: worker signals work complete, awaiting human or reviewer-agent check
- `doing -> blocked`: worker hit a Block (awaiting operator input)
- `in_review -> done`: reviewer approves
- `in_review -> doing`: reviewer requests changes
- `blocked -> doing`: operator resolves the Block; ticket re-enters the queue
- `doing -> parked`: operator explicitly suspends work (not a failure; not a Block)
- `parked -> todo`: operator un-parks; ticket re-enters as fresh
- Any state -> `cancelled`: operator explicitly cancels

### 4.2 Fields

```
id: str                          # uuid, stable primary key
text: str                        # first line = title; rest = body (per ADR 0001)
status: TicketStatus             # the state machine above
priority: int                    # lower = higher priority; preference only, not eligibility
latitude: Latitude               # strict | standard | open (see section 7.3)
scope: str                       # scope address
created_at: datetime
updated_at: datetime
job_ids: list[str]               # ordered list of Run IDs (attempts, oldest first)

# Dependency edges (new, per operator directive 2026-07-18)
depends_on: list[str]            # ticket IDs this ticket cannot start until done/cancelled
blocks: list[str]                # inverse of depends_on, derived (not stored separately)
relates_to: list[str]            # soft links, UI-only, no scheduling effect

# Escalation (widened shape)
awaiting: Awaiting | None        # see 4.3

# Learned scope
touches: list[str]               # path globs bounding file access; advisory until enforced
```

### 4.3 Awaiting shape (widened)

The `awaiting` field signals a Block. Its shape is widened from the current
single-string form to a structured record:

```
kind: str             # permission | input | external | conflict | other
why: str              # human-readable explanation of the block
unblocks_if: str      # what condition (described in plain text) resolves this
options: list[str]    # suggested operator responses, if any
default: str | None   # the option that fires if operator does not respond (None = wait forever)
proposal: str | None  # agent's recommended resolution
raised_by: str        # agent slug or "system"
dedupe_key: str       # prevents the same block from being re-raised on retry
deadline: datetime | None  # if set, fires default action when reached
```

The six operator response verbs for a Block are: approve-once, grant-standing,
amend-rule, rescope-task, reject, defer. These verbs are expressed as UI actions
on the Work page's "review" tab.

### 4.4 Dependency scheduling rule

The worker's next-pick algorithm selects the highest-priority Ticket whose
`depends_on` set is fully satisfied (all referenced Tickets in `done` or
`cancelled` status). A Ticket with an unsatisfied dependency is ineligible regardless
of its priority. This is the enforcement mechanism for build order.

Cycle detection is required. The dependency graph is a DAG; any operation that would
create a cycle must be rejected with a clear error. The operator and agents can add
or remove edges at any time; cycle detection runs on every mutation.

---

## 5. Run entity

Code noun: `job`. A Run is one attempt to work a Ticket. One Ticket has zero to N Runs.

### 5.1 State machine

```
running --> done
    |
    +--> failed
    |
    +--> cancelled
```

A Run does not have a "blocked" state. A Block is a Ticket state, not a Run state.
When a worker raises a Block, the current Run is paused (or ended) and the Ticket
transitions to `blocked`. A new Run begins when the operator resolves the Block.

This is a deliberate design choice: the LLM context at the time of the block is
stale by the time the operator responds. The new Run starts from the Ticket's durable
state, not from a resume point in the old LLM context. Durable-execution / resume is
out of scope (see section 9.7).

### 5.2 Fields

```
id: str
task_id: str         # the Ticket this Run belongs to
status: RunStatus    # running | done | failed | cancelled
agent_slug: str      # which Agent ran this attempt
started_at: datetime
ended_at: datetime | None
output_summary: str  # short human-readable summary of what the Run produced
log_ref: str | None  # pointer to the full transcript (e.g. .letta/ path or storage key)
```

---

## 6. Agent entity

An Agent is a named, typed, role-bearing, dispatchable worker definition. It is
the third Identity.kind (per ADR 0004). It holds zero ambient authority.

### 6.1 Role enum (closed)

Role is an enumeration, not a free string. The five legal values are:

| Role | Output authority | Description |
| --- | --- | --- |
| `maker` | Creates artifacts | Writes code, docs, data. Primary producer. |
| `reviewer` | Approves or rejects | Checks maker output. Cannot self-approve. |
| `triager` | Routes and classifies | Reads input, creates or assigns Tickets. No writing artifacts. |
| `keeper` | Maintains a Store | Read/write on exactly one designated Store; creates no Tickets. |
| `broker` | Mediates cross-Store access | Reviews grant requests; never reads the data it adjudicates. |

A sixth value, `None` (no role), is valid for agents that exist only as named callers
without a role-gated behavior. Any `role` string not in this set is invalid; the
registry must reject it. The current live defect (`dispatch_triggers.sh:329`
hardcodes `role == "memory"`) must be repaired in phase P0.

### 6.2 Fields

```
slug: str                     # stable unique identifier
display_name: str
role: AgentRole | None        # the closed enum above
latitude: Latitude            # strict | standard | open; inherits from project if not set
autonomy: Autonomy            # reactive | supervised | autonomous
triggers: list[str]           # events that wake this agent; empty = manual-only
limits: AgentLimits           # max tokens, max files, max tasks per run
grants: list[GrantRef]        # what Stores this agent can access and at what tier
scope: str                    # home scope (may run in child scopes per grant)
template_slug: str | None     # if this agent was instantiated from a template
```

### 6.3 Zero-agent default

New projects start with zero Agents. Agents are an optional layer added by the
operator or by the ticket-maker when a decomposed sub-ticket requires a specific
role. This matches the "0 or multiple agents" requirement from operator ask A4.

---

## 7. Chat entity

A Chat is an attended conversation between the operator and one or more Agents,
running inside a Scope.

### 7.1 Fields

```
id: str
scope: str              # the Scope this chat belongs to
title: str | None       # operator-set label; auto-generated if not set
started_at: datetime
last_active_at: datetime
participants: list[str] # agent slugs; operator is always implicit
linked_ticket_ids: list[str]  # Tickets created or referenced in this chat
linked_run_ids: list[str]     # Runs spawned from this chat
```

### 7.2 Scoping rule

Scoping and decomposition happen in the Chat BEFORE the chat spawns a Run. A Chat
does not scope inside a Run; the Run executes an already-scoped Ticket. When a Chat
is re-scoped while it has live Runs or Agents, the UI shows a warning: "Move to X
and disconnect from runs/agents/other outside this scope?" with Yes/Cancel.

Any Chat in the same Scope can edit any Ticket in that Scope. Before overwriting or
changing plans, the editing Chat must surface conflicts to the operator. Cross-chat
conflict detection is part of the B7 work item.

### 7.3 Card linking

Action cards in Chat that reference Tickets or Runs link to the target entity AND
back to the originating Chat. If a single action filed multiple Tickets, tapping the
card opens a picker to select which one.

---

## 8. Cross-cutting concepts

### 8.1 Grants and the policy engine

A Grant is a scoped permission token that specifies: who holds it, what Store it
covers, at what tier (open/gated/mediated), and which verb set is allowed
(read/write/append/delete). The `mint_grant` + `check` pair in `permissions.py`
is the policy engine (per ADR 0005). The `attenuate()` function at `permissions.py:647`
narrows Grants; it must be wired to all new Grant-creation paths.

Rule promotion: textual rules proven stable across three or more Blocks are proposed
for programmatic grant promotion via `learnings.py`. The provenance counter in the
learning mechanism (ADR 0006) supplies the stability signal.

### 8.2 Latitude

Latitude is a per-entity setting that controls how a worker behaves at the edge of
its specification. It is a property of the Ticket or Agent, not of the Run.

| Value | Meaning |
| --- | --- |
| `strict` | Worker blocks and asks when anything is ambiguous. High block rate. |
| `standard` | Worker uses judgment for low-risk decisions; blocks on high-risk ones. |
| `open` | Worker is free to resolve ambiguity autonomously within granted authority. |

Cascade order: Ticket latitude overrides Agent latitude; Agent latitude overrides
Project latitude. If none is set, `standard` is the default.

Note: `final-plan-agent-generalize-and-masini-fold-2026-07-16.md` used "low/medium/
high" for these values. The canonical names are `strict/standard/open` as defined
in `scopedness-and-escalation-2026-07-16.md` (the more fully-reasoned source). The
final-plan doc's latitude naming is superseded here.

### 8.3 Store tiers

Every Store is assigned one of three access tiers:

| Tier | Access model |
| --- | --- |
| `open` | Any Agent with a Grant can read/write. |
| `gated` | Hard API check; must present a matching Grant. |
| `mediated` | A broker Agent reviews the request against an allowlist before access. |

The mediated tier is the mechanism behind operator ask A6 (LLM-reviewed access).
The broker Agent must never read the data it adjudicates (invariant I2 from
`synthesis-agent-protocols-2026-07-16.md`).

### 8.4 Autonomy modes

| Mode | Behavior |
| --- | --- |
| `reactive` | Runs only when explicitly triggered by operator or another Agent. |
| `supervised` | Runs on trigger; surfaces results before committing. |
| `autonomous` | Runs on trigger; commits results without operator review. |

### 8.5 Live defects (not yet fixed, must be repaired in P0)

Four defects in the current code must be repaired before any entity-model work goes
into production. They are enumerated here so each implementation ticket can cite
them:

- **D1**: `scripts/dispatch_triggers.sh:329` branches on `role == "memory"` and skips
  every other value. Five trigger-carrying Agents never run. This is the blocker for
  triager, keeper, broker, and maker-on-trigger roles.
- **D2**: No `_coerce_role` in `backend/services/agents_registry.py`. A typo in a
  stored Agent's role (e.g. `memoy`) silently disables that Agent rather than
  rejecting it.
- **D3**: The `touches` field (path-glob fan-out safety) is declared in the schema but
  enforced nowhere. Advisory-only until wired.
- **D4**: `attenuate()` at `permissions.py:647` has zero call sites. Every grant
  narrowing operation currently bypasses the macaroon-style narrowing function.

---

## 9. Integration map

This section is the authoritative record of how the eight `docs/platform-redesign/
*-2026-07-16.md` documents relate to this entity model and to each other.

### 9.1 Status legend

- **SUPERSEDED**: This doc is replaced by the entity model. Do not update it. Its
  content is preserved in the entity model. Keep the file for audit history.
- **FOLDED IN**: The doc's content was absorbed into this entity model. The doc
  itself remains as supporting research but is not authoritative.
- **PRESERVED**: This doc is the record of an operator ask or an execution plan. It
  is not superseded; it remains authoritative for its stated purpose.

### 9.2 Document-by-document status

| Document | Status | What remains authoritative |
| --- | --- | --- |
| `_brief-agent-protocols-2026-07-16.md` | SUPERSEDED | Historical record only. Entity model replaces all vocabulary and schema claims. |
| `agent-roles-research-2026-07-16.md` | FOLDED IN | Supporting research. Five roles, D1-D4 defect catalog, and zero-agent default are all now in this doc (sections 6.1, 8.5). |
| `concept-schema-2026-07-16.md` | SUPERSEDED | This entity model is its direct successor. Six-axis table, role table, grant model, Task/Job fields are all superseded by sections 3-8 above. |
| `final-plan-agent-generalize-and-masini-fold-2026-07-16.md` | PRESERVED | Still the execution plan (phases P0-P4 and PM). Its latitude naming ("low/medium/high") is superseded by section 8.2. |
| `operator-asks-2026-07-16.md` | PRESERVED | Verbatim ask record. Never superseded. When a design choice in the entity model traces to an operator ask, the ask ID (A1, B3, etc.) is the citation. |
| `resource-access-protocol-2026-07-16.md` | FOLDED IN | Store tiers, broker invariants, and access modes are now in sections 8.1 and 8.3. |
| `scopedness-and-escalation-2026-07-16.md` | FOLDED IN | Latitude definitions, awaiting field shape, operator response verbs, and block loop are in sections 4.3, 7.2, 8.2. |
| `synthesis-agent-protocols-2026-07-16.md` | SUPERSEDED | This entity model is its successor. Convergent spine ("authority is a property of the grant") is encoded in section 3 and 8.1. Build order is in final-plan (preserved). |

### 9.3 What the addenda contribute

Three operator addenda supplement the 2026-07-16 thread and are absorbed here:

- `orchestration-surface-requirements-addendum-re-s-19f74360f21.md` (first addendum):
  scoping/decomposition in main chat thread before spawning a run (section 7.2);
  re-scope warning wording (section 7.2).
- `orchestration-surface-requirements-addendum-re-s-19f743cb2c2.md` (second addendum):
  shared scope context / cross-chat editing (section 7.2); card linking (section 7.3);
  resilience (deferred to implementation; no entity change).
- `ticket-dependencies-first-class-edge-model-adden-19f7440aaf0.md` (operator directive
  2026-07-18): depends_on/blocks/relates_to edges, eligibility rule, cycle detection
  (section 4.4 and Ticket fields in 4.2).

### 9.4 What the workshop-v1 overview contributes

`docs/workshop-v1/00-overview.md` defines three linkable platform concepts: Project
(workbench/build), App (launcher/run), Deployment (running instance). These are
preserved as-is; the entity model adds Chat, Ticket, Run, and Agent on top. The
v1 "Notes" concept in that doc refers to the operator messages timeline, not to the
knowledge store; read it in that context only.

### 9.5 What this doc does NOT change

- The Task store schema in `backend/services/tasks.py` (no migration in this doc)
- The code identifiers `task` and `job` (ADR 0001 is not overridden)
- The knowledge store scopes in `backend/services/knowledge.py`
- Any voice-mode files
- The six-axis table from concept-schema is retained conceptually but simplified;
  the axes role, autonomy, latitude, depth/fanout, grants, and tier are all present
  in the entity definitions above
- ADRs 0001 through 0006 are not superseded; this doc adds 0007 on top

### 9.6 Settled questions (should not be re-litigated)

These questions were open in the 2026-07-16 docs and are now closed:

- Latitude values: `strict/standard/open` (not low/medium/high)
- Agent identity: third kind with zero ambient authority (ADR 0004, confirmed)
- Role enum: closed, five values plus None (not extensible at runtime)
- Conductor agent: does not exist; conductor logic is ~20 lines of deterministic Python
- Durable-execution / job-resume: out of scope (see 9.7)
- Policy DSL: not needed; `mint_grant` + `check` is the policy engine (ADR 0005)
- Default agent set for new projects: zero

### 9.7 Explicitly deferred

- Resilience handling for voice chat (second addendum): noted, deferred to B8 implementation ticket
- Durable-execution / run-resume: deferred; LLM context is stale at unblock; the
  Task record is the durable state
- `summarized` response mode for mediated broker: not in the seed set; no mechanical
  verifier possible; extension point preserved for later
- Enforcement of `touches` (D3): deferred to P1 implementation

---

## 10. Build order

The execution plan in `final-plan-agent-generalize-and-masini-fold-2026-07-16.md`
is preserved. The phases reference this entity model for vocabulary and schema:

- P0 (repair): Fix D1-D4. No entity changes; just wiring.
- P1 (latitude): Add latitude field to Ticket and Agent; enforce cascade.
- P2 (block loop): Wire awaiting shape (4.3); build Work page review tab; wire
  operator response verbs.
- P3 (mediated broker): Wire broker role; wire attenuate() call sites (D4 repair).
- P4 (templates): Agent template library; zero-agent-default for new projects.
- PM (masini fold-in): Separate, larger scope; does not block P0-P4.
