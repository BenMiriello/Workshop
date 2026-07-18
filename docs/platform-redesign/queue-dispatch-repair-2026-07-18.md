# Queue dispatch repair — why filed tickets never ran (2026-07-18)

Symptom as reported: tickets get filed, nothing executes them; ten minutes after
"File and run", no job had run. The Usage page showed no usage and an autonomy
label that looked wrong.

Every claim below was verified against a live run in the session that wrote this.

## What was actually broken

Three independent faults, stacked. Any one of them alone hides the others, which
is why this read as "the queue is dead".

### 1. The worker never committed, so the completion gate discarded every run

**This was the real one.** `project_ralph.sh` has a committed-work done-gate
(labelled "Fix 1" in the source): a task may only reach `done` if the run
produced a real commit between the pre-run baseline and `HEAD`. The intent is
right — don't mark work done that didn't land.

But `scripts/ralph.sh`, which builds the worker's prompt, **never told the worker
to commit**. Zero mentions of the word. So the loop ran like this, every time:

1. worker does the task correctly, writes the files
2. `reviewer.sh` approves; the task's acceptance criteria pass
3. gate reads the git log, sees `head == baseline`, `diff_files: 0`
4. task is released back to `todo` — "acceptance passed but no work was committed"
5. after 3 attempts the ticket blocks

Proof: a smoke ticket asking for one file with one line produced
`docs/decisions/queue-dispatch-smoke.md` with exactly the right content, on disk,
untracked (`??`), while its Job was recorded `failed` with `diff_files: 0` and
the task reverted to `todo`. Good work, thrown away, every single run. The
operator's 07:50 run failed identically.

**Fixed** in `scripts/ralph.sh`:

- Prompt rule 5 now requires the worker to commit its files before emitting the
  completion sentinel, and explains *why* (the gate reads the git log, not the
  working tree). Staging is scoped to files the task owns — never `git add -A`
  over a dirty tree.
- `maybe_autocommit()` is a deterministic backstop: once the reviewer has
  approved, if nothing was committed, the loop stages exactly the paths this run
  newly dirtied (diffed against a pre-run snapshot, bookkeeping paths excluded
  with the same ignore pattern the receipt builder uses) and commits them. A
  forgotten commit costs a commit message, not the whole run.

In the verification run the backstop never fired — the prompt rule alone was
enough, and workers committed on their own. It is a backstop, not load-bearing.

**The backstop bit me once, and the fix is part of this change.** Its first
version staged every path that was newly dirty relative to the run's pre-run
snapshot. But the tree is shared: while a sweep was working an unrelated ticket,
this session's own in-progress edits to `backend/routes/autonomy.py` were newly
dirty too, so the backstop committed 128 lines of someone else's work under the
worker's name with an unrelated message (`0e25713`, since un-committed and
re-landed correctly). "Newly dirty" is not evidence that *this run* wrote a file.

The backstop now only commits inside a lane the ticket declared (`touches`), and
**declines entirely** when a ticket declares no lane, logging why. Guessing is
worse than releasing the task: the prompt rule is the real mechanism, and a net
that mis-attributes work is worse than no net. This is the concurrent-writer git
race this repo has hit before, in a new place.

### 2. Nothing ever dispatched the root scope

`dispatch_triggers.sh` only plans a project sweep for a project that has a `loop`
trigger and a non-`off` mode (or an explicit preset). In
`.runtime/projects.json`, `workshop` has `mode: manual` and **no triggers at
all**, so the loop-trigger gate skipped it on every tick.

`.runtime/dispatch.log` confirms it: `dispatched 0 sweep(s)` on every 20-minute
tick for the entire log, with one exception (a memory-consolidation pass). The
dispatcher has never launched a project sweep. Every sweep in `jobs.jsonl` came
from a manual "Run now".

So the 20-minute cron was alive and healthy and correctly doing nothing. There
was no resident worker either — which is why a filed ticket had nothing at all
that would pick it up.

**Fixed** by adding a resident worker loop (below) rather than by flipping
`workshop` to `continuous`. Turning the root repo's background autonomy on is an
operator policy decision, not a repair, so the preset is left as the operator set
it; the runner is an explicit, visible control instead.

### 3. "File and run" filed without running

The chat card handler `addTasksAndRun` deliberately skips the run when the
dedup step returns `merge` or `exact` ("Merged into an existing ticket; did not
start a new run"). At 07:08 the operator's filing folded into existing ticket
`8661a6a1` — the journal shows `POST /tasks` at 07:08:51 and **no** `/run` call
at all. The ticket was real, still `todo`, and nothing was dispatched.

Not changed in this pass — it is a deliberate guard against a surprise re-run.
Noted here because it explains the "ten minutes and no job" observation exactly,
and because with a resident runner the merged ticket now gets worked anyway.

### 4. The Usage page was telling the truth; the autonomy label was not

`usage_ledger.status()` returned `app_used: 0` because the rolling 5h window was
genuinely empty — the last autonomous run was ~7h earlier. Usage logging works
and always did; entries carry `task_id`/`run`/`role`. The zero was a *symptom* of
faults 1+2, not a bug in Usage.

The label was the dishonest part. `AutonomyControls` rendered
`"enabled (Ralph fires every 20m)"` with a `running` status whenever the
`AUTONOMY_DISABLED` flag file was *absent*. No worker had to exist for it to say
"enabled" — and none did. **Fixed**: the flag ("work is permitted") and process
residency ("a worker exists") are now reported as two separate facts, with a
third state for the case that was silently lying — permitted, nothing running,
tickets piling up.

## What was added

| Path | What it is |
|---|---|
| `scripts/queue_runner.sh` | Resident loop. Bounded `project_ralph.sh` sweep per poll, one runner per tree (flock), honors `AUTONOMY_DISABLED` + pause guard every iteration. Idle polls cost a process spawn, not a model call. |
| `scripts/queue_runner_watchdog.sh` | Restart half. Idempotent "ensure running" — starts a runner only if the tree's lock is free. Cron `*/5`. |
| `backend/routes/autonomy.py` | `GET /queue/runner`, `POST /queue/runner/start`, `POST /queue/runner/stop`; `queue_runner` block added to `/agents/autonomy/status`. Residency is probed via the runner's flock, never inferred from a flag. |
| `frontend/src/components/QueueRunner.tsx` | The visible control (ticket B0): Running / Paused / Stopped + Start/Stop, polled every 5s. Rendered above the tabs on the Work page. |

The control went on the **existing Work surface**, not a new page — Work is
already the home of Tickets/Runs/Review/Autonomy, and a second queue page would
be exactly the nav drift the design system forbids.

## Verification (all in-session, all real runs)

- **Repeatable drain.** Three no-op tickets filed straight into `.tasks.json` at
  priorities 130/120/110 drained in one sweep, in strict priority order, first
  attempt each: `done=3 blocked=0 failed=0`. Three commits landed
  (`590681b`, `e7f0220`, `f82129f`) and all three files had the exact expected
  content.
- **Durability.** Killed the runner (pid 4147127); trap fired, pidfile cleared.
  `queue_runner_watchdog.sh` restarted it (pid 4148888), and the new runner
  **reclaimed the interrupted ticket** at attempt 2/3 via the existing lease
  path. Running the watchdog twice more left exactly one runner (flock holds).
- **Usage.** `app_used` went 0 → 105294 across the test runs, attributed
  per-`task_id`/`run`/`role`, `projects.workshop.used` populated.
- **Control.** `queue_runner_start` → `enabled: true`; `queue_runner_stop` →
  `running: false, pid: null`. `GET /queue/runner` returns 401 unauthenticated
  (route registered and operator-gated).
- **Gates.** `check_design.sh` → `design: clean`. `npx tsc --noEmit` → exit 0.
  `frontend/dist` rebuilt; the hash served by :8002 (`index-BvL_IkN0.js`)
  matches the new build.

## Running it

The loop is started and supervised by cron; nothing needs a manual start on a
healthy host. To start it by hand:

```bash
setsid nohup bash ~/workshop/scripts/queue_runner.sh >/dev/null 2>&1 &
```

Ensure-running (safe to run any time, what cron calls every 5 min):

```bash
bash ~/workshop/scripts/queue_runner_watchdog.sh
```

Options: `--dir PATH` (default the workshop root), `--poll SEC` (default 60),
`--max-tasks N` per sweep (default 3), `--once`.

Stop it from the UI (Work → Stop queue) or by hand:

```bash
touch ~/workshop/.runtime/AUTONOMY_DISABLED   # runner stays up, idles
```

Deliberately **not** a systemd unit: per repo learnings the backend's `--user`
service is the exception here, and autonomous workers are nohup + cron-watchdog
so a wedged runner is recovered without touching systemctl.

Logs: `.runtime/queue-runner.log` (runner + sweep output),
`.runtime/dispatch.log` (the 20-min dispatcher), `.runtime/jobs.jsonl` (the Job
graph), `.runtime/usage.jsonl` (token spend).

## Still open

- `workshop` remains `mode: manual` with no `loop` trigger, so the 20-minute
  dispatcher still plans nothing for it. The resident runner is what drains the
  root queue. Flipping the preset to `continuous` would additionally arm the
  dispatcher path — an operator call, not made here.
- "File and run" on a ticket that dedups to `merge`/`exact` still does not start
  a run. With the runner resident the ticket gets worked on the next poll, so the
  practical impact is a delay rather than a stall.
- The queue view shows loop state and per-ticket status, but there is no
  "dispatch the top eligible ticket" button distinct from Start — starting the
  loop is what dispatches. Worth revisiting if the operator wants one-shot
  dispatch without leaving the loop resident.
