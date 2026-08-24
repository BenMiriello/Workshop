# Backlog reconciliation: resolving eight contradictions and sketching configurable limits

- Date: 2026-08-24
- Status: **DECISIONS. These settle conflicts between documents that are all
  current.** Where this disagrees with an earlier doc, this wins, and the earlier
  doc's section is named so it can be corrected in place.
- Written because the operator turned the autonomous loop OFF on 2026-08-24
  ("I don't want it running yet unattended when it's this broken and wasteful"),
  which makes the backlog a queue he releases by hand. A hand-released queue must
  be correct, not merely populated.

## 0. What the audit found, which is the reason this exists

Two audits over the 184-ticket store and the 22 documents changed since
2026-08-13:

- **27 of 79 open tickets are ALREADY DONE.** Their described output is in the
  tree. They are open because nothing closes a ticket whose work arrived by
  another route.
- **7 rest on a false premise**: a file, symbol, table or measurement they cite no
  longer exists or is no longer true.
- **7 are obsolete**, superseded or duplicated.
- **11 need a rewrite** before a worker could act on them.
- **18 of 79 have no Objective and no Output line at all.**
- **12 decisions have been made that have no ticket at all.**
- **8 pairs of current documents contradict each other.**

That last number is the important one. This repo's stated recurring defect is
parallel systems, and two current documents disagreeing is how the next parallel
system starts. So they get settled here rather than left for whoever reads one and
not the other.

## 1. The eight contradictions, settled

### 1.1 A learning folds in as a `kind`, or as a `reach`

`agents-and-memory-reassessment-2026-08-23.md` section 6.2 says a note KIND.
`what-a-memory-is-2026-08-23.md` section 5 and `the-plan-2026-08-23.md` section 2
reject exactly that and choose a `reach` field. Same day, opposite conclusions.

**`reach` wins.** A `kind` preserves the unanswerable write-time question of
whether a sentence is a note or a learning, which is the question that produced 11
rows of machine noise out of 49. `reach` asks a question with an answer: should
this be injected in full, indexed by title, or merely findable. Correct
reassessment section 6.2 in place.

### 1.2 "Nothing queues" versus pause-and-resume

`providers-credentials-and-limits-2026-08-23.md` section 7.3 says "Nothing queues.
A queued turn is a refusal that lies about when it will run."
`limiting-usage-2026-08-24.md` R2 makes pause-at-the-boundary plus a sweep that
prefers resumable tickets the number one action.

**Both are right, because they are about different things, and the distinction is
worth naming precisely: a refusal has no state and a pause does.**

- A REFUSED turn never started. It holds nothing. Queueing it means promising a
  future you cannot schedule, which is the lie section 7.3 objects to. Refusals
  still never queue.
- A PAUSED run already started. It has committed code, a branch and a resumable
  session. It is not a promise, it is unfinished work with a bookmark.

So the rule stands as written and gains one sentence: **a refusal never queues; a
pause is resumable work and is not a queue entry.** Add that to providers 7.3.

### 1.3 Tokens or money

Providers section 7.2: "Tokens remain the universal unit."
`limiting-usage` section 4: a token total is not a cost proxy and is not even
rank-preserving between scopes, because cache reads dominate and bill at about a
tenth of input. Measured: 258.8M chat tokens in one window, essentially all cache
reads, one single turn at 82,027,535.

**Money wins for LIMITS. Tokens stay for ACCOUNTING.** The row records all four
counters (input, cache read, cache creation, output) plus a computed `micro_usd`;
a limit is expressed in money. This is not a compromise, it is the correct split:
the counters are the measurement and the money is the policy. Nobody has an
intuition for thirty million tokens a month and everybody has one for a dollar
figure. Correct providers 7.2.

### 1.4 The reserve preserves a defect

Providers section 7.4 preserves `reserve: nominal_task` "so nothing regresses".
`limiting-usage` section 0 measures that same constant as wrong by five to seven
times. Preserving it verbatim preserves the defect.

**`limiting-usage` wins, and the reserve is demoted regardless**: it is a
scheduling hint, not a guard, because the guard is now pause-and-resume.

### 1.5 Ship no new default rules, versus ship two

Providers stage 6 and `the-plan` stage 4 forbid new default rules because defaults
are a business decision. `limiting-usage` 4C ships two.

**Resolved by the operator, 2026-08-24: "let's have the five hour and monthly."**
The prohibition was against inventing policy he had not asked for. He has now
asked. Ship exactly those two and no others.

### 1.6 The window enum cannot express the decision

Providers 7.3 declares `window` a closed enum of `rolling_5h`, `calendar_day`,
`calendar_week`, `calendar_month`. `limiting-usage` R6 decides the short limit
becomes a SESSION window, which that enum cannot express.

**Add `session` to the enum, with its length as a parameter rather than baked into
the name.** `rolling_5h` was already a bad enum member for the same reason: it
welds a shape to a duration. The member is the SHAPE (`session`, `rolling`,
`calendar`) and the duration is a field. That change is what makes section 2 below
possible at all.

### 1.7 `prune()` live or inert

Providers 7.2 skips a ledger backfill on the grounds that "prune() drops rows past
the window anyway". `limiting-usage` R4 measures that `prune()` has no live caller
and that 40 days of history survive only because of that.

**`limiting-usage` is correct.** The backfill reasoning in 7.2 is void. And
wiring `prune` without first raising its retention past the longest configured
limit would destroy the data a monthly limit needs.

### 1.8 Two documents are stale about the tools work

`audit-what-actually-runs-2026-08-23.md` section 3.1 lists tickets as implemented
only on branches when `frontend/src/pages/settings/Tools.tsx`,
`backend/services/tool_registry.py` and `.runtime/tools.json` are all present on
main. And `tools-are-declared-2026-08-23.md` section 1 still reads "no tools table,
no tools.json, no tool CRUD, no tools UI", which is false on disk.

**Correct both in place.** A status banner that lies is how a rebuilt-from-scratch
duplicate gets written, which is the failure that started this whole effort.

## 2. Configurable limits beyond the two: the sketch

The operator has asked for a sketch and reasoning, not tickets, and has twice
declined to request the feature. So this is design intent recorded for when it is
wanted, and deliberately not filed.

### 2.1 Why the default is two and not six

> A **short** window limits the RATE. It answers "how fast should this go".
> A **long** window limits the BUDGET. It answers "how much am I willing to spend".

Those are the only two questions a limit can answer. A third period does not answer
a third question; it interpolates between two answers already given. That is the
whole argument, and it is also the argument for why more periods are cheap to ALLOW
and wrong to DEFAULT: allowing them costs one row in a list, defaulting them
invents policy nobody asked for.

### 2.2 The shape

One rule, four fields that matter:

    { shape: session | rolling | calendar,
      length: a duration,
      amount: money,
      on_breach: refuse | warn | degrade }

plus a scope (deployment, principal, project, app, agent, provider or model) and an
id. Rules compose with AND. Every matching rule must pass. The most specific
refusing rule is the one reported, and a refusal always names which rule stopped
it, never a bare "limited".

Note that `shape` and `length` are separate, per 1.6. That separation is what makes
"any time frame the user chooses" a value rather than a new enum member each time.

### 2.3 What the operator actually sees

Two named rules by default, labelled by the question each answers rather than by
its period, because "Pace" and "Budget" are understandable and "rolling_5h" is not.
Everything else is an Add limit control behind Advanced.

**Autofill, which the operator raised specifically.** When a third limit is added,
default it to a WEEK, for a concrete reason rather than symmetry: a week is the
only period between five hours and a month that people actually plan in, and it is
also what Claude Code itself added as its second tier. Prefill the amount from the
existing two by simple interpolation and say in the field's hint that it was
guessed, so an unchanged prefill is never mistaken for a considered value.

### 2.4 What NOT to build

- No per-limit notification or alerting. A refusal already reports itself.
- No priority or weight between rules. AND composition is the entire semantics, and
  weights are how a limit system becomes a scheduler nobody can predict.
- No queueing on breach, per 1.2.
- No inheritance hierarchy between scopes. A rule matches or it does not.

### 2.5 Why it stays unbuilt for now

The two limits deliver the whole benefit for a single operator. The rule list is
the mechanism that makes the third cheap, and it is already the shape decided in
providers 7.3, so nothing here is blocked by not doing it. Build it when a second
person uses the system or when the operator wants a period the two do not cover.

## 3. What the backlog needs, in order

1. Close the 27 tickets whose work is already in the tree. This is the largest
   single source of confusion about what is left.
2. Cancel the 7 obsolete ones, naming what superseded each.
3. Correct the 7 false premises. Several are one dead path reference.
4. Rewrite the 11 that a worker could not act on, and give the 18 with no Objective
   or Output the four labelled lines.
5. File the 12 decisions that have no ticket.
6. Correct the doc sections named in section 1 above, in place.

Only after 1 through 4 is the count of open work meaningful. Today "79 open" is
really about 40.
