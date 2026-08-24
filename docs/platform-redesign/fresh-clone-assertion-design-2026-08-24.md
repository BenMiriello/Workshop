# `scripts/check_fresh_clone.sh`: the executable definition of "app separation is done"

**Status: design, not built. 2026-08-24.** Specifies the command named in
[`app-separation-why-it-recurs-2026-08-24.md`](app-separation-why-it-recurs-2026-08-24.md)
section 4. That document argues for the instrument; this one specifies it, and
corrects three of its four assertions against what the repository actually does
when you run them.

Everything numbered below was measured on this machine on 2026-08-24 at
`9ec46f88`, by cloning, installing, building and booting. Where a number is
inherited from another document it says so. Where an assertion turned out not
to hold as written, it says that too.

## 0. What measuring it changed

Four things the plan did not anticipate, each of which changes the design:

1. **The build succeeds in a fresh clone today.** A clone of `HEAD` installs and
   builds in 24 seconds with no edits. Assertion 2 as written ("the build
   succeeds with zero apps installed") is therefore satisfied today by accident,
   because the 102 app-source files are IN the repo, so a clone has them. The
   assertion only means anything when it is run against a tree with the app
   source **removed**. That is how it is specified in section 2 below, and run
   that way it fails, loudly and specifically.
2. **Assertion 2 has a backend half that nobody has named.** The frontend
   failure (18 static imports in `frontend/src/apps/registry.tsx`) is the one
   every document cites. Removing the app source also breaks `import main`:
   `backend/routes/owntracks.py`, which is platform code and is not counted in
   the 102, imports `services.location`, which is. Details in section 2.3.
3. **Assertion 4 does not pass today.** It is at 31 hits. The plan assumed zero.
   All 31 are in four test files and all are fixtures, so the fix is small, but
   it is a prerequisite commit rather than a thing the gate can simply start
   asserting. Section 4.
4. **Assertion 3 needs a database, and PostgreSQL server binaries are on this
   box while Docker is not.** `GET /apps` is served from Postgres with no file
   fallback, so "the shell serves an empty app catalog" cannot be asserted
   against a backend with no database. A throwaway cluster under `initdb` costs
   0.35s and is perfectly isolated. Section 3.

## 1. Assertion 1: NO APP SOURCE

**Statement.** In a clone of the repository at `HEAD`, the number of tracked
files classified as app source by `scripts/check_no_app_names.py` is zero.

**Today: 102.** So it is pinned and ratchets down, exactly as the plan says.

    frontend/src/pages/apps/**     62
    backend/{routes,services}/**   40
      birds 48   commute 21   finance 9   notes-app 9
      budget 7   location 5   health 3

**It reuses the existing classifier and adds no second definition.** The
`content` category in `check_no_app_names.py` is path-shaped, has zero false
positives by construction, and already carries the roster logic (catalog plus
per-instance install list plus every app the baseline has ever watched). This
assertion is `scan()["content_files"] == 0`, nothing more.

**The pin lives in `scripts/no_app_names_baseline.json`, not in a new file.**
`content_files: 102` is already pinned there, `--write-baseline` already refuses
an upward move without `--because`, and `check_ratchets_not_loosened.py` already
blocks re-pinning inside a review. A second copy of the number in a
fresh-clone baseline would be a second declaration of one fact, which is the
defect `check_one_definition.py` exists to catch.

**It asserts `content_files` only, not the other two counts.** `import_sites`
and `literal_sites` are the existing checker's business and are gated per diff
by it. Two reasons to keep them out of here. First, `literal_sites` is at 305
against a pinned 302 and the checker is failing on clean HEAD right now, so
importing it would make the new gate born red. Second, the assertion is about
what a stranger RECEIVES, and a literal in a platform file is coupling debt
rather than a file the stranger should not have been sent.

**Measured cost: 3.3s** for the full scan over 1,562 tracked files inside the
clone.

**Roster parity in a clone is fine, and this was checked.** The roster is drawn
partly from `.runtime/installed-apps.json`, which is untracked and therefore
absent from a clone. Run inside a clone the checker reported exactly the host's
numbers, 102 / 20 / 305, because the baseline's `watched` list already names all
eight apps. No host-side roster injection is needed.

### 1.1 One classifier bug found, which caps this assertion at 1

`backend/routes/notes_all.py` is counted as app source. It is not. It is the
platform's cross-scope knowledge feed, and `check_no_app_names.py` already knows
this: `_PLATFORM_MODULES = frozenset({"notes_all"})` exempts it from the
`imports` category, with a comment explaining that a prefix match cannot tell
the notes app's modules from the platform's. The `content` category never got
the same exemption, because its pattern is
`^backend/(routes|services)/notes(_[a-z_]+)?\.py$` and `notes_all.py` matches.

So `content_files` cannot reach 0 while that file exists where it is. Either
the content patterns consult `_PLATFORM_MODULES` the way the import matcher
does, or the file moves. That is a one-line fix and it re-pins DOWN, 102 to 101.
It is listed here rather than done here because this document changes no code.

## 2. Assertion 2: THE BUILD SUCCEEDS WITH ZERO APPS INSTALLED

**Statement.** Take the clone. Delete every tracked file the assertion-1
classifier calls app source. Install. Build the UI kit and the web bundle. Build
the backend's import graph. All of it must succeed.

This is the assertion no other gate in the repository makes, and the deletion
step is what makes it mean anything. Without it, the check passes today for the
wrong reason.

### 2.1 What it does, precisely

1. `git clone --no-hardlinks <repo> <workdir>/clone`, then
   `git -C clone checkout --detach <rev>` (default `HEAD`).
2. Run the assertion-1 classifier to get the app-source file list. Delete those
   files from the clone. Nothing else is touched: no registry is edited, no
   `.runtime` file is written, no `installed-apps.json` is created. "Zero apps
   installed" means the app content is not there and the instance never had it.
3. `npm ci` at the root (workspaces hoist; no per-package install).
4. `npm run build:ui && npm run build:web`.
5. `python3 -m venv` plus `pip install -r backend/requirements.txt`.
6. `cd backend && python -c 'import main'` in a subprocess. This is exactly what
   uvicorn does at startup, and `scripts/restart_backend.sh` already uses it as
   its preflight for the same reason.

Steps 3 to 6 have no dependency on any gitignored file. Verified: a bare clone
with no `.env`, no `frontend/.env`, no `.runtime/`, no `node_modules` and no
`packages/ui/dist` built cleanly. The repository has no submodules, no
`.gitattributes`, and no Git LFS, so a clone is the whole tracked tree.

### 2.2 Result today: FAILS, in the frontend

`tsc -b` exits non-zero with 18 instances of TS2307, all in one file:

    src/apps/registry.tsx(57,39): error TS2307: Cannot find module '../pages/apps/Finance'
    src/apps/registry.tsx(58,38): error TS2307: Cannot find module '../pages/apps/Budget'
    ... 16 more, one per statically named app page ...

This is mechanism M3 from the why-it-recurs document, produced as an exit code
instead of a paragraph. `lazyPage` defers loading, not building; the specifiers
are literal, so the modules are in the build graph whether or not the app is
installed.

### 2.3 Result today: ALSO fails, in the backend, and this is new

    ImportError: cannot import name 'location' from 'services'
      backend/services/app_registry.py:54  _has_module("owntracks")
      backend/routes/owntracks.py:17       from services import owntracks, ...
      backend/services/owntracks.py:24     from . import location

Three things are worth stating about this.

`services/app_registry.py` already has the guard for exactly this case, and the
guard does not fire. `_has_module` catches `ModuleNotFoundError`, which is what
you get when the module you asked for is missing. Here the module asked for
(`routes.owntracks`) is present; it is a module IT imports, two levels down,
that is gone, and that surfaces as a plain `ImportError`. The comment above
`_has_module` states the intent correctly ("a factory whose modules aren't
present is skipped") and the implementation is one exception class too narrow.

`backend/routes/owntracks.py` is platform code by every current measure. It is
not in the 102, the roster does not contain `owntracks`, and the import checker
does not flag it. So **assertion 1 reaching zero would not by itself make
assertion 2 pass.** The two are independent, which is the strongest argument in
this document for having assertion 2 at all: the count going to zero is not the
same event as a stranger's install working.

The check stops at the first backend import failure and reports it. Enumerating
every such edge is the work, not the gate.

### 2.4 Measured timings

| step | cold caches | warm caches |
| --- | --- | --- |
| `git clone --no-hardlinks` (local path) | 0.23s | 0.09s |
| `npm ci` at root, 347 packages | 2.62s, 50 MB fetched | 1.72s |
| `npm run build:ui` | 2.54s | 2.54s |
| `npm run build:web` (frontend, apps present) | 13.94s | 13.94s |
| `build:ui` + `build:web` back to back | 18.89s | 18.89s |
| `build:ui` + `build:web`, app source removed, to first `tsc` failure | 8.64s | 8.64s |
| `python3 -m venv` + `pip install -r requirements.txt`, 344 MB venv | 17.47s | 8.91s |
| `python -c 'import main'` in the clone | 1.82s | 1.82s |

"Cold caches" means a fresh `--cache` directory for npm and a fresh
`--cache-dir` for pip, on this host's network (npm registry ping 154ms, roughly
30 MB/s sustained). On a slower link the two install steps are the only ones
that move. Nothing here is dominated by CPU.

The build currently fails fast, at 8.6s, because `tsc -b` runs before `vite
build`. When the loader lands and the build succeeds, this step becomes the full
18.9s.

## 3. Assertion 3: THE SHELL BOOTS AND SERVES WITH AN EMPTY APP CATALOG

**Statement, reduced from the plan, and here is why.** The plan says "Nav, chat
and settings render with an empty app catalog." Rendering cannot be asserted:
`playwright-core` is a root devDependency but no browser binary is installed
(`~/.cache/ms-playwright` does not exist), and pulling one is a ~150 MB download
that would dominate the runtime of the whole check. What CAN be asserted, and is
worth as much for the question this gate answers, is that the backend the shell
talks to comes up against an empty catalog and answers the shell's own calls
without a 5xx. That is the reduced statement:

1. `import main` succeeds and constructs the route table (measured: 333 routes,
   268 distinct paths, with app source present).
2. `uvicorn main:app` serves `GET /healthz` 200.
3. A first user can be created and promoted, and then:
   - `GET /apps` returns `{"apps": []}`
   - `GET /agents` returns `[]`
   - `GET /chat/threads` returns `[]`
   - none of the three returns 5xx
4. Zero routes mounted from `app_registry.installed_specs()`.
5. The built bundle exists and `index.html` references an entry chunk that is
   present on disk. Static serving is a separate service in this deployment
   (`scripts/apply.sh` and the public edge serve `frontend/dist`; the backend's
   `GET /` only redirects or reports liveness), so the bundle is asserted as an
   artifact rather than over HTTP.

All five are measured as working TODAY, with app source present and zero apps
installed. Assertion 3's mechanism is proven. Assertion 3 as an app-separation
statement is blocked behind assertion 2: with app source removed there is
nothing to boot.

### 3.1 The isolation problem, which is the hardest practical part

The live system is running now: backend on `127.0.0.1:8000`, Vite on `:5173`,
Postgres on `127.0.0.1:5440`, plus three other Postgres servers on this host
that belong to other stacks. A second backend booted carelessly against that
state is not merely noisy, it is destructive. `main.py`'s lifespan starts seven
background loops, and at least four of them mutate shared state:

- `jobs.reconcile_loop` closes Jobs left `running` by a dead process. Pointed at
  the live database it would close jobs the live backend is running.
- `work_driver.reconcile_loop` ensures a resident queue runner exists for every
  armed scope. It would spawn duplicates.
- `chat_drain.continuation_loop` starts a thread's next turn when a
  continuation seed is present. It would start turns.
- `port_registry.reconcile_loop` frees ports of processes that have died.
- `semantic_index.maintenance_loop` calls `pin_owner()` and can start a
  multi-GB rebuild.

There is no environment switch that disables these. That is worth knowing and
is not worth adding: the isolation below is complete without one, and a
"disable background work" flag is a thing a future agent would find and use to
make a gate green.

### 3.2 The isolation, item by item, all of it measured

**Filesystem.** `WORKSHOP_ROOT` defaults to two levels above
`backend/wsplatform/paths.py`, so a backend run out of the clone already reads
and writes the clone's own `.runtime/`. The check sets it explicitly anyway and
launches with `env -i`, so nothing about the live instance's environment leaks
in. `HOME` and `TMPDIR` are pointed at a throwaway directory inside the workdir,
which keeps the process out of `~/.config/workshop` and away from every secret
the live instance holds. Measured: the clone's backend booted with a
`.runtime/` containing exactly the files it created itself.

**Database.** `initdb` a throwaway PostgreSQL cluster in the workdir, listening
on a **unix socket inside the workdir** with TCP disabled (`-h ''`). This is
stronger than a spare TCP port: there is no port to collide with, nothing on the
loopback interface to connect to by accident, and the cluster dies with the
workdir. Measured: `initdb` plus `pg_ctl -w start` plus `createdb` plus
`CREATE ROLE` totals **0.35s**.

Docker is NOT usable for this. `infra/postgres/docker-compose.yml` describes the
live server, and `docker` is installed, but the daemon socket is not reachable
from an agent's sandbox (`dial unix /run/user/<uid>/docker.sock: no such file or
directory`). PostgreSQL 15 server binaries ARE present at
`/usr/lib/postgresql/15/bin`. The live deployment runs Postgres 18; the
migrations were run against 15 as part of this measurement and applied cleanly,
23 tables, in **0.41s**. If a future migration needs an 18-only feature this
step gets a version probe and a skip, and that is a better failure than a
docker dependency.

**Bring-up sequence, measured, and it is not the documented one.** The order
that works is:

    CREATE ROLE workshop_app LOGIN NOSUPERUSER NOBYPASSRLS   (before alembic)
    alembic upgrade head                                      0.41s
    python -m db.bootstrap  (WORKSHOP_DB_APP_PASSWORD set)    0.16s
    POST /auth/register     (WORKSHOP_REGISTRATION_OPEN=1)
    auth_store.set_operator(1)
    python db/backfill.py --apply --force
    restart uvicorn

`db/bootstrap.py`'s docstring says "Run after `alembic upgrade`". Run in that
order against an empty cluster the migration fails with `role "workshop_app"
does not exist` on `REVOKE INSERT, UPDATE, DELETE ON organization FROM
workshop_app`. The role has to exist first, then `db.bootstrap` sets its
password and grants. Second, `db/backfill.py --apply` refuses without `--force`
("the JSON ticket store is retired"), which is right for the live instance and
wrong for an empty one; the check passes `--force` because it is seeding a
database it created 2 seconds ago. Third, `POST /apps` and friends 500 with
`TenantUnresolved: app user 1 has no user_account/membership` until backfill has
run, so the order above is not optional.

None of that is a defect the check should paper over, and all of it is
information a stranger's install needs. A reasonable follow-on is a
`scripts/bootstrap.sh` path that does this in one step; the check should call
that path once it exists rather than keeping its own copy.

**Ports.** The backend binds `127.0.0.1` on a port picked from the ephemeral
range and probed free first. `8000`, `5173`, `5440`, `5432`, `5433` and `5436`
are all in use on this host and none of them is touched.

**Network egress.** `deploy_watcher.watcher_loop` polls a git remote. In the
clone the only remote is the local repository path, so the poll is a read of a
directory on the same disk. It cannot push, and the clone's
`.runtime/projects.json` does not exist so it finds nothing to deploy.

**Registration.** `WORKSHOP_REGISTRATION_OPEN` fails closed and is set only for
the temp instance. `auth_store` is SQLite under the clone's `.runtime/`, so the
first user exists only in the workdir.

**Teardown.** `pg_ctl -m immediate stop`, kill the uvicorn pid, remove the
workdir. On failure the workdir is kept and its path printed, because a gate
that deletes its own evidence gets rerun by hand anyway.

### 3.3 Measured cost of assertion 3

| step | measured |
| --- | --- |
| `initdb` + `pg_ctl start` + `createdb` + `CREATE ROLE` | 0.35s |
| `alembic upgrade head` (23 tables, Postgres 15) | 0.41s |
| `python -m db.bootstrap` | 0.16s |
| uvicorn launch to `GET /healthz` 200 | 2.06s |
| register + promote + backfill + relaunch | 1.99s |
| **subtotal, excluding the venv it needs** | **4.97s** |

Cheap once the venv exists. The venv is the expensive part (8.9s warm, 17.5s
cold) and it is shared with assertion 2's backend import.

## 4. Assertion 4: NO OPERATOR IDENTITY

**Statement.** In a clone of the repository at `HEAD`, zero tracked files
contain this machine's identity: its login name, its home directory path, its
git identity, its tailnet hostname, or any value from its `instance.env`.

**This already exists and is not reimplemented.** `scripts/check_no_hardcoding.sh`
asks the machine who it is and greps the tracked tree for the answers, over four
probes (`homes`, `identity`, `tailnet`, `mail`). Assertion 4 is that script run
against the clone, in absolute mode, expecting zero. Writing a second list of
patterns here would be a second declaration of "what counts as identity", and
would be a guard that is itself a violation, which is the exact mistake that
script's header records having already been made once.

### 4.1 Measured today: 31 hits, not zero

Absolute mode, code and config (the gating scope, which excludes `docs/` and
`*.md`), 12 machine-derived needles:

    homes      6   absolute home paths in tracked source
    identity  25   references to this deployment's identity
    tailnet    0
    mail       0

All 31 are in four tracked files, and every one is a test fixture:

    backend/tests/test_control_verbs.py        15   operator username as an `actor=` value
    backend/tests/test_control_routes.py        4   same
    backend/tests/test_check_ratchet_delta.py   6   `/home/<name>/...` fixture paths for
                                                    the delta checker's own test
    frontend/src/lib/holders.test.ts            3   login name as a `principal` value

With `--all` (docs included) the totals are 14 and 69, the extra being dated
handoff documents that quote the operator by name. Documents are excluded from
the gating scope on purpose and this assertion keeps that scope.

**So assertion 4 cannot gate at zero on the day the script lands.** The plan
assumed it could. It is 31, the 31 are trivially fixable (rename fixture actors
to a placeholder, change the delta test's fixture paths to something that is not
a home path), and the fix is roughly 22 line edits across four files. That is a
**prerequisite commit, not a pin**: see section 5.4 for why assertion 4 must not
be allowed to take the pin route.

### 4.2 A clone under-reports identity, and the checker needs one small change

Run inside the clone as it stands, `check_no_hardcoding.sh` reports 6 identity
hits instead of 25. The reason is precise and is the same failure the script's
own header warns about in a different guise:

    git -C /workspace config --local user.name   ->  the operator's username
    git -C <clone>    config --local user.name   ->  (empty)

The git identity is set **repo-locally** in this checkout, and `git clone` does
not copy `.git/config`. So a needle list derived inside the clone is shorter
than the one derived on the host, the base is undercounted, and 19 real hits
vanish. That is "the needle list shrinking underneath the scan", which the
script already avoids for delta mode by computing needles once and reusing them
on both sides.

The fix is a `--tree <dir>` option: derive the needles from `$WORKSHOP` and the
machine as it does now, scan `<dir>`. Six lines, and it makes the fresh-clone
assertion and the delta assertion share one rule about where needles come from.
Without it, assertion 4 in a clone is quietly weaker than assertion 4 in the
working tree, which is the failure shape this whole document exists to prevent.

**Measured cost: 0.31s.**

## 5. The pinned expected-failure mechanism

This is the subtle part. A pinned expected failure has to be a load-bearing
admission of failure, not a mute button. Five properties, and each one is
enforced by something rather than asked for.

### 5.1 It is stored in `scripts/fresh_clone_baseline.json`

```json
{
  "_comment": "Expected failures for scripts/check_fresh_clone.sh. An entry here means the assertion DOES NOT HOLD and is known not to hold. It is never a pass. Only assertions 2 and 3 may appear: the script rejects any other key. Adding an entry grows this dict, which check_ratchets_not_loosened.py reads as a loosened ratchet and rejects inside a review.",
  "expected_failures": {
    "build_with_zero_apps": {
      "pinned": "2026-08-24",
      "because": "The frontend registry names app pages with literal module specifiers and the backend imports app services from platform routes, so a tree with the app source removed does not compile and does not import.",
      "blocked_on": "docs/platform-redesign/app-separation-and-github-2026-08-20.md section 2, the app loader (import maps + dynamic import of a built ESM bundle). Stage 5 of the handoff.",
      "signature": "TS2307|cannot import name 'location' from 'services'"
    },
    "shell_boots_with_empty_catalog": {
      "pinned": "2026-08-24",
      "because": "Unreachable while build_with_zero_apps fails: with the app source removed there is no bundle and no importable backend to boot. The boot path itself is proven working against a tree WITH app source and zero apps installed.",
      "blocked_on": "build_with_zero_apps",
      "signature": "prerequisite assertion 2 did not hold"
    }
  }
}
```

The filename is chosen so that `check_ratchets_not_loosened.py` picks it up: it
globs `scripts/*baseline*.json`, and `_size()` of a dict is its length. Adding a
third pin grows `expected_failures` from 2 to 3, which that gate reports as
`fresh_clone_baseline.json: expected_failures 2 -> 3` and rejects inside a
review. Removing a pin shrinks it, which is allowed. This costs zero new code
and reuses the mechanism that already caught a worker re-pinning five metrics to
pass its own task on 2026-08-22.

### 5.2 A pin can only exist for assertions 2 and 3

The script carries the set in code, not in data:

```python
PINNABLE = frozenset({"build_with_zero_apps", "shell_boots_with_empty_catalog"})
```

A key outside that set exits **2** with
`fresh_clone: assertion 'no_app_source' is not pinnable; it must hold or fail`.
Two reasons this lives in code. It shows up in a diff, where a reviewer and the
existing gates can see it, instead of in a JSON file that reads as
configuration. And a worker that wants to pin assertion 1 or 4 has to edit the
checker to do it, which is a categorically different and much more visible act
than adding a line to a data file.

Assertions 1 and 4 do not need pinning and must not have it. Assertion 1 is
already a ratchet with a pinned number, an upward-move refusal and a recorded
`raised` reason. Assertion 4 is absolute at zero once its 31 fixture hits are
cleaned up, and an absolute check with an escape hatch is not absolute.

### 5.3 A pin never prints as a pass

Three states, three words, and `PASS` is one of them:

    PASS      the assertion holds
    FAIL      the assertion does not hold and nobody said so in advance
    NOT YET   the assertion does not hold and it is pinned, with a reason

The summary line counts them separately and never adds `NOT YET` to `PASS`. The
string `app separation is done` is produced by exactly one code path: all four
`PASS` and `expected_failures` empty. It is unreachable while any pin exists,
which is what makes the pin file, rather than a document, the thing that decides
whether the claim may be made.

### 5.4 A pin that starts passing is a failure

If a pinned assertion PASSES, the script exits 1 with `PIN STALE`. The pin is a
statement about the world; if the world moved, the statement is wrong and the
correct response is to delete the pin and start gating, not to leave a green
run reporting `NOT YET`. This is the property that stops a pin from outliving
the problem it describes, and it is why the full run re-executes pinned
assertions rather than skipping them.

### 5.5 A pin covers one failure, not any failure

Each pin carries a `signature` regex, matched against the assertion's captured
output. A pinned assertion that fails for a reason the signature does not match
reports `PIN MISMATCH` and exits 1, printing both the recorded signature and
what actually happened. A pin says "this specific thing is broken and we know
why"; without the signature it says "this area may fail for any reason", which
is a mute button.

### 5.6 What the pins imply about `--since` mode

Assertions 2 and 3 are binary, so a delta needs a base state to compare against.
The obvious implementation is a second clone at the base ref, which costs a
second full build. It is not needed: **the pin file IS the recorded base
state.** It is committed, it can only be grown by a visible edit that
`check_ratchets_not_loosened.py` rejects, and it already records "this did not
hold as of this date". So `--since` needs no second extraction for 2 and 3, and
`scripts/_ratchet_delta.py` is used only where it already is, by the two
delegate checkers. No fifth extraction mechanism is introduced.

## 6. Clone, workdir, and what `HEAD` means

**Clone by path, never by remote.** `git clone --no-hardlinks <repo> <workdir>/clone`.
This installation has an `origin` pointing at a GitHub URL and must not push;
the check never fetches, never pushes, and never reads the network for git.
`--no-hardlinks` costs 0.09s against 0.23s and buys the guarantee that the clone
shares no object file with the live repository, so a repack or a `gc` on either
side cannot surprise the other. `--shared` and `--reference` are forbidden for
the same reason.

**Nothing gitignored is needed.** Verified end to end: no submodules, no LFS, no
`.gitattributes`, and a bare clone installs, builds and imports. `packages/ui/dist`
is not tracked and is produced by `build:ui`. The `packages/ui/node_modules`
symlink noted in `.gitignore` is not needed either; the root workspace install
hoists everything to `node_modules/` at the top (measured: 236 entries, 317 MB,
and neither `frontend/node_modules` nor `packages/ui/node_modules` exists).

**`--rev` defaults to `HEAD`, and uncommitted work is not measured.** The clone
holds committed content only. That matches what the sibling checks already
assume: `check_no_hardcoding.sh`'s header states it scans tracked files on both
sides because "a worker commits its work before review (CLAUDE.md), so the gate
still sees it". The same sentence applies here and the script says so when it
starts, printing the ref and short sha it cloned.

**Workdir defaults to disk, not to `/tmp`.** Peak usage measured at **1.1 GB**
(clone 371 MB, a second tree with app source removed 358 MB, venv 340 MB, pgdata
43 MB). `/tmp` on this host is a 32 GB tmpfs, which is RAM, and this is a box
where a turn has already been killed at a 6 GB ceiling. So the default is
`mktemp -d -p "$WORKSHOP/tmp"`, which CLAUDE.md designates for ephemeral output
and `.gitignore` covers with `**/tmp/`. `--workdir DIR` overrides it and
`--keep` retains the tree and prints its path.

**The second tree can go away.** The 358 MB copy exists in the prototype only
because assertion 3 was measured against a tree WITH app source while assertion
2 was measured against one without. Once assertion 2 passes, there is one tree:
delete the app source, build it, boot that. Peak drops to about 750 MB and the
18.9s second build disappears.

## 7. Measured total, and the split

**Full run, all four assertions, end to end: 47.0 seconds.** One prototype run,
warm host caches, timings cumulative from process start:

    clone                    0.09s
    A1 app-source scan       3.41s
    A4 identity scan         3.72s
    npm ci                   5.44s
    venv + pip               14.35s
    list app-source files    14.39s
    A2 build, apps removed   23.03s   (fails, as pinned)
    full build               41.92s   (only needed while A2 fails)
    postgres up              42.27s
    alembic upgrade head     42.68s
    db bootstrap             42.84s
    backend answering        44.90s
    first user + backfill    46.89s
    TOTAL                    47.04s

47 seconds on demand is fine. 47 seconds on every review is not: a gate that
takes a minute gets disabled by the first person it annoys, and this repository
has the receipts for that failure mode (`check_pluggable_apps.sh` was not
disabled, it was worse, it was believed). So the run is split, and the split
falls out of the pins rather than being a policy someone has to remember.

**Per diff, in `reviewer.sh`: assertion 2 only.**

- Assertions 1 and 4 are NOT re-run here. `check_no_app_names.py --since` and
  `check_no_hardcoding.sh --since` are already unconditional in `reviewer.sh`
  and measure the same tracked files. Paying 3.7s to measure them twice buys
  nothing, and the script says so in its output rather than silently skipping.
- Assertion 3 is NOT run here. Its marginal cost is the venv plus the cluster
  plus the boot, measured at 13.9s to 22.4s depending on pip cache, for a
  property that cannot regress without assertion 2 regressing first.
- Assertion 2 costs `clone + npm ci + build`, measured at **10.5s** while it
  fails fast at `tsc`, rising to about **20.7s** once it compiles.
- **While assertion 2 is pinned, `--since` mode costs 0.1s**: it clones, reads
  the pin, prints `NOT YET`, and exits. The cost only appears on the day the pin
  is deleted, which is the day the check starts being able to catch something.
  That is the right shape: the gate gets expensive exactly when it becomes
  useful.

**Full run: on demand, and on the schedule that already exists.** `bash
scripts/check_fresh_clone.sh` with no arguments. It is the command anyone runs
before claiming app separation is done, and it belongs in whatever daily sweep
already runs (`scripts/daily_todo_digest.sh` is the obvious host). It must not
be wired into `scripts/apply.sh`, which is documented as safe to run at any time
from any agent concurrently, and a 47-second 1.1 GB check is neither cheap nor
concurrency-neutral.

### 7.1 `reviewer.sh` wiring

The header of `reviewer.sh` documents the pattern for adding a delta-capable
checker, and this follows it exactly, including the `.sh` runner branch that
`_delta_check` already handles:

```bash
# 4.6c-x The fresh-clone assertion: what a stranger's install must be true of.
# UNCONDITIONAL, like the two whole-tree checks above it, and for the same
# reason: the gate this one replaces (check_pluggable_apps.sh) was scoped to
# five files and reported "clean" for five weeks while the violation grew.
#
# In --since mode this runs assertion 2 ONLY. Assertions 1 and 4 are already
# unconditional above (check_no_app_names.py, check_no_hardcoding.sh) and
# assertion 3 costs a venv and a Postgres cluster it cannot regress without 2
# regressing first. While assertion 2 is pinned as an expected failure this
# costs 0.1s; when the pin is deleted it costs ~20s and starts gating.
# See docs/platform-redesign/fresh-clone-assertion-design-2026-08-24.md section 7.
FRESH_CLONE_CHECK="skipped"
_delta_check FRESH_CLONE_CHECK "check_fresh_clone.sh" "fresh_clone_baseline.json"
```

and in the JSON payload, beside its siblings:

```python
"fresh_clone": os.environ.get("FRESH_CLONE_CHECK", "skipped"),
```

`_delta_check` sets `ok` or `fail` from the exit code and adds
`fresh_clone_check_unavailable` to `AMBIGUOUS` when either file is missing,
which is the behaviour the strict-latitude path already knows how to read.

Two exit codes matter to that wiring. Exit 2 (a pin for an unpinnable
assertion, or a ref that will not resolve) reads as `fail`, which is correct:
neither is a state a review should pass through. Exit 1 covers a genuine
assertion failure, a stale pin, and a pin mismatch, all three of which are
things the worker must act on.

## 8. What the output looks like

Today, full run, with assertion 4's prerequisite commit not yet landed:

```
fresh-clone: cloning /home/<user>/workshop at HEAD (9ec46f88) -> /home/<user>/workshop/tmp/fresh-clone.Xq7pLd
fresh-clone: 1562 tracked files, 49 MB, no submodules, no LFS, no export-ignore
fresh-clone: committed content only; uncommitted work in the source tree is not measured

  [1] NO APP SOURCE ................................. FAIL   102 tracked app-source files
      pinned at 102 in scripts/no_app_names_baseline.json, and it may only go down.
      frontend/src/pages/apps  62      backend/{routes,services}  40
      birds 48  commute 21  finance 9  notes-app 9  budget 7  location 5  health 3
      note content_files was RAISED 95 -> 102: stage 4 moved four apps' business logic
      out of platform modules into app-owned files. imports 24 -> 20 in the same change.

  [2] BUILD WITH ZERO APPS INSTALLED ................ NOT YET   pinned expected failure
      pinned 2026-08-24. THIS IS NOT A PASS.
      because:    the frontend registry names app pages with literal module specifiers
                  and platform routes import app services, so a tree with the app
                  source removed does not compile and does not import.
      blocked on: app-separation-and-github-2026-08-20.md section 2 (the app loader).
      observed:   102 app-source files removed, then
                  frontend  tsc -b, 18x TS2307 in frontend/src/apps/registry.tsx
                  backend   ImportError: cannot import name 'location' from 'services'
                            routes/owntracks.py -> services/owntracks.py -> services.location
      signature matches the pin. This assertion is failing on purpose and on record.

  [3] SHELL BOOTS WITH AN EMPTY CATALOG ............. NOT YET   pinned expected failure
      pinned 2026-08-24. THIS IS NOT A PASS.
      because:    unreachable while [2] fails; there is nothing to boot.
      blocked on: assertion 2.
      proven separately, against a tree WITH app source and zero apps installed:
                  import main            333 routes
                  GET /healthz           200 in 2.1s
                  GET /apps              {"apps": []}
                  GET /agents            []
                  GET /chat/threads      []
                  mounted app routers    0

  [4] NO OPERATOR IDENTITY .......................... FAIL   31 hits, must be 0
      12 identity needles asked of this machine, scanned against the clone.
      homes       6   backend/tests/test_check_ratchet_delta.py
      identity   25   backend/tests/test_control_verbs.py 15
                      backend/tests/test_control_routes.py 4
                      frontend/src/lib/holders.test.ts 3
                      3 more from ~/.config/workshop/instance.env values
      tailnet     0
      mail        0
      All 31 are test fixtures. This assertion is NOT pinnable: fix the fixtures.

fresh-clone: 0 of 4 assertions hold. 2 pinned as expected failures, 2 failing.
fresh-clone: APP SEPARATION IS NOT DONE.
fresh-clone: workdir kept at /home/<user>/workshop/tmp/fresh-clone.Xq7pLd (--keep was implied by failure)
exit 1
```

After the fixture cleanup, which is the state the script is designed to land in:

```
  [1] NO APP SOURCE ................................. FAIL   102 tracked app-source files
  [2] BUILD WITH ZERO APPS INSTALLED ................ NOT YET   pinned expected failure
  [3] SHELL BOOTS WITH AN EMPTY CATALOG ............. NOT YET   pinned expected failure
  [4] NO OPERATOR IDENTITY .......................... PASS   0 hits, 12 needles

fresh-clone: 1 of 4 assertions hold. 2 pinned as expected failures, 1 failing.
fresh-clone: APP SEPARATION IS NOT DONE.
exit 1
```

Note that assertion 1 at 102 is a `FAIL` in this display and an `ok` to
`reviewer.sh`, and that is deliberate. The ratchet's contract is "did this run
make it worse", which is what gates a diff. The fresh-clone display's contract
is "is a stranger's clone free of this operator's apps", and 102 is not free.
The two are different questions and conflating them is mechanism M4 from the
why-it-recurs document, where a number that legitimately went up read as
regression. The display prints both: the absolute state, and the pinned figure
it is judged against.

The only output that permits the claim:

```
fresh-clone: 4 of 4 assertions hold. 0 pinned.
fresh-clone: APP SEPARATION IS DONE. A clone of 9ec46f88 contains no app source and
             no operator identity, builds with zero apps installed, and boots with an
             empty catalog.
exit 0
```

## 9. Things that make an assertion impractical as stated

Collected so a later reader does not have to rediscover them.

1. **"The build succeeds with zero apps installed" is vacuous unless the app
   source is deleted first.** A clone today has the app source, so it builds.
   Section 2.
2. **Assertion 1 reaching zero does not imply assertion 2 passes.** The backend
   break runs through `routes/owntracks.py`, which the classifier calls platform
   code and which is therefore not among the 102. Section 2.3.
3. **Assertion 1 cannot reach zero at all until the classifier stops counting
   `backend/routes/notes_all.py`**, which its own import matcher already exempts
   as platform code. Re-pins DOWN, 102 to 101. Section 1.1.
4. **Assertion 3 cannot mean "nav, chat and settings render".** No browser
   binary is installed and installing one would dominate the check. The reduced
   statement in section 3 is what is assertable and it is worth roughly as much.
5. **Assertion 3 requires PostgreSQL**, because `GET /apps` is served from it
   with no file fallback, and Docker is not reachable from an agent's sandbox on
   this host. It works because PostgreSQL 15 server binaries happen to be
   installed. If that stops being true the assertion degrades to the no-database
   subset (import, `/healthz`, `/agents`, `/chat/threads`, zero mounted app
   routers), all of which were measured working with no database at all.
6. **Assertion 4 is at 31, not 0, and a clone reports 6 unless the checker
   learns `--tree`**, because this checkout sets its git identity repo-locally
   and `git clone` does not copy `.git/config`. Sections 4.1 and 4.2.
7. **`db/bootstrap.py`'s documented order does not work on an empty cluster**,
   and `db/backfill.py --apply` refuses without `--force`. Both are correct for
   the live instance. Section 3.2 records the order that does work.
8. **There is no switch that disables the backend's background loops**, and
   there should not be one. Full environment isolation is what makes assertion 3
   safe, and a "no background work" flag is precisely the kind of thing a future
   agent would reach for to make a gate green. Section 3.1.

## 10. Order of work

1. `check_no_hardcoding.sh --tree <dir>`: derive needles from the host, scan a
   given tree. Six lines. Without it assertion 4 is silently weaker in a clone
   than in the working tree.
2. The fixture cleanup: 31 identity references in four test files. Turns
   assertion 4 from a failing check into a gating one on the day the script
   lands.
3. `check_no_app_names.py`: teach the `content` patterns the `_PLATFORM_MODULES`
   exemption its import matcher already has. Re-pins 102 to 101, downward, so no
   `--because` is needed.
4. `scripts/check_fresh_clone.sh` and `scripts/fresh_clone_baseline.json` with
   assertions 2 and 3 pinned, plus the one-line `_delta_check` wiring in
   `reviewer.sh`.
5. Then the loader (stage 5), which is what deletes the first pin, and extraction
   (stage 7), which is what takes assertion 1 to zero.

Steps 1 to 4 are a day's work and they establish the finish line. Nothing in
them removes a single app file, and that is the point: the finish line has to
exist before the race can be called.
