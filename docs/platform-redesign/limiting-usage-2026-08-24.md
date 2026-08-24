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

Median around 800,000. **Admission under-reserves by five to seven times.**

So the sequence is: the window frees up 200k, admission asks "is there room for
150k", says yes, starts a task that costs 800k, and the ceiling ends up 31% over
(`1,972,531` against `1,500,000` was exactly one under-reserved task overshooting).
Then everything is refused for hours while it decays.

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
