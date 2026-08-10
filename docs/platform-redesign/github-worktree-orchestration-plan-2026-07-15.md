# GitHub + worktree orchestration: code persists off-box, agents work in parallel (2026-07-15)

Status: reviewed against shipped code (every file:line below was read by audit, not assumed).
Scope: ASK G. Bind projects to GitHub repos; let agents create repos, branches, worktrees,
push, and open/merge PRs themselves; run concurrent agents in disjoint worktrees with a
philosophy and orchestration that keeps them synchronized to one canonical branch and does
not drift; and make code persist in the remote, not only locally. Companion to
`chat-tickets-orchestrator-plan-2026-07-15.md`; slots in AFTER that plan's Phases 0-4.

## Executive summary

1. The capability gap is entirely in platform code, not the environment. `gh` is installed and authenticated as `<owner>` (scopes `read:org, repo`, https), `git worktree` works. The box can talk to GitHub today; nothing in the project/worktree flow does.
2. We already have half of it, locally. `services/worktrees.py` does the full local worktree lifecycle (create/list/diff/merge-and-reap/discard) with a stable naming scheme (`branches/<slug>/<branch>`, ref `wt/<slug>/<branch>`). It touches no remote, opens no PR, and re-guesses the canonical branch every call instead of tracking it.
3. Projects have no notion of a remote. The registry record has no repo/remote/default-branch field; `git init -b main` + one commit is all `create_project` does; exactly one project (gengui) incidentally carries an `origin` because it was cloned in via `/import`.
4. There is a real but narrow PR path: `scripts/auto_pr.sh` calls `gh pr create` in a non-default "real" mode, but only against a repo you hand it that already has an `origin`, and it never pushes the branch first. It is the bridge agent's self-fix tool, not wired into the worktree flow.
5. Workers do not use worktrees. `project_ralph.sh` sweeps run in the project MAIN dir on whatever branch is checked out; the only branch-to-agent binding is a static pin at build-agent creation (`build_agents.py:113-122`). There is no dynamic worktree-per-task allocation.
6. Minimal buildable first slice (early value): a `repo` binding on the project record + "Connect GitHub" (create-or-bind via `gh`) + canonical-branch tracking (store `default_branch`, stop guessing) + fetch-before-work + push-and-open-PR (close the auto_pr no-push gap). That alone makes code persist in the remote and lets an agent open a PR.
7. Fuller orchestration (later): dynamic worktree-per-agent for concurrent work, a rebase-before-merge reconcile model with `git rerere` and a merged-branch cleanup sweep, and a canonical-state view (a `Repo` panel + a machine-readable state block + a branch-philosophy skill) so agents and the operator both always see which branch is main, when to branch, and when to merge.
8. Hard boundaries honored: project repos are separate git repos, never committed into workshop (extend the existing `/projects/*`, `/branches/`, `/build-agents/` ignores). Repos go to the operator's personal GitHub (`<owner>`) only; never the `<blocked-org>` org, never cross-org PAT scopes. GitHub Issues sync stays out; this is code/repo/PR only.
9. Phases G0-G3 slot after ticket Phases 0-4. G touches `project_ralph.sh` and the receipt path that ticket Phases 1 and 3 also own; overlaps are flagged so the builder sequences correctly.

---

## Part 1: audit of current state (what exists vs missing, file:line)

### Environment (ready)

`git version 2.39.5`, `git worktree` present. `gh` at `/usr/bin/gh` v2.23.0, authenticated as `<owner>`, token scopes `read:org, repo`, git ops over https. GitHub auth is fully available on the box.

### Local worktree lifecycle (exists, local-only)

`backend/services/worktrees.py`: `BRANCHES_ROOT` = `~/workshop/branches` (`:31-34`); worktree path `branches/<slug>/<branch-slug>` (`_branch_path :91-92`), git ref `wt/<slug>/<branch-slug>` (`_branch_name :87-88`), kebab slugs capped 48 (`:41,63-67`). Operations: `create_branch` = `git worktree add -b wt/... <path>` off current HEAD (`:127-155`); `list_branches` parses `git worktree list --porcelain`, filters to `wt/<slug>/` (`:158-190`); `diff_branch` = `git diff <main>...<branch>` shortstat + name-status + unified (`:207-239`); `merge_branch` checks out main, `git merge --ff-only` then `--no-ff` fallback, and on success REAPS the worktree + deletes the branch (`:242-300`); `discard_branch` removes worktree + branch (`:303-320`); `branch_dir` returns the path (`:323-331`, used to pin a build-agent cwd). `_main_branch` prefers `main`, then `master`, then HEAD, recomputed every call (`:108-124`). Routes at `/api/worktrees` (list/create/diff/merge/discard). Committer forced to `Workshop Projects <projects@workshop.local>` (`:48-60`).

Missing here: no `git fetch/push/pull`, no `origin`, no `gh`, no PR, no stored canonical branch.

### Project "Branches" UI (exists, local-only)

`frontend/src/pages/ProjectDetail.tsx:1029-1093`: a static "Working on main" trunk row, the side-branch list from `GET /api/worktrees/{slug}`, per-branch Merge (message modal -> `POST .../merge`) and Discard (`DELETE`), a Refresh. The per-branch Diff button was removed (`:1075-1078`, pointed at a 404 route). No push/PR/remote UI anywhere.

### Project creation (exists, no remote)

`backend/routes/projects.py`: `_git_init` = `git init -q -b main` + `git add .` + initial commit, neutral committer, no remote (`:254-270`). `create_project` scaffolds from `projects/_templates/<template>`, `_git_init`, and registers a record with `dir/state/surfaces/...` but NO `repo`/`remote`/`origin`/`default_branch` field (`:273-309`). Project dir = `~/workshop/projects/<slug>`. Build-agent cwd resolution (`build_agents.py:113-163`): branch pin -> `worktrees.branch_dir`; else project main dir; else a scratch dir with its own `git init`. `POST /import` clones an external repo in via `git clone` (the one path a project arrives with a remote) but never pushes back (`projects.py:648+`).

### The existing PR path (exists, narrow, no push)

`backend/routes/auto_pr.py` + `scripts/auto_pr.sh`: gated by `MASINI_AUTO_PR` in a gitignored secrets file (`auto_pr.py:11,16-25`). Dry-run default writes a PR-body `.md` to `.runtime/auto-prs/`. Real mode (`MASINI_AUTO_PR=1`) runs `auto_pr.sh`, which refuses on the default branch or detached HEAD (`:105-111`) then calls `gh pr create --repo $(git remote get-url origin) --title ... --body ...` (`:113-115`). Two gaps for our purpose: it never runs `git push` first (relies on an already-pushed branch), and it is a the bridge agent self-fix tool, not wired into the worktree/merge flow.

### Workers and branches (no worktree use)

`scripts/project_ralph.sh` resolves `DIR` + autonomy mode from `.runtime/projects.json` (`:167-187`) or takes `--dir`; records `BASELINE=git rev-parse HEAD` before a task and `COMMITS=git rev-list BASELINE..HEAD` after (`:326,351-352`); locks on the resolved DIR (`:219-231`). `ralph.sh` `cd`s to `$DIR` and operates on whatever branch is checked out (`:80,151`). Neither checks out a branch or creates a worktree. The only worktree-to-agent binding is the static build-agent pin.

### Registries and remotes (facts to reconcile)

`.runtime/projects.json` lists 9 projects; none has a repo/remote field. Actual on-disk remotes: `projects/gengui` -> `origin https://github.com/<owner>/gengui.git`; the workshop root repo -> `origin https://github.com/<owner>/Workshop.git`. The instances model uses the Workshop repo's own `origin` for the fork/upstream flow (`docs/instances-and-forking.md`), which is repo-level platform maintenance, entirely separate from project git. Marketplace "update from upstream" uses local git BUNDLES, not GitHub (`services/registry.py:348-352,515-530`).

Discrepancy to fix first: `worktrees.py` resolves projects via `~/workshop/projects.json` (`PROJECTS_ROOT.parent / "projects.json"`, `:39`) while `routes/projects.py` and `project_ralph.sh` use `.runtime/projects.json`. These must point at the same registry before worktree ops can be trusted across all projects.

### Gitignore boundary (honor and extend)

`.gitignore`: `/projects/*` (`:63`) with `!/projects/_templates/` (`:64`), `/build-agents/` (`:65`), `/branches/` (`:66`), plus `.runtime/`. Project working trees, build-agent scratch, and worktrees are each their own git repo and are NOT committed into workshop. Extend this boundary as remotes are added; never let a project repo's `.git` or worktree leak into the workshop tree.

---

## Part 2: research findings and what we adopt/reject

### Worktree isolation for parallel agents (adopt)

Give each agent its own worktree: an isolated working directory and private git index over a shared object store, so agents never contend on `.git/index.lock` and never contaminate each other's context; the goal is hard infra-level isolation so agents do not have to coordinate at the file level at all (MindStudio, https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents; Augment Code, https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution; Nx, https://nx.dev/blog/git-worktrees-ai-agents). Standard naming `<repo>-<branch>` or a `.trees/<task-id>` layout; ours (`branches/<slug>/<branch>`, ref `wt/<slug>/<branch>`) already fits. Adopt: worktree-per-concurrent-agent. Reject: shared working dir with agents taking turns (defeats the point and reintroduces lock contention).

### Canonical branch + reconcile model (adopt the discipline)

Keep `main` canonical; branch off `origin/<default>` after a `git fetch`, work on short-lived `agent/<task-id>` branches, rebase on latest main before merge so conflicts surface as ordinary git conflicts at merge time, and for multi-agent waves use a dedicated integration branch: merge feature branches there first, test, resolve, then fast-forward to main to keep main stable throughout (Augment Code, same URL; Nx, same URL). Enable `git rerere` so recurring conflict resolutions replay automatically. A cleanup sweep removes worktrees whose branch is an ancestor of `origin/main` (`git merge-base --is-ancestor`, `git worktree remove --force`, `git branch -D`, `git worktree prune`). Adopt all of this. Reject the heavier framings (FIFO merge queues, four-tier conflict resolvers, Coordinator/Verifier fleets from the same sources): multi-tenant orchestration cruft for a single operator; our thin dispatcher plus rebase-before-merge is enough.

### Trunk-based, short-lived branches for agents (adopt)

Long-lived branches are worse with agents: the agent writes against branch state, main moves, and half its assumptions are stale by merge time; the fix is branches that live hours not days, merged early and often, small and self-contained enough for a reviewer to integrate fast (Atlassian, https://www.atlassian.com/continuous-delivery/continuous-integration/trunk-based-development; trunkbaseddevelopment.com, https://trunkbaseddevelopment.com/short-lived-feature-branches/; Mergify, https://mergify.com/learn/trunk-based-development). This is exactly the shape our reviewer gate + one-task-per-branch already wants. Adopt as the stated philosophy agents read.

### gh automation (adopt, it is one command each)

Non-interactive repo create from an existing local repo: `gh repo create <name> --private --source=<dir> --remote=origin --push` creates the remote, wires `origin`, and pushes in one call (cli.github.com, https://cli.github.com/manual/gh_repo_create; GitHub blog "Scripting with GitHub CLI", https://github.blog/engineering/engineering-principles/scripting-with-github-cli/). `gh` emits machine-readable output when piped, and PR create/merge (`gh pr create`, `gh pr merge --squash/--auto`) are scriptable. Adopt `gh` for create/push/PR/merge. Reject GitHub Actions / `gh aw` agentic-workflow machinery (server-side, unnecessary; our agents run locally).

---

## Part 3: the design

### Data model: a `repo` binding on the project record

Add to the project registry record (`.runtime/projects.json`, and FIRST reconcile the `worktrees.py` `~/workshop/projects.json` vs `.runtime/projects.json` discrepancy so there is one registry):

```
"repo": {
  "provider": "github",
  "owner": "<owner>",          // personal only, never <blocked-org>
  "name": "<repo-name>",
  "remote_url": "https://github.com/<owner>/<repo-name>.git",
  "default_branch": "main",         // canonical, TRACKED not guessed
  "connected_at": "<iso>"
}
```

Absent field = a purely local project (today's behavior, still valid). This is additive; nothing breaks for unbound projects.

### Canonical-branch tracking (stop guessing)

`worktrees._main_branch` and the merge/diff paths read `repo.default_branch` when present, falling back to the current guess only for unbound projects. Store it once at connect time (`gh repo view --json defaultBranchRef` or the `-b main` we already init with). Every agent and every UI reads the same field: this is the single source of "which branch is canonical."

### Minimal first slice (Phase G1): bind, push, PR

- Connect GitHub: a project action (button + `POST /api/projects/{slug}/connect-github`) that either creates a new private repo from the project dir (`gh repo create <owner>/<slug> --private --source=<dir> --remote=origin --push`) or binds an existing one (`git remote add origin <url>` + `git fetch`), then writes the `repo` field. Default visibility private; owner always `<owner>`. Guardrail in the connect path: reject any owner/remote containing `<blocked-org>` (the excluded work org is out of scope, never a host).
- Provision on create (opt-in, default on for real projects): `create_project` gains a `create_repo: bool` (default true for non-scratch templates, false for blank/scratch) that runs the same connect flow after `_git_init`. So "create a project -> it provisions the repo" is one flag, and scratch experiments stay local.
- Fetch-before-work: `project_ralph.sh` (and worktree create) run `git fetch origin <default_branch>` when the project is bound, and branch worktrees are created off `origin/<default_branch>` so agents never start from a stale base.
- Push + open PR: a worktree op `POST /api/worktrees/{slug}/{branch}/publish` that runs `git push -u origin wt/<slug>/<branch>` then `gh pr create --repo <remote> --base <default_branch> --head wt/<slug>/<branch> --title ... --body ...`. This closes the auto_pr no-push gap (auto_pr never pushed). Reuse `auto_pr.sh`'s `gh pr create` invocation shape; the new op adds the missing push.
- Merge: extend `merge_branch` so a bound project merges via the remote (`gh pr merge --squash --delete-branch` after the reviewer gate passes) rather than only a local `git merge`; unbound projects keep the local merge. The reviewer gate is the merge gate (single operator; no separate approval ceremony).

This slice alone: code persists in the GitHub remote, agents open and merge PRs, and there is a tracked canonical branch. It reuses the existing worktree lifecycle and the existing `gh pr create` shape; the net-new is push, connect, and the `repo` field.

### Fuller orchestration (Phase G3): concurrent worktrees + reconcile + visibility

- Dynamic worktree-per-agent: today sweeps run in the project main dir. For concurrent agents, allocate a worktree per concurrent claim off `origin/<default_branch>` (`git worktree add -b wt/<slug>/<task-id> <path> origin/<default_branch>`), run the worker in it (`ralph.sh --dir <worktree>`), and lock the worktree during execution (`git worktree lock`) so a crashed agent's stale index does not block others. The existing per-DIR flock already serializes same-tree work; per-worktree DIRs give free parallelism across tasks. Assignment is stigmergic and thin (ants, not a conductor, per `unified-agent-model`): the dispatcher hands each ready task its own worktree; agents do not negotiate.
- Reconcile model: rebase-before-merge (`git fetch` + `git rebase origin/<default_branch>` in the worktree before publish), `git config rerere.enabled true` at connect time so recurring conflict resolutions replay, and for multi-task waves an optional integration branch (`wt/<slug>/integration`) that features merge into first, verify, then fast-forward to main. Conflicts surface as ordinary git conflicts at merge; a conflicting merge parks the task (dead-letter status, per the self-healing model) rather than looping.
- Cleanup sweep: a periodic op removing worktrees whose branch is an ancestor of `origin/<default_branch>` (merged) and pruning orphaned branches, so `branches/<slug>/` does not accumulate stale trees.
- Canonical-state visibility (both operator and agents see it): a `Repo` panel on ProjectDetail showing default branch, remote URL, active worktrees with their assigned task and PR link, ahead/behind vs `origin/<default_branch>`, and open PRs (`gh pr list --json`). For agents, a machine-readable state block (a scoped note or an injected digest line) plus the branch-philosophy skill below, so a worker always knows which branch is canonical, whether another worktree is in flight, and when to rebase/merge, without guessing.
- The branch philosophy as a consumed artifact: a `shared/skills/git-worktree-workflow/SKILL.md` (trunk-based, short-lived `wt/<slug>/<task-id>` branches off `origin/<default_branch>`, rebase before publish, small self-contained changes, open a PR, merge on green, never touch the workshop repo's own remote, never push to <blocked-org>). Builders auto-discover it; a persona line in `chat_context.py` points at it. This is how agents "always know which worktree is canonical and when to merge" without a heavy conductor.

### Boundaries (hard)

- Project repos are separate git repos with their own remotes; never committed into workshop. The `/projects/*`, `/branches/`, `/build-agents/` ignores already enforce this; the connect flow must operate inside the project dir only and never stage a project `.git` into workshop. Verify the boundary holds after connect (`git -C <workshop> check-ignore projects/<slug>`).
- Personal GitHub only. Owner is always `<owner>`. The connect and create paths reject any `<blocked-org>` owner/remote. Do not expand `gh` PAT scopes for org access; personal repo create/push needs only `repo`, which is already present. the excluded work org is never proposed as a host and never mixed in.
- GitHub Issues sync stays out. This design touches repos, branches, worktrees, and PRs only. No `external_ref` on tasks, no issue mirroring.

---

## Part 4: sequenced phases (after ticket Phases 0-4)

Global constraints as in the ticket plan (no em-dashes, no emoji, `frontend/CLAUDE.md`, restart via script, stage only own files, autonomy paused for verification). Editing `project_ralph.sh` requires `touch .runtime/AUTONOMY_DISABLED` then `rm` per the coherent-rebuild op notes.

### Phase G0: registry reconcile + repo field [PARALLEL-OK, backend only]

- Fix the registry discrepancy: `worktrees.py` must read the same registry as `routes/projects.py` and `project_ralph.sh` (`.runtime/projects.json`). One change in `worktrees.py:39`; verify all worktree ops resolve every project.
- Add the additive `repo` field to the project record schema (writer + reader in `routes/projects.py`; default absent). No behavior change yet.
- Seam files: `backend/services/worktrees.py` (registry path), `backend/routes/projects.py` (record schema). No frontend.
- Verification: worktree list/create works for a project not under `~/workshop/projects.json`'s old assumptions; a project record round-trips a `repo` field; existing unbound projects unaffected.

### Phase G1: bind + push + PR (the minimal slice) [SEQUENTIAL after G0]

- `POST /api/projects/{slug}/connect-github` (create-or-bind via `gh`, writes `repo`, rejects <blocked-org>). `create_project` gains `create_repo` (default on for non-scratch).
- Canonical tracking: `worktrees._main_branch` and diff/merge read `repo.default_branch` when present.
- `POST /api/worktrees/{slug}/{branch}/publish`: `git push -u origin` + `gh pr create`. Reuse `auto_pr.sh`'s `gh pr create` shape; add the push.
- `merge_branch`: bound projects merge via `gh pr merge --squash --delete-branch` after the reviewer gate; unbound keep local merge.
- Frontend: a `Repo` / Connect control on ProjectDetail (button + status) and a Publish/PR action per branch in the Branches section (`ProjectDetail.tsx:1029-1093`). OVERLAPS ticket Phase 2 (ProjectDetail.tsx task section) and Notes Phase N2 (Notes section); sequence after both or coordinate the file owner.
- Seam files: `backend/routes/projects.py`, `backend/services/worktrees.py`, `backend/routes/worktrees.py`, `scripts/auto_pr.sh` (reuse), `frontend/src/pages/ProjectDetail.tsx`.
- Verification (uses the authenticated `gh`): connect a scratch project, confirm the private repo exists (`gh repo view`), the initial commit is pushed, a `wt/<slug>/x` branch publishes and opens a PR (`gh pr list`), merge closes the PR and updates `origin/main`; the workshop tree never stages the project `.git` (`check-ignore`); a connect attempt with a <blocked-org> owner is rejected.

### Phase G2: fetch-before-work + remote-aware sweeps [SEQUENTIAL after G1]

- `project_ralph.sh`: when the project is bound, `git fetch origin <default_branch>` before the sweep and record baseline against `origin/<default_branch>`; the receipt's `commits` (ticket Phase 1 / D3) gains the pushed branch and PR ref. OVERLAPS ticket Phase 1 (receipt in project_ralph.sh) and Phase 3 (`--task-id`); sequence AFTER ticket Phase 4 and coordinate `project_ralph.sh` edits.
- Verification: a bound project's sweep fetches, works, pushes, and the child Job receipt carries the branch + PR link; a failing run still records a receipt (per ticket Phase 1).

### Phase G3: concurrent worktrees + reconcile + visibility [SEQUENTIAL after G2]

- Dynamic worktree-per-task allocation in the dispatch path: create/lock a worktree per concurrent claim off `origin/<default_branch>`, run `ralph.sh --dir <worktree>`, unlock and hand to publish on success. Reuse the per-DIR flock for same-tree serialization.
- Reconcile: rebase-before-publish, `git config rerere.enabled true` at connect, optional integration branch for waves, park (not loop) on unresolved conflict.
- Cleanup sweep op for merged/orphaned worktrees and branches.
- Visibility: a `Repo` panel (default branch, remote, active worktrees + assigned task + PR, ahead/behind, open PRs via `gh pr list --json`) and `shared/skills/git-worktree-workflow/SKILL.md` + a persona line in `chat_context.py`. OVERLAPS ticket Phase 4 persona edits; sequence after.
- Seam files: `scripts/project_ralph.sh`, `scripts/dispatch_triggers.sh`, `backend/services/worktrees.py`, `backend/routes/worktrees.py`, `frontend/src/pages/ProjectDetail.tsx`, `shared/skills/git-worktree-workflow/SKILL.md`, `backend/services/chat_context.py`.
- Verification: two tasks in one bound project run concurrently in separate worktrees, each opens its own PR, both merge cleanly to main; an induced conflict parks the second task with the conflict recorded rather than looping; the cleanup sweep removes merged worktrees; the Repo panel shows the live state; a worker's injected context names the canonical branch and any sibling worktree.

### Parallelization and overlap summary

- G0 is backend-only and safe alongside other work.
- G1-G3 are sequential (each builds on the prior remote/worktree wiring).
- Cross-plan overlaps to sequence AFTER: `project_ralph.sh` (ticket Phases 1 and 3, so G2/G3 after ticket Phase 4), `ProjectDetail.tsx` (ticket Phase 2 + Notes N2, so G1's Repo panel after both), `chat_context.py` (ticket Phase 4 + Notes N3, so G3's persona line after them). The receipt `commits` field (ticket D3) is the natural carrier for the pushed branch/PR ref; coordinate that field's shape once.

---

## Part 5: explicitly not now / needs operator decision

- GitHub Issues sync: out of scope by operator direction. No task-to-issue mirroring, no `external_ref`.
- The workshop repo's own instance/upstream flow (`instances-and-forking.md`): untouched. This plan is project repos only; do not entangle project remotes with the Workshop-repo cherry-pick flow.
- Operator decision (recommended default taken, proceed): repo visibility default private; provision-on-create default on for non-scratch templates and off for blank/scratch; merge policy = agents merge on a green reviewer gate (single operator, trunk-based), with a per-project "require manual merge" flag available if the operator wants to review PRs himself before merge. Flip any of these if the operator prefers.
- Do not expand `gh` PAT scopes. Personal `repo` scope already present suffices; no org scopes, never <blocked-org>.
- Heavier reconcile machinery (FIFO merge queues, multi-tier conflict resolvers, Coordinator/Verifier fleets): rejected as multi-tenant cruft. Rebase-before-merge + rerere + park-on-conflict is the single-operator ceiling.
- GitHub Actions / `gh aw` server-side agentic workflows: not now; agents run locally via `claude -p`.
- Migrating existing local projects (birds, budget, commute, ...) to remotes: a per-project operator choice via Connect GitHub, not a bulk migration in this plan.
- No allowlist/audit/rate-limit/confirmation-gate on push/PR/merge: single operator; the reviewer gate and flocks are the correctness bounds.
