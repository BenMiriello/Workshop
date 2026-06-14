# Agent rules — {{title}}

- Read `PROJECT.md` first every turn.
- Update `TODO.md` as work progresses.
- Edit only within this directory unless the operator explicitly asks for
  workshop-wide wiring (then stop and surface what files outside need to change).
- Workshop design system is mandatory. Build from `frontend/src/ui` primitives.
  Lucide icons only; no emoji. Five status kinds. ConfirmDialog for destructive.
- Before claiming a UI task done: run `scripts/check_design.sh` and `npx tsc --noEmit`
  from the workshop root.
- No stubs (TODO/FIXME/NotImplementedError/pass # stub/...). Mark `[~] BLOCKED` if
  a task is too big to finish in one tick.
