# Work placement, chat modes, preference learning, and queue concurrency

Plan, 2026-07-18. Grounded in code as it exists today; every claim carries a
file:line and was read, not assumed.

## The question

Should chats be hard-blocked from tool calls, pushing all work through tickets?

**No.** A hard block is the wrong shape of answer. The right boundary is not
*capability* ("chat may not use tools") but *cost and guarantees* ("does this
work need what the queue provides?"). And the boundary should be a principle the
agent reasons about, not a rule that fires, because the cases that matter most
are exactly the ones a rule gets wrong.

## Principles (the actual policy)

These are written to be reasoned with, not matched against.

1. **Do small things now.** The operator asked *you*, not a queue. A read, an
   answer, a single-file edit while they are watching: just do it.
2. **Hand off when the work needs what chat cannot give it.** The queue's value
   is concrete: the reviewer gate, `touches` enforcement, attempts/lease/retry,
   a receipt, and metering. Multi-file writes and anything needing verification
   you cannot do in one pass belong there. Not because they are "big" — because
   unreviewed multi-file writes are how a tree gets quietly broken.
3. **Offload reading that would crowd out the conversation.** A task needing a
   lot of context read in is a *subagent*, not necessarily a ticket. The
   subagent reads; the answer comes back into chat. This keeps the thread
   usable without turning a question into a work item.
4. **The operator's expressed wish outranks every principle above.** If they
   want the whole book in context because predicting the ending *is* the task,
   that is the task. Offloading it would be the system failing them. When the
   user has said what they want, stop optimizing and do it.
5. **Say what you did and why, in one line.** "Filed this as a ticket — it
   touches four files and wants a review gate." Never hand off silently.

Principle 4 is the one that must survive contact with implementation. Every
mechanism below has an override, and none of them is allowed to refuse.

## Where the reminder lives

Per the operator's instinct: **at the write boundary, not the turn boundary.**
A note on the file-writing tool, surfaced when the agent is about to write, is
the moment the cost/reviewability tradeoff becomes real. A turn-level rule fires
before the agent knows what it is dealing with; a write-time reminder fires when
it does. It reminds and lets the agent decide — it does not block.

## Modes (the escape hatch that makes leniency safe)

A per-thread `mode`, defaulting to the orchestrated system, switchable mid-chat:

| Mode | Behavior |
|---|---|
| `orchestrated` (default) | Today's system: inline for small work, task-proposal cards for large, subagent offload for context-heavy reading. |
| `direct` | Traditional chat. No auto-ticketing, no cards. Explicit ticket requests still honored — "file this" always works. |

Precedent exists: per-thread `model` (`chat_store.py:57-59`, `set_thread_model`
at `:68`) is the exact pattern, and `POST /threads/{id}/rescope`
(`chat.py:344`) shows a thread's behavior being rebound in place without losing
identity. Mode modulates two existing decision points that currently move
together: the tool surface (`effective_allowed_tools`, `chat_context.py:1065`)
and the persona fragments (`persona()`, `chat_context.py:679-708`).

## Precondition nobody expected: general chat cannot do small work

`chat_context.py:1004-1005` — general chat's allowlist is
`mcp__workshop__query` (read-only) plus `WebSearch`/`WebFetch`. **No Bash, Read,
Edit, or Write.** Its persona already tells it to prefer task-proposal cards
(`chat_context.py:679-708`), which reads as an orchestration preference but is
partly just a description of its actual inability.

So "let chat do small work inline" is not a prompt edit. Widening general
chat's tool surface is step one; the persona rewrite is meaningless without it.
Build/project providers already have full tools with cwd pinned
(`chat.py:1039-1057`) and already carry a policy close to the target
(`TASK_PROPOSAL_BUILD_RULE`, `chat_context.py:166-175`).

## Chat usage tracking

The operator asked for this and it is nearly free.

`build_streaming.py:142` and `:155-161` already parse `usage` off every
assistant message and the terminal envelope. `_ctx_tokens_from_usage`
(`:166-179`) reduces it to context occupancy and **deliberately drops
`output_tokens`**; only that one integer survives, via
`set_thread_context_tokens` (`chat_store.py:250`). Cumulative spend, the
cache-read/cache-creation split, and `total_cost_usd` are parsed and discarded.

Nothing is lost at the source: raw stdout is journaled per turn under
`.runtime/turns/<turn_id>/` (`build_streaming.py:209-214`), so past turns are
backfillable.

`usage_ledger.record()` (`usage_ledger.py:95`) is fully general and already
takes role/task/run attribution. **Chats are exempt by policy, not capability**
(`usage_ledger.py:21`).

Plan: record chat turns with `role="chat"`, **reported but not gated**. This
matches the operator's "tracked even if not limited the same way," and it is
also the correct call mechanically: `can_admit`'s reserve fraction
(`usage_ledger.py:399-426`) is calibrated on the assumption that chat spend is
invisible, so metering chat *and* gating on it would double-count the reserve.
Gating, if ever wanted, is a separate decision after real numbers exist.

Existing ticket `da952d26` covers the by-chat Usage *view*. The backend metering
below is its unstated prerequisite.

## Preference learning: what exists, and the four things missing

The learnings system is genuinely well-built and should be extended, not
replaced: scoped JSONL stores (`learnings.py`), two-stage dedup where a repeat
appends provenance rather than dropping the signal (`:340-352`), ranked and
budgeted injection with a retrieval feedback loop (`memory_block`, `:569-633`),
and consolidation via the memory keeper (`memory_keeper.py`).

Preferences from chat are captured today through `LEARNING_NOTE_PROTOCOL`
(`chat_context.py:204-217`) → `apply_learning_note` (`task_scope.py:198-234`) →
`learnings.record_feedback`. Live data confirms it works: all five entries in
`.runtime/learnings/workshop.jsonl` are `src=chat tags=['feedback']`.

Four real gaps:

1. **Chat preferences can never become durable rules.** Promotion counts only
   provenance sources prefixed `review:` (`_BLOCK_ORIGIN`, `learnings.py:388`,
   filtered at `:434`). A preference stated ten times in chat dedups to one
   entry with provenance 10 and remains ineligible forever. `promoted.jsonl`
   does not exist — zero candidates have ever been generated.
2. **Operator preferences land in a scope that reaches almost nothing.** They
   default to `workshop` (`task_scope.py:219-227`), which is neither `global`
   (injected everywhere) nor a project scope — and `chat_context.py:616`
   explicitly skips the scoped block when the scope *is* `workshop`. So
   operator preferences are absent from the global block every agent receives.
   There is no `operator:`/`user:` scope.
3. **No typed preference record.** `get_preference(key, scope)` /
   `set_preference` are specified in
   `docs/platform-redesign/chat-work-linkage-2026-07-18.md:188` and
   `docs/decisions/0006-auto-run-learning-mechanism.md:41` but **do not exist**
   in `learnings.py`. Preferences are free prose sharing one 60-entry/700-token
   bucket (`learnings.py:56-62`) with mechanical worker noise — and the noise
   wins on the ranking formula, which weights provenance (the sentinel-forgotten
   learning already sits at provenance 18).
4. **No contradiction handling.** A reversal ("actually, stop doing X") creates
   a second entry contradicting the first, and both get injected. The memory
   keeper has supersession logic (`memory_keeper.py:61-77`) but it applies only
   to semantic knowledge notes, not to the learnings store.

Capture is also **model-discretionary**: the only path is the model choosing to
emit a `learning-note` fence. There is zero implicit learning from behavior —
corrections, re-asks, rejected proposals, mode switches.

### Design stance

Add a typed preference record beside the prose, with its own budget, an
`operator` scope injected everywhere, explicit supersession, and provenance that
records *which message taught it*.

**Learned preferences must be inspectable and editable.** A mis-learned rule
that silently distorts behavior forever is worse than no learning at all. Every
learned preference gets a "learned this from message X" trail and a UI to view,
correct, and delete. This is a deliberate constraint on the feature, not a
nice-to-have: an opaque preference store that drifts is a system that becomes
untrustworthy exactly when it becomes influential.

Implicit/behavioral capture is **proposal-only**: it may suggest a preference,
never silently install one.

## Queue concurrency and latency

### Why it is serial today

Four independent serializers, stacked:

1. `MAX_PROJECT_SWEEPS = 1`, `MAX_AGENT_SWEEPS = 1`
   (`dispatch_triggers.sh:62-63`, enforced `:293`, `:522`).
2. Three exclusive `flock`s, all keyed on `md5(DIR)` — the runner
   (`queue_runner.sh:64-69`), the sweep (`project_ralph.sh:529-535`), the inner
   ralph (`ralph.sh:142-147`). One tree = one worker, deliberately
   (`project_ralph.sh:523-528`).
3. The sweep loop (`project_ralph.sh:625-980`) is sequential: claim, run
   synchronously (`:798`), finalize, `sleep 1` (`:979`). No fan-out.
4. `next_ready` computes the full ready set and returns `cands[0]`
   (`tasks.py:969-971`).

**The data model is not the constraint.** `depends_on` is a real DAG and the
ready set is materialized at `tasks.py:969` and discarded one line later.
Measured today: **21 of 24 open tickets are ready right now, and zero have
`depends_on` set.** The width is already there; nothing consumes it.

Related defect: dependencies exist in ticket *prose* ("Depends on: B1, B4") but
are never encoded in the `depends_on` field, so the scheduler is blind to them
and can work a dependent ticket before its prerequisite. Fixing the field is a
prerequisite for concurrency being *correct* rather than merely parallel.

### Worktrees: built, correct, and switched off

`allocate_task_worktree` (`worktrees.py:205`) creates an isolated worktree off
`origin/<default_branch>` and `git worktree lock`s it (`:241-243`) so a crashed
agent cannot wedge others; `publish_or_park` (`project_ralph.sh:248-270`)
pushes and opens a PR, parking on conflict rather than looping. This is exactly
the git-index-race answer.

It is disabled everywhere: `WT_ENABLED` requires `repo.remote_url`,
`concurrent_worktrees: true`, and a slug-resolved project
(`project_ralph.sh:494-495`), and **all 9 projects have
`concurrent_worktrees = None` with no `remote_url`**. Even the enabled path is
explicitly sequential (`:713`). `CONCURRENT_WORKTREES_MAX` (`:57`) is declared
and never read.

Note the constraint this creates: worktree mode's success path *publishes a PR*,
so enabling it on the workshop root — which has no remote — needs a local-only
completion path (merge/fast-forward to the base branch) before it is usable
here. That is the real work, not flipping the flag.

### Latency, measured

A trivial ticket takes ~30s end to end (~22s model call, ~4s reviewer). Where
the rest goes:

- **Up to 60s of dead time** waiting for the next poll (`queue_runner.sh:102`).
  Unconditional, and the largest fixed cost for a fast ticket.
- **`reviewer.sh` runs N+1 times per task** — once per iteration plus once more
  inside `eval_criteria` (`project_ralph.sh:411-416`). It makes **zero model
  calls** (it is fully deterministic; the metering block at `:388-432` is dead
  code because nothing sets `REVIEWER_CLAUDE_OUT`), but it costs wall-clock:
  `check_pluggable_apps.sh` runs repo-wide and unconditionally
  (`reviewer.sh:224-230`), and `tsc -b --noEmit` carries a 90s timeout (`:192`).
- **~10-12 cold Python starts** importing the backend before the first model
  call, and 30-50 subprocess launches per task.
- **A cap mismatch:** the sweep breaks when `REMAIN < RALPH_MAX_WALL + 30`
  (= 1230s, `project_ralph.sh:627`) while the runner passes `--max-wall 1500`
  (`queue_runner.sh:96`). Once a task takes >270s the sweep exits regardless of
  `MAX_TASKS=3`. Trivial tasks are unaffected (verified: 3 drained in one 97s
  sweep), but real tickets take minutes, so in practice the queue does ~1 real
  ticket per poll cycle.

### Fixes, cheapest first

| # | Change | Win | Risk |
|---|---|---|---|
| L1 | Wake the runner on file/enqueue instead of waiting out the poll (signal file or short poll) | removes up to 60s dead time per ticket | low |
| L2 | Align the `REMAIN` guard with the caller's wall budget | `MAX_TASKS` becomes reachable; ~3x per sweep on real tickets | low |
| L3 | Gate `check_pluggable_apps.sh` and `tsc` on changed paths | cuts reviewer wall-clock, N+1 times per task | low |
| L4 | Don't re-run the full reviewer inside `eval_criteria` | removes one full gate run per task | medium |
| C1 | `next_ready_batch(n)` — return the ready set, not `cands[0]` | prerequisite for all concurrency | low |
| C2 | Encode prose dependencies into `depends_on` | makes concurrency *correct* | low |
| C3 | Local-only completion path for worktree mode (no remote/PR) | unblocks worktrees on the root tree | medium |
| C4 | Parallel dispatch of N independent ready tickets into worktrees | the actual throughput win | high |

C4 is deliberately last. Parallel execution on one tree without worktree
isolation is how the index race that already bit this session becomes routine.
C1-C3 are the honest prerequisites.

### On the swarm

The operator's framing — not a single long chain but a reasoned concurrent swarm
that coordinates when there is enough work — is the right target, and the ready
set is already 21 wide, so the material exists. The ordering that gets there
safely is C1 → C2 → C3 → C4: expose the width, make the ordering correct,
isolate the trees, then fan out. Fanning out first just produces fast corruption.

## Sequencing

**Now (small, provable, low risk):** chat usage metering (report-only); L1, L2.

**Next:** widen general chat's tool surface + rewrite the persona around the
five principles; write-boundary reminder; per-thread `mode`.

**Then:** `operator` preference scope + typed preference record + supersession;
extend promotion beyond `review:` origin; the preference-review UI.

**Then:** C1 → C2 → C3 → C4, in that order.

**Deferred pending a decision:** whether chat spend ever gates admission
(needs real numbers first); whether implicit behavioral capture graduates from
proposal-only.

## Open questions for the operator

1. Should `direct` mode still capture preferences, or be fully inert?
2. Subagent offload for large reads: silent, or always announced in-thread?
3. Worktree publishing on the root tree: local merge to the base branch, or
   stay sequential there and reserve worktrees for projects with real remotes?
