# Who is working here: branch ownership, leases, and takeover

- Date: 2026-08-22
- Status: **B0, B2a, B2, B4, B3, B1 and B8 are built** (B1 and B8 on 2026-08-23,
  B3 and B4 the same day, B2 the same day, the other two 2026-08-22 evening),
  along with track C's record, review surface and merge-on-accept, and all of
  track A. **B5, B6 and B7 are built too, as of 2026-08-23** (commits `5cfc8630`
  for the holder UI and `939ab1dd` for Queue, Steer and Preempt); this line said
  they were not until 2026-08-27, and that stale sentence is the reason the work
  was believed unstarted for four days. **ALL OF B0 THROUGH B8 IS BUILT.**
  What is NOT true is that any of it has run: measured 2026-08-27, the `lease`,
  `worktree` and `change` tables hold zero rows each. See
  [`EXECUTION-PLAN-2026-08-27.md`](EXECUTION-PLAN-2026-08-27.md) §1 for the three
  connections that hold it inert, the largest being that nothing gives an
  interactive chat a lease, which is why the flag below cannot be armed. B1 had MOVED to after B3 (it is defined as
  read-write-if-you-hold-the-lease and there was no lease until B3) and shipped
  once that prerequisite existed.
  **B1 IS BEHIND A FLAG THAT IS OFF** (`WORKSHOP_TREE_LEASE_MOUNT`, unset =
  off), for the reason the uid layer SEC-A1 did the same: `scoped_exec` is what
  every live turn spawns inside, including the operator's own chat, so its
  attended verification is the operator's and nothing has turned it on. With the
  flag off the spawn path is byte-identical to the one before it
  (`tests/test_tree_access_mount.py::TestTheFlagOffPathIsUnchanged`). Build log
  and the corrections it
  forced: [`HANDOFF-branch-isolation-2026-08-22.md`](HANDOFF-branch-isolation-2026-08-22.md)
  section 0. Revision 2, after an adversarial review pass
  (§16). Revision 1's central mechanism was wrong and is replaced.
- Answers what [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)
  §14 left open: that doc decided a worktree is created when work first needs to
  write, and said nothing about what happens when a second participant wants to
  write to one that is busy.
- Designed for the production target: any number of users, apps and participants.

---

## 1. What was asked

> Does branch management need to be its own thing, decoupled from the chat? The
> ticket is coupled to it but can change. An agent can be coupled to a branch. Have
> rules against overlapping ownership, but do not be overly strict: warn, error, or
> default to new or existing as appropriate. One agent does the actual work at a
> time; others coordinate with it. If another chat or agent is already there, how do
> we coordinate? What if it is old or stopped? How do we tell if it is still
> working? Who has the lease, and when can it be swapped? If a chat wants to work
> while an agent is working, can it inject the request with priority, or add it to
> the ticket and notify the agent? Show that in the chat with options: do nothing,
> implement then hand back, or implement and take over. If we take over, what
> happens to the agent's work? Stop it, but keep a record that is not buried text.
> When we make a new chat, how do we know which branch to put it in, and what to
> default to? Everything that needs to know must be able to see or query the others'
> state and who is current.

---

## 2. Ground truth: what exists, verified

Revision 1 asserted several things about this codebase that are false, and built on
them. Everything below was checked against the source on 2026-08-22.

**The ticket lease exists and is real, but is weaker than claimed.**
`services/tasks.py` has `claim_token` (fencing generation, `:2294`), `lease_until`,
`last_heartbeat`, `claim_pid`, `heartbeat()` fenced by token (`:2479`), `reclaim()`
(`:1770`), `LEASE_TTL_SEC = 3600` (`:222`). But:

- Its atomicity is a same-filesystem `flock` (`:350-364`). The relational write is an
  unconditional `UPDATE ... SET claim_token = :ct` (`db/ticket_sync.py:720-739`) with
  no compare-and-set, so two processes not sharing that lock have no mutual exclusion.
- `reclaim()` deliberately leaves `claim_token` intact (`:1797`), so between reclaim
  and re-claim a zombie's token still matches. It is not fenced in that window.
- `_pid_alive` is `os.kill(pid, 0)` (`:266-284`), which is single-node and also
  reports a **zombie as alive**.

**It is NOT "entirely internal", and revision 1's "delete it outright" rested on
that.** `claim_token` is on the wire (`routes/api_models.py:375` and `:422`, whose own
comment says dropping a concurrency primitive from the wire is not a change to make
silently), in the generated client (4 occurrences in `frontend/src/api/schema.gen.ts`,
gated absolutely by `scripts/reviewer.sh`), a NOT-NULL Postgres column
(`db/models.py:512`) in scopes where Postgres is authoritative, and in
`scripts/field_occupancy_baseline.json`. Plus 14 `--expect-claim-token` call sites in
`project_ralph.sh` and 5 test files asserting the shape.

**Worktree mode has never run.** `WT_ENABLED` requires `CONCURRENT_WT == 1 && -n
"$SLUG"` (`project_ralph.sh:632-633`), and `queue_runner.sh:214` invokes the sweep as
`--dir "$DIR"` with no slug. So `SLUG` is always empty and the gate is always 0,
regardless of the flag.

**Provisioning is written and tested but not wired.** `provision_worktree`
(`worktrees.py:594`) has **only test callers**; the live path is
`allocate_task_worktree` with no dependency provisioning at all.

**Steer does not do what revision 1 said.** `build_streaming.inject()` (`:3707`) really
does write into the running turn's stdin FIFO. But its docstring (`:3714-3720`),
citing a measurement in `chat-message-ordering-audit-2026-08-05.md §2`, states that
the model **addresses the message at the end of its current plan, skipping the step
boundaries in between**. Revision 1 sold steer as "delivered at the next safe
boundary... in seconds". That is the behaviour this repo measured and found false.

**Several things this design would have rebuilt already exist:**

- `services/work_driver.py` is already a **per-tree single-writer lock**
  (`queue-runner-<md5(dir)>.lock` + pidfile, `:80-107`), probed live rather than
  trusted. Agent sweeps are already serialized per tree
  (`services/agent_slots.py:34-37`).
- `port_registry.process_alive` (`:90-119`) is a **better pid probe** than
  `tasks._pid_alive`: it reads `/proc/<pid>/stat` and treats `Z`/`X` as dead, for
  exactly the zombie reason above.
- `.runtime/heartbeats/<id>.json` (written by `scripts/heartbeat.sh`, read by
  `routes/agents.py:1445-1450` into `heartbeat_age_s` + `liveness`) is already an
  ephemeral presence channel with the right lifetime split.
- `chat_queue` + `chat_drain` (`routes/chat.py:3786-3852`) already implement the
  **Queue** verb: a message recorded against a busy turn, restart-surviving,
  re-entering through the same door a live message does.
- `allocate_task_worktree` already `git worktree lock`s the tree (`:255-258`).
- The **Tickets panel already has one host**; the popover was retired in `19e8aae`
  earlier the same day. Revision 1's only cross-track ordering constraint was
  therefore vacuous.

---

## 3. Vocabulary

Ticket keeps its one meaning; nothing here reuses it.

- **Ticket** — a unit of work. Unchanged.
- **Branch** — the user-facing name for an isolated line of work and its tree. This
  is not a new word: `branches-worktrees-and-environments` §3.2 already ruled that
  "the disk concept stays worktree (git's term)... the user-facing concept is the
  branch." Revision 1 coined "workspace" for this, which was a mistake: `/workspace`
  is literally the bwrap mountpoint every agent sees (`scoped_exec.py:72`), and
  `workspace_inputs` already means an app's heavy dependency directories
  (`worktrees.py:657`). Three meanings for one word, in a document whose own
  vocabulary section forbids exactly that.
- **Worktree** — the implementation and the record. Not shown in the UI.
- **Session** — one live participant: a chat, or an agent run. Cheap, disposable.
- **Lease** — the durable, exclusive right to write to a subject.
- **Presence** — the expiring fact that a session is here and what it is doing.
- **Directory** — the queryable index of sessions. A read-time join, never a store.

"Claim" retires as a second word for lease.

---

## 4. Exclusivity is enforced by the mount, not by a token

This is the correction that reorganizes the whole design.

A fencing token works because a **storage layer validates it**. For the ticket lease
that layer is `tasks.update()`, which really does check (`tasks.py:2093`). For a
working tree the storage layer is the filesystem and git, and **nothing validates a
generation there**. A holder whose lease expired mid-test, whose replacement has
already started, will `git add -A && git commit` into the same tree and no check
anywhere stops it. Revision 1 transplanted a pattern whose load-bearing half does not
transplant.

The enforcement mechanism already exists in this tree. `scoped_exec` mounts a
session's root at a fixed mountpoint and already fails closed when confinement is
unavailable. So:

> **Holding the lease means getting the tree mounted read-write. Not holding it means
> getting the same tree mounted read-only.**

That is Perforce-style exclusive checkout enforced by the OS instead of by
cooperation, and it buys three things at once:

1. **Exclusion that survives a misbehaving holder.** Revocation is simply that the
   next turn does not get a read-write mount.
2. **A write-detection mechanism**, which §14.1 of the sibling doc needed and did not
   have. "Branch on write" was unbuildable as written: a turn is a detached process
   inside bwrap and the backend cannot observe individual writes, and "first write" is
   not even classifiable because the harness writes session and temp files constantly.
   With a read-only mount the first genuine write fails `EROFS`, which is an exact,
   cheap, observable event. The platform catches it and offers to branch.
3. **A safe default reader state.** A session with no lease physically cannot damage
   the tree it is reading.

The lease record remains the *bookkeeping* (who holds it, since when, until when).
The mount is the *enforcement*. Do not claim the token protects the tree.

---

## 5. Two leases, one mechanism

Revision 1 collapsed the ticket lease into the tree lease, arguing a code ticket needs
no ticket lease because holding the tree admits you to the work. **That is wrong at
the only moment that matters.** A `todo` ticket has no tree yet. Two sweeps both see
`todo`, both create a worktree, both hold a lease on *different* trees, and both work
the same ticket. What prevents that today is the `status == "todo"` compare-and-set
inside `claim()` (`tasks.py:2290-2301`), which revision 1 proposed to delete.

So there are two subjects and both are needed:

- **Ticket lease** — who took this work. Needed for every ticket, including code
  ones, because it is acquired before a tree exists.
- **Worktree lease** — who may write to this tree.

One mechanism, one table, a typed subject:

```
lease
  id
  org_id                                  -- RLS, per the Ticket model
  subject_kind   enum(worktree, ticket)
  subject_id     uuid
  holder_kind    enum(chat, run)          -- a session kind
  holder_id      uuid
  principal_user_id                       -- WHO it acts for; separate from kind
  generation     int                      -- fencing, bumped on every acquire
  expires_at     timestamptz              -- server-computed, never a caller's clock
  acquired_at, last_renewed_at
  UNIQUE (subject_kind, subject_id)
```

Acquire is **one atomic statement**, not an application lock:

```sql
INSERT INTO lease (...) VALUES (...)
ON CONFLICT (subject_kind, subject_id) DO UPDATE
   SET holder_id = EXCLUDED.holder_id,
       generation = lease.generation + 1,
       expires_at = now() + :ttl
 WHERE lease.expires_at < now()
```

Renew and release are `UPDATE ... WHERE generation = :g`. `expires_at` is compared
server-side, which is also the answer to clock skew. This is what lets the `flock`
actually be deleted rather than silently inherited.

The boring alternative worth naming for the ticket subject: Postgres
`SELECT ... FOR UPDATE SKIP LOCKED` is the standard queue-claim primitive, and using
it would make this a genuine simplification rather than a re-implementation.

**Windows, with values.** A tree lease must not inherit the ticket lease's one-hour
TTL: an hour of write-exclusivity for a crashed chat is unusable.

- Lease TTL **90s**, heartbeat every **30s**.
- Idle after **10 minutes** with no work event (a timestamp, not a state, see §6).
- Preempt grace **30s**.

---

## 6. Liveness and presence

**Two lease states, not three.** Held, or expired. "Idle" is an `idle_since`
timestamp rendered as a `warn`, not a fourth thing with its own verb.

Liveness is **heartbeat and expiry only**. Process-probing is an optional per-profile
accelerator that may only ever *shorten* time-to-expiry, never extend it and never
gate a state. Where a profile does use it, it must use
`port_registry.process_alive`, not `tasks._pid_alive`, because the former already
handles the zombie case the latter gets wrong. Removing the fast path entirely without
replacing it would strand a crashed sweep's ticket for the full TTL, so the
accelerator is kept, made pluggable, and pointed at the correct implementation.

**Presence must be a shared, expiring store, not in-process memory.** Revision 1 said
presence lives in RAM and never touches a database. That cannot work here: the
participants that actually hold trees are `project_ralph.sh`, `ralph.sh` and
`queue_runner.sh`, separate OS processes that heartbeat by shelling into the task
store. An in-memory map in the backend cannot see any of them, would go empty on every
backend restart while every participant kept working, and would not survive a second
worker process.

The lesson from the 753-row inbox was **do not keep a derived duplicate of a fact
another record owns**, not "never persist a fast-changing fact". Presence is not
derived from anything; it is a primary observation with a natural expiry. Kubernetes
`Lease`, the model cited throughout, is itself persisted.

So: presence is a **TTL'd row any process can write** and nobody reconciles on read.
The existing `.runtime/heartbeats/<id>.json` channel is already this shape and is the
thing to generalize rather than replace.

**The join rule, which revision 1 declared the split and then never supplied:**

> An interactive holder's lease is renewed by its **presence**. A non-interactive
> holder's is renewed by its **work events**. Presence expiry for an interactive
> holder releases its lease immediately.

Without that rule, closing a chat tab orphans a tree for a full TTL, which is exactly
the cost §7 promises is zero, and structurally the Gitpod failure (§15.3) with the
roles swapped.

**The directory** is a read-time join over presence, leases, tickets and worktrees. It
stores nothing. It is a function on the presence module, not a module of its own.

---

## 7. Three nouns, one of them disposable

- **Worktree** — durable resource, gets the lease.
- **Ticket** — durable, says what the work is.
- **Session** — a chat or an agent run. Cheap, disposable, **never an owner**.

> A chat never owns a tree. Sessions are disposable; trees are not.

Closing a chat, abandoning it, or opening six about the same thing must cost nothing
and orphan nothing, which is true only because of the presence-releases-lease rule in
§6.

---

## 8. The verbs

- **Queue** (default) — record the request against the ticket; the holder takes it
  when it finishes. **Already built** as `chat_queue` + `chat_drain`; do not write a
  second one.
- **Steer** — deliver the request into the holder's running turn. **Best-effort with
  unbounded latency**: measured behaviour is that the model addresses it at the end
  of its current plan, not at the next tool boundary. The UI must not label this
  "now".
- **Preempt** — stop the holder, take the lease.

**No priority numbers.** The verb is the urgency. Queued requests are **FIFO** and
cancellable. The closer precedent is GitHub Actions concurrency groups (queue versus
cancel-in-progress, no scalar) rather than Kubernetes PriorityClasses.

**Authority is bound to a live human action, not to a session kind.** Revision 1 said
the operator "and any chat acting on his behalf" may preempt, which swallows the rule
it was next to, because every chat is acting on his behalf. Preempt requires an
explicit human interaction in the request that carries it. An agent may never preempt
another agent; it queues or asks.

**The sweep needs a control channel, because it has no FIFO.** `project_ralph.sh` is a
bash loop with no stdin to inject into, so both steer and yield are currently
undefined for the participant that most needs them. It already loops, so it reads
`.runtime/control/<run-id>.json` at the top of each iteration: `steer` becomes "fold
this into the next iteration's prompt", which for a sweep is a *better* boundary than
mid-turn injection, and `yield` becomes "snapshot, release, exit cleanly" through the
normal finalize path.

---

## 9. Yield is a snapshot, and force-stop must quiesce

Revision 1 said a preempted holder commits its work to the branch. That puts a
half-applied, possibly non-compiling commit permanently into the merge set, and the
next holder starts from a HEAD it did not choose.

> **Yield means snapshot to a dangling ref** (`git stash create`, or
> `git commit-tree` into `refs/wip/<session-id>`), not commit to the branch.

That still produces the SHA the design wants (diffable, revertable, resumable,
linkable from the timeline) without touching the branch or moving HEAD.

And the lapsed-grace path is not "nothing is destroyed". A different participant is
about to take the tree and will find someone else's half-applied edits
indistinguishable from its own, which is precisely the scar that motivated this whole
effort. So force-stop is followed by a **quiesce**: snapshot to the dangling ref,
restore the tree to a known state, clear a stale `.git/index.lock` left by a killed
git, and only then grant the lease. Handing over a dirty tree is never acceptable; the
snapshot is what makes cleaning it safe.

The record goes on the **worktree timeline** as structured events (acquired, steered,
preempted by whom, snapshot `<sha>`, merged), not as prose in a transcript.

---

## 10. Defaults

1. **Opened from a ticket, run or branch** → that one. Inherited, shown, changeable.
2. **Otherwise** → the app's default ref, **read-only** (§4), with a staleness banner.
   Most sessions never leave this state. Read-only matters here: the default ref is
   the most-written tree in the system.
3. **On first write** (an `EROFS`, §4) → **create a new branch by default.** Joining an
   existing one is offered, not automatic. Revision 1 defaulted to joining, which
   silently couples unrelated work to another ticket's fate given that a branch merges
   as one unit. Branches are cheap (0.27s, sibling doc §13.2), which is exactly what
   makes defaulting to new affordable.
4. **Writing with no ticket** is common and not second-class: it gets a branch a
   ticket can adopt later.

So "which branch does a new chat go in" is never asked. You pick the work; the branch
follows; the ref pill shows what was picked and lets you change it.

---

## 11. Reader staleness

A session reading a tree another session is writing will confidently describe a file
that changed two minutes ago. Contention is loud; staleness is silent.

Half of this is already handled: for *edits*, the harness enforces read-before-write
and errors when a file changed since it was read. The unhandled residual is the model
**reasoning** from a stale read it will never re-take, which no invalidation protocol
fixes because the read is already in the context window. The only real remedy is a
message into the session ("the tree moved: N files changed since your last read"),
which is a presence-driven steer, not a new protocol.

---

## 12. What "done" means: answered elsewhere

Revision 2 posed this as a yes/no ("does accepting merge?"). It is the wrong shape of
question, and the answer has its own doc:
[`changes-and-review-2026-08-22.md`](changes-and-review-2026-08-22.md).

The short version. `accept` is a legitimate, pre-existing transition
(`entity-model-2026-07-18.md:126`), but the thing being accepted today is a **status,
not an artifact**: the operator is asked to accept work he cannot see. Every mature
system separates intent from change, and derives the intent's status from the change
rather than from a self-report. So a branch produces a **Change** (local-first,
mirrored to a GitHub PR only when the app is bound to a remote), the Change is what is
reviewed and merged, and the ticket becomes `in_review` when a change opens and `done`
when it merges. Yes, accepting merges, but the merge is a consequence of approving an
artifact rather than the meaning of a status flip.

That doc also answers pre-approval: it is **auto-merge**, the standard mechanism, per
change plus an optional per-scope `merge_policy` beside the existing
`scope_execution` setting.

## 13. Service boundaries: git is its own service, and every entity is its own resource

Operator direction, 2026-08-22: if we are going to have git and version-management
tooling, it must be its own dedicated thing, and every service, API and endpoint must
be designed as a standalone RESTful resource usable for whatever is needed. Same for
every other table, entity and system.

**This design does not currently satisfy that, and neither does the code it builds on.**
`services/worktrees.py` mixes four concerns in one module: git plumbing (`_run`,
`_git_env`, raw subprocess), project-registry lookup (`PROJECTS_REGISTRY`,
`_project_record`, `_project_dir`, `_project_repo`), branch-naming and path policy
(`_kebab_branch_slug`, `_branch_name`, `_branch_path`), and merge/publish policy
(`merge_branch`, `publish_branch`). It is reachable only by passing a `project_slug`
it resolves itself, so nothing can use it to operate on an arbitrary repository. The
drift is visible at the HTTP layer too: several routers are declared as bare
`APIRouter()` with no prefix and no tag (`agents.py`, `chat.py`, `base_apps.py`), so
their paths are not a resource, they are wherever `main.py` happened to mount them.

### 13.1 The git service

**`services/git_service.py` speaks git and knows nothing else.** It takes a repository
path and arguments. It does not know what a project, an app, a ticket, a lease, a
chat, or a scope is, and it never reads a registry.

Verbs, all of them repo-path-first: `status`, `log`, `diff` (ref-to-ref, name-status
and unified), `ahead_behind`, `branch_list / create / delete`, `worktree
list / add / lock / remove / prune`, `commit`, `merge` (with fast-forward preference and
a structured conflict result), `snapshot` (the dangling-ref stash of §9), `checkout`,
`clean`, `rev_parse`, `is_ancestor`.

Its REST surface is its own: `/git/...`, taking an explicit repo reference. That is
what makes it usable "for whatever is needed" later: a CLI, a different app, an agent
tool, a future desktop client. Everything that shells out to git in this repo today
(`worktrees.py`, `project_ralph.sh`, `apply.sh`, `gc.py`, `github.py`) becomes a
caller instead of a re-implementation.

The remote half stays separate: `services/github.py` already owns the GitHub-specific
surface and keeps it. A change mirrors to a PR through that service; local merging goes
through the git service. Two services, one per system, not one module that branches on
whether a remote exists.

### 13.2 The layering, and the rule that keeps it

```
tickets ─┐
changes ─┼─→ branches (worktree records + naming/merge policy) ─→ git service ─→ git
leases  ─┘                                                        github service ─→ GitHub
presence
```

Each layer may call the one below it through its published interface. **No service
imports another service's policy**, and nothing below knows anything about the layer
above it: the git service must never learn what a ticket is.

Rules, all mechanically checkable:

- Every router declares a `prefix` and a `tag`. A bare `APIRouter()` is a bug.
- Every entity is addressable on its own: `/git`, `/branches`, `/leases`, `/presence`,
  `/changes`, `/tickets`, `/apps`, `/deployments`. A resource is never reachable only
  as a side effect of another resource's endpoint.
- Standard verbs on standard paths; a sub-resource for a state change that is not a
  plain update (`POST /branches/{id}/lease`, `POST /changes/{id}/merge`).
- Every resource appears in the generated client, so any of them can be driven from
  the frontend, a script, or an agent without a bespoke path.
- Composition happens in a thin orchestration layer or in the caller, never by one
  service reaching into another's internals.

### 13.3 What this adds to the plan

An extraction step, ahead of the records that depend on it: **B2a. Extract
`git_service` out of `worktrees.py`**, leaving `worktrees.py` as branch records plus
naming and merge policy, and repointing every other git caller at the new service. It
is a pure refactor with no behaviour change, it is provable by the existing worktree
tests, and doing it before B2 means the worktree record is built on a clean boundary
rather than having one retrofitted underneath it.

## 14. UI

To the Workshop Design System: kit primitives, tokens, lucide, five status kinds.

- **Ref pill** (chat toolbar, app header): current branch, plus the holder's avatar
  when someone else holds it. Tap for the branch menu: default ref first, then this
  app's branches with holder and status.
- **Directory sheet**: who is working where. Renders into the Tickets panel's
  existing single sheet host, which already exists (`19e8aae`).
- **Lease card, in the log**, under the message that caused it (the rule
  `ApprovalBlock` and `TicketDecision` already follow): **Add to the queue**,
  **Send to the agent** (steer, and the copy must not promise immediacy), **Take
  over** (`danger`, behind `ConfirmDialog`, naming what will be stopped).
- **Worktree timeline**: the structured event record from §9.
- **Status kinds**: `ok` free or yours, `running` someone working, `warn` holder
  idle, `danger` preempted or conflicted, `stopped` expired or reaped. Staleness is a
  banner on the reader's own view, not a status kind.

---

## 15. The plan

**B0. Ungate worktree mode for the self-repo and wire the provisioner.** This is the
highest-value step and revision 1 did not contain it. The motivating scar is agents
wiping each other's uncommitted work in the shared tree. Fixing it needs **no lease,
no presence, no directory**: pass a slug so `WT_ENABLED` can be true, and call
`provision_worktree` where the sweep currently calls `allocate_task_worktree`. Ends
the collisions outright.

**B1. Read-only mounts for sessions without a lease** (§4). The enforcement primitive
everything else assumes, and on its own it makes the shared tree safe to read.
BUILT, FLAG OFF: `services/tree_access.py` answers "does this session hold this
tree", `scoped_exec.wrap` binds the scope `--ro-bind` when the answer is not an
affirmative yes, and `WORKSHOP_TREE_LEASE_MOUNT` gates the whole thing. The
subject id is the worktree record id when a record exists for the path and the
path's realpath when none does — one identity, deterministic, because two
identities for one subject is two locks on one door. Every non-affirmative
outcome (no lease, someone else's, an unreadable store, no store at all, an
unexpected exception) is read-only; nothing turns "I could not tell" into
read-write. Per §14.3 of the branches doc the self-repo is NOT carved out, which
is also the sharpest edge on the flag: with it on, a platform-repo chat holding
no lease — including the operator's own — reads.

**The `EROFS` half of B1.** `build_streaming._maybe_write_blocked` turns the
first failed write into §10.3's offer (`write_blocked`), once per turn. It
offers; it does not create a branch, does not join one, and does not fail the
turn.

**B2a. Extract the git service** (§13.1). Pure refactor: `services/git_service.py`
takes a repo path and speaks git, knowing nothing about projects, tickets or scopes;
`worktrees.py` keeps only branch records and naming/merge policy; every other git
caller is repointed at it. Ahead of B2 so the worktree record is built on a clean
boundary instead of having one retrofitted under it.

**B2. The worktree record and lifecycle**, with `worktrees.py` as its git plumbing.
Fold in `work_driver`'s existing per-tree lock rather than adding a third.

**B3. The lease table and module** (§5), with the ticket lease **kept**, the atomic
upsert, and `tasks` calling it. Not behaviour-preserving and not free: it needs an
alembic migration on a NOT-NULL column, a `schema.gen.ts` regeneration that
`reviewer.sh` gates absolutely, a `field_occupancy_baseline.json` edit, 14
`--expect-claim-token` shell call sites, and 5 test files rewritten. It also needs a
CLI surface, because its most important caller is bash.

**B4. Presence as a shared TTL'd store** (§6), generalizing `.runtime/heartbeats/`,
plus the presence-releases-lease join rule.

**B5. Show the holder**: ref pill and directory sheet, into the existing host.

**B6. Queue and steer**: wire the existing `chat_queue`, add the sweep control file,
and write honest copy. Answer §12 first.

**B7. Preempt with snapshot-and-quiesce** (§9) and the timeline.

**B8. Session defaults** (§10), then reader staleness (§11). BUILT:
`services/session_defaults.py` is §10's resolution table as pure functions
(`resolve`, `first_write_offer`, `choose`) and names no branch — naming is
`worktrees.py`'s policy per §13.2. `services/reader_staleness.py` is §11:
`files_changed_since` (a sha, not a clock, and None rather than a guess) plus
delivery through `control.steer`, which is the presence-driven steer §11 asks
for and not a new protocol. Nothing calls `notify()` on a timer yet: WHO decides
a reader should be told rides on B5's directory, and arming it is the same
attended decision as the B1 flag.

**Track A** (from `one-fact-one-place-2026-08-22.md` §6): A1 read needs-you from
tickets and delete `run_inbox`; A2 retire the dead run notifications. A3 (one panel
host) **already shipped**.

**Dependencies**, stated because revision 1 wrongly called the tracks independent:
B5 needs B4; B7 needs B2; B8 needs B2 and B1; B6's steer half needs §12 answered.
Track A is genuinely independent of Track B.

---

## 16. Research appendix

### 16.1 Leases, fencing, liveness
Kubernetes `coordination.k8s.io/Lease`: a time-bound grant to one identity, renewed
by heartbeat, expiring silently when the holder dies. Paired with a monotonically
increasing fencing token validated at the storage layer, because a paused holder
that wakes after expiry will otherwise keep writing alongside the new one. This is
exactly the shape `tasks.py` already implements.
- kubernetes.io/docs/concepts/architecture/leases/
- singhajit.com/distributed-systems/lease/ ; acritelli.com/blog/kubernetes-leases/

### 16.2 Single-writer and presence
Figma's multiplayer is server-authoritative with presence (cursors, selection,
follow), and permission is a separate axis from presence. The generalizable part is
that *who is here* and *who may write* are two different questions, both of which
must be visible.
- figma.com/blog/how-figmas-multiplayer-technology-works/

### 16.3 Session-to-workspace binding
Gitpod and Codespaces bind many client sessions to one workspace with an idle
timeout reset by real activity. Their documented failure modes are the design
lessons: a workspace timed out while collaborators were active because only the
*owner's* idleness was measured, and the lifecycle assumed a single client
connection per workspace.
- gitpod.io/docs/configure/workspaces/workspace-lifecycle
- github.com/gitpod-io/gitpod issues 7350, 10373 ; docs.github.com Codespaces timeout

### 16.4 Steer vs queue vs preempt
The agent-harness field has converged on distinct delivery semantics: *steering*
injects into the in-flight turn at a safe boundary between tool calls; *queue*
delivers when the current work finishes; a *priority interrupt* channel stops the
tool chain outright. Naming these separately is what makes the behaviour predictable.
- github.com/anthropics/claude-code issues 30492, 64624, 71726
- github.com/langchain-ai/deepagents issue 1390 ; ably.com/blog/ai-transport-redirect-steering

### 16.5 Preemption with a grace window
Kubernetes preemption evicts a lower-priority pod and gives it its graceful
termination period before force-kill; GitHub Actions concurrency groups cancel a
superseded in-progress run, and deployments opt out of cancellation instead of
queueing behind it. Both are the "stop it, but give it a moment to become durable"
shape.
- kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/
- docs.github.com Actions concurrency ; starsling.dev/best-practices/github-actions/cancel-superseded-runs

### 16.6 Ephemeral presence (awareness protocols)
Yjs's awareness protocol is the reference design for presence that is deliberately
NOT part of the persisted document: a separate ephemeral channel carrying who is
online and what they are pointing at, re-broadcast on an interval (~15s), with any
entry not refreshed inside a window (~30s) marked offline and dropped locally. The
transferable rule is the lifetime split, not the CRDT: observations expire, decisions
persist.
- docs.yjs.dev/api/about-awareness ; docs.yjs.dev/getting-started/adding-awareness

### 16.7 Agent directories and capability cards
The agent-interop ecosystem converged on a small published card per agent (identity,
capabilities, endpoint, auth) plus a registry other agents query at runtime, rather
than hardcoded knowledge of peers: A2A's Agent Card and its discovery/registry model,
which reached v1.0 under the Linux Foundation in 2026. We borrow the SHAPE (a small
queryable card per participant, one index, discovered rather than configured) and not
the protocol, since this is one system with one principal rather than a cross-vendor
market.
- a2a-protocol.org/latest/topics/agent-discovery/ ; tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/

### 16.8 Coordination models we did not take
Contract Net (announce, bid, award), auctions, and consensus/voting protocols are the
classic answers to allocating work among self-interested peers, and blackboard
architectures are the classic answer to shared-state collaboration. With one
principal, one queue and an existing readiness scheduler, the negotiation family buys
nothing and adds deadlock and thrash modes; the blackboard half (a shared, queryable
state everyone reads and writes) is what §14 actually implements.
- sce.carleton.ca/faculty/weiss/papers/1992/weiss-seke-1992.pdf (hierarchical blackboard)
- gki.informatik.uni-freiburg.de/teaching/ss14/multiagent-systems/mas07-handout4.pdf (coordination methods)


## 17. What the adversarial review changed

Recorded so the reasoning is not lost and the same ground is not re-argued.

**Replaced outright:** fencing tokens as tree protection (§4, now the mount);
"branch on write" as an unspecified trigger (§4, now `EROFS`); the collapse of the
ticket lease into the tree lease (§5, restored); in-process presence (§6, now a
shared TTL'd store); yield-as-commit (§9, now a dangling ref); "workspace" as the
user-facing word (§3, now "branch", which the sibling doc had already decided).

**Corrected as factually wrong:** "entirely internal" (four external surfaces);
steer's latency; "provisioning is already shipped" (test callers only); the A3
ordering constraint (already shipped); "behaviour-preserving" for the lease refactor.

**Kept because the review confirmed them:** the three nouns and the disposable
session; queue/steer/preempt with no priority scalar; steer rather than preempt as
the answer to urgency; a SHA rather than a log entry as the record; refusing to
arbitrate between two of one person's chats; rejecting Contract Net and voting with
the reason stated; and the reader-staleness observation.
