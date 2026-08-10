# Work scopedness as a setting, and the blocked/escalation loop (2026-07-16)

> **STATUS: PROPOSED. NOT BUILT.** No code written. `latitude` does not exist as a
> field. `touches` is still enforced nowhere. The project-learnings `global` fallback
> (F3) is still unfixed and is a hard prerequisite for rule minting.

Owns asks **B1-B4** and **C1-C4** from `operator-asks-2026-07-16.md`. Companion to
`resource-access-protocol-2026-07-16.md` (thread A) and
`agent-roles-research-2026-07-16.md` (threads D/E).

## Executive summary

Six claims, each defended below.

1. **Scopedness is not a setting. Latitude is.** The operator flagged that they had
   not found the name for the axis (C1: "better phrased I mean a task or ticket may
   be well scoped or not"). The reason the name would not come is that two different
   things are fused in it. How well-scoped a ticket *is* describes the ticket text:
   it is an observation, an input quality, not something you can dial. What you can
   dial is what the worker does **when it reaches the edge of the spec**. Name that
   `latitude`. Section 3.
2. **Latitude is the block rate.** Low latitude means "when the ticket does not say,
   ask" (escalate). High latitude means "when the ticket does not say, decide and
   report". This makes thread C and thread B **one mechanism, not two**: latitude is
   the dial that governs how often the escalation loop fires. That is the central
   finding of this report and it is why these two threads belong in one document.
3. **One new knob, not four.** Of the four candidate axes, specification tightness is
   a property of the ticket (not settable), checkpoint frequency is *derived* from
   latitude, and blast radius already exists as `touches` (`tasks.py:32`) plus grants
   (`permissions.py:616`). Only latitude is new. Section 3.3.
4. **Inheritance is already solved; do not build a second cascade.**
   `orchestration-and-autonomy.md` section 7.1 already fixes the cascade at
   **invocation > task > scope**. `latitude` joins `depth` and `fanout` at exactly
   those levels, in exactly those files. C4 is answered by reuse. Section 4.
5. **`touches` is documented as a safety mechanism and is enforced nowhere.** Verified:
   it is stored, echoed, and passed (`tasks.py:32,262,690,717`; `task_dedup.py:344,391,414`;
   `task_scope.py:152`) and there is no read of it in any gate or in `scripts/ralph.sh`.
   The comment calls it "path globs this task writes (fan-out safety)". It is
   currently a comment. This is the advisory-vs-enforced gap (C2) sitting in the
   codebase already. Section 5.
6. **The best idea in this report: rule promotion.** An operator answering a block can
   mint a textual rule or a programmatic one (B3). A textual rule that proves stable
   gets **promoted** to a programmatic one. `learnings.py` already accumulates the
   evidence for free (dedup attaches source and timestamp to an existing entry's
   provenance list), so the stability counter exists and needs no new store. Promotion
   plus expiry is also a partial fix for the already-confirmed F3 global-pollution bug.
   Section 7.

And one decision that falls straight out of the model: a blocked job **dies**. The
task parks and a **new** job picks it up. This is not a compromise, it is what
Job=attempt / Task=ticket already means, and `tasks.py:781-782` already implements the
mechanical half of it. Section 6.4.

---

## Part 1: audit of current state (what exists, file:line)

### The blocked substrate is two thirds built

- `STATUSES = {"todo", "doing", "in_review", "blocked", "parked", "done", "cancelled"}`
  (`tasks.py:61`). `blocked` is a first-class status, and `parked` is a documented
  dead-letter for attempt-cap exhaustion (`tasks.py:57-60`).
- `blocked_reason: str | None` (`tasks.py:22`), settable via `update()`
  (`tasks.py:737,799-800`), echoed on read (`tasks.py:252`), set by the reclaim path
  on a dead worker (`tasks.py:629-636`), and mapped to the `!` box in the TODO.md
  export (`tasks.py:339,457`).
- **`awaiting: {"question": str, "since": iso} | None` (`tasks.py:23`) already exists**
  and is described as "operator input this task waits on". It is patched through
  `update(awaiting=...)` (`tasks.py:743,808-811`) and cleared automatically on any
  transition to a non-waiting status (`tasks.py:781-782`).

This last point is the important one. `awaiting` is already *almost* the structured
escalation request this report needs. It is a `question` string, which is enough for
"ask the human something" and not enough for "ask for a specific grant on a specific
resource". The design below **generalizes `awaiting` rather than adding a sibling
field**, which keeps the shipped UI wiring intact (see below).

**What is missing** is exactly what the brief says: the escalation loop and the
operator review surface on top of it. A block today is a string a human reads.

### The claim-release rule already makes a blocked job free its slot

`tasks.py:783-788`, on any transition out of `doing`:

> Any transition out of `doing` releases the claim: a task that is waiting
> (blocked/in_review), released (todo), parked, or finished is NOT being worked, so it
> must not hold a lease/pid. This is the single most important detail for "the whole
> system never freezes" (2d).

So `blocked` already clears `lease_until` and `claim_pid`. The substrate already
assumes a blocked task is **not** an in-flight job parked mid-execution. It assumes
the job is gone. Section 6.4 argues this is correct and should be made explicit rather
than reversed.

### Attempts already punish block-looping, for free

`attempts` is bumped at claim time (`tasks.py:622-623` comment: "attempts was already
incremented at claim time"), and a task at its cap is `parked` with a reason rather
than retried (`tasks.py:627-633`, `RECLAIM_ATTEMPT_CAP` at `tasks.py:72`). A job that
blocks, gets unblocked, and blocks again burns one attempt per cycle and eventually
parks itself. This is an existing, unplanned defense against the "job games the block"
condition (Section 8.5). It needs no new machinery, only to be noticed.

### The autonomy cascade already exists and is already three-level

`orchestration-and-autonomy.md` section 7.1, "Three levels of granularity, one preset",
fixes the override cascade as **invocation > task > scope**:

- **Scope level** (project registry entry or agent-definition frontmatter):
  `autonomy` preset, `depth`, `fanout`, `limits`.
- **Task level** (override): `max_iterations`, `acceptance`, `effort`, `touches`.
- **Invocation level** (override): `--force`, a one-off cap, a depth override.

In code:

- `PRESETS = ("off", "on-demand", "scheduled", "continuous")` (`triggers.py:214`),
  with a deterministic expansion to triggers and a back-compat shadow of the old
  `autonomy.mode {off, manual, continuous}` (`triggers.py:216-260`). `continuous`
  expands to a **bounded** loop: `max_iterations` + budget + `completion_gate`
  (`triggers.py:190-207`, `triggers.py:1-22`).
- `DEPTHS = ("quick", "standard", "deep")`, `FANOUTS = ("off", "read-only")`,
  `DEFAULT_DEPTH = "standard"`, `DEFAULT_FANOUT = "read-only"`
  (`agents_registry.py:65-72`). Parsed into the record (`agents_registry.py:160-163`)
  and re-serialized only when non-default, so a fresh definition file is not littered
  with `depth: standard` noise (`agents_registry.py:374-381`).
- `role` is explicitly free-form: "a free-form tag (e.g. 'memory', 'builder',
  'researcher') that TYPES an agent without changing how it is assigned work"
  (`agents_registry.py:61-64`).

**C4 is therefore not an open design question about mechanism. It is a question about
which knob.** The cascade exists, is documented, is implemented, and has two knobs
riding it already.

### The authz spine, and its posture

`permissions.py` (733 lines). The module docstring is unambiguous and is the operator
hard rule encoded:

> `authorize(operator, ...)` is ALWAYS "allow" (never "deny"). ... Gating
> ("deny"/"confirm" for safety) exists ONLY for the external/shared boundary.

Primitives that matter here:

- `Identity.kind` in {`operator`, `guest`}; `resolve_identity` defaults to operator
  (`permissions.py:96`), guest auth an intentional unbuilt seam.
- `_VERBS = ("read", "write", "append")` (`permissions.py:56`).
- `mint_grant()` (`:616`), **`attenuate()` (`:647`)**, `revoke()` (`:680`),
  `check(token_or_grant, store_uri, verb)` (`:710`), `_caveat_expired` (`:697`).
- Resource grants live in a separate `resource-grants.json` (`permissions.py:49-55`).

`attenuate()` is the sleeper primitive. Macaroon-style attenuation is exactly the
mechanism for "a job runs with less authority than the operator who launched it", and
it is already written.

**There is no middleware.** The docstring says this module is "a CONSULTED source of
truth, NOT a wall in front of existing routes". Nothing calls it unless a caller
chooses to. This is the single most important fact for Section 5: **we currently have
no enforcement point at all**, only an oracle that would answer correctly if asked.

### `touches` is enforced nowhere (verified)

`tasks.py:32`:

```
"touches": [str],                # path globs this task writes (fan-out safety)
```

It is stored (`tasks.py:690,717`), echoed on read (`tasks.py:262`), threaded through
dedup (`task_dedup.py:344,391,414`) and scope patching (`task_scope.py:152`). A grep
for `touches` across `backend/services/*.py` and `scripts/*.sh` returns no gate, no
predicate, and no read in `scripts/ralph.sh`. The only other hits are the word
"touches" in unrelated English prose comments.

So the field the design calls "fan-out safety" is, today, a hint in a JSON file that
nothing consults. It is a perfect illustration of C2's problem and it is already in
the tree.

### Rules today: three stores, all advisory

- **Learnings** (`learnings.py`): "a short, pattern-focused lesson ... a recurring
  failure pattern, an operator correction, a verified approach". Scoped `global` plus
  per-scope; the `global` scope is "the workspace-wide guardrails store. **Injected
  into every agent's context**" (`learnings.py:20-24`). Dedup is canonicalize + exact
  hash + token-set Jaccard >= 0.85, and critically: "**A duplicate does not drop the
  signal: the new source + timestamp are attached to the existing entry's
  provenance**" (`learnings.py:31-35`). Storage is JSONL, append-ordered "so a stable
  prefix stays byte-stable for prompt caching" (`learnings.py:24-28`).
- **Knowledge notes** (`knowledge.py`): the Notes substrate, scoped and retrieved,
  never concatenated (ADR 0002).
- **Skills** (`skills.py`): SKILL.md bundles. Two consumers: builders get in-scope
  skill folders symlinked into `<cwd>/.claude/skills/<name>` so the CLI discovers them
  natively, "free, no prompt tokens until the model opens one"; assistants get
  `descriptions_block()` in the cache-stable prefix plus `get_skill()` on demand
  (`skills.py:20-28`). The docstring closes with the line that matters most for C2:
  "**Same context-hygiene framing as knowledge: it decides what a chat sees ambiently,
  not a security wall**" (`skills.py:30-33`).

Every one of these is prose put in front of a model. Not one of them can stop
anything. That is fine for most of what they carry and fatal for a small residue.

### The known-broken bit we must not build on top of

**F3 (HIGH, confirmed)** in `review-findings-2026-07-15.md:105-129`: project sweeps
pass a bare slug as the learning scope, `knowledge._parse_scope` rejects bare slugs,
so per-project learning injection is silently `""` and **recording falls back to
GLOBAL**. Result: "transient reviewer reasons ('Ralph rejected: no_diff', 'Ralph
rejected: new_stubs:3') become permanent workspace-wide guardrails injected into EVERY
chat". **F15** adds that reviewer-rejection reasons are per-run diagnostics stored as
durable lessons.

This matters here because Section 7 proposes minting textual rules from block
requests. Minting more rows into a store that is currently mis-scoped and polluting
the global guardrail block would make a confirmed HIGH bug worse. **F3 is a
prerequisite, not a nice-to-have.** It is Phase E0 below.

### The UI that exists

- `Work.tsx:50`: `type Tab = "tickets" | "runs" | "autonomy";` The Work surface is
  already **three** tabs, not two. It renders `blocked_reason` in the ticket drawer
  (`Work.tsx:562-563`).
- `TaskPanel.tsx:446-447` renders `Blocked: {t.blocked_reason}`.
- `TaskPanel.tsx:291-310`: `sendReply` already resolves an awaiting task by answering
  its question, posting to `/api/jobs/<job>/tasks/<task>/action` with the answer as a
  note, "recorded as evidence, clears the awaiting payload". `in_review` maps to
  `reject` (rework with the answer); `blocked` maps to `unblock`.
- `JobDetail.tsx:399-403`: a "Needs your input" card rendering
  `{t.question || t.blocked_reason || "Waiting on your input."}` behind a lucide
  `HelpCircle`, with Approve / Send back buttons (`JobDetail.tsx:406-415`).
- `AutonomyPanel.tsx:363` and `AutonomyScopes.tsx:131` already count `blocked` as a
  `danger`-status queue chip.

So: **one operator answer verb ("answer the question, unblock") is already shipped
end to end**, across two surfaces, against the `awaiting` field. What does not exist
is a structured request, a review queue, the other five verbs, batching, or any path
from an answer to a rule.

---

## Part 2: research, and what we adopt or reject

### Delegation levels: the axis is one axis, and management literature already named it

Tannenbaum and Schmidt (1958) model leadership as a **single continuum** between
manager authority and team freedom: "as the team's freedom is increased, so the
manager's authority decreases". The seven positions are tell, sell, suggest, consult,
join, delegate, abdicate
(https://www.businessballs.com/team-management/delegating-tannenbaum-and-schmidt-continuum/,
https://www.praxisframework.org/en/library/tannenbaum-and-schmidt).

Appelo's Delegation Poker restates it as seven levels from Tell ("I decide, I tell
you") through Consult, Agree, Advise, Inquire, to Delegate ("you decide, I trust you")
(https://medium.com/@jurgenappelo/the-7-levels-of-delegation-672ec2a48103,
https://management30.com/practice/delegation-poker/).

**What we adopt: the one-axis shape, and two specific ideas.**

First, both models are a **single dial**, not a matrix, and both are dials over
*decision authority* rather than over *task description quality*. Sixty-eight years of
management practice did not find it necessary to split latitude into four orthogonal
knobs. That is the strongest available evidence for Section 3.3's collapse.

Second, Tannenbaum and Schmidt say a leader picks a position by assessing "forces in
the manager, the team, and the situation, especially significance, **reversibility**,
information distribution, and time pressure"
(https://www.businessballs.com/team-management/delegating-tannenbaum-and-schmidt-continuum/).
**Reversibility is the discriminator**, and it is the rule we adopt for the middle
preset: decide the reversible, escalate the irreversible. This is the same instinct as
one-way versus two-way doors, arrived at independently.

**What we reject: seven levels.** Appelo's own caveat is that the levels "should not be
applied to individual tasks and deliverables. Instead, they should be applied to key
decision areas"
(https://medium.com/@jurgenappelo/the-7-levels-of-delegation-672ec2a48103). Our knob
*is* per-scope with a per-task override, so the caveat lands mostly on our side, but
seven presets for a single operator is exactly the exhaustive taxonomy the brief tells
us not to build. Three, matching the existing `DEPTHS` shape (`agents_registry.py:69`),
is the right cardinality. Also rejected: the "sell" and "join" positions, which model a
subordinate who must be persuaded or who co-decides. Neither is a thing an LLM worker
needs.

**What we reject: the word "delegation".** It is already overloaded in this codebase
(delegation = an agent spawning a subagent, `fanout`, `orchestration-and-autonomy.md`
section 4.3). Reusing it for decision authority would collide on day one.

### Progressive autonomy / trust ladders

The pattern: "start AI agents with narrow, low-risk permissions and expand them
gradually as the agent demonstrates it can be trusted, with high-stakes decisions
routing to humans while routine, well-understood tasks run automatically"; permission
ladders run Read, Suggest, Draft, Act-with-confirmation, Autonomous; the autonomy
spectrum runs Advisory, Supervised, Monitored, Autonomous, Self-improving
(https://www.mindstudio.ai/blog/ai-agent-permission-ladder-autonomy-levels,
https://www.mindstudio.ai/blog/progressive-autonomy-ai-agents-safe-deployment,
https://groundy.com/articles/how-much-autonomy-should-ai-agents-have-framework/).

**What we adopt: exactly one idea, the "Monitored" rung.** "Agent executes
autonomously; human reviews post-hoc" is the correct answer to Section 8.1 (blocked
forever with no operator around) for reversible decisions at high latitude: proceed,
record the decision as evidence, surface it for post-hoc review instead of blocking
until the operator returns. That is a real gap-filler.

**What we reject: everything else in this literature, and firmly.** "Permissions always
starting at zero (zero trust)" and agents that "earn expanded permissions over time
based on observed performance and confidence scores" is a multi-tenant governance
posture aimed at enterprises deploying agents against other people's data. Applied
here it would mean the operator's own workers start distrusted and must climb a ladder
to do the work the operator just asked for. That is the "no defensive cruft for
single-user setups" hard rule, violated in the most direct way available. It also
directly contradicts `permissions.py`'s stated posture. The Microsoft "defense in
depth" framing (https://www.microsoft.com/en-us/security/blog/2026/05/14/defense-in-depth-autonomous-ai-agents/)
is the same shape and is rejected for the same reason.

We take the rung name. We do not take the ladder.

### Durable execution (Temporal) as the precedent for parking on a decision

The brief asks specifically whether the Temporal-style suspend-and-resume model maps
onto Job=attempt. It maps beautifully, and then it should be rejected. This is worth
the space.

The mechanism: "When a Workflow calls `workflow.wait_condition()`, the Worker returns
the current task to the Temporal Server and becomes idle, the Workflow's state is
persisted and suspended on the Server, and when a Signal arrives or a timeout fires,
the Server schedules a new Workflow Task and execution resumes from the wait_condition
call" (https://docs.temporal.io/ai-cookbook/human-in-the-loop-python). Approval UIs
"send decisions into the workflow as signals" with no polling. "The workflow can await
a condition for hours or days without holding a thread or a DB connection"
(https://temporal.io/blog/human-in-the-loop-approvals). Durable timers "fire precisely
when a deadline expires without requiring an external scheduler, polling loop, or cron
job", and on expiry the workflow "escalates to a backup approver without any external
trigger" (https://temporal.io/blog/human-in-the-loop-approvals,
https://danielfridljand.de/post/temporal-human-in-the-loop).

**What we adopt: three concepts, zero machinery.**

1. **Park on the external decision; do not hold a worker.** Already true here, by
   accident of good design: `tasks.py:783-788` releases the lease and pid on the
   transition to `blocked`. A blocked task holds no dispatch slot. Temporal's
   "without holding a thread or a DB connection" is the same property.
2. **The durable timer with escalation on expiry.** Adopted as the per-escalation
   deadline in Section 8.1, implemented as a field the existing dispatcher cron reads,
   not as a timer service.
3. **The signal shape.** An external decision arrives as a structured message, not as
   the reviewer scraping a log. This is why Section 6.2 makes the block a typed
   record.

**What we reject: the durable-execution engine, and the resume.** Two reasons, and the
second is the interesting one.

The shallow reason: the stack is `claude -p` + bash + python + json, no agent
framework (brief, hard constraints). Adding a workflow engine to suspend a shell script
is not proportionate.

The real reason: **Temporal's core value is preserving in-memory state across the
wait, and an LLM job has no in-memory state worth preserving.** Temporal exists because
resuming a half-finished order-fulfilment workflow means restoring local variables,
loop counters, and partial results that are expensive or impossible to recompute. A
blocked LLM job's "state" is a context window: a reconstructible function of the task
text, the evidence list (`tasks.py:29`), the job's prior output, and now the operator's
answer. Worse, that context is *stale by construction*: it was assembled before a
human spent an hour deciding, and it contains the model's own dead-end reasoning about
a question that has since been authoritatively answered. Resuming it means paying to
reload a context whose most salient content is now wrong.

So we get Temporal's benefit for free by a different route: **the Task is the durable
workflow state.** It is already a flock-guarded JSON record with evidence,
dependencies, attempts, and a fencing token. Parking a task and re-running from it is
Temporal's model with the durable store we already have and the resume replaced by
something cheaper and *more* correct. Section 6.4.

### Policy-as-code (OPA/Rego, Cedar) as the "programmatic rule" precedent

Both are non-trivial to learn; Rego "is a logical programming language (like Datalog or
Prolog) and less like common languages like Python or Go", and policy-as-code languages
are "tailored for precision and flexibility but are difficult to write and maintain,
which is especially true with the Rego policy language". Cedar reads better and claims
42 to 60 times Rego's throughput with formal verification
(https://www.permit.io/blog/opa-vs-cedar,
https://www.styra.com/blog/comparing-opa-rego-to-aws-cedar-and-google-zanzibar/,
https://aws.amazon.com/blogs/security/migrating-from-open-policy-agent-to-amazon-verified-permissions/).
Permit.io's own title says the rest: "Everyone Loves Policy as Code, No One Wants to
Write Rego" (https://www.permit.io/blog/no-one-wants-to-write-rego).

**What we adopt: the vocabulary and one structural idea.** The vocabulary is
principal / action / resource / condition, which `permissions.py` already speaks in
its own dialect (`Identity`, `_VERBS`, `store_uri`, `caveats`). The structural idea is
that a decision is a **pure function of (principal, action, resource, context)** with
no side effects, which is what makes it cacheable, testable, and explainable. That is
already how `check()` is written (`permissions.py:710`).

**What we reject: a policy DSL, an engine, and a policy language.** The programmatic
rule in this system is **a row in `resource-grants.json`**, minted by `mint_grant()`
(`permissions.py:616`), narrowed by `attenuate()` (`:647`), expired by caveats
(`:697`), and evaluated by `check()` (`:710`). That machinery is written, tested, and
shipped. Introducing Rego so a single operator can express "this job may write
`docs/**`" would import a Datalog dialect, an evaluation engine, and a policy-authoring
problem that the cited literature says nobody wants, in order to replace a `fnmatch`
call. The formal-verification argument for Cedar is real and does not apply: it exists
to prove properties over policy sets authored by many hands across an org. There is one
hand here.

This is the anti-cruft rule doing useful work rather than being cited defensively: the
correct programmatic-rule implementation is roughly forty lines against an existing
API.

### Prior art in-repo (do not re-run)

- `orchestration-and-autonomy.md` section 7.1: the three-level cascade. Adopted
  wholesale, extended by one field.
- Section 7.3 of the same doc pre-commits us: "There are no approval queues, no
  per-action confirmation, no allowlists, no audit journals." Section 9 below must
  therefore justify the Review tab as *not* an approval queue for routine work. It
  does: nothing routes through it. It is where work that **already stopped on its own**
  goes to get restarted. The distinction is load-bearing and Section 9.1 makes it.
- `notes-system-plan-2026-07-15.md` part 2: the write-only-markdown failure mode.
  "Agents append but never re-read or reconcile; stale facts silently lie to the model
  after a refactor; every conversation pays the full attention cost of irrelevant
  rules; contradictory notes cohabit with equal weight and no salience or half-life"
  (Zep, https://blog.getzep.com/markdown-is-not-agent-memory/). **Textual rules minted
  from blocks are a direct route back into this failure mode**, which is why Section 7
  will not ship minting without expiry.
- ADR 0002: knowledge is the Notes substrate. ADR 0001: the task store is canonical.
  Neither is contradicted here.
- `review-findings-2026-07-15.md` F3, F15: prerequisites, above.

---

## Part 3: the design, part one, the latitude axis (C1, C3)

### 3.1 Why the name would not come

C1 records the operator reaching for a name twice and rejecting the first attempt:
"perhaps want a way to make the job or project be more strict or not - **better phrased
I mean a task or ticket may be well scoped or not**".

The second phrasing is more accurate about the *phenomenon* and cannot be a setting.
"Well scoped" is a property of the ticket's text. You do not set a ticket to be
well-scoped; you either wrote a good ticket or you did not. A `scopedness: high` field
on a vague ticket would be a lie stored in JSON, and the worker would read the lie and
proceed as though the ticket said things it does not say. That is worse than nothing.

The first phrasing ("more strict or not") is settable but reads as a property of
enforcement, which is a different mechanism (Section 5) that must not be conflated with
this one.

The thing that is both real and settable sits between them:

> **Latitude: what the worker is authorized to do when the ticket does not say.**

Every ticket underspecifies something; that is what a ticket is. Scopedness is how
*much* is underspecified, which we observe and cannot dial. Latitude is the standing
policy for the underspecified residue, which we cannot observe and can dial exactly.
C3's two named modes are the two ends of it, verbatim: "specify the requirements and
goals and let it figure things out" is high latitude; "be more hands on" is low.

`latitude` is also the right English word: room to act within limits. It does not
collide with `depth`, `fanout`, `effort`, `scope`, or `delegation`, all of which are
taken in this codebase.

### 3.2 Latitude is the block rate (the load-bearing claim)

Give the definition a mechanism and it produces the escalation loop for free:

| Ticket does not say... | Low latitude | High latitude |
|---|---|---|
| which test framework | ask | pick one, note it |
| whether to refactor the caller | ask | decide by reversibility |
| whether to add a dependency | ask | ask (irreversible-ish) |
| whether it may write `auth/` | **cannot, regardless** | **cannot, regardless** |

Reading down the columns: **latitude sets how often a job stops and asks.** The
escalation loop (thread B) is not a separate subsystem that happens to exist near the
scopedness setting. It is *the runtime behavior of the scopedness setting.* Thread C is
the dial; thread B is what the dial does.

Two consequences.

**It makes low latitude affordable.** "Be more hands on" is only usable if the
hands-on interaction is cheap and asynchronous. If every ask is a blocking modal, the
operator will never choose it. This is why Section 9's Review tab and Section 8.3's
batching are not UI polish: they are what makes half the C3 requirement usable at all.

**It makes the bottom row impossible to express as latitude.** No latitude setting
should ever authorize writing `auth/` if a grant says otherwise. That row is enforced,
not advised, which is Section 5, and it is why blast radius must not collapse into this
axis (3.3).

### 3.3 The minimum set of axes: one

The brief names four candidates. Taking them honestly:

**Specification tightness (goals-only vs step-level): REJECT as an axis.** This is
scopedness. It is a property of the ticket text and is not settable (3.1). It is a real
and important variable, and the place it is already expressed is the ticket text plus
`acceptance` (`tasks.py:18`, kinds `reviewer`/`command`/`operator`). A step-level spec
*is* a ticket with steps in it, or a parent with subtask children (`parent`,
`tasks.py:15`). Both exist. Adding a field that redundantly announces "this ticket is
specific" would be cruft that can disagree with the text.

**Latitude of means: ADOPT. This is the axis.** May it refactor, add deps, touch other
files: every one of these is "the ticket did not say, what now".

**Checkpoint frequency (report at end vs per-step approval): REJECT, derived.** A
checkpoint is a block the worker raises at a predictable moment. Setting latitude low
*is* setting checkpoints frequent; the checkpoints land where the uncertainty actually
is rather than at arbitrary step boundaries, which is strictly better. A separate
frequency knob could contradict the latitude knob (high latitude, per-step approval:
what does the worker do?) and would produce checkpoints at moments the worker had
nothing to say. Note the one thing this does drop: a *time-based* progress ping,
"tell me every 20 minutes even if all is well". That is a genuinely different want and
it is not this axis; it is the Runs tab, which already streams job progress
(`Work.tsx:28` `JobsFeed`). Named as deliberately left out.

**Blast radius (what it may write): REJECT as a latitude axis; it already exists and
must stay separate.** `touches` (`tasks.py:32`) and resource grants
(`permissions.py:616`) are the blast radius. Keeping it separate is not tidiness, it is
the whole point of Section 5: blast radius is the **enforced floor** and latitude is
the **advisory policy above the floor**. Merging them would either make latitude
enforceable (which is impossible for "should I use pytest") or make blast radius
advisory (which makes it a comment, which, per the audit, is exactly the bug we already
have).

**One new knob. `latitude`.** It rides the existing cascade (Section 4), it does not
duplicate `touches`, and it is the only one of the four the operator cannot already
express.

### 3.4 The presets

Three values, mirroring `DEPTHS` (`agents_registry.py:69`) so the vocabulary rhymes and
the UI control is the same shape.

```
LATITUDES = ("strict", "standard", "open")
DEFAULT_LATITUDE = "standard"
```

| `latitude` | Tannenbaum-Schmidt | Rule at the edge of the spec | Blocks | Use when |
|---|---|---|---|---|
| `strict` | tell / consult | Do what the ticket says. Any decision the ticket does not specify raises a block. | many | "be more hands on" (C3); unfamiliar area; a ticket you wrote carefully and mean literally |
| `standard` | consult / advise | Decide **reversible** things and record the decision as evidence. Block on **irreversible** ones, on anything outside `touches`, and on any hard gate. | few | the default; most work |
| `open` | delegate | Pursue the goal. Decide freely within the enforced floor. Report decisions at the end. | rare | "specify the requirements and goals and let it figure things out" (C3) |

The `standard` rule is the Tannenbaum-Schmidt reversibility criterion
(https://www.businessballs.com/team-management/delegating-tannenbaum-and-schmidt-continuum/)
made operational. "Reversible" is judged by the worker with a stated definition in the
persona block: reversible means a later `git revert` or a one-line edit fully undoes it,
with no external side effect and no third party having observed it. Adding a file:
reversible. Adding a dependency to a lockfile others will install: not. Pushing a
branch: not. Deleting a store: not.

Note what `strict` does **not** mean. It does not mean "less capable", "fewer
iterations", or "a weaker model". That is `depth`, and the distinction is 4.2's whole
argument. A `strict` + `deep` job is coherent and useful: think hard, then ask before
choosing. So is `open` + `quick`: do the obvious thing, do not bother me.

Rejected fourth preset: `propose-only` / "plan but do not execute". It is tempting and
it is `depth` plus an `acceptance` of `operator` on a ticket that says "produce a plan".
Both exist. Named as left out.

---

## Part 4: the design, part two, inheritance (C4)

### 4.1 The recommendation

**Recommendation: `latitude` rides the cascade that already exists, at exactly the
levels `depth` and `fanout` already ride. Do not build a second inheritance mechanism.
Do not put latitude on the agent as a separate level from the scope.**

C4 asked: "Some settings on a project level (and agent or job level? Inheriting from
project?)". The direct answers:

- **Project level: yes.** This is the primary knob, as the operator guessed.
- **Agent level: yes, but it is the same level, not a further one.** In the existing
  model, an agent scope and a project scope are both *scopes*
  (`orchestration-and-autonomy.md` 7.1: "on the project registry entry **or**
  agent-definition frontmatter"). An agent does not inherit from a project; an agent
  *is* a peer scope. There is one exception, 4.3.
- **Task level: yes, as an override.** Already where `effort` and `touches` live.
- **Job level: no, with one exception.** A job is an attempt at a task, not a
  configuration site. Configuring a job means configuring the invocation that launched
  it, which is the invocation level, which already exists (`--force`, one-off caps).
  So: **invocation override, yes; a `latitude` field stored on the Job record, no.**
  The resolved value is *recorded* on the job for provenance (5.4), which is different
  from being settable there.

**Resolution order (unchanged from `orchestration-and-autonomy.md` 7.1):**

```
invocation  >  task  >  scope  >  DEFAULT_LATITUDE ("standard")
```

First non-null wins. Identical to `depth`. No new code path: the cascade is the same
function with one more key.

**Storage (each in the file that already holds its siblings):**

| Level | Where | Shape |
|---|---|---|
| Scope (agent) | agent definition frontmatter, `agents_registry.py:160-163` parse, `:374-381` serialize | `latitude: strict` |
| Scope (project) | project registry entry, beside `autonomy` | `"latitude": "strict"` |
| Task | `.tasks.json`, beside `effort` (`tasks.py:34`) | `"latitude": "open"` |
| Invocation | sweep argv, beside the depth override | `--latitude open` |

Follow the existing serialization discipline exactly: emit only when non-default, so
definition files are not littered with `latitude: standard`
(`agents_registry.py:374-381`).

**Null means inherit; it does not mean `standard`.** `DEFAULT_LATITUDE` applies only
when the whole chain is null. Otherwise a project set to `strict` would be silently
undone by a task record that stores its default. This is why the emit-only-when-
non-default rule is correctness here and not just tidiness.

### 4.2 Testing the hypothesis the brief asked us to test honestly

The brief says: "A serious possible finding: this axis is partly the SAME thing as the
existing autonomy preset/depth/fanout and should extend it rather than sit beside it.
Test that hypothesis honestly."

Tested. It is **half right**, and the useful half is not the obvious one.

**Against `autonomy` (`triggers.py:214`): not the same. Genuinely orthogonal.** The
preset answers **when a sweep starts**: it "expands DETERMINISTICALLY into the concrete
trigger list" (`triggers.py:207-212`), and the whole table in `triggers.py:216-222` is
about dispatcher behavior. Latitude answers **how much room the sweep has once
running**. The cross product is populated in all four corners, which is the test:

- `continuous` + `strict`: a standing loop doing mechanical, fully-specified sweeps that
  stops the moment anything is ambiguous. This is the *correct* setting for an
  unattended overnight loop and it is currently inexpressible.
- `on-demand` + `open`: "go figure this out" on a manual run. This is C3's first mode
  verbatim, and it is also currently inexpressible.
- `continuous` + `open`: standing autonomous work. Expressible today, sort of, by
  accident, because latitude is currently an unstated constant.
- `off` + anything: latitude is moot until someone presses Run now, at which point the
  invocation supplies it.

Two of four corners are wanted and unreachable. Folding latitude into the autonomy
preset would delete them. **Reject the merge with `autonomy`.**

**Against `depth` (`agents_registry.py:69`): adjacent, correlated, and still distinct.**
`depth` "maps to `ralph.sh` `--max-iter` and the worker model/effort" 
(`orchestration-and-autonomy.md` 7.1): it is an **effort budget**. Latitude is
**decision rights**. They correlate in practice (people who want deep thinking often
want to be consulted) and they are not the same: `strict` + `deep` (think hard, then
ask) and `open` + `quick` (do the obvious thing, don't bother me) are both coherent and
both wanted. Collapsing them would force "I want it to think hard" to imply "I want to
be interrupted", which is false and would make `deep` annoying enough to avoid.
**Reject the merge with `depth`.**

**Against `fanout` (`agents_registry.py:70`): not the same; `fanout` is enforced.**
`fanout: off` "forbids even that" (read-only subagents). It is a capability gate on the
harness, not advice. It belongs with `touches` on the enforced side of Section 5.

**So where is the hypothesis right?** Not in "latitude duplicates an existing knob".
In something better: **latitude is the fourth member of a set whose first three already
exist, are already stored in the same places, already inherit through the same cascade,
and are already rendered by the same panel.** The finding is not "do not add it". It is
"**adding it must not create anything new except a string field and a persona
paragraph**". If the implementation touches a new store, a new resolution function, or
a new settings page, the implementation is wrong. Section 10's phases are sized to that
claim: E1 is a day.

### 4.3 The one real inheritance subtlety

An agent scope and a project scope are peers, except when an agent runs *inside* a
project: `project_slug` on the agent record (`agents_registry.py:164`), and
`assignee = "agent:<slug>"` on a task in a project's tree (`tasks.py:14`). Whose
latitude applies?

**Recommendation: the task's tree wins; the agent's own latitude applies only in its
own scope.**

Latitude is a property of **the work and where it lands**, not of the worker's
personality. A `strict` project means "work in this tree is hands-on", and an agent
with `latitude: open` in its frontmatter must not import its own permissiveness into a
tree the operator marked strict. The reverse is also right: the same agent running in
its own agent scope uses its own value.

So the scope level of the cascade resolves to **the scope that owns the tree the task
lives in**, which is what `assignee` and the sweep's cwd already determine. No new
concept. If a specific agent genuinely needs different latitude within a project, that
is a per-task override, which exists.

This also disposes of "inheriting from project?" cleanly: **agents do not inherit from
projects, because they are not below them.** Tasks inherit from whatever scope owns
their tree. The tree is the unit.

---

## Part 5: the design, part three, how skills and rules enforce scopedness (C2)

### 5.1 The distinction, stated once

> An LLM told "do not touch `auth/`" is not the same as a job that **cannot** write
> `auth/`.

This is the heart of the section, and the audit found it already sitting in the tree:
`touches` is documented as "fan-out safety" (`tasks.py:32`) and is read by no gate. We
have shipped the first kind while believing we shipped the second.

Two kinds of rule, and every rule in the system must be consciously one of them:

- **ADVISORY**: text placed in the model's context. Learnings (`learnings.py`),
  knowledge notes (`knowledge.py`), skills (`skills.py`), the persona. The model
  usually complies. It may not, and no bug is filed when it does not, because
  compliance was never guaranteed. `skills.py:30-33` says this outright: "it decides
  what a chat sees ambiently, **not a security wall**".
- **ENFORCED**: a predicate the harness evaluates outside the model's control. The
  model cannot talk its way past it, and does not need to be told about it for it to
  hold (though it should be, so it can raise a coherent block instead of thrashing).

### 5.2 Which cases need which

**The test:** can the rule be decided by a pure predicate over (identity, action,
resource, context) with no LLM in the loop?

- **No** (needs judgment: "prefer table-driven tests", "match the house voice", "do not
  over-abstract"): it can only be advisory. There is no other option, so do not agonize.
  Put it in learnings or a skill and accept the compliance rate.
- **Yes, and the action is reversible** ("prefer `rg` over `grep`"): advisory. A gate
  here buys nothing and costs a failure mode, because a wrong predicate blocks correct
  work at 3am.
- **Yes, and the action is irreversible, external, or secret-adjacent**: **enforce**.
  This set is small and enumerable, which is exactly why enforcing it is not cruft:
  - writes outside the task's `touches`
  - network egress (already a hook shared with the bridge agent,
    `project_workshop_v1_platform_2026_06_14`; unverified from this thread's reads)
  - `git push`, branch deletion, force operations
  - dependency and lockfile mutation
  - reading or writing a Store the job holds no grant for (`permissions.py:710`)
  - spawning subagents when `fanout: off` (`agents_registry.py:70`)

That list is six items. It is not an allowlist over routine work, it is a floor under
irreversible work, and every item on it is something the operator would want to have
been asked about.

**Where latitude sits relative to the floor:** latitude modulates the **advisory**
layer and the **block rate**, and never moves the floor. `latitude: open` does not
grant `auth/`. It means "stop asking me about reversible choices", not "stop asking me
about anything". This is the property that makes high latitude safe to actually use,
which is the point: if `open` meant "no constraints", nobody could ever select it, and
C3's first mode would be undeliverable.

### 5.3 The missing piece: there is no enforcement point

`permissions.py`'s docstring: "This module is a CONSULTED source of truth, **NOT a wall
in front of existing routes**. There is no middleware here." Correct as authored, and
it means an enforced rule currently has nowhere to run.

**Propose: one function, two call sites. No middleware, no engine.**

```python
# backend/services/gate.py  (new, small)
def check_action(job_ctx, action) -> ("allow" | "block", reason, escalation | None)
```

`job_ctx` carries the task's `touches`, the scope's grants, `fanout`, and the resolved
`latitude`. `action` is a normalized (verb, target) pair. On block it returns a
**prefilled escalation record** (Section 6.2), so the very thing that stopped the job is
the thing the operator reviews. That is B4's "based on that request", satisfied
structurally rather than by convention.

Two call sites, because there are exactly two runtimes (`RUNTIMES = ("builder",
"assistant")`, `agents_registry.py:60`):

- **Builder** (`claude -p`, we do not own the loop): a `PreToolUse` hook installed by
  the sweep wrapper, shelling to the same predicate. This is the natural fit and it is
  the harness's own mechanism, not a new one.
- **Assistant** (data mode, we own the prompt loop): the action dispatcher calls
  `check_action` before executing, next to where it already resolves tool calls
  (`routes/chat.py`, referenced by `skills.py:26-28`; exact line unverified from this
  thread's reads).

**Do not gate the operator.** `authorize(operator, ...)` stays always-allow
(`permissions.py:16-20`). `check_action` is consulted for **unattended job actions
only**. An operator typing in a chat is never gated by this, and a job running because
the operator pressed Run now is gated, because the operator is not watching it and that
is the entire point.

### 5.4 The identity question, answered as far as this report can

The brief calls this "arguably the central design decision of this whole thread" and
assigns it to the resource-access report. It is load-bearing here, so: a recommendation,
offered as input, not a ruling.

**Recommendation: a third identity kind, `agent`. Not operator, not guest.**

- Not `operator`: `authorize(operator, ...)` is always-allow, so an autonomous job as
  operator can never be gated, and `check_action` becomes a no-op. Every enforced rule
  in 5.2 dies.
- Not `guest`: guests are the external boundary, capped at `shareable`/`public`
  sensitivity and denied `private` apps outright
  (`permissions.py:60-63`). A job would be unable to do the operator's private work,
  which is all of the work.

`agent` sits between: **the operator's authority, attenuated by the job's task**. And
the primitive for that is written. `attenuate()` (`permissions.py:647`) is
macaroon-style narrowing: the sweep mints a grant from the operator's authority,
attenuated to the task's `touches` and the scope's stores, and hands the token to the
job. `check(token, uri, verb)` (`:710`) then does the enforcing with no new evaluator,
and `revoke()` (`:680`) covers Section 8.6.

This is not a trust ladder (rejected in Part 2) and not the operator asking permission
of themselves. It is the operator's own authority, narrowed to the job they asked for,
using an API this repo already has and does not currently call.

### 5.5 What skills do here

`skills.py` is advisory and should stay advisory: "not a security wall"
(`skills.py:30-33`). Skills contribute to scopedness in the way C2 actually meant, which
is not enforcement:

**A skill is how a strict scope stays cheap.** At `latitude: strict`, every
underspecified decision is a block, and blocks cost operator attention. A skill answers
a class of questions *before* they become blocks. "How we write tests here" as a
SKILL.md converts twenty potential blocks into zero, without lowering latitude. So the
correct reading of C2 ("we want skills and rules to be sure they're well scoped") is:
**skills and rules raise scopedness, which reduces the block rate at fixed latitude.**
That is a real relationship between the two threads and it is the reason C2 sits inside
C's block rather than B's.

Which gives the honest formulation of the whole design:

```
block rate  ~  f(latitude, ticket scopedness, coverage of skills+rules)
```

The operator sets latitude. The ticket text and the skills corpus set the rest. If a
strict scope blocks constantly, the fix is usually a skill, not a latitude change. That
sentence belongs in the UI as help text (Section 9.4).

---

## Part 6: the design, part four, the blocked/escalation loop (B1, B2)

### 6.1 The general shape (B1: "blocked by permission or anything")

B1 is explicit that the mechanism must not be permission-specific. Section 5's gate
produces permission blocks; those are a *subset*. A job must be able to raise a block
because a spec is ambiguous, because a dependency is missing, because a test fixture is
absent, because two rules contradict, or because it is at `strict` latitude and the
ticket did not say.

**So the escalation record is the general primitive, and the gate is one producer of
it.** Producers:

1. `gate.check_action` returns block (Section 5.3): permission.
2. The worker itself decides it should ask, per latitude (Section 3.2): decision or
   missing-info. This is the *common* case at `strict` and the one B1's "or anything"
   is pointing at.
3. The reviewer rejects in a way that needs an operator (Section 8.5).
4. The harness: environment failure, missing tool, contradiction.

All four write the same record and land in the same queue.

### 6.2 The record: generalize `awaiting`, do not add a sibling

`awaiting` (`tasks.py:23`) is `{"question": str, "since": iso}`. It is the right seam
and the wrong shape: a question string cannot say *what would unblock me* in a form the
system can check.

**Recommendation: widen `awaiting` in place, keep `question` as the human-readable
line.** Additive, so `TaskPanel.tsx:291-310` and `JobDetail.tsx:399-403` keep working
unchanged on day one and get richer as fields land.

```
"awaiting": {
  "question": str,          # EXISTING. the human sentence. stays first-class.
  "since": iso,             # EXISTING.
  "kind": str,              # "permission"|"decision"|"missing-info"|
                            # "dependency"|"environment"|<other>   (open, per A4/A8)
  "why": str,               # why the job cannot proceed without an answer
  "unblocks_if": str,       # what would resolve it, in one line
  "options": [str] | None,  # for kind=decision: what it would choose between
  "default": str | None,    # what it WOULD do at higher latitude (drives 8.1)
  "proposal": {             # for machine-checkable asks. drives rule-kind proposal (7.3)
    "uri": str,             # workshop://store/<app>/<name>  or a path glob
    "verb": str             # read|write|append  (permissions.py:56)
  } | None,
  "raised_by": "job:<id>",
  "dedupe_key": str,        # groups mass-blocks (8.3). same discipline as
                            # tasks.py:34 `provenance`, "stable dedupe key"
  "deadline": iso | None    # 8.1. the durable-timer idea, no timer service
}
```

Design notes:

- **`kind` is an open string, not an enum.** The operator's "or other" appears four
  times across A4/A5/A8 and the asks doc's Part 3 reads that as a deliberate signal:
  "treat the named items as the seed set, and treat extensibility of the set as a
  design requirement". A closed enum would fail B1's "or anything" on the first
  unanticipated case. Validate that it is a short slug, do not validate membership.
- **`default` is what makes 8.1 possible.** A job that says "I would use pytest" has
  told us what to do if nobody answers by the deadline.
- **`proposal` is what makes B4 and 7.3 possible.** Its presence or absence is the
  signal for whether a programmatic rule is even expressible.
- **`dedupe_key` is what makes 8.3 possible**, and follows a discipline the store
  already uses (`provenance`, `tasks.py:34`).

The clearing rule at `tasks.py:781-782` already does the right thing and needs no
change: leaving `blocked`/`in_review` nulls the whole payload.

### 6.3 Surfacing (B2)

**Recommendation: the Work page, as a fourth tab. Not a new surface, not Discord as
the system of record.**

- `Work.tsx:50` is already `type Tab = "tickets" | "runs" | "autonomy"`. The Work
  surface is already the multi-tab home of work state. A `Review` tab is the natural
  fourth and is precisely "one place" (B2). Section 9 details it.
- **Discord: notify, do not decide.** The bridge exists and is where the operator
  actually is (user-scope CLAUDE.md). It should post "3 blocks waiting, oldest 40m"
  with a deep link. It must not be where approvals are recorded: the answer needs
  structure (which rule, which scope, which verb), the operator hard rule about
  authorization via Discord is explicit, and a chat log is a bad system of record for
  authz. Notification, not decision.
- **Not a separate `/review` route.** That is the third surface the brief warns against
  and it splits work state across two pages.

### 6.4 How the job resumes: it does not (argued from Job=attempt)

**Recommendation: the blocked job ends. The task holds the block. A new job picks up the
unblocked task, with the operator's answer in its context.**

The argument from the model:

`chat-tickets-orchestrator-plan-2026-07-15.md:29` is unambiguous, and worth quoting
because it settles this: "Merging records would destroy the attempts model (a task
retried 3 times is one ticket, three runs) ... The Linear analogy is exactly right:
issue = Task, activity feed = its Jobs."

A block is not a property of an attempt. It is a property of the ticket: *this work
cannot proceed until someone decides X*. It survives the attempt, it is what the next
attempt needs to know, and it is what the operator is reviewing. Ticket-level state
belongs on the ticket. Which is where `blocked` and `awaiting` already are
(`tasks.py:61,23`), on the Task, not on the Job. **The substrate already voted.**

The argument from the code: `tasks.py:783-788` already releases the lease and pid on any
transition out of `doing`, explicitly including `blocked`, and calls this "the single
most important detail for 'the whole system never freezes'". A blocked task today holds
no worker and no slot. There is no process to resume. Building resume means *undoing*
that property and reintroducing the freeze it was written to prevent.

The argument from cost (Part 2, Temporal): the blocked job's context is stale by
construction. It was assembled before the operator spent an hour deciding, and its most
salient content is the model's own dead-end reasoning about a question that now has an
authoritative answer. A fresh job re-reads the task, the evidence list (`tasks.py:29`),
and the answer, and starts from a clean, correct context. That is cheaper *and* better.
This is also the house's existing instinct: the completion gate uses a **fresh-context**
reviewer precisely because a stale context judges badly (workshop CLAUDE.md).

The counter-argument, stated fairly: re-running discards genuine work an attempt did
before it blocked (a half-finished refactor, an explored dead end worth not re-exploring).
**Mitigation, and it is enough:** the blocking job writes what it learned to the task's
`evidence` list (`tasks.py:29`) as it dies, and the operator's answer is appended as
evidence too (which `TaskPanel.tsx:291-296` already does: "recorded as evidence, clears
the awaiting payload"). Evidence is the handoff. The next attempt reads the ticket plus
the evidence trail, which is *exactly* Temporal's durable state, stored in the record
that already exists rather than in a suspended process. If the discarded work is
substantial, that is a signal the ticket was too big, and the answer is subtasks
(`parent`, `tasks.py:15`), not process resurrection.

**Costs, stated honestly:** each block-unblock cycle burns one attempt (`tasks.py:622`),
so a task blocked three times against a cap of three parks itself. That is mostly a
feature (Section 8.5) and occasionally wrong: a task that blocked twice for good reasons
and got answered twice should not be penalized as though it failed twice. **Recommend:
`restore_attempt` (`tasks.py:745`, which already exists to give back an attempt the
claim consumed) is called on the unblock path when the block was operator-answered
rather than worker-failure.** The primitive is written; this is one call site.

### 6.5 The operator's response verbs

Six, each mapping to something that already exists. Every one of them ends the review
of that block.

| Verb | Effect | Mechanism |
|---|---|---|
| **Approve once** | this task proceeds; nothing durable changes | `update(status="todo", awaiting=None)` + answer as evidence (`tasks.py:737,808`) + `restore_attempt` (6.4) |
| **Grant standing access** | a durable programmatic rule | `mint_grant()` (`permissions.py:616`), prefilled from `awaiting.proposal` |
| **Amend the rule** | durable textual or programmatic (Section 7) | `learnings.add(scope=...)` or a `touches`/grant edit |
| **Rescope the task** | the ticket was wrong, not the worker | `update(text=..., touches=..., acceptance=...)` |
| **Reject with reason** | do not do this; the reason is the lesson | `update(status="cancelled", blocked_reason=...)`, optionally + a learning |
| **Defer** | not now, do not ask again this sweep | `update(status="parked")` (`tasks.py:57-60`, an existing non-terminal dead-letter) |

Note **Approve once** and **Grant standing** are the same UI gesture with a different
durability, which is exactly the choice B2 and B3 are asking the operator to make, at
the moment they have the context to make it. That adjacency is the design.

---

## Part 7: programmatic vs textual rules, and promotion (B3, B4)

### 7.1 What each one is, concretely

- **Programmatic rule**: machine-checkable, evaluated by `check()`
  (`permissions.py:710`) with no LLM. Concretely one of: a row in
  `resource-grants.json` via `mint_grant()` (`:616`), possibly narrowed by
  `attenuate()` (`:647`) and expired by a caveat (`:697`); or an entry in a task's
  `touches` (`tasks.py:32`) once Section 5.3 makes it real. **Not a DSL** (Part 2).
- **Textual rule**: model-interpreted prose. A learning (`learnings.py`), a knowledge
  note (`knowledge.py`), a skill (`skills.py`), or a persona line. Injected into
  context; complied with usually.

### 7.2 When each is right

Same test as 5.2, because it is the same distinction viewed from the authoring end:

**Programmatic when** the ask names a resource and a verb; the answer is stable
("yes, always" not "yes, this time"); and being wrong is expensive. "May this job write
`docs/**`?" is a grant. It is checkable, and a textual "you may write docs" is a
suggestion the model might over-read into `docs/../backend`.

**Textual when** the rule needs judgment ("prefer the existing UI primitive over a new
one"); or it is about *approach* rather than *access*; or it is provisional. "Use pytest
here" is not expressible as (principal, action, resource) and should not be tortured
into it.

**The honest boundary case, named:** "never touch `auth/` without asking". This is
checkable *and* it is a judgment about care rather than capability. It should be
**both**: a grant that excludes `auth/` (so it cannot happen by accident at 3am) and a
learning that says why (so the model does not thrash against a wall it does not
understand, and so a future operator reading the rule knows the reason). This is not
indecision; enforced rules without their rationale in context produce jobs that fail
mysteriously and repeatedly. **The floor and the explanation are different artifacts
with different jobs.**

### 7.3 How the operator picks: the system proposes, from the request (B4)

B4 is that the rule change is driven **from the block**, not a separate authoring flow.
Structurally:

**`awaiting.proposal` decides which rule kind is offered.**

- **`proposal` is present** (the ask named a uri and a verb): offer a **grant**,
  prefilled, one click. Show the textual option as secondary.
- **`proposal` is absent** (the ask was a judgment call): offer a **textual rule**,
  prefilled with the question and the operator's answer, with a scope selector
  defaulting to the task's scope (**not** `global`; see 7.5).
- **`kind == "decision"` with `options`**: offer both. Picking an option is the answer;
  a checkbox turns it into a standing textual rule.

The operator never opens a rule editor. They answer a block, and the durable artifact is
a byproduct of the answer with the context already filled in. That is B4 satisfied by
construction rather than by discipline. And the field that carries it (`proposal`) is
the *same* field the gate emits when it blocks (5.3), so the loop closes: the thing that
stopped the job is the thing that becomes the rule that stops stopping it.

### 7.4 Promotion: textual rules that prove themselves become programmatic

The best idea here, and the mechanism is nearly free.

**The problem it solves.** Textual rules rot (Part 2, Zep). They accumulate, they
contradict each other, they are never re-read, and every job pays attention cost for all
of them. Meanwhile some of them are *not really judgments at all*: they are grants the
operator expressed in prose because prose was the easy box to type into. "Don't write
outside `docs/`" as a learning is a grant wearing a costume, and it will be violated
eventually because it is advice.

**The mechanism.** `learnings.py:31-35`, already shipped:

> A duplicate does not drop the signal: the new source + timestamp are attached to the
> existing entry's provenance.

So **provenance length is a free stability counter.** A rule that keeps getting
re-derived from independent blocks has been independently confirmed N times. Nothing new
is stored; the evidence is already accruing in a field that exists.

**The promotion rule.** Propose promotion when all three hold:

1. the entry's `provenance` has >= 3 independent sources, or the rule has survived >= 30
   days with no contradicting correction;
2. the rule is **expressible** as (uri, verb): either it was minted from an escalation
   whose `proposal` was non-null (which we recorded), or it pattern-matches a path or
   store reference;
3. no operator correction has contradicted it since (`learnings.py` already types
   operator corrections).

Then surface it in the same Review tab as a proposal card: "This rule has held 4 times
across 3 jobs. Make it enforced?" One click mints the grant (`permissions.py:616`) and
marks the learning `promoted: <grant_id>`.

**Why this is worth it, in one line:** it makes the cheap path (type prose) the *entry*
to the correct path (an enforced predicate) instead of a dead end, so the operator never
has to decide up front whether a rule deserves to be programmatic. They discover it from
use. This inverts the policy-as-code adoption problem the research names ("no one wants
to write Rego", https://www.permit.io/blog/no-one-wants-to-write-rego): nobody writes
the policy. The policy is *promoted* out of prose that already proved itself.

**Keep the prose after promotion.** Per 7.2's boundary case, the learning stays as the
rationale and gains a marker that it is now enforced. The model should know the wall is
there and why.

### 7.5 How textual rules avoid becoming write-only prose

The notes plan diagnosed this and we must not re-introduce it. Four rules, all cheap:

1. **Scope correctly, never `global` by default.** A rule minted from a block in
   `project:foo` is a `project:foo` learning. `global` is "injected into every agent's
   context" (`learnings.py:20-24`) and every entry there taxes every job forever. The
   Review tab's scope selector defaults to the task's scope and makes `global` a
   deliberate act. **This is blocked on F3**, which currently forces the opposite
   (`review-findings-2026-07-15.md:105-129`): project scopes silently fall back to
   global today. Minting rules into that bug would accelerate a confirmed HIGH defect.
2. **Expiry by disuse.** A rule not cited in 90 days surfaces in Review as "still true?"
   with Keep / Retire. This is the salience-and-half-life property Zep says markdown
   memory lacks (https://blog.getzep.com/markdown-is-not-agent-memory/), obtained with a
   timestamp and a list.
3. **Promotion drains the corpus** (7.4). The rules most worth keeping leave the prose
   store for the predicate store. Textual rules should be a *working set*, not an
   archive.
4. **Do not mint diagnostics as rules.** F15's exact bug: "Ralph rejected: no_diff" is
   not a lesson. A block answered with "fix your environment" produces no rule. The
   Review tab must let the operator answer *without* minting, and that must be the
   default (the "Approve once" verb, 6.5).

Rules 2 and 3 are the ones that make this a *managed* corpus. Without them, this design
is a machine for generating the write-only failure faster than a human ever could, which
is precisely why they are in Phase E4 and not in a backlog.

---

## Part 8: condition handling

### 8.1 Blocked forever, no operator around

The dominant condition for this operator: work runs overnight, blocks at 2am, nobody
answers until 9am. Seven hours idle, or seven hours of wrong autonomous choices. Both
are bad.

**Latitude already answers this, which is the payoff of Section 3.2.** The escalation's
`deadline` and `default` fields (6.2) plus the resolved latitude:

| latitude | at deadline |
|---|---|
| `strict` | stay blocked. The operator chose hands-on; guessing violates the choice. |
| `standard` | if the ask is **reversible** and has a `default`: take it, record it as evidence, mark it `auto_resolved`. Surface it in Review for post-hoc confirmation. Else stay blocked. |
| `open` | as `standard`, and a wider reversibility bar. |

**Never auto-resolve an enforced gate, at any latitude.** If `check_action` blocked it,
a timer must not unblock it. The floor does not have a timeout. This is the property
that makes the whole auto-resolve mechanism safe to have at all.

This is the "Monitored" rung adopted from the trust-ladder research (agent executes;
human reviews post-hoc) and the durable-timer-with-escalation from Temporal
(https://temporal.io/blog/human-in-the-loop-approvals), implemented as a field the
existing dispatcher cron reads on its normal tick. No timer service, no scheduler.

Default deadline: 4 hours at `standard`, none at `strict`, 1 hour at `open`.

### 8.2 Stale block (blocked on something already granted)

A job blocks asking for `write docs/**`. The operator grants it. Two other tasks are
still blocked on the same thing and nobody notices.

**Recommendation: re-evaluate on grant, and re-check before surfacing.** Two cheap
guards:

1. **On `mint_grant()`**, sweep open escalations whose `proposal` the new grant now
   satisfies (`check(token, uri, verb)`, `permissions.py:710`) and auto-unblock them,
   recording "unblocked by grant `<id>`" as evidence. This is a loop over open
   escalations calling a function that exists.
2. **On render**, the Review tab re-checks each `proposal` before showing it. A block
   that is already satisfied never reaches the operator's eyes.

Guard 1 makes the common case instant. Guard 2 catches everything else, including
grants minted out-of-band, and costs one predicate call per card.

Note this is also 8.3's mechanism, arrived at independently, which is a good sign.

### 8.3 Mass-blocking (10 jobs, one rule)

The brief is right that this must not be 10 clicks. It is also the *expected* case: a
sweep with fanout hits the same wall ten times in a minute.

**Recommendation: group by `dedupe_key`, and make the rule the primary verb.**

- `awaiting.dedupe_key` (6.2) is a hash of (`kind`, `proposal.uri`, `proposal.verb`) or,
  for judgment asks, of the canonicalized question. Same discipline as `provenance`
  (`tasks.py:34`), and the canonicalization is `learnings.py`'s (lowercase, collapse
  whitespace, drop trailing punctuation) which is already written and already tuned for
  this exact corpus.
- The Review tab renders **one card per group**, headed "8 tasks blocked on: write
  access to `docs/**`", with the tasks listed inside it, collapsed.
- One action applies to the group. **Grant standing access resolves all 8 at once by
  8.2's mechanism**, not by iterating: mint the grant, and the re-evaluation sweep
  unblocks every task whose proposal it satisfies. The batch is a consequence of the
  rule, not a bulk operation with its own code path.

This is why 6.5 puts Grant standing next to Approve once. **A mass-block is the system
telling you a rule is missing.** The grouped card makes that legible: 8 tasks asking the
same question is not 8 decisions, it is one.

Per-task verbs (rescope, reject) stay available inside the group for the odd one out.

### 8.4 A block raised by the reviewer

The completion gate is a fresh-context reviewer and "the worker does not judge its own
completion" (workshop CLAUDE.md). If the reviewer can raise blocks, two failures appear:

- **Self-approval**: a reviewer that raises a block and can resolve it has become the
  operator.
- **Grantee capture**: a reviewer whose block mints a grant *to itself* widens its own
  authority.

**Recommendation:**
- Reviewer-raised escalations are **always** operator-resolved: never auto-resolved at
  deadline (8.1), regardless of latitude. A reviewer block means the *verification* is
  ambiguous, which is exactly the case where guessing is worst.
- **A grant minted from an escalation may never name the raiser as grantee** when the
  raiser's role is the gate. Enforce in `mint_grant`'s caller, one condition.
- Mark it in the record: `awaiting.raised_by` already carries the job id (6.2), and the
  job's role is resolvable from its agent (`agents_registry.py:65`).

### 8.5 A job that games the block to escape scope

The failure: a job at `strict` discovers that blocking produces an operator answer that
lands in its context as authority, and learns to block its way to permissions, or blocks
on a pretext and treats the answer as blanket license.

Three defenses, two of which already exist:

1. **The answer is scoped, not appended.** An "approve once" resolves *this*
   escalation's `proposal`, and the enforced floor does not move because a job said it
   was stuck. Free text in the answer is context, not capability. This is the whole
   reason `proposal` is a structured field rather than the model's prose.
2. **Attempts already punish it** (audit, `tasks.py:622,627-633`). Each block-unblock
   cycle burns one attempt; a task at cap parks itself with a reason. A job cannot
   block-loop indefinitely because the substrate stops it. This is an unplanned defense
   and it is the strongest one. Note the tension with 6.4's `restore_attempt`
   recommendation: **restore the attempt only for operator-answered blocks, and only
   twice.** Past that, the loop is the signal.
3. **The dedupe_key makes it visible.** A job raising the same block repeatedly is one
   card with a rising count, not N novel-looking asks.

Honest note: defense 1 is the real one; 2 and 3 are backstops. And the framing matters,
because this condition is easy to over-engineer into the trust-ladder cruft rejected in
Part 2. The threat model here is not a malicious agent. It is a model that has found a
cheap gradient. Removing the gradient (the answer grants nothing beyond the ask) is
sufficient and costs one design decision, not a subsystem.

### 8.6 Revocation while running

`revoke()` (`permissions.py:680`) removes a grant while a job holds its token.

**This already behaves correctly and the design should say so rather than add anything.**
`check(token_or_grant, store_uri, verb)` (`:710`) resolves the grant per invocation
(`_resolve_grant`, `:603`); a revoked grant fails to resolve; the next `check_action`
returns block. There is no cached capability and no long-lived decision. Revocation takes
effect at the job's next gated action, which is the correct semantics and the only one a
per-invocation check can have.

Two details:

- The job discovers this as a **block**, not a crash: it raises an escalation of
  `kind: "permission"`, which lands in Review, where the operator who just revoked can
  see the consequence of what they did. That is the right feedback loop.
- `_caveat_expired` (`:697`) already gives time-limited grants the same property, so
  expiry and revocation share one path.

The fencing analogue already exists for the task store: `claim_token` (`tasks.py:26`,
`update(expect_claim_token=...)`, `:746,762-765`) rejects a stale writer's finalize.
Grants get the same property from per-invocation resolution rather than from a token
comparison. Nothing to build.

### 8.7 Contradictory rules

Two textual rules disagree; the model picks one, silently. Zep's exact diagnosis:
"contradictory notes cohabit with equal weight and no salience".

**Recommendation, minimal:** when a block is answered with a textual rule,
`learnings.py`'s existing Jaccard >= 0.85 near-dupe check (`learnings.py:31-35`) already
surfaces the nearest existing entry. Show it in the Review tab at mint time: "This is
close to an existing rule: <text>. Replace / Keep both / Cancel." One read of a function
that already runs. Contradiction detection in general is not worth building for one
operator; catching the near-dupe at the moment of authoring catches most of it for free.

Enforced rules cannot contradict silently: `check()` is a conjunction, and a denial
wins. That asymmetry is another argument for promotion (7.4).

---

## Part 9: UI

Per `frontend/CLAUDE.md`: `src/ui` primitives, `tokens.css`, no inline hex, lucide only,
never emoji, five status kinds, all four list states, dark default.

### 9.1 Where blocked work lives: a fourth Work tab

`Work.tsx:50` is already `type Tab = "tickets" | "runs" | "autonomy"`.

```
type Tab = "tickets" | "runs" | "autonomy" | "review";
```

**Why this is not the approval queue that `orchestration-and-autonomy.md` 7.3
forbids.** That section pre-commits: "There are no approval queues, no per-action
confirmation, no allowlists, no audit journals." The distinction is real and this design
must be held to it:

- An **approval queue** sits *in front of* routine work. Work waits in it by default.
  It is a tax on everything, and it is the thing the operator has repeatedly rejected.
- The **Review tab** sits *behind* work that **already stopped on its own**. Nothing
  routes through it. If zero jobs block, it is empty forever and costs nothing. It is a
  view over `status == "blocked"`, which is a state the substrate has had since
  `tasks.py:61`.

The test: removing the Review tab does not let any blocked work proceed. It only removes
the operator's ability to see and answer it. That is a dashboard, not a gate. Section 7.3
stands.

**Why a tab and not a page:** B2 wants review + change access + approve + update rules in
one place. A ticket, its blocking run, and the rule that unblocks it are one train of
thought. Splitting them across routes splits the thought. And Work is already the
multi-tab home of work state.

**Badge:** the tab label carries a count when > 0, reusing the `danger` status kind that
`AutonomyPanel.tsx:363` and `AutonomyScopes.tsx:131` already use for `blocked`. Consistent
by reuse, not by convention.

### 9.2 The request card

One card per `dedupe_key` group (8.3), built from `Card` + `Stack` + `Row`. Icons by
`awaiting.kind`, lucide only:

| kind | icon |
|---|---|
| permission | `KeyRound` |
| decision | `GitBranch` |
| missing-info | `HelpCircle` (already used at `JobDetail.tsx:400`) |
| dependency | `Package` |
| environment | `AlertTriangle` |
| default / other | `HelpCircle` |

Contents, top to bottom:

- **Header**: `StatusPill status="danger"` with the kind; the question
  (`awaiting.question`); a `Tag` with the group count when > 1 ("8 tasks"); age from
  `since`; a `Clock` + deadline when set (8.1).
- **Body**: `why`, then `unblocks_if` as the emphasized line, because it is the thing the
  operator is being asked to make true.
- **Scope**: `scopeLabel` / `scopeHref`, already imported by `Work.tsx:38-39`.
- **Tasks**: collapsed list linking to the ticket drawer; expanded when the group is 1.
- **The proposed rule**, prefilled (7.3): for a `proposal`, a read-only line
  "`write` on `workshop://store/notes/project-foo`" plus a `Select` for duration (once /
  standing / 7 days, mapping to caveats, `permissions.py:697`). For a judgment ask, a
  `Textarea` prefilled with the question and answer, plus a scope `Select` **defaulting
  to the task's scope, never global** (7.5), and the near-dupe warning inline (8.7).
- **Actions** (`Row`, primary first): Approve once, Grant standing, Amend rule, Rescope,
  Reject, Defer. Rescope and Reject open the existing ticket drawer rather than
  duplicating its editor. Reject uses `ConfirmDialog` (destructive, per frontend/CLAUDE.md).

### 9.3 Batch actions

- Header row: "Select all in group", plus Approve once / Defer across selection.
- **Grant standing is deliberately not a bulk action**: it resolves the group by
  mechanism (8.2), so bulk would be a second path to the same effect and could diverge.
  One click on the group's card. This is the design showing through the UI, which is the
  point.
- Empty state (a required list state): `StateBlock` with "No blocked work." plus the
  help text from 9.4. This is the *normal* state and should read as healthy, not as an
  empty inbox that implies something is missing.
- Loading: `Skeleton`. Error: `StateBlock`. Overflow: cap at 50 groups with a count.

### 9.4 Latitude controls

**Scope level**: `AutonomyPanel.tsx`, in the same control group as `depth` and `fanout`,
because it is the same cascade level (4.2). A `Segmented` (already imported by
`Work.tsx:12`) with Strict / Standard / Open, and one line of help under it:

> What the worker does when the ticket does not say. Strict asks. Open decides.

Plus the sentence from 5.5, which is the thing that will actually save the operator
grief:

> If a strict scope blocks often, add a skill rather than loosening it.

**Task level**: the ticket drawer, beside `effort` (`tasks.py:34`), rendered as
inherit-by-default. Show the inherited value greyed with its source ("from project:
standard") so an override is visibly an override, not a mystery. This matters because
null-means-inherit (4.1) is otherwise invisible.

**Invocation**: the Run now control gains an optional latitude override, same shape as
the depth override that already exists there.

**Review tab, per card**: show the task's resolved latitude and its source. A block
raised at `open` is more interesting than one raised at `strict`, because at `open` the
job judged the question genuinely important, and the operator should read it that way.

### 9.5 What we do not build

- No `/review` route (6.3).
- No rule-editor page. Rules are minted from blocks (B4) and listed read-only in
  Settings.
- No policy DSL editor (Part 2).
- No per-action confirmation anywhere (`orchestration-and-autonomy.md` 7.3).
- No approval-queue routing of unblocked work (9.1).

---

## Part 10: sequenced phases, with file-level touch points

Sized against 4.2's claim: if this is more than a field plus a persona paragraph plus one
new small module, it is being built wrong.

### Phase E0: F3 prerequisite [BLOCKING for E4; independent of E1-E3]

Fix the learnings scope-key mismatch before anything mints rules into it (7.5).

- `scripts/project_ralph.sh:194`: pass `--learn-scope "project:$SLUG"` (or map bare slug
  to `project:<slug>` in `scripts/ralph.sh:157`).
- `scripts/ralph.sh:331-347`: stop recording raw reviewer reason codes as learnings (F15).

Per `review-findings-2026-07-15.md:127-129`. Small. **E4 must not ship without it.**

### Phase E1: the latitude field [PARALLEL-OK; additive only]

No behavior change. The knob exists and resolves; nothing reads it yet.

- `backend/services/agents_registry.py:65-72`: add `LATITUDES = ("strict", "standard",
  "open")`, `DEFAULT_LATITUDE = "standard"`, `_coerce_latitude`, beside `DEPTHS`/`FANOUTS`.
- `:160-163` parse; `:374-381` serialize, **non-default only**.
- `backend/services/tasks.py`: `latitude` on the record beside `effort` (`:34`), the
  create path (`:690,717`), the read echo (`:262`), and `update()` (`:737`), following
  `effort`'s pattern exactly (`:807`, empty string clears to None).
- Project registry: `latitude` beside `autonomy`.
- The resolver: extend the existing cascade helper with one key. **If this needs a new
  function, stop and re-read 4.2.**

### Phase E2: latitude actually does something [SEQUENTIAL after E1]

The advisory half. Cheap, and delivers C1/C3 on its own.

- `backend/services/chat_context.py`: a persona paragraph keyed on resolved latitude,
  in the **cache-stable prefix** (the discipline `learnings.py:24-28` and `skills.py:22-26`
  already follow). Three short variants (3.4), each stating the rule at the edge of the
  spec and, for `standard`, the reversibility definition.
- `scripts/ralph.sh`: accept `--latitude`, pass through to the worker prompt.
- Worker prompt: how to raise a block (E3's shape) and when, per latitude.

At the end of E2 the operator can set a project to strict and get a worker that asks
instead of guessing. That is C1+C3 delivered before any of the escalation UI exists,
which is why E2 is early.

### Phase E3: the structured escalation [SEQUENTIAL after E1; PARALLEL-OK with E2]

Widen `awaiting`. Additive: `TaskPanel.tsx:291-310` and `JobDetail.tsx:399-403` keep
working untouched.

- `backend/services/tasks.py:23`: the widened shape (6.2). Update the docstring.
- `:808-811`: patch the new fields; keep `question` required, everything else optional.
  Preserve the `tasks.py:781-782` clearing rule as-is.
- `dedupe_key` computation: reuse `learnings.py`'s canonicalizer (`:31-35`) rather than
  writing a second one.
- `backend/routes/`: the task action endpoint gains the six verbs (6.5); `unblock` and
  `reject` already exist per `TaskPanel.tsx:296-299`.
- `restore_attempt` on the operator-answered unblock path (6.4, twice max per 8.5).

### Phase E4: the Review tab [SEQUENTIAL after E3 and E0]

- `frontend/src/pages/Work.tsx:50`: the fourth tab, plus the badge (9.1).
- `frontend/src/components/ReviewQueue.tsx` (new): grouped cards, six verbs, batch,
  four list states (9.2, 9.3).
- `backend/routes/`: `GET /api/tasks?status=blocked` grouped by `dedupe_key`, with the
  8.2 guard-2 re-check before returning.
- Wire Grant standing to `mint_grant()` (`permissions.py:616`), Amend to
  `learnings.add` scoped to the task's scope (7.5, needs E0).
- `frontend/src/components/AutonomyPanel.tsx`: the latitude `Segmented` (9.4).
- Ticket drawer: the per-task override with its inherited-value display (9.4).
- Discord: a notify-only nudge with a deep link (6.3).

### Phase E5: the enforced floor [SEQUENTIAL after E3; the biggest phase, and separable]

Makes `touches` real. This is the audit's headline finding and it is deliberately last,
because everything above delivers value without it and it is the only phase with real
risk.

- `backend/services/gate.py` (new, small): `check_action(job_ctx, action)` (5.3),
  returning a prefilled escalation on block.
- Builder call site: a `PreToolUse` hook installed by the sweep wrapper.
- Assistant call site: the action dispatcher in `routes/chat.py`.
- `permissions.py`: no changes. Use `attenuate()` (`:647`) to mint the job's narrowed
  token from `touches`; `check()` (`:710`) to evaluate. **If this phase edits
  `permissions.py`, the design drifted.**
- The `agent` identity kind (5.4), pending the resource-access report's ruling. If that
  report lands a different answer, this phase adapts and E1-E4 are unaffected: that
  separation is deliberate.
- The 8.2 grant-time re-evaluation sweep.

### Phase E6: promotion and hygiene [SEQUENTIAL after E4 + E5]

- Promotion proposal cards from `learnings.py` provenance (7.4).
- Disuse expiry surfacing (7.5 rule 2).
- The near-dupe warning at mint time (8.7), reusing `learnings.py`'s Jaccard check.

**Parallelization:** E0 and E1 start together. E2 and E3 both follow E1 and are
independent of each other. E4 needs E0+E3. E5 needs E3 and is independent of E4. E6 is
last. The critical path to delivering C1/C3 is E1 -> E2, which is small.

---

## Part 11: coverage of the owned asks

| Ask | Where | Status |
|---|---|---|
| **B1** flag blocked by permission **or anything** | 6.1, 6.2 (`kind` open, four producers) | addressed |
| **B2** operator reviews: change access / approve / update rules | 6.3, 6.5, 9.1, 9.2 (one tab, six verbs) | addressed |
| **B3** programmatic **or** textual rules | 7.1, 7.2, 7.4 | addressed |
| **B4** rule updates driven **from the request** | 7.3 (`proposal` drives the offer) | addressed |
| **C1** name the axis | 3.1 (`latitude`; scopedness is not settable) | addressed |
| **C2** skills and rules make work well-scoped | 5.1-5.5 (advisory vs enforced; skills cut block rate) | addressed |
| **C3** goals-only vs hands-on | 3.4 (`open` vs `strict`) | addressed |
| **C4** project / agent / job level, inheriting? | 4.1-4.3 (existing cascade; agents are peers; no job-level field) | addressed |

---

## Part 12: open questions, each with a recommended default

1. **Is `latitude` the right name?** Alternatives: `discretion`, `autonomy_scope`,
   `strictness`, `delegation`. `strictness` is the operator's own first word but reads as
   enforcement (Section 5), which is the confusion to avoid. `delegation` collides with
   fanout. **Recommend `latitude`.** Cheap to rename before E1; expensive after E4.

2. **Three presets or two?** `strict` and `open` are C3's two named modes verbatim;
   `standard` is our invention and carries the reversibility rule that most of the design
   leans on. **Recommend three**, matching `DEPTHS` (`agents_registry.py:69`). If
   `standard` proves to be a value nobody consciously selects, it is still the right
   *default*, so the cost of being wrong is zero.

3. **Should `standard` auto-resolve reversible blocks at deadline (8.1)?** This is the
   one place the design lets a machine decide because a human did not answer. **Recommend
   yes, 4 hours, reversible-only, never for enforced gates, always surfaced post-hoc.**
   The alternative is an overnight loop that idles seven hours on "which test framework".
   If it misbehaves once, set the scope to `strict`, which is exactly what that setting is
   for.

4. **Is an autonomous job a third identity kind (`agent`)?** The brief assigns this to the
   resource-access report and calls it the central decision of the thread. **Recommend
   `agent`, minted via `attenuate()` (`permissions.py:647`)** (5.4). Flagged for
   cross-report reconciliation: only Phase E5 depends on it.

5. **Does the interface contract cover the out-of-tree `~/notes` vault?** Out of scope
   here, noted because E5's gate would need to know. **Recommend: no.** ADR 0002 keeps it
   separate; the escalation loop covers knowledge scopes and Stores.

6. **`restore_attempt` on operator-answered unblock (6.4)?** Trades 8.5's free defense for
   fairness to legitimately-blocked tasks. **Recommend yes, capped at two restores per
   task.** Past that, a task blocking repeatedly is signal, not noise.

7. **Should promotion (7.4) be automatic rather than proposed?** **Recommend proposed,
   never automatic.** An enforced rule minted without the operator looking is the
   defensive-cruft failure arriving through the back door, and the whole value of
   promotion is that the operator sees the evidence and says yes.

8. **Where does the Discord nudge fire?** **Recommend: on the first block in a scope per
   hour, not per block.** Ten blocks in a minute is one notification. Mass-blocking (8.3)
   is one problem, so it should ping once.

---

## Appendix: what this report deliberately leaves out

- **A separate checkpoint-frequency knob.** Derived from latitude (3.3). The one real
  casualty is a time-based progress ping, which is the Runs tab's job.
- **A `propose-only` latitude.** Expressible as `depth` + `acceptance: operator` (3.4).
- **Seven delegation levels.** Appelo's own caveat plus the anti-cruft rule (Part 2).
- **A policy DSL (Rego, Cedar).** `mint_grant` + `check` already are the policy engine
  (Part 2).
- **A durable-execution engine.** The Task is the durable state (Part 2, 6.4).
- **Trust ladders, zero-trust defaults, agent performance scoring.** Multi-tenant
  governance; violates the operator hard rule (Part 2).
- **Contradiction detection across the rule corpus.** Near-dupe at mint time catches most
  of it for free (8.7).
- **A rule-editor surface.** Rules are minted from blocks (B4, 9.5).
- **Audit journals of block decisions.** Evidence (`tasks.py:29`) already records what
  happened, as a byproduct of the answer.
