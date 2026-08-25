# Counting tokens: one meter, four questions, three incompatible units

- Date: 2026-08-25
- Status: **DESIGN. Nothing changed yet.** Written after the operator asked
  whether usage is being counted "in an inconsistent or incomplete or bad way,"
  and asked for a holistic centralized fix rather than a patch at one call site.
- Sequences BEFORE [`limiting-usage-2026-08-24.md`](limiting-usage-2026-08-24.md).
  See section 0.

---

## 0. Read this first

**No usage cap, monthly limit or spend alert can be trusted until this lands,
because the number a cap would compare against is not a consistent quantity.**

`.runtime/usage.jsonl` has one column called `tokens`. Three writers fill it, in
three different units, from three different sources, and `window_usage()` adds
them together. On the same measured turn those units differ by up to **13.6x**
(section 3). A cap of "30M a month" therefore means somewhere between 2.2M and
30M of real work depending on which writer happened to produce the rows, and the
operator has no way to know which.

Everything in `limiting-usage-2026-08-24.md` — the session window, the monthly
limit, pause-and-resume at the cap, sizing a reservation from history — is
arithmetic performed on this column. Fixing the window shape while the unit is
undefined moves the error, it does not remove it. **This work is the
prerequisite for that work, not a parallel track.**

The defect is the meter. It is not chat, and the current 99.4%-of-spend-is-chat
ratio is not evidence about chat: it is a snapshot of a system whose autonomous
half is barely running, measured with a meter that inflates the chat half by
roughly an order of magnitude relative to the worker half.

---

## 1. What was measured

`.runtime/usage.jsonl`, 5,040 rows, 2026-07-15 to 2026-08-25. Totals as recorded:

| role | rows | tokens |
|---|---:|---:|
| chat | 3,121 | 6,506,338,712 |
| compaction | 1,813 | 16,703,286 |
| worker | 93 | 20,378,637 |
| (none, pre-role) | 13 | 998,353 |
| **total** | **5,040** | **6,544,418,988** |

The `chat` population has **median 11 and max 120,087,735**. Both are impossible
for one chat turn. That is the symptom this document explains. There are two
independent causes and they are not the same bug.

### 1a. The median is test pollution, not a metering bug

`usage_ledger.LEDGER_PATH` is resolved **at import time** from
`paths.env_path("WORKSHOP_USAGE_LEDGER", "usage.jsonl")`, i.e. the real
`.runtime/usage.jsonl`. Five test files patch it
(`test_usage_ledger.py`, `test_run_cost_is_visible.py`, `test_voice_spend_is_metered.py`,
`test_chat_compact.py`, `test_sweep_halt_is_not_success.py`). Every other test
that happens to exercise a path calling `record()` writes into the operator's
production ledger.

Measured, exactly:

- **1,724 rows of exactly 11 tokens**, all with `run: "run-ctx"` — the turn id
  hardcoded at `backend/tests/test_context_measurement.py:93`. Its `_result()`
  fixture is `_usage(1)` = `input 0 / cache_read 1 / cache_creation 0 / output 10`,
  and `_spend_tokens_from_usage` of that is 11. Only one of the four tests in
  that class monkeypatches `record`; the other three do not, so each run of the
  suite appends three real rows.
- **335 rows of exactly 4,500 and 336 of exactly 9,050**, all with
  `run: "run-second-prompt"` (`test_turn_survives_a_second_prompt.py:76`).
  4,500 = 1000+1000+2500, the three `_assistant_with_usage` frames at `:411-413`
  with no usage on the `result` — so this is `spend_sum`. 9,050 = 9000 + the
  50-token delegate frame at `:431-433`. Same, also `spend_sum`.
- **1,801 of the 1,813 compaction rows name a chat id that has never existed on
  disk** (134 real chat directories; 1,695 distinct chat ids in the compaction
  rows). Their values repeat exactly: 9,977 appears 582 times, 9,972 appears 388
  times. These are synthetic threads from the compaction suite.

Totals: **4,208 of 5,040 rows (83.5%) are test writes.** They are only
**21.1M of 6.544B tokens (0.32%)**, which is why nobody noticed — but they are
what sets the median to 11, and they are 99.0% of everything filed under
`compaction`.

Cross-check that isolates the real population: rows whose `chat` id matches a
thread directory that actually exists.

| role | rows | with a real chat id | tokens on real ids | tokens on phantom ids |
|---|---:|---:|---:|---:|
| chat | 3,122 | 716 | 6,527,230,301 | 5,222,565 |
| compaction | 1,813 | 12 | 173,407 | 16,529,879 |

**The real chat population**: 716 rows, median 4,070,432, p80 15,022,893,
max 120,087,735, min 16,053. That is a believable *shape* for long agentic turns
and an unbelievable *magnitude*, which is section 2.

This is also, on its own, a correctness bug in the test suite: it mutates
operator state, and any statistic computed from the ledger is contaminated.

### 1b. `spend_result` and `spend_sum` — what each actually contains

Both are real and both are visible in the ledger (11 is a `spend_result`; 4,500
and 9,050 are `spend_sum`s), which confirms the `or` chain at
`build_streaming.py:2788` picks whichever the CLI happened to emit:

```python
spend_tokens=int(state.get("spend_result") or state.get("spend_sum") or 0)
```

Semantics, confirmed by reading: a `spend_result` of 0 falls through to
`spend_sum`; a small nonzero `spend_result` **suppresses** `spend_sum` entirely;
and when a turn sees more than one `result` (an injected second prompt),
`state["spend_result"]` is overwritten at `:2607` by the last one, so the earlier
rounds' spend is silently dropped. Which branch fires is a property of the CLI
build, not of the work done. **A single ledger column holding either of two
quantities selected by an `or` is not a measurement.**

---

## 2. The magnitude: the same API call is counted several times, and its output is never counted at all

Three real turn journals survived pruning (`.runtime/turns/*/stream.jsonl`; the
directory holds ~10 turns at a time, so this is the only real-data window
available and it closes fast — one of the three was pruned mid-analysis).

```
turn      frames  distinct message ids   spend_sum(all frames)  spend_sum(dedup by msg id)
ef36e0da     308                  200              27,063,556                  17,318,761
826ab331      86                   54              10,652,929                   5,587,648
79cb5787     105       (result present)             10,721,985   spend_result = 3,526,423
```

Component breakdown, deduplicated by `message.id`:

| turn | fresh input | cache_read | cache_creation | output |
|---|---:|---:|---:|---:|
| ef36e0da | 400 | 16,685,137 | 631,760 | 1,464 |
| 826ab331 | 108 | 5,339,452 | 247,716 | 372 |

Three separate defects fall out of this table.

**(i) Duplicate frames.** The CLI emits one `assistant` stream event per content
block — text, thinking, each `tool_use` — and every event for one API message
carries the *same* message-level `usage` object. `state["spend_sum"]` adds it
once per event. Measured inflation: **1.54x** (308/200) and **1.59x** (86/54).
Verified by printing consecutive frames: three frames sharing
`msg_011CePyzjWhwhDGRdbfxUZms`, each reporting the identical
`{input 2, cache_creation 56168, cache_read 16012, output 5}`.

**(ii) Output is never measured.** In those frames `output_tokens` is 2–20 —
the `message_start` placeholder, not the completed count. 200 API messages
report 1,464 output tokens between them. The CLI's own session transcript
(`~/.claude/projects/<slug>/<session>.jsonl`) carries the FINAL usage for the
same messages, with real values (`output_tokens: 391`, plus
`output_tokens_details.thinking_tokens`). **The journal's assistant frames are
prompt-side snapshots; the one thing that is actually priced at 5x input is
absent from every chat row we have ever written.**

**(iii) Cache reads dominate and are counted at parity.** cache_read is
**96.3%** and **95.6%** of the deduplicated total on those two turns. This is
expected and correct behaviour from the API — each model call re-reads the whole
cached prefix — and it means summing raw usage across a turn multiplies the
prefix by the number of model calls. A 200-call agentic turn on a 160k prefix
reports ~32M "tokens" for work that cost a fraction of that.

**So the parent hypothesis was half right.** `spend_result` vs `spend_sum` is a
real inconsistency (1b) and cache-read summation is a real unit problem (iii),
but neither explains the median, and neither is the largest error. The largest
error is that **`spend_sum` is not a sum over API calls at all — it is a sum over
stream frames** — and the second largest is that output is missing entirely.

---

## 3. Three writers, three units, one column

| writer | source of usage | dedup | cache_read weight | output counted |
|---|---|---|---|---|
| `build_streaming.py:2788` (text chat) | `result` usage if present, else sum over **frames** | no | 1.0 | no (placeholder only) |
| `voice_worker.py:395` (voice chat) | `result` usage only | n/a (one event) | 1.0 | yes |
| `scripts/ralph.sh:905-911` (worker) | `result` usage per iteration | n/a | **0.1** | yes |
| `routes/chat.py:2208` (compaction) | none — `len(chars)/4` estimate | n/a | n/a | n/a |

`ralph.sh` already made the decision this document is asking for, in a comment at
`:905`: *"Weight cache-read tokens at 0.1: the subscription rate limits count a
cache read far below a fresh input token, so metering them at parity trips the
budget long before the real quota is spent."* It was right, and it was applied to
one writer out of four.

### The conversion factor, on our data

Same two real turns, evaluated under each candidate unit:

| unit | ef36e0da | 826ab331 |
|---|---:|---:|
| what chat records today (raw sum over frames) | 27,063,556 | 10,652,929 |
| raw sum, deduplicated by message id | 17,318,761 | 5,587,648 |
| **cost-weighted** (components priced at published API rates), deduplicated | **2,302,137** | **782,141** |
| fresh-only (fresh + cache_creation + output), deduplicated | 633,624 | 248,196 |

- today vs cost-weighted: **11.8x** and **13.6x**
- today vs fresh-only: **42.7x** and **42.9x**
- of which duplicate frames alone: 1.54x / 1.59x; cache weighting alone: 7.5x / 7.1x

**The same ledger number means between 1x and 43x different amounts of real work
depending on which of these four definitions you had in mind.** That is the fact
that makes a monthly cap meaningless today, and it is the whole argument of
section 0.

Sanity check on the recommendation: the 716 real chat rows have median 4.07M
today; at the measured ~12.6x that is ~323k cost-weighted, against a worker-run
median of 107,284 (already cost-weighted, from `.runtime/ralph.log`,
n=168). A heavy chat turn costing two to ten worker runs is a believable number.
6.4M is not.

---

## 4. The four questions

One number is used for all four today. They cannot share a number, and two of
them cannot even share a helper.

**(a) Occupancy — "how full is this context window right now."**
`fresh_input + cache_read + cache_creation` for the LATEST model call, from the
parent conversation only. Overwritten per call, never summed; drops after a
`/compact`. Bounded by the model's window. Answered by
`_ctx_tokens_from_usage` (`build_streaming.py:1266`), which is correct and whose
docstring already states the rule this document generalizes: spend and occupancy
*"are different questions and must not share a helper."* Consumer: the composer
meter, `chat_store.set_thread_context_tokens`.

**(b) Billable spend — "what did this turn or run cost to serve."**
Summed over DISTINCT API calls: `fresh_input + cache_creation + cache_read +
output`, cache_read at its real volume. Unbounded; grows with the number of model
calls. Answered today by `_spend_tokens_from_usage` (`:1301`) — correct in
formula, wrong in application, because its caller sums it over frames rather than
calls and reads a placeholder for output. Consumer: the per-turn and per-run cost
display.

**(c) Budget consumption — "what has this scope consumed this period," the number
admission compares against.**
Must be *rate-limit-shaped*, not volume-shaped, because that is what actually runs
out. Cache reads are consumed far below parity, so this is not (b). Answered today
by nothing consistent: `ralph.sh` computes it at weight 0.1 and everything else
hands it (b). Consumer: `can_admit`, `window_usage`, the Usage page headline.

**(d) Money — "what would this have cost on metered API pricing."**
Requires the model id and a price table; the four components have different
prices (cache_read ~0.1x input, cache_creation ~1.25x input, output ~5x input),
and the ratio between (b) and (d) changes with the model, so **no single token
total is even rank-preserving between two scopes on different models.** Answered
today by nothing. The CLI already hands us the answer for free in the `result`
envelope's `total_cost_usd`, which `build_streaming.py:889` parses, forwards to
the SSE stream, and then discards.

**Which cannot share a number, and why:**

- (a) and (b) cannot, because (a) is a max over calls and (b) is a sum over calls.
  Already established in the tree and already respected.
- (b) and (c) cannot, because they weight cache_read differently by construction —
  by a measured factor of 7.1x to 7.5x on our own turns.
- (c) and (d) cannot, because (c) is model-independent and (d) is not. A monthly
  budget expressed in tokens silently changes meaning when the model does.
- (a), alone, is the only one that is not additive and must never be summed
  across turns.

---

## 5. Decision: the unit for budget and admission

**Store the four raw components. Derive dollars from a versioned price table.
Denominate budgets in dollars.** There is no Workshop token unit.

This corrects an earlier draft of this section, which proposed a composite
"effective tokens" = `input + cache_creation + output + 0.1 x cache_read`. That
is withdrawn. The reasons it is withdrawn are the reasons not to re-propose it:

- **It is not what the field does.** The OpenTelemetry GenAI semantic conventions
  define four raw counters (`gen_ai.usage.input_tokens`, `output_tokens`,
  `cache_read.input_tokens`, `cache_write.input_tokens`) and the metric
  `gen_ai.client.token.usage` splits only by `gen_ai.token.type` in
  {input, output}. There is no weighted unit and no cost metric in the registry.
  Anthropic's own published sum is unweighted. Langfuse, LangSmith, Helicone,
  Braintrust and Portkey all store raw components and derive dollars from a
  versioned price table. None normalizes to one token unit.
- **It has been tried and retired.** GitHub Agentic Workflows shipped this exact
  composite, named Effective Tokens, with weights 1.0 input / 0.1 cache / 4.0
  output. They deprecated it on 2026-06-05 and replaced it with a dollar-derived
  credit. Adopting a unit another vendor withdrew two months ago is not a
  defensible position.
- **The weights were wrong anyway.** Output is 4x to 5x input, not 1x, and
  cache_creation is 1.25x, not 1x.
- **This document's own dependency already said so.** `limiting-usage` §4 ends
  "And count money, not tokens", and R5 says the monthly limit "is the only limit
  that should ever be denominated in currency."

Dollars are the correct denominator for a specific published reason, not a
preference: usage past a plan's allowance bills at standard API rates. That is
the only unit a subscription plan and a token ledger share.

**Raw sum, which is what we record today, stays rejected.** It ranks a
cache-heavy agentic turn about 12x above a chat turn doing the same work, and it
is the only option that gets worse as prompt caching improves, so making the
system cheaper makes it look more expensive. The measured gap between what the
chat writers record and a cost-weighted view is 11.8x to 13.6x, which is the
evidence that the current column is unusable rather than merely imprecise.

**Fresh-only, excluding cache reads, also stays rejected.** Cache reads are real
API calls at a real price, and excluding them reports a 200-call cached turn as
nearly free.

### The price table

`services/model_prices.py`: per model, input / output / cache-write / cache-read
per million tokens, plus an `effective_from` date. A ledger row records the
`price_version` in force when it was written, so a price change never rewrites
history and an old row keeps the cost it actually had. Version 1, as published
on 2026-08-25: Opus 5 $5 / $25 per MTok, Sonnet 5 $3 / $15, Haiku 4.5 $1 / $5,
cache write 1.25x base input, cache read 0.1x base input.

`effective_from` rather than an opaque integer because the question asked of it
is always "what did this cost on the day it ran", and a date answers that without
a lookup table of what each version meant.

### What the operator sees

Dollar figures are derived at published API rates and are an estimate of value
consumed, not an invoice. That is worth **one short info note beside the totals
in Settings, and nothing else**. No per-figure caveat, no per-row tooltip, no
banner. Stating it once is honest; stating it everywhere is padding, and it is
explicitly not wanted.

---

## 6. The central seam

One module owns the parse from a vendor usage block to named quantities. Every
writer calls it. **No caller re-derives a token count from a usage dict again.**

### New module: `backend/services/token_usage.py`

Pure stdlib, no imports from `services.*` — importable from the FastAPI app, from
`voice_worker`, and from the shell via the existing `usage_ledger` CLI shim. It
holds no state and does no I/O.

```python
@dataclass(frozen=True)
class Usage:
    """The four components of one API call, as the vendor reported them."""
    input: int = 0            # fresh, uncached prompt tokens
    cache_read: int = 0
    cache_creation: int = 0
    output: int = 0

def parse(u: object) -> Usage:
    """One vendor usage block -> components. Tolerates None/garbage (-> zeros).
    The ONLY place a vendor field name appears."""

def occupancy(u: Usage) -> int:
    """(a) How full the window is. input + cache_read + cache_creation.
    A MAX over calls, never a sum. Replaces _ctx_tokens_from_usage."""

def spend(u: Usage) -> int:
    """(b) Volume billed: all four components at parity. Sum over CALLS."""

def cost_usd(u: Usage, model: str, *, price_version: str | None = None) -> float:
    """(c) and (d). What this call cost at published API rates. The budget and
    admission unit. `price_version` defaults to the table in force now; a stored
    row passes the version it recorded, so history keeps its own prices."""

class TurnMeter:
    """Accumulates a turn's usage across stream frames, DEDUPLICATED BY API
    MESSAGE ID. The CLI emits one frame per content block sharing one usage
    object; adding each frame is what inflated chat spend by ~1.55x.

    Also separates the parent conversation from delegates: `occupancy` reads
    parent-only (a delegate has its own window), `spend`/`cost_usd` count
    everything (a delegate's tokens are really spent). This is the rule
    build_streaming already implements inline and would otherwise re-implement."""
    def observe(self, message_id: str | None, usage: object, *, parent: str | None) -> None: ...
    def observe_result(self, usage: object, cost_usd: float | None) -> None: ...
    @property
    def totals(self) -> Usage: ...        # deduplicated sum over calls
    @property
    def occupancy(self) -> int: ...       # latest parent-only call
    @property
    def cost_usd(self) -> float | None: ...
```

`observe_result` exists because the `result` envelope is the CLI's own
authoritative total and carries `total_cost_usd`. **Which of the two sources wins
must be decided by measurement, not by an `or`.** On the one turn where both were
present, `result` reported 3,526,423 against a frame sum of 10,721,985 — a 3x gap
that is probably `result` excluding sidechain/subagent usage, but that is
**unverified**, and only 10 journals exist at a time so it needs a deliberate
probe (§8, ticket 1). Until it is pinned, `TurnMeter` records both and the
recorded row says which it used.

### Record shape

`usage_ledger.record()` grows one optional keyword and one version marker.
Rows stay a superset of today's, so every existing reader keeps working:

```json
{"ts": "...", "project": "development", "tokens": 782141,
 "role": "chat", "run": "...", "chat": "...",
 "v": 2,
 "unit": "usd",
 "components": {"input": 108, "cache_read": 5339452,
                "cache_creation": 247716, "output": 372},
 "calls": 54,
 "source": "result" | "frames",
 "cost_usd": 1.83}
```

- **`components` is the load-bearing addition.** `tokens` is a *derived* field.
  Storing the components means the unit decision in §5 can be revisited, and money
  can be added, **without losing history** — every past row can be re-evaluated
  under a new weight or a price table by replaying the file. Storing only a total,
  which is what we do today, is why the existing 6.5B is unrecoverable.
- `unit` names which of §4's quantities `tokens` holds, so a reader never has to
  guess and a mixed-version file is still summable (§7).
- `calls` is the deduplicated API-call count: the direct check on defect (i), and
  a far better cost predictor for `limiting-usage` §4A than a whole-task p80.
- `cost_usd` from the vendor when present. It is the (d) answer and it is free.
- `v: 2` marks post-fix rows. Absence means v1.

`record()` keeps its positional `(project, tokens)` signature — a test monkeypatches
it positionally on purpose (`test_context_measurement.py:147`) and several call
sites use it that way.

### Writer migration

1. **`build_streaming`** — delete `_ctx_tokens_from_usage` and
   `_spend_tokens_from_usage`, delete `state["spend_sum"]` and
   `state["spend_result"]` and the `or` chain at `:2788`. `_drive_turn` holds one
   `TurnMeter`; `_ingest` calls `observe(message_id, usage, parent=...)`;
   `_finalize_turn` takes the meter and records components once. The re-attach
   path at `:2528` calls `observe_result`.
2. **`voice_worker`** — drops its private import of `_spend_tokens_from_usage`
   and calls `observe_result` on the same `TurnMeter`. Voice and text then agree
   by construction, which is the property that was missing when voice went
   unmetered for months.
3. **`ralph.sh`** — the inline `python3 -c` at `:882-913` is replaced by a call to
   the CLI shim, which grows `record --usage-json <file>` (and `--chat`, missing
   today). The 0.1 weight stops being duplicated in shell. Behaviour is unchanged
   for the worker, by design: the worker numbers are already in the target unit.
4. **`routes/chat.py` compaction** — keeps its `chars/4` estimate (`call_cloud`
   returns no counts) but records `unit: "estimate"` and no `components`, so an
   estimate is never mistaken for a measurement. If `call_cloud` is later made to
   return usage, this becomes a real row with no reader change.

### Reading old rows

`_read_entries()` normalizes on load: a row with no `v` is `v: 1`, `unit:
"raw-frames"` for `role in (chat,)`, `unit: "weighted-tokens"` for `role == "worker"`,
`unit: "estimate"` for `role == "compaction"`, and carries no components. Callers
that need a consistent unit ask for it and get an explicit answer about what
cannot be converted — see §7. Nothing silently rescales a v1 row by 12.6x: that
factor is a two-turn measurement, not a constant.

---

## 7. What to do with the 6.5 billion

**Recomputation from the ledger is impossible.** Rows store a total and nothing
else. There is no arithmetic that recovers components from a sum.

**Recomputation from the journals is impossible.** `.runtime/turns/` holds about
ten turns at a time. **Zero** of the 3,121 chat rows have a surviving
`stream.jsonl` — checked directly.

**Recomputation from chat logs is impossible.** `log.jsonl` message `meta` was
scanned across every thread on disk: the keys present are `streaming, turn_id,
model, segments, ended_at, started_at, injected, during_turn, at_segment,
at_unit, cut_id, notes, error, receipts, stopped, attachments, cut_head, kind,
autonomous, ticket, scope, block_kind, job, status, heard, timeout, queued`.
**No usage, no spend, no cost is persisted anywhere in the chat store.**

**Partial recomputation from the CLI's own transcripts is possible, and it is the
one real option.** `~/.claude/projects/<encoded-cwd>/<session>.jsonl` (877MB, 747
files for this checkout alone) carries per-message FINAL usage with real
`output_tokens` and `output_tokens_details.thinking_tokens`, plus `sessionId`,
`requestId` and `isSidechain`. Thread meta stores `claude_session`. So a
thread's CURRENT session can be recomputed exactly. The limits are real and
should not be oversold: `claude_session` holds only the latest session per thread
(it rotates on `/clear` and on branch), the ledger row's `run` is a turn id that
appears nowhere in the transcript, and turn→session mapping was never persisted.
So this recovers *some* threads' recent history, not the 40 days.

**Decision:**

1. **Do not delete and do not rescale.** Mark. Rewrite the file once, adding
   `v: 1` and the inferred `unit` to every existing row, in a single migration
   with a `.bak`. No token value changes.
2. **Delete the test rows.** The 4,208 rows with a phantom `chat` id or a
   `run` of `run-ctx`/`run-second-prompt` are not history, they are a bug's
   output. They are 83.5% of rows and 0.32% of tokens: removing them fixes every
   percentile without moving any total that matters. Do this in the same
   migration and report the count.
3. **Recompute where the transcript survives**, as a one-shot backfill, writing
   `v: 2` rows with components and marking the v1 rows they supersede with
   `superseded_by`. Best-effort by design: it improves the recent record and does
   not pretend to fix the archive.
4. **Never mix units in one sum.** `window_usage()` gains an explicit
   `unit=` argument and refuses to add rows it cannot express in that unit,
   returning them as a separate `unconvertible` total that the Usage page shows
   as "before the meter was fixed". A visible gap is honest; a rescaled guess is
   the same class of error as the original bug.
5. **A reader distinguishes them by `v`.** v1 = total-only, unit inferred from
   role, not convertible. v2 = components present, any unit derivable.

Note for whoever wires `prune()`: `limiting-usage` §R4 already flags that it has
no live caller and that wiring it at the 5h window would destroy the history a
monthly limit needs. That is now doubly true — it would also destroy the
components a re-weighting needs.

---

## 8. The gate

`can_admit`'s docstring says *"interactive chats are never recorded here"* and
`_DEFAULT_CONFIG` repeats it: *"interactive chats are never recorded here, so
they are free to use the whole budget."* **That is false today.** Chat is
recorded, with `role="chat"`, and has been for months. The comment at
`build_streaming.py:3383` about double-counting a reserve describes the intent
correctly, but the ledger docstring at `usage_ledger.py:22` — "CHATS ARE EXEMPT
FROM ADMISSION, NOT FROM ACCOUNTING" — is the accurate one. The two disagree in
the same file's own comments.

**Does the exemption still hold? Yes, and for a better reason than the one
written down.** Chat is not gateable: the operator cannot be refused mid-sentence,
so counting chat against the autonomous ceiling can only starve autonomy — which
is exactly what happened on 2026-08-22 with compaction, documented at
`usage_ledger.py:52-68`. The mechanism (`ADMISSION_EXCLUDED_ROLES`) is right.
What is wrong is the *justification* in the docstrings, which will mislead the
next reader, and the fact that `autonomy_budget_fraction` (0.65) reserves 35% for
interactive headroom that the code cannot see, cannot measure and therefore
cannot report as sufficient or insufficient.

Once the unit is fixed, chat rows become comparable to worker rows for the first
time, and the reserved fraction becomes checkable: **is 35% of app_total actually
what chat uses in a window?** With the corrected unit that is a one-line query
that today cannot be asked. Recommended rule, unchanged in mechanism:

- Admission keeps excluding `("chat", "compaction")` — never gate the operator.
- Admission compares **dollars** only, and refuses to admit against a
  window containing unconvertible v1 rows without saying so in the refusal text.
- The interactive reserve stops being a blind constant: report measured chat
  consumption against the reserved fraction on the Usage page, so 0.65 is a
  number with evidence behind it rather than a guess that has never been checked.
- Nothing else about admission changes here. Pause-and-resume, the session window
  and the monthly limit are `limiting-usage`'s work and they sit on top of this.

### Which of `limiting-usage-2026-08-24.md`'s open questions this closes

- **"And count money, not tokens"** (§4, and §R5's "the only limit that should
  ever be denominated in currency"). Closed as *sequenced and made cheap*: money
  is the right unit for the monthly limit, and it is also the right unit for
  the 5h limit, and storing components (§6) makes the switch a re-read of history
  rather than a rewrite. `total_cost_usd` is already on the wire and already
  discarded.
- **"Should the operator's own chat count against anything at all?"** (§6).
  Closed on the mechanism — no, it must never gate — and *reopened correctly*:
  the reason it surprised him is that the Settings headline sums two different
  units and calls the result usage. Once (b), (c) and (d) are named, the page can
  show autonomous consumption against the ceiling and chat consumption against the
  reserve, separately, which is the answer he was actually looking for.
- **§4A "size the reservation from history"**, demoted to a scheduling hint by
  R1. This makes it a *usable* hint for the first time: `calls` and per-iteration
  components give a per-step cost, which R2 already identified as the only
  estimate that stays a hint. It does not resurrect the estimate as a safety
  mechanism.
- **NOT closed, and explicitly out of scope**: pause-and-resume (R2), the session
  window (R6), the monthly limit itself. This document makes their arithmetic
  trustworthy; it does not do them.

---

## 9. Open

- **Does `result.usage` include subagent (sidechain) usage?** One observation
  says no (3,526,423 vs a 10.7M frame sum on the same turn) and one observation
  is not a finding. Decides whether the authoritative source is the `result`
  envelope or the deduplicated frames. Needs a deliberate probe against a turn
  with a known delegate before `TurnMeter` picks a winner.
- **`output_tokens_details.thinking_tokens`** is reported separately in the
  transcript. Thinking is billed as output; confirm it is already inside
  `output_tokens` rather than additional to it, before adding it anywhere.
- **`cache_creation` is priced above fresh input** (~1.25x) while this design
  weights it at 1.0. Below the noise floor for a dollar figure; matters for
  money, where the price table handles it.
- The 0.1 cache-read weight is taken from `ralph.sh`'s stated reasoning about
  subscription rate limits, not from a measurement of the rate limiter itself.
  It is the right order of magnitude and it should be revisited when money lands.
