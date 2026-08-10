# 05 — Deployment, Isolation, and Safety

Status: **draft for operator review** · Date: 2026-06-14

Companion to [00-overview.md](00-overview.md). This is the "complex technical design"
the operator flagged: how apps run (deployment), how projects/apps/people stay
separated (isolation), and how a person can do as much as possible inside a project
while being caught when they break something locally (safety), without ever
destroying a remote. Designed local-now, remote-later: one contract, two runners.

Guiding principle (operator): **maximize freedom inside the project's blast radius;
make that radius recoverable and contained; hard-protect everything outside it
(other projects, the platform, the remote).**

---

## 1. Five isolation layers

| Layer | What it separates | Now (local dev) | Later (remote) |
|---|---|---|---|
| **Source/branch** | one builder's edits from another's | git worktrees (built) | same |
| **Filesystem** | one project from another | per-project dir + repo; default-deny cross-project | per-tenant volume |
| **Runtime** | a running app from the host and other apps | scoped subprocess (own cwd, scoped env, limits) | OCI container |
| **Network/egress** | a project from the internet | default-deny + per-project approved host list | same, per container |
| **Capability/data** | an app/agent's reach over data | `grants.json` (app-platform-plan.md) | same |

The blast radius of any builder = its **worktree** + its app's **granted
capabilities** + its **approved egress hosts**. Inside that, do anything. Outside it,
default-deny.

## 2. The run model: worktrees for build, runners for deploy

Two phases, one manifest.

### Build-time isolation (worktrees, already built)
Each builder works in `branches/<project>/<branch>/`, a git worktree off `main`.
Edits are isolated per branch; merge is a controlled platform action (§4). This is
also the recovery substrate (§5).

### Run-time isolation (the Runner interface)
A **Deployment** runs an app via a `Runner`. Define one interface with two
implementations so remote is additive, not a rewrite:

- **`LocalRunner` (now)**: launches the app as a managed subprocess with its own
  working dir (a worktree or a built artifact), a **scoped environment** (only the
  secrets/capabilities the app is granted, never the platform's full env), resource
  limits where the OS allows, and a dedicated port. Stop/restart/logs are platform
  actions.
- **`RemoteRunner` (later)**: builds an OCI image from the project and runs it as a
  container on a host. Same lifecycle verbs.

### The manifest (the contract both runners read)
Per app/project: `entrypoint`, `build` command, `ports`, `env`/capabilities needed,
`egress` hosts requested, `kit` version, `sensitivity`. The LocalRunner and the
future RemoteRunner both consume this, so nothing in the app changes when it moves to
remote. This is the same manifest as app-platform-plan.md §3.1, extended with
runtime fields.

## 3. Egress: request-and-approve, not silent block

Default-deny external network stays. On top of it, a **per-project approved host
list**:

- A project declares the hosts it needs in its manifest (e.g. `github.com`,
  `registry.npmjs.org`, `pypi.org`). The operator approves per project with one
  action. Approved hosts are allowed for that project's builders and runtime;
  everything else stays blocked.
- For the operator's own projects, a standard dev allowlist (github/npm/pypi) can be
  the default so it never nags. Shared/marketplace apps get reviewed explicitly.
- The builder's role preamble (doc 01 §4) lists currently-allowed hosts, so an agent
  never promises a blocked fetch; a blocked attempt shows "host X is not approved for
  this project; approve it in Settings or run it yourself," not raw stderr.

This satisfies "review and allow what people want/need" while keeping the core block.
It is a per-project capability the operator grants, not multi-tenant cruft.

## 4. Remote protection (never destroy the git remote)

Builders operate on **local worktrees only**. They never hold remote write
credentials and never run raw `git push`/`git remote` against a protected remote.
Publishing is a **platform-mediated action**:

- **No force-push, no history rewrite, no branch/remote deletion** to protected refs
  (main, published release tags) from inside a project or agent. The platform refuses
  these on the remote path.
- **Push goes through merge**: changes reach a shared/published remote only via the
  controlled merge action, which the platform performs after the operator's go-ahead,
  not via an agent shell command.
- Personal remotes get the same default protection; the operator can loosen it
  per-project. A runaway agent cannot `git push --force origin main` or
  `git remote remove origin`, because those code paths are not exposed to it; the
  agent's git world ends at its worktree.

Implementation seam: all remote operations sit behind a backend git service
(worktrees.py is the start). Agents get local git; the platform owns remote git.

## 5. History and undo (catch local mistakes)

The substrate is built now even if the full undo UI comes later, so the data always
exists to recover from.

- **Frequent auto-checkpoints.** Builders auto-commit working checkpoints on their
  branch, so the branch is a recoverable timeline even amid uncommitted chaos.
- **Snapshot before risky ops.** Before a merge, a destructive command, or a deploy,
  take a lightweight anchor (tag/stash + reflog). Cheap and automatic.
- **Undo = restore to a checkpoint**, never a destroy: restoring checks out a prior
  checkpoint into a **new** branch, leaving the current state intact. Reflog + tags
  make this recoverable even after a hard reset.
- **UI later, data now.** A per-project/branch checkpoint timeline with "restore" is
  the eventual UI; v1 guarantees the checkpoints, tags, and reflog anchors exist so
  nothing is unrecoverable in the meantime.

## 6. Guardrails inside the blast radius

Maximize freedom, keep it reversible and contained:

- Destructive shell ops (`rm -rf`, `git reset --hard`, `git clean -fdx`, force-push)
  are **allowed inside the worktree** but auto-anchored first (so reversible), and
  **blocked when they would escape** it (touch another project, the platform root, or
  a remote). The worktree boundary is the firewall.
- The existing the bridge agent egress-guard (curl/wget to external) stays as defence-in-depth
  on the shell channel; the per-project approved-host list (§3) is the policy on top.

## 7. Anti-bloat (copies must not accumulate forever)

- **Auto-reap merged work**: merging a branch removes its worktree (already in the
  merge path); abandoned/idle branches are flagged and offered for reaping.
- **Checkpoint retention/GC**: keep recent auto-checkpoints at full granularity, thin
  older ones (daily then weekly), always keep tagged snapshots. A periodic GC pass
  squashes/prunes.
- **Artifact cleanup**: built deployment artifacts and stopped runtimes are reaped on
  a schedule.

## 8. Phasing

- **05a (with Phases 2 and 4)**: checkpoint substrate (auto-commit + reflog +
  snapshot-before-risky), worktree blast-radius guardrails, remote-protection
  wrapper, per-project egress approve-list. These are the safety floor and are cheap.
- **05b (with Phase 5+)**: `LocalRunner` + manifest runtime fields; deployment
  lifecycle UI; anti-bloat GC.
- **05c (remote)**: `RemoteRunner` containers + hosted host; per-tenant volumes.
  Designed now, built when remote is on the table.

## 9. Open questions

1. Resource limits on the LocalRunner: rely on the OS (cgroups/ulimit) or keep it
   advisory at first? (Leaning advisory locally, real limits in containers.)
2. Checkpoint cadence: every agent turn, every N edits, or time-based? (Leaning
   per-turn for builders, since turns are the natural unit.)
3. Where the approved-egress enforcement actually sits: the existing PreToolUse hook
   (extend it to read the per-project list) vs a network namespace per runtime.
   (Leaning extend-the-hook now, namespaces with containers later.)
4. Default dev allowlist contents for the operator's own projects (github, npm, pypi,
   and what else: deno, crates.io, huggingface for the bird models?).
