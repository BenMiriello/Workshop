# Applying a change (development)

**Finished a change? Run this:**

```bash
bash scripts/apply.sh
```

That is the whole instruction. It works out what is stale and fixes it. It is
safe to run at any time, from any agent, concurrently with any other agent, and
running it when nothing changed does nothing.

This file is the ONLY place in the repo that spells out the mechanics. If you
find restart or rebuild commands anywhere else, they are either a script's own
header or they are stale (see "Where instructions live", below).

Scope: the development deployment. Production is a separate problem and is not
designed here.

## Why one command

Several surfaces update several different ways, and every agent that finished a
change was re-deriving which ones it had touched, which of several commands applied,
and whether it was safe right now. Those judgement calls were the failure, not
the typing. `apply.sh` removes them: it takes no arguments and asks nothing.

It does not read git and it does not ask what you changed. It compares **what is
running against what is on disk**, surface by surface, and acts only where they
differ:

- **The UI bundle** the operator's origin serves. `frontend/dist/build-info.json`
  carries the id of the source it was built from; `scripts/ui_source_id.sh`
  computes the id of the source now. Different means rebuild.
- **Each installed app's own UI bundle**, which is a SEPARATE surface from the
  one above and has been since the runtime app loader landed. An app's screens
  are compiled by the app's own build into the app's own directory and fetched
  by URL, so rebuilding the shell no longer carries them: without this, editing
  an app page and running the apply step published nothing. `scripts/
  build_app_ui.sh` compares each app's build inputs against the stamp its last
  build left, and rebuilds only what moved.
- **The backend.** `GET /healthz` reports the `source_id` of the code answering;
  `scripts/backend_source_id.sh` computes the id on disk. Different means restart.
- **The Vite dev server**, which reads `frontend/.env` only at service start.
- **The public edge**, when `backend/workshop_public_app.py` itself changes.
- **The database schema** — reported, never applied. See below.
- **Which apps the running backend actually mounted**, and from which roster
  file — reported, never applied. See below.

Both of the first two comparisons are exact, because the running thing reports
its own version. That is what makes the script idempotent and what makes "did my
change land" answerable instead of assumed.

`bash scripts/apply.sh --check` reports drift and changes nothing. It is a good
first move when you pick up work someone else left.

## The other surface it reports and never fixes: what the backend mounted

Every check above compares SOURCE to SOURCE, and that is exactly why this one
had to be added. On 2026-09-04 every app route on this installation answered
404 for two days while `apply.sh --check` printed `ok backend` and `in sync` on
every run. The source was identical and its id matched; what differed was which
`.runtime/installed-apps.json` the running process resolved. That path is
anchored to the module's own location, so two checkouts of one source on one
host read two different rosters — and a roster is instance state that no source
hash covers.

Nothing served said which one it had read, and it could not be established from
outside either: an agent in a sandbox does not share the backend's mount
namespace, so `/proc/<pid>/cwd` and `/proc/<pid>/environ` are unreadable and
every check measured the checker's own filesystem instead.

So the process answers for itself. `GET /healthz` carries an `apps` record —
the resolved roster path, the roster it read, the ids whose routers it actually
mounted, the catalog it had to draw from, and the route count — pinned at mount
time rather than recomputed per request. Run it directly:

```
python3 scripts/backend_apps_state.py
```

| what it prints | what it means |
| --- | --- |
| `ok` | the process mounted everything its roster names, and that roster agrees with this tree's (or this tree has none, which a linked worktree and a fresh clone both legitimately do) |
| `unresolved` | the roster names apps the process did not mount. Those routes are answering 404 right now; the usual cause is that their distributions are not installed into the backend venv (`python3 scripts/install_apps.py`) |
| `roster_differs` | the process read a different roster file than this tree holds. This is the outage above, and note that the process looks internally consistent while it happens |
| `unreadable` | one side's roster is present and cannot be parsed |
| `unreported` | the backend answers but carries no `apps` record, so it is running code from before this existed. Restart it once |
| `unreachable` | nothing answered on the backend port |

The last two are **not** in sync and `apply.sh` says so rather than staying
quiet, the same way an unmeasurable schema does. Neither a restart nor a
rebuild resolves the first four: they are configuration, so the script reports
and names the fix.

An empty roster with nothing mounted is a **blank instance**, which is a
correct state — a fresh clone installs no apps — and it stays silent. The
distinction between that and a roster of eight serving nothing is the entire
point; they used to produce the same empty list and the same empty log.

## The one surface it reports and never fixes: the schema

`apply.sh` compares the tree's alembic head against the live database's
`alembic_version` and prints it beside the others. It does **not** run alembic,
and nothing else in the deploy path does either.

| what it prints | what it means | does it hold the restart? |
| --- | --- | --- |
| `ok      schema (<rev>)` | live is exactly at the tree's head | no |
| `STALE   schema: the tree is N migrations AHEAD` | code here queries columns the database does not have | **yes** |
| `STALE   schema: the live database is at <rev>, which THIS TREE DOES NOT CONTAIN` | a revert, or a checkout older than the database | no |
| `warn    schema: COULD NOT DETERMINE ...` | unreachable, or no driver — this is **not** "in sync", and `--check` exits non-zero | no |
| `warn    schema: no database configured ...` | nothing to compare | no |

**Tree ahead of live is the one case that declines the backend restart**, in the
same shape as the uncommitted-work refusal: it says why and names the command.
Applying it is yours to run, in a terminal:

```bash
set -a; . ~/.config/workshop/db-admin.env; set +a
cd backend && .venv/bin/alembic upgrade head
```

Then `bash scripts/apply.sh` again, which will now restart.

The reverse case — live ahead of the tree — is reported but does not block,
because the code on disk never names what that revision added, so the extra
schema is inert, and after a revert the restart is the thing that puts the app
back on code the database can serve.

WHY THIS EXISTS, and why it is a guard rather than an auto-migrator. On
2026-09-01 a commit added two columns to `chat_thread` plus the migration that
creates them. Live stayed at `b9f3e2d07a16`, a read through the committed code
answered `UndefinedColumn: column chat_thread.armed does not exist`, and a
deferred restart was already armed; the commit was reverted to save the app.
`restart_backend.sh`'s import preflight cannot catch that class of break — a
missing column fails at QUERY time and `import main` imports perfectly. Applying
DDL to a live database is an act with a blast radius no script can judge, so the
job here is to make the drift impossible to miss, not to resolve it.

## You do not need to coordinate

Several agents share this checkout and several chat turns run at once. You do not
need to check on them, wait for them, or ask the operator whether it is a good
moment.

- The UI build takes an `flock`. Concurrent callers queue; they do not race.
- The backend restart is deferred: it waits for your own turn to finish, then
  briefly for anyone else's, then fires.
- A turn that does get interrupted by a restart is **paused, not lost**. Its
  agent process is detached and survives (`KillMode=process`), the backend adopts
  it again on the way back up, and the browser reconnects on its own.

An agent reasoning about other agents' in-flight work is inventing a problem the
tooling already owns.

### Why the backend restart in particular is deferred

Because in a self-hosting checkout **the agent is a child process of the backend
it is editing**. Measured 2026-09-01:

    uvicorn main:app (:8000)
      -> bwrap
        -> claude
          -> the agent's own shell

Restarting in-turn would kill the session making the change, and every subagent
under it. `restart_backend.sh` refuses while a turn it hosts is live, and that is
correct rather than an obstacle to route around. Do not force it, do not reach
for `--publish-wip` to get past it, and do not try to restart out of band.

**The consequence to plan for: within the turn that makes a backend change, you
cannot see that change running.** Anything you `curl` or screenshot is the old
code. So:

- **Frontend changes CAN be closed in-turn.** The bundle rebuilds and the
  operator's origin serves it immediately; no restart is involved. Screenshot it
  and be done.
- **Backend changes CANNOT.** Either say **"unverified-live"** and mean it, or —
  better, and this is the technique that works — stand up a second backend on
  another port against the same tree and drive that. It costs a minute and it
  turns a claim into evidence.

Reporting a backend change as verified in the same turn you made it is either a
mistake or a different verification than the one it sounds like. Say which.

## What it costs

Measured 2026-08-13, so nobody has to guess again:

- **UI rebuild: about 17 seconds, and nothing restarts.** The edge serves
  `frontend/dist` from disk per request, so no requests drop and no stream cuts.
  This is the operation agents were most afraid of and it is the cheapest one.
- **An app UI rebuild: well under a second each** (measured 2026-09-05 across
  seven apps, the largest 0.4s), because an app bundle externalizes React, the
  router and the design kit rather than compiling them. A run with nothing to do
  only hashes, and nothing restarts either way.
- **Vite restart: about 2 seconds.** Does not touch the operator's origin.
- **Backend restart: a few seconds of pause**, deferred until after your reply
  lands. This is the only genuinely disruptive one, which is why it is the only
  one that waits.

## The two origins

Both serve this UI and they update differently.

- `$WORKSHOP_PUBLIC_ORIGIN` (port 8002) serves the **built** `frontend/dist`.
  **This is the one the operator uses** (settled 2026-08-11). A frontend change
  is not delivered until this origin serves it.
- `$WORKSHOP_LOCAL_ORIGIN` (port 5173) is the Vite dev server, from source, live
  on save with no build.

Both are set in `~/.config/workshop/instance.env`. `scripts/ui_sync.sh` reports
what each origin is actually serving.

**`packages/ui` counts as frontend.** `frontend/vite.config.ts` sets
`conditions: ["workshop-source"]`, and the kit's exports map resolves that to
`packages/ui/src`, so the kit compiles *into* the bundle. A change to a shared
primitive needs a rebuild exactly like a change under `frontend/src`.

## The stale-bundle warning is not noise

`check_design.sh` printing **"the public bundle is STALE"** means the operator
may be looking at old code. Never report a UI task complete over it. On
2026-08-09 a whole session of correct width fixes was never built, and the
operator said three times that it was not done while the warning fired after
every commit and went unread.

Committing on the default branch in the canonical checkout rebuilds
automatically (`.githooks/post-commit`, install with `scripts/install_hooks.sh`).
That does **not** cover a linked worktree under `branches/`: the public edge
serves the canonical tree's `dist` by absolute path, so a UI change built from a
worktree reaches nobody until the branch merges. `apply.sh` says so when it
detects one.

## When something does not work

- `bash scripts/apply.sh --check` — what is stale right now.
- `bash scripts/ui_sync.sh` — what each origin is actually serving.
- `.runtime/apply-last.json` — the outcome of the last deferred backend restart,
  including whether the pid actually changed. `apply.sh` prints it.
- `/tmp/workshop-backend.log` — the deferred restarter's own log.
- `journalctl --user -u workshop-backend -n 50` — why the backend did not come up.

A deferred restart reports "armed" and then succeeds or fails minutes later, so
its outcome lands in the receipt rather than in the terminal that asked for it.
Check the receipt on your next turn rather than assuming.

## The underlying scripts

`apply.sh` calls these. Call them directly only when you specifically want one
of them; the entry point is `apply.sh`.

- `scripts/build_workshop_public.sh` — build `frontend/dist`. Add
  `--restart-edge` only when `backend/workshop_public_app.py` changed.
- `scripts/restart_backend.sh --deferred` — restart the backend after the
  current turn's reply lands. `--force` skips the wait. Bare restarts refuse
  while a turn is live, which is correct for a human at a terminal.
- `scripts/build_app_ui.sh` — rebuild the installed apps' own UI bundles.
  `--check` reports drift without building. It names no app: an app is in scope
  because its own manifest declares a `ui.entry`.
- `systemctl --user restart workshop-frontend` — pick up a new `frontend/.env`.
- `scripts/ui_source_id.sh`, `scripts/backend_source_id.sh` — the two definitions
  of "which version is this". Everything that compares running code to source
  calls these, so there is never a second opinion.
- `scripts/migration_state.py` — the tree's alembic head against the live
  `alembic_version`, as one `state<TAB>headline<TAB>remedy` line. One SELECT, no
  writes, no alembic invocation. Run it directly to see the schema answer alone.
- `scripts/backend_apps_state.py` — what the RUNNING backend mounted and from
  which roster file, against the roster in this tree. Same
  `state<TAB>headline<TAB>remedy` contract under `--tsv`; run it bare for a
  readable report, `--json` for the raw record. Read-only: one GET to
  `/healthz` and one file read.

## Where instructions live

The commands are written **here and nowhere else**. Every other place links to
this file and states no command of its own. That rule is the fix for the actual
problem: on 2026-08-13 restart instructions were found in about thirty files with
six live contradictions, including two different answers to "which command
restarts the backend" and a flat disagreement about whether a frontend change
needs a build at all.

The three places that legitimately mention applying a change, each pointing here:

- `CLAUDE.md` (repo root) — loaded in every session in this tree.
- `frontend/CLAUDE.md` — the design-system doc for all frontend work.
- `backend/services/chat_context.py` — the runtime briefing injected into every
  in-app chat and build turn.

**Dated handoff docs and everything under `docs/platform-redesign/` are
historical.** They record what was true on the day they were written and several
of them describe restart behaviour that has since been fixed or reversed. Do not
follow their restart instructions and do not update them; this file supersedes
them.
