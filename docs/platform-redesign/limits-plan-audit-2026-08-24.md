# Audit: is the usage-limits plan implementable?

- Date: 2026-08-24
- Audits `limiting-usage-2026-08-24.md` (including revision 2) and the five
  tickets filed from it.
- Standard applied, in the operator's words: length is not implementability, and
  the plans that held up were the ones whose author actually ran the commands.
  So every factual claim below was re-derived from the tree and from
  `.runtime/usage.jsonl`, `.runtime/ralph.log` and the 161 `.runtime/ralph-iter-*.out`
  files, and each is marked CONFIRMED, FALSE or NOT REPRODUCIBLE.
- Verdict up front: the plan's DIRECTION is right and the operator's two
  corrections (an estimate cannot be load bearing, pause and resume is the fix)
  survive audit and are strengthened by the measurements. The tickets are not
  right. Four of five carried at least one false measured claim, and the two on
  the critical path carried the worst of them. All five were cancelled and
  re-filed.

## 1. What was verified against the code

### 1.1 The ralph.sh claims in ticket 32bd76cf

| Claim | Verdict | Evidence |
|---|---|---|
| ralph runs a task as a loop of iterations | CONFIRMED | `scripts/ralph.sh`, the `for iter` loop, `MAX_ITER` |
| keeps this run's own `SESSION_ID` at about :737 | CONFIRMED | `SESSION_ID=""` is exactly `scripts/ralph.sh:737`; captured at `:949-978` |
| passes `--resume` with it at about :825 and :949 | CONFIRMED | `RESUME_FLAG=(--resume "$SESSION_ID")` at `:826`; the capture that feeds it at `:949` |
| auto-commits work a worker left uncommitted at about :342 | CONFIRMED as a line, **FALSE as a checkpoint** | see below |
| "an iteration boundary is therefore already a checkpoint with committed code" | **FALSE** | see below |
| the no-progress park is at about :1143 | CONFIRMED | `scripts/ralph.sh:1143-1144`, `exit_code=6` |

**The false one, and it is the load bearing one.** `maybe_autocommit` is defined
at `scripts/ralph.sh:252` and logs at `:342`, so the line reference is right. But
it is called from exactly one place, `scripts/ralph.sh:1077`, and that call is
guarded:

    (( COMPLETE == 1 )) && maybe_autocommit

`COMPLETE` is the reviewer's verdict for this iteration. The net therefore fires
only after the gate has APPROVED the work, never at an ordinary mid-run boundary.
It also early-returns the moment any commit already exists between `BASELINE` and
`HEAD` (`landed` non-empty at `:254`), so it is not a general "commit whatever is
loose" mechanism even then.

Consequence for the ticket: at the boundary where a pause would happen, the tree
may hold uncommitted work and nothing in the current code will commit it. The
feasibility paragraph in both the design doc (R2, "Pausing is using machinery that
exists across a boundary it does not currently cross, not building new machinery")
and the ticket is wrong. The pause path has to commit explicitly. It can and should
reuse `maybe_autocommit`'s lane discipline (it only ever stages paths inside the
task's declared `touches`, for a reason recorded at `:256-262`), but it must not
reuse its approval guard. That is real new code, and an engineer who believed the
ticket would have discovered it only after being told the hard part was already done.

### 1.2 `usage_ledger.prune()` has no live caller

CONFIRMED. `backend/services/usage_ledger.py:359` defines it. A repo-wide search
for callers returns exactly two: its own CLI at `:797` and
`backend/tests/test_usage_ledger.py:135`. Nothing on any read or write path calls
it.

The consequence the doc draws is CONFIRMED too, with a bigger number than it
quoted: the ledger now holds **5,029 rows** (the doc said 4,876, measured earlier
the same day) spanning `2026-07-15T04:27:06Z` to `2026-08-25T00:38:15Z`, about 41
days. That history exists only because the inert mechanism stayed inert, and it is
the only history a monthly limit could be enforced against.

### 1.3 `WINDOW_S` is the only enforced period

CONFIRMED. `WINDOW_S = 5 * 3600` at `backend/services/usage_ledger.py:45`. The
admission path (`_admission`, `:697`) passes no `window_s` to `window_rows` or
`window_usage`, so every one of its three refusal branches is evaluated over the
default 5 hours. `status()` at `:525` takes `window_s` purely for the reporting
lookbacks and sets `capped = window_s == WINDOW_S`, reporting `allowance` and
`remaining` as `None` on any wider window. Nothing daily, weekly or monthly gates
anything.

### 1.4 The cost figures quoted from `.runtime/ralph.log`

Individually CONFIRMED, collectively **MISLEADING**, and the summary statistic
drawn from them is **FALSE**.

All six quoted values exist verbatim in `.runtime/ralph.log`: 750,842; 798,189;
855,699; 1,042,287; 1,045,598; 168,643. But there are 168 `billed N tokens` lines
in that log, not six, and the six chosen are the top of the distribution:

    n = 168
    min      24,559
    p50     107,215
    p60     150,000
    p70     154,859
    p80     231,965
    p90     579,416
    p95     798,189
    max   1,045,598

The ledger's own `role="worker"` rows agree: n = 93, median 108,494, max 1,045,598.

So "Median around 800,000" is FALSE by a factor of about 7.4. The true median is
about 108,000. And "admission under-reserves by five to seven times" is FALSE at
the median: the 150,000 reserve is slightly ABOVE the median run. It under-reserves
only in the tail, where it under-reserves by up to 7x.

The true spread is also larger than either the doc or the ticket says. Both say
"168,643 to 1,045,598, a 6x spread". The measured range is **24,559 to 1,045,598,
a 43x spread**. That correction does not weaken the operator's argument, it
strengthens it: a 43x spread is even less predictable than a 6x one, so
"a design whose correctness DEPENDS on the estimate is wrong" holds a fortiori.

### 1.5 `nominal_task_tokens` is what the ticket says

CONFIRMED, and then the inference from it is FALSE.

`.runtime/usage-budget.json` reads `"nominal_task_tokens": 150000`, matching
`_DEFAULT_CONFIG` at `backend/services/usage_ledger.py:83`. The live config also
sets `app_total: 5000000`, `autonomy_budget_fraction: 0.65` and a `ceil` of
1,500,000 each for `development` and `workshop`.

**But `nominal_task_tokens` does not guard the ceiling the tickets say it
overshot.** In `_admission` the reserve is computed once,

    reserve = nominal * (1 + _inflight_coordinator_sweeps(exclude_job_id))

and it is applied to exactly one branch, the app-wide one:

    if usage["app"] + reserve > autonomous_ceiling:

The two per-scope branches immediately below it use no reserve at all:

    if ceil is not None and used_scope >= float(ceil):
    if allowance is not None and used_scope >= allowance:

The overshoot both the doc and ticket 4ea2a388 cite (1,972,531 against a 1,500,000
ceiling) is a SCOPE ceiling breach. It went through a branch that has never
consulted `nominal_task_tokens`. Raising the reserve from 150k to a history-derived
p80 would not have prevented it, because that branch does not read the reserve.
This is the single clearest example of a plan whose author did not run the
commands: the fix is aimed at a constant that is not in the code path of the
failure it is justified by.

### 1.6 `ralph.sh` bills `NOMINAL_TOKENS` on a timeout-killed iteration

CONFIRMED, exactly at the claimed line.

    scripts/ralph.sh:1011  if (( CLAUDE_EXIT == 124 && ITER_TOKENS == 0 && ITER_DUR > 60 )); then
    scripts/ralph.sh:1012    RUN_TOKENS=$((RUN_TOKENS + NOMINAL_TOKENS))

The consequence is visible in the data. Ten ledger rows carry a `tokens` value of
exactly 150,000, seven of them on 2026-08-22 and 2026-08-23, six of those in the
`development` scope. Those are proxy values sitting in the history indistinguishable
from measurements, which is exactly why any p-something estimator computed over this
ledger would be computing over partly synthetic data.

### 1.7 Two claims nobody asked me to check, which change the design

**(a) The ledger's `tokens` column is not one unit. It is three.**

- `scripts/ralph.sh:900-908` and `scripts/reviewer.sh:1105-1108` compute
  `input + 0.1*cache_read + cache_creation + output`. Cache reads are already
  discounted tenfold, deliberately (review L3, the comment is in the code).
- `backend/services/build_streaming.py:1301` `_spend_tokens_from_usage` sums the
  same four counters at **parity**, and that is what both the text chat path
  (`backend/routes/chat.py:2203`) and the voice path
  (`backend/services/voice_worker.py:396`) record.
- `backend/routes/chat.py:2203`'s compaction sibling records a `chars/4`
  ESTIMATE, because `call_cloud` returns no token counts at all.

So the design doc's sentence "the ledger sums input, cache read, cache creation
and output into one number" is FALSE for the autonomous path, which already
applies the tenth. Anyone implementing money on top of this without knowing that
will apply the discount twice on worker rows and not at all on chat rows.

It also explains a number that would otherwise look like a catastrophe. August
2026 by role: chat 6,154,567,830; compaction 16,678,481; worker 15,984,370. Chat
looks like 99.7% of all usage. Converted to a common unit (the observed
weighted-to-raw ratio on real iterations is about 0.12) chat is roughly 740M
weighted against worker's 16M, so autonomous work is about 2% of comparable spend,
not 0.26%. Both figures are still dominated by the operator's own chat, which is
excluded from admission on purpose.

**(b) Dollars are already being measured, and thrown away.**

The Claude CLI's result envelope carries `total_cost_usd`. `build_streaming.py:889`
parses it and keeps it as telemetry; nothing records it. It is present and
non-zero in ralph's own iteration output files right now: 80 of the 161
`.runtime/ralph-iter-*.out` files carry a parseable result envelope, and their
costs are

    min $0.089   p50 $0.421   mean $0.712   p90 $1.955   max $4.578
    total across the retained files: $56.95

Cross-checked against the same envelopes' token counters, the implied rate is
**$4.00 per million weighted tokens** (median; mean $4.28), which is stable enough
across 80 samples to use as a conversion. The weighted token totals in those
envelopes reproduce the ledger's billed values exactly (854,338; 618,525; 616,028
and others all appear in both), which confirms the parse.

This matters for ticket 106d996b and is answered in section 4.

## 2. Per-ticket audit

### 2.1 32bd76cf, pause a run at the budget cap. **Right ticket, wrong facts.**

Could an engineer start Monday morning with the body and nothing else? No, and
the reason is that the body tells them the hard part is done.

Blocking questions, with the sentence each comes from:

1. *"An iteration boundary is therefore already a checkpoint with committed
   code."* It is not, per 1.1. What commits the work at the pause boundary, and
   does it obey the task's `touches` lane the way `maybe_autocommit` does?
2. *"backend/services/usage_ledger.py can_admit for the remaining-budget query."*
   `can_admit` returns `(bool, str)`. It does not return a remaining budget, and
   there is no accessor that does except
   `status()["projects"][scope]["remaining"]`, which exists only when
   `window_s == WINDOW_S`. Which one should be called?
3. Nothing in the body notes that **ralph bills the ledger once, at exit**
   (`scripts/ralph.sh:1213`). Mid-run, the ledger has no knowledge of this run's
   spend, so a naive remaining-budget query is stale by the entire run so far.
   Remaining must be reduced by `RUN_TOKENS` (accumulated at `:916`). Unstated.
4. *"exiting with a distinct code so the sweep can tell a pause from a failure."*
   Which code? 0, 1, 2, 3, 5, 6 and 7 are all taken. And `project_ralph.sh` has a
   `*)` default leg at `:1559` that release-or-blocks as a plain failure, so an
   unwired new code degrades silently into "failed (rc=8)".
5. *"it needs its own visible state."* Does that mean a ninth status token? The
   eight live in `backend/services/tasks.py:147` (`STATUSES`), `:180`
   (`_STATE_TYPE`), `backend/db/legacy_map.py:23` (`STATES`), plus at least eight
   frontend switch statements. That is a large, unscoped change hiding inside one
   clause, and it is avoidable.
6. Worktree mode is not mentioned once. In `WT_MODE` the run holds a lease and a
   locked worktree on a `wt/<slug>/<branch>` branch, and `cleanup_merged_worktrees`
   reaps merged trees at the end of a sweep. A paused run's tree must survive that.

The good news is that the ticket's own core is sound and one of its claims is
better than it knew: the receipt already carries `session_id`
(`scripts/ralph.sh:1227` and `:1289`), and `project_ralph.sh` already reads the
receipt (`build_receipt_evidence`, `:238`) before deleting it at `:1568`. So the
bookmark has a path to the ticket that needs plumbing, not invention.

**Action: cancelled and re-filed.**

### 2.2 57dac664, two limits. **Right shape, one false premise, and no number.**

Line references CONFIRMED: `WINDOW_S` at `:45`, `window_rows` at `:217`,
`window_usage` at `:245`, `prune` at `:359`, `_DEFAULT_CONFIG` at `:78`, `status`
at `:525`. This ticket's author did open the file.

Blocking problems:

1. **The central justification is false.** *"a rolling window returns budget in
   dribbles far smaller than a unit of work"*. Workshop's limiter is a sliding
   window LOG, not a decaying counter: budget returns in exactly the lumps it was
   spent, when each row ages past five hours. Measured on the actual data, the
   `development` window went 1,972,531 (21:00) to 1,867,674 (00:00) to 750,842
   (02:00). Those are steps of 104,857 and 1,116,832, not a dribble. The design
   doc's R6 makes the same error.
2. *"decayed only 180,000 in five hours"*: **NOT REPRODUCIBLE**. Over the five
   hours following the 1,972,531 peak the window fell by 1,221,689. In a sliding
   window log a five hour decay of 180,000 is not possible unless new spend
   arrived, and the loop was refused throughout.
3. *"a refusal names WHICH limit stopped it, never a bare 'limited'"* is written
   as new work. It is already true: `_refusal` records
   `kind in {app_ceiling, scope_ceiling, scope_allocation}` and the whole sentence.
   The real work is adding a fourth kind, not building the mechanism.
4. **No amount for the monthly limit.** The ticket says keep 1,500,000 for the
   short one and adds a monthly with no number and no unit. An engineer cannot
   ship this. Answered in section 4.
5. **No session anchor rule.** A session window resets from first use, so
   something has to define where a session starts. Per scope or app wide? Derived
   from the log or stored? Unstated, and the two configured scopes would drift
   apart under the per-scope reading.
6. **Silent on what the monthly counts.** `ADMISSION_EXCLUDED_ROLES` keeps chat
   and compaction out of admission. A monthly cap that inherits that exclusion
   governs about 2% of measured spend. The design doc lists this as OPEN in
   section 6; the ticket neither resolves it nor repeats it.
7. Silent on whether `app_total` and the scope `ceil` also become session shaped,
   which they must, since the app branch is what usually refuses first.

**Action: cancelled and re-filed, with the number, the anchor rule and the scope
decision supplied.**

### 2.3 4ea2a388, reserve what a run actually costs. **Superseded and factually wrong.**

This ticket carries a long `evidence` entry from 2026-08-24 recording that the
operator reframed it and demoted it. **The body does not mention any of that**,
and a worker gets the body and nothing else. An engineer assigned this on Monday
would build a p80 estimator as a safety mechanism, which is precisely the design
the operator rejected.

On top of that:

- *"Median about 800,000, so admission under-reserves by five to seven times"*:
  FALSE, per 1.4. The median is about 108,000 and the constant is above it.
- *"cost ranges 168,643 to 1,045,598, a 6x spread"*: FALSE, per 1.4. It is 43x.
- The overshoot it is justified by went through a branch with no reserve, per 1.5,
  so the proposed change would not have prevented it.
- The one part that survives is the smaller one: proxy-billed rows
  (`scripts/ralph.sh:1012`) are indistinguishable from measurements in the
  ledger, and there are ten of them.

**Action: cancelled and re-filed, narrowed to the part that is still true and
demoted to last.**

### 2.4 031b626c, the rule list. **Contradicts its own source, and its pointers are stale.**

- `can_admit()` is claimed at `:555-576`. It is at `:674`, and it is a
  three-line wrapper. The logic is `_admission` at `:697`, and the structured
  output the ticket cares about is built by `_refusal` and returned by
  `admission()` at `:684`. An engineer editing `can_admit` as instructed would
  change nothing and would miss the refusal record entirely.
- `plan_budgets()` is claimed at `:376-441`. It is at `:446`.
- *"the two-knob config"*: FALSE. `.runtime/usage-budget.json` holds `app_total`,
  `nominal_task_tokens`, `autonomy_budget_fraction`, `default_project` and a
  per-project `weight`/`floor`/`ceil` map, and enforcement runs through three
  distinct paths, not two.
- *"today's behaviour is expressed as exactly two rules so day one is
  byte-identical"*: not achievable. Two rules cannot express the app ceiling
  scaled by the autonomy fraction with an in-flight-sweep-scaled reserve, plus a
  per-scope ceiling, plus a weighted max-min allowance. The ticket half-knows this
  (it says `plan_budgets` keeps running) but still promises byte-identity.
- **It contradicts the document it cites.** `backlog-reconciliation-2026-08-24.md`
  section 2 opens "The operator has asked for a sketch and reasoning, not tickets,
  and has twice declined to request the feature... deliberately not filed", and
  section 2.5 says it stays unbuilt. This ticket is that feature, filed, citing
  section 2.2 for its shape. Also `limiting-usage` R7 puts it last of five.
- `backend/tests/test_chat_compact.py:287-303` is cited as the regression test
  that chat is never gated. That range is a compaction METERING test
  (`TestTheSummarizerMetersItsSpend`). It is a reasonable thing to not regress,
  but it is not the test the ticket describes.

**Action: cancelled and re-filed with correct pointers, an honest statement of
what "no behaviour change" can mean, and the contradiction surfaced to the
operator rather than buried.**

### 2.5 106d996b, provider, model and cost on every row. **The best of the five, with one decision that is wrong here.**

Sound: the four counters, the integer `micro_usd`, the dated price table, the
refusal to build a second budget system, the warning not to wire `prune`, and
`_read_entries` genuinely tolerating a row missing any optional field.

Wrong or missing:

1. *"Every existing call site is populated from the resolver, five Python and
   four shell."* FALSE. There are five call sites total: three Python
   (`backend/routes/chat.py:2203`, `backend/services/voice_worker.py:396`,
   `backend/services/build_streaming.py:3392`) and two shell
   (`scripts/ralph.sh:1213`, `scripts/reviewer.sh:1117`).
2. **The four counters are not comparable across call sites today**, per 1.7(a).
   The ticket must say that the counters are recorded RAW and that the 0.1
   cache-read weighting moves to the reader, or worker rows will be discounted
   twice.
3. **One call site has no counters at all.** The compaction record is a `chars/4`
   estimate from a `call_cloud` return that carries no usage block. What does it
   write for four counters and a cost? Unstated.
4. **`micro_usd = 0` for a subscription turn makes the whole feature inert here.**
   Every writer in this installation is the Claude CLI on the operator's
   subscription. Under the ticket's rule every row would carry `micro_usd = 0`,
   the By-model cost column would read $0.00 for everything, and a
   dollar-denominated limit would never bind. Meanwhile the CLI is reporting a
   real per-turn `total_cost_usd` that ralph parses past and discards, per 1.7(b).
   The correct rule is: record the reported cost when the provider reports one,
   price it from the table when it does not, and record a `cost_source` so a zero
   is never ambiguous.

**Action: cancelled and re-filed with those four corrections.**

## 3. Cross-ticket findings

**Contradiction 1: 4ea2a388 versus 32bd76cf.** 4ea2a388's body sells a
history-derived reservation as the thing that makes admission correct. 32bd76cf's
body says a design whose correctness depends on the estimate is wrong. Both were
open, both were `todo`, and only one carried the reframing, in an `evidence` entry
a worker never reads. Whichever was picked up first would have set the direction.
Resolved by re-filing 4ea2a388 as a hint-only, last-place ticket that says so in
its Objective.

**Contradiction 2: 031b626c versus `backlog-reconciliation` section 2.5 and
`limiting-usage` R7.** The rule list is filed while both of its own sources say it
should not be built yet. Surfaced in the re-filed body and flagged to the operator.

**Undeclared dependency 1: 57dac664 depends on 32bd76cf.** The session window's
known cost is the boundary burst, and 57dac664's own body says "that is what the
monthly limit below is for". But R3 and R6 both rest the burst argument on the cap
being HARD, which is only true once pause-and-resume exists. Shipping the session
window first gives a legible contract with a softer real ceiling than today. Now
declared with `--depends-on`.

**Undeclared dependency 2: 57dac664's monthly limit depends on 106d996b if the
limit is to be in dollars.** 57dac664 says "in money once money exists, tokens
until then" in the design doc but the ticket body only says do not switch to money
here. Resolved by fixing the day-one unit as tokens (section 4) so the dependency
is removed rather than hidden.

**Declared and correct: 031b626c already depends on both 106d996b and 57dac664.**
That is the one dependency edge that was right.

**Is the priority order the build order?** No. The priorities were 80, 80, 40, 40,
40 (32bd76cf, 106d996b, then the other three), which reads as "the two 80s are
interchangeable". They are not, and one ordering constraint was missing entirely.
The order that actually works:

1. **32bd76cf**, pause and resume. On the critical path to turning the loop back
   on, and it is what makes every later cap hard. Nothing depends on it being
   preceded by anything.
2. **106d996b**, the row shape. Independent of 1, and it is what makes any dollar
   figure possible. It also fixes the three-units problem, which every later
   limit inherits if it is not fixed first.
3. **57dac664**, the two limits. Needs 1 for the burst argument to hold. Does not
   need 2 if day one is in tokens, which is the recommendation.
4. **4ea2a388**, the scheduling hint. Only meaningful once 1 exists, because
   until then it is a guard by default.
5. **031b626c**, the rule list. Last, per its own sources, if at all.

Re-filed priorities encode that: 100, 80, 40, 1, 1. Note that the store maps
priority to four bands (100 urgent, 80 high, 40 medium, 1 low), so those are the
only five values available and there is no rung between 80 and 40.

## 4. The design question the operator left open: dollars or tokens

He said "both dollars and tokens if possible but just one we can measure first".
So the question is what is actually measurable today.

**What is measurable today, per row, without building anything:**

- Four token counters, on every call site except compaction. Already parsed.
- A real per-turn dollar figure for the Claude CLI, `total_cost_usd`, already in
  the envelope, already parsed by `build_streaming.py:889`, measured non-zero on
  this machine (median $0.421 per iteration, max $4.578, $56.95 across the 80
  retained iteration files). Nothing records it.

**Is the price-table approach sound for the providers actually in use?** Partly,
and the ticket has it backwards for the provider that matters.
`backend/services/model_providers.py` declares three: `claude_cli`
(subscription OAuth), `anthropic_api` (key) and `openai_api` (key). Today, 100% of
recorded usage comes from `claude_cli`. For that provider a price table is the
LESS accurate path, because the vendor already reports the number and the table
would only re-derive it from counters, going stale silently the next time list
prices move. The table is the right mechanism for `anthropic_api` and `openai_api`,
which report no cost. So: reported cost first, table second, and a `cost_source`
field so the two are never confused. The table's dated "as of" is still right and
should stay.

**Is `micro_usd = 0` for a subscription turn correct?** As a statement about
marginal cash, yes: a Max subscription turn costs nothing extra. As the value to
record, no, and here it is actively harmful. It would zero every row in this
installation, make the By-model cost column useless, and make a dollar limit
non-binding. What the operator actually wants to bound is CONSUMPTION, and the
CLI's `total_cost_usd` is the correct denomination of it: the API-equivalent value
of the work. Record it, and label it in the UI as API-equivalent rather than as
billed, which is honest and is the only version of the number that can carry a
limit.

**Which unit for the monthly limit on day one: tokens.** Reasons, in order:

1. It is the only unit already present on every gated row. The dollar figure is
   present only on the CLI path and only after 106d996b lands.
2. The five-hour limit is in tokens. Two limits in two units, composed with AND,
   is one conversion table away from a refusal nobody can check by hand.
3. `micro_usd` as currently specified would be zero everywhere, so a dollar limit
   would silently never bind, which is the worst failure mode a limit can have.
4. The conversion is measured and stable, so the dollar figure can be DISPLAYED
   beside the token limit from day one at $4.00 per million weighted tokens. The
   operator gets the intuition without the enforcement depending on a price table.

Then switch the monthly to dollars once 106d996b lands with the correction above.
That is a one-line change to the rule's `unit` at that point, not a redesign.

**The number I would set: 60,000,000 weighted tokens per calendar month,
app-wide, over admission-gated roles only, displayed as "about $240 per month
equivalent".**

Reasoning:

- Measured autonomous spend, August 2026, admission-gated roles only:
  15,984,370 over 24 days, so about 20M per 30 days. That is with the autonomous
  loop off for a large part of the period.
- The theoretical maximum under the short limit alone is 1.5M per scope per 5h
  session, two scopes, so about 14.4M per day, about 432M per month. A monthly
  limit set anywhere near that is decoration.
- 60M is 3x the measured recent rate, so it permits roughly a tripling of
  autonomous throughput before it binds, and it is about 14% of the theoretical
  maximum, so it is a real bound rather than a formality. At the measured $4.00
  per million weighted it is about $240 per month of API-equivalent work, which is
  the right order of magnitude next to a Max subscription.
- Set it to `on_breach: refuse` on day one. Degrading to a cheaper model is more
  useful and is correctly deferred until a model catalogue exists.

**The exclusion decision, which the operator must confirm.** I recommend the
monthly limit inherit `ADMISSION_EXCLUDED_ROLES`, so it bounds autonomous work and
never gates the operator's own chat. But the Usage page must then show BOTH
numbers, because the excluded spend is about 98% of the total in comparable units,
and the design doc already records that the headline surprising him has happened
once. A monthly limit that reads "16M of 60M" while the machine actually consumed
roughly 740M weighted is a true number that tells a false story if it stands alone.

## 5. Ticket changes made

All five cancelled, all five re-filed. Full ids in both directions:

| Cancelled | Re-filed as | Priority | Why |
|---|---|---|---|
| `32bd76cf58dc42ee8321b3ee9a138b04` | `70860d58b62441c9bd1a4f6a0c356f98` | 100 | false checkpoint claim, `can_admit` cannot answer the query it is pointed at, no exit code chosen, ninth-status ambiguity, worktree mode unmentioned |
| `106d996b0ccd43a1970d01651758a8fc` | `d0b103e3fdf74a948f6c7f2209541059` | 80 | wrong call-site count, three incompatible token units unmentioned, compaction site has no counters, `micro_usd = 0` would zero every row here |
| `57dac66475f04a1eb2c012e2ddee3ad2` | `a731ec9b1a2f42188520a2d8cb5a292a` | 40 | false dribble premise, unreproducible decay figure, no monthly amount, no session anchor rule, silent on the exclusion |
| `4ea2a38804b9413d96d38270087925a0` | `972c1d8ac7ea4d6093ef874fecbf285b` | 1 | body contradicts the operator's reframing which lives only in evidence, median claim wrong by 7x, aimed at a branch with no reserve |
| `031b626c71e747418fbaa2fc227968c1` | `d37c33142041439c99e74f2689220fb7` | 1 | stale line numbers, wrong function, "two knobs" wrong, byte-identity not achievable, contradicts its own sources on whether to build it at all |

Dependencies now declared rather than implied: the two-limits ticket depends on
the pause ticket, the sizing-hint ticket depends on the pause ticket, and the
rule-list ticket depends on both the row-shape and the two-limits tickets.

Three further ids were filed and cancelled inside this session before the five
above settled, and are listed so the store's history is not confusing later:
`2ca2c75b38924ef6a8bf36d99da59f21` (the pause ticket, re-filed because the store
maps priority to the bands 100/80/40/1 and a requested 90 silently collapsed to
80, losing the ordering against the row-shape ticket),
`410a4b73180f4df8bb5043d966704f66` and `41761270400a49cab049d8fcfa166d36` (re-filed
for the label defect below), and `55c395b96d8f42b3b75db6142ce79651` (re-filed
because its dependency pointed at one of those).

**A format finding worth keeping.** Two of my first drafts wrote "Output, part
one" instead of an `Output:` line, and so did the original 57dac664.
`backend/services/ticket_format.py`'s `_label_re` matches a label only when it is
followed by a colon or a dash (`[:\-...]`), so "Output," does not match and the
body does not state Output at all. The original 57dac664 therefore never satisfied
the four-label contract it was filed under, and nothing caught it. That is a
one-line gap in whatever path files tickets, and it is not fixed here.

## 6. What needs the operator

1. **Confirm the monthly number and unit**: 60,000,000 weighted tokens per
   calendar month, app-wide, admission-gated roles only, refusing on breach.
   Nothing else in the plan needs a number from him.
2. **Confirm the exclusion**: should the monthly limit count his own chat? The
   recommendation is no for enforcement and yes for display.
3. **Decide whether the rule list is wanted at all.** Two of its own source
   documents say it is deliberately not filed and he has declined it twice, yet
   it is filed against his name. It is re-filed at priority 20 so nothing picks it
   up, but it should probably be cancelled outright rather than left as a low
   priority that misrepresents his position.
4. **Ratify one judgement call I made inside a ticket**: the paused state reuses
   `parked` plus a structured resume bookmark rather than adding a ninth status
   token. The original ticket said the paused state "needs its own visible state",
   which read literally is a change across four backend definitions and eight
   frontend switches for one bit of information. If he wants the ninth token, that
   is a separate ticket and should be sized as one.
