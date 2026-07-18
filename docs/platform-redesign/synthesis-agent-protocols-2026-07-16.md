# Synthesis: agent protocols, resource access, and scoped work (2026-07-16)

> **STATUS: PROPOSED. NOT BUILT. Zero code written.**
> The build order P0-P4 in Part 5 is a proposal. No phase has started.
> The gating decision in Part 7 (agent as a third identity kind) is UNANSWERED.
> The four live defects in Part 1 are STILL LIVE and UNFIXED.

Status: reconciles the three thread reports of 2026-07-16 against each other and
against the shipped tree. Every file:line below was verified by the synthesizer or by
the owning report, not assumed. Owns operator asks E5 and F1-F6.

Companion docs:
- `operator-asks-2026-07-16.md` (the ask, verbatim, with stable ask IDs; authoritative)
- `_brief-agent-protocols-2026-07-16.md` (the stage-setting brief)
- `agent-roles-research-2026-07-16.md` (D1-D6, E1-E5; 1401 lines)
- `resource-access-protocol-2026-07-16.md` (A1-A12; 1393 lines)
- `scopedness-and-escalation-2026-07-16.md` (B1-B4, C1-C4; 1573 lines)

## Executive summary

1. The three threads converged, independently, on one spine: **authority is grants, not
   identity**. The access report reached it from the confused-deputy problem; the
   scopedness report reached it from the block rate; the roles report reached it from
   output authority. Three different starting points, one model. That convergence is
   the strongest evidence in the set that the model is right.
2. **One decision gates the whole thread**: is an autonomous agent a third identity kind
   (`agent`), distinct from `operator` and `guest`? Both reports that touch it recommend
   yes. If the answer is no, asks A6 and A7 become unimplementable by construction and
   roughly half the design set collapses. Everything else is downstream.
3. **The build is unblocked by bug fixes, not by features.** Four live defects were
   found by reading, and three of them block the operator's asks outright. The first
   phase of this work is repair, and it is small.
4. **The ask is a concept reduction, not an expansion.** Net new primitives across all
   three reports: one role (`broker`), one identity kind (`agent`), one task field
   (`latitude`), one policy object. Everything else already exists and is either unused,
   unenforced, or hardcoded to a single value.
5. Sequencing across the three reports is not the order they were written. The correct
   order is: repair (P0), then latitude and blocks (P1-P2, cheap, high daily value),
   then the mediated broker (P3, the expensive part), then the resident-agent and
   template layers (P4). The broker is last because it is the only part with a hard
   dependency on a live cloud model.

---

## Part 1: the four live defects (found by reading, verified by the synthesizer)

These are not design proposals. They are bugs in the shipped tree, and three of them
block asks the operator made today.

### D1. The `role` tag is a closed enum of size one, hardcoded in the dispatcher

`scripts/dispatch_triggers.sh:329` is `if a.get("role") == "memory":`. Every other
assistant-runtime agent falls through to `skip-assistant` at `:344`. Verified by
reading `:320-348`.

Consequence, verified by auditing all 15 files in `agents/`: five agents are registered
with triggers and have no path to execution.

| Agent | runtime | role | trigger | reality |
| --- | --- | --- | --- | --- |
| `memory-keeper` | assistant | `memory` | cron | runs |
| `workshop-overseer` | assistant | none | loop | never runs |
| `email-triager` | assistant | none | event | never runs |
| `plaid-watcher` | assistant | none | loop | never runs |
| `masini`, `masini-commute` | assistant | none | loop | never runs via this dispatcher |

Caveat, not overclaimed: `masini` and `masini-commute` have their own tmux bridge
(`~/clawdbot/`) and may be driven entirely outside this dispatcher. `workshop-overseer`,
`email-triager`, and `plaid-watcher` are ghosts: the roster shows them as configured,
and they are skipped every tick.

**This blocks D5 directly.** The operator's note-triage ant, written today, would
register cleanly, appear in the roster, and never run.

### D2. `role` is unvalidated, unlike its siblings

`agents_registry.py:75-79` defines `_coerce_depth` and `_coerce_fanout`. There is no
`_coerce_role` (verified: grep returns the two coercers and no third). So `role: memoy`
is a valid write that silently disables the memory-keeper, with no error at write time
and no error at dispatch time. A closed enum wearing a free-form field's clothes.

### D3. `touches` is enforced nowhere

`tasks.py:32` documents `"touches": [str]` as "path globs this task writes (fan-out
safety)". Verified reference sweep across `backend/` and `scripts/`: it is stored
(`tasks.py:262`), accepted (`:690`, `:717`), and threaded through scope and dedup
(`task_scope.py:152`, `task_dedup.py:344,391,414`). **No gate reads it.** No hit in
`ralph.sh`.

The advisory-vs-enforced gap the operator asked about in C2 is therefore not a
hypothetical design question. It is a live bug: the field that names a task's blast
radius is decorative.

### D4. `attenuate()` is written and never called

`permissions.py:647` defines `attenuate(...)`. Verified: zero call sites anywhere in
`backend/` or `scripts/`. The function that narrows a grant, which is exactly the
primitive both the access and scopedness reports build on, already exists and is dead
code.

### D5 (prerequisite, from the scopedness report). Project learnings fall back to `global`

Flagged as F3 in `review-findings-2026-07-15.md` and confirmed HIGH. Project-scope
learnings currently fall back to `global`, which is injected into every agent's context.
Minting rules from blocks (ask B4) into that bug would accelerate a confirmed defect:
every project-specific rule would leak into every agent everywhere. This must be fixed
before rule-minting ships, and it is why P2 has a prerequisite.

**Reading of the four defects together**: the platform has the primitives for the
operator's ask already (`role`, `touches`, `attenuate`, grants, `blocked`), and they
are inert. The gap between the shipped system and the asked-for system is much smaller
than the ask implies, but it is gated behind wiring that was never finished.

---

## Part 2: the convergent spine

The three reports were briefed separately and did not read each other. They agree:

- **Roles report**: taxonomy built on *output authority*, not job title. A role that can
  only propose a ticket differs in kind from one that can edit code.
- **Access report**: an agent's authority is *exactly the grants it holds*. Zero ambient
  authority. `operator` is not a person, it is an assignment of authority to a running
  process.
- **Scopedness report**: `latitude` is *the block rate*. Low latitude means "when the
  ticket does not say, ask"; high means "decide and report".

These are one idea in three vocabularies: **what an agent may do is a property of its
grant, not of what it is called.** The role names the authority shape; the grant carries
the authority; latitude sets what it does at the edge of that authority.

Two invariants from the access report carry the safety argument, and both are
structural rather than aspirational:

- **I1: the LLM can only attenuate, never amplify.** Every broker verdict narrows a
  maximum the operator declared programmatically. A fully injected broker yields a grant
  inside `topics_allow`, which the operator already blessed. This is what makes an LLM
  safe in the decision path at all, and it is why D4 (`attenuate()` uncalled) is the
  single most load-bearing dead function in the tree.
- **I2: the broker never reads the data it adjudicates.** Its inputs are identity, URI,
  selector, purpose, policy. `llm_router.py:444-447` already supports
  `call_cloud(tools_disabled=True)`.

---

## Part 3: the central decision (ask A6/A7 lives or dies here)

**Is an autonomous agent a third identity kind?**

Today: `Identity.kind` is `operator` or `guest` (`permissions.py`), `authorize(operator,
...)` is always allow, and gating exists only for the guest/shared boundary. That posture
is the operator's no-defensive-cruft rule encoded in code, and it is correct as written.

The recommendation from both reports that touch it: **yes, a third kind (`agent`) with
zero ambient authority.**

The argument that makes it consistent with the hard rule rather than a violation of it:

1. `operator` is not a description of the human. It is an assignment of authority to a
   *running process*. The hard rule forbids gating the human. It does not forbid
   bounding a program that ingests hostile input.
2. An unattended job reading note bodies and web pages is Hardy's confused deputy
   exactly. The deputy holds the operator's authority and takes instructions from
   attacker-controlled text.
3. The ask settles it. A6 and A7 request a gate in front of agents. An
   operator-identity agent makes them unimplementable by construction, not merely
   awkward.

The reason this does not regress the operator's reach, and does not become cruft: **the
dispatcher mints a generous in-scope grant set at job start.** A job on `project:acme`
begins already holding read/write over its own project notes. The gate is on the job's
*perimeter*, not inside its own work, and never on the operator. That is what keeps A9
(the goal is that agents and apps can share) alive rather than strangled.

**If the operator rejects this**: A6, A7, and most of the access report collapse; agents
remain the operator; the mediated tier cannot exist. The scopedness report notes that
only its Phase E5 depends on it, so latitude and the block loop (P1-P2 below) survive a
"no" intact. That is a useful property: the cheap, high-value half of this work does not
depend on the contested decision.

---

## Part 4: cross-report reconciliation

Where the reports touch, they mostly compose. The seams worth naming:

### 4.1 The `broker` role vs the mediated broker

The roles report reserves `broker` in the role enum and recommends **shipping no
runner** (it is demand-only, so no dispatcher path is needed). The access report designs
the broker in full. These are consistent, not contradictory: the broker is invoked
in-band on a request, not swept by the dispatcher. **Resolution: reserve the enum name
in P0, implement the adjudication call in P3, never give it a trigger.**

### 4.2 latitude vs the block loop

The scopedness report's finding that thread B is the runtime behavior of thread C's dial
is the cleanest structural claim in the set. Blocks are not a subsystem beside
scopedness; they are what low latitude *does*. **Resolution: build them as one phase
(P2), not two.** A block loop without latitude has no dial; a latitude field without
blocks has no effect.

### 4.3 The triager and the blocked queue

The roles report's `triager` turns notes into tickets (D5). The scopedness report turns
blocks into tickets with structured requests (B1). Both produce operator-facing work
items from an agent-detected condition. **Resolution: same surface, same primitive.**
The Work surface (Tickets|Runs) gains a Review tab, per the scopedness report, and
triager output and block requests both land there. This satisfies B2's "review and
change access or approve request or update rules" from one place, and honors
`orchestration-and-autonomy.md` §7.3's pre-commitment to no approval queues: the Review
tab sits *behind* work that already stopped on its own. Removing it lets no blocked work
proceed. It is a dashboard, not a gate.

### 4.4 Policy location: agreed

The access report: policy lives outside the resource it protects, never in a folder
sidecar, because an agent with `write` on the folder could rewrite its own gate. Nothing
in the other reports contradicts this. **Resolution: adopt.** This is also why the
`~/notes` vault registration (A3) is a Store-registration problem, not a
dotfile-in-the-folder problem.

### 4.5 A conflict worth flagging: `summarized`

The access report argues `summarized` is the *least* safe response mode (novel tokens,
no mechanical verifier possible; verbatim/filtered/redacted all satisfy output ⊆ input
and are assertable in deterministic code) and asks in its Q6 whether it should ship at
all. The roles report does not weigh in. **Resolution: do not ship `summarized` in the
seed set.** A8 is explicitly open-ended ("or other"), so the set is extensible and
`summarized` can be added later behind a verifier or an operator opt-in. Shipping the
one mode we cannot check, in the first cut, inverts the risk ordering for no gain.

---

## Part 5: sequenced build order

This is the reconciled order across all three reports. It is not the order the reports
were written, and it deliberately puts the expensive, contested part last.

### P0: repair (small, unblocks everything, no new concepts)

- `_coerce_role` in `agents_registry.py` beside `_coerce_depth`/`_coerce_fanout`, with
  a closed enum (`maker`, `reviewer`, `triager`, `keeper`, `broker`, `None`) and `None`
  valid. Fixes D2.
- Generalize `dispatch_triggers.sh:329` from `role == "memory"` to a role-dispatch
  table. Fixes D1 and unblocks D5 (the note-triage ant).
- Retire `workshop-overseer` (recommended by the roles report: it has never run, and the
  overseer function is a `triager` at workshop scope, not a distinct role).
- Register the `~/notes` vault as a Store. Fixes A3's precondition: it is the only
  resource with real folders, and per-folder policy is unsatisfiable on it today.
- Decide D3: either enforce `touches` or delete it. Do not leave a documented safety
  field that is decorative.

P0 is mostly deletion and one dispatch table. It ships without any of the contested
decisions being made.

### P1: latitude (the scopedness report estimates a day)

One string field, cascading through the existing invocation > task > scope resolution
(`orchestration-and-autonomy.md` §7.1, no new mechanism needed), plus a persona
paragraph and a panel control. Reconciles with existing `autonomy`, `depth`, `fanout`,
and effort rather than sitting beside them. Answers C1, C3, C4.

### P2: the block loop and rule minting (prereq: fix F3 first)

Structured block requests (B1: "blocked by permission or anything", so the mechanism is
general, not permission-specific), the Review tab, the operator's response verbs, and
rule minting from the block itself (B4). Job resume is **rejected**: the Task is the
durable state, `tasks.py:783-788` already releases the lease on `blocked`, and an LLM
job's context is stale by construction at the moment it unblocks.

**Prerequisite**: fix the project-learnings `global` fallback (D5 above / F3 in the
review findings) before minting textual rules, or every project rule leaks into every
agent.

**The best idea in the set lands here**: rule promotion. `learnings.py` dedup already
attaches source and timestamp to an existing entry's provenance, so provenance length is
a free stability counter. A textual rule that proves itself across three independent
blocks gets *proposed* for promotion to a programmatic grant. This inverts the
policy-as-code adoption problem: nobody writes Rego, the policy is promoted out of prose
that earned it. It is also the cleanest answer to B3's "programmatic rules or textual
rules": both, with a path between them.

### P3: the mediated broker (the expensive part, depends on Part 3's decision)

The three tiers (open / gated / mediated), the request and verdict envelopes, the
capability floor, the response modes, and the Finance generality proof. Last because it
is the only part with a hard dependency on a live cloud model, and because P0-P2 deliver
daily value without it.

Note the operational finding, which today's session proved rather than hypothesized:
`check()` is mechanical, so unexpired grants keep working at full speed during an API
outage. Only *new* boundary crossings block. During the 529 storm that killed five
subagents in this very session, a mediated system built this way would have kept
serving every in-flight job and blocked only new cross-boundary requests. That is the
correct failure shape.

### P4: resident agents and templates

A4 is a concept *reduction*: all four resident-agent modes (on-call, autonomous,
write-only, read-only) decompose into (trigger mode) x (verb set), both of which exist
by P0. Zero resident agents falls out free, which is the tell that the decomposition is
right. Templates fork rather than link, with `template: <slug>@<version>` recorded and
no sync engine. Default set for a new project: **zero agents**.

---

## Part 6: what this set deliberately does not build

Named so that silence is not mistaken for oversight:

- No conductor agent. The conductor is roughly 20 lines of Python; the overseer function
  is a `triager` at workshop scope.
- No durable-execution engine, no job resume. The Task is the durable state.
- No policy DSL (Rego, Cedar). `mint_grant` + `check` already are the policy engine.
- No approval queues that gate work. The Review tab sits behind work that already
  stopped.
- No audit journals of block decisions. Evidence (`tasks.py:29`) already records what is
  needed for the escalation loop, and an audit journal is exactly the cruft the hard
  rule forbids.
- No trust ladders, agent performance scoring, or zero-trust defaults. Multi-tenant
  machinery for a single-operator system.
- No `summarized` response mode in the seed set (Part 4.5).
- The ant metaphor, rejected: response-threshold models are population-scale statistics
  assuming thousands of free agents and no operator. What survives is stigmergy proper,
  which was already the posture: coordinate through shared state, never agent-to-agent
  messaging.

---

## Part 7: the decisions the operator owns

Consolidated from 27 open questions across the three reports. Ranked by blast radius.
Everything else has a recommended default in its owning report and can proceed without
a decision.

1. **Is an autonomous agent a third identity kind (`agent`)?** (access Q1, scopedness
   Q4.) Both reports recommend yes. **This is the one that matters.** A6 and A7 die on a
   no. P0-P2 survive a no; P3 does not.
2. **Enforce `touches`, or delete it?** (D3.) A documented safety field that no gate
   reads is worse than no field. Recommend enforce, since C2 asked for exactly this and
   the field is already threaded everywhere it needs to be.
3. **Retire `workshop-overseer`, `email-triager`, `plaid-watcher`?** They have never
   run. Recommend retire the overseer (its function is a workshop-scope triager);
   decide whether the other two were wanted, because P0 would bring them to life.
4. **Ship `summarized`?** Recommend no, per Part 4.5.
5. **Sonnet floor for adjudication, or allow Haiku for low-sensitivity stores?**
   (access Q2.) Recommend the Sonnet floor. Ollama is excluded entirely: its
   availability is a function of GPU contention, and a judge that flips with GPU load is
   not a judge.

## Part 8: coverage

Every ask ID in `operator-asks-2026-07-16.md` is claimed by a named report section. The
audit table in that document's Part 4 is now completed. One ask is worth noting as
resolved-by-reading rather than resolved-by-design: **"denounced"** (recorded as a
probable dictation artifact) is read by the roles report as "de-duped" and reported
satisfied by the `link` behavior either way. If the operator meant something else, that
is the one place a re-read is cheap.
