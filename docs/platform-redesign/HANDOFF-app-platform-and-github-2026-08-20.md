# Handoff: finish app separation, build the loader, ship GitHub

- Date: 2026-08-20
- For: the next agent picking this up
- Read first, in this order:
  1. [`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md) — the container model. App is the entity; project is retired.
  2. [`app-separation-and-github-2026-08-20.md`](app-separation-and-github-2026-08-20.md) — the research, the decisions, and the full stage list.
  3. This file — what is done, what is next, and the traps.

**Do not re-research the loader or the GitHub auth model.** Both were decided
with sourced current research on 2026-08-20 and the reasoning is in doc 2. If
you disagree, argue with the reasoning; do not start over.

---

## 1. The requirement, in the operator's words

> "All of the actual apps and projects that a user includes are gonna be
> completely unique to them and only to them ... and would never ever in a
> million years be in the source code or in anybody else's implementation."

> "Don't ever in any way, shape, or form, at all, leave my own custom
> application specific code in the workshop code."

This is not a cleanup preference. It is the central design constraint of the
product: Workshop is a container, an app is installed content, and a clone by
another user must contain zero trace of whoever ran the instance it came from.

**It has been believed done twice and was not.** `handoff-pluggable-apps-migration.md`
says "Status: DONE on main (2026-07-12)". `check_pluggable_apps.sh` has printed
`pluggable-apps: clean` throughout while 143 tracked files of one operator's
apps sat in the tree, because it inspects five files. Assume nothing is done
that you have not measured.

**The measurement is `python3 scripts/check_no_app_names.py`.** Three counts,
all of which must reach zero. Run it before and after everything.

---

## 2. State as of 2026-08-21 (stage 4 backend landed)

```
content  102  tracked files that ARE app source      (was 143, then 95)
imports   20  platform files importing an app module (was 24)
literals 314  platform lines naming an app           (was 331, then 315)
```

**`content` went UP, on purpose, and the gate now makes you say so.** Stage 4's
job is to lift an app's logic out of a platform module into an app-owned file.
That is more app-source files in the repo (`content`) and fewer platform files
that know an app exists (`imports`, `literals`). Both moved in the same change.
All seven new files leave with their app at stage 7, which is where `content`
goes to zero.

`--write-baseline` now REFUSES to pin an increase without `--because "<why>"`,
records the reason in the baseline, and prints it on every subsequent run until
the count comes back down. A ratchet you re-pin whenever it fails is not a
ratchet, and that is exactly how `check_pluggable_apps.sh` died.

### Done

- **Stage 0, the gate.** `scripts/check_no_app_names.py`, wired
  unconditionally into `reviewer.sh` (`app_name_in_platform_source`). Reads its
  roster from the catalog + install list + a sticky baseline, so it hardcodes no
  app names and an app that leaves the catalog stays watched. Pin the baseline
  **from a committed tree**; the first pin was mid-edit and produced a phantom
  failure.
- **Stage 1, the bundle.** `BirdsLayout` moved into the app and made lazy. The
  entry stylesheet went 190,032 -> 174,139 bytes and 24 `.birds-*` rules left
  it. App-owned CSS moved to the app (`.tabs`, `.sec-h`); `.num-in` stayed and
  the comment says why.
- **Stage 2, icons are data.** `frontend/src/lib/appIcon.tsx`: a declared token,
  a url, or a two-letter monogram. No `import { Bird }` anywhere in platform
  source.
- **Stage 3, Settings is a place.** Ten routed tabs; machine-specific controls
  quarantined under "This host".
- **First real extraction.** 55 files untracked: `apps/`, `agents/apps/`,
  `backend/mcp/plaid/`, `commute/`. Still on disk, gitignored, history intact.
  Plus `infra/cron/workshop.crontab` scrubbed (an app's schedule now lives in
  `apps/<id>/*.crontab`, concatenated by `install_cron.sh`) and
  `mobile/app.json`'s App Store permission strings genericised.
- **`/services/` repaired.** Moved from loopback-gated to `require_operator`
  and off the edge blocklist. It had 404'd on the operator's origin, silently
  breaking the Apps launcher's per-app status and making Commute report its 511
  key missing whether or not it was set.
- **`GET /events/subscriptions` and `pages/Events.tsx` deleted.**

- **Stage 4, the backend seams. DONE 2026-08-21.** All six, plus the catalog and
  both cross-app edges. Detail in §2b below.

### Not done, in dependency order

**Stage 4b — the frontend half, and it is now the next job.** Promote the 11
platform-internal modules app pages reach for (`lib/usePoll` 18 uses, `lib/time`,
`components/appNav`, `components/AppSettings`, `lib/storage`,
`components/AppChatDock`) into `@workshop/ui`. `notes-app` already imports
nothing outside the kit, which is the proof this is achievable. Until this is
done, gitignoring `frontend/src/pages/apps/` breaks a fresh clone's build, which
is what holds `content` at 102.

**Stage 5 — the loader.** Import maps + dynamic `import()` of a built ESM
bundle. Decided; do not relitigate. Doc 2 §2 has the mechanism, the two fatal
traps (Vite's dev pre-bundler creating a second React; `cssCodeSplit` making the
app render unstyled in production only), and why not module federation or
iframes. Adopt declare-then-activate and named mount points.

**Stage 6 — GitHub.** Doc 2 §3. GitHub App via the App Manifest flow, device
flow for the user token, `githubkit`, Fernet-encrypted fields with the key from
systemd `LoadCredentialEncrypted`. The one fact that decides the architecture:
`POST /user/repos` is **user-token only**, so a server-to-server App cannot
create a repo in a personal account. Do not use GitPython (ten CVEs in three
weeks to 2026-08-10, all from forwarding kwargs to `git`).

**Stage 7 — extraction.** The overlay repo at `apps/`. Ratchet the gate to zero.

**Stage 8 — the per-app page column**, including per-app Settings. This is the
operator's outstanding UI complaint and it depends on 4 and the `app_id`
columns from doc 1's stage A.

---

## 2b. Stage 4, as built (2026-08-21)

Six seams, the catalog, and both cross-app edges. Every one is exercised by
`backend/tests/test_appspec_seams.py`, which plugs in a fabricated app
(`seamtest`) with a fabricated source, store type and corpus. That is the point:
each of these sites was already TOLERANT (a missing app degraded rather than
crashing), and tolerant is not extensible. The test of extensible is whether
something the platform has never heard of can plug in, so no test in that file
names a real app.

**New `AppSpec` fields**, each with an `installed_*` accessor resolved per call
so installing an app takes effect without a restart:

- `event_verifiers` — `{source: (headers, body) -> (ok, reason)}`. Replaces
  `routes/events.py`'s `VERIFIERS = {"plaid": ...}`. The platform keeps only
  `github` (a platform source), and an app CANNOT override a platform source:
  `verifier_for()` checks `PLATFORM_VERIFIERS` first, so an app cannot claim
  `github` and vouch for it. Pinned by a test.
- `event_dedup` — `{source: (summary) -> str | None}`. Replaces the
  `if source == "plaid":` branch in `services/event_dispatch.py`. An app's
  `None` is an ANSWER, not a fallthrough: falling back to the platform's content
  hash would overrule an owner that has said this payload carries no stable
  identity, and dedup two genuinely distinct events that serialize alike.
- `store_adapters` — replaces `stores._ADAPTERS`. `StoreAdapter` gained
  `claims(store)` (type alone by default) and `write(...)` (refusal by default).
- `index_collectors` / `index_collectors_default` — replaces
  `semantic_index._COLLECTORS["vault"]`, which imported the notes app's module.

**Modules moved out of platform directories, and what they were:**

- `services/app_personas.py` (559 lines, four apps' logic) is GONE. The base
  class is `services/app_persona.py` (platform); the rest is
  `services/{budget,finance,commute,location}_persona.py`. Plaid's PFC
  bucketization, a commute schedule reader and a budget plan-patch applier no
  longer sit in a platform directory.
- `NoteAdapter` served TWO substrates and picked between them with
  `if store.owner_app == "knowledge"` — the platform's generic data door
  branching on which app it was talking to. Split: `KnowledgeNoteAdapter` stays
  in `stores.py` (the knowledge scope IS the platform's own note store),
  `VaultNoteAdapter` moved to `services/notes_store_adapter.py`.
- `FinanceAdapter` moved to `services/finance_store_adapter.py`.
- The plaid verifier and dedup deriver moved to `services/finance_events.py`.

**`services/event_log.py` is new, and it is the seam `routes/birds.py` needed.**
There was no event-publish API at all: the append-to-events.jsonl sequence was
hand-copied in three places and the three had already drifted. Plaid's copy
wrote **no `signature` field**, so a fully ES256-verified webhook was
indistinguishable in the feed from an unverified one, despite that route having
just verified it. Birds' copy imported `EVENTS_LOG` and the PRIVATE
`_load_subscriptions` from `routes/events.py`, so one app's route module
depended on another route module's internals. `publish()` / `publish_and_dispatch()`
own the record shape, the credential-header strip, and the
capture-the-offset-BEFORE-the-write rule (that offset is the event's address;
a pending run seeks to it). `_load_subscriptions` had FOUR copies and now has one.

**The catalog can be extended.** `_CATALOG_FACTORIES` was a literal tuple
consumed once at import, with the factory required to be defined in
`app_registry.py` and the route module required to sit in `backend/routes/`.
There is now a second path, the standard Python one:

```
[project.entry-points."workshop.apps"]
my-app = "my_app.spec:app_spec"
```

`app_spec()` returns an `AppSpec` exactly as a built-in factory does. No new
format, no manifest parser, `pip install` is the install step. A discovered app
SHADOWS a built-in of the same id, deliberately: that is what extraction looks
like from the platform's side. A broken entry point is skipped, and one that
returns a non-`AppSpec` fails at build rather than entering the catalog and
failing at every accessor that reads a field.

**Both cross-app load-time edges are call-time now.** `routes/budget.py` and
`services/categorizer.py` imported finance at module level, so budget could not
LOAD without finance even though the catalog admits budget on the `budget` route
module alone. `services/commute/scoring.py` was the one load-time
commute -> location import in a package whose other three are already call-time
and say why. Each now degrades with the dependency named (a 503 that says budget
needs finance; a commute brief without the location block) instead of an
ImportError.

Watch out: `scoring.py` needed a module `__getattr__` to keep serving
`scoring.location_svc`, because nine existing tests reach it as
`patch.object(scoring.location_svc, "latest", ...)`. Removing a module-level
import removes the attribute, and those tests fail with `AttributeError`.

**Verification.** Full backend suite run against a detached worktree of
unmodified HEAD with the same command: **zero regressions**, and 15 previously
failing tests now pass. Seven of those 15 were `test_app_personas_declared.py`,
which had been RED ON MAIN since commit 18d984c widened `WORKSHOP_TOOLKIT`: the
test asserted `p.allowed_tools == d["tools"]` while the base class had begun
adding the toolkit on top. Repaired to assert the superset plus the toolkit.

---

## 3. Also queued, from the operator

- **Import an app by zip or folder drop.** New, low priority, third source
  alongside create-new and import-from-GitHub. Needs §3.3 below first.
- **Adopt-existing-code.** `POST /projects/` and `POST /projects/import` both
  409 on an existing directory, and there is no app<->project link field, so an
  in-tree app cannot become an app record without extraction first. **A real
  prerequisite for stages 6 and 7, and a genuine gap in the prior plan.**
- **Settings redesign, phase two.** The operator's own list: the Health panel is
  "a leftover from when this was a one person app", GPU memory and the broker
  are "specific to my particular distribution and should not be included at
  all", and Base apps, Agents and System need revisiting. Explicitly NOT to be
  taken on inside another task. Quarantined under "This host" meanwhile.
- **The ref/branch pill**, deferred pending its own branches-and-worktrees
  discussion. **The Dashboard**, deferred. **The file browser/editor**, an
  existing package, not hand-built. **Search**, deferred; `services/semantic_index.py`
  already works and the palette is a thin wrapper.
- **Sign in with GitHub** as an auth provider, plugging into the same
  integration alongside Google. Noted for whenever auth is next touched.

---

## 4. Traps

**The gate is the only honest signal.** Every previous "done" here was asserted.
`check_pluggable_apps.sh` is still wired in and still inspects five files; it is
not a substitute, and its own `APP_LOOPS` and `FORBIDDEN_TRACKED` hardcode app
names. De-hardcode or retire it during stage 7.

**Two modules hide their owner behind a generic name.**
`backend/services/environment.py` is the **birds** weather spine and hardcodes
`<rig-data>/environment.db`. `backend/services/categorizer.py` is
budget/finance only. Both read as platform code. There will be others; check
what a module actually imports, not what it is called.

**Birds' data lives inside the source tree** (`apps/birds/birdnet.db`,
`apps/birds/clips/`, via `birds_store.py`). `BIRDNET_DB` and `BIRDNET_CLIPS`
override it, so moving the code is an ops step, but it must happen in the same
change or the detection database moves with the source.

**`GET /events/recent` has exactly one caller and it has never succeeded.**
`scripts/chat_event_bridge.py` polls it as an outbound message bus, deduping on
a string-compared ISO timestamp. The bridge is not running and `discord-reply`
has never appeared in the log. It is kept because deleting a route from under a
script that calls it is a landmine. If you build a real outbound queue, retire
both together.

**`/events` must stay on the edge blocklist.** That router carries
`POST /events/{source}`, an unauthenticated webhook receiver. Blocking is
per-prefix, which is why its read routes were unreachable. Do not "fix" this by
unblocking the prefix.

**Do not stash to compare measurements.** `git stash --include-untracked`
silently reverted a `git rm --cached` mid-session here. Use a detached worktree.

**Backend tests: `test_chat_personas_are_install_gated` fails inside the agent
sandbox** because `WORKSHOP_ROOT` resolves to `~/workshop`, which is
not mounted at that path there. It passes with `WORKSHOP_APP_AGENTS_DIR` pointed
at the tree. Not a regression; do not "fix" it.

**Four DB-fixture test errors** (`test_db_chat`, `test_db_ticket_mirror`) need
`WORKSHOP_DATABASE_ADMIN_URL` and are pre-existing.

---

## 5. How to work this

Each stage is shippable and none may wire a dead control (operator's standing
rule: if it does not work, do not build it, flag it). Run
`python3 scripts/check_no_app_names.py` before and after every stage and put the
three numbers in the commit message. `npm run typecheck`, `bash scripts/check_design.sh`,
`cd frontend && npm test`, then `bash scripts/apply.sh` as the final action.

Verify UI in the built app, not only in source. Playwright's chromium is
installed at `~/.cache/ms-playwright/chromium_headless_shell-1228/`; node 22 is
at `~/.nvm/versions/node/v22.22.3/bin/node` (the system node is 18 and
Playwright refuses it). The built origin is `127.0.0.1:8002` and needs a session
cookie; mint one with `auth_store.create_session` and **revoke it afterwards**.
