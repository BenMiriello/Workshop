# The tree has two paths: /workspace in the sandbox, the host path outside it

Status: observed 2026-10-16, flagged for further thinking. Nothing here is a
decided design. The numbers and file names are what was measured that day.

## What there is

An agent turn runs in a bubblewrap sandbox (`backend/services/scoped_exec.py`).
The scope's tree is bind-mounted at a fixed path, `MOUNTPOINT = "/workspace"`,
and the real host path (the checkout's own location) does not
exist inside it. The module's own docstring says this is intended: every path
the agent's tools print is a `/workspace/...` path, and the host path is
absent. Argv and env values under the scope root are rewritten to `/workspace`
(`_remap`, `turn_env`) so tools launched inside still find things.

The backend, and so the app runner, is not in that sandbox. It sees the same
files at the host path, and `/workspace` does not exist for it.

So one directory has two names depending on who is looking. Anything that
records an absolute path while being created on one side is wrong for the
other.

## The failure it caused

A newly started app showed "Not running / build exited 127 (see log)" on the
first start and worked after a restart.

- The agent created the app's Python venv from inside the sandbox. Every
  console script in `.venv/bin` got a shebang naming `/workspace/projects/...`.
- On Start, the runner (`app_runner._run_build`) runs the manifest's `build`
  string on the host. It ran `./.venv/bin/pip`, whose shebang names a path that
  does not exist there, and bash reported "required file not found" (127).
- A restart goes through `restart_service`, which does not rebuild and starts
  the app with `.venv/bin/python` (a symlink to a real interpreter), so it
  worked.
- Seen in the logs of `terminal`, `files` and `browser`. `pyvenv.cfg` showed the
  host path while `bin/pip` showed `/workspace`, which is how the two sides
  were told apart.

## What was done about it

Commit `da5b81cf` removed a venv whose `pip` shebang named a missing
interpreter before each build. That was Python-specific code inside an
otherwise language-blind runner. It was reworked into a table,
`backend/services/relocated_artifacts.py` (`STACK_ARTIFACTS`), one row per
ecosystem with a probe, so the runner has no language logic. Only the Python
venv row exists.

This treats the symptom for the outputs the table names. It does not touch the
cause.

## What is not known

- **Which other outputs bake an absolute path.** Node apps were checked:
  `node_modules/.bin` in six installed apps held no `/workspace` path (relative symlinks). Native build
  caches, generated config, Rust `target/`, CMake caches, Go build caches and
  lockfiles with file paths were not checked.
- **The reverse direction.** A venv or cache created by the backend carries the
  host path, which an agent in the sandbox cannot execute. No instance of this
  was looked for.
- **How far the table can go.** It names outputs one at a time and is reactive.
  Whether that is enough, or only holds until the next ecosystem, is open.

## Ideas that came up, not evaluated enough to recommend

- **Mount the tree at its real host path inside the sandbox.** That would
  remove the mismatch at the source for every ecosystem. It conflicts with the
  documented design (host path absent, one fixed mountpoint for every scope,
  worktree and owner copy), touches `scoped_exec.py` and about 16 non-test
  callers, and has tests that assert `/workspace`. It could not be tried from
  inside the sandbox. Whether the isolation property depends on the path
  being hidden, or only on the host tree not being reachable, was not
  established.
- **A symlink from `/workspace` to the tree on the host.** Needs root. Only one
  tree could be the target, so a scope that is a single app would resolve to
  the wrong directory.
- **Build on the host side only.** Builds could run outside the sandbox, so
  that nothing built carries `/workspace`. Not examined: the agent also runs
  installs and builds itself while working.

## Where to look first

- `backend/services/scoped_exec.py`: `MOUNTPOINT`, `wrap`, `_remap`, `turn_env`.
- `backend/services/app_runner.py`: `_run_build`, `restart_service`.
- `backend/services/relocated_artifacts.py`: the table and its probe.
- `PROJECT.md`, scoped-execution entries of 2026-08-18 and 2026-08-19 (the
  earlier `/workspace`-vs-host root mismatch, fixed for env values, not for
  files on disk).
