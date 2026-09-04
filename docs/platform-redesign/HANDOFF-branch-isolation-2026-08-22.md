# Handoff: branch isolation, leases, and coordination

- Date: 2026-08-22
- For: the agent or agents that will build this. Read this first, then the two
  design docs it points at.
- Status of the work: **B0, B2a, B2, B3, B4, C1, C2, C5 and Track A are BUILT**
  (B2, B3 and B4 on 2026-08-23; the rest 2026-08-22 evening). See section 0. B1
  and B5 to B8 are unstarted. B1's position in the order was wrong -- it needs
  the lease -- and the lease now exists, so B1 is next.
- **CORRECTED 2026-08-27: that status line is wrong and has been since
  2026-08-23. B1 and B5 through B8 all shipped.** The whole of B0 to B8 is
  built. B1 remains behind `WORKSHOP_TREE_LEASE_MOUNT`, which is off.
- **CORRECTED AGAIN 2026-09-04: "cannot safely be armed because nothing gives an
  interactive chat a lease" is STALE and had been for a week.** Measured today:
  `chat_worktree.enter` is called from `routes/chat.py:3765`, and
  `session_defaults.resolve`/`.choose` have real callers at
  `chat_worktree.py:274, 302, 529`. The connection C-3 said was missing exists.
  What was still missing until today is smaller and was the real obstacle: the
  write-blocked path refused to auto-create a worktree for any chat whose
  `context_key` did not start with `project:`, which is precisely the operator's
  own chat, so arming would have dropped him into the manual picker the design
  exists to delete. Fixed in `d9d36677`. `reader_staleness.py` still has zero
  callers anywhere; that is B8 and is a separate piece. The
  measured state, and the ordered plan for the rest of the platform around it,
  is [`EXECUTION-PLAN-2026-08-27.md`](EXECUTION-PLAN-2026-08-27.md). Read that
  before acting on any status line in this file.
- Design of record: [`workspace-ownership-and-leases-2026-08-22.md`](workspace-ownership-and-leases-2026-08-22.md)
  (revision 2, post-review) and
  [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)
  (the environment model underneath it).

---

## 0. What is built, and what it changed about the plan

Shipped 2026-08-22 in this order, each verified rather than asserted:

- **B0** (`75526b8`). Worktree mode had never once executed: `WT_ENABLED`
  required a slug the resident runner never passes. It now resolves the project
  from the REGISTRY by directory (`WT_SLUG`, kept separate from `SLUG` so the
  billing and learnings keys fixed earlier the same day are not undone), and the
  allocation calls `provision_worktree`, which had only test callers. Measured:
  0.48s, `node_modules` and `backend/.venv` hardlink-cloned, same inode.
- **The trap B0 would have died on**, same commit. A linked worktree has no
  `.git` directory, only a FILE with an absolute `gitdir:` pointer into the
  parent repo. Confined alone it is NOT A GIT REPOSITORY: measured, every git
  command failed. `scoped_exec` now binds the parent `.git` at its real path,
  read-write. The host working tree stays absent; only `.git` appears. **Bind
  ORDER is load-bearing** and is pinned by a test: bwrap applies mounts in
  sequence, so a git bind emitted before the mountpoint bind is shadowed.
- **B2a** (`14addd7`). `services/git_service.py` + `/git`, repo-path-first,
  knows nothing about projects or tickets. `worktrees.py` keeps branch records
  and naming/merge policy. No public signature changed. +63 tests.
- **Track A** (`d00c820`). Needs-you is read off tickets; `run_inbox.py` and the
  dead run notifier are gone. **756 open inbox items to 3.** Net -139 lines. The
  "operator asks himself" attribution is fixed as a consequence.
- **C1** (`9febe9d`). The `change` table, `services/changes.py`, `/changes`, RLS
  per the Ticket model, migration `a7c3e10d9b64`.
- **C2 + C5** (`865e219`, `bcc1f5b`, `50fc367`). A supervised local sweep now
  leaves its branch UNMERGED and opens a change; accepting is what merges, on
  both acceptance surfaces. A merge that fails refuses the acceptance with the
  git reason rather than marking the ticket done over work that did not land.
- **C4** (`1ccf888`). The review surface: diff first, the summary explicitly
  labelled as the author's claim, risk signals, `/changes` list, entry points
  from Home and the ticket page.
- **B2** (2026-08-23). The `worktree` record and its `worktree_event` timeline,
  migration `e7d4c1b90a63`, `services/worktree_records.py`, `/branches`.
  `services/worktrees.py` writes the row at allocate/provision and moves it
  through merge, discard and reap. Record-keeping is ADDITIVE and a test pins
  it: with no relational store the tree still allocates and answers with a null
  `worktree_id`. The per-tree lock is `work_driver.tree_lock` -- section 15's
  "fold in work_driver's existing lock rather than adding a third" -- keyed the
  same way, with a `kind` prefix so a record write does not wait out a resident
  sweep. `change.worktree_id` finally got the composite foreign key
  `a7c3e10d9b64` deferred to this migration by name. **Correction 3 below is
  fixed as part of it**: teardown now unlocks before removing and prunes before
  deleting the ref, so an allocated branch's slug is reusable after its merge.
  It was not, and no test had noticed, because the branch ref survived every
  reap of an allocated (as opposed to hand-created) worktree.

- **B3** (2026-08-23). The `lease` table, migration `f2a6d0c31b78`,
  `db/lease_repo.py`, `services/leases.py`, `scripts/lease.py`. One mechanism,
  one table, a typed subject: the ticket lease (kept, per §5) and the worktree
  lease. Acquire is the one atomic `INSERT ... ON CONFLICT ... DO UPDATE ...
  WHERE lease.expires_at < now()`; renew and release are `UPDATE ... WHERE
  generation = :g`; every instant is computed and compared by Postgres, so no
  caller's clock is ever accepted. `tasks.claim/heartbeat/update/reclaim` call
  the module instead of counting their own generation, and §6's join rule is
  wired both ways: `presence.release()` gives the leases up, and an interactive
  holder whose presence has gone reads as expired (a `min()`-shaped overlay that
  can only ever SHORTEN a lease). `presence.directory(lease_source=...)` is
  filled and `GET /presence/directory` answers who holds what.
  **`SELECT ... FOR UPDATE SKIP LOCKED`, which §5 named as the boring
  alternative, was evaluated and rejected**, with the reasoning in the module
  docstring: its exclusion ends at COMMIT, a ticket claim outlives the process
  that took it, and a row lock carries no fencing generation.

### What building it corrected in this document

1. **B1 cannot be second.** "Read-write if the session holds the lease" needs a
   lease, which is B3. Ordered before it, B1 is either a no-op or it makes every
   tree read-only. Correct order: B0, B2a, B2, **B3, then B1**.
1b. **`ticket.claim_token` could not be deleted, and §15's cost list assumed it
   would be.** That list named "an alembic migration on a NOT-NULL column, a
   `schema.gen.ts` regeneration, a `field_occupancy_baseline.json` edit, 14
   `--expect-claim-token` shell call sites and 5 test files". It missed that the
   ticket store is DUAL-BACKED: `development` and `workshop` read Postgres, and
   every tree under `projects/` is file-backed by design with no database near
   it. A lease is a Postgres row, so deleting the column would leave a project
   sweep with no fence at all rather than a degraded one. What shipped instead:
   the column keeps its name and its place on the wire, and its VALUE is the
   generation the lease issued wherever a lease store exists. So there was no
   NOT-NULL migration, no baseline edit and no test rewrite; the
   `schema.gen.ts` regeneration DID happen, for the new `DirectoryEntry.lease`
   field, and all 12 `--expect-claim-token` argument sites plus `ralph.sh`'s
   `--claim-token` were re-read and are unchanged because the flag still means
   exactly what it meant. The column stops being two facts when the file-backed
   ticket store does, which is not this step.
2. **The supervised ceiling was cosmetic and this doc did not notice.** A
   supervised sweep merged into the trunk and THEN set `in_review`, so declining
   unlanded nothing. C2 is what makes worktree isolation pay for itself, and it
   belongs beside B0 rather than late in track C.
3. **`git worktree prune` will not reclaim a LOCKED worktree** whose directory
   was removed, and `allocate_task_worktree` locks every one it makes. The slug
   stays permanently taken. Hit repeatedly while testing. `gc.reap_merged_worktrees`
   needs `unlock` before `prune`, or `remove -f -f`.
4. **A merged change loses its diff unless the base sha is pinned BEFORE the
   merge.** Diffing `base_ref..merged_sha` afterwards is guaranteed empty,
   because the base ref by then contains the change. `change.base_sha` exists
   for this (migration `c8299a81457a`).
5. **`ownership.pin_operator` is now a SIXTH entrypoint's lesson.** Any new
   shell-into-python helper that touches the relational store dies on OwnerUnset
   and fails SILENTLY. Add the pin when you write the helper, not after.
6. **This tree had `main` 160 commits behind a `wt/` branch that had silently
   become trunk.** With B0 live that would have branched every worker off stale
   code and merged into a branch nobody used. `main` was fast-forwarded and the
   checkout moved onto it.

---

## 1. The problem, in one paragraph

Every chat and every autonomous agent working a given scope shares **one working
directory**. On 2026-08-22 a concurrent worker reset that tree mid-session and
discarded another agent's uncommitted edits, which had to be redone. That is not an
edge case; it is the normal consequence of the current design. The fix is per-unit-of-
work git worktrees, one writer at a time, and a way for participants to see each other
and hand off without destroying work.

## 2. Read these before touching anything

1. `workspace-ownership-and-leases-2026-08-22.md` §2 ("ground truth") **first**. It
   lists what already exists. An earlier revision of that doc was wrong about several
   of these and the plan built on top of the errors; §2 is the corrected list.
2. The same doc §4 (exclusivity by mount) and §5 (two leases). These are the two
   decisions everything else depends on.
3. `branches-worktrees-and-environments-2026-08-20.md` §5, §6, §13, §14.

## 3. Traps. Read this section twice.

**Things that look shipped and are not:**

- ~~`worktrees.provision_worktree` has only test callers.~~ **FIXED in `75526b8`**:
  the sweep calls it. Left here because the SHAPE of the mistake recurs: a fully
  built path with only test callers reads as shipped.
- ~~Worktree mode has never executed.~~ **FIXED in `75526b8`** (`WT_SLUG` resolves
  the project from the registry by directory). Both halves of the old gate were
  unreachable; do not re-add a gate that asks whether a human typed a name.
- `gc.reap_merged_worktrees` exists but nothing schedules it, it cannot reclaim a
  LOCKED worktree (which is every one `allocate_task_worktree` makes) without an
  `unlock` or a double `--force`, and it disagrees with
  `worktrees.cleanup_merged` about what "merged" means (local `main` ancestry versus
  `origin/<default>`). Reconcile them or pick one.
- The post-commit hook that refuses to build from a linked worktree is in
  `.githooks/post-commit`, but `core.hooksPath` points at `.git/hooks`, which holds
  only samples. **The hook is not installed in this checkout.** Install it before
  relying on it.
- All 8 records in `.runtime/build_agents.json` are `state: "retired"`. The whole
  registry is dead; do not treat it as a live consumer.

**Things you would otherwise build a second (or third) copy of:**

- A per-tree single-writer lock → `services/work_driver.py` (`:80-107`) already has
  one, flock-based, keyed by `md5(dir)`, probed live rather than trusted.
- A pid liveness probe → use `port_registry.process_alive` (`:90-119`), which handles
  zombies. Do **not** use or copy `tasks._pid_alive`, which reports a zombie as alive.
- An ephemeral presence channel → `.runtime/heartbeats/<id>.json` written by
  `scripts/heartbeat.sh` and read by `routes/agents.py:1445-1450` already is one.
  Generalize it; do not write `services/presence.py` from scratch.
- The Queue verb → `chat_queue` + `chat_drain` (`routes/chat.py:3786-3852`) already
  does exactly this, restart-surviving.
- A scope→directory map → there are already **two** (`paths.scope_root` and
  `task_scope._raw_root_for_context`). Do not add a third; if you touch this, reduce
  it to one.

**Claims that are false and must not be repeated in a commit message or a ticket:**

- "The lease is entirely internal." It is on the wire (`api_models.py:375,:422`), in
  the generated client (4 places in `schema.gen.ts`, gated absolutely by
  `reviewer.sh`), a NOT-NULL Postgres column (`db/models.py:512`), and in
  `field_occupancy_baseline.json`.
- "Steer delivers at the next tool boundary." `build_streaming.py:3714-3720` records
  the measurement: the model addresses an injected message at the **end of its current
  plan**.
- "Deleting the ticket claim is behaviour-preserving." It removes the `todo → doing`
  compare-and-set (`tasks.py:2290-2301`), which is the only thing stopping two sweeps
  working one ticket.

## 4. The steps

Each is scoped to be filed as one ticket. Do them in order; the dependencies are real.

### B0. Ungate worktree mode for the self-repo and wire the provisioner
**DONE (`75526b8`).** Kept for the record; see section 0 for what it actually
took, including the sandbox git-bind trap this spec did not contain.
**Objective:** an unattended sweep works each ticket in its own worktree, so two
workers can never write to one tree.
**Output:** the sweep's allocation path calls `provision_worktree` rather than
`allocate_task_worktree`; the gate that makes `WT_ENABLED` unreachable is fixed (the
runner passes what the gate needs, or the gate stops requiring a slug it never gets);
demonstrated by running two sweeps concurrently and showing each writes only inside
its own directory while the shared tree is untouched, and that both results land on
the base branch. The existing local merge path (`publish_or_park`, no remote, no PR)
is what completes them.
**Start from:** `scripts/project_ralph.sh:632-633` (the gate), `:238-246` (the
allocation call), `:324` (`publish_or_park`); `scripts/queue_runner.sh:214` (the
invocation); `backend/services/worktrees.py:594` (`provision_worktree`).
**Boundaries:** no lease, no presence, no directory, no UI. Do not change the
supervised ceiling, the reviewer gate, or how work is selected. Do not touch
voice-mode.
**Why first:** it ends the actual collisions and needs nothing else in this plan.

### B1. Read-only mounts for a session with no lease
**REORDERED: this now follows B3.** It is defined as read-write when the session
holds the tree's lease, and there is no lease until B3, so ahead of it this step
is either a no-op or it makes every tree read-only.
**Objective:** a session that does not hold the tree physically cannot write to it.
**Output:** `scoped_exec` mounts the session's root read-only unless the session
holds the tree's lease, in which case read-write. A write attempt without the lease
fails `EROFS` and the platform surfaces it as an offer to branch. Verified by running
a real confined turn both ways.
**Start from:** `backend/services/scoped_exec.py` (the mountpoint and the bind flags),
`backend/services/build_streaming.py` (where a turn is spawned and its env built).
**Boundaries:** the mount flag and its plumbing only. Must fail closed. Do not change
the grant set or the confinement default.
**Why it is the enforcement primitive:** §4 rests on it, and once B3 exists it
makes the shared tree safe to read.

### B2a. Extract the git service
**DONE (`14addd7`).**
**Objective:** all git operations live in one service that knows nothing about
projects, tickets, leases or scopes, so anything can use it against any repository.
**Output:** `services/git_service.py` taking a repo path plus arguments, covering
status, log, diff, ahead/behind, branch and worktree operations, commit, merge,
snapshot, checkout, clean, rev-parse and is-ancestor. Its own router with a prefix and
a tag under `/git`. `worktrees.py` keeps only branch records and naming/merge policy
and calls the service for everything else. Every other place that shells out to git is
repointed. No behaviour change; the existing worktree tests prove it.
**Start from:** `backend/services/worktrees.py` — it currently mixes four concerns:
git subprocess (`_run`, `_git_env`), registry lookup (`PROJECTS_REGISTRY`,
`_project_record`, `_project_dir`, `_project_repo`), branch naming and paths
(`_kebab_branch_slug`, `_branch_name`, `_branch_path`), and merge/publish policy. Other
git callers: `scripts/project_ralph.sh`, `scripts/apply.sh`, `backend/services/gc.py`,
`backend/services/github.py`.
**Boundaries:** the git service must never learn what a ticket, a project or a scope
is. Do not fold GitHub into it: `services/github.py` keeps the remote surface. No
behaviour change in this step. Regenerate the client if any response model moves.

### B2. The worktree record and lifecycle
**Objective:** a worktree is a first-class record with an owner, a status and a
timeline, not just a directory git happens to know about.
**Output:** the record from `branches-...-2026-08-20.md` §3.2 with RLS per the Ticket
model; open, provision, merge and reap driven through it; `work_driver`'s existing
per-tree lock folded in rather than duplicated; the two disagreeing definitions of
"merged" reconciled to one.
**Boundaries:** no lease table yet. Do not rewrite the git plumbing in `worktrees.py`;
wrap it.

### B3. The lease table and module
**Objective:** one lease mechanism with a typed subject replaces the ticket store's
private implementation.
**Output:** the schema and the atomic upsert in
`workspace-ownership-and-leases-2026-08-22.md` §5, including `UNIQUE (subject_kind,
subject_id)`, server-computed `expires_at`, and generation-checked renew and release.
The ticket lease is **kept** for every ticket. `tasks` calls the module instead of
implementing it. A CLI surface exists, because the most important caller is bash.
**This is not free and must not be scoped as if it were:** an alembic migration on a
NOT-NULL column in scopes where Postgres is authoritative; `npm run gen:api-types` to
regenerate `schema.gen.ts` (the reviewer gate fails absolutely without it); an edit to
`field_occupancy_baseline.json`; 14 `--expect-claim-token` call sites in
`project_ralph.sh`; 5 test files that assert the stored shape.
**Boundaries:** do not delete liveness probing outright; make it pluggable and point
it at `port_registry.process_alive`.

### B4. Presence as a shared, expiring store
**Objective:** every participant, including the bash sweeps, is visible to every other.
**Output:** presence rows any process can write, expiring on TTL, that nobody
reconciles on read; the existing `.runtime/heartbeats/` channel generalized rather
than replaced; and the join rule from §6 (an interactive holder's lease is renewed by
its presence, and presence expiry releases it immediately).
**Boundaries:** presence must not become a derived copy of anything a ticket or a
lease already owns. It must be readable from a separate OS process.

### B5. Show the holder
**Objective:** "who is working here" is answerable without opening anything.
**Output:** the ref pill (current branch plus holder avatar) and the directory sheet,
rendered into the Tickets panel's **existing** single sheet host. Follow
`frontend/CLAUDE.md`.
**Boundaries:** do not add a second panel or a second host. The popover was already
retired in `19e8aae`.

### B6. Queue and steer
**Objective:** a session can ask a busy holder for something without destroying its work.
**Output:** the existing `chat_queue` wired as the Queue verb; a control file the
sweep reads at the top of each iteration for steer and yield; copy that does not
promise immediacy, because steer's latency is unbounded.
**Blocked on:** §12's product question (does accept merge?) being answered.

### B7. Preempt, snapshot and quiesce
**Objective:** taking a tree from a holder never loses its work and never hands over a
dirty tree.
**Output:** preempt gives a 30s grace; the holder snapshots to a dangling ref
(`git stash create` or `refs/wip/<session-id>`), **not** a commit on the branch, and
releases. On lapse it is killed and then the tree is quiesced: snapshot, restore to a
known state, clear a stale `.git/index.lock`, and only then grant the lease. Every
step lands on the worktree timeline as a structured event.

### B8. Session defaults, then reader staleness
**Objective:** a new chat lands somewhere sensible without being asked, and a reader
is told when its view is stale.
**Output:** inherit from what opened it, else the default ref read-only with a
staleness banner, else create a **new** branch on first write (joining is offered, not
automatic). Then the "the tree moved: N files changed since your last read" message
into a reading session.

### Track A (independent of all the above)
- **A1.** Read "needs you" from tickets; delete `run_inbox.sync`, the task-derived
  inbox path, and reconcile-on-read. Fixes the "operator asks" attribution for free.
- **A2.** Retire the ~744 dead run notifications; decide whether `notify` survives.
- **A3.** Already shipped (`19e8aae`). Do not redo it.

## 5. Gates every step must pass

- `bash scripts/reviewer.sh` is the completion gate; the worker does not judge itself.
- Backend: full suite, plus `python3 scripts/check_routes_answer.py` and the ratchets.
- Frontend or kit: `npm run typecheck` from the repo root, `bash scripts/check_design.sh`,
  `cd frontend && npm test`, then `bash scripts/apply.sh` so it is live on the built
  origin. A UI change is not delivered until the built origin serves it.
- Any change to a backend response model requires `npm run gen:api-types` in the same
  commit.
- Every new router declares a `prefix` and a `tag`; a bare `APIRouter()` is a bug. Every
  entity is independently addressable and appears in the generated client. No service
  imports another service's policy. (Design of record: the ownership doc §13.)
- No new `TODO`/`FIXME`/`NotImplementedError`/stub bodies. Too big for one tick means
  the ticket is `[~] BLOCKED` with proposed sub-tasks.

## 6. Track C: what "done" means

Do not build a review or merge step from scratch. It has its own design:
[`changes-and-review-2026-08-22.md`](changes-and-review-2026-08-22.md). Summary of
what it changes for you:

- A branch produces a **Change** (the reviewable artifact), local-first, mirrored to a
  GitHub PR only when the app is bound to a remote. `worktrees.diff_branch`,
  `merge_branch`, `publish_branch` and `discard_branch` already exist and are its
  plumbing; what is missing is the record and the surface.
- **Ticket status is derived from its change**, not set by the worker. A change opening
  makes the ticket `in_review`; a merge makes it `done`. C2 in that doc is the step
  that removes self-reported completion, and it is the highest-value one.
- The review surface distrusts the summary on purpose: diff first, summary labelled as
  the author's claim, machine gate results attached, intent beside outcome. The
  measured top defect in agent-authored changes is a description claiming things the
  diff does not do.
- Pre-approval is **auto-merge**: per change, plus an optional per-scope
  `merge_policy` beside the existing `scope_execution` setting.

Sequence it after B2 (the worktree record). B6's steer half and B7 both assume it.

## 7. The one decision that is the operator's


**Should a scope be able to auto-merge low-risk changes nobody looked at?** Per-change
auto-merge is uncontroversial: the operator read it and chose not to babysit the gates.
A standing per-scope `auto_when_green_and_low_risk` is the one place in this design
where code merges without a human ever seeing it. The argument for it is that it is
what makes a long unattended run useful overnight; the argument against is exactly
what it says. Do not guess: ask through the ticket.
