# Review: plan vs reality, 2026-08-19 to 2026-08-29

- Date: 2026-08-29
- Method: the tree was measured. Every claim below carries a file path, a command
  output, a SQL result or a commit hash. Where a doc, a ticket or a commit
  message says one thing and the tree says another, the tree wins and the
  disagreement is recorded.
- Reviewed at: `HEAD = 79592119`, branch `feat/client-state-consolidation`.
- Scope: the 500 commits since 2026-08-19, the design docs in this folder, the
  ticket store in Postgres, and the running installation.

Ticket ids in this document are `ticket.external_id` prefixes. They are NOT
`ticket.id`, which is a UUID. Querying by UUID prefix returns nothing, which is
worth writing down because it cost a pass here.

```
select t.external_id, ws.name, t.title
from ticket t left join workflow_state ws on ws.id = t.state_id
where t.external_id like '<prefix>%' and t.deleted_at is null
```
run against `WORKSHOP_DATABASE_ADMIN_URL`. The application role is unprivileged
and RLS-filtered, so an unset tenant reads every table as empty and looks like an
empty database rather than an access failure.

---

## 1. The goal, in one paragraph

Workshop is one operator's self-hosting personal-ops platform, and the redesign
has one destination: **work should move from a conversation to a ticket to an
agent that executes it and reports back, without the operator managing the
handoffs, with context shared across chats so nothing has to be re-explained**
(`HANDOFF-2026-08-26.md` §1). Riding on that are three constraints the operator
stated himself and which the platform is measured against: an **app** is the one
container a user names and there is no second "project" object
(`app-is-the-entity-2026-08-20.md` §1); **no platform source file may name a
specific app**, because production must carry no trace of this operator's own
apps (same §1, enforced by `scripts/check_no_app_names.py`); and services must be
RESTful and independent of whatever the frontend happens to be. The 2026-08-29
model of record adds the consolidation rule that the last ten days are actually
about: **a change deletes the surface it replaces in the same commit that adds
the new one**, because five design generations have each left their pages behind.

**The docs disagree about which object is "the one entity", and no doc
reconciles them.** `app-is-the-entity-2026-08-20.md` says the app is the entity.
`threads-branches-and-pages-2026-08-29.md` §1 says "A thread is the one entity".
Its own addendum then says "THE PRIMARY AXIS IS THE TICKET". These are reconcilable
(a container, an actor, and a unit of work are different levels) and the addendum
gets most of the way there, but the phrase "the one entity" is used three times
for three different objects across two current documents, and a reader arriving
cold will pick whichever they read last. That should be settled in one sentence
somewhere rather than left to inference.

---

## 2. The plan of record, and the supersession chains

### Current, in reading order

1. `HANDOFF-2026-08-29.md` (checkpoint)
2. `threads-branches-and-pages-2026-08-29.md` **including its addendum** (model)
3. `app-is-the-entity-2026-08-20.md` (still current for the app/project question;
   its §9 migration is not built)

### Chain A: entity and pages

- `entity-model-2026-07-18.md` §2 and §9.4 (App vs Project split)
  -> **superseded** by `app-is-the-entity-2026-08-20.md`. Deliberate, cited in
  that doc's header and in `CLAUDE.md`.
- `entity-model-2026-07-18.md` page structure
  -> **superseded** by `threads-branches-and-pages-2026-08-29.md` §6. Deliberate.
- `primitive-roles-2026-07-18.md` (Agents as a roster)
  -> **superseded** by `threads-branches-and-pages-2026-08-29.md` §1 and §2.
  Deliberate.
- `one-thread-many-senders-2026-08-27.md`, thread half
  -> **superseded** by the 08-29 model. Its message half **shipped**: ticket
  `17d81555` reads Done and commit `bc2caaeb` adds the `sender` field. That doc's
  own header already says "PARTLY BUILT, and superseded on the thread half",
  which is the cleanest supersession note in the folder.
- `threads-branches-and-pages-2026-08-29.md` §6 (branch-first Work tab)
  -> **superseded by its own addendum**, same day: ticket-first, branch as a
  lens. Deliberate and explicitly argued.

### Chain B: sequencing and handoffs

- `agents-and-memory-reassessment-2026-08-23.md` §6.3 (order only)
  -> `the-plan-2026-08-23.md`. Deliberate.
- `HANDOFF-2026-08-26.md` and `EXECUTION-PLAN-2026-08-27.md`
  -> `HANDOFF-2026-08-29.md`, "both of whose measurements are now stale".
  See §6 of this review: the execution plan's named next item did not survive
  the supersession.

### Chain C: worktrees, branches, leases

- `branches-worktrees-and-environments-2026-08-20.md` §14 and
  `workspace-ownership-and-leases-2026-08-22.md` §7, §10, §14 (models)
  -> `worktree-as-the-place-2026-08-28.md` settles the interaction contract, and
  changes neither model
  -> `threads-branches-and-pages-2026-08-29.md` §3 renames the concept: "the
  interface says branch, worktree is an implementation detail". Deliberate, and
  the commits `06bf48ca` and `a782c114` record the intermediate correction.

### Chain D: compaction

- `compaction-both-paths-plan-2026-08-20.md`,
  `context-compaction-research-2026-08-20.md`,
  `custom-context-management-proposal-2026-08-20.md`,
  `chat-context-and-compaction-2026-08-25.md`
  -> **closed by operator instruction on 2026-08-29** and the subsystem deleted
  in `5be52599`. `backend/services/compaction_adapter.py` no longer exists.
  Deliberate, and the handoff explicitly forbids reopening it.

### The blanket supersession, which is the risky one

`HANDOFF-2026-08-29.md` says: "Read this file, then
`threads-branches-and-pages-2026-08-29.md` including its addendum, **and nothing
else. The rest of `docs/platform-redesign/` is history.**"

That is 138 other files declared history in one line, including three written in
the preceding 36 hours and still carrying "proposal" or "design only" status:
`worktree-as-the-place-2026-08-28.md`, `who-is-speaking-in-a-thread-2026-08-28.md`,
`memory-is-files-2026-08-28.md`, and `development-as-a-project-2026-08-29.md`,
whose stated deliverable ("what is dev-only today and ships anyway, enumerated
from the tree") is not restated anywhere in the handoff. A blanket line is not
a supersession chain; it is a way for an unfinished design to disappear without
anyone deciding to drop it.

---

## 3. Verified state: what is genuinely built and working

Each line names what was checked.

1. **`development` is a real app row, named "Development".**
   SQL against the app table:
   `('development', 'Development', 'terminal', 'app', 'development', True, None, None)`
   for `(slug, name, icon, kind, project_slug, dev_only, archived_at, deleted_at)`.
   Commits `5082d28b` (row created from the `self: true` declaration rather than
   from a slug literal) and `79592119` (`_icon()` falls back to the linked
   project's declared icon, and the title loses the parenthetical that was
   rendering the monogram as "D("). `WORKSHOP_DEV_MODE=1` is set in
   `~/.config/workshop/instance.env:31`, so the `dev_only` filter admits it.

2. **One alembic head, applied.** `alembic heads` -> `b9f3e2d07a16 (head)`;
   `alembic current` -> `b9f3e2d07a16 (head)`; the `alembic_version` table holds
   the same. The "six heads" the model doc named as blocking are gone
   (`7c7ffb0e`).

3. **`dev_only` is on the wire.** `backend/routes/apps.py:66`
   (`dev_only: bool = False`), `backend/services/apps.py:91,107,121` (the filter
   and the two 404 guards), `frontend/src/api/schema.gen.ts:9366`. Gate
   `scripts/check_dev_mode_off.sh` is wired into `scripts/reviewer.sh:678` with a
   `dev_mode_off_regression` reason at :960.

4. **The semantic index covers notes.**
   `.runtime/users/6/semantic/index.meta.jsonl` holds **12,679 chunks**, of which
   `note` = **350**, `doc` = 5841, `chat` = 5274, `ticket` = 1214. Rebuilt
   2026-08-29 17:59. This corroborates `d380dfdd`; the handoff's "345" is within
   a rebuild of the measured number.

5. **Memory is files, path-aware, split system/user.**
   `shared/knowledge/development/` holds 202 markdown files across `system/`
   (192), `user/` (1) and `archive/` (3). Tickets `fe266258`, `1ce4b3c8`,
   `8357515c` all read Done; `ChatFiles.tsx` is gone from `frontend/src/pages`.

6. **The Branches page is deleted.** `frontend/src/pages/Branches.tsx` does not
   exist; `frontend/src/App.tsx:140` routes `/branches` to a redirect to
   `/chats`. Commit `dd4659bb`.

7. **Chats is a flat recency list with the branch as an inline tag.**
   `frontend/src/pages/Chats.tsx:253` groups by date, and `:418-421` renders
   `branch_slug` as a row tag. Commit `b26162a2`. This matches the settled model.

8. **Workshop's own compaction fold is deleted.**
   `backend/services/compaction_adapter.py` is absent; `_fold_summary`,
   `_land_fold` and `_land_fold_when_quiet` have no definition anywhere under
   `backend/services` or `backend/routes`. Commit `5be52599` removed 481 lines
   from `routes/chat.py` alone. (But see §4C.)

9. **Every thread carries a current branch binding.** `chat_thread` has a
   `current_branch_id` column and it is non-null on **218 of 218** live threads.
   The "a thread has a current branch, the way a shell has a working directory"
   half of the model already has its column.

10. **The gates are green.**
    - `python3 scripts/check_no_app_names.py` exits 0: content 106, imports 24,
      literals 479, homepath **0**.
    - `python3 scripts/check_prompt_layers.py`: "platform prompt text is clean
      (0 offense(s), floor 0)".
    - `npm run typecheck` from the repo root: clean, both the kit and the
      frontend.
    - `bash scripts/check_design.sh`: "design: clean", "check_undefined_css: 0
      referenced-but-undefined class(es) ... pinned at 0".
    (Two of these are green for reasons worth knowing: see §8.4.)

11. **Message provenance shipped.** Ticket `17d81555` Done, commit `bc2caaeb`,
    `sender` on the message and on the wire.

---

## 4. Claimed but not true

This is the section that matters. Each item is something a doc, a ticket or a
commit message asserts, that the tree contradicts.

### A. The public bundle is STALE, so the operator is not looking at today's work

`bash scripts/apply.sh --check`:

```
apply: /workspace
  STALE   ui bundle: dist=56d91a35abf0 source=32436119f946
  STALE   backend: running=523639ec6f4f source=53711a838043
  ok      last backend restart: restarted (pid 1571398 -> 1680734) at 2026-08-29T17:34:47
drift found (run: bash scripts/apply.sh)
```

`bash scripts/check_design.sh` independently prints
`warn: the public bundle is STALE (dist=56d91a35abf0, source=32436119f946)`.

The last backend restart was **17:34:47**. The final commit of the day,
`79592119` "fix(apps): an app declares its icon and the platform reads it, **so
Development is visible**", is timestamped **17:42:09** and was never applied.
Neither was `5be52599`, the compaction deletion, nor `5814939f`, nor `7e2eb3d0`.

`CLAUDE.md` is unambiguous about this: *"A UI change is not delivered until the
operator's origin serves it ... 'the public bundle is STALE' means the operator
may be looking at old code. It is not noise. Never report a UI task complete over
it."* Section 2 of the handoff lists "a dock tile, an Apps card" as built and
verified. That is true in source and false on the operator's origin, which is the
exact failure `CLAUDE.md` records from 2026-08-09. **This is the single most
consequential item in this review and it is fixed by running one command.**

### B. The `worktree` table contains no real worktree, and `display_name` is null on every row

Eight rows, all of them smoke and demo residue:

```
verify-1     /tmp/wsv/root/branches/demo/verify-1       allocated
t-26c69d40   /tmp/wsv/root/branches/demo/t-26c69d40     allocated
t-24430668   /tmp/wsv/root/branches/demo/t-24430668     allocated
d4875115     /tmp/wsv/root/branches/demo/d4875115       allocated
t-0256ec42   /tmp/wsv/root/branches/demo/t-0256ec42     allocated
t-45fff2e7   /tmp/wsv/root/branches/demo/t-45fff2e7     allocated
t-smoke1     /workspace/scratch/leasesmoke/.../t-smoke1 provisioned
t-smoke2     /workspace/scratch/leasesmoke/.../t-smoke2 provisioned
```

`/tmp/wsv/root/` does not exist. `scratch/` is gitignored throwaway. Meanwhile
the **seven real worktrees** under `branches/development/` have **zero rows**.

`display_name` is **NULL on all eight**. Commit `ecb0f115` and handoff §2 both
say "Branch `display_name` survives the wire; the ORM matches the migration".
The response-model half of that is real and the schema-drift test passes, but
**nothing writes a display name to any row**, so the observable symptom the fix
was for (branches showing as hex) is unchanged. The fix repaired plumbing for
data that does not exist.

Consequence for the plan: ticket `427a86fe` (the Branches lens page) and
`130ef15e` (the app Work view) would today render six nonexistent demo
directories and none of the operator's actual branches.

All **19** lease rows are expired (`select count(*) from lease where expires_at <
now()` returns 19 of 19), the newest expiring 2026-08-28. Exactly one lease was
ever held by a `chat` holder, and it expired 2026-08-23. The `change` table has
5 rows, two of which are named `e2e-probe`. The `run` table has **0** rows.

### C. The compaction deletion left four red tests

`backend/tests/test_operation_cancel.py` still monkeypatches
`routes.chat._fold_summary`, `._land_fold` and `._land_fold_when_quiet`, all of
which `5be52599` deleted.

```
$ .venv/bin/python -m pytest tests/test_operation_cancel.py -q -p no:randomly
E   AttributeError: module 'routes.chat' has no attribute '_land_fold_when_quiet'
E   AttributeError: <module 'routes.chat'> has no attribute '_fold_summary'
4 failed, 4 passed in 0.55s
```

`5be52599` updated `test_chat_compact.py`, deleted `test_compaction_adapter.py`
and rewrote `test_compaction_working_state.py`, and missed this fourth file.
These are **new failures introduced today**, not part of the "7 pre-existing
backend test failures in the ticket area" the handoff attributes to `b130579e`.
No ticket exists for them.

This is also a violation of the model doc's own governing rule, applied to tests:
a change that deletes a surface has to delete what referenced it.

### D. The background-runner fix is not in effect

`7e2eb3d0` "runner.log 0-byte root cause + status file + reaper" is timestamped
**16:48:31**. The most recent background job directory,
`.runtime/background/job_1788050977306_9f41`, was created at **17:49:37**, an
hour later, and contains `prompt.txt` (22 bytes) and `runner.log` (**0 bytes**),
with **no `status.json`**. The only directory with a `status.json` is
`job-run-1`, a test fixture.

The proximate cause is (A): the running backend predates the fix. The fix may
well be correct. But the handoff's "`7e2eb3d0` fixed the 0-byte log" is not
observable in the tree, and the two failures compound: the fix for the thing that
made background agents untrustworthy is itself not deployed.

### E. "Seven legacy `t-<hex>` worktrees are still on disk" is wrong in both directions

Measured. `branches/development/` holds **seven** directories:

```
a89d3bd46dfc48c6bcfe4ca49c639962          <- ticket a89d3bd4, status Done
bc81b563abe14333be4e8626dc8182bd          <- ticket bc81b563, status In Review
cb311b87376f46dbab5203a5da5806d4          <- ticket cb311b87; NOT a registered git worktree
d8676bbabd924e7e8d4e6d18d48dbcdc          <- ticket d8676bba, status Done
give-the-operator-real-search-over-his-own-works
sec-a6-harden-generated-app-containers-defense-i
useeditmode-save-has-no-catch-so-a-failing-save
```

`git worktree list` reports **six**, omitting `cb311b87...`, which is a directory
git does not know about. Only **three** are hex-named, and every one of those
hex strings is a **ticket id**, not a random name. The `t-<hex>` spelling the
handoff describes survives only as rows in the `worktree` table and the `change`
table (`t-98ad02be17cc43a6ac26d7702fa150b0`), not on disk. Ticket `1d779456` is
`In Review` and its remaining work is smaller and differently shaped than the
handoff says.

### F. The ticket board is wrong on both sides of one revert

- `0346adcc` "Group the chats page by worktree, and retire the Branches page"
  reads **Done**. Its grouping half was **reverted** the same day by `b26162a2`,
  and the model of record §6 now forbids rebuilding it.
- `65cb8c6f` "The chats page is a list of chats again; a worktree is a label"
  reads **Todo**. It is **built**: `b26162a2` implements it and names the ticket
  in its own subject line.

So one revert produced a Done ticket for work that was undone and a Todo ticket
for work that landed. Nothing reconciles a ticket against the commit that names
it.

### G. "Nothing has ever merged to main" is not what the tree says

Measured:

```
git rev-list --count origin/main..HEAD  -> 977
git rev-list --count main..HEAD         -> 165
git rev-list --count HEAD..main         -> 0
git rev-list --count origin/main..main  -> 812
origin/main  = 682d71e1, 2026-08-11
main         = 29705754, 2026-08-26
```

Local `main` is an ancestor of HEAD and is **812 commits ahead of
`origin/main`**. Worktree branches **have** been merged into the line of
development: `git log --merges` shows `ab844459`, `13f1109d`, `94b7f8eb`,
`ae248cf9`, `f95020a7`, `7f547c00`, `ceffb37f` and others. The true statement,
and the one ticket `e48268d0` should be written against, is: **nothing has been
pushed to `origin/main` since 2026-08-11**, and there is no merge-to-default-branch
event that a ticket could key `done` off. "Nothing has ever merged" as written is
falsifiable in ten seconds and undermines a finding that is otherwise correct and
important.

### H. Section 2's counts are stale within hours of being written

The handoff says "verified today, not recalled": "126 chats, 198 notes, 403
tickets". Measured at review time: **150** threads carry `app_id = development`,
**202** markdown files live under `shared/knowledge/development`, **409** tickets
belong to project `development`. The numbers are close, and the drift is normal.
The problem is that they are presented as verification with no timestamp, in a
document that elsewhere insists on measurement over recall.

### I. Section 2's dock claim is true by accident

"The launcher and the dock list every app the platform knows about" was written
into the handoff at `a1d785b6` (17:03), **falsified eight minutes later** by
`5814939f` (17:11), which reverted exactly that, and **made true again** by
`79592119` (17:42). The sentence was never edited across that cycle. It is
correct right now because the third commit went the same way as the first, not
because anyone checked.

### J. `thread.armed` and `thread.role` do not exist, and the roster has nothing to migrate

Confirming the handoff's §3.1 and sharpening it. The `chat_thread` column list is:

```
id, org_id, owner_user_id, scope_locked, current_branch_id, context_tokens,
next_seq, state, archived_at, created_at, updated_at, deleted_at, app_id,
agent_slug, claude_session, summary, external_id, context_key, scope,
voice_session, mode, title, seed_title, model, effort, permission_mode
```

No `armed`. No `role`. And `agent_slug` is set on **0 of 218** threads, so the
file-agent roster (`agents/*.md`, four files, still on disk) is joined to
nothing at all. Retiring it is a pure deletion with zero data migration, which is
easier than the handoff implies.

---

## 5. The real TODO, in dependency order

Status is the measured status, not the claimed one.

### Tier 0: do these before anything else

| # | Item | Ticket | True status |
|---|---|---|---|
| 0.1 | **Run `bash scripts/apply.sh`.** Four of today's commits, including the two the handoff reports as the day's outcome, are not serving. | none | **NOT DONE**, drift confirmed by `apply.sh --check` and `check_design.sh` |
| 0.2 | Fix the 4 failures in `backend/tests/test_operation_cancel.py` left by `5be52599`. Delete the tests for the deleted fold; keep the cancel tests that still apply. | **none filed** | **NOT DONE** |
| 0.3 | Commit or discard the 10 uncommitted entries under `shared/knowledge/development/` (3 deletions, 6 untracked, 1 modified). This is the §6 failure mode still live in the tree. | related: `dfa4e8e6` | **NOT DONE** |
| 0.4 | Reconcile `0346adcc` (Done, reverted) and `65cb8c6f` (Todo, built). | those two | **NOT DONE** |

### Tier 1: the model of record's own order

| # | Item | Ticket | True status |
|---|---|---|---|
| 1 | Thread `armed` + `role`, and retire the file-agent roster, in one change | `d5a051c8` | **Todo.** No columns; `agents/*.md` still 4 files; `agent_slug` set on 0 threads |
| 2 | Roles page; assignment replaces fork/template | `9fc4ce67` | **Todo.** No Roles page anywhere under `frontend/src`. `fork_from_template` / `adopt_group` still live in `backend/services/agents_registry.py`, `services/makers.py`, `routes/agents.py`, `frontend/src/api/schema.gen.ts` |
| 3 | App Work view, ticket-first; delete top-level Runs, Changes, Deploys | `130ef15e` | **Todo.** No Work page. `Runs.tsx` (18 lines), `Changes.tsx` (163), `Deploys.tsx` (305) all still routed at `App.tsx:126,139,142` |
| 3a | **Prerequisite nobody has filed:** make real worktrees produce `worktree` rows with a `display_name`, and clear the 8 demo rows | related: `1d779456` | **NOT DONE.** See §4B. Items 3 and 4 render demo data until this is fixed |
| 4 | Branches lens page; `GET /leases` has no browser caller | `427a86fe` | **Todo, and confirmed literally.** `/leases` exists in `frontend/src/api/schema.gen.ts:6257` and query keys are defined for it at `frontend/src/lib/queryKeys.ts:186-187`, and **`queryKeys.leases` is referenced by zero files**. Every other `leases` hit in `frontend/src` is a code comment citing the design doc |
| 5 | Delete `ProjectDetail` and `/projects` | `d12a0332` | **Todo.** `ProjectDetail.tsx` 1801 lines, `Projects.tsx` 224, `ProjectNew.tsx` present; four routes live at `App.tsx:153-156` |
| 6 | Settings 12 rows -> 9 | `85a51a32` | **Todo.** Measured 12: account, appearance, chat, memory, apps, agents, grants, trackers, mcp, models, tools, instructions |
| 7 | Delete the deploy subsystem, which has never run | `726d75a9` | **Todo.** `backend/main.py:25` imports it, `:413` mounts it. `run` table has 0 rows |

### Tier 2: chat context, the "keystone" chain

Strictly ordered; each depends on the one above.

| # | Item | Ticket | True status |
|---|---|---|---|
| 8 | `PROMPT_BLOCKS` registry: declare every prompt block and its cap | `0e810bf9` | **Todo** |
| 9 | Gate the prefix budget with `scripts/check_prompt_size.py` | `4614bfee` | **Todo** |
| 10 | Measure the whole turn-1 window | `c971b8f9` | **Todo** (Judgement) |
| 11 | Index `PROJECT.md` and project-root markdown into the corpus | `f8fced4a` | **Todo** |
| 12 | Inline only the `PROJECT.md` brief, with a recency window | `1f0d6dcd` | **Blocked** |

### Tier 3: outstanding and unsequenced

| # | Item | Ticket | True status |
|---|---|---|---|
| 13 | Branch display names; reap or rename the legacy trees | `1d779456` | **In Review.** Scope is smaller than described: 3 hex dirs on disk, all ticket-named; one orphan dir `cb311b87...` git does not track |
| 14 | "7 pre-existing backend failures in the ticket/worktree area" | `b130579e` | **Todo, and badly under-scoped.** Measured: **47 failed, 4072 passed, 2 errors** across 17 files, spanning compaction, dev mode, chat scope, memory, the queue drain and the ratchet guard. At least 12 were introduced inside this window. Rewrite the ticket. See §9 |
| 14a | **Nothing runs the backend suite.** `reviewer.sh:277-296` runs mypy on changed files, never `pytest`. Wire it, even as a non-blocking report | **none filed** | **NOT DONE** |
| 14b | The suite hangs rather than erroring when the DB credentials are absent | **none filed** | **NOT DONE** |
| 15 | A ticket may not read `done` while its work sits unmerged | `e48268d0` | **Todo.** Restate it against `origin/main`, per §4G |
| 16 | Background agents commit or say so | `dfa4e8e6` | **Done**, but the enabling fix is not deployed (§4D) |
| 17 | Merge the two app registries; kill both enumerating registries | `app-is-the-entity` §9 stages A and E | **NOT DONE.** See §8.1 |
| 18 | Give an interactive chat a lease; arm `WORKSHOP_TREE_LEASE_MOUNT` | `EXECUTION-PLAN` C-3, no ticket | **NOT DONE.** See §6 |

---

## 6. Dropped on the floor

Work that was started, designed or promised and then abandoned with no record.

### 6.1 C-3, the largest named piece of dead code, was demoted without a decision

`EXECUTION-PLAN-2026-08-27.md` §1 names three connections, calls C-3 *"the
largest single piece of dead code in the platform"*, roughly 500 lines held
hostage by one missing wire, and states plainly: **"It is next."**

Two days later `HANDOFF-2026-08-29.md` supersedes that plan and C-3 appears only
in §7 as one of "three decisions that are the operator's": "`WORKSHOP_TREE_LEASE_MOUNT`
is unarmed, and it gates the whole lease system". It is in none of the six
ordered items in §3 and has no ticket. Verified unarmed:
`WORKSHOP_TREE_LEASE_MOUNT` appears in 19 source and test locations and in **no**
env file under `~/.config/workshop/` and no systemd unit under `infra/`.

A stated next-action became an open question with no one recording that the
priority changed. That is the definition of dropped.

### 6.2 The lease and worktree subsystem was declared built, then left holding only smoke data

`EXECUTION-PLAN-2026-08-27.md` §0 reports every stage B0..B8 of the leases design
shipped, ~5,000 lines, with `lease`, `worktree` and `change` all at **0 rows**.
Two days later they hold **19, 8 and 5** rows, and every one of those rows is a
smoke test, a demo path under `/tmp/wsv/`, or an `e2e-probe`. Nobody noticed that
the only thing which has ever exercised 5,000 lines of finished code is its own
test fixtures, and no document records it.

### 6.3 Designs written and buried inside 36 hours

- `development-as-a-project-2026-08-29.md`, ticket `cefff926` (**Done**), status
  "design only, no migration started". Its §3 deliverable, an enumeration of what
  is dev-only and ships anyway, is not carried into the handoff. The ticket is
  Done because the *research* is done; the enumeration has no follow-on ticket.
- `worktree-as-the-place-2026-08-28.md` (proposal), `memory-is-files-2026-08-28.md`
  (proposal), `who-is-speaking-in-a-thread-2026-08-28.md` (proposal). All three
  are one day old and all three fall under the handoff's "the rest is history".
  Some of each shipped; none says which parts.

### 6.4 Tickets that went quiet

- `65cb8c6f` **Todo**, work landed in `b26162a2`. Quiet since.
- `1d779456` **In Review**, no reviewer, no close. Quiet since `ecb0f115`.
- `0346adcc` **Done** for work that was reverted.
- `bc81b563` **In Review** with a locked worktree still on disk and a merge
  commit already in the log (`f95020a7` "merge: model-provider routes and the
  Models settings page").
- `a89d3bd4` and `d8676bba` are **Done**, merged (`ceffb37f`, `94b7f8eb`), and
  their worktrees are still locked on disk.

Note on measurement: **`ticket.updated_at` is useless as an activity signal.**
Every one of the 22 tickets sampled carries the identical timestamp
`2026-08-30 00:38`, which is a bulk sync rewriting the column. Use
`ticket_event` or the git log instead.

### 6.5 The backlog grew four times faster than it was worked

Tickets created per day, 2026-08-21 onward: 6, 12, 37, 29, **93**, 21, 26, 26,
33 -> **283 created**. Closed as Done in the same window: 2, 15, 31, 15, 7, 17,
15, 34 -> **136 closed**. Net **+147**.

`INDEX-open-work-2026-08-26.md` recorded 159 live tickets at generation. Measured
now: **193 live** (not Done, not Cancelled), of a 423-ticket store. The plan of
record names six items. The store gained a hundred and forty-seven.

---

## 7. Deviations

### 7.1 Deliberate corrections (good)

- **Branch-first -> ticket-first.** `threads-branches-and-pages-2026-08-29.md`
  §6 drew branches as containers with threads nested inside; the addendum, same
  day, replaces containment with "a branch is a place, a thread has a current
  one" and makes the ticket the primary axis. The reasoning is written out and
  the failure cases (an orchestrator belongs to no branch; delegation crosses
  branches; a branch dies at merge and its threads do not) are named. This is
  the best piece of thinking in the ten days.
- **Deleting Workshop's compaction fold** on one operator sentence, `5be52599`,
  -1162 lines. The commit body distinguishes what went (the fold, the adapter,
  the deferred-landing task) from what deliberately stayed (the working-state
  note, the read path for stored `summary` and `compact` cuts). Correct
  reasoning, correctly recorded, and it retires four design docs. Marred only by
  the four tests it forgot (§4C).
- **Reverting the chats-by-worktree grouping** (`b26162a2`) and then writing the
  reversal into the model as settled. Correcting a shipped surface within a day
  is the rule working.

### 7.2 Churn, not correction

- **The dock listed apps three different ways in 43 minutes.** `6d41981b` +
  `69a7465b` (list every app row, 16:59) -> `5814939f` (revert: an app with no
  surface gets no tile, 17:11) -> `79592119` (restore, plus the icon fallback
  that was the actual missing piece, 17:42). The revert's stated argument was
  that `development` and `gengui` are "things you work ON" and should not be
  tiles, which directly contradicts `5082d28b` ninety minutes earlier ("the
  self-hosting project **is** an app"). The restore wins on the better argument,
  but nothing in the tree records that the revert's argument was retired, so it
  is available to be made again.

### 7.3 Drift the plan did not call for

- **`development`'s app-ness now depends on untracked instance state.**
  `79592119` is right that the platform should read a declaration rather than
  hardcode a slug. But the declaration it reads is `.runtime/projects.json`,
  which is gitignored, untracked, and written by nothing in a fresh install; and
  the app row exists only because someone ran `db/app_backfill.py --apply` by
  hand. The app-name gate is satisfied and the app-name coupling has moved from
  versioned code into unversioned local state, where no gate can see it.
- **The interface still says worktree.** `threads-branches-and-pages` §3: "The
  interface says branch." `frontend/src/pages/Chats.tsx:421` renders
  `aria-label="In a worktree"`. Small, but it is on the surface the model doc
  singled out.
- **Ten days, 500 commits, and the plan of record was written on day ten.** Of
  its six ordered items, **zero** are built. The bulk of the period went to
  memory, chat UI and compaction. That may have been the right work; the point is
  that no document written before 08-29 asked for it, and
  `EXECUTION-PLAN-2026-08-27.md` exists precisely because the operator asked
  whether the whole plan was being considered or whether the most recent
  conversation was being rushed. The honest answer recorded there was "the
  second one". Two days later the pattern is visible again: the plan arrives
  after the work.

---

## 8. Structural risks

Things that will silently rot.

### 8.1 Two app registries still disagree, and a third derives from both

| Source | Slugs |
|---|---|
| `.runtime/projects.json` (7) | birds, budget, commute, **development**, **gengui**, location, notes-app |
| `.runtime/installed-apps.json` (8) | birds, budget, commute, finance, health, location, notes-app, wearables |
| `app` table (10) | the union of the two |

Five slugs are in both files (birds, budget, commute, location, notes-app),
exactly as `CLAUDE.md` records. `app-is-the-entity-2026-08-20.md` §9 stage A
("merge the two registries with the five colliding slugs resolved to one row
each") and stage E ("kill the two enumerating registries") are both unbuilt. The
`app` table is now a third surface derived from two files that can disagree, and
nothing detects a disagreement. Both files are untracked, so a fresh clone
produces a different app table than this one.

### 8.2 Three answers to "which worktrees exist"

- `worktree` table: **8** rows, all demo/smoke, none pointing at a real directory
  on this host.
- `git worktree list`: **6**.
- `branches/development/` on disk: **7** directories.

`cb311b87376f46dbab5203a5da5806d4` is in the third list and neither of the first
two. Nothing reconciles them. Whichever one a future Branches page reads, it will
be wrong.

### 8.3 Dead code that can be re-revealed

Every one of these is deletable per the model of record and is still routed or
still exported:

- `/projects`, `/projects/new`, `/projects/:slug`, `/projects/:slug/run`
  (`App.tsx:153-156`), backed by 2,025 lines across `ProjectDetail.tsx` and
  `Projects.tsx`.
- `/agents` as a roster: `Agents.tsx`, 695 lines, plus `/agents/new`,
  `/agents/:slug`, `/agents/:slug/chat` and the legacy redirect
  `/agents/build/:slug` (`App.tsx:145-148`).
- `/deploys`, `/deploys/:id`, `Deploys.tsx` 305 lines, `deploys_routes` mounted at
  `backend/main.py:413` for a subsystem that has never run.
- `/runs` (18 lines, already unreachable from nav), `/changes` (163 lines).
- `fork_from_template` and `adopt_group`: three backend modules plus the
  generated client, a live HTTP surface for a concept the model retires and which
  has been exercised **zero** times.
- `agents/*.md`: four files describing a roster joined to 0 of 218 threads. The
  handoff's own §5 says "dead code naming a retired thing is a false claim the
  next reader must disprove", and then leaves four such files in place.

### 8.4 Gates that are green for the wrong reason

- **`check_design.sh` reports the stale bundle as `warn`, not as failure.** The
  script prints "design: clean" and the STALE warning in the same run and exits
  0. A correct build and an undelivered build look identical to any automation
  reading the exit code. `CLAUDE.md` records a whole session lost to exactly this
  in August; the warning still cannot fail a run.
- **`check_no_app_names.py` passes at `literal_sites = 479`**, a baseline
  **raised** from 291 on 2026-08-27 when the scan widened. The note in the output
  argues the raise honestly and it is a defensible one-time re-pin. But the gate
  now ratchets against a three-day-old number that nobody has reduced, and the
  target in `app-is-the-entity` §9E is zero. `frontend/src/apps/registry.tsx`
  still enumerates seven app ids in platform source, under the baseline.
- **`test_ratchets_not_loosened.py` is itself failing** (1 of 6). The test whose
  job is to detect a loosened ratchet is red, so the meta-guard over every other
  gate is not currently guarding anything.
- **No gate runs the backend suite.** `reviewer.sh:277-296` is mypy over changed
  files. So `reviewer.sh` returns `complete: true` on a diff that deletes a
  function eight tests call. It did, on 2026-08-22 (`41119015`) and again today
  (`5be52599`). A reviewer gate that cannot see a red suite is green for the
  wrong reason on every run.

### 8.5 The memory store has no dedupe, and the index inherits it

Two untracked notes, written today, about the same decision:

```
shared/knowledge/development/compaction-is-the-provider-s-compact-workshop-s-1a05016e44d.md
shared/knowledge/development/compaction-is-the-provider-s-own-compact-not-som-1a05016e44a.md
```

Both are in the 350 note chunks now in the semantic index, so a query about
compaction retrieves the same decision twice and spends two of its capped slots
on it. The `system/` vs `user/` split is real but lopsided: 192 notes in
`system/`, **1** in `user/`.

### 8.6 `ticket.updated_at` is rewritten in bulk

Every sampled ticket reads `2026-08-30 00:38`. Any future "what is stale"
query, dashboard or reaper built on that column will treat the entire backlog as
touched-just-now. This is silent and it will not announce itself.

### 8.7 The ticket board is a list of claims, and now demonstrably so

171 development tickets read Done. Nothing has been pushed to `origin/main` since
2026-08-11. `e48268d0` names this and nothing enforces it. §4F shows the board
is wrong in *both* directions on a single revert, which is the concrete case that
makes the abstract risk real: it is not only that `done` overclaims, it is that
`todo` underclaims too, so neither status can be trusted without reading the git
log.

---

## 9. Backend test suite: measured result

### 9.1 The suite as invoked does not finish

`cd backend && .venv/bin/python -m pytest tests/ -q -p no:randomly` reaches
**32%** and makes no further progress. Killed by `timeout 1800`, `EXIT=124`, the
process alive the whole time. There is no `pytest-timeout` plugin installed, so
the suite cannot self-limit. **The exact command in the review brief cannot
produce a failure count on this tree.**

The cause is environment, not a single bad file: run file by file **with**
`WORKSHOP_DATABASE_URL` and `WORKSHOP_DATABASE_ADMIN_URL` sourced, all 256 files
complete and **not one hits a 120-second timeout**. Without the admin credential,
`tests/test_apps_api.py` reports 7 errors that are purely
`RuntimeError: WORKSHOP_DATABASE_ADMIN_URL is not set` (`db/engine.py:229`), and
at least one file (`tests/test_compact_detaches.py`) blocks indefinitely rather
than erroring. So **any "7 failures" figure produced without sourcing
`~/.config/workshop/db-admin.env` is measuring the shell, not the tree**, and the
suite's failure mode for a missing credential is a hang rather than a message.
That is a defect in its own right.

### 9.2 The measured result

256 files, each run separately, both credentials sourced, 120s per-file cap:

```
47 failed, 4072 passed, 2 errors     (17 files with failures, 0 timeouts)
```

| File | Result |
|---|---|
| `test_task_decision.py` | **8 failed**, 18 passed |
| `test_compact_detaches.py` | **5 failed**, 1 passed |
| `test_dev_mode_gate.py` | **5 failed**, 47 passed |
| `test_operation_cancel.py` | **4 failed**, 4 passed |
| `test_next_ready_unattended.py` | **4 failed**, 6 passed |
| `test_chat_queue_drain.py` | **3 failed**, 16 passed |
| `test_development_chat_scope.py` | **3 failed**, 11 passed |
| `test_memory_consolidation_m6.py` | **3 failed**, 19 passed |
| `test_note_chat_provenance.py` | **3 failed**, 8 passed |
| `test_ticket_producers_state_acceptance.py` | **2 failed**, 19 passed |
| `test_change_migration.py` | 1 failed, 2 passed |
| `test_chat_turn_lifecycle.py` | 1 failed, 51 passed |
| `test_db_chat.py` | 1 failed, 28 passed, **2 errors** |
| `test_doc_status.py` | 1 failed, 21 passed |
| `test_edge_websocket_proxy.py` | 1 failed, 6 passed |
| `test_ratchets_not_loosened.py` | 1 failed, 5 passed |
| `test_runner_brake_split.py` | 1 failed, 15 passed |

### 9.3 The real number is 47, not 7, and they are not all pre-existing

Ticket `b130579e` says "seven pre-existing backend test failures in the ticket
and worktree area". The measured number is **47 failures plus 2 errors across 17
files**, and the failures are not confined to tickets and worktrees: they reach
compaction, the dev-mode gate, chat scope, memory consolidation, the queue drain,
and the ratchet guard.

At least **12 of the 47 were introduced inside this 10-day window**, both by the
same mechanism, a symbol deleted or moved without its tests:

- **`test_task_decision.py`, 8 failures.**
  `AttributeError: module 'services.task_decision' has no attribute 'apply'`.
  `apply()` was moved out by `41119015` "task_decision: break SCC cycle by moving
  apply() into tasks.py", **2026-08-22**. Seven days red, unnoticed.
- **`test_operation_cancel.py`, 4 failures.** `_fold_summary`, `_land_fold`,
  `_land_fold_when_quiet` deleted by `5be52599`, **today**. See §4C.
- **`test_ratchets_not_loosened.py`, 1 failure.** This is the gate that exists to
  detect a loosened ratchet. It is itself red, which means the guard against the
  guards being weakened is not currently guarding anything.

`test_compact_detaches.py` (5 failures) was last touched on 2026-08-27 by
`8e78ef0d`, a compaction commit, and is very likely collateral from the same
deletion; it could not be confirmed here because it hangs intermittently.
`test_dev_mode_gate.py` (5 failures) fails on
`assert PosixPath('~/workshop') is None` and `assert 404 == 403`,
which is the ADR-0014 dev gate, the same area as this week's `dev_only` work.

**The bottom line: the suite is 47 red and has been drifting redder all week
because nothing runs it.** `scripts/reviewer.sh` gates on twenty-odd checks and
**does not run `pytest`**: its `PY_CHECK` (`scripts/reviewer.sh:277-296`) is
`mypy --ignore-missing-imports`, falling back to `py_compile`, over the *changed
files only*. And `CLAUDE.md`'s own pre-completion checklist asks for
`npm run typecheck`, `check_design.sh`, `cd frontend && npm test`, and for the
backend only "a quick import check". No instruction anywhere in the repo asks
anyone to run the backend suite.

That is the single mechanical reason a commit can move `apply()` out of a module
on 2026-08-22 and leave eight tests calling it red for seven days, and why the
same class of mistake repeated today with the compaction fold. Every gate in this
repo checks shape; nothing checks behaviour.

---

## 10. If you do five things

1. `bash scripts/apply.sh`. Nothing else in this document matters until the
   operator's origin serves the code that was written today.
2. Get the backend suite from 47 red to green, starting with the 12 failures this
   window caused (`test_task_decision.py` 8, `test_operation_cancel.py` 4), and
   then wire `pytest` into `reviewer.sh`. Without that step every other item on
   this list can be undone silently by the next commit.
3. Fix the `worktree` table before building anything that reads it: clear the
   eight demo rows, record the seven real trees, write a `display_name`. Items
   `130ef15e` and `427a86fe` are otherwise building views over fixture data.
4. Reconcile `0346adcc` and `65cb8c6f`, and rewrite `e48268d0` against
   `origin/main` rather than "main", so the enforcement it asks for is written
   against a condition that is actually true.
5. Decide C-3 explicitly. Either arm `WORKSHOP_TREE_LEASE_MOUNT` and give an
   interactive chat a lease, or write down that ~500 lines of lease enforcement
   are being kept unused on purpose. Right now it is neither, and a document
   that called it "next" was superseded without anyone saying so.
