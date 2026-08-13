# Plan: one way to apply a change (development context)

Status: PROPOSED, 2026-08-13. Awaiting operator approval before any code moves.

Scope: the **development** deployment only (this box, systemd `--user`, one shared
checkout at `~/workshop`). Production is not designed here. The plan does put the
command name behind a seam so a production implementation can land later without
re-teaching every agent, but nothing here claims to be a deploy pipeline.

## 1. The complaint

Every agent that finishes a change re-derives how to make it live, worries about
other agents, and often concludes it is not safe and does nothing. The result is
delay, discussion, and a running app on stale code.

The investigation found this is **not** agent timidity. It is three real defects
in the system the agents are reading and calling.

## 2. What is actually wrong

### 2.1 The deferred backend restart silently does nothing, ~19% of the time

`scripts/restart_backend.sh --deferred` forks a detached waiter. The waiter waits
for the arming turn (6h cap), then any other turn (180s cap), then the commit
window (180s cap), printing `warn: ... restarting anyway` when a cap expires.
Then, at `scripts/restart_backend.sh:129`, it re-invokes itself:

```bash
    sleep 1
    bash "$self"          # no --force
```

The synchronous guard at `:157` is `[ "${1:-}" != "--force" ]`. It is true. The
guard runs, finds the still-live turn the waiter just gave up on, prints
`REFUSING`, and exits 2. **Nothing restarts.**

The caller already got `exit 0` and the message `Backend restart armed: fires when
this turn ends`. The refusal goes only to `/tmp/workshop-backend.log`, which
nothing reads and nothing surfaces.

Measured in the current log: **61 successful restarts, 14 refusals.** Every one of
those 14 was reported to its agent as armed and successful.

This is the third instance of this exact bug class in this one file. The file's
own comments at `:26-33` and `:66-74` document the previous two as fixed. Both
fixes were correct and both left this path open.

**Consequence for the complaint:** agents have restarted the backend, reported
success, and been wrong. The distrust is earned. No amount of instruction fixes
a tool that lies.

### 2.2 The instruction every in-app agent gets is wrong about the frontend

`backend/services/chat_context.py:934-948` is injected into every chat and build
turn on this project. It says:

> FRONTEND (frontend/, Vite + React): HOT-RELOADS on save. Edits to
> frontend/src/... appear in the running UI immediately. ...
> (Frontend changes are already live via HMR, so no restart.)

It never mentions `build_workshop_public.sh` or `ui_sync.sh`. But `CLAUDE.md:12-19`
records the settled 2026-08-11 decision that **the operator uses the public origin,
which serves the built `frontend/dist`**. An agent that follows its injected
briefing ships a frontend change the operator cannot see, and is told it is done.

`scripts/restart_app.sh:8` repeats the same wrong framing.

### 2.3 The instructions are defined in about thirty places and contradict

Restart or rebuild instructions appear in: `CLAUDE.md`, `chat_context.py` (two
separate blocks), `restart_app.sh`, `restart_backend.sh`, `build_workshop_public.sh`,
`bootstrap.sh`, `preflight.sh`, `check_design.sh`, `.githooks/post-commit`,
`shared/learnings.md` (injected every turn), four project `CLAUDE.md` files, and
**25 files under `docs/`**.

Six live contradictions:

1. **Which command restarts the backend.** `CLAUDE.md` says
   `restart_backend.sh --deferred`. `chat_context.py:943` says
   `restart_app.sh workshop`. `restart_backend.sh:3-8` says the preferred path is
   `systemctl --user restart workshop-backend`.
   `docs/platform-redesign/continuation-2026-07-31.md:154` says use systemctl on a
   detached delay because the guard never clears. They do reconcile in code
   (`restart_app.sh:61` execs the deferred path) but that is stated nowhere.
2. **Whether `--deferred` works.** `CLAUDE.md:37` states it "cannot eat the reply"
   as a guarantee. `docs/chat-durability-handoff-2026-08-12.md:400` and
   `docs/platform-redesign/agent-interaction-model-2026-07-31.md:517` both say it
   silently fails. Per 2.1, the handoff docs are right and `CLAUDE.md` is wrong.
3. **Whether a frontend change needs a build.** `CLAUDE.md` says always.
   `chat_context.py` says never. `.githooks/post-commit` says it is automatic
   (true, but only in the canonical worktree, on `main`, after a commit).
4. **What the build command is.** Four answers:
   `build_workshop_public.sh`, `npm run build:ui && npm run build:web`,
   `npm run build:web`, and `systemctl --user restart workshop-frontend`.
5. **Whether to restart at all.** `projects/gengui/CLAUDE.md:46` says
   "NEVER restart servers, even after code changes; just tell the user".
   `~/.claude/agents/work-reviewer.md:89` treats "needs operator restart" as a
   reason to **fail** the completion gate. An agent obeying the first fails the second.
6. **The edge.** `docs/session-followups-2026-07-12.md:7` says frontend changes
   need no edge restart (true). Neither `CLAUDE.md` nor the injected briefing
   mentions `--restart-edge`, which is required when
   `backend/workshop_public_app.py` itself changes.

Also stale: `docs/ui-origins.md:14` still calls the tailnet Vite origin "the
operator's usual UI", which the 2026-08-11 decision reversed.

Also: `shared/learnings.md:23` is injected into every prompt and says "Use `nohup`
for long-running services", i.e. do not use systemctl. Measured today: an agent
can run `systemctl --user restart workshop-frontend` on this box without being
blocked. That learning is stale and is actively steering agents away from the
correct mechanism.

### 2.4 Nothing tells an agent it needs to think about this at all

`frontend/CLAUDE.md` (the mandatory design-system doc for all frontend work)
contains **no** restart or rebuild guidance. A frontend agent reading its own
scoped doc learns nothing about the built origin.

## 3. What is NOT wrong (measured, so agents can stop worrying)

These findings should be stated to agents as plainly as the rules, because the
fear is mostly attached to the safe operations:

- **Rebuilding the public bundle is cheap and non-disruptive.** Measured: 16.5
  seconds. The edge serves `dist` from disk per request, so no service restarts,
  no request drops, no SSE cut. `build_workshop_public.sh` takes an `flock`, so
  concurrent callers queue instead of racing.
- **Restarting the Vite dev server is ~2 seconds** and does not touch the
  operator's origin at all.
- **Restarting the backend is the only disruptive act**, and its blast radius is
  bounded: `KillMode=process` means in-flight `claude` children survive, and a
  killed turn replays its transcript rather than losing it.
- **`systemctl --user` is not blocked** for agents on this box. Verified today.

## 4. The plan

Four phases. Phase 1 is a one-line bug fix and should land regardless of the rest.

### Phase 1 — Make the restart honest

1. `scripts/restart_backend.sh:129`: `bash "$self"` becomes `bash "$self" --force`.
   The waiter has already served every wait the guard exists to enforce; re-checking
   the guard after deliberately giving up is what turns a bounded wait into a
   silent no-op. The guard stays exactly as it is for direct synchronous callers.
2. The waiter verifies and writes a receipt to `.runtime/apply-last.json`:
   `{when, action, outcome, old_pid, new_pid, error}`. Today the only record is
   `/tmp/workshop-backend.log`.
3. `restart_app.sh` stops exiting 0 when it restarted nothing (`:64`, `:68-72`
   both swallow failures and return success).

**Acceptance:** arm a deferred restart under a deliberately long concurrent turn,
confirm the backend PID changes, and confirm `.runtime/apply-last.json` records it.

### Phase 2 — One command: `scripts/apply.sh`

One entry point an agent calls when it finishes, whatever it changed.

It takes **no arguments and requires no judgement**, because judgement is what
agents are getting wrong. It does not ask the agent what it changed and does not
read git. It compares **what is live against what is on disk** and fixes only the
drift:

- `frontend/dist` source-id vs `ui_source_id.sh` (this is exactly what
  `ui_sync.sh` already computes) → rebuild if stale
- newest mtime under `backend/` vs the backend unit's `ActiveEnterTimestamp`
  → arm a deferred restart if newer
- `frontend/.env` mtime vs the frontend unit's start → restart Vite if newer
- `backend/workshop_public_app.py` mtime vs the public unit's start
  → `build_workshop_public.sh --restart-edge`

Properties that make it safe to tell every agent "just run this":

- **Idempotent.** Running it twice in a row does nothing the second time.
- **Always safe to run**, including concurrently from several agents. Serialization
  is the script's problem (the existing `flock`, the existing turn-wait), never
  the agent's.
- **Prints a receipt** of what it did and what it skipped, and why.
- `--check` reports drift without acting, for the start of a turn.
- The backend line necessarily reports "armed", not "done", so the receipt from
  Phase 1 is what closes it out.

Deliberately out of scope: it does not commit, does not push, does not decide
whether work is finished. It only makes the running system match the disk.

### Phase 3 — Collapse the instructions to one source

The rule that prevents recurrence: **the commands are written in exactly one
file; every other place links to it and states no command of its own.**

- **New canonical doc:** `docs/applying-changes.md`. The only file in the repo
  that spells out the mechanics. Absorbs the relevant parts of `deploying.md`
  and `ui-origins.md`.
- **`CLAUDE.md`:** the current 30-line section collapses to roughly:
  "Finished a change? Run `bash scripts/apply.sh`. It works out what is stale and
  fixes it. Safe to run any time, concurrently, from any agent. Detail:
  `docs/applying-changes.md`." Keep the "stale bundle means the operator is
  looking at old code" warning; it is load-bearing and was learned expensively.
- **`chat_context.py:934-948`:** rewrite. Remove "frontend changes are already
  live, so no restart" (2.2). Point at `scripts/apply.sh`. Stop describing
  deferred-restart mechanics in the system prompt.
- **`chat_context.py:1046-1056`** (non-self app projects): unchanged in substance,
  but re-worded so the two blocks do not read as competing rules.
- **`frontend/CLAUDE.md`:** add one line pointing at the same place (2.4).
- **`restart_app.sh` / `restart_backend.sh` headers:** correct the frontend claim,
  and mark both as the internals `apply.sh` calls, not the agent-facing entry point.
- **`shared/learnings.md:23`:** retire the stale "use nohup, not systemctl"
  learning (3), which is contradicted by measurement.
- **`projects/gengui/CLAUDE.md:46`:** resolve against the work-reviewer gate (5).
  Operator decision: is gengui genuinely hands-off, or is that line stale?
- **The 25 `docs/` files:** do not edit them individually. They are dated handoffs
  and historical plans. Add one line to `CLAUDE.md`: dated handoff and
  `platform-redesign/` docs are historical; `docs/applying-changes.md` is the only
  current source for how to apply a change. `ui-origins.md` gets the one factual
  correction about which origin the operator uses.

### Phase 4 — Close the loop so a failure is visible

Silent failure is what created the distrust, so the fix has to be observable.

- Surface the `.runtime/apply-last.json` receipt in the runtime briefing: an
  agent starting a turn sees "last apply: backend restart REFUSED 12:04" instead
  of nothing.
- `check_design.sh` already calls `ui_sync.sh --warn`. Leave the warning, but the
  reviewer gate should fail a UI task on a stale bundle rather than only warn,
  since the failure mode it guards is "operator is looking at old code".
- Worktree gap: `.githooks/post-commit` correctly refuses to build from a linked
  worktree on a branch, so a UI change built under `branches/` never reaches the
  operator until merge. `apply.sh` should say so rather than silently doing nothing.

## 5. The coordination answer

The operator asked specifically how agents should coordinate without getting
blocked. The answer the instructions should give, in one line:

> You do not coordinate. Run `scripts/apply.sh`. It is safe to run at any time,
> from any agent, concurrently with any other agent.

Everything real is handled inside the script: the build takes a lock, the backend
restart waits for its own turn and briefly for others, and a turn that does get
killed replays its transcript. An agent reasoning about other agents' in-flight
work is an agent inventing a problem the tooling already owns.

The one genuine shared-tree hazard is unrelated to restarts: a build picks up
another agent's uncommitted frontend WIP. That is a worktree question, it is
already tracked, and it must not be allowed to justify blocking on restarts.

## 6. Open questions for the operator

1. **gengui.** Its `CLAUDE.md` forbids restarting servers while the reviewer gate
   fails work that needs an operator restart. Which one is current?
2. **Reviewer gate on a stale bundle:** promote from warn to fail (Phase 4)?
3. **Command name.** `scripts/apply.sh` is proposed. It is the name that will be
   taught to every agent and later re-pointed at a production implementation, so
   it is worth picking deliberately.
