# The concept schema: agents, scopes, autonomy, authority (2026-07-16)

> **STATUS: PROPOSED. NOT BUILT. No code has been written for any of this.**
> Nothing in this document is shipped. Fields marked `NEW` do not exist in the tree.
> The gating decision (agent as a third identity kind) is UNANSWERED by the operator
> as of 2026-07-16. See `synthesis-agent-protocols-2026-07-16.md` Part 7.

One page for the whole model. Distilled from the three thread reports of 2026-07-16 and
reconciled in `synthesis-agent-protocols-2026-07-16.md`. Every field below is either
shipped (marked `exists`), inert-but-shipped (`inert`), or proposed (`NEW`).

Read this first; the reports are the arguments, this is the model.

## The one idea

**Authority is a property of the grant, not of the name.** A role names the shape of
authority, a grant carries it, latitude says what to do at its edge, and autonomy says
when to wake up. Everything below is that sentence, decomposed.

## The six orthogonal axes

The single most common modeling error here is fusing two of these. They are independent,
and every pair is a coherent combination:

| Axis | Question it answers | Values | Applies to |
| --- | --- | --- | --- |
| `role` | What may it **write**? | maker, reviewer, triager, keeper, broker | every agent |
| `autonomy` | **When** does it wake? | off, manual, on-demand, scheduled, event-driven, continuous | every agent |
| `latitude` | What at the **edge of the spec**? | strict, standard, open | every task/job |
| `depth` / `fanout` | How **hard** does it think? | quick/standard/deep; a width cap | `maker` only |
| grants | What may it **reach**? | (Store URI, verbs, caveats) | every job |
| tier | How does a **resource** decide? | open, gated, mediated | every Store |

Worked orthogonality, to make it concrete:

- `strict` + `deep` is coherent and useful: think hard, then ask before choosing.
- `open` + `quick` is coherent: do the obvious thing, do not bother me.
- A `triager` with `continuous` autonomy is coherent and a bad idea (a note is not
  urgent), which is a policy default, not a schema constraint.
- A `maker` with rich grants and `strict` latitude is the "hands-on" mode from ask C3.

`role` and `latitude` are the two most often confused. `role` is a hard boundary the
shell enforces (a triager *cannot* write code). `latitude` is an advisory policy above
that floor (a maker *may* refactor without asking). One is a wall, one is a judgment
call. See "the two-layer rule" below.

---

## 1. Identity: three kinds

`Identity.kind` today is `operator | guest` (`permissions.py:71-93`). Proposed third:

| Kind | Ambient authority | Gated by | Status |
| --- | --- | --- | --- |
| `operator` | Always allow (`permissions.py:354-356`) | Nothing. Opt-in `confirm` only, as UX. | exists |
| `guest` | Sensitivity ladder (`:379-393`) | The external/shared boundary. | exists |
| `agent` | **None whatsoever** | `check(token, uri, verb)` only. `authorize()` is never consulted. | **NEW** |

```
Identity(kind="agent", id="<agent-slug>", on_behalf_of="operator", job_id="<job>")
```

**Why this is not defensive cruft**: `operator` is not a description of the human, it is
an assignment of authority to a *running process*. The hard rule forbids gating the
human. It does not forbid bounding a program that ingests hostile input (note bodies,
web pages) while holding the operator's authority. That is Hardy's confused deputy.

**Why nothing regresses**: the dispatcher **mints a generous in-scope grant set at job
start**. A job on `project:acme` begins holding a subtree grant over
`workshop://store/knowledge/project-acme` with `[read, list, search, append, write]`,
which is exactly its reach today. The gate exists only at the job's **perimeter**.

> Default-permissive within scope. Mediated at the boundary. Permissive-by-declaration
> for the operator.

**Attended chats need no fourth kind.** An attended session's agent is minted its chat
scope's grants at session start. The operator's presence adds nothing, because the
operator can always read the thing themselves via the ungated route.

---

## 2. Agent: the registry record

An agent is a definition file in `agents/` (gitignored; on-disk only), parsed by
`agents_registry.py`.

```yaml
name: notes-triager           # slug
runtime: assistant            # builder | assistant          exists
role: triager                 # the five                      inert -> NEW enum
project_slug: acme            # binds it to a scope           exists
purpose: ''                   # free-form                     exists
capabilities: []              # free-form                     exists
triggers:                     # cron | loop | event           exists
  - kind: cron
    schedule: '0 9 * * *'
depth: standard               # maker only                    exists
fanout: standard              # maker only                    exists
latitude: strict              # NEW, emit only when non-default
```

**`runtime` vs `role` are different things.** `runtime` is *how it executes* (builder =
code-writing loop; assistant = data-tier turn). `role` is *what it may write*. The
current dispatcher conflates them, which is bug D1 below.

### The five roles (axis: output authority)

| Role | May write | Must not write | Dispatch | Status |
| --- | --- | --- | --- | --- |
| `maker` | working tree, commits, task transitions on its own claim | memory, other scopes' trees | loop-invoked | exists |
| `reviewer` | a verdict; rejection reasons to `learnings.md` | the tree, tasks | loop-invoked | exists |
| `triager` | tasks (file/link/escalate/none) + a decision record | the tree, notes, verdicts | shell | **NEW** |
| `keeper` | knowledge notes, `learnings.md`, skill *proposals* | the tree, tasks | shell | exists (`memory-keeper`) |
| `broker` | nothing (releases information only) | everything | in-band, never swept | **NEW** |

Two structural consequences worth internalizing:

- **Loop-invoked roles are not templatable.** `maker` and `reviewer` are called by the
  worker loop (`ralph.sh` invokes the reviewer), not by the control shell. Only
  shell-dispatched roles (`triager`, `keeper`, `broker`) get templates. Three templates,
  not five.
- **Loop safety is structural, not prompted.** A `triager` cannot write notes, so it
  cannot alter its own trigger input, so it cannot re-trigger itself. That is a
  guarantee. Prompt-level mitigation buys 5-14% (MAST); guarantees are what pay.

**At most one shell-dispatched agent per (scope, role).**

### What is deliberately not a role

- **watcher** = a `triager` with an `event` trigger. A configuration, not a role.
- **janitor / gardener / curator** = a `keeper`. Same authority.
- **conductor / overseer** = not an agent at all. The conductor is ~20 lines of Python.
  The overseer function is a `triager` at `workshop` scope: same role, different scope
  key. This is why `agents/workshop-overseer.md` is recommended for retirement.
- **researcher** = a `maker` whose output is a document, or a read-only subagent.

---

## 3. Scope: the addressing system

Scopes already exist and already map one-to-one onto knowledge roots
(`knowledge.py:92-111`):

| Scope key | Root | Committed? |
| --- | --- | --- |
| `workshop` | `shared/knowledge/` | yes |
| `project:<slug>` | `projects/<slug>/knowledge/` | no (instance-local) |
| `app:<name>` | `apps/<name>/knowledge/` | yes |
| `agent:<slug>` | `.runtime/agents/<slug>/knowledge/` | no |

**Load-bearing fact**: `knowledge.register_knowledge_stores()` already registers each
scope as a Store of type `"Note"`. A folder of notes is *already* an addressable,
grantable resource with a URI. This is why the notes contract does not need a new
substrate (ADR 0002).

**Projects and agents are peers, not parent and child.** An agent does not inherit from
a project; both are *scopes*. This answers ask C4 directly and is why no new inheritance
mechanism is needed.

**Scope nesting for escalation**: a triager that cannot decide within its scope
escalates to the parent scope (`project:acme` -> `workshop`). Escalation is scope
traversal, not a message to another agent.

---

## 4. Autonomy: when it wakes

Exists (`triggers.py:214`, `:26`, `event_dispatch.py:147-149`). Unchanged by this work.

| Mode | Meaning |
| --- | --- |
| `off` | never |
| `manual` / `on-demand` | operator presses something |
| `scheduled` | cron cadence + a cheap ready predicate |
| `event-driven` | a subscription fires it |
| `continuous` | a **bounded** loop (max_iterations + budget + reviewer gate, `triggers.py:190-207`) |

Defaults by role: `maker` inherits the scope preset (project/builder default
`on-demand`); `triager` defaults to `scheduled` (a note is not urgent; and `on-demand`
would violate ask D1, since the operator would have to press something); `keeper` is
`scheduled`; `broker` has **no trigger, ever** (demand-only, in-band).

---

## 5. Latitude: what it does at the edge of the spec

**NEW.** One field. The answer to ask C1, which the operator could not name.

Why the name would not come: two things were fused. **How well-scoped a ticket *is*** is
a property of the ticket text, an observation, not a dial (a `scopedness: high` field on
a vague ticket would be a lie stored in JSON). What is settable is **what the worker does
when the ticket does not say**.

```
LATITUDES = ("strict", "standard", "open")
DEFAULT_LATITUDE = "standard"
```

| `latitude` | Rule at the edge of the spec | Blocks | Use when |
| --- | --- | --- | --- |
| `strict` | Do what the ticket says. Any unspecified decision raises a block. | many | "be more hands on"; unfamiliar area |
| `standard` | Decide **reversible** things, record as evidence. Block on **irreversible**, on anything outside `touches`, on any hard gate. | few | the default |
| `open` | Pursue the goal, decide freely within the enforced floor, report at the end. | rare | "requirements and goals, figure it out" |

**Reversible** has a stated definition in the persona: a later `git revert` or one-line
edit fully undoes it, no external side effect, no third party observed it. Adding a
file: reversible. Adding a lockfile dependency others will install: not. Pushing a
branch: not.

**Latitude *is* the block rate.** This is the load-bearing claim that unifies two of the
operator's threads: the blocked/escalation loop is not a subsystem beside scopedness, it
is the *runtime behavior* of this dial. Build them as one phase.

### The cascade (no new mechanism)

```
invocation  >  task  >  scope  >  DEFAULT_LATITUDE
```

First non-null wins. Identical to `depth` (`orchestration-and-autonomy.md` 7.1).

| Level | Where it is stored |
| --- | --- |
| scope (agent) | agent frontmatter (`agents_registry.py:160-163` parse, `:374-381` serialize) |
| scope (project) | project registry entry, beside `autonomy` |
| task | `.tasks.json`, beside `effort` (`tasks.py:34`) |
| invocation | sweep argv, beside the depth override |

**Job level: no.** A Job is an *attempt*, not a configuration site. The resolved value is
*recorded* on the job for provenance, which is different from being settable there.

**Null means inherit, not `standard`.** Emit only when non-default, or a project set to
`strict` gets silently undone by a task storing its own default. This is correctness,
not tidiness.

---

## 6. Authority: grants, verbs, tiers

### Grant

```
grant = (agent identity, Store URI, verbs[], caveats{expiry, filter}, token)
```

`mint_grant()` (`permissions.py:616`), `check(token, uri, verb)` (`:710`), `revoke()`
(`:680`), `attenuate()` (`:647`, **zero call sites today**).

### Verbs

Today: `("read", "write", "append")` (`permissions.py:56`). Insufficient for ask A5
("request by search or name or other").

**Proposed: `read`, `list`, `search`, `append`, `write`.**

- `list`: ids, names, titles, tags, mtimes. No bodies.
- `search`: query bodies, return which match.
- **The existence distinction is real**, so both verbs are real: `list` without `read`
  is coherent (an index agent needs names, not contents); `read` without `list` is
  coherent (a worker handed one note id needs no enumeration power).
- **No `exists` verb.** It is `list` filtered to a singleton: no added expressive power,
  but a cheap oracle (probe in a loop, reconstruct the index).
- **The search leak rule**: `search` without `read` returns ids, titles, match counts.
  No snippets. `search` with `read` may return snippets. Mechanical, enforced in the
  provider, not a broker judgment.

### Tier (a property of a policy record attached to a URI)

| Tier | Who decides | Cost |
| --- | --- | --- |
| `open` | nobody, at request time | zero |
| `gated` | code, from the policy record (verbs, subtree, mechanical predicate) | zero |
| `mediated` | a smart LLM, attenuating within the policy allowlist | one Sonnet call |

**`open` is the default for a Store with no policy record.** An empty policy file means
exactly today's behavior. A fresh install gates nothing. Policy is opt-in (asks A11 +
anti-cruft).

**Policy lives outside the resource it protects**, never in a folder sidecar: an agent
with `write` on the folder could rewrite its own gate.

**Secrets are `gated` only, never `mediated`.** There is no filtered view of a secret;
any release is total release. The schema should refuse `tier: mediated` on type
`Secret`. Naming a place the flagship feature is banned is how we know the tiers mean
something.

### The two broker invariants

- **I1: the LLM can only attenuate, never amplify.** Every verdict narrows a maximum the
  operator declared programmatically. A fully injected broker yields a grant inside
  `topics_allow`, which the operator already blessed. This is what makes an LLM safe in
  the decision path at all.
- **I2: the broker never reads the data it adjudicates.** Inputs: identity, URI,
  selector, purpose, policy. `llm_router.py:444-447` already supports
  `call_cloud(tools_disabled=True)`.

**Capability floor**: Sonnet. Ollama is excluded entirely: its availability is a
function of GPU contention, and a judge that flips with GPU load is not a judge. The
real line is deterministic-vs-generative, not redaction-vs-adjudication.

**Fail-closed broker, fail-open policy loader.** Opposite directions, on purpose: a
corrupt JSON file must not lock the operator out of their own notes. Note that `check()`
is mechanical, so **unexpired grants keep working at full speed during an API outage**;
only new boundary crossings block.

---

## 7. Task and Job

Unchanged concepts (`chat-tickets-orchestrator-plan-2026-07-15.md`): **Task = ticket**,
**Job = attempt**. Deliberately not merged.

Task fields relevant here (`tasks.py`):

| Field | Meaning | Status |
| --- | --- | --- |
| `status` | todo, doing, in_review, **blocked**, parked, done, cancelled (`:61`) | exists |
| `blocked_reason` | free string (`:22`) | exists, to be generalized into a structured request |
| `touches` | path globs this task writes, "fan-out safety" (`:32`) | **inert: enforced nowhere** |
| `effort` | s/m/l hint | exists |
| `latitude` | override | **NEW** |
| `provenance` | stable dedupe key | exists |
| `acceptance` | reviewer/command/operator | exists |

**A blocked job does not resume; it dies and the Task carries the state.** The Task *is*
the durable workflow state. `tasks.py:783-788` already releases the lease on `blocked`.
Temporal's value is preserving in-memory state across a wait; an LLM job has none worth
preserving (its context is stale by construction at the moment it unblocks, most of it
dead-end reasoning about a now-answered question). Building resume means undoing the
property that prevents freezes.

---

## 8. The two-layer rule (the answer to ask C2)

This is the distinction the whole system rests on:

| Layer | Mechanism | Enforced by | Example |
| --- | --- | --- | --- |
| **Floor** (hard) | `touches`, grants, role write-authority | code: a gate | a triager *cannot* write code |
| **Policy** (advisory) | latitude, skills, learnings, persona | the model, in context | a maker *may* refactor without asking |

An LLM told "do not touch auth/" is not the same as a job that **cannot** write auth/.

**The live bug**: `touches` is documented as fan-out safety (`tasks.py:32`) and read by
no gate. `permissions.py` is explicitly "NOT a wall... no middleware". So **enforced
rules currently have nowhere to run.** The floor exists on paper only. Blast radius must
stay separate from latitude precisely because merging them would either make latitude
enforceable (impossible for "should I use pytest") or make blast radius advisory (which
is the bug we already have).

---

## 9. Rule promotion (the answer to ask B3/B4)

Both rule kinds, with a path between them:

- **Textual rule**: a learning/note/persona line. Model-interpreted. Cheap to write,
  expressive, unenforceable.
- **Programmatic rule**: a grant, a policy predicate, a scope entry. Machine-checkable,
  narrow, tedious to author.

**Promotion**: `learnings.py` dedup already attaches source and timestamp to an existing
entry's provenance, so **provenance length is a free stability counter**. A textual rule
that proves itself across three independent blocks gets *proposed* for promotion to a
programmatic grant.

This inverts the policy-as-code adoption problem: nobody wants to write Rego, so nobody
does. Here nobody writes the policy; it is promoted out of prose that earned it. Rules
are minted **from the block request** (ask B4), never from a separate authoring flow.
There is no rule-editor surface.

---

## 10. The four live defects this schema depends on

Verified by reading, not assumed. Three of them block the operator's asks outright.

| # | Defect | Blocks |
| --- | --- | --- |
| D1 | `dispatch_triggers.sh:329` hardcodes `role == "memory"`; all other assistant agents hit `skip-assistant` at `:344`. Five agents have triggers and never run (`workshop-overseer`, `email-triager`, `plaid-watcher`, and `the bridge agent`/`the bridge agent-commute` which may use their own bridge). | the `triager` entirely (ask D5) |
| D2 | No `_coerce_role` beside `_coerce_depth`/`_coerce_fanout` (`agents_registry.py:75-79`). `role: memoy` silently disables the memory-keeper. | schema integrity |
| D3 | `touches` enforced nowhere. | the enforced floor (ask C2) |
| D4 | `attenuate()` has zero call sites. | every grant narrowing |
| D5 | Project learnings fall back to `global`, injected into every agent (F3, confirmed HIGH). | rule minting (ask B4) |

**Reading of these together**: the platform already has the primitives (`role`,
`touches`, `attenuate`, grants, `blocked`) and they are inert. The gap between the
shipped system and the asked-for system is mostly unfinished wiring, not missing design.

---

## 11. Build order

| Phase | Content | Depends on the identity decision? |
| --- | --- | --- |
| **P0** | Repair: `_coerce_role`; role-dispatch table replacing `:329`; retire `workshop-overseer`; register `~/notes` as a Store; decide enforce-or-delete on `touches` | no |
| **P1** | `latitude` (one string field + cascade key + persona paragraph + panel control; ~a day) | no |
| **P2** | Block loop + rule minting (**prereq: fix the `global` learnings fallback first**) | no |
| **P3** | The mediated broker (last: the only part needing a live cloud model) | **yes** |
| **P4** | Resident agents + templates (a concept *reduction*: the four modes are (trigger) x (verbs), both of which exist by P0; zero agents falls out free) | partly |

P0-P2 survive a "no" on the third identity kind. Only P3 dies with it.

**Default agent set for a brand-new project: zero.** The project loop is not an agent, a
triager over an empty notes folder is a scheduled no-op, and ask A4 blessed zero
explicitly. `tended` (triager + keeper) is the one-click upgrade. Two template groups,
not a catalog.
