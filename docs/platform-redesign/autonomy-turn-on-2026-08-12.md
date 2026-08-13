# The automation loop: what exists, what is dead, and the plan to turn it on

**Status: PLAN. Written 2026-08-12 against the live tree, not against the docs.**
Supersedes nothing. It is deliberately not another design: the design is done,
and this says which parts of it are real, which are decoration, and the
shortest honest path from here to autonomous ticket execution.

> **Read this first if you are about to write design doc number thirty-two.**
> There are already ~30 design docs across `docs/platform-redesign/`. The role
> model, the autonomy model, latitude, the `awaiting` shape and the triage
> decision set are all decided AND largely shipped in the backend. The gap is
> not design. The gap is that nothing runs, and that several shipped controls
> do nothing.

---

## 1. What is actually true today (measured 2026-08-12)

Every number here came from the live stores and the running host.

### The loop is complete and dormant

| | |
|---|---|
| Last ticket executed | **2026-07-25 12:02** (`.runtime/ralph.log` mtime) |
| Resident queue runner | **none.** `.runtime/queue-runner.pid` absent; last runner exited 2026-08-10T06:54:42 |
| Autonomous cron | **commented out**, under the header `# ---- Autonomous work: OFF by operator decision 2026-08-10 ----` |
| Global autonomy brake | **off** (`.runtime/autonomy-override.json` `engaged: false`) |
| Runs in the log | 2753, of which **0 are non-terminal**; most recent start 2026-08-05 |

The path itself works: `POST /api/tasks/{scope}/{id}/run` opens a Job and
`Popen`s a detached `project_ralph.sh --task-id`, which claims the ticket,
resolves an effort tier, and hands `title + body` to `claude -p` in a loop
bounded by `scripts/reviewer.sh`. That is the whole executor. There is no
runner agent, no queue daemon, and nothing that starts a run except the API.

### The agent layer is disjoint from the system that runs

- 10 agent definitions exist (3 live: `masini`, `masini-commute`,
  `memory-keeper`; 7 retired builders). **All 8 build agents are `retired`,
  last active mid-July.**
- **Zero registry agents have ever produced a job record.** The 7 `agent_id`
  values in the job log are Letta UUIDs belonging to per-channel Discord
  agents, which the registry explicitly excludes.
- Every agent is `autonomy: reactive`, which means never dispatched in the
  background. `dispatch_triggers.sh` (the conductor) is **not in crontab**.
- The one `triager` in the roster declares `triggers: [{kind: discord}]`, and
  the dispatcher serves `cron`/`loop`. It has never fired; `.runtime/triager/`
  does not exist.

So: the roles are real in code (`ROLES = maker, reviewer, triager, keeper,
broker`, validated on write, with a handler table), and no agent has ever been
routed through them.

### Scope review exists, is advisory, and most tickets fail it

`backend/services/ticket_readiness.py` asks six questions of a ticket: title,
objective, acceptance, entry points, boundaries, dependencies resolve. Only
`title` and `dependencies` block a run; the rest report.

Measured across all 34 open tickets in every scope right now:

| check | open tickets failing |
|---|---|
| acceptance (what done looks like) | **25 (74%)** |
| boundaries (what it may change) | **23 (68%)** |
| entry points | 6 (18%) |
| objective | 1 (3%) |
| **pass every check** | **9 (26%)** |

That 26% is up from "1 of 40" when the checker shipped, so ticket quality is
improving. It is still not a number you can gate on without wedging the queue.

There WAS an LLM scope reviewer: `POST /scope-review` returned
`{verdict: ok|split|underspecified, reason, subtickets, blockers}` and `/split`
turned that into child tickets. **I deleted both routes on 2026-08-12**,
because nothing but one button called them, no ticket in the store had ever
been split, and no ticket carried a `parent`. That was the right call for a
dead route and the wrong shape to leave the capability in:
`backend/services/ticket_scope_review.py` still exists, 189 lines, reachable
only through a dangling import. **The engine survived; only its button died.**

### The linkage between tickets, runs, agents and chats is mostly unwritten

Job records carry `task_id`, `agent_id`, `chat_id`, `project_slug`,
`parent_id`. Across 2753 records:

| link | populated |
|---|---|
| `agent_id` | 1957 (71%), but every value is an unregistered UUID |
| `task_id` | 79 (2.9%) |
| `parent_id` | 74 |
| `project_slug` | 47, only two distinct values ever |
| `chat_id` | **0** |

`chat_id` was zero because `launch_task_run` never took one, while the chat's
own "file and run" path called it with the thread id sitting in scope. Fixed
2026-08-12; new runs record their origin chat, and the run page prefers the
run's own chat over the ticket's when they differ.

### Controls that exist and do nothing

Naming these is half the plan, because each one is a lie the UI currently tells.

| control | reality |
|---|---|
| `latitude` (strict/standard/open) | **prompt text.** One machine-visible consequence: reviewer rc=7 blocks instead of retrying. Zero tickets and zero projects set it. |
| `touches` | an **after-the-fact audit on the committed diff**, not a write barrier. No ticket sets it. |
| `depth` / `fanout` on an agent | **no executor reads either.** Parsed, echoed to the API, dropped. Still rendered in `AutonomyPanel`. |
| Settings → start the queue runner | **cannot work.** `POST /queue/runner/start` Popens the watchdog with no directory; the watchdog refuses an empty dir (ADR 0014) and exits 2. |
| `assignee` | `"operator"` on all 93 platform tickets. `--assignee agent:<slug>` would select nothing. |
| `broker` role | in the enum, no handler, no agent declares it. |

And one honest note that the governance addendum already makes in writing:
scope, `touches`, latitude and grants are **coordination, not confinement**.
The autonomous worker runs as the same uid as the interactive session with the
docker group, so it can rewrite the very files that describe its own limits.
Nothing in this plan should be presented in the UI as security.

### The reviewer gate checks completion, not correctness

`scripts/reviewer.sh` makes **zero model calls**. It requires: a non-empty
diff, no new stub markers, python typecheck on changed `.py`, `tsc --noEmit` on
changed `.ts/.tsx`, and any executed acceptance criteria passing. A second gate
in the coordinator requires a real commit.

This is a good gate and it is worth keeping exactly as it is. It answers "did
something compile and land", not "is this the work the ticket asked for". Every
proposal below that adds judgement adds it somewhere else, never inside this
script.

---

## 2. What a ticket page is FOR, and the operations that belong on it

A ticket page is where the operator decides three things: is this the right
work, is it ready to be done by something that cannot ask them, and what
happened when it ran. Every control on the page should serve one of those.

**The operations, and where each one lives.**

| operation | surface | commit rule |
|---|---|---|
| change status | menu on the status word in the header | **on change.** Where the work has got to is not config; it is the same move as dragging a card between columns. Behind Edit mode it cost three interactions. |
| approve a draft | primary button, only while `draft` | on click. A button press is an approval. |
| run it | primary button, only while `todo` | on click, no confirm |
| answer a block | one field and one button in the Stuck section | on click, and it is written into the BODY, because a worker sees only title + body |
| edit title / body / priority / effort / depends-on | Edit mode, one Save | on Save. This is a spec a worker will execute. |
| comment | composer at the foot of Comments | on click. Deliberately NOT part of the spec (`tasks.py:1897`). |
| refresh, delete | overflow menu | delete behind `ConfirmDialog` |

**What is deliberately absent:** a Deploy button (that is `/deploys`), an
assignee picker (one value in the entire store), a latitude editor (0% set, and
the field is advisory), an attempt-cap editor (0% set), and a readiness nag
panel (approve already reports the gaps in one line).

**What is missing and should exist**, in the order the loop needs it:

1. **A readiness line, not a checklist.** The six checks already run on every
   ticket read. The page shows the count only at approval. One quiet line
   ("does not say what done looks like") next to the Run button turns a 74%
   failure rate into something the operator can fix in ten seconds.
2. **Re-run.** A failed run leaves the ticket `todo` with no affordance except
   Run, which reads as "start", not "try again".
3. **A scope-review action** that calls the surviving engine and proposes
   sub-tickets. See §4.
4. **A link to the chat that filed it, and to every run's chat.** The first
   exists; the second is newly recorded.

---

## 3. Autonomy, boiled down to three dials

There are eight autonomy-adjacent controls today. Four do nothing. A control
that does nothing is worse than a missing one, because the operator reasons
with it. The proposal is to keep three, delete or hide the rest, and make each
one answer a different question.

| dial | question it answers | where it lives | today |
|---|---|---|---|
| **1. Is unattended work permitted at all?** | the brake | one global switch, `.runtime/autonomy-override.json` | **shipped and correct.** Missing file = clear, corrupt = engaged (fail closed). Keep exactly. |
| **2. What may run unattended HERE?** | per scope | `autonomy.mode` on a project / scope | exists on one project, `manual`. Needs to be a real per-scope setting with three values. |
| **3. What happens when a run finishes?** | per agent | the `autonomy` enum: `reactive` / `supervised` / `autonomous` | shipped in the registry, read by nothing, because nothing dispatches. |

The three compose as one sentence the operator can read:

> Unattended work is **on**; in **workshop** scope, tickets marked ready may run
> **supervised**; results land in **review** before they commit.

Rules that fall out, and that the current design already decided:

- **The global brake is an overlay, never a mutation** (ADR 0008). Clearing it
  restores every agent's own setting because nothing was changed. No restore
  log, no restore API. Keep this; it is the one piece of the autonomy model
  that is both shipped and load bearing.
- **Autonomy has no cascade.** A ticket does not override its agent's autonomy.
  Latitude cascades; autonomy does not. Two different questions.
- **Manual runs are never blocked by the brake.** "Run now" is the operator
  present and asking.

**What to delete rather than fix:** `depth` and `fanout` from the agent surface
(no reader), the queue-runner Start button in its current form (it cannot
start anything), and any UI text that describes `touches`, `latitude` or grants
as a boundary. Latitude stays as a field and as prompt text, honestly labelled:
it changes what the worker DOES when the ticket is silent, and it is not a
permission.

---

## 4. The roles, and the one that is missing

The role enum is settled and correct: **maker, reviewer, triager, keeper,
broker**, closed in code, free-form in the body. The definition that makes it
work is `primitive-roles-2026-07-18.md:18` — "a role is the set of writes an
agent is permitted to make". Everything else about an agent (what it reads,
when it wakes, how it judges) is a field, not a new role. That is why planner,
watcher, researcher, scribe, critic and conductor were each rejected with a
reason, and none of those rejections should be reopened.

Two structural rules from the same design that this plan keeps:

- **No agent addresses another agent.** Coordination is through shared state:
  a ticket store and a knowledge store both sides read and write. No inbox, no
  handoff protocol, no message bus.
- **The conductor is not an agent.** It is ~20 lines of deterministic Python
  (today, `dispatch_triggers.sh`). No LLM runs to decide whether an LLM should
  run.

**The stock set for this installation, and what each is for:**

| role | instance | trigger | writes | status |
|---|---|---|---|---|
| maker | the sweep worker | claimed from the queue | tree, commits, its own ticket's transitions | runs today, as a shell loop with no agent identity |
| reviewer | the completion gate | after every maker run | a verdict + rejection reasons | runs today as `reviewer.sh`, deterministic, no identity |
| triager | note and inbox router | cron, daily | tickets, and one decision record per note | shipped (`triager.py`, `triager_run.sh`), never fired |
| keeper | memory-keeper | cron, 6h | knowledge notes, learnings, skill proposals | registered, never fired |
| broker | on demand | never dispatched | nothing | enum only |

**The missing one is not a new role. It is a triager pass over the QUEUE
rather than over notes.** Scope review is a triager decision: given a ticket,
emit exactly one of `ok` / `split` / `underspecified` / `none`, where `split`
files children and `underspecified` sets `awaiting` with a specific question.
That is the same closed four-outcome set the note triager already implements,
pointed at a different input, and the LLM engine for it still exists in
`ticket_scope_review.py`.

This is the answer to "do we need a scoping agent". **No new role, no new
authority, one new trigger on an existing one.** It also respects the standing
operator rule that scoping and decomposition happen in the main chat thread
before a run is spawned: the queue triager is the *unattended* fallback for
tickets that were filed without that conversation, and its output is a question
back to the operator, not a plan it executes.

### Centralized or decentralized

Both, and the split is already decided: **execution is single-threaded and
supervised, coordination is stigmergic.** One writer per tree at a time (the
flock), one deterministic dispatcher, no peer negotiation. Agents "see" each
other only through the ticket store and the knowledge store. The reasoning is
recorded across several docs and it is the same in each: parallel writers make
conflicting implicit decisions, every inter-agent message is lossy compression,
and at ten agents a peer mesh has 45 edges nobody can test.

A user adding their own agent therefore adds: a slug, a persona body, a role
from the closed five, a trigger, and an autonomy mode. Nothing else, and no new
wiring, because the shell does not know what a role does.

---

## 6. The sequence

Ordered so that each step is observable before the next one is trusted. The
rule for the whole sequence: **nothing runs unattended until the thing before
it has run attended, and been watched.**

### Phase 0 — stop lying (a day)

No new capability. Every item here removes a control that does nothing or a
label that is false.

- Delete `depth` / `fanout` from the agent surface.
- Fix or remove the queue-runner Start button. As written it cannot start a
  runner.
- Relabel `latitude` in the UI as what it is: what the worker decides when the
  ticket is silent. Remove any framing of `touches` / grants as a boundary.
- Re-wire `ticket_scope_review.py` or delete it. A 189-line engine behind a
  dangling import is the worst of both.

### Phase 1 — make readiness visible where it is decided (a day)

- Show the readiness line on the ticket page and in the chat's file-a-ticket
  card, at the moment of filing, not only at approval.
- Make the ticket-maker fill `acceptance` and `boundaries`, the two checks 74%
  and 68% of open tickets fail. This is a prompt change in the distiller plus
  the four-field body format that already exists.
- Target: the pass rate goes from 26% toward 80% on newly filed tickets. Do not
  gate on it yet. Measure it for a week.

### Phase 2 — one watched run, end to end (a day)

- Run one real ticket with `POST /run` and watch the whole path: claim, worker,
  reviewer, commit, receipt, status.
- Confirm the run's links are now written (`task_id`, `chat_id`), that the
  ticket page shows the run, and that the run page links back to the chat.
- Fix what that surfaces. Do not proceed while anything about it is surprising.

### Phase 3 — the queue triager, attended (two days)

- Add the queue trigger to the triager: for each `todo` ticket not yet
  reviewed, emit `ok` / `split` / `underspecified` / `none`.
- `underspecified` sets `awaiting` with ONE specific question and surfaces it in
  the inbox and in the originating chat. `split` files children as drafts,
  which the operator approves. Depth cap 2, filing cap 5 per pass.
- Run it manually first. Read every decision it makes for a week before giving
  it a cron trigger.

### Phase 4 — supervised unattended execution (a week)

- Restore the queue runner, one scope, `supervised`: runs land in `in_review`
  rather than committing to `done`.
- Add the missing inbox producer: a run that finishes or fails unattended
  raises a `notify`. Today only blocked questions and memory promotions do,
  which is why the inbox is empty.
- Add the stale-question escalation the docs name as unbuilt: a question that
  has sat unanswered for a day gets louder, rather than sitting silently.

### Phase 5 — autonomous, per scope (when phase 4 is boring)

- Flip one scope from `supervised` to `autonomous`. The reviewer gate and the
  commit gate stay exactly as they are.
- The prerequisites the earlier design named and this plan keeps: a
  `supersedes` edge (nothing today expresses "this replaces that", so a stale
  ticket can be executed against a tree that moved on), a distinction between
  executable tickets and judgement tickets (today `role` types the agent, not
  the ticket, so a design ticket looks executable), and a dry run
  (`--plan`) so a sweep can say what it would do without doing it.

**Do not skip to phase 5.** That instruction is already written into
`continuation-2026-07-31.md` and it was right.
