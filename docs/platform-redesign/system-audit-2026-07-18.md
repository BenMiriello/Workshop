# System audit: tickets, roles, orchestration — 2026-07-18

Audit of how tickets are defined, how work is scheduled, whether operator asks
survive the pipeline, and whether the role architecture routes anything. Two
parallel read-only audits plus direct inspection of the live store and logs.

## Headline

The **execution** half is real: claim → lease → sweep → reviewer gate →
committed-work gate → receipt all work, and the resident runner drains the
queue. The **orchestration** half is largely unreachable. Today the system is a
single-threaded ticket-chewer with a deterministic diff-linter as its gate,
wrapped in a role/dispatch architecture that in practice routes zero agents.

## 1. Ticket definition — the effort is in the wrong fields

The suspicion that ticket-writing is procedural box-checking is **half right,
and the reality is worse**.

The prose is genuinely good. A representative body cites `file:line`, states
what already exists, and carries a measurement ("21 of 24 open tickets are
ready simultaneously"). Not padding, not uniform.

But the machine-readable fields are empty or wrong. Across 57 tickets:

| field | set on | read by |
|---|---|---|
| `text` (prose) | 57/57 | the worker |
| `touches` | 30/57 | the touches gate (enforcing) |
| `effort` | 15/57 | hint only |
| `acceptance` | **5/57** | the done-gate |
| `depends_on` | **5/57** | the scheduler |

So the effort goes into fields that read well, and the fields that actually
gate execution are blank. The template optimizes for prose completeness and
skips the load-bearing structure.

### The lane contradiction (root cause of the stall)

Four of the five blocked tickets were blocked **by their own `touches` lane,
not by anything a worker did**. The clearest case declared a lane of exactly
one file — the one file its own body says is already finished ("the computation
exists; the CONSUMERS are the work") — while excluding all three consumers it
then named.

Such a ticket cannot be completed. Write the consumers → the touches gate
blocks it. Skip them → the done-gate reports "nothing was committed". Three
attempts, then blocked, worker blameless every time.

**Fixed:** `add()` now widens a declared lane to cover repo-relative paths the
body names (`tasks.lane_gaps`). Empty lane still means unrestricted; a bare
filename with no directory does not widen anything.

### `touches` was write-once

It was a parameter of `add()` but not `update()`, so the single most common
cause of a blocked ticket could not be corrected through the supported path —
it required hand-editing `.tasks.json`. **Fixed.**

### `.tasks.json` was excluded from every commit

A ticket whose deliverable is *data* (C2: backfill `depends_on`) was unwinnable
by construction: its only output file was stripped as bookkeeping churn, so the
done-gate always saw an empty commit. **Fixed** — an explicitly declared lane
now outranks the bookkeeping filter.

### `evidence`

The suspicion here is justified: it is **two different things sharing a name**.
On a *job* it is a real receipt (files, commits, typecheck result) rendered in
`JobDetail.tsx` — that one earns its place. On a *task* it is
`[{kind: "note", ref: "Done in f031b18: ..."}]`, a freeform changelog string
with **no mechanical consumer**. The task-level field should either become
structured or collapse into the job receipt. Not yet changed.

### Priority has no scale

Live values: 90, 89, 88, 85, 84, 72, 70, 60, 20, 5, 5, 4, 4, 0, 0. Nothing
defines what a 60 means versus a 5, so the numbers are not comparable across
filing sessions and low-priority asks are unreachable in practice.

## 2. Roles — one of five dispatches

`ROLES = (maker, reviewer, triager, keeper, broker)`.

| role | status |
|---|---|
| **keeper** | **The only role that works end to end.** Live cron trigger, fresh artifacts. |
| **maker** | Built, reachable, never fires — all 20 builder agents carry `triggers: []`. |
| **triager** | 530 lines implemented, **never run once** — `.runtime/triager/` does not exist. Its only agent (`masini`) has trigger kind `discord`; the dispatcher fires `cron`/`loop` only. |
| **reviewer** | Deliberate stub (`skip-no-runner`). `agents/work-reviewer.md` has **zero call sites** — the gate is the deterministic shell script, not the documented LLM subagent. |
| **broker** | Demand-only, never dispatched. Coherent. |

The dispatcher ran **141 consecutive ticks** whose only output was
`skip-assistant plaid-watcher`; 132 ended in 0s. That read as an idle system.
It was a silent wiring failure — two filters discarded agents with no log line
at all.

**Fixed:** the dispatcher now emits `skip-no-triggers` and `skip-unroutable`.
Running it names 20 no-trigger builders plus `email-triager` (event),
`masini-commute` (manual), and `masini` (discord). An unroutable trigger kind is
a wiring bug, not a schedule, and should not look like quiet.

## 3. Scheduling — serial by four independent layers

`next_ready` **does** honor `depends_on` correctly. It is inert because only
5/57 tickets populate it. It materializes the complete ready set and then
discards everything but `cands[0]`.

Serialization points, all active:

1. `MAX_PROJECT_SWEEPS = 1`, `MAX_AGENT_SWEEPS = 1`
2. three exclusive `flock`s, all keyed on `md5(DIR)` → same tree
3. the sweep loop is strictly sequential, one blocking worker per iteration
4. `cands[0]`

Worktree isolation is built but off everywhere; `CONCURRENT_WORKTREES_MAX` is
declared and never read. **Two tasks cannot run concurrently anywhere in the
current configuration.** The "concurrent swarm" is not throttled — it is absent.

## 4. No planning step, no shared context

The raw ticket string goes straight to the worker, whose first instruction is
"execute it FULLY". There is no decomposition or reasoning phase. The only
escape hatch is failure-driven: a worker that finds a task too big writes
*prose* proposing sub-tasks — not structured tasks with `depends_on`.

Task B's worker sees task A's output **only through the git tree**. Linkage
fields (`discovered_from`, `parent`) exist, are populated on 2/57 and 0/57
tasks, and have no consumer that injects them into a prompt. The one real
channel is learnings, which is process lessons in a shared bucket, not work
product.

Planning does happen — in the chat orchestrator — but only with the operator
present. **The autonomous loop consumes specs; it cannot produce or refine
them.**

## 5. Operator asks — the pipeline leaks at two joints

Capture into design docs works well. The leaks:

- **doc → build ticket**: designs exist with no ticket implementing them.
- **chat → durable note**: memory-keeper consolidation **dropped a directive**.
  Present in `chat-distilled.md`, absent from `consolidated-memory.md`:
  *"Until automated ticket system completes several tickets end-to-end reliably,
  default to manual work and in-conversation monitoring instead of filing."*
  Restored.

**The source document is gone.** Six ticket bodies instruct a worker to read
the verbatim note *"Orchestration-surface requirements (raw)"*. That string
matches only `TODO.md` and `.tasks.json` themselves — two *addenda* survive, the
document they amend does not. This is the confirmed mechanism behind at least
one dropped ask: workers wrote design docs without ever reading the request.

**The whole corpus was untracked in git** — including
`operator-asks-2026-07-16.md`, which declares of itself that when a report and
it disagree about what was asked, *it* wins. The authority record was the least
durable file in the tree. Now committed.

Five dropped asks filed as tickets; one confirmed duplicate cancelled.

## What is fixed vs. what remains

**Fixed and verified this session:** lane-vs-bookkeeping precedence;
`touches` editable; lane widened from body at filing; five blocked tickets
reopened with corrected lanes and zeroed attempts; dispatcher silent-drop;
dropped directive restored; corpus committed.

**Open, highest value first:**

1. **Populate `depends_on`** — the scheduler is already dependency-aware. This
   is data, not code, and it is the prerequisite for any concurrency.
2. **Expose the ready SET** — one line discards it today.
3. **Parallel dispatch** — needs 1 and 2 first, plus the four serializers.
4. **A planning/decomposition pass** — the loop cannot refine a weak spec, and
   a weak spec is the top cause of a failed run.
5. **Decide the dead roles** — retire or wire triager/maker; three agents have
   never run.
6. **Task-level `evidence`** — structure it or delete it.
7. **A priority scale** — define what the numbers mean.

## Status caveat

The structural bugs are fixed, but a ticket has **not** been demonstrated
running to `done` under the corrected rules in this session: the autonomy
budget is saturated (3.185M of its 3.25M share of the rolling 5h window), so
admission correctly refuses. That is designed behavior, not a fault — the
remaining 1.8M is reserved interactive headroom. Verification resumes when the
window ages out.
