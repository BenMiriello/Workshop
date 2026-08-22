# Handoff: branch isolation, leases, and coordination

- Date: 2026-08-22
- For: the agent or agents that will build this. Read this first, then the two
  design docs it points at.
- Status of the work: **nothing built.** Every step below is unstarted.
- Design of record: [`workspace-ownership-and-leases-2026-08-22.md`](workspace-ownership-and-leases-2026-08-22.md)
  (revision 2, post-review) and
  [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)
  (the environment model underneath it).

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

- `worktrees.provision_worktree` has **only test callers**. The live sweep calls
  `allocate_task_worktree`, which does a bare `git worktree add` with no dependency
  provisioning.
- **Worktree mode has never executed.** `WT_ENABLED` needs `CONCURRENT_WT == 1 && -n
  "$SLUG"` (`project_ralph.sh:632-633`), and `queue_runner.sh:214` invokes the sweep
  as `--dir "$DIR"` with no slug, so `SLUG` is always empty.
- `gc.reap_merged_worktrees` exists but nothing schedules it, and it disagrees with
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
**Objective:** a session that does not hold the tree physically cannot write to it.
**Output:** `scoped_exec` mounts the session's root read-only unless the session
holds the tree's lease, in which case read-write. A write attempt without the lease
fails `EROFS` and the platform surfaces it as an offer to branch. Verified by running
a real confined turn both ways.
**Start from:** `backend/services/scoped_exec.py` (the mountpoint and the bind flags),
`backend/services/build_streaming.py` (where a turn is spawned and its env built).
**Boundaries:** the mount flag and its plumbing only. Must fail closed. Do not change
the grant set or the confinement default.
**Why second:** it is the enforcement primitive §4 rests on, and on its own it makes
the shared tree safe to read.

### B2a. Extract the git service
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
