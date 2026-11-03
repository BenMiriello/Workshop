# Session handoff, 2026-07-14 (continue after compaction)

Purpose: the next agent reads this first and continues the build. It has three
parts: (1) the operator's north-star vision, in his own framing; (2) what this
session actually did and the current state; (3) the remaining build queue.

Hard operating rules still apply (single-operator system, no defensive cruft, no
em-dashes, no emoji, the excluded work org separation). The operator is the operator.

---

## 1. North-star vision (operator, 2026-07-14) — build toward this

The system's purpose: **you hold conversations across many chats, and the actual
work those conversations imply gets turned into well-scoped jobs/tickets that are
managed, synchronized, and orchestrated in the background** so nothing is dropped
when a chat is dropped, and the background keeps running even when you are not
chatting.

Concretely, the operator wants:

- **Chats spawn well-scoped jobs (tickets).** When you work through ideas in a
  chat, the system scopes out discrete jobs/tasks. Jobs are deliberately
  ticket-shaped so they can OPTIONALLY link to GitHub issues or other ticket
  systems later. You scope them out, the system picks them up and takes them on.
- **Background orchestration, not chat-bound.** Different agents spin up to take
  jobs on and work them in the background, at a controlled pace. The chat's memory
  must NOT be the blocker: if a chat is dropped or hits an error, the work already
  scoped continues. Something owns keeping the background running; the system does
  not freeze/stop when a chat has an issue.
- **Shared context across chats, scoped by access.** Every chat should be able to
  know about (or at least access) all ongoing tasks, plans, docs, and context, so
  they share context and can coordinate. A builder chat in project A does not
  automatically carry project B's context, but depending on ACCESS SETTINGS it can
  reach for it if it is available/shared with the rest of the app. This is the
  grants/sensitivity model applied to knowledge + task context.
- **Dedup + self-maintenance.** Agents must be aware of all ongoing work and
  deduplicate against each other (not clash, not redo). They self-maintain the
  plans/docs/task state. The operator's pain today: he discusses different ideas in
  different chats, loses track of which chat to be in, and the chats clash / do not
  orchestrate well. The system should resolve that centrally.
- **Attention proportional to size.** A chat request might be a few small asks or
  several big ones. The system should scope each appropriately and spin up
  background agents to take them on (it already weights big over small somewhat,
  but the orchestration/dedup is the missing piece).
- **Usage pacing (the key config).** Autonomous background building must NOT burn
  through Claude Code usage fast just because you gave it a lot. It should work
  through jobs AT A PACE. The configurable knob is roughly "how much it will do per
  day / per 5-hour window." Needs BOTH a global setting AND per-project settings for
  how much usage each gets. The operator is unsure of the exact UX but that is the
  intent.
- **Chats are separate from that limit.** Chat usage need NOT be limited (you are
  manually involved). The pacing limit is only on autonomous background building.
  Keep the two budgets distinct.
- **Always-on management.** There is a supervisor/orchestrator keeping these loops
  running even when you are not chatting. (We already have cron + dispatcher +
  supervisor pieces; this vision is what they should add up to.)

The through-line: **the job/ticket queue is the durable substrate; chats are a
front-end for creating and steering it; background orchestration executes it at a
paced, deduped, self-maintaining clip, governed by global + per-project usage
budgets and by access/sensitivity for cross-scope context.**

---

## 2. What this session did + current state

### 2a. The chat fix (the operator's original complaint) — DONE + verified
The general/default chat was the old flat "notes app" (blocking request/response,
tools invisible). Now EVERY chat (general, app, project, builder) streams through
the build engine and shows its work live (tool chips, interim text, elapsed, Stop,
reconnect). Verified end-to-end (`backend/scratch/test_data_stream.py`: a general
turn called WebSearch and rendered the tool chip + answer). Details in the memory
note `project_chat_streams_all_providers` and `docs/platform-redesign/review-addendum.md` section 3.
Commits: `e262019` (P1/P2), `4ddee15` (agent: provider + Fable model).

### 2b. Platform-redesign UI cleanup (from review-addendum.md punch list) — DONE
- P5/P6 Jobs de-orphaned + Job detail page (`a7ed888`)
- P7/P10 Chats page actions/filter/overflow + ProjectDetail agents/grants (`dd6642f`)
- P4 chat CSS tokenized, fixed a real dark-theme contrast bug (`477bc81`)
- P8 Stores/Grants made visible + GrantPicker mounted (`0e9ba01`)
- P12 em-dash sweep of rendered UI copy (`5d998c2`, `fa579f0`)
- Fable 5 added as a pickable model.

### 2c. Autonomy / task-management ("agent tracks its own todos") — BUILT to O4, more remains
This is the core of the north-star vision and it is PARTLY BUILT. The design lives
in `docs/platform-redesign/autonomy-task-management.md` (assessment + phased plan)
and `de6ed44` "orchestration + autonomy + memory design (unified)". The autonomous
builder agents (workshop-builder-*) then implemented the early phases:
- **O1** canonical task model + queue primitives + lossless reconcile/export
  (`10427da`, `300a10a`, `b3ce374`). One `.tasks.json` per SCOPE (a scope = a
  directory: project tree, builder cwd, agent scope, workshop root as "self").
  `backend/services/tasks.py`, `backend/services/task_scope.py`, `scripts/project_tasks.py`.
- **O2** conversation emits tasks: `task-patch` protocol + CLI-first task personas
  (`7edfe2c`). Builders write tasks via the CLI, not by hand-editing TODO.md (which
  was the self-destroying bug the design found).
- **O3** background dispatcher + coordinator/child Job graph + task/parent linkage +
  cron `due()` (`52ffb5c`, `e68aaac`). `scripts/dispatch_triggers.sh` runs project/
  agent loops unattended from the cron tick.
- **O4** acceptance gates + per-task attempt caps + awaiting_input payload
  (`a6db6af`, `09a7d1d`). A sweep that ends with only blocked/in_review work emits an
  `awaiting_input` Job carrying each task's question; JobDetail renders it with
  approve / send-back / unblock / cancel.
- IA streamline pass 1 (`65cf36d`), compact Chats cards (`de2786c`), a reconcile
  guard on the repo root "until O7" (`42d61b0`).

### 2d. Just fixed this session: awaiting_input dead-ends (`4253668`)
The operator hit an `awaiting_input` Job with no way to act on it. Root cause: it
was an UNBOUND loop sweep (empty project_slug + agent_id) so every per-task action
returned 400 "no resolvable task scope", and there was no job-level escape hatch.
Fixed: `POST /jobs/{id}/dismiss` (always clears a job regardless of binding); a
"Dismiss this alert" button + an unbound-case explanation in JobDetail; and
`project_ralph.sh` now only emits awaiting_input when the sweep is BOUND (else it
finishes `done` and logs why). Cleared the reported stuck job. (Edited the live
autonomy script under AUTONOMY_DISABLED, re-enabled after.)

### 2e. Task list state (this session's tracker)
Done: P1-P8, P10, P12, Fable model, the awaiting fix, and O1-O4 (by the autonomous
builders). Deferred/not built (see queue below).

---

## 3. Remaining build queue (what the next agent should do)

The operator explicitly wants the rest of this built (paced across sessions; it is
too much for one low-context session). Rough priority:

### A. Finish the orchestration/autonomy build (the north-star core)
Continue `autonomy-task-management.md` beyond O4. Known/likely remaining phases:
- **Usage pacing + budgets (HIGH — operator called this out specifically).** A
  global autonomous-build budget AND per-project budgets expressed as "how much per
  day / per 5-hour window." Enforce it in the dispatcher (`dispatch_triggers.sh` /
  cron tick): cap how many sweeps/child Jobs run per window, per project and
  globally. Keep it SEPARATE from chat usage (chats stay unlimited; the operator is
  manually involved). Surface the config in Settings (global) + ProjectDetail
  (per-project). This is the "don't burn all my Claude Code usage" requirement.
- **Cross-chat shared context / dedup (HIGH).** Every chat should be able to access
  all ongoing tasks/plans/context, gated by sensitivity + grants. Builder chats
  default to their own project scope but can reach shared/other scopes when access
  allows. Agents must dedup against existing tasks/Jobs (do not re-scope the same
  work). Likely: inject an ambient "ongoing work across the workspace" digest into
  chat context (respecting scope/grants), and a dedup check when a task-patch would
  create a near-duplicate task.
- **The reconcile guard / root "self" scope (O7 per the code comment).** `42d61b0`
  guarded the repo root against destructive reconcile "until O7." Finish that so the
  workshop-root "self" project can be swept safely.
- **Orchestration robustness.** Ensure the supervisor/dispatcher keeps loops running
  even when a chat errors; the chat is never the blocker. Verify awaiting/blocked/
  in_review flows end-to-end with a real project sweep.
- **Ticket-system seam.** Jobs are intentionally ticket-shaped; leave a clean seam to
  later link a Job to a GitHub issue (do not build the integration now, just keep the
  model compatible).

### B. Audit the O1-O4 build for gaps (the operator asked)
The awaiting_input dead-end was one rough edge of the just-built system. There are
likely others (the operator's recurring worry: "says done but has holes"). Do a
focused review of O1-O4: task store reconcile correctness, dispatcher pacing/caps,
Job graph integrity, the personas actually using the CLI, awaiting/acceptance flows.
Consider a Fable review pass like the ones used earlier this session.

### C. Smaller deferred items from the earlier queue
- **A safe "query around the workshop" tool for the general chat** (operator asked;
  the chat has web + streaming now). Do it as a SCOPED read-only MCP (knowledge/task
  digest), NOT a raw filesystem Read grant (which would expose `.runtime` secrets via
  egress). Ties into the cross-chat shared-context work above.
- P9 collapse the duplicate "New builder" modal into `/agents/new` (Agents.tsx).
- P11 Health-settings home; assistant retire-in-UI.
- The chat toolbar's provider `<select>` -> context Tag + agent-select control
  (deferred; the footgun is already defused because transport follows the thread key).
- styles.css -> components.css relocation of the `.chat-*` block (pure organization).
- Doc-comment em-dashes (the P12 sweep only did rendered strings).

---

## 4. Operational notes (gotchas for the next agent)

- **Cannot drive the authed UI.** The login wall (correct) blocks the agent from the
  running frontend. Verify via `tsc`, `check_design.sh`, backend scratch tests
  (`backend/scratch/test_data_stream.py` is a good model), and 401-vs-404 curl checks
  that routes are mounted. Ask the operator to confirm anything purely visual.
- **The tree has LIVE autonomous builders working in it.** workshop-builder-* agents
  edit files, run sweeps, and commit via their reviewer gate concurrently. ALWAYS
  `git status` and stage ONLY your own files; never `git add -A` (you will grab their
  WIP). Distinguish their commits from yours.
- **Restarting the backend interrupts in-flight chat turns.** Build turns are detached
  + journaled and REATTACH on boot (they survive). After every restart, check for
  orphaned streaming turns (grep `.runtime/users/*/chat/*.json` for `"streaming": true`
  and cross-check `.runtime/turns/*/meta.json` pids) and only nudge ones YOU
  interrupted. This session's restarts orphaned nothing.
- **Editing live autonomy scripts** (`project_ralph.sh`, `dispatch_triggers.sh`,
  `cron_agent.sh`, `ralph.sh`) requires the pause: `touch .runtime/AUTONOMY_DISABLED`
  before, `rm` after. The cron fires every ~20 min against the live tree.
- **Restart** via `bash scripts/restart_backend.sh --deferred`; it is a systemd --user
  service with env overlays.
- **Backend Python:** `PYTHONPATH=$WORKSHOP/backend .venv/bin/python`;
  source `<bridge-config>/auth.env` for a token when a scratch test spawns claude.
- Design gate + typecheck before "done": `bash scripts/check_design.sh` and
  `cd frontend && npx tsc --noEmit`.

Key docs: `docs/platform-redesign/autonomy-task-management.md` (the design),
`docs/platform-redesign/review-addendum.md` (the UI punch list + chat fix spec),
`docs/platform-redesign/implementation-plan.md`, and this file.
