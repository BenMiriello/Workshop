# Branches, worktrees, and environments: an operational model

- Date: 2026-08-20
- Status: **research + design, nothing built.** Revised 2026-08-22 (§13, §14) with measured provisioning costs, the two unprivileged CoW driver rungs, and the chat-addressing rule. This is the "own discussion about
  branches and worktrees" that
  [`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md) §8 deferred
  the ref/branch pill until.
- **Designed for the production target** (cloud, multi-tenant, our choice of
  Postgres and storage), with the local single-node install as one runtime profile,
  not the design constraint. Copy-on-write storage is a pluggable driver (§7a), so
  nothing here is hardcoded to a host filesystem. Revised 2026-08-20 after operator
  direction to stop designing around the local box's quirks.
- Builds on: the entity model in `app-is-the-entity-2026-08-20.md` (App is the
  entity, a Deployment is a running instance of an app at a ref in an
  environment), the worktree/GitHub plan in
  [`github-worktree-orchestration-plan-2026-07-15.md`](github-worktree-orchestration-plan-2026-07-15.md)
  and ADR [`0003-github-is-per-project-binding.md`](../decisions/0003-github-is-per-project-binding.md),
  and the runner in [`../app-platform-runner.md`](../app-platform-runner.md).
- Companion research is inline in §11; the load-bearing sources are cited there.

---

## 1. What the operator asked for

Recorded close to the intent, because the shape matters.

> Have a proper system for branches, worktrees, and potentially forks: our own
> local git version management AND the integration with GitHub, where work can
> originate in the app OR start on GitHub and be imported. Let different agents
> and chats work in separate environments so they stop competing on the same
> files (getting confused, blocking, unable to commit or deploy). Have different
> builds of apps running at once so the user can test each while it works. Have a
> system and protocol for merging those back together. Don't let a runaway number
> of builds run at once, that gets expensive and wasteful: spin up on demand,
> spin down when idle, be efficient with memory and storage. Decide what happens
> to the database: do we duplicate the whole DB per worktree, sometimes, or never,
> and what happens when the base schema changes underneath. Make it conceptually
> and visually intuitive: you can see what you are looking at, which version is
> running, and how many. Handle the root/default branch, on remote if there is
> one. Defer deployments (their own effort, branch off this). Look at how
> Conductor and others do it (they are heavily worktree-focused, for good reason).

The rest of this doc answers each of those, grounded in what we already have.

---

## 2. The core decision, up front

**One isolation primitive: the git worktree, one per unit of work (a chat, a
ticket run, or an agent task). Everything else is a policy layer on top of it.**

This is not a new idea here; it is the mainstream answer and it is already half
built in this tree. Conductor, Crystal, Claude Squad, Vibe Kanban, and Uzi all
converge on worktree-per-task over a shared object database (§11.1). Our own
`services/worktrees.py` already does the full local lifecycle. What is missing is
not the primitive. It is the four layers around it that make it usable:

1. **Provisioning** (§5): the run-once setup that gives a fresh worktree the
   untracked state it needs to actually run: dependencies, `.env`, a port block, a
   database if it needs one.
2. **Running builds and their lifecycle** (§6): standing an app up from a
   worktree, on a unique address, and spinning it down when idle so N builds do
   not run forever.
3. **The database policy** (§7): what a worktree gets for data, decided by what
   the work does, not duplicated by reflex.
4. **Merge and GitHub** (§8): getting the work back to the trunk, locally or via a
   PR, and letting work originate from GitHub too.

And one layer under all of it: **the model and UI** (§3, §4, §9) that make "which
version is running, and how many" answerable at a glance.

The single most important framing, from the research and from our own scars: **a
worktree isolates the working tree, not the running services.** Two worktrees
share nothing in git, but if both run `npm run dev` on port 5173 or both open a
compose project named `ws_budget`, they collide. Overlapping *edits* never
conflict during work; they only surface at merge (§8.4). So the design effort is
almost entirely about the *runtime* namespace (ports, compose project names,
databases, URLs) and the *merge* protocol, not about git itself.

---

## 3. The data model

Three records. The first two already exist in some form; the third is new.

### 3.1 App (exists, being formalized)

Per `app-is-the-entity`, the App is the one entity the user names. It carries the
repo binding (`repo_id` → remote_url + `default_branch`), the canonical working
tree, and its collections (chats, tickets, runs, deploys, notes, agents). Nothing
in this doc changes the App record except to confirm two fields it already needs:

- `default_branch` (already tracked in `.runtime/projects.json` `repo.default_branch`).
  This is the **root branch**: the trunk that worktrees branch from and merge
  back into, and the only branch the post-commit hook will build/publish from.
- `repo.remote_url` (already tracked, optional). Present → GitHub-backed, merges
  can go through a PR. Absent → local-only, merges are local fast-forwards. Both
  paths must be first-class (this is ticket C3, already done in `worktrees.py` but
  gated off in `project_ralph.sh`; see §8.2).

### 3.2 Worktree (exists as files, needs a record)

Today a worktree is a directory under `branches/<slug>/<branch>` plus a git ref
`wt/<slug>/<task-id>`, tracked only by git itself and by whatever shell variable
created it. That is enough for a headless ralph sweep and nothing else. To surface
it in the UI and manage its lifecycle, it needs a real record. Proposed shape,
following the RLS/tenancy pattern of the `Ticket` model:

```
worktree
  id            uuid pk
  app_id        uuid fk -> app
  branch        text        -- wt/<slug>/<task-id>, unique per app
  base_ref      text        -- what it branched from (origin/main or local main)
  dir           text        -- absolute path under branches/<slug>/<branch>
  owner_kind    enum(chat, run, agent, manual)
  owner_id      uuid null   -- the chat/run/agent that owns it, for the UI back-link
  slot          int         -- 0..N-1, the port-block / namespace index (see §6.2)
  status        enum(active, merging, merged, parked, discarded)
  db_mode       enum(shared, branched)       -- see §7
  db_name       text null   -- the CoW branch database, when db_mode=branched
  created_at, last_active_at, merged_at
  + org_id / RLS columns per the Ticket model
```

`last_active_at` is what the idle sweeper (§6.3) reads. `slot` is the load-bearing
field for the runtime namespace: a small integer, reused when a worktree is
reaped, that deterministically fixes this worktree's port block, compose project
suffix, and database name so nothing has to be searched for or can collide.

### 3.3 Deployment (exists as `.runtime/app-deploys.json`, gains a ref + a lifecycle)

`app-is-the-entity` already defines a Deployment as "a running instance of an app,
at a **ref**, in an **environment**." Today `app-deploys.json` carries a per-service
status/port/url/health but no ref and no environment: it assumes one running
instance per app, on the canonical tree. This is exactly the record that must gain:

- `ref` — which worktree/branch this deployment is running. `main` for the trunk
  build; `wt/<slug>/<task-id>` for a branch build.
- `environment` — an enum the port window already implies: `preview` (a branch
  build, ephemeral, idle-stops), `deploy` (the app's own standalone origin),
  `integrated` (proxied into the shell). The port registry already reserves
  `preview 8500-8599 / deploy 8600-8699 / app 8700-8799`; the Deployment's
  environment picks the window.
- `desired` and `idle_stopped_at` — the lifecycle fields (§6.3). `desired` already
  exists; the idle field is new.

The key model statement: **one App has one canonical tree and its `default_branch`,
and 0..N worktrees, each of which may have 0..1 running preview Deployment.** "How
many builds are running" is a count over Deployments with a non-stopped status.
"Which version" is the Deployment's `ref`.

---

## 4. The operational model, end to end

A worktree's life, as the system should run it:

1. **Open.** A chat, a ticket run, or the operator asks for isolated work on an
   app. The platform allocates the next free `slot`, creates the worktree
   (`git worktree add -b wt/<slug>/<task-id> <dir> <base_ref>`, locked as today),
   writes the `worktree` record, and runs provisioning (§5).
2. **Work.** The agent/chat works in `dir`. Its turns already run under bwrap
   scoped to that tree (Layer 1, default-on). Nothing it does touches the trunk or
   another worktree's tree. It commits freely to its own branch.
3. **Run (optional, on demand).** The operator hits Run (or the agent decides it
   needs to show something). A preview Deployment stands the app up from `dir` on
   this worktree's port block, on a unique URL (§6). It idles-stops after a
   timeout and wakes on the next request or an explicit Run.
4. **Review.** The operator sees the diff and the running preview side by side,
   keyed to the same worktree. This is the whole point of previews: test before
   merge.
5. **Merge.** On approval, the platform rebases the branch onto the current base,
   replays `rerere` resolutions, and either fast-forwards locally (unbound app) or
   pushes and opens/merges a PR (bound app). On conflict it **parks**, does not
   loop. (§8.)
6. **Reap.** On a clean merge (or an explicit discard) the worktree is removed,
   its branch deleted, its preview Deployment torn down, its DB branch (if any)
   destroyed, and its `slot` freed. A background sweep reaps anything whose branch
   is already an ancestor of the base, and TTL-reaps abandoned worktrees.

Steps 1, 2, 5, 6 exist in `worktrees.py` and `project_ralph.sh` today for the
headless case. Steps 3 and 4, and making 1/5/6 driveable from the UI for an
interactive chat, are the new work.

---

## 5. Provisioning: the run-once setup (the load-bearing primitive)

Every serious tool converges on **a checked-in, per-worktree setup hook that runs
once at creation** (Conductor `[scripts].setup`, Vibe Kanban Setup Script, Uzi
`devCommand`). It is where the untracked state that `git worktree add` does not
carry gets recreated: dependencies, env files, seed data, symlinks. The research
is unambiguous that if you build one thing well, build this (§11.1).

For us this maps onto the App manifest (`workshop.app.json`), not a new config
file. Proposed additions, all optional:

- `setup` — a command run once in the fresh worktree (`npm ci`, `pip install`,
  etc.). Defaults inferred from the detected stack, same detection
  `compose_gen.py` already does (bun/python/deno/node).
- `worktree_include` — globs of gitignored files to copy from the canonical tree,
  defaulting to `.env*` (Conductor's default). This is the declarative shortcut for
  the common "copy my local secrets" case, so it need not be scripted.

Two rules, independent of any particular host:

- **Reinstall dependencies, do not copy or symlink `node_modules` between
  worktrees.** Copying multiplies storage per worktree; symlinking reintroduces the
  version conflicts worktrees exist to prevent (§11.1). Reinstall via the package
  manager's content-addressed store (npm/pnpm/bun all hard-link from a shared
  global store on the same volume), so the marginal cost per worktree is close to
  zero bytes even though it is a real install step. Where the platform wants
  workspace trees to branch as cheaply as databases, the same copy-on-write storage
  driver that serves DB branches (§7a) serves the workspace volume too, and the
  dependency directory branches for free.
- **Provisioning must be idempotent and killable**, because a turn can be
  interrupted by a backend restart mid-setup. Same discipline the runner already
  applies.

---

## 6. Running builds: preview deployments, ports, and spin-down

This is where "different builds running so the user can test them" and "don't let
a runaway number run" both live. The mechanism already mostly exists in
`app_runner.py` + `container_runtime.py` + `port_registry.py`; it needs a ref, an
environment, and an idle lifecycle.

### 6.1 A preview build is a Deployment at a worktree ref

Standing up a preview = the existing `app_runner` path (`compose up -d` for the
app's `ws_<slug>` project) but with the source bind-mount pointed at the
worktree's `dir` instead of the canonical tree, and the compose project name and
ports namespaced by the worktree's `slot` (§6.2). No new runner is needed; it is
the same runner with a ref and a namespace.

### 6.2 The runtime namespace: one `slot` fixes everything

The single collision-proofing idea, taken from Conductor's `CONDUCTOR_PORT` +
`CONDUCTOR_WORKSPACE_NAME` (§11.1): derive every shared-namespace resource from the
worktree's small integer `slot`, so two worktrees can never clash and nothing has
to be discovered at runtime.

- **Compose project**: `ws_<slug>__wt<slot>` instead of `ws_<slug>`. (Canonical
  trunk build keeps the bare `ws_<slug>`.)
- **Ports**: a *block* per slot out of the preview window `8500-8599`, e.g. 8
  ports per slot → slot 0 = 8500-8507, slot 1 = 8508-8515, giving 12 concurrent
  preview slots in the existing window. Block, not single, because a real app is
  web + maybe a db + maybe a worker. The app consumes its base port via env
  (`--port $WORKSHOP_PORT`), exactly the Uzi `$PORT` / Conductor `CONDUCTOR_PORT`
  contract. `port_registry.allocate(kind="preview")` already exists; it allocates
  singles today and needs a block mode.
- **Database name**: `ws_<slug>__wt<slot>` when a cloned DB is warranted (§7).
- **URL**: `wt<slot>-<slug>.<preview-host>` via a single Host-keyed reverse proxy
  (§6.5), so every simultaneous build has a stable unique address without
  per-build proxy edits.

Because `slot` is reused on reap, the namespace stays small and dense, which
matters for the port window and for the operator's mental model ("slot 2 is the
budget refactor").

### 6.3 The deployment tiers, and what may spin down

Model every deployment on two independent axes (the production model, §11.2):
**durable footprint** (provisioned disk, plus a RAM reservation while running) and
**runtime state** (hot / warm-idle / suspended). Keeping app state off the instance
(a managed or branched DB, object storage) is what makes suspend free and wake a
plain boot. Then tier deployments explicitly:

- **Primary deployment (one per app) — never idle-stopped while addressable.** This
  is the "at least one running per app" guarantee. It is pinned hot up to the
  hot-capacity budget (§6.4); the operator's "the main build shouldn't spin down if
  it's been running a while" is exactly this tier.
- **Branch / preview deployments — scale-to-zero eligible and TTL-bounded.** These
  idle-suspend after a window (propose ~24h of no traffic, the operator's "after
  about a day"), and are the first things reclaimed under pressure. A hard TTL
  destroys one on branch merge/close.

**Spin-down** is `docker stop` (or a memory-snapshot suspend for sub-second wake)
of a branch deployment whose `last_active_at` passed the window. Suspend is not
reap: the worktree, branch, and DB branch survive; only the running containers
stop. **Wake-on-request** puts a request-holding proxy in front (the activator
pattern, §11.2): a request to a suspended branch is buffered while its stack
resumes, then proxied through. This is how "spin it up as needed when the person
wants it" works without keeping everything hot. A minimum-residency guard stops a
just-woken deployment from being immediately re-suspended (anti-thrash).

**TTL-reap** (nightly) fully removes worktrees whose branch merged (already an
ancestor of base → `cleanup_merged`) or whose branch is abandoned past a longer
TTL: remove worktree, delete branch, tear down deployment, destroy its DB branch,
free the slot.

### 6.4 The budget: a multi-ceiling quota, whichever binds first

Governance is a per-user (per-org, in multi-tenant production) budget with several
ceilings evaluated together at admission, exactly the "whichever it hits first"
model the operator described. Borrowed straight from Kubernetes ResourceQuota
semantics (§11.2): a tenant carries, simultaneously,

- a **count** ceiling (max concurrent running deployments),
- a **disk** ceiling (total provisioned storage across the tenant's deployments and
  DB branches),
- a **RAM** ceiling (total reserved memory while running).

A new deployment is admitted only if it fits under **every** ceiling; it is refused
naming the **first** one it would exceed. The operator's instinct that the cap
might be a deployment count OR a disk size and "whatever it hits first" is precisely
this: you do not pick the binding limit, the admission check enforces the
intersection.

**Prioritized allocation** answers "guarantee one per app, extras only if space
allows": reserve budget for primaries first, then admit branch/preview deployments
against whatever headroom remains. Four apps → four primary slots guaranteed;
branch previews compete for the rest.

**Not limiting the number of apps (for now).** Per the operator this stays a later
product decision, not a mechanism we build now. The clean way to leave it open: an
app's primary is guaranteed a slot that is at minimum **wakeable on demand**, not
guaranteed to be **always hot**. Up to a hot-capacity, primaries stay warm; a user
who loads a cold app (their 11th, in the operator's example) gets it woken on access
via the same proxy path, and if the hot set is full the **least-recently-used
branch deployment is evicted first, never a primary**. So the system scales past the
hot capacity by rehydration, not by a hard app cap, and a cap can be added later as
policy without redesign.

**Refuse vs evict, by axis.** Disk is committed and cannot be reclaimed cheaply, so
a disk-ceiling overrun **refuses** the new deployment (or forces a reap of an
abandoned branch first). The volatile running/RAM axis **evicts** the LRU branch
deployment to make room, with the residency guard. `CONCURRENT_WORKTREES_MAX`
(declared at `project_ralph.sh:65`, never read) becomes the count ceiling's home.

**Metering.** Meter each deployment on the same three axes the budget uses (running
instance-seconds, peak RAM, provisioned disk) so the usage view shows which
deployment is consuming which ceiling, and "what is eating my budget" maps 1:1 to
what can block the next deploy. This is the same usage-ledger surface already being
built for chat token metering, extended to deployments.

### 6.5 URLs: one Host-keyed proxy

Reverse-proxy every running build by Host header: `wt<slot>-<slug>.<preview-host>`
→ the slot's base port. Wildcard DNS (`*.preview.<domain>`) plus one proxy rule, no
per-build config; the same proxy is the request-holding activator for wake-on-demand
(§6.3). A wildcard TLS cert needs DNS-01 validation (HTTP-01 cannot issue
wildcards, §11.4). In a local runtime profile this collapses to a `*.localhost`
mapping with no cert.

---

## 7a. Storage is a pluggable driver, never the host filesystem

Before the database policy, the layer under it. The platform must run in the cloud
(where we choose the volume and filesystem) and, for anyone who self-hosts, on a
machine whose filesystem we do not control. So copy-on-write is never assumed on the
host and never hardcoded. It lives behind a narrow **storage driver** the platform
owns, modelled on Docker's graphdriver (§11.5):

```
StorageDriver:
  caps: {instant, space_efficient, writable_clone, granularity}
  snapshot(volume) -> snapshot
  clone(snapshot)  -> writable branch      # copy-on-write
  reset(branch, parent)                    # complete overwrite from parent
  destroy(branch | snapshot)
  list_branches(parent)
```

Everything above this line (worktrees, DB branches, the reconciliation workflow in
§7) calls only these verbs and never knows the mechanism. Implementations, in
selection order best-to-fallback:

1. **managed-branching** — Neon (Postgres, page-level CoW branch = an LSN pointer,
   scale-to-zero compute) or Aurora fast clone. A branch is O(1) regardless of DB
   size; idle branches are near-free. The default cloud DB-plane driver.
2. **pg-clone** — Postgres 18 `CREATE DATABASE ... STRATEGY=FILE_COPY` with
   `file_copy_method=clone`, on a reflink/ZFS volume the platform owns. Sub-second
   in-cluster clones without a managed service.
3. **zfs-clone** — `zfs snapshot` + `zfs clone` of a dataset, on a platform-owned
   volume: an EBS volume we format ZFS on EC2, or **Amazon FSx for OpenZFS** mounted
   over NFS (CoW lives entirely in the service, the app host can be any filesystem).
   This is the self-hosted equivalent of Neon; Postgres.ai's Database Lab Engine is
   the turnkey packaging of it (§11.5).
4. **reflink** — `cp --reflink` on XFS-reflink or Btrfs, per-file CoW. Good for the
   workspace/tree plane.
5. **lvm-thin** — block-level CoW under any filesystem including ext4, where a spare
   block device exists. The fallback that still gives real branching on a plain host.
6. **plain-copy** — `cp -a`. Correct, not instant, not space-efficient. The driver
   advertises `instant:false` so callers expect a slow branch. Last resort only.

The load-bearing rule: **the copy-on-write volume is always one the platform creates
and formats, never the host root filesystem.** In the cloud that is a dedicated
EBS/FSx volume. On a self-hosted box with no CoW filesystem, the platform stands up
a ZFS pool on a file-backed vdev inside its own data directory (or LVM-thin on a
spare device) and gets real instant clones anyway. The host's filesystem is never on
the critical path and is never assumed. There is no "we are on ext4 so we cannot"
in this design; there is only "this runtime profile selected the lvm-thin driver."

Two driver instances, not one: a **DB-branch driver** (managed / pg-clone / zfs) and
a **workspace-branch driver** (zfs / reflink / plain-copy), because a database
branch and a filesystem-tree branch have different granularity and consistency
semantics. They share the interface, not the implementation.

## 7. The database policy: share, branch on write, reconcile schema not data

The operator's hardest question: duplicate the whole DB per worktree, sometimes, or
never; clone cheaply when a branch changes the schema; and keep the ones that share
a database synchronized. The research gives one organizing principle that answers
all of it (§11.3):

> **Schema and data reconcile by opposite strategies. Schema is a mergeable
> artifact that flows up through one reviewed source of truth. Data is not merged;
> a branch's data is refreshed one-way from the parent, or thrown away and
> re-cloned. Bidirectional data merge is the multi-master problem and is designed
> out.**

### 7.1 Share by default; branch (CoW) only when a branch must mutate

Pick a worktree's `db_mode` by what the work does:

- **`shared`** (the common case). The worktree reads, or writes non-destructively,
  against a **shared** database. Most worktrees never need their own database at all.
  Multiple worktrees sharing one database stay synchronized because every schema
  change to it is backward-compatible (§7.2), so a branch on the old shape keeps
  working while another is on the new one. This is the direct answer to "we probably
  don't need to create a different database for everything" and "keep the ones
  sharing a database synchronized."
- **`branched`** (when a worktree needs its own schema change, a destructive
  migration, or write isolation that would break siblings). Here the storage driver
  makes a **copy-on-write branch** of the parent database, ideally without copying
  the data (Neon LSN pointer, or a ZFS/pg-clone CoW clone) — the operator's "clone
  it, ideally without copying everything." The branch diverges only on the pages it
  writes.

The rule of thumb: **share until a branch needs to mutate schema or needs write
isolation, then give it a CoW branch.** Expand/contract (§7.2) is what lets you
*delay* branching, because a backward-compatible change can land on the shared
database and every sharing worktree keeps running.

### 7.2 Reconciliation: schema flows up, data refreshes down

**Schema** is the mergeable half, and this is how the shared and branched databases
stay coherent:

- **One declarative source-of-truth schema per app** (Atlas-style), versioned into a
  migration on merge. CI gates on a single migration head (the Alembic
  multiple-heads failure, §11.3), so two branches cannot silently diverge the schema.
- **Every schema change is expand/contract (parallel change):** add new structures
  additively, dual-write and backfill, then drop the old once nothing reads it. This
  is what keeps a shared database serving multiple branch versions at once, and it is
  the mechanism behind "keep them synchronized."
- **Promoting a branch's schema up** goes through a three-way merge / deploy-request
  gate (PlanetScale's model): diff the branch schema against the parent's *current*
  schema, not the parent at branch-cut time, surfacing real conflicts; apply through
  a serialized queue. This is the "reconcile and re-merge" the operator asked for,
  and it is well-defined for schema.

**Data** is the disposable half:

- A branch database is refreshed from the parent by **reset / re-clone** (a complete
  overwrite, `git reset --hard` for data), then replaying checked-in **seed
  scripts/fixtures**. Local test state lives as a small replayable delta, not a
  hand-curated snowflake, so a refresh loses nothing that matters.
- The only sanctioned parent→branch data flow beyond re-clone is **one-way logical
  replication** to seed a long-lived branch that must track parent data, with its
  known limits (DML only, no DDL, no sequences). Never bidirectional, never a merge.
- Data does not flow back up. Two branches that both wrote rows are never
  auto-merged; that is multi-master and it is lossy, so the design excludes it.

When the base schema advances, a stale branch database is not migrated in place; it
is reset/re-cloned so its schema is always "parent migrations at provision time" and
nothing diverges silently.

### 7.3 PII and multi-tenancy

In production this is multi-tenant and holds real financial/location/health data.
The platform Postgres is already RLS-tenant-isolated. A branched database must clone
from a **scrubbed/synthetic base**, never live tenant data, so branching never
multiplies PII exposure; and the shared/`shared` default (reads or synthetic seed)
is the privacy-preserving path anyway. This composes with the existing tenancy model
(RLS, per-org isolation) rather than working around it.

---

## 8. Merge and GitHub

### 8.1 The merge ceiling is already decided

ADR 0003 and the 2026-07-15 plan set it and this doc does not raise it:
rebase-onto-base, replay `rerere`, fast-forward or PR, **park on conflict, never
loop**. No merge queue, no multi-tier conflict resolver, no Coordinator-Verifier
fleet: those are the multi-tenant cruft the single-operator rule forbids. The one
addition the research suggests and that fits: **AI-assisted conflict resolution as
an option on a parked branch** (Crystal, Sculptor, Conductor all offer it) — hand
the conflict back to an agent in that worktree rather than resolving it centrally.
Optional, operator-invoked, not automatic.

### 8.2 Local and remote both first-class (C3)

`worktrees.merge_branch` already has both a local ff/merge path (unbound app) and
the PR path (bound app). The gap, named by ticket C3 (done in `worktrees.py`) but
still gated off in `project_ralph.sh:632` where `WT_ENABLED` requires
`repo.remote_url`, is that an unbound app never enters worktree mode at all. The
fix is to let worktree mode turn on for unbound apps and take the local completion
path. This doc ratifies that: **worktree isolation must not require a remote.** The
workshop self-repo has no remote and is the primary place agents collide.

### 8.3 Work originating from GitHub (import)

`POST /projects/import` already clones an arbitrary repo URL, but with two holes to
close before it is the "start on GitHub, integrate in the app" path the operator
described: it does **not** run `reject_blocked` (no Veritus-owner guard on the
clone URL — this must be added, it brushes a hard rule) and it does **not** set the
`repo` binding (so an imported app cannot publish). Both are small and both are
required. Import then becomes symmetric with create: an app can originate in the
app (create repo) or on GitHub (import + bind), and either way it has a
`default_branch` and can spawn worktrees.

Forks (the operator's "potentially forks"): two distinct meanings, keep them
separate. **Platform fork** = the instance-branch model already documented in
`instances-and-forking.md` (this box is a permanent downstream fork of the platform
repo); untouched here. **App fork** = "install a copy of someone's app and diverge
it," already sketched in `app-separation-and-github` as marketplace fork over git
bundles; a worktree is the natural mechanism (fork = a long-lived branch/worktree
that never merges back), but this is deferred with deployments.

### 8.4 Overlapping edits: partition, don't deconflict

The research is blunt: worktree isolation guarantees conflicts surface only at
merge, never during work, and **no tool prevents overlapping live edits** (§11.1).
The only real mitigation is up-front **directory ownership**: decompose work so
agents own disjoint paths, state it in the task spec, and review diffs centrally
before merge. This is a task-decomposition discipline, not a runtime lock, and it
is the honest answer to "agents get confused working the same files": the fix is
that they no longer work the same *tree*, and the residual risk (two branches
edit the same file, conflict at merge) is handled by park-on-conflict, not
prevented.

---

## 9. The UI: making "which version, how many" answerable

The deferred ref/branch pill, now designable. Three surfaces, all to the Workshop
Design System (kit primitives, tokens, lucide, five status kinds).

### 9.1 The ref pill (on an app)

A pill on the app header showing the current ref: "main" for the trunk, or the
branch/worktree name. Tapping it opens a menu listing the app's worktrees, each
row: branch name, owner (the chat/run/agent that opened it, linked), status
(active / running preview / parked / merged), and whether a preview is live and on
what URL. "main" is always first. This is the branch-selector concept from the
handoff, made concrete: it selects which working tree the app page is looking at,
and it is where you Run, Merge, or Discard a branch.

### 9.2 The environments view (across apps)

One page answering "how many builds are running right now and what are they."
A list of live preview Deployments: app, ref, slot, URL, status, idle countdown,
memory/port in use. This is the runaway-count guardrail made visible: the operator
can see the 3 running previews, stop one, or let the idle timer do it. It reuses
the run-status view patterns already designed (single canonical status, links to
ticket/chat/agent).

### 9.3 In-chat

A chat working in a worktree shows its branch inline (it already carries a scope;
this adds the ref) and, when it stands up a preview, a live element with the URL and
a stop control, collapsing the way filed-work cards already collapse. The chat is
the `owner` of its worktree, so "edit in original chat" from the environments view
lands back here.

The visual principle, per the operator: it must be obvious at a glance which
version you are looking at and how many are running. The ref pill answers the
first; the environments view answers the second.

---

## 10. What this integrates with, and what it must not break

- **`app-is-the-entity`**: this doc supplies the deferred ref/branch discussion and
  the Deployment `ref`/`environment` fields that model already names. It does not
  change the App-is-the-entity decision; it depends on it (the worktree and
  deployment records both FK to `app_id`, so they want the app-table migration in
  §4 of that doc to land first, or to be built against `.runtime/projects.json`
  slugs in the interim).
- **The runner** (`app_runner`/`container_runtime`/`port_registry`): reused, not
  replaced. New: a block-allocation mode on the port registry, a `ref`/namespace on
  the compose project, and idle-stop in the watcher.
- **worktrees.py / project_ralph.sh**: the local lifecycle is reused; the work is
  ungating worktree mode for unbound apps (C3) and giving worktrees a DB record and
  a UI, not rewriting the git plumbing.
- **The post-commit hook**: unchanged and load-bearing. It already refuses to
  build/publish from a linked worktree under `branches/` and only builds the
  default branch. That is precisely the guarantee that keeps unmerged worktree code
  from ever reaching the public origin, and previews must ride the *preview* window
  and proxy, never the public build. Do not touch it.
- **bwrap Layer 1**: each worktree turn already runs scoped to its tree. Worktrees
  make that scoping more meaningful (the tree is genuinely separate), so this
  composes cleanly.
- **The storage driver** (§7a): the platform never assumes copy-on-write on the
  host. The cloud profile selects a managed-branching or ZFS/reflink volume the
  platform owns; a self-hosted profile falls back down the driver chain to lvm-thin
  or plain-copy. The DB policy (§7) and the resource budget (§6.4) are written
  against the driver's advertised capabilities, not a specific filesystem, so the
  same code runs on Neon, on an EBS-ZFS volume, or on a laptop.
- **The tenancy model**: production is multi-tenant with RLS per org. Branch
  databases clone from a scrubbed base and compose with RLS; nothing here weakens
  the existing isolation. Per-user resource budgets (§6.4) are the multi-tenant
  governance layer, which is legitimate production infrastructure, not the
  single-user guardrail cruft the working agreement forbids.
- **Veritus separation**: import must call `reject_blocked` (§8.3). Non-negotiable.

---

## 11. Research appendix (sources)

### 11.1 Worktree orchestrators (Conductor, Crystal, Claude Squad, Vibe Kanban, Uzi, Sculptor)

Worktree-per-task over a shared object DB is the mainstream choice; Sculptor is the
container-per-agent outlier (stronger isolation, image-build cost). The load-bearing
primitives every tool converges on: a run-once **setup script** for untracked state;
a **port block or ranged port per workspace** exposed via env (`CONDUCTOR_PORT..+9`;
Uzi `portRange`+`$PORT`); **reinstall deps, do not symlink** (pnpm/bun content-store
makes it cheap); **namespace non-port resources by a workspace name**; merge either
git-native (rebase/squash in-app) or PR-centric, several with AI conflict help; and
**nothing prevents overlapping live edits** — partition directories up front,
review at merge.
- conductor.build/docs/concepts/git-worktrees, /reference/scripts, /reference/files-to-copy, /guides/review-and-merge
- github.com/stravu/crystal ; deepwiki.com/stravu/crystal/5.1-git-worktree-management
- github.com/smtg-ai/claude-squad ; deepwiki.com/smtg-ai/claude-squad/4.1
- github.com/BloopAI/vibe-kanban ; vibekanban.com/docs/workspaces/git-operations.md
- github.com/devflowinc/uzi ; skeptrune.com/posts/git-worktrees-agents-and-tmux
- imbue.com/blog/sculptor-announce, /blog/containers
- jsmanifest.com/parallel-ai-agents-git-worktrees ; dev.to/sahil_kat parallel-coding-agents ; gptfrontier.com preventing-database-and-port-collisions

### 11.2 Resource governance, scale-to-zero, wake-on-request

Every platform puts a **request-holding proxy** in front and boots idle instances on
the next request: Fly Machines auto-stop/start (one machine per pass, `min_machines_running`
pins the primary), Cloud Run scale-to-zero + `min-instances`, Knative's **activator**
(buffers the request, scales from zero, then proxies through) — the canonical
wake-on-request reference — KEDA http-add-on, Neon autosuspend (storage/compute split
is what makes suspend free). The universal split: the **primary stays hot**
(`min-instances ≥ 1`), **branch/preview deployments scale to zero and get TTL-destroyed
on merge**. Budgets are **Kubernetes ResourceQuota** semantics: a tenant carries count
+ RAM + storage ceilings simultaneously, admission rejects on the **first** exceeded
(whichever binds first), paired with LimitRange per-object defaults. Over-subscription
uses **LRU eviction of the volatile running/RAM axis with a residency guard**; disk
overruns **refuse** rather than evict (committed disk can't be reclaimed cheaply).
Meter per deployment on the budget's own axes (instance-seconds, peak RAM, disk).
- fly.io/docs/reference/fly-proxy-autostop-autostart, /docs/launch/scale-count ; cloud.google.com/run/docs/about-instance-autoscaling, /configuring/min-instances
- knative.dev/docs/serving/autoscaling/scale-to-zero ; kubernetes.io/docs/concepts/policy/resource-quotas, /limit-range
- neon.com/docs/introduction/scale-to-zero ; cloud.google.com/run/pricing (per-deployment metering)

### 11.3 Production database branching + schema/data reconciliation

The organizing finding: **schema and data reconcile by opposite strategies.** Schema
converges through one reviewed source of truth + a serialized apply path; data is not
merged, only re-cloned/reset. Cheap CoW branching: **Neon** (page-level, branch = LSN
pointer, scale-to-zero, near-free idle branches — managed), **Aurora fast clone**
(shared pages, CoW on write, 15-clones-per-lineage ceiling, same-Region), self-hosted
**ZFS/LVM-thin snapshot+clone of PGDATA** and **Database Lab Engine** (ZFS thin clones,
~10s for 1TiB), and **PG18 `file_copy_method=clone`** on reflink storage. Reconciliation:
declarative-schema-as-source-of-truth (**Atlas**/Skeema) versioned on ship + CI gate on
a single migration head (the **Alembic multiple-heads** failure); **expand/contract**
(parallel change) so one shared DB serves multiple branch versions; **PlanetScale
three-way schema merge** against the parent's *current* schema; and **reset-from-parent
+ replay seeds** for data (bidirectional data merge is the multi-master problem — LWW
loses writes, and is designed out). Logical replication is one-way seeding only (DML,
no DDL, no sequences).
- neon.com/blog/get-page-at-lsn, /docs/introduction/branching, /guides/reset-from-parent ; docs.aws.amazon.com/AmazonRDS Aurora.Managing.Clone
- planetscale.com/blog/database-branching-three-way-merge-schema-changes ; martinfowler.com/bliki/ParallelChange.html
- atlasgo.io/concepts/declarative-vs-versioned ; alembic branches ; postgres.ai/docs/database-lab ; postgresql.org logical-replication-restrictions

### 11.4 Routing + concurrency

Wildcard DNS `*.preview.domain` → one Host-keyed reverse proxy (nginx wildcard
server_name; Traefik HostRegexp), doubling as the wake-on-request activator; wildcard
TLS needs DNS-01. No universal concurrent cap — it is a budget knob (cap/label-gate +
scale-to-zero + TTL), tuned to cost. In production the cap is the per-tenant multi-ceiling
quota (§11.2); in a local profile it collapses to a `*.localhost` map.
- hazaveh.net wildcard-subdomain-router-in-traefik ; temps.sh/blog/how-to-set-up-preview-environments ; instadevops.com/blog/ephemeral-preview-environments-guide

### 11.5 Pluggable copy-on-write storage (driver abstraction)

CoW filesystems by granularity: **ZFS** (block, native, `snapshot`+`clone`, tune
`recordsize`, dedup off), **Btrfs** (block, native, weak for DB random writes),
**XFS reflink** and PG18 `file_copy_method=clone` (file/extent, needs a reflink FS),
**LVM-thin** (block-level CoW *under any FS including ext4*, watch pool exhaustion),
**OverlayFS** (whole-file copy_up — workspace layering only, wrong for DB data);
**ext4 has no native CoW.** Making it host-independent: run ZFS on a **file-backed
vdev** or LVM-thin on a spare device so a plain host still gets clones; on AWS own an
**EBS volume formatted ZFS/XFS-reflink** or use **FSx for OpenZFS** (CoW in the service,
app host FS irrelevant). The pattern is **Docker's storage/graphdriver**: a fixed verb
set, one driver per backend, capabilities advertised — the platform code never branches
on mechanism. Selection order: managed-branching → pg-clone → zfs/FSx → reflink →
lvm-thin → plain-copy.
- openzfs.github.io (snapshot/clone, workload tuning) ; postgresql.org/docs/18/sql-createdatabase.html ; man7.org/linux/man-pages/man7/lvmthin.7.html
- docs.aws.amazon.com/fsx (OpenZFS snapshot/clone), /ebs (incremental snapshots) ; postgres.ai/docs/database-lab ; docs.docker.com/engine/storage/drivers/select-storage-driver

---

## 12. Proposed build sequence (for a later effort, not filed here)

Ordered so each stage is usable on its own; this chat is Direct mode, so nothing is
filed. Rough shape:

1. **Ungate worktree mode for unbound apps** (finish C3 wiring in `project_ralph.sh`)
   and give the worktree a real record (§3.2). Smallest useful unit: agents stop
   colliding on the self-repo tree.
2. **Provisioning hook** (§5): `setup` + `worktree_include` on the manifest, with
   the reinstall-not-symlink dependency rule.
3. **Preview deployments** (§6.1-6.2): the runner from a worktree ref, port-block
   allocation, compose-project namespacing by slot.
4. **The storage driver** (§7a): the interface plus the profile's default
   implementation (managed-branching or an owned ZFS/reflink volume in the cloud;
   lvm-thin / ZFS-on-vdev / plain-copy for self-hosted). Everything below reads its
   advertised capabilities.
5. **Resource governance** (§6.3-6.4): the multi-ceiling budget, primary-hot /
   branch-scale-to-zero tiers, idle-suspend + wake-on-request, TTL reaper, LRU
   eviction on the running axis.
6. **Database policy** (§7): `db_mode` on the worktree, `shared` first; `branched`
   via the storage driver's `create_branch`, with the expand/contract + declarative
   schema + three-way-merge reconciliation workflow.
7. **UI** (§9): the ref pill, then the environments view (with per-deployment
   budget metering).
8. **Import hardening** (§8.3): `reject_blocked` + repo binding on
   `POST /projects/import`.

Merge protocol (§8.1) needs no new build; it already exists at the ceiling we want.
The storage-driver split (§7a) means DB branching can start on the managed-branching
driver in production and the self-hosted driver locally without changing anything
above the interface.

Deployments-proper (public, versioned, staged) branch off this per the operator and
per ADR 0013's exit condition; not in scope here.

---

# Revision, 2026-08-22: measurements, the local driver rungs, and the addressing rule

Added after the operator asked the two questions §1 did not cover: **how do
worktrees stay cheap at tens-to-hundreds**, and **what is a chat looking at**. The
model above is unchanged and still the model of record. This revision corrects one
factual assumption in §5, adds two measured driver rungs to §7a, and fills the
addressing gap. Every number below was measured on this box on 2026-08-22, not
estimated.

## 13. What the tree actually costs (measured)

The design above reasons about worktree cost in the abstract. The real numbers
invert which part of the problem is hard:

- **Tracked source: 22 MB**, 1,437 files. `git worktree add` completes in **0.20 s**.
- **Untracked dependencies: 1,019 MB** — `node_modules` 305 MB, `backend/.venv`
  658 MB, root `.venv` 57 MB.
- Working tree total 6.1 GB, of which `apps/` 2.2 GB and `projects/` 1.2 GB are
  untracked trees that a worktree does not copy at all.

So **source isolation is free and dependency isolation is the entire cost**, by a
factor of 46. The fear that worktrees "duplicate everything" is misplaced for the
repository and exactly right for `node_modules`. Every decision below follows from
that one ratio.

### 13.1 Correction to §5: npm does not hard-link from its cache

§5 rules "reinstall dependencies, do not copy or symlink" and justifies the cost as
near-zero because "npm/pnpm/bun all hard-link from a shared global store." **That is
true of pnpm and bun and false of npm**, which is the package manager this repo
uses (`package-lock.json`, npm 9.2.0; neither pnpm nor bun is installed). npm's
`_cacache` is content-addressed but `npm ci` *extracts* tarballs into each tree. The
rule as written therefore costs ~1 GB and minutes of install per worktree here, and
50 worktrees would want ~50 GB — more than this box has free (§13.3).

The rule is right about *why* (symlinking a dependency tree between worktrees
reintroduces the version coupling worktrees exist to remove). It is wrong about the
mechanism being free by default. Restate it as: **share dependency trees by
copy-on-write, never by symlink, and never by full copy.** Symlink is the thing to
avoid; a CoW clone is not a symlink.

### 13.2 Two driver rungs missing from §7a, both measured working here

§7a lists six workspace/DB drivers and concludes there is no "we are on ext4 so we
cannot." Correct, but the two rungs that actually apply to a plain unprivileged ext4
host are absent from the list, and both work on this box today with no root, no
spare block device, and no new filesystem:

- **`overlayfs-userns`** — `mount -t overlay` inside an unprivileged user namespace,
  with the shared base tree as `lowerdir` and a per-worktree `upperdir`. Verified:
  mounts, reads through to the lower tree, and writes land only in the upper delta.
  Requires kernel ≥ 5.11 (this box is 6.1) and unprivileged userns (enabled,
  `max_user_namespaces` unbounded). True CoW, per-file granularity, zero copy.
  Caveat: the mount lives inside the namespace, so it is visible to the turn and not
  to the backend or an editor. That suits a *turn*, not a durable workspace.
- **`hardlink-clone`** — `cp -al` of the dependency directories into the fresh
  worktree. Not true CoW (an in-place write corrupts the shared copy), but every
  package manager and build tool in this stack replaces files by write-temp-then-
  rename, which breaks the link safely. Verified: `node_modules` clones in **0.10 s**
  for **5 MB** of new blocks instead of 305 MB.

Measured end to end, a fully provisioned worktree — `git worktree add` plus
hardlink-cloned `node_modules`, `backend/.venv` and `.venv` — costs **0.27 seconds
and ~100 MB** of real new blocks, against ~1 GB and minutes for the reinstall rule.
That is the difference between 50 worktrees costing 5 GB and costing 50 GB.

Neither rung replaces the driver interface; they slot into it as workspace-plane
implementations between `reflink` and `lvm-thin`, and they are what the self-hosted
profile selects on this host. `cp --reflink` is confirmed **unsupported** here
(ext4), so the `reflink` rung does not apply.

The operating rule that makes hardlinking safe: **a cloned dependency tree is
read-only.** Any command that installs re-materializes the directory first
(`rm -rf` then a real install, or a fresh clone from an updated base), so a write
never lands in a shared inode. Because dependencies are a pure function of the
lockfile, the base to clone from is cacheable by `hash(package-lock.json)` and
shared by every worktree on the same lockfile.

### 13.3 Capacity is a policy, not a property of one host

The numbers above are measurements of **one runtime profile**, taken to settle a
mechanism question (does hardlink-cloning beat reinstalling: yes, by an order of
magnitude). They are not a design input, and an earlier draft of this section wrongly
treated one machine's free disk as a constraint on the model. It is not. The platform
targets production, any number of users, any number of apps, of any size, on storage
the platform provisions.

What generalizes is the shape, not the figures:

- **Source and dependencies scale differently.** A repository's tracked source is
  small and its untracked build inputs are large, usually by one to two orders of
  magnitude. Which directories are large varies by stack: a Node app's are
  `node_modules`, a Python app's a virtualenv, a media or ML app's are model weights,
  fixtures or asset bundles that may dwarf both. So the provisioner must not hardcode
  a list of directories to clone. It clones **whatever the app declares as its heavy
  untracked inputs**, defaulting to what the detected stack implies, exactly as §5's
  `setup` and `worktree_include` are already declared on the app manifest rather than
  in platform code.
- **Every profile has a finite budget**, so provisioning is admitted against a
  **capacity policy** the profile configures (a storage quota per user or per app,
  the same multi-ceiling admission §6.4 already defines for compute). A profile on
  elastic cloud storage sets a generous ceiling; a small self-hosted box sets a
  tight one. Neither number belongs in the design.
- **The reaper is therefore load-bearing everywhere**, not a nicety for cramped
  hosts: unmerged, idle workspaces are reclaimed on TTL under the same policy.

The rule to carry forward: measure mechanisms, configure capacities, and never let a
number from one machine become a rule in the model.

## 14. The addressing rule: what a chat is looking at

§3.2 gives a worktree an `owner_kind` of `chat | run | agent | manual` and §9.3
shows a chat displaying its branch, but the model never states the **default**: does
every chat get its own tree? Today the answer is accidental and wrong.

**Measured today:** a turn's working directory comes from `paths.scope_root(scope)`
(`project:<slug>` → `projects/<slug>`, `app:<name>` → `apps/<name>`, `agent:<slug>`
→ `.runtime/agents/<slug>`) or `paths.chat_cwd()` for a scope with no tree. That is
**one directory per scope**, so *every chat in a scope shares one working tree* and
two chats in the same scope are the exact collision worktrees exist to prevent.
Meanwhile the binding that would fix it is already built and unused:
`build_agents.create(..., branch_slug=...)` resolves `cwd` through
`worktrees.branch_dir()`, and `branch_slug` is `null` for every agent on record.

### 14.1 The rule: branch on write

A chat should **not** get a worktree by default. Most chats read, ask, plan, or
review; allocating a tree for each would be the layering the operator objects to,
and would make the common case slower and more confusing for no benefit.

> **A scope's canonical tree is the shared read view. A worktree is a write view,
> created at the moment work first needs to write, and owned by whatever asked.**

This is not a new policy. It is **the same rule §7.1 already applies to the
database** ("share until a branch needs to mutate, then give it a CoW branch"),
extended to the filesystem plane. One rule, two planes, which is why this is a
consolidation rather than an addition.

Consequences, in order:

- A chat carries `scope` (what the work is *about*) and an optional `workspace`
  (which materialization it is *looking at*). Unset means the canonical tree.
- The first write in a chat with no workspace allocates one and rebinds the chat.
  Dispatching a ticket does the same for the run. Both then show a ref pill (§9.1).
- Two chats in one scope stop sharing a tree the moment either writes, which is the
  collision the operator predicted and the current model does not prevent.
- `scope_root()` stays the one mapping and gains no branch logic: the workspace
  resolves to a directory, and an unset workspace resolves to `scope_root()`. The
  confinement layer keeps mounting exactly one directory at `/workspace` and does
  not learn a second concept.

### 14.2 What this retires

Stated because the working agreement requires an addition to pay for itself:
`build_agents.branch_slug` + its `cwd` derivation, `project_ralph.sh`'s
`WT_ENABLED`/`CONCURRENT_WT` gate (dead: `concurrent_worktrees` is `None` for all
seven registered projects, so worktree mode has never once run), and the ad-hoc
`cwd` field on the build-agent record all collapse into one workspace lookup. Four
half-built answers to "which tree" become one.

### 14.3 The self-repo case, stated explicitly

The platform repo is its own scope (`development`, ADR 0014) and is where agents
collide most: this session alone measured a concurrent worker resetting the shared
tree and discarding another agent's uncommitted edits. It is also the case §10's
post-commit hook already protects, since that hook refuses to build or publish from
a linked worktree under `branches/`. So the self-repo is both the highest-value
target for branch-on-write and the one where the guardrail already exists. It should
be first, not last.
