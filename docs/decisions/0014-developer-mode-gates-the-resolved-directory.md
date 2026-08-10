# 0014. Developer mode gates the resolved directory, not the scope name

- Status: accepted
- Date: 2026-08-10
- Design: `docs/platform-redesign/dev-mode-and-development-scope-2026-08-09.md`
- Related: 0013 (public auto-deploy is pre-launch), [SEC-A1] (execution
  identity), [SEC-A2] (stop representing scope as a security boundary)

## Context

`workshop` scope means two unrelated things. It is the root organising scope —
the widest notes layer, the root ticket queue, the universal parent that every
narrower scope inherits from — and it is also, by accident of naming, permission
to run an autonomous agent against the Workshop's own source. Everyone needs the
first. Only the developer of this installation should have the second.

The survey in §2 of the design doc is what settles the shape. The platform repo
is not reachable under one name: it is reachable under at least five, through
four subsystems (a root-scope ticket run, the `general` chat aliasing to the same
root, a `project:workshop` registry row with `self: true`, four retired
`build:workshop-builder*` records that `get_agent()` does not filter out, and
`agent:<slug>` for a maker whose build-agent record carries the self cwd). Two
more doors are the loops: `ralph.sh` and `queue_runner.sh` both default `DIR` to
the platform repo, so a sweep started with no argument works the Workshop's own
tree. No guard anywhere prevented any of this.

So renaming the scope closes exactly one door. A plan built on "make `workshop`
non-runnable" would produce a boundary that reads as real and is not — the same
failure [SEC-A2] exists to unwind.

## Decision

**Developer mode is whether `is_self_dir(cwd)` is permitted as a run target.**

The gate is the resolved working directory, because that is the one thing all
nine doors have in common. The predicate already exists —
`app_runner.is_self_dir()` / `is_self_project()` — and was previously consulted
only to refuse starting the self project as an app and to skip auto-checkpoint
commits. It is now consulted on the run-launch paths.

Four chokepoints cover every door: `task_scope.root_for_context`,
`launch_task_run`, `build_streaming.start_turn`, and the two loop scripts' `DIR`
defaults.

Two flags, one per side, each read in one place: `WORKSHOP_DEV_MODE=1` (backend
env) and `VITE_DEV_MODE` (frontend build-time). Both default off. In a normal
build the capability is not in the bundle; in a normal deployment the path does
not resolve. Absent code needs no permission system, which is how this satisfies
"a regular user cannot edit the Workshop" without adding the multi-tenant
guardrails this repo forbids.

With the gate on the directory, `development` becomes the honest name for work
that targets the platform repo, and the **only** name for it. The other aliases
collapse into it. `workshop` keeps its notes, its queue, its chats and its
universal-parent role, and loses exactly one thing: resolving to the platform
repo as a working tree.

A scope's store location and a run's working tree are different things, and only
the second is gated. Workshop-scope data still lives inside the platform repo on
disk. That is storage, not a work target, and nothing here moves those files.

## What this is, honestly

A product capability boundary and a safety interlock. **Not** a security boundary
against a hostile user: anyone with a shell on the box can still edit the source.
Real confinement is a separate execution identity, which is [SEC-A1] and still
open. Nothing in the UI or the docs may imply otherwise, and the framing is
written at the flag and at each chokepoint so a later audit does not have to
unwind it.

What it does buy: a normal build cannot be used to modify the app it is running;
the always-on loops cannot silently rewrite the platform in a normal install; and
"edit the Workshop" becomes a named visible mode rather than an emergent property
of the root scope and four forgotten registry rows.

## Consequences

- The root queue moves wholesale to `development`. Combing it found 93 tickets,
  all platform or installation work, and not one personal-ops ticket — so the
  migration is a move, not a sort. Host and infra tickets belong to `development`
  too, because `development` means work on *this installation*: its source and
  the box it runs on.
- The always-on loops stay on. The only change is that the platform repo stops
  being their default target; a target must be named.
- Phases 3 to 5 of the design must not ship without phase 4. Between them the
  system would claim a boundary it does not have, which is worse than the honest
  status quo.
- Scope is still a bare string parsed in four independent places
  (`knowledge._parse_scope`, `task_scope.root_for_context`/`key_for_root`,
  `chatScope.ts`, `lib/tasks.ts`). Adding `development` pays that tax a second
  time. Introducing a canonical scope enum is a migration, not a rename, and is
  deliberately not bundled here.
- This record and 0013 are linked. When the public origin stops being a
  pre-launch arrangement, this gate is what makes a real production build
  possible, and the two should be revisited together.
