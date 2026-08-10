# Proposal (WIP): App versioning + rollback

Status: **PROPOSAL / work-in-progress. Not implemented. No code changes implied by this doc.**
Date: 2026-06-17 · Author: the service user · For: operator review

This captures a design for letting you work on an app through the builder, then
roll back or browse versions if something goes weird, without it turning into a
heavyweight, constant-approval, branch-juggling production. It is a thinking
document to react to, not a spec to build yet.

## The goal, in the operator's words

- Background git versioning so you can always go back if things go weird. Mostly invisible.
- Stay simple. NOT a big production of approving/testing every change, NOT getting
  stuck on a sub-branch when all you want is to move forward.
- Be able to undo, go back, see versions, and optionally accept changes, but approval
  must never be mandatory.
- Modes: "stay on main and just update there" vs "work on a feature branch." The
  builder can propose a branch for a big change, but not always and not for minor things.
- Surfaces: an interface in the chat and the project page, plus a small indicator in
  the app settings / app index.

## What already exists (the foundation)

We already have most of the substrate, so this is mostly a thin read/restore layer, not a new engine.

- **Per-turn auto-checkpoint.** After each builder turn on an app project, the working
  tree is committed (`services/checkpoints.py`, called from `routes/chat.py` and
  `services/build_streaming.py`). That is already "a version per change."
- **The chat already knows the version.** `build_streaming` emits a `checkpoint` event
  with the commit sha per turn, so the chat timeline can already mark "saved as <sha>".
- **The self/platform project is excluded** from auto-checkpoint (we never auto-commit
  the live monorepo's working tree). Versioning here would target **app projects**, not
  the Workshop self-project.
- **Apps index + project page** already exist as surfaces (`Apps`, `ProjectDetail` with
  the App and Environment panels) to hang a small indicator and a timeline panel on.

So the missing pieces are: meaningful labels, a restore action, a timeline UI, a
mode setting, and a status indicator. Not a snapshot system.

## How the established tools do it (condensed research)

Sources: Replit checkpoints/rollback, Lovable versioning 2.0, Bolt.new snapshots,
v0 versions, Claude Code `/rewind`, Cursor checkpoints + worktrees. Full citations
in the research notes; the consistent findings:

- **Default UX is a single linear timeline**, even when git branches exist underneath.
  Nobody forces the user onto a sub-branch.
- **Restore is one click, not gated.** Most do not require a confirmation; none put
  approval on the happy path.
- **"Restore as a new version" beats destructive rollback.** v0 explicitly rebuilt
  rollback this way after users lost forward work: going back appends a new entry
  rather than deleting later ones. Lovable logs a restore as a new edit (like
  `git revert`).
- **Label versions by intent, not by diff.** "Added export button," not "7 files
  changed." This is the single highest-leverage UX detail; without it the timeline is
  dead weight.
- **Nobody auto-decides to branch.** Branching is always opt-in or proposed (Cursor
  `/worktree`, v0 fork). The lightweight tools (Claude Code, Cursor checkpoints) keep a
  per-turn snapshot layer separate from git as "local undo," with git as durable history.
- **Code rolls back; data does not, by default.** Replit is the only one that even
  offers DB rollback, and it is opt-in and dev-only. Lovable/Bolt punt on it and call it
  a known gap. Consensus: roll back code, leave data alone unless explicitly chosen.

## Proposed design

### 1. A linear "version timeline" over the existing commits

- Each auto-checkpoint commit is one **version**. No new storage; read `git log` of the
  project dir.
- **Restore = restore-as-new-commit** (never `reset --hard`). "Revert to v12" applies
  v12's tree as a new commit on top of the current branch. You can always go forward
  again, and you can revert the revert. This directly kills the "stuck behind your own
  undo" failure mode.
- **Intent labels.** Store the builder turn's request/summary alongside the commit (commit
  message body, or a sidecar `.runtime/versions/<slug>.json` keyed by sha). Show that, not
  the file count.
- **Star / bookmark** a known-good version (cheap, gives a stable anchor to revert to).
- **Preview before restore** (a diff, or the embedded app preview at that version): offered,
  never required.

### 2. A mode setting per project (the "don't get stuck on a branch" part)

One small setting, default to the simplest behavior:

- **Mode A: Stay on main (default).** Every turn commits to `main`. Pure linear timeline +
  restore. No branches, no merge decisions, nothing to get stuck behind. This is the
  operator's "just move forward" path.
- **Mode B: Propose a branch for big changes.** Minor edits still commit to `main`. For a
  large or risky change (heuristic: many files / a schema or dependency change / an
  explicit "big" ask), the builder *offers*: "this is a big change, want it on a throwaway
  branch?" If you decline, or in auto mode, it just commits to `main`. If you accept, the
  branch is **disposable**: when the work is done it **squash-merges to main and the branch
  is deleted**. The branch lives and dies inside one unit of work, so there is never a
  lingering sub-branch or a required PR.

The heuristic only ever triggers a *proposal*, never a block. Approval is never on the
happy path.

### 3. Code-only rollback by default

- Revert restores the **code** for the project. App **data/state** (a project's DB, external
  services, generated artifacts outside the repo) is left as-is, with a one-line caveat in
  the revert affordance ("this restores code, not data"). If a specific app ever needs DB
  rollback, add it later as an explicit opt-in checkbox (the Replit model), never fused into
  the default.

### 4. UI surfaces (minimal)

- **Chat (inline, per turn):** a tiny "Saved, v37" marker after each turn with a "Revert to
  here" affordance on hover/click. The undo lives at the turn, where the change happened.
  When Mode B proposes a branch, that is one inline line with Yes / No (declinable; auto mode
  auto-answers). We already emit the checkpoint sha per turn, so this is mostly rendering.
- **Project page:** a **Version timeline panel** (sibling to the App and Environment panels):
  date-grouped list, intent label per row, current-version highlight, star toggle, and
  Revert / Preview per row. Plus a one-line mode selector ("Versioning: Stay on main /
  Propose branch for big changes").
- **App index / app settings:** a **status-only indicator**, no controls: current version
  number, a small "known-good" dot if the latest is starred, maybe "12 versions." It
  deep-links into the project-page timeline. (Lucide icon, not an emoji, per house style.)

## Pitfalls to avoid (what makes versioning "too big")

1. **Mandatory approve/test gates.** Keep restore one-click; approval never on the happy path.
2. **Branch sprawl / stuck on a sub-branch.** Default to commit-to-main; branches are
   disposable and auto-merged at task end; never require a PR to move forward.
3. **Destructive rollback losing forward work.** Always restore-as-new-commit, never a hard
   reset of HEAD.
4. **Rolling the DB back with the code.** Code-only by default; DB rollback is a separate,
   explicit opt-in if ever needed.
5. **Useless labels.** Label by the turn's intent or the timeline is dead weight.
6. **Uncaptured side effects.** Bash side effects, migrations, and external services are not
   in a code revert; surface that as a one-liner rather than implying revert is total.

## Open questions for the operator

1. Is per-turn the right version granularity, or should a "version" be a coherent change
   (several turns), with per-turn as a finer "undo" underneath? (Lean: per-turn commits as
   the substrate, but only *label/surface* the ones that finished a change.)
2. Default mode: confirm "Stay on main" as the default, with "Propose branch for big changes"
   opt-in per project?
3. Do any current app projects have their own DB/state we would need the code-only caveat
   for, or is it all code for now?
4. Retention: keep all checkpoint commits forever (git default), or squash old ones beyond a
   window (the existing GC already reports squashable checkpoints)?

## If/when we build it (rough phasing, not a commitment)

1. **Read-only timeline.** Surface the existing commits as a labeled version list on the
   project page + the per-turn "Saved, vN" chat marker. No restore yet. Pure value, zero risk.
2. **Restore-as-new-commit** + star/bookmark. The actual "go back" button.
3. **Mode setting** (stay-on-main default) + the app-index status indicator.
4. **Mode B** (proposed, disposable, auto-merged feature branch for big changes).
5. Optional, only if needed: per-app DB rollback opt-in.

Stop after whatever phase feels like enough. Phase 1 alone ("see my versions, labeled")
may be most of the value for the least complexity.
