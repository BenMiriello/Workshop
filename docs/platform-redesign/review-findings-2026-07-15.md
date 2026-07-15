# Coherent-rebuild implementation review (2026-07-15)

Reviewer: fresh-context adversarial pass over Stages 3-8 as shipped on `main`,
against the design in `coherent-rebuild-2026-07-14.md`. Letta/Ollama code excluded
(Stage 9 in flight). Every finding below was verified against the code at the
cited lines; items marked SUSPICION were not reproduced.

## Executive summary (highest impact first)

1. **[F1, critical]** `ralph.sh` writes iteration output to a SHARED file
   (`$WORKSHOP/.runtime/ralph-iter-N.out`) regardless of `--dir`; the dispatcher
   launches concurrent sweeps, so runs read each other's output: foreign
   sentinel = false task completion, cross-fired classifier blocks, corrupted
   token billing.
2. **[F2, high]** The classifier-block detector is `grep -qiE 'classifier|safety-check|...'`
   over the model's output; any task that mentions "classifier" (this workspace
   runs a BirdNET classifier) hard-blocks the task.
3. **[F3, high]** Learnings scope keys mismatch: sweeps bill/record with a bare
   slug (`demo-x`) but the learnings store requires `project:<slug>`, so
   per-project injection is silently empty and every reviewer rejection falls
   into the GLOBAL guardrails, permanently polluting every chat prompt with
   lines like "Ralph rejected: no_diff".
4. **[F4, high]** Environment failures burn task attempts, the exact thing design
   2d forbids: an API outage makes claude exit nonzero, `release_or_block` counts
   it, and 3 outage ticks block/park perfectly good tasks.
5. **[F5, high]** Dedup can silently drop real work: the merge target set includes
   `parked` (dead-letter) tasks, so a fresh legitimate request merges into a
   poison-pill task that `next_ready` never picks, while telling the operator
   "merged into existing task".
6. **[F6, high]** The "cache-stable" ambient digest is the most volatile block in
   the prompt prefix: it sorts by `updated` and includes live job statuses, so
   every sweep/task mutation reshuffles bytes early in the prefix and busts the
   prompt cache for everything after it (learnings claim stability; this block
   defeats it). Its job lines also render raw job ids because it reads
   `title`/`summary`/`kind` fields no writer ever sets.
7. **[F7, high]** `liveness_watchdog.sh` is not in the crontab and
   `jobs.reconcile_stuck` has no other caller: a crashed sweep leaves its
   coordinator Job `running` forever and the dispatcher's `running_job()` check
   then wedges that scope permanently. (Known-pending in the handoff, but the
   consequence is a permanent wedge, not just a stale record.)
8. **[F8, medium]** Budget admission cannot see in-flight spend (billing lands
   only at ralph exit, up to 20 min later) and `timeout`-killed iterations bill
   zero, so the worst runs (thrash, runaway) are exactly the ones the ledger
   never sees.
9. **[F9, medium]** Dedup TOCTOU: `decide()` + `add()` are not under one lock, so
   two chats filing the same task concurrently both create it, the exact scenario
   the module exists to prevent. (One-line fix exists: `add()` already has an
   idempotent `provenance` path.)
10. **[F10, medium]** The weighted max-min allocator (`plan_budgets`) gates
    nothing: enforcement is only `app_total` + optional per-project `ceil`.
    Floors and weights are display-only, diverging from design 2c ("replace the
    flat MAX_*_SWEEPS caps").

---

## Findings (ranked)

### F1. Shared iteration-output file across concurrent ralph runs — CRITICAL, confirmed

`scripts/ralph.sh:230`:

```bash
ITER_OUT=$WORKSHOP/.runtime/ralph-iter-${iter}.out
```

The log is per-tree (`$DIR/.runtime/ralph.log`, line 60) but ITER_OUT is always
the workshop root, even for `--dir <project>` runs. Concurrency is by design:
`dispatch_triggers.sh` launches up to 1 project + 1 agent sweep per tick
(detached, lines 304-314), plus manual "Run now" sweeps and root runs. Two
concurrent runs at the same iteration number clobber each other's file.

Failure scenario: run A (project) and run B (root) both at iter 1. B's claude
finishes and writes a sentinel after A's claude exits but before A greps
(ralph.sh:293). A sees B's sentinel; A's own reviewer then inspects A's tree,
which has A's partial diff, and if typechecks pass (`complete:true` needs only
non-empty diff + no new stubs + typecheck, reviewer.sh:189-217), A's task is
marked done half-finished. Independently: A's token parse (ralph.sh:249) reads
B's usage (cross-billed scopes), and A's classifier grep (line 285) fires on B's
output.

**Fix (S):** `ITER_OUT="$DIR/.runtime/ralph-iter-$$-${iter}.out"` (per-tree AND
per-pid; two sequential tasks in one sweep share a pid but never overlap).

### F2. Classifier-block detection by keyword grep — HIGH, confirmed mechanism

`scripts/ralph.sh:285`:

```bash
if grep -qiE 'classifier|safety-check|3 consecutive actions were blocked' "$ITER_OUT"; then
```

This greps the model's final text. Any task whose legitimate output mentions
"classifier" ("improved the BirdNET classifier threshold", "the call-type
classifier"), or a model paraphrasing loop-rule 6 (which itself says "classifier
block", ralph.sh:130-131), exits 3 and the task is hard-blocked
(`project_ralph.sh:306`, no retry, attempts burned). This workspace has an
entire birds-classifier subsystem, so the false-positive rate on those scopes is
near-certain.

**Fix (S):** detect blocks structurally: parse the result JSON (`is_error`,
`subtype`) and/or match only the exact multi-word block phrases
(`"3 consecutive actions were blocked"`, `"Safety-Check Bypass"`), never the
bare words `classifier`/`safety-check`.

### F3. Learnings scope-key mismatch + global pollution — HIGH, confirmed

- `project_ralph.sh:194` sets `BILL_SCOPE="${SLUG:-$ASSIGNEE}"` (bare slug for a
  project) and passes it as `--bill-project`.
- `ralph.sh:157` sets `LEARN_SCOPE="$BILL_PROJECT"` and uses it both to inject
  (`learn_block "$LEARN_SCOPE"`, line 165) and to record rejections (lines
  331-347).
- `learnings._valid_scope` (learnings.py:86-94) delegates to
  `knowledge._parse_scope` (knowledge.py:102-110), which REJECTS bare slugs
  (requires `project:<slug>` / `agent:<slug>` / `app:<name>` / `workshop`).

Result: for every project sweep, the scope injection is silently `""`
(memory_block catches the ValueError, learnings.py:314) and the recording path
falls back to GLOBAL (ralph.sh:336-339). So per-project learning never
accumulates, and transient reviewer reasons ("Ralph rejected: no_diff",
"Ralph rejected: new_stubs:3") become permanent workspace-wide guardrails
injected into EVERY chat via `learnings_context` (chat_context.py:476-480) and
every future sweep. Note `agent:<slug>` scopes happen to work (assignee already
carries the prefix); only project scopes are broken, which is the main path.

**Fix (S):** in `project_ralph.sh`, pass a separate `--learn-scope
"project:$SLUG"` (or map in ralph.sh: bare slug -> `project:<slug>` when it is
not already prefixed). Separately (M): stop recording raw reviewer reason codes
as learnings at all; they are per-run diagnostics, not lessons (see F15).

### F4. Environment failures burn task attempts — HIGH, confirmed design deviation

Design 2d (plan lines 177-181): circuit breaker must separate env-failure from
task-failure and "do NOT burn task attempts (the task did not fail, the
environment did)". Implementation: no such distinction exists anywhere.

Scenario: Anthropic API outage during a sweep. Each ralph iteration's `claude`
exits nonzero, no sentinel, reviewer says `no_diff` -> ralph exits 1 ->
`release_or_block` (project_ralph.sh:205-220) counts the attempt (claim bumped
it, tasks.py:826) -> after cap 3 the task is blocked. One 20-minute outage
window can poison every ready task the dispatcher touches, then requires manual
unblocking. This also violates the operator's standing "handle API outages
actively" rule. The circuit breaker (dispatch_triggers.sh:106-146) fires on
tasks_failed>0 but that is task-level failure counting, not env detection, and
timeouts do not even count as failed (F14).

**Fix (M):** classify ralph exits: when `claude` itself failed on iteration 1
with no output/JSON (vs producing work that the reviewer rejected), exit with a
distinct code (e.g. 5 = env). In `project_ralph.sh`, on env exit release the
task WITHOUT counting the attempt (needs a small `tasks.update
--decrement-attempt` or a claim variant) and finish the sweep `failed` so the
existing circuit breaker cools the scope off.

### F5. Dedup merges into parked/dead-letter tasks — HIGH, confirmed

`task_dedup._open_tasks` (task_dedup.py:162-165) excludes only TERMINAL
(`done`/`cancelled`). `parked` and `blocked` tasks are merge candidates. A new,
legitimate request that matches a parked poison-pill task at cosine >= 0.90 (or
exact hash) is folded into it as evidence (task_dedup.py:234-251); `next_ready`
never picks parked (tasks.py:646), so the work silently never happens while the
chat reports "merged into existing task".

**Fix (S):** exclude `parked` from merge/exact targets (still allow "related"
links), or on merge into a parked task resurrect it to `todo` with attempts
reset (a fresh operator request is a legitimate un-park signal).

### F6. Ambient digest is not cache-stable and renders junk job lines — HIGH (cost/quality), confirmed

Two parts:

a) **Cache instability.** `_build_prompt` places the ambient digest early in the
prefix (chat.py:388-398), before the knowledge index, custom instructions,
summary, and the whole conversation. But the digest re-sorts open tasks by
`(priority, updated)` desc (workspace_digest.py:65) and lists running/recent
jobs (lines 84-104). Under active autonomy, every sweep tick mutates both, so
the block's bytes change between turns and invalidate the prompt cache for
everything after it, every turn. The stated goal ("cache-stable prefix",
chat.py:391, workspace_digest.py:2) is inverted: the learnings block's careful
oldest-first append design (learnings.py:308-320) is defeated by the block
right after it.

b) **Dead fields.** `_job_lines` renders
`r.get("title") or r.get("summary") or r.get("kind") or r.get("id")`
(workspace_digest.py:101), but no writer ever sets title/summary/kind
(jobs.start signature, jobs.py:85-121; design 2a's title/summary was never
implemented in the writers). The digest therefore shows raw
`job_1763..._9f2c (running)` lines: token spend with zero information.

**Fix (M):** (a) make the block stable-ordered and low-churn: sort by `created`
asc, drop the `(status)` tag for tasks, and either move jobs OUT of the
cache-stable prefix (append near the tail, after the summary) or quantize the
job list (only show jobs older than N minutes, statuses collapsed). (b) have
`project_ralph.sh` pass a short title (`--budget` style extra field or a new
`--title` arg: first 60 chars of scope + trigger) and render
`slug + trigger + status` instead of the id.

### F7. Watchdog not installed; stuck `running` Job wedges a scope permanently — HIGH, confirmed operational gap

`crontab -l` shows only `cron_agent.sh` (*/20) and `frontends_watchdog.sh`.
`jobs.reconcile_stuck` (jobs.py:257) is called ONLY by
`scripts/liveness_watchdog.sh`, which its own header says is "NOT installed"
(also handoff line 65). The dispatcher skips any scope with a `running` Job
(dispatch_triggers.sh:217-218, 280-281). A sweep killed by reboot/OOM leaves its
coordinator Job `running` with a dead pid forever, so the scope is never
dispatched again. `tasks.reclaim` recovers the TASKS (it runs inside
`next_ready`), but nothing else ever closes the Job.

**Fix (S):** call `jobs.reconcile_stuck()` at the top of the dispatcher's
decision engine (it is cheap and idempotent), AND install the watchdog cron.
Belt and suspenders, and the dispatcher stops depending on an external cron
being remembered.

### F8. Admission blind to in-flight spend; timeout kills are unmetered — MEDIUM, confirmed

- Billing happens once, at ralph exit (ralph.sh:358-362). A task run lasts up to
  20 min. `can_admit` (usage_ledger.py:274-295) reserves exactly ONE
  `nominal_task_tokens` (150k) of headroom, but with a project sweep + an agent
  sweep + a manual run in flight, actual overshoot is (in-flight runs) x (real
  cost, ~250k each) beyond the cap.
- Iterations killed by `timeout` (ralph.sh:238) produce no result JSON, so
  `ITER_TOKENS=0`: the most expensive failure mode (a run that burns its full
  wall budget) bills nothing, repeatedly, and admission never trips on it.

**Fix (M):** record a provisional entry at claim time (nominal cost, job_id
tagged) and replace it with the real total at exit; count `running` Jobs x
nominal in `can_admit`'s reserve. For the timeout case, bill the nominal task
cost when the JSON is missing but the claude process ran for >60s.

### F9. Dedup TOCTOU on concurrent creation — MEDIUM, confirmed

`dedup_add` (task_dedup.py:229-284) runs `decide()` (reads open tasks) and then
`tasks.add()` as two separate locked operations. Two chats spawning the same
task within the window both decide "distinct" and both create. This is
precisely the double-file scenario Stage 7 targets.

**Fix (S):** pass `provenance=canonical_hash(text)` into `tasks.add()`;
`add()` already short-circuits on an existing provenance under the store lock
(tasks.py:694-697). That makes the exact-dup path atomic with zero new
machinery. (The cosine path stays advisory; that is acceptable.)

### F10. Weighted max-min allocator is display-only — MEDIUM, confirmed deviation

`plan_budgets` (usage_ledger.py:174-239) is called only from `status()`
(usage_ledger.py:250). Enforcement (`can_admit`) checks only the app-wide total
and a per-project `ceil`. Floors and weights gate nothing; the flat
`MAX_PROJECT_SWEEPS=1` / `MAX_AGENT_SWEEPS=1` caps the design said to replace
(plan 2c, line 156-158) are still the real scheduler. Also, `status()`
allowances ignore actual usage in the fill, so the per-project "remaining"
values can sum to more than `app_remaining` (confusing UI, not harmful).

**Fix (M):** either make `can_admit(scope)` deny when
`used[scope] >= plan_budgets(...)[scope]` (5 lines: compute demanders = scopes
with ready work), or delete floors/weights from the config and docs and keep
the honest model "global cap + per-scope ceil". Half-implemented is the worst
state: the operator will tune weights that do nothing.

### F11. Lexical-cosine thresholds unsound at both ends — MEDIUM-HIGH, confirmed by construction

The 0.90/0.80 thresholds were designed for a semantic embedder (plan 2e) but
shipped on a hashing TF vectorizer (task_dedup.py:83-96), where cosine is just
normalized token overlap:

- **False merge, long texts:** cos = (n-1)/n for one differing token out of n;
  at >=10 content tokens a single-word difference crosses 0.90 (verified: 9
  tokens -> 0.875, 10 -> 0.90). `spawn_task` texts are Haiku-distilled full
  paragraphs (chat.py:223-232, up to 2000 chars) full of shared boilerplate
  ("backend", "endpoint", "acceptance"), so two distinct tasks about sibling
  features will routinely merge.
- **False exact-merge, word order:** `canonical()` sorts tokens
  (task_dedup.py:74), so "move the header above the footer" and "move the
  footer above the header" hash IDENTICALLY (verified). Order-sensitive tasks
  (rename A->B vs B->A, X before Y vs Y before X) are silently deduped as
  "exact" with cosine 1.0 and no new task created.

**Fix (M):** (1) raise MERGE_AT to ~0.95 for the lexical backend and make
"merge" additionally require the shorter text's token set to be a subset (or
>=95% contained) in the longer's; (2) hash on the ORDERED token sequence and
keep the sorted form only as a second "probable dup -> related" signal;
(3) compare on the first line/clipped 200 chars for spawn-task paragraphs so
boilerplate does not dominate. Alternatively wire WORKSHOP_TEXT_EMBED_URL to a
real embedder and keep 0.90 there only.

### F12. `claude --continue` can hijack a foreign session — MEDIUM, confirmed mechanism

ralph.sh iterations >=2 use `--continue` (ralph.sh:226, 238), which resumes the
MOST RECENT session for the cwd. Builder chats run claude in the same project
cwd. If a chat turn (or another tool) creates a session in `$DIR` between ralph
iterations, iteration 2 injects the reviewer-rejection prompt into the chat's
session: ralph loses its own context AND the operator's chat gets a foreign
"the reviewer rejected..." turn.

**Fix (M):** capture `session_id` from iteration 1's result JSON (it is in the
`--output-format json` envelope) and use `--resume <session_id>` instead of
`--continue`.

### F13. `max_iterations` means three different things — MEDIUM, confirmed

- Documented: "per-task override of the ralph cap" (tasks.py:19), i.e. inner
  loop iterations.
- Used by `project_ralph.sh:254-255` as the per-task ATTEMPT cap across sweeps
  (and never passed to `ralph.sh --max-iter` at all, so the inner cap is always
  10).
- Used by `tasks.reclaim` (tasks.py:613) as the attempt cap too.

An operator setting `max_iterations: 10` meaning "let ralph iterate 10 times"
actually grants 10 sweep attempts, roughly 10 x 20 min x ~250k tokens of
budget.

**Fix (S):** rename the store field to `max_attempts` (keep `max_iterations`
read-compat in `_normalize`) and have project_ralph pass a separate, fixed
inner `--max-iter`; update the docstring.

### F14. Incomplete/timeout child Jobs finished as `done`; timeout-thrash invisible to the breaker — MEDIUM, confirmed

`project_ralph.sh:307-309`: rc=1 (iteration cap) and rc=2 (wall cap) finish the
child Job with `status=done`, and the coordinator counts them in `TIMEOUT`, not
`FAILED`. Consequences: the Jobs feed reports failed work as done, and
`_failed_sweep` (dispatch_triggers.sh:106-110, needs tasks_failed>0) never sees
a pure-timeout thrash loop, so the circuit breaker cannot open on the classic
convergence failure (a task that always times out re-dispatches every tick
until the attempt cap parks it, unbilled per F8).

**Fix (S):** finish rc=1/2 child jobs as `failed` with the rc, and count them
in `tasks_failed` on the coordinator finish.

### F15. Reviewer-rejection learnings are per-run diagnostics stored as durable lessons — MEDIUM

`ralph.sh:330-347` records `"Ralph rejected: <reason>"` for every rejection.
Reasons are codes like `no_diff`, `new_stubs:3`, `py_typecheck_fail`: transient
facts about one run, not patterns. Variants (`new_stubs:2` vs `new_stubs:5`)
defeat both the exact hash and Jaccard 0.85, so they accumulate as separate
entries toward the 60-entry `memory_block` cap (learnings.py:55) and are then
injected into every prompt (F3 makes this global). This is context rot by
design.

**Fix (S):** stop recording raw reason codes; record only on a REPEATED
rejection of the same reason for the same task (2+ occurrences), with task
context in the text, into the scope (not global) store.

### F16. Cron trigger firings consumed even when the sweep is skipped — MEDIUM, confirmed

`dispatch_triggers.sh:252-256` writes `state[key] = now` the moment a cron
trigger is due, BEFORE the runtime/root/running/circuit/ready/budget gates
(lines 261-291), and `save_state` persists it unconditionally (line 295). A
scheduled run that is skipped for a transient reason (budget window, running
job) is lost until the next cron occurrence, silently.

**Fix (S):** only update `state[key]` for triggers that made it into `plan`.

### F17. Agent sweeps share one lock; agent+project sweeps on the same tree collide destructively — MEDIUM, confirmed

`project_ralph.sh:177`: `LOCK=/tmp/workshop-project-ralph-$SLUG.lock`. For every
`--dir` (agent) sweep SLUG is empty, so ALL agent sweeps serialize on
`workshop-project-ralph-.lock` (over-serialization, benign). Worse: an agent
sweep whose scope resolves to a project dir (builder linked to a project,
dispatch_triggers.sh:270-276) and that project's own sweep use DIFFERENT locks,
so both run against the same tree. The inner ralph per-DIR lock then makes the
loser exit 4 (ralph.sh:86-89), which project_ralph treats as a task failure
(`*)` branch, line 310-312): attempts burn, and 3 collisions block the task.

**Fix (S):** derive the lock from the resolved DIR
(`md5sum <<< "$DIR"`), not the slug, so any two sweeps on the same tree
serialize at the sweep level (skip, no claim, no attempt burned).

### F18. `no_diff` gate makes no-code tasks impossible — MEDIUM, confirmed

`reviewer.sh:202-205` fails any run with an empty diff. A task like
"investigate why X and write findings to the task evidence" or "restart the
birds analyzer" produces no tracked-file diff, can never pass, thrashes to the
attempt cap, and gets blocked. Conversely, uncommitted leftovers from task N
satisfy the non-empty-diff requirement for task N+1 in the same sweep
(baseline is HEAD at claim, project_ralph.sh:275), weakening the gate in the
other direction.

**Fix (M):** allow `acceptance.kind=command` tasks to pass reviewer with
`no_diff` when the acceptance command is the real gate; and/or teach ralph's
prompt that no-diff tasks must self-mark `in_review` with evidence.

### F19. `next_ready` and `claim` disagree on unassigned tasks — LOW-MEDIUM, confirmed inconsistency

`next_ready.in_scope` (tasks.py:654-658) treats `assignee is None` as claimable
by any agent, but `claim` -> `_match_assignee` (tasks.py:520-525) maps None ->
`operator`, which does NOT match `agent:<slug>`. An agent sweep then loops:
next_ready returns the task, claim refuses, "already claimed elsewhere", and
the loop re-picks the same task up to MAX_TASKS times, opening and cancelling a
child Job each pass (project_ralph.sh:259-270). Bounded, but 20 junk Jobs per
sweep. `assignee=None` is rare (only an explicit empty-string assignee
normalizes to it) which is why this has not bitten yet.

**Fix (S):** make `_match_assignee` accept a None-assignee task for any agent
scope (mirror in_scope), or drop the None-is-claimable clause from in_scope.

### F20. Smaller items — LOW

- **L1** `usage_ledger.prune()` has no caller (grep: only the CLI shim), so
  `usage.jsonl` grows forever; window filtering keeps it correct. If it is ever
  wired to cron, note it is read-then-replace with NO lock and will drop
  concurrent `record()` appends (usage_ledger.py:132-148). Wire it into
  `cron_agent.sh` under the store's own flock or an `flock` wrapper.
- **L2** `jobs.jsonl` is unbounded and fully read on every chat turn (ambient
  digest), every dispatch tick, and every watchdog pass (jobs.py:170-182).
  Fine now; add a size-based compaction (rewrite collapsed terminal records
  older than 7 days) before it reaches tens of MB.
- **L3** Token metering counts `cache_read_input_tokens` at parity
  (ralph.sh:273-274). Subscription rate limits weight cache reads far below
  fresh input, so the 2M budget trips much earlier than the real quota.
  Conservative direction (safe), but the operator will wonder why autonomy
  stops with real quota left. Weight cache reads at ~0.1 if tuning matters.
- **L4** App chats get the WORKSPACE-wide digest (workspace_digest.py:107-116
  returns None scope for app chats -> roll-up), while the docstrings say only
  general chats do. Single-operator so not a leak, but a finance app chat now
  carries unrelated project task text every turn: token cost with no benefit.
  Return "" for app chats instead.
- **L5** `learnings` store writes are unlocked; the dup path rewrites the whole
  file via `_write_all` (learnings.py:266) and can clobber a concurrent
  `_append_line` from another sweep. Tiny window, worst case one lost entry.
  Reuse the tasks-style flock if it ever matters.
- **L6** `reconcile` matches tasks by normalized FIRST LINE only
  (tasks.py:338-343, 413-415); two tasks sharing a first line (easy after a
  "related" dedup) route a hand-edited checkbox flip to whichever came first.
  SUSPICION at current corpus size; will bite as stores grow.
- **L7** `_distill_task_from_thread` prompt says "No preamble... no em-dashes"
  (chat.py:227-228) but nothing strips one if Haiku emits it anyway; operator
  hard rule. Add a `.replace("—", ", ")` on the result. Trivial.

---

## What is already sound (do not churn)

- **tasks.py locking + atomicity.** Single flock per store, atomic tmp+replace
  writes for both .tasks.json and TODO.md, claim/update/add/remove all under the
  lock, reclaim does a lock-free prescan then re-verifies under the lock
  (tasks.py:587-631). The claim fence (`expect_claim_token`) is threaded through
  every project_ralph finalize path, and `_opt_int` correctly turns an empty
  CLI token into "no fence" rather than 0 (project_tasks.py:62-69).
- **Lease semantics.** "Any transition out of doing releases the claim"
  (tasks.py:772-774) is implemented exactly as design 2d demands, and
  `_is_stale_claim` requires lease-expired AND pid-dead, so a slow-but-alive
  sweep is never robbed.
- **Jobs append-only collapse.** `_clean()` dropping Nones so a finish line
  never nulls an open line's fields (jobs.py:75-78) is a nice touch; concurrent
  shell+backend writers are safe by construction.
- **Circuit breaker state derivation.** Recomputing the failure run from
  jobs.jsonl each tick (state file is display-only) means it can never drift or
  wedge closed by itself; cooldown is capped at 2h. (Its blind spots are F4/F14,
  not its own logic.)
- **Best-effort context injection.** Every injected block (learnings, digest,
  knowledge, skills) is individually try/excepted so a broken store can never
  take down the chat path (chat.py:377-423).
- **Dedup fallback ladder.** hash -> external embedder -> local lexical ->
  hash-only, never raises into the chat/creation path (task_dedup.py:128-146),
  and dedup is wired into BOTH creation paths (spawn-task chat.py:275 and
  task-patch task_scope.py:119).
- **Dispatcher discipline.** AUTONOMY_DISABLED honored at dispatcher and sweep
  level, sweeps detached with setsid, per-scope flock, and the
  awaiting_unchanged skip correctly runs BEFORE has_ready so a store touch
  reopens the scope.

## Prioritized fix list

| # | Fix | Effort |
|---|-----|--------|
| 1 | F1: per-tree+pid ITER_OUT path | S |
| 2 | F2: structural classifier-block detection (JSON is_error / exact phrases) | S |
| 3 | F3: `project:<slug>` learn scope from project_ralph | S |
| 4 | F5: exclude parked from dedup merge targets (or un-park on merge) | S |
| 5 | F7: call jobs.reconcile_stuck() in dispatcher + install watchdog cron | S |
| 6 | F9: provenance=canonical_hash in dedup_add -> atomic exact-dedup | S |
| 7 | F14: rc=1/2 child jobs finish failed + count in tasks_failed | S |
| 8 | F16: consume cron trigger state only for dispatched plans | S |
| 9 | F17: sweep lock keyed on resolved DIR, not slug | S |
| 10 | F15: stop recording raw reviewer codes as learnings | S |
| 11 | F13: rename max_iterations->max_attempts, pass inner cap explicitly | S |
| 12 | F19: align _match_assignee with next_ready on None assignee | S |
| 13 | F4: env-failure exit class in ralph; no attempt burn; breaker feeds on it | M |
| 14 | F6: stable-order digest, jobs out of the stable prefix, real job titles | M |
| 15 | F11: merge threshold 0.95 + containment; ordered-token hash | M |
| 16 | F8: provisional billing at claim + nominal billing on timeout kills | M |
| 17 | F12: --resume <session_id> instead of --continue | M |
| 18 | F10: enforce plan_budgets in can_admit, or delete floors/weights | M |
| 19 | F18: no_diff exemption for command/operator acceptance tasks | M |
| 20 | L1-L7 as convenient | S each |

Suggested batching: fixes 1-5 are the "before autonomy is re-enabled" set (the
handoff's step 5 should not happen before F1/F2/F4/F7 at minimum, since all
four only manifest under live concurrent autonomy).
