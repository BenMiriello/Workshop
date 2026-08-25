# Planning a run before it runs

- Date: 2026-08-25
- Status: **DESIGN. Nothing implemented.** Tickets filed at the end.
- Question from the operator: should a ticket carry a model pin? He answered his
  own question and landed somewhere better: *"maybe we don't set the model on the
  ticket but we set it on the agent that is taking up the ticket... if the ticket
  is not being taken up yet, but it will be, do we need some sort of... maybe we
  can assign an agent to it without it actually being executed yet... what I'm
  looking for is some way to associate an agent run with the ticket, but without
  actually running it, and that is where we could store the model."*
- Sits directly under `routing-work-to-a-model-2026-08-25.md` and answers the
  second of its four open questions (§8.2). Constrained by
  `work-status-contract-2026-08-24.md` §6 (settled: `done` is refused when the
  change is unmerged).

---

## 0. The decision, up front

**A planned run is an existing run in the `queued` state.** No new table, no new
object, no new status word. `services/jobs.py:52` already declares
`STATUSES = ("queued", "running", "awaiting_input", "done", "failed", "cancelled")`
and `db/models.py:852` already carries the same six in the `run` table's CHECK
constraint. Measured: **0 of 11,883 lines in `.runtime/jobs.jsonl` carry
`"status":"queued"`.** The state was declared and never written. This design
writes it.

- A run is planned by a new writer, `jobs.plan()`, beside `jobs.start()`. It
  mints the same `job_<epoch>_<hex>` id, sets `status="queued"`, records
  `task_id`, `agent_id`, and the resolved routing, and starts nothing.
- It stores the routing decision and **who made it**, as a source name and not a
  boolean: `policy | escalation | agent | operator`.
- At execution the resolver runs again. An answer sourced from `policy` is
  replaced by the fresh one. An answer sourced from `operator` is kept.
  **Escalation is not exempted from re-resolution and does not need to be**,
  because the escalated tier is stored on the ticket, so re-resolving reproduces
  it (§4).
- **The ticket gets no `model` field.** The pin the routing doc listed as an open
  question lives on the planned run (§6).
- **The ticket keeps its own status and the run keeps its own.** They already do.
  This design declines to merge them, on the evidence in §5.
- A planned run takes no claim, no lease and no worktree. It holds nothing, so
  nothing can be displaced by it or from it (§3.6).

---

## 1. What exists today

### 1.1 A run comes into existence only at the instant it starts

A run is the operator-facing name for a Job. `RunDetail.tsx:286` says so: the
route is `/runs` and `job` is the code noun. The store is
`.runtime/jobs.jsonl`, append-only, collapsed by id (`jobs.py:236-247`): 11,883
lines, 5,830 distinct ids.

Every writer opens the record already running. `jobs.start()` (`jobs.py:87-127`)
writes `id, agent_id, project_slug, chat_id, trigger, status, started_at, budget,
task_id, parent_id, pid, kind, binding`, and all seven call sites pass
`status="running"` (`routes/tasks_all.py:1631`, `routes/projects.py:1000`,
`services/triggers.py:382-400`, `services/deploy_runner.py:301,391`,
`services/background_runs.py:221`, `scripts/project_ralph.sh:201-207`). There is
no gap in which a run exists and has not begun.

The `run` table (`db/models.py:785-870`, migration `f3b8d2a17e94`, 2026-08-22)
carries `task_id` as a real FK column and has **no writer**: the migration's own
docstring says the writer switch belongs to a dependent ticket, and that ticket
has not landed. So `.runtime/jobs.jsonl` is still the only authority. One
schema detail matters here: `started_at` is `nullable=False`
(`db/models.py:817`). A queued run has not started, so the writer switch needs
that column nullable. It is one line in the migration that does the switch, and
it is named as a dependency rather than done ahead of it.

Nothing carries the model. `jobs.start()` has no model parameter, `JobEvidence`
has no model field, and the `run` table has no model column.

### 1.2 The claim, and where the boundary sits

`scripts/project_ralph.sh:1161` mints the child job id. `:1181` passes that same
id to `project_tasks.py claim`, which reaches `tasks.claim`
(`tasks.py:2468-2520`): a compare-and-set from `todo` to `doing` under the
store's flock, plus `_acquire_ticket_lease` (`tasks.py:2408-2432`), which takes
the ticket lease with `holder_kind="run"` and **`holder_id=job_id`**.

So the job id is already the lease holder identity, which forces the ordering:
the run record has to exist before the claim. A planned run does not disturb
this. It moves the mint earlier and leaves the claim exactly where it is.

The boundary is therefore precise and it is not a judgement call:

| | planned run (`queued`) | claimed run (`running`) |
|---|---|---|
| ticket status | unchanged (`todo` or `draft`) | `doing` |
| ticket lease | none | held, `holder_id` = this run's id |
| `claim_token` | not written | the lease generation |
| `attempts` | not incremented | incremented |
| exclusivity | none needed | atomic, one holder |
| can be discarded | freely | only by reclaim or preempt |

A planned run is a statement of intent. A claim is a statement of possession.
The claim is the only thing that excludes anybody, and it stays at
`project_ralph.sh:1181`.

### 1.3 Model resolution has nowhere to land

Today the model is chosen in bash from the ticket's `effort`
(`project_ralph.sh:1256-1265`), exported as `ANTHROPIC_MODEL` around the child
(`:1291`) and unset after (`:1300`). It is never recorded. `usage_ledger.record`
(`usage_ledger.py:139-150`) has `agent`, `job_id`, `task_id`, `run` and `role`,
and no model.

`routing-work-to-a-model-2026-08-25.md` §3.3 designs one resolver,
`work_model.resolve(scope, ticket) -> (handle, ceiling_handle, effort|None)`,
over a six-rung ladder. It does not say where the answer is written down. That
is this document's job.

### 1.4 A ticket's status is not ours alone

`triage_inbox.py:99-168` polls a bound GitHub tracker and files candidates;
`accept` (`:187-231`) creates a local ticket with a `sync` record. `tracker_sync.pull`
then applies remote closes: `tracker_sync.py:329` writes
`patch["status"] = trackers.local_status(...)`, mapping a remote close to `done`
or, for `not_planned`, to `cancelled` (`trackers/__init__.py:48-74`).

That write is on a collision course with the settled ruling in
`work-status-contract-2026-08-24.md` §6, which refuses `done` when the ticket's
change is unmerged. Someone closing an imported issue on GitHub asserts a fact
about the issue that our merge guard is entitled to refuse about our work. §5
resolves which of the two is talking about what.

### 1.5 Honest gaps in the record

- `scripts/lease.py` exists, is documented as existing for bash, and has zero
  callers.
- `jobs.py` `KINDS` is aspirational: the live `kind` values on disk are mostly
  not in the tuple.
- `usage_ledger`'s `run` parameter carries three different identifier spaces
  (job id, chat-turn id, `sweep-<job_id>` presence id). A planned run adds no
  fourth, because it reuses the job id.

---

## 2. Prior art: a run object that exists before it runs is the normal shape

- **GitHub Actions.** A workflow run's `status` includes `queued`, `waiting`,
  `requested` and `pending` alongside `in_progress` and `completed`, and
  `conclusion` is a separate field
  (https://docs.github.com/en/rest/actions/workflow-runs). The run object is
  addressable, listable and cancellable before any job starts.
- **GitLab.** A job's status is
  `canceled, canceling, created, failed, manual, pending, preparing, running,
  scheduled, skipped, success, waiting_for_callback, waiting_for_resource`
  (https://docs.gitlab.com/api/jobs/). `created`, `manual` and `scheduled` are
  all jobs that exist, carry their full configuration, and have not run. A
  `manual` job may sit there indefinitely waiting for a person, which is exactly
  the operator's case.
- **Jira Cloud.** A build's state is `pending, in_progress, successful, failed,
  cancelled, rolled_back, unknown`
  (https://developer.atlassian.com/cloud/jira/software/rest/api-group-builds/),
  entirely separate from the issue's workflow status.

None of the three invented a second object for the pre-execution phase. Each
gave the execution object a state that means "planned". That is the design.

---

## 3. The planned run

### 3.1 The object and its identity

The Job record already in `.runtime/jobs.jsonl`, written by a new `jobs.plan()`
in `status="queued"`. Its id is the same `job_<epoch>_<hex>` string. That id then
carries unchanged into the claim (`holder_id`), into `ticket.job_ids`, into the
sweep's `--run` argument and into every usage ledger row for the attempt. One id
spans intent, execution and spend, which nothing has today.

### 3.2 What it stores

Existing fields, unchanged in meaning: `id`, `task_id`, `agent_id`,
`project_slug`, `trigger`, `parent_id`, `kind`, `budget`.

New fields, each with a named reader:

| field | meaning | reader |
|---|---|---|
| `model` | resolved handle | the sweep, which exports it instead of computing it |
| `model_tier` | the short name it resolved to | ticket page, run row |
| `model_ceiling` | the band's ceiling handle | escalation, to know when to stop |
| `reasoning_effort` | third element of `resolve()`, may be null | the turn site |
| `model_source` | `policy \| escalation \| agent \| operator` | the re-resolve rule (§4) |
| `resolved_at` | when the routing was last computed | shown on the ticket page and the run row, never consulted |

`resolved_at` is display only and is stated as display only, because
`agents_registry.py:132-140` deleted two fields for having no reader and named
the failure. Its reader is the line "planned on Sonnet, 3 days ago".

`started_at` is absent while queued and written at the transition to `running`.

### 3.3 Lifecycle

```
          plan()                claim + start            finish()
  (none) --------> queued -------------------> running -----------> done
                     |                            |                 failed
                     |                            |                 awaiting_input
                     +--------> cancelled <-------+                 cancelled
```

Four rules, and no new status word:

1. `queued` to `running` is the existing claim. If the claim returns `{}` the run
   goes straight to `cancelled`, which is what `project_ralph.sh:1183-1193`
   already does with a lost race today.
2. `queued` to `cancelled` happens when the ticket leaves `todo`/`draft`, when
   the operator discards the plan, or when a newer plan replaces it. The reason
   is written into `evidence`.
3. A `queued` run has no timeout and is not reconciled by
   `jobs.reconcile_stuck` (`jobs.py:387-420`), which measures liveness against
   `started_at`. A plan is allowed to sit.
4. Nothing else changes. `running` and the three terminal states behave as they
   do now.

### 3.4 How it is created, and how many

**Both, through one writer.** The executor plans and immediately starts, so the
`queued` state usually exists for microseconds; a person or a chat can plan
earlier and leave it sitting. Same function, same record, one code path, which is
the reason to do it this way rather than adding a planning path beside the
execution path.

- `project_ralph.sh` replaces `job_start_child` with a plan-then-start pair, or
  adopts the ticket's existing `queued` run if there is one.
- The ticket page gets a Plan action next to Run now. The proposal card in chat
  can produce a planned run at the same time it produces a ticket.
- **Auto-planning every ready ticket is not proposed here** and is operator
  question 3.

**At most one non-terminal run per ticket.** A second `plan()` for a ticket that
already has a `queued` run replaces it and cancels the first with reason
`superseded`. One `running` run per ticket is already enforced by the ticket
lease and is not re-enforced here. Terminal runs accumulate in `job_ids` as
history, which is what that list already is.

The link is written at plan time: the run carries `task_id`, and `plan()`
appends its id to `ticket.job_ids`. The claim's existing append is already
guarded by `if job_id not in found["job_ids"]` (`tasks.py:2513`), so it needs no
change.

### 3.5 The ticket changing underneath

A planned run stores no copy of the ticket. It stores a routing decision. So:

- **Title, body, labels, boundaries change:** nothing happens. The worker reads
  the ticket at execution, not at plan time.
- **`effort` changes, or the policy table changes:** nothing happens at edit
  time. The re-resolve at execution picks it up. This is the whole reason
  re-resolution exists, and it means there is no invalidation machinery to build
  and no cache to sweep.
- **Status leaves `todo`/`draft`:** the queued run is cancelled with the reason.
  This includes a remote close arriving through `tracker_sync.pull`.
- **`assignee` changes:** the queued run's `agent_id` is re-derived at execution
  the same way the model is, unless a human set it, under the same rule as §4.
- **An operator edits a ticket that has a queued run:** nothing. The `claim_token`
  bump at `tasks.py:2317` exists to fence a *running* worker. A queued run holds
  no token and cannot clobber anything.

---

## 4. Re-resolution, pins, and escalation

### 4.1 A boolean is not enough

A boolean `pinned` distinguishes two cases and there are four writers: the policy
table, escalation, the agent's own `model` pin (`agents_registry._coerce_model`,
`:228-236`), and a person. Escalation is automatic and must **outrank** a stale
policy answer, so a boolean would have to call it pinned, which would then also
protect it from a policy change the operator deliberately made. Store the source
name instead:

```
model_source: "policy" | "escalation" | "agent" | "operator"
```

`operator` means a person chose this model for this run. Everything else is
machine-chosen and replaceable.

### 4.2 The rule

At the moment of execution, before the claim:

```
if planned.model_source == "operator":
    keep planned.model, planned.reasoning_effort
else:
    (model, ceiling, effort) = work_model.resolve(scope, ticket)
    overwrite, and set resolved_at
```

One comparison, one call. The operator's requirement is met exactly: *"unless the
user has changed that setting... whatever the global default is... should be
consulted again and should cascade down to this decision again, even if it was
set once in the past."*

### 4.3 Why this does not undo an escalation

The apparent conflict is that re-resolving could discard the tier a failed
attempt escalated to. It does not, and the reason is a storage decision already
made by the routing doc rather than a special case added here.

`routing-work-to-a-model-2026-08-25.md` §3.4 stores the escalation **on the
ticket**, in `attempt_tier`, written by `release_or_block` on the way past, and
§3.3 reads it at rung 2 of the ladder, above the policy rows. So calling
`resolve()` again returns the escalated tier, because `resolve()` consults the
ticket. Re-resolution reproduces the escalation instead of overwriting it.

`model_source: "escalation"` on the planned run is therefore a **label on a
derived value**, not a second home for the fact. It exists so the ticket page can
say "escalated to Opus after a failed review" rather than "Opus", and so §7's
falsification test can count escalated retries. One fact, one home, per
`one-fact-one-place-2026-08-22.md`.

The three-way outcome, stated once:

| stored source | policy changes underneath | ticket escalates | result at execution |
|---|---|---|---|
| `policy` | yes | no | new policy answer |
| `policy` | no | yes | escalated tier, from rung 2 |
| `operator` | either | either | the pinned model, and no escalation |

The last row is a real consequence and it is intended: a human pin means the
system stops guessing, in both directions. Whether the pin should also carry
forward to the **next** attempt is operator question 1.

---

## 5. Two status sets, and it is not a new one

**Recommendation: two, and they both already exist.** `tasks.STATUSES`
(`tasks.py:150`, eight values) and `jobs.STATUSES` (`jobs.py:52`, six values).
This design writes one previously unused value and adds nothing.

The operator's instinct against a second set was right about the thing he was
worried about, which is a second set describing the same fact. These do not
describe the same fact. A ticket's status answers "what is the state of this
piece of work". A run's status answers "what happened to this attempt". A ticket
with three failed runs and one succeeded run has one status and four run
statuses, and no single field can hold that.

### 5.1 The evidence

Every established system separates them, and none of the three collapses the pair
even where the integration is tight:

- GitHub: issue `state` is `open | closed` and nothing else, while a workflow run
  carries `queued/waiting/requested/pending/in_progress/completed` plus a
  separate `conclusion`.
- GitLab: an issue is `opened | closed`; a job has thirteen states.
- Jira: an issue's status comes from its workflow; a linked build reports
  `pending/in_progress/successful/failed/cancelled/rolled_back/unknown`.

This repo already reached the same conclusion for a neighbouring question.
`work-status-contract-2026-08-24.md` §2: GitHub keeps `state`, `merged` and
released as **three orthogonal facts in three fields**, and the doc's ruling was
explicitly *not* to add a ninth ticket status. The same reasoning applies one
step over, and the tree already agrees: `CHANGE_STATUSES` (`db/models.py:1193`)
is a third vocabulary over the same work, deliberately.

### 5.2 The case that decides it

The operator's own: a ticket defined elsewhere, whose state changes without us
knowing.

An issue is imported through `triage_inbox.accept`. A planned run sits `queued`
against it. Someone closes the issue on GitHub. `tracker_sync.pull` reaches
`tracker_sync.py:329` and writes `status="done"` locally.

With two status sets, the outcome is well defined and every field is telling the
truth: the ticket is `done` because the authority that owns it said so, and the
queued run goes to `cancelled` with reason "ticket closed externally". Nobody
claims an attempt happened.

With one status set, closing an issue on GitHub would be asserting a fact about
our execution that never occurred, and there would be no field left to say what
actually happened to the run. The external case does not weaken the two-status
answer; it is the strongest argument for it.

There is a residual collision, and it is not created by this design: the settled
refusal of `done` on an unmerged change (§6 of the work-status contract) and the
remote close at `tracker_sync.py:329` are two authorities writing one field.
That is operator question 2.

### 5.3 Where each one is shown

- **Ticket list, ticket card in chat, notifications:** ticket status only. One
  pill.
- **Ticket page:** ticket status as the pill, plus a runs section listing this
  ticket's runs with their own statuses, the planned one first. A queued row
  reads "planned, will run on Sonnet", not a status word on its own.
- **`/runs` and `/runs/:id`:** run status. Queued runs appear in the list, which
  is the first time that page can answer "what is about to happen" rather than
  only "what happened".
- **Nowhere** shows a merged pill combining the two.

---

## 6. What this replaces

**The ticket gets no `model` field.** `routing-work-to-a-model-2026-08-25.md`
§8.2 asks the operator to decide "whether the ticket keeps a `model` pin at all",
naming the tension precisely: it is the escape hatch that makes the policy table
safe to be wrong about, and it is also the field most likely to be set once and
quietly outlive its reason. This design keeps the escape hatch and removes the
outliving, by putting the pin on the run rather than on the ticket. A run is
consumed once, so a pin on it cannot go stale; the next attempt re-resolves.

Concretely, against that document:

- **§3.3 rung 1**, "the ticket's own `model` pin", is deleted. The ladder becomes
  five rungs: escalation state, agent pin, scope policy row, global policy row,
  built-in default. A human pin is not a rung at all; it is the `operator` source
  on the planned run, checked before the ladder is entered (§4.2).
- **§3.3 rungs 2 to 6 are unchanged**, and `resolve()` keeps its signature.
- **§3.4 escalation is unchanged.** `attempt_tier` stays on the ticket. §4.3 is
  the reason it must.
- **§6's ticket-page line**, "standard: starting on Sonnet, may escalate to
  Opus", becomes a property of the planned run rather than a computation the
  page redoes. Where there is no planned run the page still computes it.
- **§8.2 is answered** and is not repeated as an operator question here.
- **§7's first falsifier** ("record the tier on every attempt... compare the
  success rate of retries that escalated against retries at the same tier")
  becomes cheap: the run record already carries the ticket, the tier and the
  source, so the comparison is a query over `jobs.jsonl` and not new
  instrumentation.

Nothing else is replaced. The claim, the lease, `ticket.job_ids`, `assignee`,
`effort`, `execution`, the tracker field-ownership table and the eight ticket
statuses are all unchanged.

---

## 7. Failure modes

**A stale plan.** A run planned three weeks ago against a ticket whose body has
been rewritten. It runs anyway, because the worker reads the ticket fresh. The
only stale content is the model choice, and §4.2 refreshes it. The one thing that
genuinely survives is an `operator` pin, which is what a pin is.

**Queued runs accumulating.** If auto-planning is turned on (question 3), 95 open
tickets produce 95 queued runs, and `/runs` fills with rows describing nothing
that happened. Mitigation if it is turned on: plan only what the sweep would
select next, not everything ready. This is the reason auto-planning is a question
and not a decision.

**A plan that can never execute.** A planned run against a ticket that fails
`ticket_readiness.check`. Planning does not check readiness, on purpose: a plan is
a statement of intent and intent may precede readiness. Execution still refuses.
The ticket page must say which of the two is true, or this becomes another
control that lies.

**The writer switch.** When the `run` table gets its writer, `started_at`
(`db/models.py:817`) must become nullable in the same migration or every queued
run is unwritable. Named in §1.1 and carried on the ticket.

**What would falsify this design.** If, six months in, the count of runs that
were ever observed in `queued` for longer than one second is near zero, then
nobody plans ahead, the whole pre-execution phase is machinery for a case that
does not arise, and the honest response is to keep `jobs.plan()` as the internal
mint-then-start pair and delete the Plan action and the queued rows from `/runs`.
The design still works in that configuration, which is the point of building it
as one writer.

---

## 8. For the operator to decide

Three. Everything else here follows from something measured.

**1. When a run you pinned to a model fails and the ticket is retried, does the
next attempt use your model again, or go back to automatic?**

- *Sticky:* the pin is copied onto the next planned run. Your choice keeps
  applying until you change it. The cost is that a pinned ticket never escalates,
  so if you pinned it too low it will burn all its attempts at that model and
  block, and the block reason has to say the model was pinned.
- *Per attempt:* the pin applies to the run you pinned and nothing after it. The
  retry re-resolves and can escalate. The cost is that a deliberate choice
  silently expires after one failure.

Recommendation: **sticky.** You pinned a model for the work, not for one attempt,
and a choice that evaporates on failure is a choice you have to remember to make
again at the worst moment. Take the blocking case and make the block reason name
the pin.

**2. When someone closes an imported GitHub issue and our own rule says the
ticket cannot be `done` because its change is not merged, which one wins?**

- *The remote close wins:* the ticket goes `done`, and a note on the ticket
  records that its change was never merged. Simple, and it matches the tracker
  field-ownership table, where the tracker owns the words and the close.
- *The refusal wins:* the pull is rejected, the `sync` record goes to `conflict`,
  and the ticket stays where it is until you resolve it. Consistent with the
  2026-08-25 ruling, at the cost of our copy disagreeing with GitHub until you
  act.

Recommendation: **the remote close wins, with the note.** The refusal exists to
stop *us* asserting something nobody checked. A person closing an issue on GitHub
already happened, and refusing to record a fact that occurred elsewhere is not
available to us. The note preserves what the refusal was protecting.

**3. Should the system plan a run for every ticket that is ready to run, so you
can see the queue before anything starts?**

- *Yes:* every ready ticket gets a queued run showing which agent and model it
  will use. `/runs` becomes a forward-looking queue. Cost: about 95 queued rows
  today, most of which will be cancelled and re-planned as the policy or the
  backlog changes, and the Runs page fills with things that have not happened.
- *No:* a planned run exists only when you or a chat asks for one, or for the
  microseconds before the sweep starts it. `/runs` stays a record of what
  happened. Cost: you cannot see next week's routing without asking for it
  ticket by ticket.

Recommendation: **no, with one exception.** Plan the ticket the sweep would pick
next, so there is always exactly one visible "up next" row. That gives the
forward view at a cost of one row instead of ninety-five, and it is deletable
without touching anything else if it turns out nobody looks at it.

---

## 9. Tickets filed

Sequenced. 1 blocks 2, 5 and 6; 2 blocks 3; 3 blocks 4.

| id | title | priority |
|---|---|---|
| `d0ea84b4` | A run can exist in the queued state before anything starts it | high |
| `62262e77` | A planned run stores the model it will use and who chose it | high |
| `ca29a29b` | The sweep adopts the planned run instead of minting its own | high |
| `60972a9c` | Re-resolve the model at execution unless a person pinned it | medium |
| `860966e5` | A ticket's runs are listed on its page, with the planned one first | medium |
| `82afaea4` | Closing an imported issue elsewhere cancels its planned run | medium |
