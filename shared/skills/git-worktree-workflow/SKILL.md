---
name: Git Worktree Workflow
description: "How agents branch, work, and merge in a project bound to a GitHub remote: trunk-based short-lived worktree branches off the canonical default branch, rebase before publish, small self-contained changes, open a PR and merge on a green reviewer gate. Read before creating a branch, publishing, or opening a PR in any project repo."
---

# Git worktree workflow: one canonical branch, short-lived branches, merge often

This is how work flows in a Workshop project that is bound to a GitHub remote. The
goal is that many agents can work in parallel without drifting, and the code always
converges on ONE canonical branch. Follow it whenever you branch, publish, or merge.

## The canonical branch

Every bound project tracks its canonical branch in `repo.default_branch` (usually
`main`). That field is the single source of truth for "which branch is trunk." Do
not guess it, do not infer it from whatever is checked out. Read it, branch off it,
merge back into it. The Repository panel on the project shows it; agents get it in
their injected repo-state.

## Branch off origin, work small, live short

- Work on a worktree branch named `wt/<project-slug>/<task-id>`, created off
  `origin/<default_branch>` after a fetch, so you never start from a stale base.
  Each task gets its own worktree (an isolated working dir over the shared object
  store); you never share a working tree with another agent.
- Keep branches SHORT-LIVED (hours, not days) and changes SMALL and self-contained.
  A long-lived branch rots: trunk moves and your assumptions go stale by merge time.
  Merge early and often.
- One task per branch. Do the one thing the task describes, then publish it. Do not
  pile unrelated changes onto a branch.

## Rebase before you publish

Before opening or updating a PR, bring your branch up to date:

1. `git fetch origin <default_branch>`
2. `git rebase origin/<default_branch>` in your worktree

Rebasing makes conflicts surface as ordinary git conflicts NOW, on your branch,
instead of at merge time on trunk. If a rebase conflicts and you cannot cleanly
resolve it, stop and park the task with the conflict recorded (do not loop or
force through it). Recurring resolutions replay automatically via `git rerere`.

## Open a PR, merge on green

- Publish pushes your branch to `origin` and opens a PR against `<default_branch>`.
  Use the platform's publish action; do not hand-roll the push/PR ceremony.
- The reviewer gate is the merge gate. When the reviewer approves (a green gate),
  the PR merges (squash) and the branch is deleted. A single operator means no
  separate approval ceremony beyond that gate.
- After merges, merged worktrees are cleaned up (their branch is an ancestor of
  `origin/<default_branch>`); do not accumulate stale trees.

## Hard boundaries

- These rules are for PROJECT repos only. NEVER push to, repoint, or reference the
  Workshop platform repo's own remote from project work. Publishing/deploying the
  Workshop itself goes through the platform, not your shell.
- Personal GitHub only. Repos live under the operator's personal account
  (`BenMiriello`). NEVER push to, fork, mirror, or open a PR against the
  `veritusagent` organization or any repo under it. Veritus is out of scope and is
  never a host for anything here. If a task seems to want it, stop and surface it.
