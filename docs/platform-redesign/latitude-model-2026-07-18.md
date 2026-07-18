# Latitude model (2026-07-18)

- Status: accepted
- Depends on: entity-model-2026-07-18.md (section 8.2), autonomy-model-2026-07-18.md
- Source: operator asks C1/C2/C3/C4 (operator-asks-2026-07-16.md)
- Reasoning detail: scopedness-and-escalation-2026-07-16.md (the full exploration; this doc is its distilled output)

---

## The key distinction: scopedness is not settable, latitude is

"A task or ticket may be well-scoped or not" (operator ask C1) names a real phenomenon
but cannot be a dial. How well-scoped a ticket *is* is a property of the ticket's text —
an observation, not a setting. A `scopedness: high` flag on a vague ticket would be a lie
in JSON; the worker would read the lie and proceed as though the ticket said things it
does not say.

What is settable is what the worker does **when the ticket runs out of specification**. Every
ticket underspecifies something; that is what a ticket is. **Latitude** is the standing policy
for the underspecified residue. The operator dials latitude; scopedness is an input they
observe.

---

## 1. The latitude setting

### 1.1 Definition

> **Latitude**: what the worker is authorized to decide when the ticket does not say.

Low latitude means "when the ticket does not say, block and ask". High latitude means "when
the ticket does not say, decide and report". Latitude is therefore the primary variable that
governs how often the escalation loop fires for a given scope or ticket.

### 1.2 Three presets

```
LATITUDES = ("strict", "standard", "open")
DEFAULT_LATITUDE = "standard"
```

| Preset | Rule at the edge of the spec | Block rate | Use when |
| --- | --- | --- | --- |
| `strict` | Do what the ticket says. Any decision the ticket does not specify raises a Block. | high | Hands-on work; unfamiliar areas; a ticket written precisely and meant literally |
| `standard` | Decide **reversible** things; record the decision as evidence. Block on **irreversible** decisions, anything outside `touches`, and any hard gate. | low | Default; most work |
| `open` | Pursue the goal. Decide freely within the enforced floor. Surface decisions in evidence at the end. | rare | Goal-only tickets; "figure it out" work |

**The `standard` reversibility criterion** (the load-bearing rule):

> Reversible means a later `git revert` or a one-line edit fully undoes it, with no external
> side effect and no third party having observed it.
>
> Reversible: adding a file, choosing a test framework, picking a variable name.
> Not reversible: adding a dependency to a lockfile others install, pushing a branch,
> sending a message, deleting a store, writing `auth/`.

### 1.3 What latitude is NOT

- **Not `depth`** (`agents_registry.py:69`). Depth is an effort budget (iterations, model
  selection). Latitude is decision rights. `strict + deep` (think hard, then ask) and
  `open + quick` (do the obvious thing, don't bother me) are both coherent and both wanted.
  Collapsing them would make "I want it to think hard" imply "I want to be interrupted."
- **Not `autonomy`** (`triggers.py:214`). Autonomy controls *when a sweep starts*; latitude
  controls *how much room the sweep has once running*. Both are needed; `continuous + strict`
  (unattended overnight loop that asks at every ambiguity) is inexpressible without both.
- **Not `touches`** or grants. Those are the enforced floor (section 4). Latitude modulates
  the advisory layer above the floor. `open` does not grant write access to `auth/`. It
  means "stop asking me about reversible choices", not "stop asking me about anything."
- **Not "specification tightness"**. A step-level spec is a ticket with steps in it, or a
  parent with subtask children (`tasks.py:15`). Latitude does not duplicate that.

---

## 2. Inheritance cascade

### 2.1 Resolution order

```
invocation  >  ticket  >  scope  >  DEFAULT_LATITUDE ("standard")
```

First non-null wins. Identical structure to `depth` and `fanout`. No new resolution
mechanism: the existing cascade helper gains one more key.

**Null means inherit; it does not mean `standard`.** DEFAULT_LATITUDE applies only when
the entire chain is null. A project set to `strict` is not silently undone by a ticket
record that stores its own default. This is why the serialization rule is **emit only
when non-default** (matching `agents_registry.py:374-381` for depth/fanout): a ticket with
no latitude field inherits; a ticket with `latitude: standard` explicitly overrides.

### 2.2 Storage locations

| Level | Where | Shape |
| --- | --- | --- |
| Scope (project) | project registry entry, beside `autonomy` | `"latitude": "strict"` |
| Scope (agent) | agent definition frontmatter, `agents_registry.py:160-163` parse, `:374-381` serialize | `latitude: strict` |
| Ticket | `.tasks.json`, beside `effort` (`tasks.py:34`) | `"latitude": "open"` |
| Invocation | sweep argv, beside the depth override | `--latitude open` |

The resolved value is recorded on the Run for provenance. It is not settable on the Run
(a Run is an attempt, not a configuration site).

### 2.3 Agent inside a project (the one subtlety)

An agent scope and a project scope are peers in the general cascade. When an agent runs
*inside* a project (`project_slug` on the agent record, `agents_registry.py:164`), the
project's latitude takes precedence over the agent's own latitude at runtime.

Latitude is a property of **the work and where it lands**, not of the worker's
personality. A `strict` project means "work in this tree is hands-on"; an agent with
`latitude: open` in its frontmatter must not import its permissiveness into a tree the
operator marked strict. If a specific agent genuinely needs different latitude inside a
project, that is a per-ticket override.

---

## 3. How a run's guardrails change with latitude

### 3.1 The block rate formula

```
block_rate  ~  f(latitude, ticket_scopedness, skills_and_rules_coverage)
```

The operator sets latitude. The ticket text and the skills corpus set the rest. If a scope
at `strict` latitude blocks constantly, the fix is usually to add a skill, not to loosen
latitude (section 4).

### 3.2 Block behavior per preset

When a worker hits an underspecified decision point, the resolved latitude determines
what happens:

| Decision type | `strict` | `standard` | `open` |
| --- | --- | --- | --- |
| Any ambiguity | Block immediately | — | — |
| Reversible choice | Block | Decide; record in evidence | Decide; surface at end |
| Irreversible choice | Block | Block | Block (see 3.3) |
| Outside `touches` | Block | Block | Block |
| Hard gate (enforced floor) | Block (never auto-resolved) | Block (never auto-resolved) | Block (never auto-resolved) |

A "Block" means the worker writes a structured `awaiting` record (`tasks.py:23`, widened
shape in entity-model-2026-07-18.md section 4.3), ends the current Run, and parks the
Ticket as `blocked`. The Block surfaces in the Work page "review" tab. A new Run begins
when the operator resolves it.

The worker prompt (injected by the sweep at `scripts/ralph.sh`) includes a per-latitude
paragraph that specifies the rule at the edge of the spec and, for `standard`, the
reversibility definition word-for-word.

### 3.3 Auto-resolve at deadline

A Block raised at `standard` or `open` may carry:
- `awaiting.default`: what the worker would decide if nobody answers
- `awaiting.deadline`: an ISO timestamp; the dispatcher cron evaluates it on its normal tick

At deadline, if the decision is **reversible** and a `default` is present:

| `latitude` | At deadline |
| --- | --- |
| `strict` | Stay blocked. The operator chose hands-on; guessing violates the choice. |
| `standard` | Take the `default`; record it as evidence; mark `auto_resolved`. Surface in Review for post-hoc confirmation. |
| `open` | Same as `standard`, with a wider reversibility bar. |

**Hard gates never auto-resolve at any latitude.** If `check_action` blocked it, a timer
cannot unblock it. This property is what makes auto-resolve safe to have at all.

Default deadline: `strict` — none; `standard` — 4 hours; `open` — 1 hour. (The shorter
deadline at `open` reflects that an open-latitude worker is more likely to have a
sensible `default` and less likely to block legitimately in the first place.)

### 3.4 The enforced floor (unchanged by latitude)

Latitude modulates the advisory layer only. The enforced floor — the set of predicates
the harness evaluates outside the model's control — does not move with latitude:

- writes outside the ticket's `touches`
- network egress (hook-level check)
- `git push`, branch deletion, force operations
- dependency and lockfile mutation
- reading or writing a Store the Run holds no Grant for (`permissions.py:710`)
- spawning subagents when `fanout: off` (`agents_registry.py:70`)

These produce a Block regardless of latitude preset, and the Block is never auto-resolved
(3.3). The model cannot talk past them because they are evaluated outside the model's
control (`gate.check_action`, proposed in scopedness-and-escalation-2026-07-16.md section
5.3, to be built in Phase E5).

---

## 4. Latitude and skills/rules

### 4.1 Two kinds of rule (the central distinction)

> An LLM told "do not touch `auth/`" is not the same as a job that **cannot** write `auth/`.

Every rule in the system is consciously one of:

**Advisory**: text placed in the model's context. Learnings (`learnings.py`), knowledge
notes (`knowledge.py`), skills (`skills.py`), persona lines. The model usually complies;
it may not; no enforcement happens.

**Enforced**: a predicate the harness evaluates outside the model's control (section 3.4).
Cannot be talked past; does not require the model to know about it to hold.

The test for which kind a rule needs: *Can this rule be decided by a pure predicate over
(identity, action, resource, context) with no LLM in the loop, AND is the action
irreversible or secret-adjacent?* If yes — enforce. If the rule requires judgment ("prefer
table-driven tests") or is about approach rather than access — advisory.

### 4.2 Skills reduce block rate at fixed latitude

A skill answers a class of questions *before* they become Blocks. "How we write tests
here" as a SKILL.md converts twenty potential `strict`-latitude blocks into zero, without
changing latitude. This is the correct reading of operator ask C2 ("we want skills and
rules to be sure they're well-scoped"): **skills and rules raise effective scopedness,
which reduces block rate at fixed latitude**.

The corollary: at `strict` latitude, a scope that blocks constantly is almost always
missing a skill, not set to the wrong latitude. The answer is to write the skill. If you
lower latitude instead, you trade block reduction for autonomous decisions in exactly the
area where the operator wanted to be hands-on.

UI implication: the AutonomyPanel's latitude control carries the hint:
> "If a strict scope blocks often, add a skill rather than loosening it."

### 4.3 Rule promotion: prose that proves itself becomes enforced

Textual rules (learnings) that survive independently across multiple Blocks accumulate
provenance in `learnings.py`'s existing dedup mechanism (section 7.4 of the exploration
doc). A rule with ≥ 3 independent sources **and** a `proposal` field from an escalation
(meaning it names a resource + verb and is checkable) is proposed for promotion to a
programmatic Grant in the Work page "review" tab.

Promotion path: operator confirms → `mint_grant()` (`permissions.py:616`) from
`awaiting.proposal` → learning gains `promoted: <grant_id>` → learning stays as rationale
(enforced rules without context produce mysterious failures).

This makes the cheap path (type prose) the *entry point* to the correct path (enforced
predicate), so the operator never has to decide up front whether a rule deserves enforcement
— they discover it from use.

Prerequisite: the learnings scope-key mismatch (F3 in review-findings-2026-07-15.md)
must be fixed before any rules are minted into the learnings store. Minting into a
mis-scoped store that silently falls back to global would accelerate a confirmed HIGH bug.

---

## 5. Implementation phases

These phases follow the sequencing in scopedness-and-escalation-2026-07-16.md section 10.
They are ordered here as a prioritized build list, not a timeline.

**E0 — F3 prerequisite [blocking for E4]**: Fix the learnings scope-key mismatch so
per-project rules land in the correct scope, not global. `scripts/project_ralph.sh:194`,
`scripts/ralph.sh:331-347`. Small fix; no latitude behavior until this is shipped.

**E1 — latitude field [additive; parallel-OK]**: Add `LATITUDES`, `DEFAULT_LATITUDE`,
`_coerce_latitude` beside `DEPTHS/FANOUTS` in `agents_registry.py:65-72`. Parse/serialize
(non-default only) at `:160-163`/`:374-381`. Add `latitude` to `tasks.py` beside `effort`
(`:34`, create paths `:690,717`, read echo `:262`, `update()` `:737`). Extend the cascade
resolver with one key. Adds no behavior; makes the field available.

**E2 — latitude does something (advisory) [sequential after E1]**: Inject a per-latitude
persona paragraph into the worker's context from `backend/services/chat_context.py`
(cache-stable prefix). Three variants: `strict` (ask at every edge), `standard` (decide
reversible, block irreversible — include the reversibility definition verbatim), `open`
(decide freely, surface decisions at end). Wire `--latitude` through `scripts/ralph.sh`.
At the end of E2, the operator can set a scope to `strict` and get a worker that asks
instead of guessing. This is C1+C3 delivered before any escalation UI.

**E3 — structured escalation [sequential after E1; parallel-OK with E2]**: Widen the
`awaiting` field in `tasks.py:23` to the shape in entity-model-2026-07-18.md section 4.3.
Add the six operator response verbs to the task action endpoint. Add `restore_attempt`
on the operator-answered unblock path (capped at two restores per ticket; past that,
repeated blocking is a signal). Additive: existing `TaskPanel.tsx` and `JobDetail.tsx`
wiring stays intact.

**E4 — review tab [sequential after E0 + E3]**: Add a fourth "review" tab to Work.tsx.
Build `ReviewQueue.tsx` from `Card + Stack + Row + StatusPill + ConfirmDialog`, grouped
by `dedupe_key`, six verbs, four list states. Wire Grant standing to `mint_grant()`
scoped to the ticket's scope (not global, per E0). Add latitude `Segmented` to
`AutonomyPanel.tsx` beside depth. Show inherited latitude in the ticket drawer with
source label.

**E5 — enforced floor [sequential after E3; largest phase]**: Build `backend/services/
gate.py` with `check_action(job_ctx, action) → (allow|block, reason, escalation|None)`.
Wire via `PreToolUse` hook (builder runtime) and action dispatcher in `routes/chat.py`
(assistant runtime). Use `attenuate()` (`permissions.py:647`) to narrow job tokens from
`touches`; evaluate with `check()` (`:710`). This is when `touches` becomes real
enforcement rather than advisory annotation (defect D3 in entity-model-2026-07-18.md).

**E6 — promotion and hygiene [sequential after E4 + E5]**: Promotion proposal cards from
`learnings.py` provenance. Disuse expiry (90-day surface). Near-dupe warning at rule mint
time (reusing the Jaccard check already in `learnings.py:31-35`).

Parallelization: E0 and E1 start together. E2 and E3 both follow E1, are independent of
each other. E4 needs E0+E3. E5 needs E3, is independent of E4. E6 is last. The critical
path to C1+C3 is E1 → E2.

---

## 6. Open questions and defaults

| # | Question | Recommended default |
| --- | --- | --- |
| 1 | Is `latitude` the right name? Alternatives: `discretion`, `strictness`, `autonomy_scope` | `latitude`. Cheap to rename before E1; expensive after E4. `strictness` reads as enforcement (section 3.4), which is the confusion to avoid. |
| 2 | Three presets or two? | Three, matching `DEPTHS` shape. `standard` carries the reversibility rule that most of the design leans on. If nobody consciously selects it, it is still the right *default*, so the cost of being wrong is zero. |
| 3 | Should `standard` auto-resolve reversible blocks at deadline? | Yes, 4 hours, reversible-only, never for enforced gates, always surfaced post-hoc. The alternative is an overnight loop idling seven hours on "which test framework". A misbehaving scope gets set to `strict`. |
| 4 | Should the Discord nudge fire per block or per scope per hour? | Per scope per hour. Ten blocks in a minute is one problem; it should ping once. |
| 5 | `restore_attempt` on operator-answered unblock? | Yes, capped at two per ticket. Past that, repeated blocking is a signal, not noise. |
| 6 | Should promotion (rule → grant) be automatic? | Proposed, never automatic. An enforced rule minted without the operator looking is defensive cruft arriving through the back door. The operator sees the evidence and says yes. |

---

## 7. What this document deliberately excludes

- A separate checkpoint-frequency knob. Derived from latitude; the one casualty is a
  time-based progress ping, which is the Runs tab's job.
- A `propose-only` preset. Already expressible as `depth + acceptance: operator`.
- Seven delegation levels (Appelo's Delegation Poker). Three matches DEPTHS; seven is
  an exhaustive taxonomy for one operator.
- A policy DSL (Rego, Cedar). `mint_grant + check` in `permissions.py` already is the
  policy engine.
- A durable-execution engine for blocking jobs. The Ticket is the durable state; a new
  Run re-reads it (entity-model-2026-07-18.md section 5.1).
- Trust ladders or agent performance scoring. Multi-tenant governance; violates the
  single-operator hard rule.
- A rule-editor surface. Rules are minted from Blocks (B4); listed read-only in Settings.
