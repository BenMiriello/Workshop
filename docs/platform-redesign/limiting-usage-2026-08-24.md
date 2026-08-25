# Limiting usage: what we have, what it is called, and what to do

- Date: 2026-08-24
- Status: **PROPOSAL. Nothing changed yet.** Operator raised it for the record on
  2026-08-24 after watching the loop start a task and run out mid-flight.
- Feeds the limits section of `providers-credentials-and-limits-2026-08-24.md`
  (section 3d), which already decided that limits extend `usage_ledger` and are
  expressed as a rule list. This document decides the SHAPE of the rules and
  fixes the defect underneath the operator's complaint.

## 0. The finding that reframes the question

The operator's symptom: "it can wind up with a bit of usage freeing up but then
not enough, so an agent might start work but then run out, and overall less work
gets done."

That is real, and **it is not caused by the shape of the window.** It is caused by
admission reserving the wrong number.

Measured 2026-08-24, `nominal_task_tokens` in `.runtime/usage-budget.json` is
**150,000**. The real cost of recent runs, from the `billed N tokens` lines in
`.runtime/ralph.log`:

- 750,842
- 798,189
- 855,699
- 1,042,287
- 1,045,598
- 168,643 (one small ticket)

**RETRACTED 2026-08-24, and the retraction matters: these six are the TOP of the
distribution, not the middle.** Measured over all 168 `billed` lines in
`.runtime/ralph.log`, not a hand-picked sample:

    count 168   min 24,559   median 107,284   p80 231,965   max 1,045,598

So the spread is **42.6x**, not 6x, and the nominal reserve of 150,000 is **ABOVE
the median**, not five to seven times below it. The claim that admission
under-reserves by five to seven times is false and is withdrawn. Two further
claims in earlier revisions go with it: "a rolling window returns budget in
dribbles" (it is a sliding-window log, so budget returns in the lumps it was
spent) and "decayed only 180,000 in five hours" (does not reproduce).

**The conclusion survives the correction, and is strengthened by it.** The point
was never that the constant is too small. It is that cost is unpredictable, and a
42x spread makes that case far better than a 6x one would: no estimate, p80 or
otherwise, can be right often enough to be the thing correctness rests on. That
is the argument for pause-and-resume, which makes the estimate not matter, and
against tuning the reserve, which cannot.

One more measured correction: the `1,972,531` against `1,500,000` overshoot is a
PER-SCOPE ceiling breach, and in `_admission` the reserve is applied only on the
app-wide branch. The two scope branches consult no reserve at all. So the
reservation fix was aimed at a constant that is not in the code path of the
failure that justified it.

**Any limiter has this failure with a 5x-wrong cost estimate.** Switching from a
rolling window to a fixed window, a session window or a token bucket does not fix
it. This is why the first recommendation below is not about windows at all.

Second, smaller defect found while measuring: when an iteration is timeout-killed
and produces no usage JSON, `ralph.sh:1012` bills `NOMINAL_TOKENS` as a proxy so
the work is not invisible to admission. That is a deliberate choice (review F8b)
and better than billing zero, but it means a killed iteration that actually burned
600k is recorded as 150k. Four such rows exist for one task. So the ledger
under-counts exactly the runs that went worst.

## 1. What these things are called

The operator asked directly, so: precise names, because they make the options
searchable.

**What Workshop has now is a sliding window log.** Every spend event is stored
with its timestamp (`.runtime/usage.jsonl`) and the limiter sums the events inside
the trailing 5 hours. It is the most accurate of the classic algorithms and the
most expensive in storage, which is a fine trade at this volume.

The alternatives, all standard:

- **Fixed window counter.** A counter that resets on a wall-clock boundary.
  Cheapest. Its known flaw is the boundary burst: a caller can spend the full
  allowance at the end of one window and again at the start of the next, so the
  true peak is 2x the nominal rate.
- **Session window** (a fixed window whose start is lazy, set by the first use).
  **This is what Claude Code itself does**, and it is what the operator was
  reaching for when he said "a full reset every 5 hours, starting from first
  usage, which isn't quite right". It is not wrong; it is a different contract. It
  gives you a clean slate and a predictable reset you can plan around, at the cost
  of the same boundary burst.
- **Sliding window counter.** An approximation that blends the current and
  previous fixed windows by elapsed fraction. Nearly as accurate as the log with
  O(1) state. The usual production default when the log is too expensive.
- **Token bucket.** A bucket of capacity C refilling continuously at rate R. Work
  takes N tokens to start. Allows a burst up to C, then settles to a sustained
  rate R. Two numbers of state.
- **Leaky bucket** and **GCRA** (generic cell rate algorithm). GCRA is the elegant
  one: a leaky bucket expressed as a single timestamp, O(1), no counters. Common
  in rate-limiter libraries.

The distinction the operator is circling, and the one that matters most here:

> A **limiter** answers "how much may be spent per period".
> An **admission controller** answers "should this unit of work start now".

Workshop currently has a good limiter and a bad admission controller, and it has
been reading the resulting pain as a limiter problem.

## 2. What other systems do

- **Claude Code**: a 5-hour session window, plus a weekly limit added on top in
  2025. Two limiters composed with AND. Precedent for the operator's instinct that
  a short window and a long one should coexist.
- **OpenAI, Anthropic API, GitHub, Stripe, AWS**: all run several limiters at
  once at different periods (per second, per minute, per day) and refuse if ANY
  of them refuses. Composing limiters with AND is the industry-normal answer, not
  an exotic one.
- **Cluster schedulers (Kubernetes, Borg, Slurm, YARN)**: the closest analogue to
  our actual problem, because they schedule long-running units of work against a
  shared quota. Every one of them **reserves the job's requested capacity for its
  whole duration before it starts**, and none of them admits a job because a
  fraction of it fits. Kubernetes calls the number a `request`; Slurm makes you
  declare it. Getting the request wrong is the classic operational failure there
  too, which is why they all also measure actuals and nag you when your request is
  badly sized.

That is the pattern to copy, and it is the thing we are missing.

## 3. Answering the operator's specific proposals

**"Delay starting until it drains to, say, 50%."** This works, and it is worth
understanding why. It works because 50% of 1.5M is 750k, which is approximately
the true cost of a task. It is a correct answer arrived at by guessing the right
number. His own doubt is also correct: as a fixed rule it strictly reduces
utilization, because it refuses in the band between the true task cost and the
threshold whenever those differ. **Reserving the measured cost achieves the same
protection without the waste, and it retunes itself as tasks get cheaper or dearer.**
So: adopt the intent, reject the fixed percentage.

**"Would this mean we never hit full usage, or that we limit usage further?"**
A fixed headroom threshold does the second: it lowers effective utilization,
permanently, by the size of the gap. Reserving true cost does neither. It moves
the refusal EARLIER and makes it accurate, so the ceiling is approached and not
overshot. Today the ceiling is not a ceiling; it is a number the system passes by
31% and then sits above.

**"Any number of limits at any period, or is that too many options?"** Not too
many, provided the default is two and the rest is advanced disclosure. The
mechanism is one rule list evaluated with AND, which costs the same whether there
are two rules or six. What must not happen is two mechanisms.

**"Different limiting systems, up to what the user wants."** Yes, and this is the
right instinct: the limiter shape is a preference, not an architecture. Offer
rolling, session and none, sharing one accounting layer. Adding a shape must be a
row, not a rewrite.

## 4. Proposal

Four parts, in order of value. The first is most of the benefit.

**A. Reserve what the work actually costs.**
Replace the constant `nominal_task_tokens` with an estimate derived from history:
the p80 of recent completed runs for that scope, bucketed by the ticket's `effort`
when it has one, falling back to the global p80 and only then to a constant.
Admission asks for the estimate, not for 150k. This alone removes the observed
failure. Report the estimate in the refusal text so it is legible: "needs about
800k and 200k is free" is a sentence the operator can act on.

**B. Make the unit of work yield rather than die.**
Even a good estimate is sometimes wrong. When a run exceeds its reservation, the
current behaviour parks it and the work is lost. The tree already has the concept
needed (a yield is a snapshot, not a commit, from the branch-isolation work), so
the run should checkpoint and be resumable rather than discarded. This is what
turns an over-run from lost work into deferred work, and it is what actually
answers "overall less work gets done".

**C. One rule list, several periods, evaluated with AND.**
`{scope, window, unit, amount, on_breach}`. Ship two defaults: the existing 5 hour
rule, and a monthly one. Everything else is advanced. The most specific refusing
rule is the one reported, so a refusal names which limit stopped it, never just
"limited". This is the shape already decided in the providers document, so this is
an instance of that, not a second system.

**D. Limiter shape as a setting, one accounting layer underneath.**
Offer `rolling` (today), `session` (Claude-style, resets from first use), and
`none`. All three read the same event log; only the query differs. Token bucket is
deliberately NOT in the first cut: it is the better pacing model, but it is a third
shape to explain and the value here comes from A and B, not from the window.

**And count money, not tokens.** Established separately on 2026-08-23 and it
belongs in this record: the ledger sums input, cache read, cache creation and
output into one number, and cache reads dominate while billing at about a tenth of
input. A token total is not a cost proxy and is not even rank-preserving between
scopes. Every amount above should be money once that lands.

## 5. What to do first

A is a small change with a measured, specific payoff, and it is independent of
every other part. Do it alone, watch one window, then decide whether B is needed.
C rides along with the providers work. D is last and optional.

## 6. Open

- Should a long-period limit (monthly) refuse, or degrade to a cheaper model?
  Degrading is more useful and is only expressible once the model catalogue exists.
- Should the operator's own chat count against anything at all? Today it is
  excluded from admission and that is correct, but it means the headline number in
  Settings describes only autonomous work, which has surprised him once already.

---

# Revision 2, same day, after operator response

Three corrections from the operator, one of which overturns the ordering above.

## R1. "We don't know what the work is going to cost. Don't assume a guess is reliable."

Correct, and it demotes section 4A. A p80 of history is still a guess: ticket cost
here ranges from 168,643 to 1,045,598, a 6x spread, and the estimator has no way to
know which kind it just claimed before it runs it. Sizing the reservation better
narrows the error; it does not remove it. Any design whose correctness DEPENDS on
the estimate is wrong.

So the estimate stops being a safety mechanism and becomes only a scheduling hint.

## R2. The real fix is that a run can stop at the cap and resume

If a run can be paused at the boundary and resumed later without losing work or
context, then every problem in this document dissolves at once:

- The cap becomes genuinely HARD. Overshoot is impossible, because the run stops
  at the line rather than discovering the line after crossing it.
- A wrong estimate becomes harmless, which is exactly what R1 requires.
- No utilization is wasted. Work can start whenever there is any budget at all,
  run to the cap, pause, and continue in the next window. The "drain to 50% first"
  idea becomes unnecessary rather than merely imperfect.
- Nothing is redone. Today an over-run is parked and its work is discarded.

**Preemption removes the need for estimation.** That is the whole argument, and it
is why this is now first and 4A is second.

FEASIBILITY, checked rather than assumed. `scripts/ralph.sh` already runs a task as
a LOOP of iterations, already keeps that run's own `SESSION_ID` (`:737`), already
passes `--resume "$SESSION_ID"` between iterations (`:825`, `:949`), and already
auto-commits work a worker left uncommitted (`:342`). An iteration boundary is
therefore already a checkpoint with committed code and a resumable model session.
Pausing is using machinery that exists across a boundary it does not currently
cross, not building new machinery.

The shape:
- Before each iteration, compare remaining budget against the cost of the last
  iteration (a much better predictor than the cost of a whole task, because it is
  one step of the same work, and it is the only estimate that stays a hint).
- If the next iteration would cross the cap, stop: commit, record `SESSION_ID`,
  the branch, and the iteration number on the ticket, and set it to a resumable
  state rather than parked.
- When budget returns, the sweep prefers resumable tickets and continues with
  `--resume`.

STATE THE LIMITS HONESTLY, because this is not free. A model session can expire or
grow too large to resume, and a very long pause makes that likelier. So resume must
be best-effort with a stated fallback: if the session cannot be resumed, the work is
still committed and the ticket still carries its branch, so the next attempt starts
from real code rather than from nothing. That is strictly better than today even
when resume fails.

## R3. Soft caps, "a little soft at most", or hard

With R2 in place the right answer is: **hard cap, soft landing.** The number is
never exceeded, and the thing that would have exceeded it is paused instead of
killed. Softness is not needed as slack in the limit; what was actually wanted from
softness was not losing the work, and preemption gives that directly.

Keep one small allowance and no more: the iteration in flight when the boundary is
reached is allowed to finish, because killing it mid-write is what loses work. That
is bounded by one iteration, which is measurable and reportable, and it is the only
softness in the design.

## R4. Weekly and monthly: we have none

Measured 2026-08-24. `WINDOW_S = 5 * 3600` (`usage_ledger.py:45`) is the ONLY
enforced period. There is no weekly limit, no monthly limit, and no daily one. The
24h / 7d / 30d ranges in `status()` (`:525-528`) are REPORTING lookbacks for the
Usage page and gate nothing.

One piece of luck worth recording: `prune()` (`:359`) deletes rows older than the
window and **has no live caller**, only its own CLI and a test. So the ledger has
4,876 rows going back to 2026-07-15, about 40 days. Had that inert mechanism been
wired, there would be no history to enforce a monthly limit against and none to
estimate costs from. Do not wire it without first raising its retention.

## R5. What the two limits are FOR, which decides how many there should be

This is the argument against the six-period rule table, and it is simple:

> A **short** window limits the RATE. It answers "how fast should this go".
> A **long** window limits the BUDGET. It answers "how much am I willing to spend".

They are different questions, so both are worth having. A third and fourth period
answer neither question better; they interpolate between two answers already given.
That is why the recommendation is exactly two by default, with more available and
not encouraged.

Setting them follows from that split:
- The short one is set from impatience, not from money. "About two tasks per
  session" is what 1.5M currently expresses at a measured ~800k per task.
- The long one is set from money, and should be the number the operator actually
  cares about. It is the only limit that should ever be denominated in currency,
  and it is the strongest argument for the money-not-tokens change: nobody has an
  intuition for 30 million tokens a month, and everybody has one for a dollar
  figure.

## R6. Session window versus rolling, for the short limit

The operator's read is right and it is worth saying why, because it is not obvious.

A rolling window's budget dribbles back continuously, in amounts far smaller than a
unit of work. That is precisely the condition that produces the observed failure: a
little frees up, something starts, it is not enough. A session window returns the
whole allowance at once, so when work becomes possible it becomes fully possible.
**A rolling window is the worst shape for a workload whose unit is large and
indivisible**, which is what a coding task is.

It also has a legible contract, which matters more here than accuracy: "you have
1.5M until 4:11 PM, then a fresh 1.5M" is something the operator can plan around,
where "1,793,129 of 1,500,000, decaying continuously" is not.

The classic objection is the boundary burst, 2x the nominal rate across the seam.
With a hard cap and preemption that is bounded and visible rather than dangerous,
and the long-period limit is what actually stops sustained overspend. So: session
window for the short limit, and the monthly is what keeps it honest.

## R7. Revised order

1. **Pause at the cap and resume** (R2). Makes the cap hard, makes estimation
   unnecessary, stops discarding work. Everything else is smaller.
2. **Switch the short limit to a session window** (R6), keeping 1.5M per scope.
3. **Add a monthly limit** (R4, R5), in money once money exists, tokens until then.
4. **Size the reservation from history** (4A), now only a scheduling hint: prefer a
   task that plausibly fits the remaining session over one that plainly does not.
5. Configurable periods beyond those two: available, not encouraged, advanced
   disclosure. The operator has twice declined to request this, so it is last.
