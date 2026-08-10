# 0003. GitHub is a per-project repo binding on the operator's personal account

- Status: accepted
- Date: 2026-07-15

## Context

Project code persisted only locally: the registry record had no remote field, project
creation did `git init -b main` plus one commit with no origin, and workers ran in
the project main dir with no push or PR. `gh` is installed and authenticated as
`<owner>` (scopes `read:org, repo`), and `git worktree` works, so the box can
talk to GitHub today, but nothing in the project or worktree flow does.
`services/worktrees.py` already does the full local worktree lifecycle but touches no
remote. The operator wants code to persist in the remote and agents to create repos,
branches, worktrees, push, and open and merge PRs themselves. See
`docs/platform-redesign/github-worktree-orchestration-plan-2026-07-15.md`.

## Decision

Bind each project to its own GitHub repo as a per-project record field
(`repo` / `default_branch`), created or bound via `gh` ("Connect GitHub"), with the
canonical branch tracked rather than re-guessed each call. Agents fetch before work,
push, and open PRs through `gh`. Concurrent work runs in disjoint git worktrees under
the existing `branches/<slug>/<branch>` scheme, reconciled with a
rebase-before-merge model (short-lived `agent/<task-id>` branches, `git rerere`, a
merged-branch cleanup sweep). Repos go to the operator's personal GitHub
(`<owner>`) only.

## Consequences

Hard boundaries are honored: the `<blocked-org>` org is strictly excluded, with no
cross-org PAT scopes and no PRs against it. Project repos stay separate git repos and
are never committed into the workshop tree; the existing `/projects/*`, `/branches/`,
and `/build-agents/` gitignores are extended so no project `.git` or worktree leaks
in. GitHub Issues sync stays out of scope; this binding is code, repo, and PR only.
GitHub Actions and server-side agentic-workflow machinery are rejected; agents run
locally and drive `gh` per command.
