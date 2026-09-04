# Audit: what actually runs, what is built and unreachable, what is broken (2026-08-23)

Status: CURRENT. A measurement pass, not a design. Every claim below cites a
`file:line` or a command and its output. Nothing here was taken from a design
document; where a design document disagrees with the tree, the tree wins and the
document is named.

Method: read from `/workspace`, backend modules imported through
`backend/.venv/bin/python` with `services.ownership.pin_operator()` first.

---

## 0. The one-paragraph answer

The work loop is alive and has been structurally incapable of delivering
anything for 36 days. `scripts/queue_runner.sh` is resident and sweeps every ~30
seconds, but the last job that recorded a completed task ended
**2026-07-18T20:28:26Z**; the 2,066 loop jobs since have recorded `tasks_done: 0`
every time. The cause is not idleness. Two reviewer ratchets
(`check_no_app_names.py`, `check_module_layout.py`) measure the WHOLE tree with no
diff scoping and both currently FAIL on pre-existing debt, so every run is
rejected for defects it did not introduce, trips the same-rejection thrash guard
on the second iteration, and exits 6. The work it had already committed is then
stranded on a locked per-task branch that nothing merges: **nine reviewer-approved
commits sit unmerged on `wt/development/*` right now, and the `change` table that
was designed to surface them has zero rows, ever.** Five of those nine belong to
tickets already marked `done`.

---

## 1. The agent system, end to end

### 1.1 Is there a resident queue runner alive right now? Yes. Two of them.

```
$ ps aux | grep queue_runner
<service-user>  1060001  bash /workspace/scripts/queue_runner.sh --dir /workspace          (started 10:42 today)
<service-user>  1169974  bash ~/workshop/scripts/queue_runner.sh --dir ~/workshop  (started Aug22)
```

Both target the same tree through two different path strings. The
one-runner-per-tree invariant does not hold, because the flock key is a hash of
the path STRING:

```
$ python -c "from services import work_driver as w; ..."
/workspace              -> 508c753f681d | queue-runner-508c753f681d.lock   resident: True
~/workshop  -> 09978cb2b5d3 | queue-runner-09978cb2b5d3.lock   resident: True
```

`backend/services/work_driver.py:89` derives the lock name from `queue_key(dir_str)`.
Ticket `ff147867` ("Move the queue-runner lock off /tmp so one-runner-per-tree
holds across a sandbox boundary", status `in_review`) is a true statement of a
live defect. The sweep log shows both running against one tree with DIFFERENT
worktree modes, alternating every ~30s:

```
$ tail .runtime/ralph.log
[11:39:38] sweep start dir=/workspace              ... worktrees=0
[11:40:04] sweep start dir=~/workshop  ... worktrees=1
```

Side effect: **357 stale lock files** accumulate in `.runtime/`
(`ls .runtime/ | grep -c 'queue-runner-.*\.lock'` -> `357`), one per task
worktree ever created, never reaped.

### 1.2 The last time the loop actually worked a ticket

```
$ python (over .runtime/jobs.jsonl, 9,917 lines)
done rows                       3248
done rows with tasks_done > 0    423
LAST worked: {"id":"job_1784406074384_8830","status":"done",
              "ended_at":"2026-07-18T20:28:26.291858+00:00","tasks_done":2}
job starts since 2026-07-18T20:28: 2304  [('loop', 2066), ('manual', 238)]
```

**36 days.** Every one of the 2,066 loop jobs since then recorded `tasks_done: 0`.

This is not because the loop did nothing. It picked tickets up and committed
work. It is because the sweep's success leg is never reached (§1.4).

### 1.3 Registered agents, triggers, and runs

`agents_registry.list_agents(include_retired=True)` returns **four**:

| slug | role | template | trigger | can it fire? |
|---|---|---|---|---|
| `development-maker` | maker | None | `[]` | no trigger at all |
| `<bridge-agent>` | triager | None | `discord`, `disabled_reason: "discord trigger has no channels"` | disabled |
| `memory-keeper` | keeper | None | `cron 0 */6 * * *` | no dispatcher exists (§1.5) |
| `<bridge-agent>-commute` | None | None | `manual` | operator-only |

- **Every agent carries `template: None`.** The adoption path built in R3 has
  never been exercised on this instance. This confirms
  `agents-and-memory-reassessment-2026-08-23.md` §2.2 independently.
- **Zero agents have produced a run since 2026-07-14.** Last start per
  `agent_id` in `.runtime/jobs.jsonl` is `2026-07-14T22:32:38Z` for the newest of
  six historical agent ids, none of which is in the registry today. Everything
  since is `agent_id: ""` (the anonymous loop).
- `.runtime/agents/` contains a `notes-app-maker/` directory that
  `list_agents()` does not return, and does NOT contain `memory-keeper`, which it
  does. The definition directory and the registry disagree in both directions.

### 1.4 The dispatch is coherent on `role`; the failure is downstream

**The `runtime` axis is genuinely gone.** `ROLES = ('maker','reviewer','triager','keeper','broker')`
(`backend/services/agents_registry.py`, `ROLES`), and
`agents_registry.py:23` states "THERE IS NO `runtime` FIELD". A grep of
`backend/services`, `backend/routes` and `frontend/src` for `runtime` outside
`container_runtime`, `local_runner` and the `.runtime/` path prefix finds no
surviving field. CLAUDE.md's claim holds.

**Two of the five roles are declared and have no agent and no runner path
exercised:** `reviewer` and `broker` have zero registered agents. `triager` has
one, disabled. So the closed enum has 5 members and 1 (`keeper`) with a
trigger that cannot fire, and 1 (`maker`) with no trigger at all.

### 1.5 The dispatcher that fires every declared trigger is not installed

`scripts/dispatch_triggers.sh` is the only thing that turns a declared `cron`,
`discord`, `triager` or `keeper` trigger into a run
(`scripts/dispatch_triggers.sh:53-54` resolve `MEMORY_KEEPER` and `TRIAGER`).

```
$ crontab -l
/var/spool/cron: No such file or directory        <- no crontab installed at all

$ cat infra/cron/workshop.crontab | grep -v '^#'
0 14 * * *  @WORKSHOP_ROOT@/scripts/daily_todo_digest.sh
0  7 * * *  @WORKSHOP_ROOT@/scripts/autonomy_report.sh
*/2 * * * *  @WORKSHOP_ROOT@/scripts/frontends_watchdog.sh
```

**`dispatch_triggers.sh` is not in the crontab template**, and the template is
not installed. `scripts/queue_runner_watchdog.sh` is not in it either.

```
$ tail -1 .runtime/dispatch.log
[2026-08-04T22:02:21-07:00] [dispatch] autonomy engaged (...); dispatching nothing
$ ls -la .runtime/dispatch.log
-rw-r--r-- 1 <service-user> <service-user> 19445 Aug  4 22:02
```

The dispatcher last ran 19 days ago and its last non-refusal
(`dispatched 0 sweep(s)`) was 2026-07-18T14:40. Consequently:

```
$ cat .runtime/trigger-state.json
{ "agent:memory-keeper:cron:0 */6 * * *": "2026-07-18T18:00:01+00:00" }
```

One key. Frozen 36 days. This is the same date the loop stopped completing tasks
and the same date the dispatcher went quiet. **2026-07-18 is when the autonomous
system stopped, and nothing has reported it since.**

---

## 2. Why the loop delivers nothing: the causal chain, measured

### 2.1 The unattended selector currently rejects 100% of ready work

```
$ tail .runtime/ralph.log
[project_ralph] no ready tasks
[project_ralph] sweep end ... done=0 blocked=0 failed=0 parked=0 in_review=0 open=77 waiting=13
```

77 open, zero ready. Measured through the real predicates:

```
ready todo (tasks.is_ready)                 38
  of those, execution == "code"              9      (26 are execution: None, 2 judgement, 1 external)
  of those, ticket_readiness.check().ok      0
readiness fail reasons:
  {'check': 'acceptance',  'why': 'the ticket does not say what done looks like'}  x9
  {'check': 'boundaries', 'why': 'the ticket does not say which files it may change'} x8
```

`backend/services/tasks.py` `next_ready(..., unattended=True)` requires
`execution == "code"` AND `ticket_readiness.check(t)["ok"]`. Both filters are
correct in intent. Together, right now, they pass nothing.

**The remedy that exists is broken.** `scripts/classify_open_tickets.py` is the
documented operation for setting `execution` on the 26 unclassified tickets. It
crashed on every invocation ever:

```
$ python3 scripts/classify_open_tickets.py
services.ownership.OwnerUnset: no data owner is pinned for this call.
```

`tasks.list_tasks` resolves the relational branch through
`tenancy.org_and_user()`, and the script never pinned an owner. **Fixed in this
pass** (one import plus `ownership.pin_operator()`); it now prints a 44-ticket
proposal that would classify ~19 tickets as `code`.

### 2.2 Two reviewer ratchets fail unconditionally on pre-existing debt

`scripts/reviewer.sh:466-471` runs `check_no_app_names.py` with **no diff
precondition at all** and **no `--only-changed`**. `scripts/reviewer.sh:368-374`
runs `check_module_layout.py` on any `backend/**.py` change, also whole-tree.
Both fail today:

```
$ python3 scripts/check_no_app_names.py ; echo $?
  literals  319  platform lines naming an app
FAIL literal_sites: 309 -> 319. The rule is that this only goes down.
1

$ python3 scripts/check_module_layout.py ; echo $?
=== 3 metric(s) regressed past the pinned baseline
    function_local_imports: 552 -> 572
    largest_scc_size:       110 -> 124
    big_file_count:          22 -> 24
1
```

Both failures are in committed `main`, not in the uncommitted working tree. I
verified by extracting `main` to a clean tree (`git archive main | tar -x -C
/tmp/headtree`, `git init` + `git add -A`, then running the gate): still 319.

Therefore **every reviewer run emits `app_name_in_platform_source`, and every
backend-touching run also emits `module_layout_regression`, regardless of what
the worker did.** The worker fixes what it can, the reason set repeats, and
`scripts/ralph.sh:1128-1131` fires the thrash guard:

```
$ grep 'no-progress PARK' .runtime/ralph.log | tail
[ralph] no-progress PARK: same reviewer rejection x2 (reasons=app_name_in_platform_source|hardcoded_identity) at iter=2
[ralph] no-progress PARK: same reviewer rejection x2 (reasons=app_name_in_platform_source|module_layout_regression|hardcoded_identity) at iter=2
```

All-time rejection histogram from `.runtime/ralph.log`:

```
8 raw_browser_storage        5 app_name_in_platform_source   4 opaque_response_field
3 module_layout_regression   3 design_violations             2 ts_typecheck_fail
2 route_returns_5xx          2 response_model_regression     2 process_globals_regression
2 hardcoded_identity         2 generated_client_stale        1 duplicate_helper
```

**This exact bug has already been diagnosed once in this repo and only half
fixed.** An archived learning at `.runtime/learnings/archive/project-workshop.jsonl`
reads, verbatim:

> retired: false premise. These recorded the reviewer rejecting three tickets for
> raw_browser_storage, which was a defect in the CHECK, not in the work:
> check_no_raw_storage.py ran repo-wide with no --only-changed and its allowlist
> had never followed the storage engine into packages/ui/src/storage.ts (fixed
> 05513ba, 2026-08-21). All three tickets' work had in fact landed and committed.

The same repo-wide-with-no-diff-scoping defect is live in two more gates today.

### 2.3 Approved work is committed to branches nothing merges

`scripts/ralph.sh:22-34` documents exit 6 as "in-run no-progress / thrash park".
The receipts show runs that COMMITTED and whose worker emitted the completion
sentinel still exiting 6:

```
{"id":"job_1787507454817_2808","status":"failed","exit_code":6,
 "evidence":{"files":["scripts/project_ralph.sh","scripts/ralph.sh"],
             "commits":["66df9a63..."],"iterations":2,"tokens":855699,
             "summary":"The implementation looks correct. The reviewer approved.
                        Let me emit the completion sentinel.\n<promise>COMPLETE</promise>"}}
```

Nine such commits are unmerged right now:

```
$ for b in $(git branch --list 'wt/development/*'); do git log main..$b --oneline; done
bdefb46 2026-08-22 20:07  fix(ui): useEditMode.save() catches onSave rejections via saveError state
e08a086 2026-08-22 20:25  fix(gate): exclude /api/notes platform sense and gate script's own literals
225c02f 2026-08-23 01:27  feat(files): put desktop file tree in page column via directory takeover
5bf6a2a 2026-08-23 01:54  fix(chat): capture linked-thread ref synchronously before scope resolver awaits
624b151 2026-08-23 02:10  tools(registry): the four declaration sources read from one enumeration
d1031a7 2026-08-23 02:52  feat(tools): command tools run through the existing sandbox and deny list
8173739 2026-08-23 03:11  feat(settings): add Tools page with CRUD for user tools
b643c66 2026-08-23 08:06  fix(gc,worktrees): reclaim locked worktrees; unify the merged definition
66df9a6 2026-08-23 11:02  fix(touches): attribute files only to the worker that committed them
```

Every one of the twelve `wt/*` worktrees is `locked` (`git worktree list`).

**Three of those commits are the fixes for the very defects blocking the loop**,
stranded:
- `66df9a6` fixes the touches gate; ticket `5d557e2a` is `blocked` with
  `blocked_reason: "touches violation: wrote outside the task's declared globs ... -> scripts/project_ralph.sh, scripts/ralph.sh"`.
- `b643c66` "reclaim locked worktrees"; ticket `2b0b794a` ("A locked worktree
  cannot be reclaimed, so its branch slug is taken forever") is `parked`.
- `bdefb46` fixes `useEditMode.save()`; ticket `30777f91` is `parked`.

I tested whether the stranded gate fix would clear the blocking gate. It does
not: `git show e08a086:scripts/check_no_app_names.py` run against the current
tree still reports `319`. Ticket `7d3cea01` rests on solid ground.

### 2.4 The `change` table designed to surface all of this has zero rows

`scripts/project_ralph.sh:440-465` (`publish_or_park`) is explicit: a supervised
local-only sweep leaves the branch unmerged and opens a `change` for the
operator, and "ACCEPTING it is what merges it".

```
$ python -c "from services import changes; print(len(changes.list_changes()))"
0
```

**Zero changes have ever been opened.** Therefore
`backend/services/auto_merge.py`, `backend/services/merge_policy.py`,
`backend/services/change_gates.py`, `backend/services/claim_check.py`,
`backend/routes/changes.py` and the `ChangeReview` UI have never processed a
single record. The chain is wired (`change_gates.py:421-423` calls
`auto_merge.consider`; `project_ralph.sh:397` shells `python -m
services.change_gates`), but the success leg that would call
`open_change_for_task` is never reached, because exit 6 takes the failure leg.
`.runtime/merge-policy.json` does not exist, so no scope has ever been given a
merge policy either.

`grep -rn merge_policy` outside its own module finds hits ONLY in
`backend/tests/test_auto_merge.py` and prose docstrings. It is fully built,
extensively tested, and has never run.

---

## 3. The ticket backlog, measured

`tasks.list_tasks(Path('/workspace'))` -> **175 rows**, one scope
(`development`; every row's `owner` is `operator` and `scope` is `None`).

| status | count |
|---|---|
| done | 77 |
| todo | 46 |
| cancelled | 21 |
| parked | 12 |
| in_review | 11 |
| draft | 4 |
| blocked | 2 |
| doing | 2 |
| **open (non-terminal)** | **77** |

`execution` across the 77 open: `code` 35, **`None` 32**, `external` 7,
`judgement` 3. The 32 unclassified can never be selected unattended (§2.1).

### 3.1 Tickets that are ALREADY DONE in the tree

**`8b172649` (`todo`, p40) "Two GET routes under /chat/attachments answer 500 and
block every backend ticket".** Done.
```
$ python3 scripts/check_routes_answer.py --json
{"get_routes": 228, "answered": 200, "failed": [], "skipped": 7, "unprobed": 21}
```
`failed: []`, zero 5xx. The gate this ticket says is blocked now passes.

**`e8c53c78` (`todo`, p0) "Give each unattended run its own worktree so the loop
stops resetting the shared tree".** Done. Per-task worktrees exist and were used
today: `scripts/project_ralph.sh:1176-1183` sets `WT_MODE=1` and `WORK_DIR=$WT_DIR`,
and `git worktree list` shows nine `branches/development/t-<taskid>` worktrees
created 2026-08-22 and 2026-08-23.

**Five tickets are marked `done` while their only implementation is on an
unmerged branch** (branch -> ticket mapping via `wt/development/t-<task_id>`):

| ticket | status | branch commit | in `main`? |
|---|---|---|---|
| `d8676bba` Add a Tools settings page | **done** | `8173739` | Tools.tsx IS in main (branch cut from a stale base; commit is redundant) |
| `3ed6cecd` Put the desktop file tree in the page column | **done** | `225c02f` | NOT in main |
| `59ed115c` Fold MemoryDrill onto the kit FileDrill | **done** | `e08a086` | NOT in main, and the commit does not match the ticket title at all (it is the no-app-names gate fix) |
| `6b45b4fb` Resolve tool names against the registry | **done** | `624b151` | NOT in main |
| `9a1cdab5` Make a user-defined command tool actually run | **done** | `d1031a7` | NOT in main |

`MemoryDrill` is still present in `main:frontend/src/pages/Memory.tsx`, so
`59ed115c` is marked done and is not done. Branch-to-ticket attribution is also
wrong for that one: the branch named for it carries an unrelated commit.

### 3.2 Tickets resting on a FALSE PREMISE

Measured by extracting cited paths with `tasks.cited_paths()` and resolving each
against the tree with `backend/`, `frontend/` and basename fallbacks. 19 of 77
cite something that does not resolve literally; after removing notation artifacts
(`services/x.py` for `backend/services/x.py`, `runtime/x.json` for `.runtime/x.json`),
**five are genuine**:

| ticket | unresolvable citation | reality |
|---|---|---|
| `7d3cea01` | `scripts/app_names_baseline.json` | the file is `scripts/no_app_names_baseline.json` |
| `307544a7` "Ship the two surfaces that make a triage decision visible" | `frontend/src/pages/Work.tsx` | no such page exists |
| `203b0eb8` "Read 'needs you' from tickets" | `services/run_inbox.py` AND `backend/services/run_inbox.py` | no such module; the real one is `backend/services/inbox.py` |
| `d8ab8749` (draft) | `backend/models/ticket.py` | the model lives in `backend/db/models.py` |
| `00777fcd` (draft) | `backend/models/scope.py` | no such module |

**A sixth, by stale number rather than stale path:** `7c3b804d` "Explain or reduce
the backend dependency cycle that grew to **110** modules". It is now **124**
(`check_module_layout.py`). The ticket understates the problem and its baseline
is already breached.

### 3.3 Blocked and parked: are the reasons still true?

- `5d557e2a` **blocked**, reason `touches violation ... -> scripts/project_ralph.sh, scripts/ralph.sh`.
  **No longer true in substance:** the fix (`66df9a6`, "attribute files only to
  the worker that committed them") is written, reviewed and committed, on an
  unmerged branch. The ticket is blocked by its own unlanded fix.
- `2b0b794a` **parked**. Same shape: `b643c66` is its fix, unmerged.
- `30777f91`, `a89d3bd4` **parked**. Same shape: `bdefb46`, `5bf6a2a` unmerged.
- `cb311b87` **blocked**, `incomplete (rc=2) after 3 attempt(s) (cap 3)`. True;
  its branch has 0 unmerged commits.
- **Six SEC-* tickets parked with `host administration; not agent-executable`**
  (`cc2e83df`, `f128c5dc`, `5c893b55`, `30d2d99e`, `911be1b2`, `dad50a93`). Reason
  still true and permanent. They inflate the "open" count by 8%.

### 3.4 Two tickets have been stuck `doing` for 41 hours because `reclaim()` is inert

```
97b0f04a  doing  lease 2026-08-22T02:00:14Z  pid 3467240   (dead)
5f0cf756  doing  lease 2026-08-22T01:24:31Z  pid 3388426   (dead)
$ ps -p 3467240,3388426   ->  no such processes
$ tasks._is_stale_claim(t, now, 3600)  ->  True   (for both)
$ tasks.reclaim(Path('/workspace'))    ->  []
```

`next_ready` calls `reclaim(root)` on every poll, twice a minute, and it has
released nothing for 41 hours. **The cause is a split-brain store.**

`backend/services/tasks.py:1779`, the first line of `reclaim`:

```python
    raw = _read_store(root)      # <- the retired .tasks.json file store
    if not raw:
        return []
    ...
    if not any(_is_stale_claim(t, now_dt, ttl) for t in normalized):
        return []                # <- always returns here on a relational scope
```

Every other mutator in the module uses `_load(root)`
(`tasks.py:1337-1366`), which honours the relational branch. `_read_store` reads
`.tasks.json` directly. Measured:

```
tasks.is_relational(root)      True
tasks._read_store(root)        126 rows   {done 68, todo 30, cancelled 21, parked 6, draft 1}
tasks.list_tasks(root)         175 rows   {done 77, todo 46, cancelled 21, parked 12, in_review 11, draft 4, blocked 2, doing 2}
$ ls -la .tasks.json  ->  Aug 20 10:43   (3 days stale)
```

The file store has **no `doing`, no `in_review` and no `blocked` rows at all**, so
the stale-claim pre-scan can never find anything and `reclaim` returns `[]`
unconditionally on this scope. The self-healing described in its own docstring
("so a crashed/killed sweep never strands a task forever") has never fired here.

**Fix: one line, `_read_store(root)` -> `_load(root)` at `tasks.py:1779`.** NOT
applied in this pass, deliberately: both stuck tickets have `execution: code` and
`ticket_readiness.check().ok == True`, so releasing them would immediately hand a
live resident runner two chat-storage rebuilds to start. That is the operator's
call, not an auditor's.

### 3.5 The three reviewer gates reported failing on 2026-08-22

Run now, from `/workspace`, with the bare `python3` that `reviewer.sh` itself uses:

| gate | exit | result |
|---|---|---|
| `check_opaque_fields.py` | **0** | `533 schema(s) scanned, 45 opaque field(s), 33 schema(s) affected`: **no regression against baseline. FIXED.** |
| `check_routes_answer.py` | **0** | `200 of 228 GET routes answered, 0 returned 5xx, 7 skipped, 21 not probed`: **FIXED.** |
| `check_no_app_names.py` | **1** | `literals 319` vs pinned `309`. **STILL FAILING, and unconditional.** |
| `check_module_layout.py` | **1** | 3 metrics regressed. **STILL FAILING** on any backend `.py` diff. |
| `check_response_model.py` | 1 | `backend/routes/chat.py: 4 -> 7`. Scoped `--only-changed`, so it gates only diffs touching `chat.py`. |
| `check_one_definition.py` | 1 | one enum in 3 places. Scoped `--only-changed` in the gate. |
| `check_duplicate_helpers.py` | 1 | 2 new duplicated helpers (`getJson/readJson`, `messageOf`). |

Two of the three named on 08-22 are genuinely fixed. The blocking pair today is
**`check_no_app_names` and `check_module_layout`**, and they are worse than the
old pair because neither is diff-scoped.

---

## 4. Inert mechanism sweep

### 4.1 Dead HTTP routes

Route table taken by importing the app: **478 route objects, 484 operations, 389
distinct path templates.** Cross-referenced against `frontend/src`, `mobile/` and
`packages/ui/src` (452 files), resolving per-feature base constants through the
import graph and excluding prose mentions in comments and `<code>` tags.

```
paths with >= 1 caller     287
paths with ZERO callers    102        operations with ZERO callers  118
```

`frontend/src/api/schema.gen.ts` is an `openapi-typescript` TYPE dump, not a
call-emitting client, so all 102 are "typed, never called". No router module in
`backend/routes/` is entirely unmounted.

**The largest single dead surface: `backend/routes/git.py`.** 19 of its 20
operations have no caller anywhere in the repo, including `scripts/` and
`backend/mcp/`. Only `GET /git/rev-parse` is live
(`frontend/src/components/holders/useHolders.ts:142`). Dead:
`git.py:293` `/git/status`, `:300` `/git/log`, `:309` `/git/diff`, `:328`
`/git/ahead-behind`, `:345` `/git/is-ancestor`, `:355,363,371` `/git/branches`,
`:380,387,396,403,410` the `/git/worktrees` family, `:418` `/git/commit`, `:425`
`/git/merge`, `:436` `/git/snapshot`, `:442` `/git/checkout`, `:449` `/git/clean`,
`:458` `/git/push`.

Priority-area dead routes:

| area | route | file:line |
|---|---|---|
| memory / search | `POST /knowledge/{scope_key}/search` | `backend/routes/knowledge.py:162` |
| memory / search | `POST /knowledge/recall` | `backend/routes/knowledge.py:168` |
| memory | `GET /notes/scopes` | `backend/routes/notes_all.py:297` |
| files | `GET|PUT /apps/{app_id}/files/content` | `backend/routes/apps.py:346,364` |
| agents | `GET /agents/tools` | `backend/routes/agents.py:730` |
| agents | `GET /agents/{agent_id}/runs` | `backend/routes/agents.py:1683` |
| agents | `GET|PUT /agents/build/{slug}/todo` | `backend/routes/agents.py:944,951` |
| agents | the whole build-agent task CRUD | `backend/routes/agents.py:1002,1007,1021,1035` |
| autonomy | `POST /agents/autonomy/disable|enable` | `backend/routes/autonomy.py:61,71` |
| autonomy | `GET|POST /agents/autonomy/merge-policy` | `backend/routes/autonomy.py:453,475` |
| review | `POST /changes`, `PATCH|DELETE /changes/{id}` | `backend/routes/changes.py:236,257,285` |
| triage | `POST /trackers/triage/{candidate_id}/unignore` | `backend/routes/trackers.py:266` |
| triage | `POST /trackers/pull`, `POST /trackers/conflicts/.../resolve` | `backend/routes/trackers.py:277,295` |
| budget | `GET /budget/overrides`, `POST /budget/categorize` | `backend/routes/budget.py:95,101` |
| branches | `GET /worktrees/{slug}/{branch}/diff` | `backend/routes/worktrees.py:187` |
| branches | `GET /branches/{id}/timeline`, `POST /branches/{id}/events` | `backend/routes/branches.py:169,178` |
| permissions | `GET /base-apps/permissions/{app_id}` | `backend/routes/base_apps.py:366` |
| stores | `POST /stores/{store_id}/write` | `backend/routes/stores.py:261` |

Whole-router dead-from-the-UI (services imported in-process instead):
`backend/routes/commute.py` 18 dead ops, `backend/routes/birds.py` 22 dead ops,
`backend/routes/projects.py` 8 dead ops, `backend/routes/base_apps.py` 6 more.

**Do not delete these.** Dead from the UI, driven by machines:
`relay.py:94,103,130,150` (`cron_agent.sh:216`), `events.py:149,220`
(`scripts/chat_event_bridge.py`), `birds.py:625` (BirdNET-Go webhook),
`plaid_webhook.py:15`, `main.py:503` `/healthz`, `push.py:55,63,68`.

**Broken callers (frontend calling routes that do not exist):**
- `GET|POST /api/auth/invite/{token}` at
  `frontend/src/pages/apps/<slug>/standalone/main.tsx:77,129`. `backend/routes/auth.py`
  declares no `invite` route; `grep -rn invite backend --include=*.py` finds none.
  Every invite link renders `state = "badinvite"` and redemption always fails.
- `fetch("/api/agents")` at `frontend/src/pages/ProjectDetail.tsx:538` omits the
  trailing slash. FastAPI 307s to `/agents/`, `frontend/vite.config.ts:195` has
  already stripped `^/api`, so the redirect resolves against the SPA origin and
  returns index HTML with a 200; `r.json()` throws. The panel silently shows an
  error. Every other caller uses the slash (`Agents.tsx:257`, `Projects.tsx:137`).

### 4.2 Public service functions with no caller

An AST + whole-corpus reference sweep (823 files across `backend`, `frontend/src`,
`scripts`, `packages`, `mobile`; tests and `.venv` excluded) finds **226 public,
non-route functions in `backend/services` and `backend/routes` with zero
reference outside their own defining file.** Script: `scratch/inert2.py`.

The ones that matter, each verified individually with
`grep -rnw <fn> ... | grep -v <own module> | grep -v tests` returning **0**:

| file:line | function | why it matters |
|---|---|---|
| `backend/services/triager.py:113,141,174,211,257` | `validate_decision`, `state_path`, `due_notes`, `parse_rules`, `apply_rules` | **the entire note-triager surface.** Nothing outside the module references any of it. The only reachable path is `scripts/triager_run.sh`, which only `dispatch_triggers.sh:54` invokes, and that is not scheduled (§1.5). |
| `backend/services/learnings.py:1103` | `prune_stale` | the time-based learnings GC. Only reference outside the module is `scripts/verify_memory.py:358-376`, which *checks* it, does not call it. |
| `backend/services/learnings.py:1166` | `sweep_quality` | **zero references anywhere**, including tests. |
| `backend/services/work_review.py:97` | `change_for_task` | the lookup the merge-on-accept path needs. Never called; consistent with 0 change rows. |
| `backend/services/scope_execution.py:176` | `armed_scopes` | no way to enumerate what is armed. |
| `backend/services/usage_ledger.py:376` | `plan_budgets` | budget planning, unreachable. |
| `backend/services/budget_store.py:213` | `save_overrides` | pairs with the dead `GET /budget/overrides`. |
| `backend/services/skills.py:56,102,118,131,168` | the whole `skills` module surface | built, no caller, no route. |
| `backend/services/tool_registry.py:637,722` | `user_tools`, `tool_ids` | |
| `backend/services/stores.py:305,324` | `register_app_stores`, `register_from_manifests` | app store registration from manifests never runs. |
| `backend/services/semantic_index.py:1009,1016` | `enable_maintenance`, `owns_maintenance` | |
| `backend/services/tasks.py:801,1030` | `awaiting_may_expire`, `lane_gaps` | |
| `backend/services/tracker_bindings.py:196,224` | `target_for`, `writes_allowed` | |

**Correction to the standing claim about the learnings GC.** The archive is not
empty: `.runtime/learnings/archive/` holds 21 rows, last written 2026-08-23T11:11.
But every row's reason comes from the **write path**, `record()`'s dedupe,
supersede and quality gate (`learnings.py:662,692,1098`), never from the
collector:
```
'superseded by a revision (sim 0.84)' x4, 'updated in place ... (sim 0.82)' x2,
'refused by the quality gate: machine status token, not instructional guidance' x2, ...
```
The precise statement is: **`prune_stale` and `sweep_quality` have never been
invoked by any production path.** Nothing archives on age or on quality sweep.

### 4.3 State that has not advanced in over 7 days

| age | path | what it means |
|---|---|---|
| 36 d | `.runtime/trigger-state.json` (2026-07-18) | one key; no agent trigger has fired since |
| 36 d | `.runtime/memory-keeper/workshop.watermark` = `2026-07-18T18:00:01Z` | memory keeper frozen |
| 36 d | `.runtime/memory-keeper/workshop.proposed.json` (1 id, `cc2e61fcd30f`) | one proposal, never resolved |
| 36 d | `.runtime/circuit-breaker.json`, `.runtime/store-policies.json` (2026-07-18) | |
| 36 d | 4x `.runtime/receipt-job_1784*` + 4x `criteria-spec-job_1784*` | orphaned receipts from the last working sweep |
| 19 d | `.runtime/dispatch.log` (2026-08-04) | the dispatcher itself |
| 39 d | `.runtime/proposal-seen.json`, `.runtime/curriculum.log`, `.runtime/graduate_proposals.log`, `.runtime/work_generator.log`, `.runtime/work_verifier.log` (2026-07-15) | a whole curriculum/work-generation subsystem, silent |
| 49 d | `.runtime/ports.json`, `.runtime/app-deploys.json` (2026-07-05) | |
| 59 d | `.runtime/finance-cache.json` (2026-06-25) | |
| 70 d | `.runtime/grants.json` (2026-06-14) | the grants store, despite a live `/grants` UI |
| 75 d | `.runtime/event-dedup.json` (2026-06-09) | |
| 93 d | `.runtime/supervisor-archived-offset.json` (2026-05-22) | |
| 3 d | `.tasks.json` (2026-08-20) | still read by `tasks.reclaim` (§3.4) |
| n/a | `.runtime/merge-policy.json` | **does not exist**; no scope has a merge policy |
| n/a | 357 x `.runtime/queue-runner-*.lock` | never reaped |

---

## 5. Duplicate implementations

`check_one_definition.py` has **no baseline by design** (`scripts/check_one_definition.py:22-25`:
"re-derives its findings from the tree every run"). It reports one enum in three
places and **under-reports**: `{autonomous, reactive, supervised}` is declared at
least five times.

```
MODES            backend/services/scope_execution.py:45
AUTONOMY_MODES   backend/services/triggers.py:376
AUTONOMY_MODES   frontend/src/lib/autonomy.ts:19
AutonomyMode     backend/routes/agents.py:56                        (Literal alias, missed by the checker)
CHECK constraint backend/db/migrations/versions/966ddcaa35ae_...:110 (missed)
CHECK constraint backend/db/migrations/versions/def2cbf58df8_...:165 (missed)
```
`backend/services/agents_registry.py:168` is the one that did it right
(`AUTONOMY = triggers.AUTONOMY_MODES`). `backend/services/triggers.py:370-375`
carries a comment admitting the collapse was left undone.

`check_module_layout.py` against `scripts/module_layout_baseline.json`:

| metric | baseline | now |
|---|---|---|
| function_local_imports | 552 | **572** |
| largest_scc_size | 110 | **124** |
| big_file_count | 22 | **24** |
| upward_violations | 48 | 47 (improved, not repinned) |

The baseline's `_measured_against: 244` vs 272 files scanned now: the counts are
not size-normalised. **A 124-module SCC is over half the backend**; the layering
this gate checks is nominal.

`check_duplicate_helpers.py`: baseline pins 10 groups, current run finds 11. Two
new (`getJson/readJson`, `messageOf`). The ratchet keys on file COUNT per body
hash, not membership, so it silently missed group `58a8760ab334` swapping
`lib/chat.ts` out for `pages/settings/CustomInstructions.tsx`, and
`scripts/duplicate_helpers_baseline.json` is internally inconsistent for group
`e7c4d28bf9e5` (`"files": 2` while listing three `where` entries).

### 5.1 The named concepts

**File trees: 3, all live.** `packages/ui/src/FileTree.tsx:132` (desktop) and
`packages/ui/src/FileDrill.tsx:100` (phone) are a deliberate pair sharing the pure
layer `packages/ui/src/fileTree.ts`, but they still duplicate `messageOf`
(`FileTree.tsx:128` / `FileDrill.tsx:96`). The genuine third is
`frontend/src/pages/AgentBuild.tsx:164` `FileTreeNode`, a hand-rolled recursive
`<li>` tree with inline `paddingLeft` styling, feeding a different backend
(`/api/agents/build/{slug}/tree`, `AgentBuild.tsx:381`). Ticket `100ba5d2` says
"two hand-rolled file trees survive"; there is one.

**File reads over HTTP: 8 doors, not 4.**

| route | implementation | live |
|---|---|---|
| `GET /api/files/content` `backend/routes/files.py:108` | `app_files.read_file` `backend/services/app_files.py:308` | yes |
| `GET /api/apps/{app_id}/files/content` `backend/routes/apps.py:346` | the SAME `app_files.read_file` | **dead** |
| `GET /api/docs/file` `backend/routes/docs_index.py:152` | own `read_text()` at `:161` | **dead** |
| `GET /api/agents/build/{slug}/file` `backend/routes/agents.py:908` | own `read_text()` at `:915` | yes |
| `GET /api/projects/{slug}/file` `backend/routes/projects.py:1073` | own `read_text()` at `:1082` | **dead** |
| `GET /api/<app>/notes/{note_path}` `backend/routes/notes.py:159` | `notes_vault.read_note:224` | yes |
| `POST /api/stores/{store_id}/read` `backend/routes/stores.py:219` | `stores.read:581` | yes |
| `GET /api/knowledge/{scope_key}/notes/{note_id}` `backend/routes/knowledge.py:128` | `knowledge.get_note:768` | yes |

Path containment is implemented **four times with four different rules**:
`app_files.safe_path` (`backend/services/app_files.py:234`),
`docs_index._resolve_within_root` (`backend/routes/docs_index.py:143-149`),
`agents._safe_resolve`, and an inline
`root.resolve() not in target.parents` at `backend/routes/projects.py:1076-1077`,
which is the weakest of the four.

**Search: 3 backend engines, only 2 reachable over HTTP.**
1. Semantic (embeddings + BM25), `backend/services/semantic_index.py:1365` `search()`.
   **No HTTP route.** Callers: the MCP stdio server
   `backend/mcp/workshop_query_server.py:263` and its own CLI at `:1532`. The most
   expensive search in the system is invisible to the web UI.
2. Plaintext term-count, `backend/services/knowledge.py:1297` `_plaintext_search`
   -> `POST /knowledge/{scope}/search` and `/knowledge/recall`, both of which are
   **dead routes** (§4.1); reached indirectly by `GET /api/notes?q=`
   (`backend/routes/notes_all.py:285`), which calls it with `limit=1000` per scope
   and intersects ids: an O(scopes x notes) full read on every list request.
3. Line-grep, `backend/services/notes_vault.py:405` -> `GET /api/<app>/search`.

Title-substring search is frontend-only and copied **five** times:
`frontend/src/pages/memory/memoryTree.ts:298`, `frontend/src/pages/Memory.tsx:377`
and `:816`, `frontend/src/pages/apps/tabs/AppFilesTab.tsx:407`,
`frontend/src/pages/Chats.tsx:100-103`.

**Scope -> directory: 7 mappings, and two disagree.** `backend/wsplatform/paths.py:125`
`scope_root` documents itself at `:132-138` as "the SINGLE mapping every other
per-scope path derives from". It is not. `agent:x` resolves to
`runtime_dir()/agents/x` via `paths.py:154` but to `.runtime/agent-scopes/x` via
`backend/services/task_scope.py:345` `_agent_scope_dir`. **Two directories, one
scope key**, and the tree confirms it: `.runtime/agents/` and
`.runtime/agent-scopes/` both exist with different members. `project:<slug>` goes
through a path CONVENTION in one place (`paths.py`) and the registry's recorded
`dir` field in another (`backend/services/build_agents.py:84`); they agree only
while every project happens to sit under `projects/`. Others:
`knowledge.scope_dir:106`, `task_scope._raw_root_for_context:76`,
`task_scope._workshop_scope_dir:206`, `task_scope._maker_root:296`,
`task_scope.key_for_root:211`.

**Date formatting: 5 canonical, ~15 bypasses.** `packages/ui/src/time.ts` has
`timeAgo:33`, `timeAbsolute:56`, `timeAbsoluteShort:65`, `formatDuration:76`,
`timeAgoSeconds:109`. Bypassing it: `Agents.tsx:54` `activityLabel` and
`Chats.tsx:82` `archivedLabel` are the same function with a different first word
and a different today-test (`diff < 24h` vs `toDateString()` equality): **they
disagree at 11pm**. Plus `Usage.tsx:73`, `apps/Birds.tsx:197`,
`birds/forecastUtil.tsx:37`, `birds/Devices.tsx:34`, `birds/ForecastWeek.tsx:31,35`,
`notes/NoteView.tsx:32`, `commute/CommutePages.tsx:774`, `budget/Projection.tsx:99,201`.
Five duration formatters: `chat/chatScope.ts:174` `formatElapsed`,
`chat/work/ticketCard.ts:157` `clockText` (whose own docstring at `:152-153` says
"Byte-identical to formatElapsed under an hour, deliberately", an invariant
nothing enforces), `chat/grouping.ts:374,405`, `lib/holders.ts:103`.

**Status mapping: 13 frontend tables, 5 backend, one true triplicate.**
`frontend/src/pages/Home.tsx:206` `projectStateKind`,
`frontend/src/pages/ProjectDetail.tsx:127` `stateKind` and
`frontend/src/pages/Projects.tsx:114` `stateKind` are three identical bodies.
`check_duplicate_helpers` pins only two of them (group `852a764bc54b`); the
`Projects.tsx` copy escapes because it annotates its parameter and reorders two
cases, changing the normalised hash: exactly the blind spot that script's own
header warns about at `scripts/check_duplicate_helpers.py:41-46`.

### 5.2 New duplicates found

- **Five atomic-JSON-write implementations**: `backend/services/tasks.py:336`,
  `knowledge.py:226`, `notes_vault.py:243`, `app_files.py:363`, plus the same
  three lines inside `agent_slots.py:113` and `usage_ledger.py:348`.
- **Six project-slug -> directory resolvers**, four of which raise different
  exception types for the same failure: `build_agents.py:76` (returns `None`),
  `local_runner.py:184` (returns `str`, raises `DeployError`), `app_runner.py:163`
  (returns `Path`, raises `AppError`, byte-for-byte `local_runner`'s body with
  two words changed), `worktrees.py:143` (raises `ValueError`), `routes/projects.py:646`,
  `agents_registry.py:415` (bulk). Only `app_files.py:135` `project_root` delegates.
- **Two env vars for one projects registry.** `backend/wsplatform/paths.py:85`
  and five others read `$WORKSHOP_PROJECTS_REGISTRY`;
  `backend/services/worktrees.py:84-88` reads **`$WORKSHOP_REGISTRY_PATH`**
  instead. Set one and not the other and the Branches page reads a different
  `projects.json` than everything else. `worktrees.py:79-82` contains a comment
  describing the PREVIOUS incarnation of this same bug.
- **Six runtime-root readers, two behaviours.** `backend/wsplatform/paths.py:43`
  `runtime_dir()` applies `.expanduser().resolve()`. `stores.py:202`,
  `ownership.py:33`, `knowledge.py:47`, `run_report.py:46`, `db/chat_backfill.py:39`
  do not. Set `WORKSHOP_RUNTIME=~/wsdata` and half the system looks for a literal
  `./~/wsdata`. On top of that ~30 modules spell `WORKSHOP_ROOT / ".runtime" / ...`
  inline and ignore `$WORKSHOP_RUNTIME` entirely.
- **Three ticket-row projections** with three different column sets:
  `backend/db/repo.py:164` `_row_to_legacy` (33 keys), `backend/db/ticket_sync.py:274`
  `_row_view` (28 keys), `backend/db/ticket_sync.py:200` `_legacy_view`, backed by
  two independent SELECTs. A new column has to be added in three places.
- **Seven HTTP-JSON fetch wrappers and no `frontend/src/lib/http.ts`.** `j<T>(r)`
  is byte-identical in `lib/marketplace.ts:31`, `lib/memoryPolicy.ts:34`,
  `lib/refs.ts:28`, `lib/voiceApi.ts:5`, `pages/settings/CustomInstructions.tsx:18`;
  the URL-taking variant twice more at `components/holders/useHolders.ts:25` and
  `pages/memory/api.ts:6`.
- **A dual-write chat store with a parity checker.** `backend/db/chat_repo.py` is
  a second Postgres-backed chat store selected at runtime by
  `$WORKSHOP_CHAT_LIST_PG` at `backend/routes/chat.py:337`, with
  `backend/services/chat_list_pg.py` and `backend/db/chat_list_parity.py` existing
  only to compare the two.

Refuted: two markdown renderers (`packages/ui/src/Markdown.tsx:51` is the single
renderer), two job logs (`backend/services/jobs.py:60` is the only one),
`scripts/project_tasks.py` as a third ticket store (it is a CLI shim that
`from services import tasks` at `:87`), `tree_access.py` as a file reader (it is
mount arbitration), `git_service.py` as a file reader (it has no content read).

---

## 6. What the gates say right now

```
$ bash scripts/check_design.sh                                          EXIT 0
gen-tokens: color files up to date (8 palettes x 2 modes), scales in sync
api-snapshot: public API matches api/ui.api.md (v0.5.25)
design: clean
check_undefined_css: 0 referenced-but-undefined class(es) (1093 defined, 778 referenced), pinned at 0

$ npm run typecheck            (from the repo root)                     EXIT 0
> npm run typecheck --workspace @workshop/ui && cd frontend && tsc -b --noEmit
> @workshop/ui@0.5.25 typecheck / tsc -p tsconfig.json --noEmit
(clean, no diagnostics)

$ backend/.venv/bin/python scripts/check_no_app_names.py                EXIT 1
  content   102  tracked files that ARE app source
  imports    20  platform files importing an app module
  literals  319  platform lines naming an app
FAIL literal_sites: 309 -> 319. The rule is that this only goes down.

$ bash scripts/apply.sh --check                                         EXIT 0
apply: /workspace
  ok  ui bundle (b615f5956f14)
  ok  backend (30b020b69678)
  ok  vite env (frontend/.env) (c1ce9ad3c90f)
  ok  public edge code (c066fcb10a6b)
  ok  last backend restart: restarted (pid 974714 -> 1106347) at 2026-08-23T10:51:28
in sync
```

**The public bundle is NOT stale. The operator is looking at current code.**
Design and typecheck are clean. The failing gate is `check_no_app_names`, and it
is the one that blocks every autonomous run.

---

## 7. The honest top ten

Ordered by consequence. Items 1-7 are **wrong** (built, referenced, broken or
inert). Items 8-10 are **not built yet**.

### WRONG

**1. Two whole-tree reviewer ratchets fail on pre-existing debt, so every
autonomous run is rejected for something the worker did not do.**
Evidence: `scripts/reviewer.sh:466-471` runs `check_no_app_names.py` with no diff
precondition and no `--only-changed`; `:368-374` runs `check_module_layout.py` on
any backend `.py` diff. Both exit 1 today (`319` vs `309`; three regressed
metrics), verified against a clean extract of `main`. Rejection histogram in
`.runtime/ralph.log` shows `app_name_in_platform_source` x5 and
`module_layout_regression` x3, which then trip the same-rejection thrash guard at
`scripts/ralph.sh:1128-1131` and exit 6. The identical defect was already
diagnosed and fixed once for `check_no_raw_storage` (archived learning,
`.runtime/learnings/archive/project-workshop.jsonl`, commit `05513ba`).
**Fix:** give both gates the `--only-changed`/baseline-at-BASELINE treatment
`check_response_model` and `check_one_definition` already have, so a run is
judged on its own diff. Blocks everything else.

**2. Nine reviewer-approved commits are stranded on locked branches and the
`change` record designed to surface them has never been written.**
Evidence: `git log main..wt/development/*` -> 9 commits, 2026-08-22 20:07 to
2026-08-23 11:02, all worktrees `locked`. `changes.list_changes()` -> `0`.
`grep -rn merge_policy` outside its module hits only `backend/tests/test_auto_merge.py`.
The success leg `scripts/project_ralph.sh:440-465` that would call
`open_change_for_task` is never reached because exit 6 takes the failure leg.
**Fix:** fixing item 1 lets the success leg run; then verify one change row
appears end to end. Until then no autonomous work can ever land.

**3. `tasks.reclaim()` reads the retired `.tasks.json` on a relational scope, so
crashed claims are never recovered.**
Evidence: `backend/services/tasks.py:1779` uses `_read_store(root)` while every
other mutator uses `_load(root)` (`tasks.py:1337-1366`). `is_relational` -> `True`;
`_read_store` -> 126 rows with **no `doing` status at all**; `list_tasks` -> 175
rows with 2 `doing`. `reclaim()` -> `[]` while `_is_stale_claim` -> `True` for
both. Tickets `97b0f04a` and `5f0cf756` have been `doing` with dead pids
(3467240, 3388426) for 41 hours.
**Fix:** one line, `_read_store(root)` -> `_load(root)`. Not applied here because
both tickets are unattended-eligible and would start running immediately.

**4. No trigger dispatcher is scheduled, so every declared agent trigger is dead.**
Evidence: `crontab -l` -> `/var/spool/cron: No such file or directory`;
`infra/cron/workshop.crontab` contains three lines and `dispatch_triggers.sh` is
not one of them; `.runtime/dispatch.log` last written 2026-08-04;
`.runtime/trigger-state.json` has one key frozen at `2026-07-18T18:00:01Z`.
Consequence: memory-keeper 36 days frozen, note-triager never fired, `<bridge-agent>`
disabled.
**Fix:** add `dispatch_triggers.sh` (and `queue_runner_watchdog.sh`) to
`infra/cron/workshop.crontab` and run `scripts/install_cron.sh`.

**5. Five tickets are marked `done` whose only implementation is not in `main`,
and two open tickets are already done.**
Evidence: branch-to-ticket mapping in §3.1. `59ed115c` is `done` while
`MemoryDrill` is still in `main:frontend/src/pages/Memory.tsx`, and the branch
named for it carries an unrelated commit (`e08a086`, the gate fix). Conversely
`8b172649` (`todo`, "/chat/attachments answer 500") is done:
`check_routes_answer --json` reports `"failed": []`. And `e8c53c78` (`todo`,
"give each unattended run its own worktree") is done and in use today.
**Fix:** re-derive ticket status from `git merge-base --is-ancestor` on the
recorded branch rather than from the sweep's exit code.

**6. The unattended selector passes zero of 77 open tickets, and the script that
would fix it crashed on every invocation.**
Evidence: 38 ready, 9 with `execution == "code"`, 0 passing
`ticket_readiness.check` (all 9 fail `acceptance`, 8 fail `boundaries`); 32 of 77
open tickets have `execution: None`. `scripts/classify_open_tickets.py` died with
`OwnerUnset` before printing anything, ever.
**Fixed in this pass** (the owner pin). Still needed: run it with `--apply`, and
add acceptance criteria and `touches` globs to the `code` tickets.

**7. Two resident queue runners share one tree, and 357 lock files have never
been reaped.**
Evidence: `ps` shows `queue_runner.sh --dir /workspace` and
`--dir ~/workshop`; `work_driver.queue_key` hashes the path string, so
they get different locks and `is_resident` is `True` for both. They alternate in
`.runtime/ralph.log` with different worktree modes (`worktrees=0` vs `worktrees=1`).
`ls .runtime/ | grep -c 'queue-runner-.*\.lock'` -> `357`.
**Fix:** key the lock on the resolved git common dir, not the path string
(ticket `ff147867`, already `in_review`); reap lock files with the worktree.

### NOT BUILT YET

**8. Semantic search has no HTTP route, and the two knowledge search routes that
do exist have no caller.** `backend/services/semantic_index.py:1365` `search()` is
reachable only from MCP (`backend/mcp/workshop_query_server.py:263`) and its own
CLI. `POST /knowledge/{scope}/search` (`backend/routes/knowledge.py:162`) and
`POST /knowledge/recall` (`:168`) have zero frontend callers. Ticket `00592ec5`
("Give the operator a real search over his own workshop") is unbuilt and correct.
**Fix:** one route over `semantic_index.search`, one search surface in the UI.

**9. The learnings collector has never been invoked, and there is no window into
the store.** `learnings.prune_stale` (`:1103`) is referenced only by
`scripts/verify_memory.py`, which checks it rather than calling it;
`learnings.sweep_quality` (`:1166`) has zero references anywhere. The archive's 21
rows all come from the write path's dedupe/supersede/quality gate
(`learnings.py:662,692,1098`). Tickets `652dc696` and `8a79d530` are correct.
**Fix:** schedule `prune_stale` alongside item 4; add the read surface.

**10. Nothing detects an inert mechanism, which is this repo's recurring defect.**
226 public functions in `backend/services` and `backend/routes` have no reference
outside their own file; 102 of 389 route paths have no caller; five `.runtime`
state files have not advanced in over a month. There is a ratchet for inline hex,
for opaque fields, for module layout and for app names, and none for "this code
cannot run". Ticket `7c095e56` names it exactly.
**Fix:** a `check_reachability.py` ratchet over (a) route path -> frontend/mcp/script
caller and (b) public service symbol -> non-test reference, pinned like the others.

---

## 8. Change made in this pass

One, and it is reported rather than assumed:

- `scripts/classify_open_tickets.py`: added `ownership` to the import at `:55`
  and `ownership.pin_operator()` as the first statement of `main()` after
  argument parsing. The script is print-only by default (`--apply` gates all
  writes) and previously raised `services.ownership.OwnerUnset` on every
  invocation before printing anything. Verified: it now prints a 44-ticket
  proposal.

Deliberately NOT changed: `backend/services/tasks.py:1779` (§3.4, item 3). The
one-line fix is correct, but applying it during an audit would release two
unattended-eligible tickets to a live resident runner.
