# In-app updates and migrations for downstream Workshop instances

Status: proposal for operator decision · Date: 2026-07-12 · Branch: `simonsays-local`

Companion to [`workshop-self-hosting-and-panel.md`](workshop-self-hosting-and-panel.md)
(the supervisor that restarts Workshop is the same thing that must roll it
back) and grounded in [`simonsays-local-instance.md`](../simonsays-local-instance.md)
(what a permanent downstream fork actually carries) and
[`apps-are-pluggable.md`](../apps-are-pluggable.md) (the platform/app boundary
this must not blur).

## 0. Problem statement

This instance runs `simonsays-local`: a permanent fork of
`github.com/BenMiriello/Workshop` carrying ~13 local-only commits that must
never go upstream and must survive every update — PIN auth, tailnet origin
config, the app removals, the `.runtime` data relocations. "Update" therefore
means: **integrate a new upstream version into a branch that has its own
permanent patches**, migrate every local store the new code expects (auth
SQLite, `.runtime/**` JSON/JSONL, `apps.json`, env/config), restart into it,
and be provably able to abort, roll back, and recover from any interruption —
including power loss mid-migration and a rollback that itself fails. Triggered
and observed from Settings. Mobile and PWA clients are downstream consumers of
the same server and add a version-skew problem, not a second update engine.

The invariant everything below serves: **the instance always converges to a
known-good version.** Worst case is "still on the old version" or "a
maintenance page that says exactly what state it's in" — never a dead port,
never live code running against a half-migrated store.

## 1. Update model for a divergent fork

Three candidate models, compared honestly:

**(a) Merge upstream into the local branch, in place (git merge).**
One operation with a well-defined abort (`git merge --abort`), never rewrites
the local commits (their SHAs, and anything pinned to them, stay stable), and
each conflict is resolved exactly once — the resolution is recorded in the
merge commit, and `git rerere` replays it if the same hunks ever conflict
again. Local divergence stays enumerable: `git log simonsays-local ^origin/main`
still lists exactly the local-only commits after any number of merges.
Downside: merge history is uglier than a clean patch series, and a naive
implementation does the merge in the *live* checkout.

**(b) Release artifacts + patch-replay of local commits on top.**
Upstream ships a built artifact or bundle per release; the updater re-applies
the local patch series onto it. This is rebase wearing a different hat: every
update re-litigates every patch against the new base, rewrites all local SHAs
(breaking the deployed ref, `git log ^upstream` bookkeeping, and anything that
recorded a local commit), and a failure midway through replaying patch 7 of 13
leaves a half-built series — the most "stuck-prone" state git offers. Artifact
distribution also discards the merge base unless the artifact is a full git
bundle anyway, at which point it is (a) with extra steps.

**(c) Maintained three-way patch set (quilt/stgit style), re-applied per update.**
Same mechanics and same failure modes as (b) — its one real advantage, a
clean, reviewable list of "what we carry," is already provided for free by
`git log simonsays-local ^origin/main`. Not worth adopting a patch-queue tool
for.

**Decision: (a), merge — but never in the live checkout.** All merging happens
in a dedicated **staging worktree** (`.runtime/update/worktree`, a
`git worktree` of this repo). The live tree is untouched until the merge is
complete, conflict-free (or conflict-resolved), built, and snapshotted. If the
merge conflicts, the *update* pauses — the *instance* doesn't even notice.

**Conflict surfacing.** When upstream touches something a local patch also
touches (the canonical case: upstream refactors `auth_store.py` and the PIN
patch conflicts), the updater:

1. Marks state `needs-attention` (see §4) with the conflicting paths.
2. For each conflicting file, attributes it to local commits via
   `git log simonsays-local ^origin/main -- <file>` so Settings can say
   "conflicts with your local patch: *PIN login* (`9a6f59c`)" — not just a
   filename.
3. Offers two actions in Settings: **Abort** (delete the staging worktree,
   back to `idle`, nothing happened) or **Resolve** — open a builder chat
   session scoped to the staging worktree, where the operator (or an agent,
   with the operator reviewing the diff) resolves the conflict and the update
   resumes from `staged`. Resolution is committed as part of the merge commit;
   `rerere` remembers it.

There is no automated "pick theirs/ours" — a wrong auto-resolution of an auth
file is strictly worse than pausing.

**Role of the git-bundle registry** (`backend/services/registry.py`, the
Marketplace substrate). Two uses, both optional in phase 1:

- *Transport:* an instance without direct GitHub egress can consume upstream
  releases as git bundles (a bundle carries real history, so the three-way
  merge base survives — this is why bundles work where tarballs don't). The
  registry's catalog shape (`history: [{version, bundle_path, ...}]`) is
  already the right release-feed record; the platform repo becomes one more
  publishable item, the "self" entry.
- *Snapshot format:* `git bundle create` of the pre-update ref is a compact,
  self-verifying belt-and-suspenders copy of "the code we can go back to,"
  stored beside the data snapshot (§4), immune to a later aggressive
  `git gc`/pruned reflog.

Apps installed from the Marketplace keep their own per-app versioning (see
[`app-versioning-rollback.md`](app-versioning-rollback.md)); this proposal is
about the platform itself. The engines share the bundle format, not state.

## 2. Version detection and the versioning scheme

**Update unit = a tagged upstream release, never "whatever `origin/main` points
at."** `main` is clawd-a's working branch on another machine; pulling arbitrary
tips into an unattended updater is how forks get stuck on someone's
mid-refactor commit. This requires a small upstream discipline (an ask, listed
in §9): tag releases and commit a manifest.

**Release manifest** — `workshop.release.json` at the repo root, authored per
release, read at the tag:

```jsonc
{
  "version": "1.4.0",
  "released_at": "2026-07-20",
  "changelog": ["Auth: session table gains device binding", "..."],
  "migration_head": 12,        // highest upstream migration id at this release
  "min_client": "1.3.0",       // oldest SPA/shell allowed to talk to this server (§5)
  "logout_required": false,    // true only if the session schema breaks (§6)
  "notes": "anything the operator should read before updating"
}
```

**Detection.** A daily background task plus a "Check now" button runs
`git fetch origin --tags --prune` (or fetches a release bundle), finds the
highest release tag reachable from `origin/main`, and compares it with the
**merge base**: `git merge-base simonsays-local origin/main`, described against
tags. Fetch-and-compare beats "poll the ref" because it distinguishes *new
release* from *new commits*, and beats "poll a hosted feed" because there is no
hosted feed to run — the manifest travels inside the repo, versioned with it.
Result is cached in `.runtime/update/available.json` for Settings to render
without shelling out per page load.

**What "version" means for a rolling fork.** The instance's version is
two-part: the upstream base plus the local delta.

```
v1.4.0+local.13.188dc6b
  │           │   └─ short SHA of the local branch head
  │           └──── count of local-only commits: git rev-list --count HEAD ^v1.4.0
  └─────────────── highest upstream release tag contained in the merge base
```

This string is computed at update time, written to
`.runtime/update/current.json`, and exposed at `GET /api/meta/version`. It is
honest about both axes: "which upstream am I based on" (what updates compare
against) and "how far have I locally diverged" (what conflicts are made of).
Semver semantics live entirely in the upstream tag; the local suffix is
informational, never ordered against other instances.

**Channels.** Default channel `release` (tags only). An explicit `edge` channel
(track `origin/main` tip) exists for the clawd-a-adjacent developer case but is
never the default and Settings labels it as unsupported-for-rollback-guarantees
beyond the snapshot.

## 3. The migration runner

Stores that migrate: the auth/runtime SQLite (`.runtime/auth.db` on this
instance), `.runtime/**` JSON/JSONL (registries, app-deploys, drafts, memory,
letta-config), `apps.json`/manifest seed, and env/config files
(`~/.config/workshop/secrets.env`, per-service env).

**Layout and discovery.**

```
backend/migrations/
  __init__.py            # HEAD = highest id; loader; runner entrypoint
  0001_sessions_device.py
  0012_runtime_projects_v2.py
  local/                 # this fork's own migrations, separate sequence
    L0001_pin_columns.py
```

Each migration is a module with:

```python
id = 12                      # ordinal within its sequence
stores = ["sqlite", "runtime"]  # what it touches (for planning + display)
reversible = True            # False is allowed and must be declared

def up(ctx): ...
def down(ctx): ...           # required iff reversible
```

`ctx` provides: an open SQLite connection (the runner wraps each migration's
SQLite work in one transaction), the `.runtime` root, the apps-manifest path,
and an env-file editor helper. Discovery is `sorted(glob)` by id; gaps are an
error; two files with one id are an error; a checksum of each file is recorded
when applied and verified on later runs (a silently edited historical
migration is a hard error, not a shrug).

**Two sequences, one ledger.** Upstream migrations (`NNNN_`) and local-fork
migrations (`LNNNN_`) are separate monotonically increasing sequences — local
patches like PIN auth need schema changes too, and sharing one number space
with upstream guarantees collisions at every merge. Both record into one
ledger, a `schema_migrations` table in the auth SQLite (the only transactional
store, so it is the authority): `(sequence, id, name, checksum, applied_at)`.
A human-readable mirror is written to `.runtime/state/migration-state.json`
after every change, but the table is the truth.

**Idempotency rules by store.**

- *SQLite:* one transaction per migration; the ledger row commits in the same
  transaction as the schema change. A migration therefore cannot be
  half-applied *and* recorded; a crash between migrations leaves a clean
  prefix.
- *`.runtime` JSON:* not transactional, so every JSON migration must be
  **shape-idempotent**: detect the target shape (a `_v` field in each
  document, added by migration 0001 of that store, or structural detection for
  legacy files) and no-op if already there; write via temp-file +
  `os.replace()` so a crash leaves either the old file or the new file, never
  a torn one. Re-running any JSON migration is always safe.
- *JSONL/event logs:* append-only stores migrate by writing a new file and
  renaming, or by versioning the *reader* instead (prefer the latter; rewriting
  history logs is usually not worth it and is declared `reversible = False`).
- *env/config:* migrations may add keys with defaults and rename keys; they
  never invent secret values. A migration that needs a value it cannot default
  (new required API key) declares it in the release manifest `notes` and the
  runner surfaces it as a pre-update requirement rather than failing midway.

**Binding migrations to code — neither direction may run mismatched.**

- `backend/migrations/__init__.py` defines `HEAD` (and `LOCAL_HEAD`). These are
  facts about the checked-out code.
- At boot, before serving anything, the backend compares the ledger with
  `HEAD`/`LOCAL_HEAD`. Ledger behind code → the backend does **not**
  self-migrate silently (that would bypass the snapshot discipline of §4); it
  serves only `/healthz`, reporting `{"status": "migrations_pending"}`, and
  everything else 503s. Ledger *ahead* of code (we rolled code back past a
  migration) → same refusal, `{"status": "schema_ahead"}` — this is the state
  the down-migration path in §4 exists to clear.
- The updater always runs migrations while the backend is stopped, then starts
  it. The boot check is the safety net for every path that isn't the updater
  (manual git surgery, restored disks), not the primary mechanism.

Upstream authors `up()`/`down()` with its features; this fork adds `L*`
migrations alongside its local patches. The one honest gap: nothing forces
upstream to write migrations at all. The fork's defense is the boot check plus
release-manifest `migration_head` — if code changes shape without a migration,
it fails loudly at health-check time and auto-rolls back, rather than
corrupting anything.

## 4. Atomicity, abort, rollback, recovery — the state machine

**Actors.** The backend must not update itself — it dies mid-operation by
design (restart). The updater is `scripts/workshop_update.py`: standalone,
stdlib-only (it must run when the venv is broken), run as a systemd `--user`
oneshot unit (`workshop-update.service`) that the backend triggers via
`systemctl --user start`. Before doing anything irreversible the updater
**copies itself and its state module to `.runtime/update/runner/`** and
re-executes from there, so the mid-update `git checkout` can never yank the
running updater's own source out from under it. A second oneshot,
`workshop-update-recover.service`, is ordered `Before=workshop-backend.service`
at every boot. This is the concrete coupling to the self-hosting proposal:
systemd is the bootloader (its Phase 0 native units are a **prerequisite**),
Workshop decides; panel is not in the loop.

**On-disk state.** `.runtime/update/state.json`, written atomically
(temp + `os.replace` + fsync of file and directory), holding
`{state, step, target_version, previous_version, started_at, error, log_tail}`.
An `flock` on `.runtime/update/lock` serializes updates. The state machine:

```
idle
 └─ checking ──────────────► idle (no new release)
     └─ staging             merge in worktree, build frontend, build venv
         ├─ needs-attention (merge conflict; live untouched; Abort/Resolve)
         └─ staged          everything buildable is built; still abortable
             └─ backing-up  snapshot set written and verified
                 └─ applying        ◄── point of no return begins
                     └─ migrating
                         └─ restarting
                             └─ health-check
                                 ├─ committed ─► idle   (new known-good)
                                 └─ rolling-back
                                     ├─ rolled-back ─► idle (old known-good)
                                     └─ broken ─► maintenance responder
```

Every state up to and including `staged` is **abortable with zero effect on
the live instance** — abort deletes the worktree and staged builds. Everything
from `applying` onward is designed to be short, journaled, and recoverable.

**Staging (before the point of no return).** In the staging worktree: fetch,
merge, `npm ci && npm run build` into `frontend/dist-releases/<version>/`,
create `backend/.venv-releases/<version>/` and install `requirements.txt` into
it. Long, failure-prone work (network, compilers) all happens here, while the
old version keeps serving. The live tree's `frontend/dist` and
`backend/.venv` become **symlinks** into those release directories (a one-time
migration converts them); flipping a symlink is atomic and instantly
reversible.

**Snapshot set (`backing-up`)** — written to
`.runtime/update/snapshots/<previous_version>/`:

- `code.ref` — the exact commit of the running version, plus a
  `code.bundle` (git bundle of that ref, per §1) so even a pruned reflog can't
  lose it.
- `auth.db.bak` — via SQLite's backup API / `VACUUM INTO`, never a raw file
  copy (raw copies of a WAL-mode DB are torn by construction).
- `runtime.snapshot/` — hardlink copy (`cp -al`) of `.runtime/` excluding
  `update/`, `app-logs/`, and the letta-db volume (Letta/pgvector has its own
  lifecycle; a platform update does not migrate it — if a release ever needs
  to, that is a declared, manifest-flagged special case). Hardlinks are safe
  here because all Workshop JSON writers use write-temp-then-rename, which
  creates new inodes rather than mutating shared ones; this is now a stated
  invariant, and the one known in-place writer class (SQLite) is excluded and
  backed up properly above.
- `env/` — copies of the service env files and `apps.json`.
- `manifest.json` — inventory with sizes and checksums; the snapshot is
  verified before `applying` may begin.

**Apply → migrate → restart (the short critical section).** Steps, each
journaled (`step: N` in state.json) and idempotent so they can be re-run from
the journal after a crash:

1. `systemctl --user stop workshop-backend workshop-public`.
2. Fast-forward `simonsays-local` in the live checkout to the staged merge
   commit (objects already local; this is a ref move + checkout, sub-second).
3. Flip `frontend/dist` and `backend/.venv` symlinks to the new release dirs.
4. Run the migration runner (new venv, backend stopped) up to the new
   `HEAD`/`LOCAL_HEAD`. Per-migration transactionality per §3.
5. `systemctl --user start workshop-backend workshop-public`.

**Health gate.** The updater polls `GET /healthz` — extended to report
`{status, version, migration_head, ready}` — for up to a configurable window
(default 90s), requiring `ready` plus a real request through
`workshop-public`. Pass → `committed`: write `current.json`, keep this
snapshot as the new "previous," prune older snapshots per retention (default:
keep 2). Fail → automatic rollback.

**Automatic rollback (seconds after the update).** Stop services; restore the
git ref from `code.ref`; flip both symlinks back; restore `auth.db` from the
backup; restore `.runtime` from the hardlink snapshot (rsync from snapshot,
delete-extraneous within the covered paths); restore env files; start; poll
health. **Snapshot restore, not down-migrations**, is correct here because the
window since the snapshot is seconds and no meaningful user data was written
into the new schema. On success → `rolled-back`, Settings shows the captured
failure log.

**Manual "go back" from Settings (hours or days later).** Now data *has* been
written under the new schema, so blind snapshot restore would destroy it. This
path runs **down-migrations** from current head to the previous release's
`migration_head`, then moves the code ref back. If any migration in that range
is `reversible = False`, Settings says so up front and offers the only honest
alternative: snapshot restore *with* its data-loss window stated explicitly
("restores data as of <timestamp>"). This is the belt-and-suspenders division
of labor: **snapshots guarantee you can always get back; down-migrations make
getting back non-destructive when the migrations allow it.**

**Interruption (power loss) at any point.** On every boot,
`workshop-update-recover.service` reads `state.json` before the backend
starts:

- `idle`, `committed`, `rolled-back`, `checking`, `staging`, `staged`,
  `needs-attention` → nothing to do (staging artifacts are inert; a stale
  worktree is garbage-collected).
- `backing-up` → delete the partial snapshot, revert state to `staged`.
- `applying`/`migrating`/`restarting`/`health-check` → the update never passed
  its health gate, so **roll back by default** (converge to known-good) using
  the journal: every rollback step is idempotent, so a crash *during recovery*
  just repeats it next boot. Roll-*forward* (resume the update) is
  deliberately not attempted automatically — the operator can simply re-run
  the update once the old version is confirmed up.
- `rolling-back` → resume the rollback from its journal step.

**Rollback itself fails (the last tier).** If recovery cannot bring the old
version to health (disk full, corrupted venv, bad snapshot), it does not loop
forever: after N attempts it enters `broken` and starts the **maintenance
responder** — a ~100-line stdlib HTTP server on the backend port serving a
static page with the state machine contents, the last error, and the paths to
the snapshot set. "Never stuck" does not mean "never broken"; it means the
box never presents a dead port or a silently wrong version — the worst state
is self-describing and hand-recoverable, with everything needed for that
recovery (bundle, DB backup, runtime snapshot, journal) sitting in one
directory whose location the page prints.

## 5. Backend vs frontend vs mobile

**Local server build** is the whole of §4: fetch/merge → deps → frontend
build → snapshot → migrate → restart under systemd → health gate → commit or
roll back.

**Browser SPA.** Vite-hashed assets plus no-cache `index.html` mean new loads
get the new frontend atomically with the symlink flip. Open tabs: the SPA
polls `GET /api/meta/version` (piggybacked on existing polling) and, on
mismatch with its built-in version, shows a non-blocking "Workshop updated —
reload" toast. During the restart window the SPA's existing
connection-loss handling shows a reconnecting state rather than an error wall
(§6).

**Mobile (Expo shell).** Today's iOS app is a WebView around the server
(`scripts/mobile-deploy.sh`): backend + SPA updates arrive on next app open
with *no mobile release at all* — the server update system is the mobile
update system for everything except the native shell. The shell itself
(native deps, `app.json`, plugins) stays a manual Xcode build; free-tier
signing makes any OTA promise there dishonest. What the platform *does* own:

- The shell injects `X-Workshop-Shell: <shell-version>` on its requests.
- The release manifest's `min_client` gates skew: if the shell (or a cached
  PWA bundle) is older than `min_client`, `/api/meta/version` says so and the
  client shows a blocking "update the app / hard-refresh" screen instead of
  half-working against an API it predates.
- The compatibility contract that makes `min_client` rarely bite: **API
  changes are backward-tolerant for one release** (additive first, remove one
  release later). This is a documented upstream discipline, not code.
- If the shell ever grows real native-side JS logic, `expo-updates` OTA
  becomes the shell's equivalent of the symlink flip — same manifest, same
  `min_client` gate, staged rollout optional. Explicitly future; nothing in
  §4 changes.

**PWA / service worker.** The service worker is versioned by build hash;
activation of a new worker waits for user consent via the same "updated —
reload" toast (`skipWaiting` on tap), so a mid-session cache swap can't tear
the app. The worker never caches `/api/**` responses that carry version
semantics.

## 6. Settings UX

Lives in the existing System section of `Settings.tsx` as an **Updates** card,
built from the Design System (`SectionLabel` + `Card`, `src/ui` primitives,
tokens, lucide icons, all four list states, `ConfirmDialog` where warranted —
per `frontend/CLAUDE.md`).

- **Idle:** current version (`v1.4.0+local.13.188dc6b`, with the local-delta
  part explained on hover), channel, last-checked timestamp, "Check now."
- **Update available:** version, release date, changelog list and manifest
  `notes` from `workshop.release.json`, and any pre-update requirements
  (missing required config from §3). "Update" opens a `ConfirmDialog` — not
  because it is destructive (it is designed not to be) but because it
  restarts the instance; the dialog states expected downtime (seconds), that
  a snapshot is taken first, and whether `logout_required` is set.
- **In progress:** a step list mirroring the §4 states (staging → backup →
  apply → migrate → restart → health check), streamed from `state.json` via
  the backend until the restart, then the page intentionally loses its server:
  the card switches to an expected-disconnect state ("Restarting into
  v1.4.0…") and polls `/healthz` directly until the new version answers, then
  confirms success or renders the rollback outcome. This must not look like
  an error — the disconnect is the design.
- **Needs attention (merge conflict):** the conflicting files, each attributed
  to its local patch by name (§1), with Resolve (opens a builder chat scoped
  to the staging worktree) and Abort.
- **Roll back:** always visible while a previous snapshot exists, with the
  previous version named. `ConfirmDialog` distinguishes the two modes
  honestly: down-migration rollback ("keeps data written since the update")
  when the migration range is reversible, snapshot restore ("restores data as
  of <timestamp>") when it is not — echoing the code-rolls-back/data-does-not
  caveat from `app-versioning-rollback.md`, made explicit instead of implied.
- **Session across the update:** sessions live in the auth SQLite and the
  cookie survives a restart, so normally there is **no PIN re-prompt** —
  the reconnecting SPA resumes the session. Only a release whose manifest
  sets `logout_required` (session-schema break) forces re-login, and the
  confirm dialog said so before the operator clicked Update.

## 7. Recommended architecture (summary)

- Merge-based fork updates, staged in a worktree, targeting tagged upstream
  releases described by an in-repo `workshop.release.json`; conflicts pause
  in staging and are attributed to the local patch they touch. Bundles are
  the offline transport and the code-snapshot format.
- Version = `<upstream tag>+local.<count>.<sha>`; detection = fetch tags +
  read manifest; daily check + Check-now.
- Ordered `up`/`down` migrations in `backend/migrations/` (upstream `NNNN_`,
  fork-local `LNNNN_`), ledger in the auth SQLite, shape-idempotent JSON
  migrations, and a boot-time head check that refuses to serve mismatched
  code/schema in either direction.
- A stdlib-only updater running as a systemd oneshot (self-copied before
  apply), an atomic on-disk state machine in `.runtime/update/state.json`,
  symlinked `dist`/venv releases for atomic flip, a verified snapshot set
  (git bundle + SQLite backup API + hardlink `.runtime` copy + env), a
  health-gated commit, automatic snapshot-restore rollback, journal-driven
  boot recovery, down-migration-based *manual* rollback, and a maintenance
  responder as the never-a-dead-port floor.
- Mobile/PWA ride the server update; skew is gated by `min_client` +
  one-release API tolerance; native shell stays a manual build.

## 8. Phased build path

Prerequisite: self-hosting proposal Phase 0 (native systemd `--user` units for
backend/public; panel out of the loop) — the updater drives `systemctl`.

**Phase 1 — safe minimum, server only, CLI-driven.** `workshop_update.py`
with: release detection (fetch + manifest, tolerating manifest absence by
treating `origin/main` tip as `edge`), staging-worktree merge (abort on
conflict — no Resolve flow yet), snapshot set, symlink conversion for
`dist`/venv, apply, migration runner (ledger + `up` only + boot head-check),
health gate, automatic snapshot rollback, recovery boot unit, state machine +
lock. Settings shows read-only current/available version. Every later phase
is UX and refinement on top of this; nothing in Phase 1 is throwaway.

**Phase 2 — Settings-driven.** Update button + ConfirmDialog, live progress
with the expected-disconnect reconnect flow, manual Roll back
(snapshot-restore mode only), changelog display, conflict `needs-attention`
surfacing with Abort (Resolve-via-builder-chat may land here or in 3).

**Phase 3 — reversibility and hardening.** `down()` support + reversible-range
manual rollback, snapshot retention policy, maintenance responder, snapshot
verification, `rerere` wiring, `L*` local migration sequence (converting the
PIN-auth schema additions into `L0001` as the exemplar).

**Phase 4 — clients.** `/api/meta/version` + SPA toast, `min_client` skew
gate, PWA service-worker consent flow, shell version header. Expo OTA only
if the shell ever stops being a thin WebView.

## 9. Open decisions for the operator

1. **Upstream asks** (needs clawd-a's buy-in): tag releases, commit
   `workshop.release.json`, adopt the migrations directory, honor
   one-release API back-tolerance. Without tags, this instance runs `edge`
   with weaker guarantees — acceptable interim?
2. **Auto-check cadence and auto-update:** proposal is daily check,
   *never* auto-apply (single operator, updates restart the box's services).
   Confirm manual-apply-only.
3. **Snapshot retention:** keep 2 snapshots (previous + one older) vs more;
   `.runtime` hardlink copies are cheap but the SQLite backups and bundles
   are real bytes.
4. **Letta/pgvector exclusion:** confirmed out of update scope (own docker
   lifecycle), with manifest-flagged special-casing if a release ever must
   touch it?
5. **Conflict Resolve flow:** builder-chat-in-staging-worktree (proposed) vs
   CLI-only resolution in Phase 2/3.
6. **Edge channel exposure:** hidden config value, or a visible Settings
   toggle with a warning?
7. **`workshop-public` versioning:** it is updated in lockstep with the
   backend by the same flip; confirm no need for independent
   update/rollback of the edge process.
