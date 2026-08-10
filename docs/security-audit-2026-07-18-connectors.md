# Audit — Workshop project/app → git / GitHub separation — 2026-07-18

Second-pass companion audit. Question from the operator: when individual apps/projects push to GitHub
or other connectors, is it done in a clean, well-separated way — in particular, does a project ever
commit into the Workshop repo itself, and are the repos/scopes cleanly separate as intended? This is
a correctness + separation review more than a vulnerability hunt.

## Bottom line

**The separation is architecturally clean and defended in depth. No clear issues.** One design item
for the operator: the shared GitHub credential is broader than the code boundary that relies on it.

## Verified clean

**A project can never commit into the Workshop repo.**
- `.gitignore` excludes `projects/`, `branches/`, `build-agents/` (only `projects/_templates/` and the
  `.runtime/*.json` registries are tracked). `git check-ignore` confirms.
- New projects get their **own** repo: `create_project` copies a template (no `.git`), then
  `git init -b main` + an initial commit *in the project dir* (`backend/routes/projects.py`). Every
  project dir has its own `.git`.
- Backstop: `_assert_project_ignored(slug)` runs `git check-ignore projects/<slug>` and raises 500 if
  the boundary ever fails to hold — called after project creation and after connect-github.

**The auto-commit net is scoped to the project tree.** `ralph.sh`'s `maybe_autocommit` only stages
paths that became dirty *during* the run AND fall inside the task's `touches` lane (or, with no lane,
only newly-created untracked files), committing with `git commit --only -- <paths>` so a concurrent
session's index can't ride along. Even root-scope queue runs commit to Workshop correctly, and
because `projects/` is gitignored there, a root run physically cannot stage project content.

**Repos/scopes are isolated.** Each project's remote is stored per-project (`repo.remote_url`); no
project points at another's remote. Worktree publish pushes to *that project's* own `origin`, from a
linked worktree of the project's `.git` (not Workshop's). `task_scope.root_for_context` maps each
scope to its own dir, and `apply_task_patch` refuses to write a scope other than the chat's binding.

**The push/PR mechanism is well-formed.** `services/github.py` hardcodes `OWNER="<owner>"`,
creates repos `--private`, and calls `reject_blocked` at every entry point (create, bind, open_pr,
merge_pr, rebase). `auto_pr.sh` is dry-run by default (behind `MASINI_AUTO_PR=1`), refuses to PR from
a default branch, and uses the repo's own `origin`.

**`git-guard.py` is solid.** In a project/branch/build cwd it blocks force-push, `--mirror`, remote
deletes, pushes to `main`/`master`, `git remote remove/set-url/add origin` (can't repoint origin),
`filter-branch`, `update-ref -d`, and reset/rebase onto remote history; local destructive ops are
auto-checkpointed then allowed. It correctly prevents repointing a project at the wrong remote or
rewriting a shared branch.

**No the excluded work org mixing.** The only `the excluded work org` references outside `github.py`'s guard are a prompt
instruction reinforcing the boundary and a personal-finance label ("the excluded work org payroll") — neither
mixes the work org into the git/connector code.

## Design item for the operator

**Over-scoped shared GitHub credential (medium, tracked as SEC-G1).** The global git credential
helper uses a single **classic PAT** for `<owner>` with scopes **`repo, read:org`**, used for
every push. The platform code prevents any the excluded work org access, but that boundary is string-matching in
code, not a property of the token: a `repo`+`read:org` classic PAT is technically capable of reading
the `<blocked-org>` org and pushing to its private repos if the account is a member — which brushes
the operator hard-rule "no PAT scopes for `read:org <blocked-org>`." A **fine-grained PAT scoped to
personal repos only, dropping `read:org`**, would move the boundary into the credential itself.
Tradeoff: the same personal account is used for real work. Operator's call. (<blocked-org> membership
was deliberately NOT probed, per the separation rule.)

## Note

`git-guard.py` gates push/remote/history/destructive verbs but not plain `git commit`/`git add`. Not
a practical gap: the automated loops never commit against Workshop from a project, and the `projects/`
gitignore means project content can't be staged into Workshop even if attempted.
