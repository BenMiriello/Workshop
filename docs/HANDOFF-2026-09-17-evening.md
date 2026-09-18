# Handoff, 2026-09-17 evening

Status: CURRENT. Written at 20:15 local (03:15 UTC 09-18) to continue this work
in a new chat. Everything below was measured today, not summarised from an
earlier document. Where a number appears, the command that produced it is named
so the next session can re-run it rather than trust it.

Read this file, then `CLAUDE.md`. Nothing else is required to continue.

---

## 0. The one thing that needs the operator's own hands

**The live database is at an alembic revision that exists in no commit, and
every alembic command is dead until it is repaired.**

`alembic_version` reads `a7c93e2f0b16`. The tree's head is `a1e4f7c02b93`. The
orphan is in no commit, no dangling git object and no worktree. It was generated
by an unattended worker at 08:17:53 UTC today, applied to the live database one
second later, and the worker was killed at 08:21:50 before it ever ran `git
add`. Its branch no longer exists.

Two consequences:

- It narrowed `ck_chat_thread_armed` to `('off','on-ready-ticket')` while the
  tree still declares four values. `/roles` renders commit-on-change selects for
  the two removed values and `routes/chat.py` validates them as valid, so
  Postgres is the only layer that says no. Clicking either is an unhandled
  CheckViolation, i.e. a 500.
- `upgrade`, `downgrade` and `current` all fail with "Can't locate revision", so
  **no migration can be applied to this installation at all** until the chain is
  reconciled.

The repair is prepared and verified up to the last command, which the auto-mode
classifier blocked. It is four lines and it is in
`scratch/db-orphan-recovery/README.md`, with a reconstructed migration beside it.
The downgrade WIDENS a constraint, so it cannot fail against existing rows, and
all 240 threads are `off`.

Do not leave the recovery stub in `backend/db/migrations/versions/`. While it
sits there the tree head equals the live revision and `apply.sh --check` reports
no drift, which hides the very thing it exists to fix.

Filed: [A killed worker can leave the shared database at a revision no commit contains](workshop:ticket/4c6989e8) and
[apply.sh treats a database ahead of the tree as harmless](workshop:ticket/0ab5f960).

---

## 1. Delivery and gate state, as of now

- `bash scripts/apply.sh --check`: UI bundle current (`6c5753ba8074`), backend
  current (`6d57782cbbaa`), all eight app bundles current, vite env and public
  edge ok. **The only drift is the orphan schema above.**
- `bash scripts/done.sh --shot /chats`: **PASS**. Screenshots at 390 and 1440 were
  taken and read.
- `npm run typecheck`: clean. `cd frontend && npm test -- --run`: 101 files, 1811
  tests pass. `bash scripts/check_design.sh`: `design: clean`.
- `python3 scripts/check_backend_tests.py`: exit 0, 4626 passed, 9 known failures.
- `bash scripts/check_fresh_clone.sh`: **exit 0, all five assertions PASS.** App
  separation for a stranger's clone is done as of `c88dbc80`.
- `python3 scripts/check_prompt_layers.py`: clean, floor 0.
- `python3 scripts/check_prompt_size.py`: `static_chars` **23922**, down from
  24256. Yesterday's upward re-pin is repaid, and 23840 (the pre-overrun pin) is
  82 chars away.
- `python3 scripts/check_ratchets_not_loosened.py`: no baseline loosened.
- `python3 scripts/check_plan_landed.py`: 6/6 steps landed. **Read section 4
  before trusting that number.**
- `python3 scripts/check_module_layout.py --since <base>`: today's own delta is
  +1 function-local import and +2 big files. The larger gaps against the pinned
  baseline (function-local 571 to 605, SCC 82 to 83, upward 32 to 34) are
  inherited at the base ref and the script says so itself.

Branch is `feat/client-state-consolidation`, **490 commits ahead of `main` and
1302 ahead of `origin/main`, with nothing ever merged.** It began on 2026-08-26.

---

## 2. What landed today

Eleven commits. `git log 6bf71bcf..HEAD`.

- `6e89b97b` WORKSHOP_CANONICAL_ORIGIN documented in instance.env, out of secrets
- `ecdf3dc1` + `9170ae05` a killed worker's commits skip the reviewer and go to
  review instead of burning an attempt, plus a 409-line test. **Recovered from an
  orphaned branch; it had been written, tested and never merged.**
- `12839052` two prompts that promised what this deployment does not do
- `01eae38a` one ordered rule for where work goes, replacing three that disagreed
- `8a1b6f36` re-pin `static_chars` DOWN to 23922
- `45de26f7` the loop's own pause-at-cap commit (the preserve path working)
- `1ccaebd1` the unattended worker's search tool was rooted at the platform
  checkout, so every worktree sweep ran blind
- `c88dbc80` a chat opened from an app stays in the app, the list is bounded, and
  nothing says "armed"
- `b4793613` the prefs file moved below the import cycle so the clock can read it
- `eda29e6a` one wall budget, no floor above the fraction, six missing owner pins

The last one was **recovered this evening** from an agent that was killed after
making the changes and before validating or committing them. Its work was intact
on disk; I validated it (all five scripts parse, 334 tests pass, the budget
resolves to 2700 giving a sweep budget of 8220s) and committed it.

Also cleaned up this evening: the same crash had left a **staged deletion of
`backend/services/user_prefs.py`** while the file sat on disk. Any commit in this
tree would have deleted the module that `b4793613` had just made `clock.py`
depend on. The on-disk copy was byte-identical to HEAD, so the deletion was
spurious and is unstaged. A further four backend files held a stale index
snapshot whose disk copies also matched HEAD exactly; that index state is
cleared. **The tree is clean.**

---

## 3. The autonomy loop: root cause found, partly fixed, still stopped

This is the largest finding of the day and it reframes months of "the loop does
nothing".

### The workers were never hanging

`ralph.sh` passes `--output-format json`, which buffers a single envelope until
completion. So a `timeout` kill **always** reports `output bytes=0, tokens=0`.
Those log lines look like a worker that never started. The workers were in fact
working and committing: the worktree branches carry real commits made inside the
kill window (+60, +245, +526 insertions).

Anyone reading `.runtime/ralph.log` will make this mistake. I did.

### The actual cause was confinement, proven by accident

A controlled experiment nobody set up, in today's log:

- Every attempt with `wt=1` (in a worktree, so the scope root is NOT the repo
  root) died identically: `exit=124, bytes=0, dur=900s`. Six in a row across two
  tickets, and seven counting a third.
- The one attempt with `wt=0` in the shared checkout succeeded first try:
  `exit=0, bytes=3282, tokens=336356, dur=434s`.

The only variable is whether the scope root equals the repo root, which is
exactly the condition under which `scoped_exec._remap` rewrites the MCP spec
path. In a worktree the remap is a no-op, the platform backend is not bound, the
CLI silently drops the workshop server, and the worker runs with no `search` or
`query` and is killed at the wall.

**`1ccaebd1` fixes the worker side** by rooting the emitted spec at `$DIR` and
making the log say "not applied" instead of "enabled" when the command is
unresolvable. Verified: the server answers a JSON-RPC initialize from a worktree
path, and the shared-tree case is byte-identical to before.

This is the same defect as a project chat having no tools. One cause, two
victims. See section 5.

### It is still stopped, for a different reason

`agent:development-maker` has used **3.65M of the 3.25M** tokens allowed in a
5-hour window. Every sweep now ends `refused: autonomous work has used 3.65M of
the 3.25M ... and the next task needs 150K more`. 61 refusals today by 10:35, and
it is still refusing at 20:16.

A large share of that spend was the flat 150k nominal charge booked for killed
runs that produced nothing. `eda29e6a` corrects the nominal figure so it can only
ever bill more, never less.

**And the honest number changes what the binding constraint is, so read this
before adjusting anything.** Two charges in today's log:

- task `ba533b4b`, attempt 2: **1,925,257 tokens, $2.8124, PRICED**, meaning that
  is real reported usage and not an estimate
- task `7d9c053f`: 1,725,600 tokens, unpriced, i.e. the new duration-scaled
  nominal for a killed iteration

So a long worker run genuinely costs about 1.7M to 1.9M tokens, and the old flat
150k was under-billing it by roughly **thirteen** times, not the fivefold I first
estimated from the 506k-1.04M figures. The consequence: at 3.25M per five hours,
the window affords roughly **one and a half long runs**, so the loop will
self-block after about two attempts whether or not they succeed. That is not a
bug in the ledger; it is the ledger finally telling the truth.

Which means the next move is NOT to raise the ceiling reflexively. The reason a
run costs 1.9M is that it burned a full wall without its search tools, and
`1ccaebd1` is what addresses that. Fix the waste first, re-measure, and only then
decide whether 3.25M is the wrong number. Raising it now would buy more of the
same failure at a higher price.

**The loop did get further after my morning fixes**: iterations now run 1440s
rather than 900s and a second iteration happened at all, one of which succeeded
(`iter 2: exit=0, tokens=773257`). So it is no longer single-shot.

### The budget arithmetic is already live, and NO restart is needed

I first wrote here that `queue_runner.sh` needed restarting to pick up
`eda29e6a`. **That is wrong and I checked it rather than leaving it.** Only
`queue_runner.sh`'s own body is held in memory; it spawns `project_ralph.sh` and
`ralph.sh` as fresh processes every sweep, so their changes are read from disk
each time. Which also means the loop was already running the repair agent's
uncommitted code from 10:32 this morning, before I committed it this evening.

Measured from the log just now, so the fix is proven live rather than argued:

- a task gets `max_wall=2700s`, so the raise from 1200 is in effect
- per-iteration timeouts run `1440s` then about `715s`, which is exactly
  `(2700 - reviewer reserve) * 2/3` for the first iteration and the remainder for
  the second
- so **two iterations now fit inside one wall**, where the old arithmetic made it
  single-shot every time, and one of those second iterations succeeded tonight
  (`iter 2: exit=0, tokens=773257`)

`[project_ralph]`'s own `max_wall=8220s` is the whole-sweep budget for up to
three tasks (`3 * 2700 + 120`) and is not a per-task value. Do not read it as one.

### So the one thing left here is the stranded work

**Decide what to do with 23 worktree branches.** Every `wt/development/*` branch
carries 2 to 3 commits not in HEAD. None is merged. That is roughly 50 commits of
worker output. `git branch --list 'wt/development/*'` then
`git rev-list --count HEAD..<branch>`. One of them is ticket
[1e2b7f87](workshop:ticket/1e2b7f87)'s own work from 17:26 tonight.

### A hazard to know about

When a worker runs with `wt=0` it works in the shared checkout and **commits
straight onto the operator's working branch**. Commit `6e89b97b` is a worker's.
It also ran `git add -A` and swept another agent's new file into its commit. That
is why worktree isolation matters and why the right move is to fix it rather than
disable it.

---

## 4. Thread unification: settled, decomposed, and inert

The operator's standing ask is to merge chats, agents and subagents into one
concept of threads. **This does not need designing again.** It was settled on
2026-08-29 in `docs/platform-redesign/threads-branches-and-pages-2026-08-29.md`
(model of record, plus an addendum that supersedes its own section 6), and it is
already decomposed into filed tickets.

The settled model, in one paragraph: a thread is the one entity and every thread
is chattable. Two independent fields: `armed` (off / on-message / schedule /
on-ready-ticket) decides whether it works unasked, and `role` (nullable) names a
definition. "Agent" describes a thread that is armed, not a second kind of
object. A ticket is intent, a branch is a place, a thread is an actor. A thread
has a current branch the way a shell has a working directory. Delegation is a
call graph edge (who asked whom), never a tree. There is no orchestrator agent
and there should not be one.

### Why "6/6 steps landed" is misleading

`check_plan_landed.py` asserts STRUCTURE: a column is declared, a page file is
absent. Every one of those assertions is true. But measured against the live
database:

- **`armed` is `off` on all 240 threads. `role` is null on all 240.**
- **The two columns never reach Postgres at all.**
  `chat_log._LIST_RELEVANT_META_FIELDS` is
  `{title, _seed_title, archived_at, mode, model, agent_id, context_key, branch_slug}`.
  Neither `armed` nor `role` is in it, so an arming patch does not even trigger
  the mirror write, and `db/chat_repo.py` never writes either column.
- **There is no way to arm a thread anywhere in the UI.** `pages/Roles.tsx` is
  the only caller of the arming endpoint, and it renders only for
  `assignedThreads()`, which filters to threads that are already armed or
  already carry a role. With zero in either state the control appears for zero
  threads. The only thing that can arm a thread only shows up on threads that
  are already armed. `Roles.tsx` even tells the operator to "arm one from Chats",
  where no such control exists.
- **The app-chats payload drops both fields.** `routes/apps.py` declares
  `armed: str = "off"` and `role: str | None = None` on the response model, with
  a comment citing the design doc, and `services/apps.py` never populates them.
  So the defaults always win and an app's Chats tab could not show an armed
  thread even once the column starts being written. This is a live bug.
- **The second entity is still routed and editable.** `pages/AgentDetail.tsx`
  (1171 lines) plus `components/MakerPanel.tsx` (505) and `lib/agentTriggers.ts`
  (85) serve full CRUD over 11 markdown agent files. Good news: **nothing in the
  UI navigates to them.** Only a breadcrumb entry in `components/appNav.tsx`
  declares the routes, and `Roles.tsx` only reads the templates catalog. So the
  deletion is far cheaper than the ticket's "deep" estimate.

So the model landed as a column and a page rather than as a working loop. The
work is not undesigned and not unstarted. It is inert.

### The tickets that already own it

[1edde3ba](workshop:ticket/1edde3ba) finish merging chats, agents and subagents
into one concept (its premise is partly stale: `AgentBuild.tsx` no longer
exists). [df606813](workshop:ticket/df606813) a thread records who asked it
(**its acceptance criterion is unsatisfiable as written**: it greps for
`context_key`, which is the live scope key on all 240 rows, not the thread-join
field it means). [e35a808a](workshop:ticket/e35a808a) a thread's role narrows its
turn. [9fc4ce67](workshop:ticket/9fc4ce67) delete the agent fork routes.
[d5a051c8](workshop:ticket/d5a051c8) consolidation becomes a scheduled job.
[2f1b6a66](workshop:ticket/2f1b6a66) armed on-ready-ticket, **already built,
close it**. [5d004289](workshop:ticket/5d004289) armed schedule, carries a
do-not-work-as-written note. [427a86fe](workshop:ticket/427a86fe) the branches
lens reads GET /leases, genuinely undone.
[1d779456](workshop:ticket/1d779456) and [65cb8c6f](workshop:ticket/65cb8c6f) are
**done and should be closed**; their blocked_reason and acceptance are stale.

Recommended order: close the three stale ones first (about thirty minutes of
adjudication, and it removes three false prerequisites), then `9fc4ce67`, then
`e35a808a`, which is what makes `role` mean anything.

### The genuinely undesigned parts, and there are only four

1. **Thread-to-thread send.** Decided as the one thing to build in
   `one-thread-many-senders-2026-08-27.md` section 10 and still absent. An
   orchestrating thread can list what it delegated and cannot say anything to it.
2. **Subagents folded into the thread model.** `spawn_background` creates a
   **Job**, not a thread: `services/background_runs.launch` takes the parent
   `thread_id`, creates zero threads, and reports back via
   `chat_drain.request_continuation`. This is why "subagent" survives as a third
   concept.
3. **The worktree / branch / lease interaction model.** The operator explicitly
   did not want this decided unilaterally.
4. **One sentence reconciling "the one entity"**, which is claimed for the app,
   the thread and the ticket across two current documents.

---

## 5. Why a project-scope agent cannot work, and it is four separate faults

The operator's words: "it is constantly hitting denied for different tool calls,
and I'm constantly having to approve things... it is still talking about global
scopes... It should not be asking me for permissions when it is in auto mode."

One sentence of diagnosis: **filesystem confinement shipped and nothing else
moved with it.** The sandbox renamed the agent's world to the mountpoint, and
every other system that reasons about "where is this agent" still reasons in host
paths.

- **The denials are the operator's own hook.** `~/.claude/hooks/egress-guard.py`
  classifies a build context by testing cwd against `_BUILD_ROOTS` under
  `~/workshop`. The sandbox chdirs the turn to the mountpoint, all three tests
  fail, and control falls through to a hard deny (`exit 2`). Its pattern also
  matches the bare token `https`, so it blocked a heredoc whose TEXT contained a
  URL, and a read-only diagnostic of its own logic in which the host string was
  split (the denial named the host as `gith`). Six denials in the operator's
  project chat, three of them not network calls at all. That file is the
  operator's, outside the repo, and guards financial data:
  [eb30f479](workshop:ticket/eb30f479). **Its stated Output is unachievable as
  written** and the correction is noted on it: the registry it wants to read is
  not bound into a non-repo-root sandbox, so the platform must TELL the guard the
  scope and its sensitivity through the turn env.
- **The timer genuinely does not exist there.** A `project:<slug>` chat gets
  **zero** `mcp__workshop__*` tools, for the reason in section 3.
  [1e2b7f87](workshop:ticket/1e2b7f87). Three findings were added to it today,
  and one changes what a fix means: the bwrap root is a **writable tmpfs**, and
  the turn keeps a host `WORKSHOP_ROOT`, so a `mkdir -p` of the host `.runtime`
  path SUCCEEDS and vanishes with the turn. Merely making the server startable
  yields a `wake_me` that reports "armed" and never fires. A lying timer is worse
  than an absent one. Any implementation must assert the wakeup is readable BY
  THE BACKEND after the turn ends.
- **"Global scopes" is literal.** `~/.claude` is bind-mounted read-write, so the
  host-level `CLAUDE.md` loads into a project turn as user memory, and
  `~/.claude/projects/-workspace/` is shared by every project sandbox (it held
  3442 session transcripts). The turn env is `os.environ.copy()` and
  `turn_env` remaps only values under the scope root, so a project chat carries
  `WORKSHOP_DATABASE_URL` with its password and four other secrets.
  [aa9a87ab](workshop:ticket/aa9a87ab), [60fa5411](workshop:ticket/60fa5411).
- **Auto mode asks by construction.** `needs_approval_channel` covers every mode
  except `bypassPermissions`, so `auto` always gets `--permission-prompt-tool
  stdio`. The picker's hint said "Acts without asking", which was simply false.
  Fixed in `12839052`. Separately, a **classifier** denial is TERMINAL: it never
  reaches that channel, so there is no approve affordance and no attribution.
  That is exactly "idk what's denying it", and it is now
  [ad1206bf](workshop:ticket/ad1206bf).

The honesty half landed today: `_grants_workshop_tool` now asks whether the MCP
spec is reachable from the scope root. Verified against the operator's own
project: `development` reports reachable True and keeps every tool;
`project:new-project-5ee20d` reports False, so L1 stops claiming `wake_me`. **The
manifest still names six other tools unconditionally on the same unreachable
server**, so a project turn's tool map is still false, just less so. That is
`1e2b7f87`'s to close.

---

## 6. Why work does not route itself, with numbers

The operator asked why orchestration is not automatic. It is not a missing
design.

1. **149 tickets pass every eligibility gate except one.** `tasks.next_ready`
   requires the ticket's `assignee` to equal the sweep's worker. 138 sit on
   `assignee: operator`, which is not a worker; 11 are assigned to the maker. The
   chat filing path sets `execution`, `source` and `status` and even pokes the
   wake file, and never sets `assignee`. **Changing that default is the
   operator's call**, because it is exactly his stated fear of "blindly executing
   all of the past tickets". I did not touch it.
2. **The executor was broken**, section 3.
3. **The prompt contradicted itself three ways** inside one assembled turn, and
   the most specific instruction won, resolving to "do it here in the chat with
   subagents". The clause that tipped it was one **I tightened yesterday**: it
   over-corrected from "do not hand work back" into "do not dispatch". Replaced
   in `01eae38a` by one ordered rule, with `b8d8abb2`'s intent preserved verbatim
   and pinned by a test.
4. **There is no tool to open a thread, file a ticket, or start a run.** Auto
   mode told the agent to "file it and start it without asking" and neither verb
   exists. The mode text is now truthful; the tools are not built.

---

## 7. What is fixed that the operator reported

All nine items from yesterday were independently verified FIXED, with residuals
noted. Of today's reports:

- **Opening a chat from a project left the app.** Fixed. New route
  `/apps/:appSlug/chats/:threadId`, one `appChatPath` spelling, the app's
  breadcrumb and tab bar survive, Back returns to the tab. Screenshot-verified at
  390 and 1440.
- **The list was unbounded with broken infinite scroll.** Fixed to the design the
  repo had already settled (`frontend/CLAUDE.md` rule 9, the overflow state): a
  bounded list with "Show more" and "Showing 20 of 134". Both
  IntersectionObserver blocks deleted, which is what caused the scroll to jump.
- **"armed" is not a word he approved.** The filter is **removed**, not
  relabelled, because it was measured at 0 of 240 and a filter whose every answer
  is empty is noise. Every other user-visible occurrence rewritten in ordinary
  words ("Runs on its own"). Final grep: no user-visible `armed` string remains
  in `frontend/src`, `mobile/` or `packages/ui`. The column and API field are
  untouched.
- **Local time.** Done end to end, ticket
  [64b34f64](workshop:ticket/64b34f64) and implemented. UTC canonical and never a
  preference, then the user's setting, then the installation's declaration, then
  UTC labelled as UTC. `services/clock.py` has a section headed "THE BROWSER IS
  NOT A RUNG", so switching browsers cannot thrash it; the browser is offered
  once as a click-to-use suggestion. `wake_me` reads the same resolver, and the
  clock is on the per-turn contract rather than the once-sent manifest so it
  cannot go stale.
- **A chat in a project shows in that project.** Verified end to end, not just at
  the column: `GET /apps/<uuid>/chats` returns the operator's own thread.
  `app_id` is derived at row insert and re-derived on every sync, and the sweep
  clears as well as sets. Ticket
  [f9145fc7](workshop:ticket/f9145fc7) is in review with zero attempts and
  **should probably be closed**. Two caveats: the route takes a uuid and answers
  a slug with a 500 rather than a 404; and a thread with zero messages has no
  relational row, so it cannot appear until its first message.
- **The lost message.** The notice and Retry work for a `failed` send. The gap is
  that a restored `sending` entry is never converted to `failed`, so it returns
  as a permanently dim bubble with no notice and no Retry, which is exactly what
  he described. Diagnosed onto [b4a1dc33](workshop:ticket/b4a1dc33).

---

## 8. Decisions waiting on the operator

1. **The database downgrade** in section 0. Blocked by the classifier, not by
   judgment.
2. **`spawn_background`: open a thread, or stay a job?** Recommended: open a
   thread and arm it once. Then `opened_by_thread_id` is the one delegation edge,
   delegated work becomes chattable, and "subagent" stops being a third concept,
   which is literally the standing ask. Do not build `df606813`'s column before
   this is decided: under the other option it is null on everything that matters.
3. **`assignee` at file time**, section 6 item 1. This is his "blindly execute
   the past tickets" fear, so it is his call. A safe shape exists: set it only on
   newly filed tickets in an armed scope, never retroactively on the 138.
4. **Worktree mode.** It is what breaks the loop today, and disabling it would
   put unattended workers back into the shared tree writing on his branch.
   Recommended: keep it and let `1ccaebd1` prove itself once the budget frees,
   rather than turning isolation off.
5. **Does an operator message into an armed thread silence it?** Open since
   2026-08-27. Small, blocks nothing, but the arming control's copy depends on
   it. Recommended: merge and continue, and say nothing.
6. **The word that replaces "armed" in data**, if any. The UI no longer says it.
   Renaming the column is a migration and was not done.

---

## 9. Traps that cost time today

- **`output bytes=0` after a kill means nothing.** Section 3.
- **The git index is shared between concurrent agents.** Two agents' work landed
  in one commit (`12839052`) whose message describes half of it, and one agent's
  own commit came out EMPTY as a result and was reset off. Nothing was lost, but
  a report claiming loss was written. Always `git diff --cached --stat`
  immediately before committing, and never `git add -A`.
- **A killed agent leaves a poisoned index.** Section 2.
- **cwd drifts between Bash calls.** A `cd` persists; `.runtime/ralph.log`
  silently becomes unreadable from `backend/`. Use absolute paths.
- **The classifier will block legitimate work** and it is terminal. It blocked a
  transcript copy and the alembic downgrade today. Do not route around it; say so
  and hand it over.
- **`apply.sh`'s `live_ahead` branch assumes an orphan schema is inert.** It said
  so today over a reachable 500. Ticket `0ab5f960`.
- **A raw `SessionLocal()` reads empty.** Pin an owner: `ownership.pin_operator()`
  then `tenancy.current_org()` and `db.engine.tenant_session`. For the admin URL,
  `db-admin.env`, which the backend deliberately does not load.
- **`--type` never persists on a ticket.** `type` is `None` on 202 of 202 open
  tickets; the `work_type` column exists and nothing reads it. So the bug/feature
  shape lives only in the body labels.
- **`project_tasks.py` needs a dir argument** (`add . "<text>"`, `note . <id>
  "<text>"`) and refuses a ticket with no runnable acceptance, which is correct.

---

## 10. Filed or amended today

Filed: [ad1206bf](workshop:ticket/ad1206bf) denial attribution,
[4c6989e8](workshop:ticket/4c6989e8) uncommitted worker migrations,
[0ab5f960](workshop:ticket/0ab5f960) apply.sh live_ahead,
[64b34f64](workshop:ticket/64b34f64) user timezone (also implemented).

Amended with measured evidence: [1e2b7f87](workshop:ticket/1e2b7f87) (three
findings, including the writable tmpfs and the worktree blast radius),
[eb30f479](workshop:ticket/eb30f479) (its Output is unachievable as written),
[b4a1dc33](workshop:ticket/b4a1dc33) (the restored-`sending` gap),
[32837886](workshop:ticket/32837886) (the two meanings of "Auto").

Queue: 244 open. 201 todo, 15 in review, 14 blocked, 13 parked, 1 draft.
