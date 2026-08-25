# Routing work to a model

- Date: 2026-08-25
- Status: **DESIGN. Nothing implemented.** Tickets filed at the end.
- Question from the operator: how does the system decide which model or agent
  takes a ticket? He proposed tickets carrying dimensions (he suggested SIZE and
  COMPLEXITY), a user-configurable map from dimension level to model, a global
  default in settings with per-agent and per-ticket overrides, and asked how
  multiple dimensions combine: does the highest requirement win? He asked for
  research rather than agreement, and said he is open to a more elegant design.
- Sits under `providers-credentials-and-limits-2026-08-23.md` §6 ("one resolver,
  or the answer to 'which model ran' depends on which caller you ask") and feeds
  `limiting-usage-2026-08-24.md` §6, which has an open question this design
  makes answerable.

---

## 0. The decision, up front

**One declared field, and it is not an estimate.** The ticket does not say how
big the work is. It says how much the operator is willing to spend on it. That
is an **appetite**, in Shape Up's sense: a budget you set, not a prediction you
make. Everything else follows.

- The field is `appetite`, values `small | standard | large`, absent meaning
  `standard`. It replaces `effort` on the ticket, which also clears the
  vocabulary collision with reasoning-effort (§5).
- A settings-owned **policy table** maps each band to a **starting tier** and a
  **ceiling tier**. Global default, per-scope override, per-ticket pin.
- A run that fails for a reason a stronger model could fix **escalates one tier
  on its next attempt**, up to the band's ceiling. It adds no attempt and no run:
  the retry already happens today.
- **Reasoning effort is a genuinely separate knob**, resolved after the model is
  settled, exactly as chat already does it. Escalation moves the tier and never
  the effort.
- The design is correct with the field never set. 81 of 111 live tickets have no
  effort today and get no routing at all; under this design they get `standard`,
  start on Sonnet, and escalate to Opus if they fail. The declared field only
  moves the starting point for the tickets where the operator has an actual
  preference.

**Answer to the exact question, how do dimensions combine.** They do not,
because there is one. If there were two combined by max, the second one changes
the outcome only when it is strictly the higher, so its whole job is to catch
cases where the first under-called the work. That is one direction of error, it
is caught by guessing, and §3 builds a mechanism that catches the same direction
of error by measuring. §2.3 gives the second, sharper reason: any monotone
combination of two ordered dimensions collapses to one ordered output, so a
3x3 grid feeding a max is not two-dimensional control. It is a nine-cell way of
writing a three-valued number, and the operator can only ever observe the number.

---

## 1. What exists today, in one paragraph each

**One ticket field routes to a model.** `scripts/project_ralph.sh:1257-1265`
reads the ticket's `effort` and maps `quick` to `claude-haiku-4-5` and `deep` to
`claude-opus-4-8` via `RALPH_MODEL_QUICK` / `RALPH_MODEL_DEEP` (`:62-63`),
exporting `ANTHROPIC_MODEL` around the child (`:1291`, `:1300`). `standard` and
absent set nothing. The same case sets the inner-iteration cap (3 / 10 / 12).
There is a picker on the ticket page (`TicketDetail.tsx:624-637`) and a filter
chip (`TicketFilters.tsx:57`), but no settings store and no way to change the
mapping except two environment variables.

**73% of tickets do not use it.** 81 of 111 live tickets have `effort=None`. The
field has been available and visible for months.

**Model ids live in one table, and it carries nothing but names.**
`llm_router.py:50-68` holds six handles behind six tier names; `tier()` is the
one resolver from a declared short name to a handle, and that part is right and
stays. `chat_context.PICKABLE_MODELS:49-57` and `MODEL_ALIASES:58-66` restate the
set for the API. `model_providers.Provider` (`:69-88`) has no models attached at
all, and the Models settings page is credentials and display. **Nothing anywhere
carries a cost or a capability**, so nothing can reason about a tradeoff; it can
only follow a table someone wrote.

**Seventeen other places choose a model, almost all by hardcoded literal.**
`ticket_drafts`, `chat_naming`, `triager` and `memory_keeper` pin Haiku;
`claim_check` and `categorizer` pin Sonnet. One place actually routes on
predicted complexity: `routes/relay.py:55-71` counts words in the prompt and
keyword-matches "architecture" against "boilerplate". Keep that in mind for §2.2;
it is the in-tree instance of exactly the technique the literature says plateaus.

**Three overlapping ticket fields already exist.** `effort` (quick/standard/deep,
one reader), `execution` (code/judgement/external, fail-closed, read by
`next_ready` to decide unattended eligibility, a real gate), and `latitude`
(strict/standard/open, documented at `agents_registry.py:142` as DECISION RIGHTS
and explicitly "NOT effort", set on zero of 111 tickets). The operator's sketch
would make that five. This design makes it three, one of which changes meaning.

**The repo has already deleted a field for this exact reason.**
`agents_registry.py:132-140`: `depth` and `fanout` were removed because no
executor ever read them: "a control that lies. Do not add them back without a
reader." Any dimension proposed here has to name its reader in the same
paragraph that proposes it.

**Cost is not predictable here, measured.** Across 168 billed runs in
`.runtime/ralph.log`: min 24,559, median 107,284, p80 231,965, max 1,045,598
tokens. A 42.6x spread. `limiting-usage-2026-08-24.md` R1 already ruled, in the
operator's own words, that no design may rest its correctness on an estimate of
run cost. A predictive sizing field is an estimate of run cost.

---

## 2. Prior art, and what each source actually supports

Ten sources. Where the evidence is thin it is labelled thin. Three are 2026
preprints without a venue and are marked as such.

### 2.1 Cascades: try cheap, verify, escalate

- **FrugalGPT** (Chen, Zaharia, Zou, Stanford, arXiv:2305.05176, 2023).
  https://arxiv.org/abs/2305.05176. The canonical cascade: query models
  cheapest-first, and a learned scoring function over (query, answer) decides
  accept-or-escalate. Claims matching GPT-4 "with up to 98% cost reduction". The
  mechanism is what this design borrows. **The number is a best-case on
  classification-flavoured benchmarks and is not quoted here as a target.**
- **AutoMix** (Aggarwal et al., CMU/Google, arXiv:2310.12963, NeurIPS 2024).
  https://arxiv.org/abs/2310.12963. Escalates *after* the small model answers,
  using few-shot self-verification refined by a meta-verifier, **because raw
  self-verification is too noisy to route on**. Claims >50% cost reduction at
  comparable performance. Supports §3's design directly, and supplies its best
  argument: we do not have to build the noisy verifier, because the reviewer gate
  is already an independent judge with a fresh context.
- **LLM Cascades with Mixture of Thought Representations** (Yue et al., ICLR
  2024, arXiv:2310.03094). https://arxiv.org/abs/2310.03094. Uses the weak
  model's answer *inconsistency across samples* as the escalation signal, and
  reports GPT-4-comparable results at 40% of its cost. Supports: difficulty is
  best read off the cheap model's behaviour on this instance, not predicted from
  the text of the request.

### 2.2 Predicting difficulty up front is the weak part

- **The Routing Plateau** (Lu et al., arXiv:2606.07587, 2026, **preprint**).
  https://arxiv.org/abs/2606.07587. 21 routing methods across five benchmarks
  converge to a narrow band far below the oracle router, and the paper names the
  cause: a **predictability bottleneck**, where routers "mainly learn global
  averaged model-performance trends rather than fine-grained query-specific
  routing signals... they solve overlapping easy queries but collectively fail on
  hard queries that require instance-specific routing decisions." This is the
  best single citation for "a label attached before the work starts is a weak
  signal", and it is about *trained* routers: a human's three-valued guess is
  strictly less informed than the routers that plateaued.
- **RouteLLM** (Ong et al., Berkeley/LMSYS, arXiv:2406.18665, 2024).
  https://arxiv.org/abs/2406.18665, blog https://lmsys.org/blog/2024-07-01-routellm/.
  A router trained on ~120K human preference pairs. The paper claims cost
  reduction "by over 2 times in certain cases"; the louder figures (85% on
  MT-Bench at 95% of GPT-4 quality) are blog-only, and MMLU is a much less
  flattering 45%. What it supports here is the *shape*: the decision is a
  **learned predictor fit to outcome data, and its output is binary strong/weak**,
  not a taxonomy a person fills in.

### 2.3 The honest counter-argument, which this design has to answer

- **Is Escalation Worth It? A Decision-Theoretic Characterization of LLM
  Cascades** (Bouchard, arXiv:2605.06350, 2026, **preprint**).
  https://arxiv.org/abs/2605.06350. Across MATH, MMLU, TriviaQA, SimpleQA and
  LiveCodeBench: "A lightweight pre-generation router exceeds the best cascade
  policy on four of five datasets, mainly because it avoids the cheap model's
  generation cost on queries sent directly to a larger model rather than because
  of a stronger routing signal." The author is explicit that the router's edge is
  **economic, not epistemic**: cascades pay a **structural cost**, the sunk price
  of the failed cheap attempt.

  This is the strongest thing anyone could say against §3, and §3.4 answers it
  specifically rather than omitting it. The short version: in this system the
  cheap attempt is not added by the design, and neither is the retry.

- Also read: **Dynamic Model Routing and Cascading: A Survey** (Moslem &
  Kelleher, arXiv:2603.04445, 2026, **preprint**), taxonomy only, no head-to-head
  claim; cited for vocabulary, not for evidence.

**Gap worth stating: there is no published cascade or routing evaluation on
agentic software tasks.** LiveCodeBench in Bouchard is single-shot generation.
Every number above comes from single-turn benchmarks. So the literature settles
the *shape* of the argument and settles nothing about the magnitudes here. That
is why §7 states what would falsify this design in-tree.

### 2.4 What shipping coding agents actually expose

Six products checked. **None of them asks the user to label a task's size or
complexity.** Each either lets the user pick a model directly, classifies
automatically, or splits by workflow role.

- **Claude Code** (https://code.claude.com/docs/en/model-config,
  https://code.claude.com/docs/en/advisor). `opusplan` splits by **phase**:
  Opus while planning, Sonnet while executing, not by difficulty. The
  experimental **advisor tool** is the escalation-shaped one: a stronger model is
  consulted "before committing to an approach, when stuck on a recurring error,
  or before declaring a task complete", and "Claude decides when to call it...
  the timing is model-driven rather than rule-based", justified on exactly the
  cost argument in §3.
- **GitHub Copilot coding agent**
  (https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/changing-the-ai-model,
  https://docs.github.com/en/copilot/concepts/models/auto-model-selection). The
  most ticket-shaped product there is (you assign a GitHub issue to an agent),
  and the exposed control is a **model dropdown**, with Auto evaluating task
  complexity machine-side. Reasoning level is a **separate dropdown**.
- **Cursor Router** (https://cursor.com/blog/router, 2026). A classifier trained
  on 600K+ live requests, evaluated by online A/B. What the user picks is not a
  difficulty label but a **posture**: Cost, Balance, Intelligence. Vendor
  self-reported numbers; cited as a design pattern, not as evidence.
- **Aider** (https://aider.chat/docs/config/options.html). `--model`,
  `--weak-model`, `--editor-model`: a static split by **job function**, set once
  in config, never per ticket. Plus `--reasoning-effort` and `--thinking-tokens`
  as separate flags, the same orthogonality.
- Also checked: **Codex** (user picks model plus a reasoning level per task, no
  auto-router, no complexity label) and **Devin CLI** (`/model` picker, thinking
  level on a separate key, plus an "Adaptive" auto-router).

The inference to draw is narrow and it is about *what they expose*: a model
identity, a cost/quality posture, or an effort level. All three are things a
person can judge **without estimating the task**. That is the same property an
appetite has and a size estimate does not.

### 2.5 Effort is confirmed as a separate axis

- **Anthropic, Effort** (https://platform.claude.com/docs/en/build-with-claude/effort).
  `output_config.effort` is `low|medium|high|xhigh|max`, default `high`, and "is
  a request-level setting: each request carries its own value". It is not only a
  thinking knob: "The effort parameter affects all tokens in the response,
  including... Tool calls and function arguments... lower effort would mean
  Claude makes fewer tool calls", which is precisely the behaviour that matters
  for an agentic run. Two constraints this design honours: Anthropic recommends
  adjusting effort by task complexity but warns to **hold effort constant within
  a cached conversation** (changing it invalidates the prompt cache), and states
  effort is "a behavioral signal, not a strict token budget".
- **OpenAI, reasoning** (https://developers.openai.com/api/docs/guides/reasoning).
  `reasoning.effort` guides "how much to think", values model-dependent. Confirms
  the axis is an industry convention, not a local invention.

Our `model_caps.EFFORT_BY_MODEL:37-44` already encodes the per-model ladders
correctly, including Haiku supporting none.

### 2.6 Sizing conventions, and how well estimates hold up

- **Shape Up ch. 3, "Set Boundaries"** (Singer, Basecamp, 2019).
  https://basecamp.com/shapeup/1.2-chapter-03. Verbatim: "An appetite is
  completely different from an estimate. Estimates start with a design and end
  with a number. Appetites start with a number and end with a design." This
  supplies the reframe in §0, and it is the answer to "is there a more elegant
  design", the elegance is not in combining dimensions better, it is in not
  making a prediction at all.
- **Story points vs actual effort** (Tawosi, Moussa & Sarro, ESEM 2022).
  https://dl.acm.org/doi/10.1145/3544902.3546238. 37,440 user stories across 37
  open-source projects: the correlation between expert story points and actual
  development time was strong in only **7%** of projects, medium in 58%, low in
  35%. The first dimension is already weak. This is the number to remember before
  adding a second.
- **What We Do and Don't Know about Software Development Effort Estimation**
  (Jørgensen, IEEE Software, 2014). Average overrun is around 30%, and estimation
  accuracy has not measurably improved since the 1980s. Cited as Jørgensen's
  repeatedly published summary figure across surveys (individual surveys range
  roughly 18-41%), not as a measured constant. Companion:
  **Jørgensen & Moløkken-Østvold (2006)** dismantling the Standish 189% figure.
  https://www.sciencedirect.com/science/article/abs/pii/S0950584905001023.
- **Realism in Assessment of Effort Estimation Uncertainty** (Jørgensen, IEEE
  TSE, 2004). Intervals professionals call 90%-confident contain the actual
  effort only about 60-70% of the time. People are not merely biased low, they
  are wrong about the *width*, which is the property a three-band field is
  implicitly claiming to know.
- **Empirical analysis of function point adjustment factors** (Lokan,
  Information and Software Technology 42(9), 2000).
  https://www.sciencedirect.com/science/article/abs/pii/S0950584900001087. 235
  projects: the Value Adjustment Factor, fourteen complexity characteristics
  layered on top of raw size, "should not be used": doubts about its
  construction are not offset by practical benefit, and complexity is
  double-counted, once inside unadjusted size and again in the adjustment. This
  is the closest thing to direct evidence that a complexity dimension bolted onto
  a size dimension adds nothing. **Label it what it is: an analogy from function
  points to story bands.** I did not find a study of complexity-vs-size axes on
  agile tickets specifically. **The claim is thin and is not load-bearing.**
  §2.3's collapse argument is, and that one is arithmetic.
- **METR, Measuring the Impact of Early-2025 AI on Experienced Open-Source
  Developer Productivity** (Becker, Rush, Barnes & Rein, July 2025).
  https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/,
  arXiv:2507.09089. 16 experienced developers, 246 tasks on their own mature
  repos. They forecast a **24% speedup**; the measured result was **19% slower**;
  and having done the work, they still believed they had been sped up **20%**.
  **Carry the caveat:** METR's own 2026-02-24 follow-up describes severe
  selection bias and calls the measurement a bad proxy for real productivity
  impact. That caveat does not touch the use made of it here, which is only about
  **prediction error**: the same people forecast and were measured. Do not cite
  the 19% as a claim that AI slows developers down.
- Not used: **#NoEstimates**. I found practitioner reports and no rigorous
  empirical base. The defensible neighbouring fact is that item-count forecasting
  is a first-class built-in option in Jira alongside story points
  (https://support.atlassian.com/jira-software-cloud/docs/enable-estimation/).

---

## 3. The design

### 3.1 The field: `appetite`, not a size

One field on the ticket. `small | standard | large`. Absent means `standard`,
and absent is expected to stay the common case.

It reads as a sentence the operator can answer without knowing the code: **"how
much am I willing to spend on this?"** Not "how big is it?", which §2.6 says he
cannot know, and which the 42.6x measured spread says nobody can. The distinction
is not cosmetic. An estimate can be wrong; an appetite cannot, because it is a
decision. That is what makes it safe to attach to money.

Who sets it:

- **The drafting model, at filing time**, may propose one. It is the only actor
  present when the ticket is written and the only one that read the conversation.
  Its proposal is a default the operator can change, and it is not a gate.
- **The operator**, on the ticket page. The picker is already there
  (`TicketDetail.tsx:624-637`); it changes label and enum.
- **Not the triager.** Adding an LLM pass whose job is to predict how hard a
  ticket will be is the upfront router of §2.2, built by hand, and the Routing
  Plateau result says it converges to the model-average and fails exactly on the
  instances where it would have mattered. If the triager gets a job here later it
  should be trained on this repo's own outcome data, which does not exist yet
  (§7).

### 3.2 The policy table

Store: `.runtime/model-policy.json`. Owner: a new
`backend/services/work_model.py`, written in the `scope_execution.py` idiom
because that idiom has already been debugged: single JSON under `.runtime`,
**keyed by SCOPE KEY and never by a path** (the 2026-08-22 failure where a
confined turn wrote `/workspace` and the backend could not resolve it), atomic
write via `os.replace`, an unknown value clamped to the default rather than
raising, and **setting a value back to the default deletes the key** so the file
is exactly the set of deliberate overrides and `armed_scopes()`-style listing
needs no filtering.

Shape:

```json
{
  "default": {
    "small":    {"start": "haiku",  "ceiling": "sonnet"},
    "standard": {"start": "sonnet", "ceiling": "opus"},
    "large":    {"start": "opus",   "ceiling": "opus"}
  },
  "scope": {
    "project:some-app": { "large": {"start": "opus-5", "ceiling": "opus-5"} }
  }
}
```

Rows are **short tier names**, resolved to handles through `llm_router.tier()`
and nowhere else, the same discipline that already lets a declaration say
`sonnet` and survive a handle rotation. The values above are the built-in
defaults and are what an absent file means; note they are deliberately *not*
today's behaviour, because today's `standard` sets nothing at all and therefore
routes 73% of tickets to whatever the CLI defaults to.

A row may optionally carry `"effort"`, and §5 explains why escalation never
touches it.

### 3.3 One resolver, one ladder

Per `providers-credentials-and-limits-2026-08-23.md` §6, there is exactly one
function that answers "which model runs this ticket":
`work_model.resolve(scope, ticket) -> (handle, ceiling_handle, effort|None)`.
`project_ralph.sh` calls it through a `project_tasks.py resolve-model` verb
rather than keeping its own bash `case`, which is what retires
`RALPH_MODEL_QUICK` / `RALPH_MODEL_DEEP`.

The ladder, in the idiom written three times at `routes/chat.py:211/235/274`:

1. **The ticket's own `model` pin.** Explicit and it wins.
2. **The ticket's live escalation state** (`attempt_tier`, §3.4), set by the
   previous attempt, above the policy because it is newer information than the
   policy's guess.
3. **The assigned agent's pin.** `agents_registry._coerce_model` (`:228-236`)
   already exists and already means "this agent gets this model wherever it
   runs". No new field; it becomes a rung.
4. **The scope's policy row** for the ticket's band.
5. **The global policy row** for the band.
6. **The built-in default row** for the band; an absent band reads as `standard`.

And the two rules that ladder already has, kept verbatim in behaviour:

- A **stored** value that no longer resolves (a tier removed from the catalogue,
  an agent pinning a retired handle) is **SKIPPED to the next rung**, and the
  skip is noted on the ticket. A stored value the operator cannot see must never
  fail a run.
- An **explicit** bad value is rejected at the write, by the ticket editor and
  the API, so it never becomes a stored one.

### 3.4 Escalation, and the answer to Bouchard

**What triggers it.** A finished attempt escalates when it failed for a reason a
stronger model could plausibly fix:

- the reviewer gate refused the work,
- the acceptance criteria failed (`project_ralph.sh` criteria gate),
- the inner iteration cap was reached without converging.

**What does not.** Environment failure (`rc=5`), a touches violation, a
classifier block, a blocked-on-question, and a budget pause. None of those are
capability failures, and escalating them spends a tier for nothing. The budget
pause matters most: `limiting-usage-2026-08-24.md` R2 makes a paused run resume,
and **a pause is not a failure, so a resumed run keeps its tier**.

**How.** `release_or_block` (`project_ralph.sh:1030-1045`) already returns a
failed ticket to `todo` and already counts `attempts` against `max_attempts`.
Escalation writes one field on the way past, `attempt_tier`, the tier the *next*
attempt runs at, and the next claim reads it at rung 2 of §3.3.

**What caps it.** Four things, and they are all already in the tree:

1. The band's `ceiling`. `small` tops out at Sonnet by default. A ticket cannot
   climb out of the appetite it was given.
2. `max_attempts`. Escalation changes which model spends an attempt; it never
   grants one.
3. One step per attempt. No double jumps, so each step's outcome is readable.
4. Admission. The usage limiter already refuses to start a run without budget,
   and a retry is a run.

**Now Bouchard (§2.3).** His finding is that a cascade loses to an upfront router
because it pays the cheap model's generation cost on everything it later
escalates, a *structural* cost, not a routing-signal deficit. That objection is
correct and it does not apply here, for a reason specific to this system:

> **The cheap attempt is not added by this design, and neither is the retry.**
> A ticket that fails its reviewer gate today is already released to `todo` and
> already retried by the next sweep, at the same model, on the same budget. The
> only thing escalation changes is *which model spends the retry that was going
> to happen anyway.* Bouchard's structural cost is a cost this system already
> pays, unconditionally, and gets nothing for.

That converts his argument from a refutation into a design constraint, and the
constraint is: **never let escalation create an attempt.** Rule 2 above is that
constraint, and it is the single rule most worth defending in review.

One honest exception, stated because it cuts the other way. The ledger counts
**tokens**, so it prices a wasted Haiku attempt the same as an Opus attempt,
while the actual bill differs by roughly 15x. Under a token ledger the economic
case for starting cheap is invisible to admission and to the Usage page. That
does not break the design (the correctness argument is about attempts, not
prices), but it does mean the *savings* cannot be demonstrated until the
money conversion lands (`providers...` §7.2, `limiting-usage` "count money, not
tokens"). Listed as a dependency in §6, not smuggled past.

---

## 4. Why not two dimensions

The operator's sketch is coherent and it is the standard one. Three reasons not
to build it, in ascending order of force.

**It duplicates a mechanism, in one direction, by guessing.** Under max-combines,
`complexity` changes the outcome only when it exceeds `size`. Its entire
contribution is catching tickets whose size was called too low. §3.4 catches the
same tickets, from the reviewer's verdict rather than from an estimate, and
Routing Plateau plus Tawosi say the estimate is the weaker instrument.

**Max-combines collapses.** Any monotone combination of two ordered dimensions
produces one ordered output. A 3x3 grid feeding a max yields three distinct
outcomes, and the operator cannot observe anything except which of the three
happened. So the second dimension does not add a degree of freedom; it adds nine
cells of vocabulary in front of a three-valued result. If the two dimensions were
*not* combined monotonically (if complexity picked the model and size picked the
iteration cap, say), that would be genuine two-dimensional control, and it would
also be a different proposal, since neither axis would then be a requirement that
another could outrank.

**It fails the repo's own test.** `agents_registry.py:132-140` deleted `depth` and
`fanout` for having no reader, and named the failure: a control that lies. Two
new fields would bring the ticket to five spec dimensions (`appetite`,
`complexity`, `execution`, `latitude`, `priority`), of which the newest two would
share a single reader with the oldest. `effort` has had a reader for months and is
still unset on 73% of tickets; the observed problem is not that one dimension is
too few, it is that even one is more prediction than anyone wants to supply.

**What would change this answer:** evidence that failures split into two
populations that a single band cannot separate: long-but-simple work that a
cheap model finishes given more iterations, and short-but-hard work that needs a
strong model for three turns. That is a real hypothesis and it is measurable:
`appetite` sets both the tier and the iteration cap today, so if postmortems show
tickets that needed one raised and the other lowered, split them. Measure first.
The instrumentation in §7 is what would tell us.

---

## 5. The vocabulary collision

Two different meanings sit on one word.

- **Ticket `effort`**: `quick | standard | deep`. A size hint. One reader.
- **Chat `effort`**: `low | medium | high | xhigh | max`. Reasoning effort, with
  per-model ladders at `model_caps.EFFORT_BY_MODEL:37-44` (Haiku supports none).

**Rename the ticket one.** The chat one is the vendor's own parameter name, with
the vendor's own level names, matching `output_config.effort` at Anthropic and
`reasoning.effort` at OpenAI, and matching the CLI's `--effort` flag. Renaming it
would put this repo out of step with the API it calls, and it is referenced from
`model_caps`, `chat_store.get_default_effort`, agent frontmatter
(`_coerce_effort`), the composer, and `routes/chat.py`. The ticket field has one
reader and about thirty setters.

**What the rename costs**, named honestly:

- `tasks.py:668-698`: the `_EFFORT_ALIASES` table already folds `s/small/xs`
  and `l/large/xl`. Re-point its canonical outputs to `small|standard|large` and
  every stored value in the tree (`quick`, `S`, `M`, `deep`, `L`) migrates on
  read with no data migration. This is the cheapest part and it is why these
  band names were chosen over inventing new ones.
- Read both keys for one release: `task.get("appetite", task.get("effort"))`.
  Write only the new one.
- `scripts/project_tasks.py:250`: add `--appetite`, keep `--effort` as a
  deprecated alias so no filed script breaks.
- `routes/tasks_all.py:269,354,392,564,668`: the field, the filter and two
  bodies. `routes/chat.py` `FileTaskBody.effort`.
- `frontend/src/lib/tasks.ts:215` `TASK_EFFORTS`, `TicketDetail.tsx:624-637`,
  `TicketFilters.tsx:57,323`, `lib/ticketEdit.ts:34`.
- `scripts/project_ralph.sh:1256-1265`: replaced by the resolver call anyway.
- `docs/ticket-writing.md` gains a line, since it is the page that tells both
  readers what a ticket carries.

**How the two knobs compose.** Model first, effort second, exactly as
`routes/chat.py:235-270` already does it: "which rungs exist is a fact about the
model and validating it earlier is wrong." So a policy row that says
`{"start": "haiku", "effort": "max"}` resolves to Haiku with no effort flag,
which is already the correct behaviour and needs no special case.

**Escalation moves the tier and never the effort.** Two reasons. Changing both at
once makes the next attempt's outcome uninterpretable, which forfeits the
measurement in §7. And Anthropic's own guidance is to hold effort constant within
a cached conversation because changing it invalidates the prompt cache, and a
resumed run (`limiting-usage` R2) is exactly such a conversation. A *policy row*
may still declare a higher effort for a higher band, because that is a standing
choice rather than a mid-run change.

---

## 6. Settings, and what it shows

**`/settings/models`** (`frontend/src/pages/settings/ModelProviders.tsx`, today
credentials and display only) gains a **Work routing** section: three rows,
`small / standard / large`, each with a start-tier picker and a ceiling picker.
An inherited row shows the built-in default as placeholder text, so unset reads
as inherited rather than empty. Money-adjacent config, so **Edit mode**, per
`frontend/CLAUDE.md`, not commit-on-change.

**Per-scope overrides live on the scope**, on the project or agent detail page,
not in a global list, the same choice `scope_execution` makes, where the store
holds only deliberate overrides and the global page shows the armed set rather
than every scope that could be armed.

**On the ticket**, the Effort row becomes an Appetite row plus a read-only line
naming what it resolved to: "standard: starting on Sonnet, may escalate to Opus".
A picker whose consequence is invisible is how a field ends up unset on 73% of
tickets.

**Files that change.** New: `backend/services/work_model.py`, a policy
route (extend `backend/routes/api_models.py`), the Work routing section in
`ModelProviders.tsx`. Changed: `backend/services/tasks.py`,
`scripts/project_tasks.py`, `scripts/project_ralph.sh` (`:52-63`, `:1030-1045`,
`:1256-1300`), `backend/routes/tasks_all.py`, `backend/routes/chat.py`,
`backend/services/usage_ledger.py:139` (add `model` to `record()`),
`frontend/src/lib/tasks.ts`, `frontend/src/pages/TicketDetail.tsx`,
`frontend/src/components/tickets/TicketFilters.tsx`,
`frontend/src/lib/ticketEdit.ts`, `docs/ticket-writing.md`. Unchanged and
deliberately so: `llm_router.py` (still the one tier-to-handle map),
`agents_registry.py` (its `model` pin becomes rung 3 as-is).

**Dependency, not a blocker:** the token-to-money conversion. Until it lands, the
Usage page will show a Haiku attempt and an Opus attempt as the same size (§3.4).

---

## 7. Failure modes

**The band is too low.** The first attempt fails the reviewer, escalates one
tier, and the retry, which was going to happen regardless, runs stronger. Cost
is one cheap attempt's tokens. The bad case is a genuinely large ticket declared
`small`: its ceiling is Sonnet, so it can never reach Opus and will burn its
attempts and block. That is intended (an appetite is a limit, and a limit that
yields is not one), but it must be **legible**: the block reason has to say "hit
the appetite ceiling for band small" rather than "failed after 3 attempts", so
the one action that fixes it, raising the band, is the one the sentence suggests.

**The band is too high.** Opus does a one-line fix. **Nothing catches this, on
purpose.** De-escalation would require predicting that a ticket is trivial, which
is the same prediction §2.2 says fails, aimed the other way. The design's
asymmetry is real and should be stated rather than papered over: escalation
corrects under-calls only. The partial mitigations are that a small job finishes
in few iterations, and that the operator sees the cost, which is a reason the
money conversion matters.

**Escalation burning budget.** The four caps in §3.4. The load-bearing one is
"escalation never creates an attempt"; if a future change makes escalation grant
an extra attempt, Bouchard's structural-cost objection becomes live and this
design becomes worse than doing nothing.

**Interaction with `limiting-usage-2026-08-24.md`.**

- Its §4A wants admission to reserve a history-derived p80 "bucketed by the
  ticket's `effort` when it has one". That bucket becomes `appetite`, and it gets
  better because the band now correlates with the model tier that actually ran.
- Its R1 rules that no design may rest correctness on a cost estimate. Honoured:
  appetite is a **budget the operator sets**, and it is a scheduling hint to
  admission, never a safety mechanism. Correctness rests on the ceiling and the
  attempt cap, which are decisions, not predictions.
- Its R2 pause-and-resume: a paused run keeps its tier. Only a *failed* run
  escalates. This is one `if` and it is the easiest thing here to get wrong.
- Its §6 open question, "should a long-period limit refuse, or degrade to a
  cheaper model?", becomes answerable and cheap: degrade means clamping every
  policy row's `start` and `ceiling` one tier down, one function on the resolver,
  with no second mechanism. That is a genuine argument for this shape.

**What would falsify this design**, stated so it can be checked rather than
argued:

1. Record the tier on every attempt (ticket `attempt_tier` history) and the model
   on every ledger row (`usage_ledger.record`, already wanted by `providers` §7.2).
   Then over the next ~50 sweep failures, compare the success rate of retries
   that escalated against retries at the same tier. **If escalated retries do no
   better, escalation is a control that lies and should be deleted**, leaving the
   policy table.
2. Count how many tickets carry an `appetite` three months after the rename ships
   with the resolved-model line on the ticket page. **If it is still under 30%,
   the declared field is not wanted**, and the honest response is to delete it and
   let every ticket start at the default and escalate. The design already works in
   that configuration, which is the point of §0's last bullet.

---

## 8. For the operator to decide

Four things. Everything else in this document is either measured or follows from
something measured.

1. **The three default rows.** `small -> haiku, ceiling sonnet`,
   `standard -> sonnet, ceiling opus`, `large -> opus, ceiling opus` is a spend
   posture, not a fact. Notably `standard` starting on Sonnet is a *raise* from
   today, where 73% of tickets route to nothing; it will cost more per ticket and
   is expected to cost less per completed ticket. That trade is a judgement call
   and it is his.

2. **Whether the ticket keeps a `model` pin at all** (rung 1 of §3.3). It is the
   escape hatch that makes the policy table safe to be wrong about, and it is
   also the field most likely to be set once and then quietly outlive its reason.
   The alternative is that the only per-ticket control is the band.

3. **`appetite` as the word.** It is Shape Up's term and it carries the reframe
   in the name, which is most of why it was chosen. `budget` is plainer and
   collides with `budget_engine.py`, which is the personal-finance planner and
   has nothing to do with models. `size` is available and would re-import the
   prediction framing this design is trying to leave.

4. **Whether the drafting model proposes a band at filing time.** Yes means most
   tickets arrive with a band nobody chose, and a machine's guess in a field whose
   entire value is that it is a human decision. No means the field stays mostly
   unset and the default carries the work, which §7's second falsifier says is
   an acceptable resting state.

Decided here, and not offered as questions: one dimension rather than two (§4);
escalation on measured failure rather than a second declared dimension (§3.4);
renaming the ticket field rather than the chat one (§5); the `scope_execution`
storage idiom and the `routes/chat.py` resolution ladder (§3.2, §3.3); and one
resolver, because `providers-credentials-and-limits-2026-08-23.md` §6 already
settled that.

---

## 9. Tickets filed

Sequenced. 2 blocks 3 blocks 4; 6 blocks on 2; 1 and 5 are independent.

| id | title | priority |
|---|---|---|
| `ce1a5770` | Rename the ticket effort field to appetite, with bands small/standard/large | medium |
| `372c72f7` | One resolver decides which model runs a ticket, from a stored policy table | high |
| `0e68c881` | The sweep asks the resolver which model to run, instead of its own case statement | high |
| `2c7c245e` | A run that fails the reviewer retries one tier up, capped by the band ceiling | medium |
| `ed3d497f` | Record which model spent the tokens, so escalation can be judged rather than argued | medium |
| `4f52c95c` | Settings edits the band-to-model policy, and a ticket says which model it will run | medium |
