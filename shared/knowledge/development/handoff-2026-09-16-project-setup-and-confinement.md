<!-- author: self -->
# Handoff, 2026-09-16 evening: project setup, confinement, and the surfaces around a new project

Written at the operator's request as a starting point for the next session. The
work below is COMMITTED and APPLIED unless it says otherwise.

## What started this

The operator created a project (`new-project-5ee20d`, "Overview Effect") in a
project-scoped chat and used it to discover what the system does and does not do
when you start a project. Three things came out, in ascending severity: a list of
UI and copy defects, a workflow defect (the project chat filed a ticket and
stopped working), and a confinement defect (a project agent reported working on
absolute host paths while simultaneously being unable to install a package).

The full research write-up, including everything not yet built, is
`~/.claude/plans/platform-instructions-confidential-dynamic-shell.md`. The
GitHub design decision is `docs/platform-redesign/github-connect-flow-2026-09-16.md`.

## Landed this session (12 commits, `508b3eec..HEAD`)

- **`background_runs` is confined.** It was the only claude-spawning module with
  no `scoped_exec` reference: a raw `subprocess.run` with `bypassPermissions`,
  reachable from every project chat via `spawn_background`, whose cwd fell back
  to the PLATFORM CHECKOUT when the scope did not resolve (which happens for any
  project whose tree does not exist yet). Now goes through `spawn_argv`/`turn_env`
  and refuses rather than falling back. Two side effects handled: the MCP config
  moved from a file path to inline JSON (the path did not exist inside the
  sandbox) and `_check_tree_dirty` takes the run's own tree.
- **The prompt no longer leaks host paths.** `agent_cwd()` returns the mountpoint
  when confinement is on; `_task_cli()` is spelled relative to the scope and
  returns None when the CLI is not reachable from it.
- **`check_prompt_layers.py` now renders.** It scans 50 rendered prompt blocks as
  well as the source literals, over a synthetic installation with every
  `WORKSHOP_*` variable cleared. It was demonstrated FAILING with 18 offenses on
  the unmodified tree, and mutation-tested afterwards.
- **GitHub connect is two clicks.** App Manifest registration then the OAuth web
  flow. Callbacks sit on a separate ungated router because a `SameSite=Strict`
  cookie does not survive the forge's redirect; the state nonce carries the owner.
- **A chat joins its app at creation** (`chat_thread.app_id` written and
  re-derived on sync) instead of waiting for a hand-run backfill.
- **`jobs.finish()` is idempotent** on an already-terminal job, so a run reports
  into a chat once.
- **The chat toolbar never wraps**; a renamed chat updates live.
- **Overview**: explainer floor 40 to 18; a `Section` primitive owns header
  spacing; full-width is opt-in (`Button block`, `Segmented block`). Kit 0.7.10.

Applied: bundle `c4c68fb8a748` is in sync on the public origin. Schema was
already at head. `scripts/done.sh` passes.

## NOT verified, and the first thing to check

**The backend restart was ARMED, not completed** — `apply.sh` fires it after the
turn's reply lands. Run `bash scripts/apply.sh --check` FIRST. Until that restart
happens the backend serves pre-GitHub-flow code, which is why
`/settings/apps` currently renders "This installation has no address GitHub can
redirect back to" even though `WORKSHOP_PUBLIC_ORIGIN` is set: the old `/api/github`
response has no `manifest_available` field and the client defaults it false.
After the restart, that card should show the "Connect GitHub" primary button.
**If it still says "no address", that is a real bug in `installation.declared_origins()`,
not the stale backend.**

## The operator's decisions, blocking real work

1. **`~/.claude/hooks/egress-guard.py`** (ticket `eb30f479`). THE highest-value
   item. It decides "is this a builder" by testing cwd against host build roots;
   inside bwrap the cwd is the mountpoint, so EVERY CONFINED AGENT falls to
   deny-all and cannot reach the forge or the package registries the platform's
   own capabilities prompt promises it. Separately its sensitivity lookup reads a
   `projects.json` path that no longer exists, so every UNCONFINED builder is
   waved through as trusted. Too strict where it matters, too loose where it does
   not. It is the operator's file, outside this repo, and it guards financial
   data: propose the diff on the ticket, do not edit it unilaterally. When a
   confined caller's project cannot be identified, the fallback must be the
   shareable tier, never private.
2. **The privilege model** (ticket `a2436bf4` context, and the plan's §1.4 D).
   Per-turn uid (`WORKSHOP_TURN_USER`, currently unset) or a container the agent
   is root inside. Only the second answers `apt-get`.
3. **The autonomy brake has been engaged since 2026-08-24**, reason "Disabled
   from Settings", 23 days. `Run now` passes `--force` and bypasses it, which is
   why clicking worked. Nothing runs unattended until the operator clears it.

## About the other project, which was the operator's stated priority

`projects/new-project-5ee20d` is "Overview Effect": a lightning and atmospheric
visualisation on a 3D globe, three repos under the configured owner. Measured:

- **All three repos are PUBLIC**, so cloning needs no credential at all. Its
  blocker was never GitHub auth; it is the egress guard above.
- **Its recorded GFS blocker is false.** Its own note says the atmospheric layers
  need `grib_get_data` from `libeccodes-tools`, "which cannot be installed
  without sudo". Measured: `pip install ecmwflibs eccodes` then decoding a GRIB2
  sample returns 496 values, API 2.48.2, no apt and no root. The CLI TOOL is
  missing; the CAPABILITY is not. A correction note is written into that
  project's own `knowledge/` (it cannot see this scope and this scope cannot see
  it, so it was left where the work is). It is UNCOMMITTED in that tree: a commit
  there was blocked by the classifier for overriding the author identity.
- The general correction: `apt` and `docker` are on `PATH` in the sandbox and
  neither can work, but `pip` and `npm` do. Ask whether a dependency has a
  userspace distribution, not whether it has an apt package.

## 25 tickets filed today, unstarted

Ordered roughly by value. Urgent: `eb30f479` egress guard; `c42ce1e6` one door
for spawning an agent process, with a check (the confinement fix patched one call
site; nothing yet stops the next one); `f8c7d271` a project chat can set its own
repo through the same contract the UI uses (today NO tool writes project
settings, which is why the agent left Remote URL empty); `a2436bf4` verify a
GitHub credential reaches a confined turn.

High: `a1ef4fdb` the backend suite serialises every agent behind one test;
`650bf70d` every Overview load 404s on `/api/events/sources` (the events router
is mounted loopback-only, so a browser can never call it); `4a3abacc` sign-in on
a new tab (analysis done, `SameSite=Strict` is the leading cause, needs one
reproduction); `32837886` a chat says auto mode while the session is in plan
mode; `dabdf666` is DONE but its sibling `e1835720` (repo connection as one named
action, plus a home for the branch field) is not.

Medium: `c3be59c2` ticket and run look identical and neither links to the thread
doing the work; `b42f92e5` a chat stops on a question it could have answered;
`20b28f05` a waiting chat should say waiting with a clock, not working;
`b8d8abb2` the delegation sentence; `1ae909ec` the Health app fails to load;
`33c686d2` the egress guard's regex matches data not commands; `2519c640`
reconcile the shipped GitHub auth with the design doc that describes a different
one.

Done and closed: `03ce3460` toolbar, `89928612` live rename.

## Two process notes worth inheriting

- **Do not fan out six agents that each run the full backend suite.** One test
  (`test_change_gates.py::test_every_check_the_reviewer_ran_becomes_a_gate...`)
  shells out to `scripts/reviewer.sh`, so N suites means N full gate runs. The
  parents show near-zero CPU because the work is in children, which looks exactly
  like a deadlock and was misdiagnosed as one here. Verify with targeted test
  files plus `python3 -c "import main"`.
- **Commit with explicit pathspecs** (`git commit -- <paths>`) when other agents
  have work staged. A bare `git commit` commits the whole index and will swallow
  their staged WIP; that happened once here and had to be split back out.
