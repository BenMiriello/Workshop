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

Four surfaces update four different ways, and every agent that finished a change
was re-deriving which ones it had touched, which of several commands applied,
and whether it was safe right now. Those judgement calls were the failure, not
the typing. `apply.sh` removes them: it takes no arguments and asks nothing.

It does not read git and it does not ask what you changed. It compares **what is
running against what is on disk**, surface by surface, and acts only where they
differ:

- **The UI bundle** the operator's origin serves. `frontend/dist/build-info.json`
  carries the id of the source it was built from; `scripts/ui_source_id.sh`
  computes the id of the source now. Different means rebuild.
- **The backend.** `GET /healthz` reports the `source_id` of the code answering;
  `scripts/backend_source_id.sh` computes the id on disk. Different means restart.
- **The Vite dev server**, which reads `frontend/.env` only at service start.
- **The public edge**, when `backend/workshop_public_app.py` itself changes.

Both of the first two comparisons are exact, because the running thing reports
its own version. That is what makes the script idempotent and what makes "did my
change land" answerable instead of assumed.

`bash scripts/apply.sh --check` reports drift and changes nothing. It is a good
first move when you pick up work someone else left.

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

## What it costs

Measured 2026-08-13, so nobody has to guess again:

- **UI rebuild: about 17 seconds, and nothing restarts.** The edge serves
  `frontend/dist` from disk per request, so no requests drop and no stream cuts.
  This is the operation agents were most afraid of and it is the cheapest one.
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
- `systemctl --user restart workshop-frontend` — pick up a new `frontend/.env`.
- `scripts/ui_source_id.sh`, `scripts/backend_source_id.sh` — the two definitions
  of "which version is this". Everything that compares running code to source
  calls these, so there is never a second opinion.

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
