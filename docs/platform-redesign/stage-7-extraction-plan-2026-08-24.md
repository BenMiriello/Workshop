# Stage 7: the operator's apps leave the platform repo

- Date: 2026-08-24
- Status: plan, not built. The counts in `scripts/no_app_names_baseline.json` and
  `scripts/check_fresh_clone.sh` are the status of this work, per convention 7.1
  of [`app-separation-why-it-recurs-2026-08-24.md`](app-separation-why-it-recurs-2026-08-24.md).
  No line in this file may ever read "done".
- Sits under: [`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md)
  section 9 stage E and section 10 (the open question this plan answers),
  [`HANDOFF-app-platform-and-github-2026-08-20.md`](HANDOFF-app-platform-and-github-2026-08-20.md)
  section 2 (the stage list),
  [`stage-4b-kit-promotions-2026-08-24.md`](stage-4b-kit-promotions-2026-08-24.md)
  (the CSS finding),
  [`stage-5-app-loader-plan-2026-08-24.md`](stage-5-app-loader-plan-2026-08-24.md)
  (the loader this depends on), and
  [`fresh-clone-assertion-design-2026-08-24.md`](fresh-clone-assertion-design-2026-08-24.md)
  (the finish line this crosses).

Everything labelled measured was run against the working tree on 2026-08-24 and
the command is given. Nothing in this document changed code.

---

## 0. Re-measurement, today

```
python3 scripts/check_no_app_names.py
  content 102   imports 20   literals 302   (baseline pins 302, so the gate PASSES on clean HEAD)
```

The `literals 305` figure in the two 2026-08-24 documents is stale by one commit:
the three `"app:budget"` fixture strings in `frontend/src/components/chat/uiCardActions.test.ts`
have already been renamed, so the gate is green and no longer rejects unrelated work.

Content split, reproduced exactly:

| app | content files | of which backend | of which frontend |
|---|---|---|---|
| birds | 48 | 4 | 44 |
| commute | 21 | 17 | 4 |
| finance | 9 | 6 | 3 |
| notes-app | 9 | 4 | 5 |
| budget | 7 | 4 | 3 |
| location | 5 | 3 | 2 |
| health | 3 | 2 | 1 |
| wearables | 0 | 0 | 0 |
| **total** | **102** | **40** | **62** |

Literal split by group, measured with `--json`:

| group | literal sites |
|---|---|
| platform and app tests | 146 |
| `frontend/src/apps/registry.tsx` | 54 |
| `backend/services/app_registry.py` | 34 |
| everything else in platform source | 57 |
| `scripts/check_no_app_names.py` (its own watch list) | 11 |
| **total** | **302** |

Also confirmed:

- `git ls-files apps/` returns 0. Nothing of the operator's app DATA is tracked.
  Only the source under `frontend/src/pages/apps/`, `backend/services/`,
  `backend/routes/` and `backend/scripts/` is.
- `git ls-files agents/` returns only `agents/_templates/`, so the app assistant
  definitions already left.
- `git ls-files backend/mcp/` returns only `workshop_query_server.py`, so the
  plaid MCP already left.
- Four catalog apps are Python modules whose routers `backend/main.py` mounts
  through `app_registry.installed_specs()`: finance, budget, commute, location,
  plus birds, health and notes-app. Seven of the eight built-in factories import
  a route module. Only `wearables` does not, because it is already external.
  Extraction is therefore not a frontend problem with a backend footnote; 40 of
  the 102 files and every mounted router are backend.

---

## 1. The order, and why

The ordering rule is one sentence: **a provider leaves after its consumers, and
the rehearsal goes first.**

Three real dependency edges exist and all three were converted to call-time by
stage 4, which means the dependency now degrades with a named message instead of
failing to import. Degrading is still a visible regression for the operator on
his own instance, so the order avoids it rather than relying on it:

- `budget` needs `finance.read` (`_budget_spec.identity.needs`), and
  `routes/budget.py` reaches finance at call time.
- `commute` needs `location.read`, and `services/commute/scoring.py` reaches
  `services.location` at call time through a module `__getattr__`.
- `birds` needs `services/environment.py`, which is birds' own weather spine
  wearing a platform name (section 3, A3 and A4).

The sequence:

| # | app | files (content + hidden) | why here |
|---|---|---|---|
| 0 | wearables | 0 + 0 | Already external. It is a catalog entry and three service probes, nothing else. It leaves with the registry work in section 5, not as an extraction. |
| 1 | **health** | 3 + 0 | The rehearsal. Smallest app in the tree, headless (`card=None`, so no launcher card and no routed page to load), one settings panel, no `provides`, no `needs`, and nothing else in the tree depends on it. If the extraction mechanism is wrong, it is wrong here for three files instead of forty-eight. |
| 2 | **notes-app** | 9 + 0 | The frontend proof. It is the app that imports nothing outside `@workshop/ui`, so it is the cleanest test of the loader carrying a real routed page. Two prerequisites are specific to it: `backend/routes/notes_all.py` is the PLATFORM knowledge feed and is miscounted as notes app source (`fresh-clone-assertion-design` section 1.1), and 92 lines of its layout CSS currently live in the platform kit. |
| 3 | **budget** | 7 + 1 | Consumer of the money pair, so it goes before its provider. Carries `services/categorizer.py` with it (section 3, A5). |
| 4 | **finance** | 9 + 4 | Provider of `finance.read`. Nothing in the tree needs it once budget has gone. Carries the three plaid modules and the plaid migration script. |
| 5 | **commute** | 21 + 0 | Consumer of location. Largest backend surface of any app (a 16-module package under `backend/services/commute/`) but a small frontend, so it exercises the backend half at scale before birds does. |
| 6 | **location** | 5 + 2 | Provider of `location.read`. Nothing needs it once commute has gone. Carries the owntracks pair, which is the coupling the gate is blind to and the one that stops `import main` today. |
| 7 | **birds** | 48 + 5 | Last, in two tickets. Forty-four frontend files, a second Vite build in the frontend root, a device-storage exemption in a platform module that must not move early, three hardcoded paths in the design gate, and a live detection database that sits inside the working tree. Everything the first six steps taught goes into this one. |

**Why health and not notes-app first.** Notes-app is the better-known "clean app"
and is the tempting rehearsal. It is not, because it is the only app whose
content count is wrong (the `notes_all` misclassification caps assertion 1 at 1)
and the only app with CSS parked in the kit. Health has neither problem and has
no routed page at all, so step 1 tests the backend mount path, the manifest, the
install list and the overlay mechanics with the frontend held constant. Step 2
then adds exactly one new variable, the loader carrying a routed page.

### 1.1 A concurrent plan reached the same first step and a different middle

`app-loader-distribution-and-versioning-2026-08-24.md` landed while this was
being written. It closes stage 5's two blockers (the dead `kit` field, and how an
out-of-repo app gets its build dependencies) and its section 5.2 proposes its own
extraction order. Read it; it supersedes stage 5 sections 6, 8 item 1 and the
per-app rows of section 7.

**The two orders agree on the ends and disagree in the middle:**

| position | this plan | the distribution plan |
|---|---|---|
| first | health | health |
| second | notes-app | notes-app |
| middle | budget, finance, commute, location | location, commute, finance, budget |
| last | birds | birds |

The agreement on health first is independent and was reached from different
evidence: this plan argues it from blast radius and the absence of a routed page,
that one argues it from CSS debt (health emits no stylesheet at all, so trap B is
excluded by construction on the first real extraction). Two arguments, one answer,
which is worth more than either.

**The disagreement is real and this plan's ordering rule is the one that catches
something.** The distribution plan orders by CSS debt, which is a cost argument.
This plan orders by dependency direction, which is a correctness argument: a
provider must leave after its consumers. Put location before commute and, for as
long as the gap lasts, the operator's commute brief silently loses its location
block on his own instance, because `services/commute/scoring.py` reaches location
at call time and degrades rather than failing. Same for finance before budget.

So: **health, notes-app, then the four in provider-last order, then birds.** If
that is overruled in favour of the CSS-debt order, the four extraction tickets in
the middle need their `depends_on` re-chained, and the degradation window needs to
be stated to the operator rather than discovered.

---

## 2. Per app: what actually moves

Notation: **C** = one of the 102 content files. **H** = hidden coupling, a
platform-named file that is app code and is not in the 102. **P** = platform code
that references the app and must change but stays.

Every app also has the same four constant items, listed once here rather than
seven times below:

- Its factory and its entry in `_CATALOG_FACTORIES` leave
  `backend/services/app_registry.py`.
- Its entry leaves `frontend/src/apps/registry.tsx` (or that file is already gone;
  see section 7).
- Its id leaves the docstring roster in `backend/services/installed_apps.py:8`.
- It gains a `workshop.app.json` with the `ui` block from
  `stage-5-app-loader-plan-2026-08-24.md` section 2.1 and a
  `[project.entry-points."workshop.apps"]` declaration so the backend catalog
  discovers it (`app_registry` already supports this path, added in stage 4).

### 2.1 health (3 C, 0 H)

**Frontend (1 C)**
- `frontend/src/pages/apps/health/HealthSettings.tsx`

**Backend (2 C)**
- `backend/routes/health.py` (serves `/health`, ingest under HTTP Basic)
- `backend/services/health.py`

**Tests**: none named for health. The literals at
`backend/tests/test_http_contract_foundation.py:200-223` are health-app route
assertions and move with it; the six `backend/services/app_runner.py` hits are
the `health:` manifest FIELD (a probe path) and are false positives that stay.

**Platform code that must change (P)**
- `app_registry._health_spec` and its `_CATALOG_FACTORIES` row.
- `edge_blocked_prefixes=("/health/ingest",)` travels on the AppSpec, so the edge
  needs no separate change. Verify with `scripts/check_edge_blocklist.sh` if it
  exists; otherwise verify by requesting `/health/ingest` on the public origin.
- `infra/systemd/instance.env.example:71` `HEALTH_AUTH_ENV`.

**Watch for**: `routes/health.py` line 10 is a comment reserving
`/health/owntracks-helper` against the platform's own `/healthz`. It is prose,
not a dependency, but the reserved-name guard it describes is real and must not
be dropped when the router leaves.

### 2.2 notes-app (9 C of which 1 is a miscount, 0 H)

**Frontend (5 C)**
- `frontend/src/pages/apps/notes/{Notes,NoteView,NoteSidebar}.tsx`,
  `frontend/src/pages/apps/notes/{api.ts,wikilinks.ts}`

**Backend (4 C, one of which does NOT move)**
- `backend/routes/notes.py` (the app, prefix `/notes-app`) MOVES
- `backend/services/notes_vault.py` MOVES
- `backend/services/notes_store_adapter.py` (`VaultNoteAdapter`) MOVES
- `backend/routes/notes_all.py` **STAYS**. It is the platform's cross-scope
  knowledge feed at prefix `/notes` and `check_no_app_names.py` already exempts it
  from the `imports` category via `_PLATFORM_MODULES`. The `content` matcher
  `^backend/(routes|services)/notes(_[a-z_]+)?\.py$` never got the same exemption.
  Fix the matcher or move the file; either way this re-pins content DOWN 102 to 101.

**Tests that move**
- `backend/tests/test_notes.py`, `backend/tests/test_notes_owner_partition.py`
  (both are in the 20 import sites).

**Platform code that must change (P)**
- `app_registry._notes_spec`, including `index_collectors={"vault": ...}` and
  `store_adapters`.
- `packages/ui/src/styles/components.css:3680` and the 92 lines below it, the
  `/* ---- NOTES app ---- */` block with 33 `.ws-note-*` selectors. This is the
  one place the kit itself carries an app, and it must come out BEFORE the app
  leaves or the app renders unstyled. Covered by ticket
  `2c13ef899b084825835a08bb4f50b949`.
- `backend/tests/test_apps_api.py:160` and `frontend/src/App.tsx:117,205`
  (the comment and the redirect that distinguish `/notes` from `/apps/notes`).
- `frontend/src/api/schema.gen.ts:6381`, regenerated by `scripts/gen_openapi.py`
  once the router no longer mounts.

### 2.3 budget (7 C, 1 H)

**Frontend (3 C)**
- `frontend/src/pages/apps/Budget.tsx`, `budget/Projection.tsx`, `budget/budget.css`

**Backend (4 C + 1 H)**
- C: `backend/routes/budget.py`, `services/budget_engine.py`,
  `services/budget_persona.py`, `services/budget_store.py`
- H: `backend/services/categorizer.py`. Measured: it imports
  `budget_store, budget_engine, llm_router`, writes the budget override store and
  is called by `routes/budget.py:106`. It is budget's code under a generic name.

**A real misplacement to fix in the same change**: `_categorize_loop` is declared
on `_finance_spec.background`, not on budget's, while the function it runs lives
in budget's module. Move the loop declaration to `_budget_spec` before extraction,
or budget leaves and finance's background loop imports a module that is gone.

**Platform code that must change (P)**
- `app_registry._budget_spec`, its `_PLAID_MCP` share and its `needs: ["finance.read"]`.
- `frontend/src/components/AutonomyPanel.tsx:403`,
  `backend/services/agents_registry.py:365`, `backend/routes/usage.py:159`,
  `backend/services/chat_store.py:3`, `backend/services/jobs.py:23`,
  `backend/services/manifests.py:237-239`,
  `backend/mcp/workshop_query_server.py:93,239`,
  `backend/db/migrations/versions/f3b8d2a17e94_*.py:81`. Most of these are
  `"app:budget"` or `project:budget` used as an EXAMPLE scope key in a docstring
  or a description string. They are literals by the gate's definition, they are
  not dependencies, and each should be changed to a fictional id rather than
  deleted.
- `backend/services/triggers.py` (5 sites) is the token/loop `budget` field and
  is a false positive that stays.

### 2.4 finance (9 C, 4 H)

**Frontend (3 C)**
- `frontend/src/pages/apps/Finance.tsx`, `finance/FinanceSettings.tsx`,
  `finance/PlaidLink.tsx`

**Backend (6 C + 4 H)**
- C: `routes/finance.py`, `services/finance_cache.py`, `finance_events.py`,
  `finance_persona.py`, `finance_plaid.py`, `finance_store_adapter.py`
- H: `backend/routes/plaid_webhook.py` (already visible as one import site),
  `backend/services/plaid_store.py`, `backend/services/plaid_verify.py`,
  `backend/scripts/migrate_plaid_per_user.py`

**Tests that move**
- `backend/tests/test_plaid_env_resolution.py` (an import site).
- `backend/tests/conftest.py:18` names finance and must be re-pointed at a
  fictional app.

**Platform code that must change (P)**
- `app_registry._finance_spec` (the largest factory: two mounts, MCP launch spec
  and provider, `event_sources`, `event_verifiers`, `event_dedup`,
  `edge_blocked_prefixes`, `secret_env`, two `stores`, one `store_adapter`) plus
  `_PLAID_MCP` and `_PLAID_MCP_PROVIDER` and `_finance_on_startup` and
  `_prewarm_loop`.
- `infra/systemd/secrets.env.example:10-18` (the whole PLAID block).
- `scripts/security_audit.sh:42-43,82-91` (plaid-auth.env mode checks and the
  `PLAID_ENV` sanity block). These are host operational checks for one app and
  belong with the app.
- `infra/systemd/workshop-backend.service:51-52` names finance in a comment about
  bind addresses.

**Watch for**: `/plaid/webhook` must stay on the edge blocklist. It travels on
the AppSpec (`edge_blocked_prefixes`), so it survives extraction if and only if
the extracted spec keeps the field. Assert it.

### 2.5 commute (21 C, 0 H)

**Frontend (4 C)**
- `frontend/src/pages/apps/Commute.tsx`, `commute/CommutePages.tsx`,
  `commute/CommuteSettings.tsx`, `commute/useCommuteNative.ts`

**Backend (17 C)**
- `backend/routes/commute.py`, `backend/services/commute_persona.py`, and the
  whole 15-module package `backend/services/commute/` (`__init__`, `alerter`,
  `config`, `geo`, `journeys`, `llm`, `messages`, `patterns`, `places`, `poller`,
  `reason`, `scoring`, `segment`, `timefmt`, `transit_match`).

**Tests that move**
- `backend/tests/test_commute_adaptive.py`, `test_commute_alerter.py`,
  `test_commute_scoring.py`, `backend/tests/test_auth_env.py` (an import site).

**Platform code that must change (P)**
- `app_registry._commute_spec` and its `require_domain_owner("commute")` mount.
- `frontend/src/App.tsx:167`, the `/commute` legacy redirect. The loader's
  manifest has a `redirects` block for exactly this, so it becomes a declaration.
- `mobile/app.json:29` "commute alerts" in a permission string. The 2026-08-20
  pass genericised the other permission strings and missed this one.
- `packages/ui/src/native/types.ts` and `shared/native-bridge.ts` both declare
  `interface CommuteActivityState`, and both are invisible to the gate because it
  matches quoted strings and paths, never an identifier. Rename to
  `LiveActivityState` (`stage-4b-kit-promotions-2026-08-24.md` commit A).
- `mobile/modules/workshop-live-activity/ios/CommuteActivityAttributes.swift`,
  `mobile/targets/WorkshopWidgets/CommuteLiveActivity.swift` and its reference in
  `WorkshopWidgetsBundle.swift`. A native widget target named for one app.
- `scripts/reviewer.sh:357,364` `_APPS_FORBIDDEN` and the `commute/notes/` branch;
  `scripts/check_pluggable_apps.sh:124-137` `FORBIDDEN_TRACKED`. Both hardcode
  four commute JSON paths and a commute notes directory as forbidden-tracked
  state. Once commute is not in the tree these are dangling and must be
  de-hardcoded or retired.
- `scripts/frontends_watchdog.sh:5,78-79`, `scripts/install_cron.sh:102`,
  `scripts/preflight.sh:182,192`, `scripts/bootstrap.sh:169` all reference the
  operator's commute schedule in comments and warnings.
- `infra/systemd/instance.env.example:79` `WORKSHOP_COMMUTE_DISCORD_CHANNEL`.

**Watch for**: `services/commute/scoring.py` keeps a module `__getattr__` so that
`scoring.location_svc` still resolves; nine tests patch it that way. That
`__getattr__` is what makes the commute-to-location edge call-time. It travels
with commute and must keep working against an absent location.

### 2.6 location (5 C, 2 H)

**Frontend (2 C)**
- `frontend/src/pages/apps/Location.tsx`, `location/LocationSettings.tsx`

**Backend (3 C + 2 H)**
- C: `routes/location.py`, `services/location.py`, `services/location_persona.py`
- H: `backend/routes/owntracks.py` and `backend/services/owntracks.py`. This is
  the coupling section 3 exists for: `routes/owntracks.py` is mounted by
  `_location_spec`, is not in the 102, and `services/owntracks.py:24` does
  `from . import location`. Removing all 102 leaves this pair behind and
  `import main` fails with `ImportError: cannot import name 'location' from 'services'`.

**Tests**
- `backend/tests/test_http_contract_foundation.py` carries 15 location literals,
  all route-contract assertions. They move with the app.
- `backend/tests/test_ownership.py:75,99` and `test_commute_adaptive.py` (8 sites)
  reference location as a fixture.

**Platform code that must change (P)**
- `app_registry._location_spec`, both mounts, `edge_blocked_prefixes=("/location/owntracks",)`.
- `frontend/src/App.tsx:207-208`, the `/location` legacy redirect and its comment.
- `frontend/src/pages/apps/Location.tsx:21` imports
  `../../api/schema.gen` for a wire type. `stage-4b-kit-promotions-2026-08-24.md`
  section 2.2 decided to leave that import alone until stage 7, and **this is the
  stage**: the extracted app generates its own client from its own service, or it
  vendors the two interfaces it needs with a comment saying where they came from.
  Do not promote a 32,000-line generated schema into the kit.
- `infra/systemd/instance.env.example:72` `OWNTRACKS_AUTH_ENV`.
- `docs/owntracks-ios.md` is an operator setup document for this app.
- `backend/services/app_registry.py` `_has_module` catches `ModuleNotFoundError`
  only. Widen it to `ImportError` (covered by ticket
  `403b3b8875e644fa893a48ad87f1aa7a`) or a half-extracted location takes the whole
  backend down instead of dropping one app from the catalog.

### 2.7 birds, part one: backend, data and the weather spine (4 C, 3 H)

**Backend (4 C + 3 H)**
- C: `backend/routes/birds.py`, `services/birds_analyze.py`,
  `services/birds_forecast.py`, `services/birds_store.py`
- H: `backend/routes/environment.py`, `backend/services/environment.py`,
  `backend/scripts/atlas_backfill_embeddings.py`

`services/environment.py` is measured, not guessed: its own docstring opens
"Environment / weather data spine for the Birds app", it owns
`~/birdnet/data/environment.db` via `BIRDNET_ENV_DB`, and `_birds_spec` requires
route modules `("birds", "environment")`. It reads as platform code and is not in
the 102 because the roster has no app called `environment`.

**The data move is part of this step, not an afterthought.** `birds_store.py`
resolves the detection database and clips inside `apps/birds/`. `BIRDNET_DB`,
`BIRDNET_CLIPS`, `BIRDNET_BROWSER_DB`, `BIRDNET_CLIPS_DIR`, `BIRDNET_ATLAS_CLIPS_DIR`
and `BIRDNET_ENV_DB` override it (`infra/systemd/instance.env.example:66-71`). If
the source moves and the env is not set in the same change, the live detection
database moves with the source.

**Tests**: `backend/tests/test_ownership.py:159-178` exercises
`birds_analyze._resolve_viewer` and is an import site; it moves with the app.
`test_app_registry_tolerant.py:47,62` names `routes.birds`, `routes.commute`,
`routes.environment` as the modules it deletes to prove tolerance, and must be
re-pointed at fictional modules.

### 2.8 birds, part two: the frontend and the standalone build (44 C, 2 H)

**Frontend (44 C)**
- `frontend/src/pages/apps/Birds.tsx` and all 43 files under
  `frontend/src/pages/apps/birds/`, including the `aviary/` subtree (7 files, a
  vendored voxel engine) and `standalone/main.tsx`.

**Hidden (2 H, and neither is scanned by any gate)**
- `frontend/springer.html`
- `frontend/vite.springer.config.ts`, which carries a Vite transform plugin
  `rewriteBirdsPaths` that string-replaces `/apps/birds` out of every module in a
  birds-only build. `check_no_app_names.py`'s `PLATFORM_DIRS` starts at
  `frontend/src/`, so the frontend root is unscanned and these 15 occurrences are
  invisible. Neither file is referenced by `package.json`, `scripts/apply.sh` or
  `scripts/ui_source_id.sh` (measured), so this build is invoked by hand and
  nothing in the platform's build graph breaks when it leaves.

**Platform code that must change (P)**
- `frontend/src/lib/storage.ts` registers two birds device-scoped storage key
  groups eagerly, with a header explaining that `registerDeviceScoped` must run
  before the first `setCurrentUser(id)` migration sweep while every birds page is
  lazy. Its own comment says it "leaves with birds' content at stage 7, when the
  app has its own eager entry point". The loader's `ui.provider_entry`
  (`stage-5-app-loader-plan-2026-08-24.md` section 2.5) IS that eager entry point,
  so the registration moves into it. Do not move it earlier.
- `scripts/check_design.sh:121-123` exempts three birds file paths from the
  inline-hex rule. Nobody has checked whether the script fails open or closed on a
  missing path. Check, then remove the three.
- `scripts/duplicate_helpers_baseline.json` pins four app-page paths
  (`birds/Atlas.tsx` twice, `health/HealthSettings.tsx`, `location/LocationSettings.tsx`)
  and `scripts/opaque_fields_baseline.json` pins one. A baseline that names a file
  that no longer exists is a gate with a dangling reference.
- `frontend/src/lib/appIcon.tsx` imports the `Bird` lucide icon into the token
  table at lines 26 and 78. That is the platform bundle knowing what a bird looks
  like. The token table is generic by design; the question is whether `bird`
  belongs in a platform icon vocabulary at all, or whether the app should declare
  a url.
- `frontend/src/components/shell/{places,pageChrome,takeover}.ts(x)` and
  `packages/ui/src/{bytes,time,appChatDock}.ts` reference birds and commute in
  explanatory comments only. Prose, not dependencies. Leave them, or generalise
  the wording; do not let a comment sweep masquerade as extraction progress.

---

## 3. The platform-side coupling the gate does not measure

This is the section that decides whether a clone boots. **Thirty-nine coupling
sites, in ten classes. Twenty-four of them are invisible to all three counts**,
which is to say: drive `content` to zero and those twenty-four are still there.
The other fifteen show up as literals or imports but are not fixed by removing
app source, so they need naming too.

Each item below was verified on 2026-08-24.

### Class A. App code wearing a platform name (10 files, 7 invisible)

| # | file | app | seen by the gate? |
|---|---|---|---|
| A1 | `backend/routes/owntracks.py` | location | 2 literals only |
| A2 | `backend/services/owntracks.py` | location | 2 literals only |
| A3 | `backend/routes/environment.py` | birds | no |
| A4 | `backend/services/environment.py` | birds | no |
| A5 | `backend/services/categorizer.py` | budget | 2 imports only |
| A6 | `backend/routes/plaid_webhook.py` | finance | 1 import only |
| A7 | `backend/services/plaid_store.py` | finance | no |
| A8 | `backend/services/plaid_verify.py` | finance | no |
| A9 | `backend/scripts/atlas_backfill_embeddings.py` | birds | 2 literals only |
| A10 | `backend/scripts/migrate_plaid_per_user.py` | finance | no |

A1 and A2 are the pair that makes `import main` fail. The handoff already warns
that "two modules hide their owner behind a generic name" and names two; the
measured count is ten. The general rule the handoff states is the right one and
should be the checklist item: **check what a module imports, not what it is called.**

### Class B. App build files outside the scanned directories (2 files, 2 invisible)

B1 `frontend/springer.html`, B2 `frontend/vite.springer.config.ts`.
`PLATFORM_DIRS` is `("backend/", "frontend/src/", "packages/", "scripts/", "infra/", "mobile/")`,
so the frontend's own root is never scanned.

### Class C. Identifiers naming an app (5 files, 5 invisible)

C1 `packages/ui/src/native/types.ts` (`CommuteActivityState`, 3 sites),
C2 `shared/native-bridge.ts` (same interface, 3 sites),
C3 `mobile/modules/workshop-live-activity/ios/CommuteActivityAttributes.swift`,
C4 `mobile/targets/WorkshopWidgets/CommuteLiveActivity.swift`,
C5 its registration in `mobile/targets/WorkshopWidgets/WorkshopWidgetsBundle.swift`.

The gate's literal patterns match quoted strings and path shapes. An identifier is
never matched, so the design system kit and the iOS widget bundle can both name an
app while all three counts read clean.

### Class D. CSS (2 files, 1 invisible, 1 counted once)

D1 `frontend/src/styles.css`: about 22 class names and roughly 320 use sites under
`frontend/src/pages/apps`, defined only here. `.muted` 222 uses, `.card` 58.
D2 `packages/ui/src/styles/components.css:3680`: the 92-line `NOTES app` block,
invisible because the gate skips comment lines and the class names are `.ws-note-*`
rather than a quoted app id.

Neither is caught by `npm run build`, `npm run typecheck`, `npm test` or
`check_design.sh`. Gitignore the app directories and the build stays green;
extract them and they render unstyled, in production only.

### Class E. Undercounted dynamic imports (1 file, structural)

E1 `frontend/src/apps/registry.tsx` reaches app pages 15 times through
`lazyPage(() => import(...))` and once statically. The gate reports **1** import
site for the file. The number most likely to move at extraction is the one least
well measured.

### Class F. Gates, scripts and baselines that hardcode an app (9 files, 5 invisible)

F1 `scripts/check_pluggable_apps.sh` (`APP_LOOPS` naming five app loop functions,
a grep for four `<App>ContextProvider` class names, four commute JSON paths in
`FORBIDDEN_TRACKED`, and a `commute/notes/` branch). Still wired into
`reviewer.sh`; the handoff says de-hardcode or retire it during stage 7.
F2 `scripts/check_design.sh` (3 birds paths).
F3 `scripts/reviewer.sh:357,364` (`_APPS_FORBIDDEN` plus the commute-notes branch).
F4 `scripts/gen_openapi.py:24-25` (its docstring enumerates the catalog, and it
mounts the full catalog to generate types).
F5 `scripts/preflight.sh:67,182,192`.
F6 `scripts/security_audit.sh:42-43,82-91`.
F7 `scripts/duplicate_helpers_baseline.json` (4 app-page paths).
F8 `scripts/opaque_fields_baseline.json` (1).
F9 `scripts/check_no_app_names.py` itself (11 sites, its watch list). See section 5.

### Class G. Infrastructure, env examples and ignore rules (5 files, 4 invisible)

G1 `infra/systemd/instance.env.example:62-79` (five `BIRDNET_*`, `HEALTH_AUTH_ENV`,
`OWNTRACKS_AUTH_ENV`, `WORKSHOP_COMMUTE_DISCORD_CHANNEL`).
G2 `infra/systemd/secrets.env.example:8-28` (the PLAID block, health-auth,
owntracks-auth, `OPEN_WEARABLES_API_KEY`).
G3 `infra/systemd/workshop-backend.service:51-52`.
G4 `mobile/app.json:29`.
G5 `.gitignore`: at least twelve app-specific lines
(`apps/birds/public-site/birds-public.json`, `apps/birds/**/*.db`,
`apps/birds/BIRDSTATION-CONTEXT*.md`, four `/commute/*.json` paths,
`/commute/notes/`, `/commute/`, `/backend/mcp/plaid/`, `/agents/apps/`), plus
comment blocks naming budget, finance, commute and location.

### Class H. Tracked documentation about the operator's apps (11 files, all invisible)

`docs/birdnet-accuracy.md`, `docs/birds-atlas.md`,
`docs/birds-presence-vs-count.md`, `docs/birds-recordings-plan.md`,
`docs/birds-redesign/` (5 files including two HTML exports),
`docs/commute-v2-plan.md`, `docs/owntracks-ios.md`.

`docs/` is outside `PLATFORM_DIRS` on purpose (prose is a historical record, not a
build dependency) and the fresh-clone assertion's identity scope also excludes
`*.md`. So a stranger's clone contains eleven documents about one operator's bird
rig and commute, and no gate in the repo will ever say so.

### Class I. Data and ops paths (2 sites, both invisible)

I1 `apps/birds/birdnet.db` and `apps/birds/clips/`, resolved by
`services/birds_store.py` inside the source tree. The env overrides exist; they
must be set in the same change that moves the code.
I2 `~/birdnet/data/environment.db` hardcoded as the default in
`services/environment.py`.

### Class J. The mechanism that turns any of the above into a boot failure (1 site)

J1 `backend/services/app_registry.py:54` `_has_module` catches
`ModuleNotFoundError`. The failure mode is a module that IS present importing one
that is not, two levels down, which raises a plain `ImportError`. The comment
above it states the intent correctly and the implementation is one exception class
too narrow. This single line is the difference between "location is not in the
catalog" and "the backend does not start".

### Summary

```
Class A  app code under a platform name          10 files   (7 invisible)
Class B  build files outside the scan             2 files   (2 invisible)
Class C  identifiers                              5 files   (5 invisible)
Class D  CSS                                      2 files   (1 invisible)
Class E  undercounted dynamic imports             1 file    (structural)
Class F  gates, scripts, baselines                9 files   (5 invisible)
Class G  infra, env, ignore rules                 5 files   (4 invisible)
Class H  tracked app documentation               11 files  (11 invisible)
Class I  data and ops paths                       2 sites   (2 invisible)
Class J  the exception width in _has_module        1 site   (1 invisible)
                                                 --------
                                                 39 items, 24 wholly invisible
```

---

## 4. Where each app goes

**This is the operator's decision and it is stated in full in section 8.** What
follows is the recommendation and, more usefully, the consequence of each option
for three specific things that will otherwise be discovered late.

### 4.1 The recommendation: one overlay repo now, individual repos later

`app-is-the-entity-2026-08-20.md` section 10 already recommends the overlay and
the reasoning still holds: it gets the platform to zero app names in one move,
which is the actual requirement, without paying for seven repos or losing the
birds rig's history. Nothing measured since changes that.

A private repo, call it `workshop-apps`, cloned to `apps/` in this checkout. Each
app is a directory `apps/<slug>/` containing its source, its
`workshop.app.json`, its own `package.json` and `pyproject.toml`, and its built
`dist/ui/`. `apps/` is already gitignored by the platform (`.gitignore:160`), so
the overlay's own `.git` lives inside a directory the platform never tracks.

Split an individual app out later when it earns one. Birds is the obvious first
candidate, because it has a Pi rig, a public site and a standalone build, which is
three consumers of a version number.

### 4.2 Consequence for worktrees, and it is the sharpest one

`apps/` is gitignored, which means **a `git worktree` of the platform gets an
empty `apps/`**. Today that is harmless because the app source lives in
`frontend/src/` and `backend/`, which a worktree does carry. After extraction it
is not harmless: every worktree, every branch environment and every isolated
build-agent tree starts with no apps installed.

Three ways that resolves and the plan must pick one before the first extraction,
because it decides what "run the app locally" means for every agent afterwards:

1. **Nothing.** A worktree has no apps. Correct for platform work, and it is also
   exactly what the fresh-clone assertion asserts, so the worktree becomes a free
   continuous test of assertion 2. Wrong the moment someone needs to reproduce an
   app bug in a worktree.
2. **Symlink `apps/` into every worktree** from the main checkout. Cheap, and the
   apps are then shared mutable state across worktrees, which is the thing
   `HANDOFF-branch-isolation-2026-08-22.md` exists to prevent.
3. **The overlay is its own worktree**, created alongside the platform worktree
   from the overlay repo. Correct and costs one extra command in whatever creates
   a worktree today.

Recommendation is 3, with 1 as the default when the overlay is absent, because a
worktree that fails to build without apps would make the platform's own tests
depend on the operator's apps, which is the failure this whole effort is about.

### 4.3 Consequence for the app loader

None that is new, and this is the good news. `stage-5-app-loader-plan-2026-08-24.md`
section 3.3 already puts the built bytes at `apps/<slug>/dist/ui/` and serves them
through `GET /api/base-apps/ui/{slug}/{path}`, resolved via
`paths.scope_root(f"app:{slug}")`. That path does not care whether `apps/` is an
untracked directory, an overlay clone or seven separate clones. The manifest is
read by `app_runner._read_manifest_file` in all three cases.

Stage 5's open question Q2, recorded on ticket
`86564637054249aab54c668da03e98ea` as "how an out-of-repo app obtains
`@workshop/ui` and `@workshop/app-build` at build time", **has since been
answered** by `app-loader-distribution-and-versioning-2026-08-24.md` section 4.2:
the platform is the channel and hands out versioned tarballs produced by
`npm pack`, which an app repo depends on as
`"file:.workshop/kit/workshop-ui-0.6.2.tgz"` in `devDependencies`, populated by
`scripts/kit_channel.sh sync`.

That answer is deliberately independent of where the app's repo lives, which
removes what looked like a coupling between D1 and Q2. It does not remove D1:
history, backup and worktrees still turn on it (4.2 above).

### 4.4 Consequence for the fresh-clone assertion

Assertion 1 (`content_files == 0`) is satisfied by extraction regardless of
destination, because it counts tracked files in the platform clone.

Assertions 2 and 3 are pinned expected failures today
(`scripts/fresh_clone_baseline.json`, per the design). They clear in this order:

- Assertion 2 clears when the loader lands AND the last app leaves, because both
  halves must pass: the frontend (`registry.tsx` gone) and the backend (`import main`
  with every app module absent, which needs Class A and Class J from section 3).
- Assertion 3 clears immediately after 2, since its mechanism is already proven
  against a tree with app source present and zero apps installed.
- Deleting a pin that has started passing is mandatory, not optional: the design
  makes a still-pinned passing assertion exit 1 as `PIN STALE`.

**The last extraction ticket is the one that deletes both pins.** That is the
moment the string "app separation is done" becomes reachable, and it is reachable
by exactly one code path.

---

## 5. The two enumerating registries, and the gate itself

### 5.1 `frontend/src/apps/registry.tsx` (54 literal sites, 16 module specifiers)

It does not get cleaned up. **It is deleted**, and stage 5 already specifies what
replaces it: `frontend/src/apps/contract.ts`, `declarations.ts`, `loader.ts` and
`AppRouteHost.tsx`, none of which may contain an app name. The signatures
`appRoutes()`, `appCrumbs()`, `appGroups()`, `appPlaces()`, `appIcon(id)`,
`appStatuses()` and `appSettingsPanels()` survive so `App.tsx`, `appNav.tsx`,
`useShell.ts` and `Apps.tsx` barely change.

So of the 302 literals, 54 leave with the loader rather than with any app, and
they leave before the first frontend extraction rather than after the last.

### 5.2 `backend/services/app_registry.py` (34 literal sites, 7 import sites)

It does not get deleted. Stage 4 already built the replacement path:

```
[project.entry-points."workshop.apps"]
my-app = "my_app.spec:app_spec"
```

and a discovered app SHADOWS a built-in of the same id, "deliberately: that is
what extraction looks like from the platform's side". So each extraction step
deletes one `_<app>_spec` factory and one `_CATALOG_FACTORIES` row, and the
entry-point path picks the app up from the overlay.

At the end, `_CATALOG_FACTORIES` is an empty tuple and every helper that iterates
it still works. **Keep the empty tuple and its comment.** Deleting the built-in
path entirely would remove the thing that makes the file testable
(`test_appspec_seams.py` plugs in a fabricated `seamtest` app) and would make a
future built-in impossible without re-inventing it.

`_has_module` stays and gets one word wider (Class J).

### 5.3 The gate, once its subjects are gone

**A target of literally zero is unreachable and the plan must say so.** Measured,
the 302 literals decompose as:

| group | sites | what happens at stage 7 |
|---|---|---|
| tests | 146 | Roughly 40 move with their app. The rest are PLATFORM tests using a real app id as a fixture and must be renamed to a fictional id. This is its own ticket. |
| `registry.tsx` | 54 | Deleted by the loader (5.1). |
| `app_registry.py` | 34 | Deleted factory by factory (5.2). |
| other platform source | 57 | Mixed. Some are dependencies, some are example scope keys in docstrings, and some are FALSE POSITIVES that can never go to zero: `backend/services/triggers.py` (5) is the loop `budget` field, `backend/services/app_runner.py` (6) is the manifest `health:` probe-path field. |
| `check_no_app_names.py` | 11 | The list of apps it watches. It is the one file that must name them. |

The `watched` roster is sticky by design ("once an app is known, it is known
forever"), and that stickiness is what makes those 11 permanent. So:

**Recommendation: the gate stops counting itself, and stops being the finish
line.** Three concrete changes, in the ticket in section 9:

1. `scan()` already skips `scripts/no_app_names_baseline.json` with a comment
   explaining that the ratchet must not count its own memory. Extend the same
   exemption to `scripts/check_no_app_names.py`. That is 302 to 291 and it is
   honest for exactly the reason the baseline exemption is.
2. Move the roster out of the checker and into the baseline entirely, so the
   checker holds zero app names and the baseline holds all of them. The baseline
   is already exempt. This is strictly better than 1 and makes the target
   genuinely zero for `content` and `imports`.
3. Re-pin `literal_sites` at whatever the irreducible remainder is, once measured,
   with the `--because` note recording that the remainder is the ambiguous-English
   false positives (`budget` the field, `health` the probe path) and naming each
   one. Do not chase them to zero by renaming platform fields; that is the tail
   wagging the dog.

And per the handoff, `scripts/check_pluggable_apps.sh` is de-hardcoded or retired
in this stage. It inspects five files, hardcodes five loop function names and four
commute paths, and is still wired into `reviewer.sh`. It is the gate that reported
`pluggable-apps: clean` for five weeks. Retiring it is the honest option; if it is
kept, the app names must come out.

---

## 6. Rollback

Every step is designed to be undoable in one command, and the reason is specific:
this work has been believed finished twice, so the cost of a bad step must be
minutes rather than an evening.

**The invariant that makes rollback cheap.** Extraction is `git rm --cached` on
the platform side plus a commit in the overlay. The BYTES do not move off the
machine and the platform's history keeps every version of every extracted file.
`git log --follow` and `git show <sha>:<path>` work forever.

**Per-step rollback, in order of cost:**

1. **Before the platform commit lands.** `git checkout -- .` in the platform tree.
   The overlay already has the copy; nothing is lost.
2. **After the platform commit, before the app is reinstalled.** `git revert <sha>`
   restores the files, the factory, the registry entry and the tests in one
   commit. The app is back in the catalog on the next backend restart because
   `_has_module` finds its route module again.
3. **After the overlay is the only copy.** Still `git revert <sha>` on the
   platform side, because the platform commit contains the deletions and reverting
   a deletion restores the content. Then remove the overlay's copy so the app is
   not defined twice; a discovered app shadows a built-in, so a duplicate is a
   silent preference rather than an error, which is exactly why this cleanup is
   part of the rollback and not optional.
4. **The data.** Birds is the only app whose data sits inside the tree
   (`apps/birds/birdnet.db`, `apps/birds/clips/`). Its move is an ops step guarded
   by `BIRDNET_DB` and `BIRDNET_CLIPS`. Rollback is unsetting the env, and the
   rule is that the database is COPIED and the old copy left in place until the
   app has run against the new location for a day. Do not move it.

**The rehearsal that makes all of this cheap, and it should be run before every
extraction commit is merged:**

```bash
bash scripts/check_fresh_clone.sh            # once it exists, ticket e278fa5f...
```

It clones, deletes the app source, installs, builds and imports. Running it BEFORE
the extraction commit tells you whether the extraction would work, in a throwaway
directory, without touching the live tree. That is the whole point of having built
it first.

**What is NOT recoverable and must therefore not happen:** an extraction that also
rewrites platform history to purge app content. Do not use `git filter-repo`, do
not rewrite `main`. The requirement is that a stranger's clone contains no app
source at HEAD, not that the repository has never contained it. History rewriting
would break every worktree, every open branch and every `git log --follow` on the
extracted files, and it buys nothing the requirement asks for.

---

## 7. Where this sits relative to the loader and the fresh-clone assertion

```
   [already filed]                                    [this plan]

   403b3b8875e6...  gate blind spots  ─────────────┐
   e278fa5faa10...  fresh-clone assertion ─────────┤
   2c13ef899b08...  CSS out of the kit  ───────────┤
                          │                        │
                          v                        │
   86564637054...  stage 5 part one (loader to     │
                   the fixture app, steps 1-4)     │
                          │                        │
                          │  BLOCKED: stage 5      │
                          │  steps 5-9 need the    │
                          │  distribution answer   │
                          │  (Q2), which is being  │
                          │  designed now          │
                          v                        v
                   stage 5 part two  ───────>  step 1  health
                   (a real app through          step 2  notes-app
                    the loader)                 step 3  budget
                                                step 4  finance
                                                step 5  commute
                                                step 6  location
                                                step 7  birds (two tickets)
                                                        │
                                                        v
                                                registry retirement,
                                                gate re-target,
                                                BOTH PINS DELETED
```

**Three hard orderings, each with a measured reason.**

1. **No frontend extraction before the loader.** `registry.tsx` names sixteen app
   page modules with literal specifiers. `lazyPage` defers loading, not building.
   A tree with any app's pages removed fails `tsc -b` with TS2307, measured at
   8.6 seconds into a fresh clone build. This is mechanism M3 and it is the one
   genuine blocker.
2. **No frontend extraction before the CSS lands.** 320 use sites of 22 classes
   defined only in `frontend/src/styles.css`. Nothing compile-checks them. An app
   extracted first renders unstyled, in production only, which is the exact
   failure shape of the 2026-08-09 stale-bundle incident.
3. **The fresh-clone assertion goes first, not last.** Its own ticket says so and
   the reason is that it is the instrument: building the instrument and crossing
   the line in the same change hides whether the instrument works. It also gives
   every extraction step its rehearsal (section 6).

**And one soft ordering that is not a blocker.** The BACKEND half of an app can
leave before the loader exists, because the backend already has its extension
path (entry points, stage 4) and `_has_module` already skips an absent app. Health
is headless, so **step 1 can start the day the gate blind spots and the fresh-clone
assertion land, without waiting for the loader at all.** That is worth taking: it
proves the overlay, the manifest, the entry point and the install list against a
real app while the loader is still being built.

**What stage 5 part two needs before extraction of any frontend can start**, from
ticket `86564637054249aab54c668da03e98ea`:

- Q1, the `kit` field in `services/manifests.py` has zero readers and is not wired
  to the manifest the loader reads, so the version gate cannot be built on it yet.
- Q2, how an out-of-repo app obtains `@workshop/ui` and `@workshop/app-build` at
  build time. Section 4.3 shows this is the same question as the destination
  decision.

**Q2 is now answered** by `app-loader-distribution-and-versioning-2026-08-24.md`
section 4.2 (tarballs through a platform-owned channel) and Q1 by its section 2
(delete the dead `kit` field in `manifests.py`, replace it with `ui.kit` in
`workshop.app.json` with one reader). Both landed 2026-08-24. `app-artifact-spec-2026-08-24.md`
landed the same day and fills the hole that
`hosting-design-review-2026-08-24.md` identified in the hosting paper. So stage 5
part two is designed and buildable, and it is the only remaining thing between the
loader and the frontend half of every extraction step after the first.

---

## 8. The operator's decisions

No tickets are filed for these. Each blocks something named above.

**D1. Where the apps go.** Overlay repo, or seven repos, or an untracked tree.
Recommendation: one private overlay repo `workshop-apps` cloned to `apps/`, with
individual repos split out later, per `app-is-the-entity` section 10. It gets the
platform to zero app names in one move, and it keeps the birds rig's history,
which an untracked tree does not. The build-dependency question that used to ride
on this has been answered separately (section 4.3), so D1 is now purely about
history, backup and worktrees.

**D2. What a worktree gets.** Section 4.2. Three options: nothing, a symlink, or
the overlay as its own worktree. Recommendation is the third with the first as the
fallback. This changes what "run the app locally" means for every agent after
stage 7 and it should be decided before step 1, not discovered at step 3.

**D3. `.muted` and `.card`: inline or promote.** Carried forward unchanged from
`stage-4b-kit-promotions-2026-08-24.md` section 6, where it is flagged as the one
place that plan would not proceed without an answer. 280 of 320 sites. The effort
ratio is roughly ten to one and it is the operator's cost.

**D4. Does the birds standalone build survive extraction, and where does it live?**
`frontend/springer.html` and `frontend/vite.springer.config.ts` build a
root-mounted birds-only app served by `apps/birds/springer/app.py`. It is a second
product with its own consumers. It can move into the app unchanged, or it can
become the app's own repo's problem, or it can be retired if the public site now
comes from somewhere else. Nobody has asked. It is a genuine product question and
it changes the size of step 7 materially.

**D5. Do the eleven tracked app documents leave?** Section 3, Class H. `docs/` is
outside every gate's scope on purpose, so this will never be forced by a check. A
stranger's clone currently ships four documents about a bird rig, a redesign
folder with two HTML exports, a commute plan and an OwnTracks iOS setup guide.
Recommendation is that they move to the overlay beside their app, but they are the
operator's writing and deleting someone's documentation on a gate's say-so is not
a call an agent should make.

**D7. Which extraction order wins for the middle four.** Section 1.1. This plan
says budget, finance, commute, location, so a provider always leaves after its
consumer and nothing degrades on the operator's instance mid-sequence.
`app-loader-distribution-and-versioning-2026-08-24.md` section 5.2 says location,
commute, finance, budget, ordering by CSS debt. Only one can be right and the
tickets currently encode this plan's. Worth a sentence from the operator, or a
measurement of how bad the degradation actually is, before ticket 9 starts.

**D6. What the app-name gate counts once no app is in the tree.** Section 5.3
recommends moving the roster into the baseline so the checker holds zero app
names, and re-pinning `literal_sites` at the irreducible false-positive remainder
with a recorded reason. The alternative is to retire the gate entirely once the
fresh-clone assertion is green, on the grounds that a clone that builds without
apps is a stronger statement than any count. Both are defensible. Retiring a gate
in this repository has a bad record, which is why the recommendation is the
conservative one.

---

## 9. Tickets filed

Filed with `python3 scripts/project_tasks.py add . "..." --priority N --execution code --source agent`.
Ids are recorded here so the dependency chain can be read without querying.

| # | id | filed priority | title | depends on |
|---|---|---|---|---|
| 1 | `df91b7c447c841c981499604a6f9b2b1` | 78 | Give the ten app modules that hide behind a platform name their app's name | none |
| 2 | `84028d4797054f4883003bd0384d7a4f` | 78 | Stand up apps/ as the destination tree, with history, for all seven apps | none |
| 3 | `57d641b82f34415db4486b6363eb342c` | 74 | Rename every real app id in platform test fixtures to a fictional app | none |
| 4 | `9a609740a48e46bea703771b4b4908db` | 72 | Take one app's name out of the kit, the shared bridge and the iOS widget | none |
| 5 | `64590936559546eaa4380d1f904ca18b` | 72 | De-hardcode the app names in the scripts, gates, baselines and env examples | none |
| 6 | `1732ce4e7e9840a09cae9636faab8e39` | 70 | Move the operator's app documentation out of the platform repo | 2 |
| 7 | `d65244c4adc54cdc9f5de2e7c6d82e6a` | 76 | Extract the health app, the rehearsal for the other six | 1, 2 |
| 8 | `668a18f30eef48adbc5fd77ccc8bca40` | 74 | Extract the notes app, and stop counting notes_all.py as app source | 7, `2c13ef899b08...` |
| 9 | `2e958c371c6343d799aa183339176fec` | 72 | Extract the budget app, with the categorizer and its misplaced loop | 8 |
| 10 | `1fac9c03f8154c39a0c80e33a49fc617` | 72 | Extract the finance app, with the three plaid modules and the webhook | 9 |
| 11 | `58136c09b34c4653ab56e3d6525aafe1` | 70 | Extract the commute app, its 15-module package and its native widget | 10 |
| 12 | `26dbe9b3be7f4bcea974cb02257130f1` | 70 | Extract the location app, with the owntracks pair that breaks import main | 11 |
| 13 | `1db9d9dbb7074902b2dff95baef29f96` | 68 | Extract the birds backend, the weather spine and the detection data | 12 |
| 14 | `9e5545827bee4da8941d9a8b1650f277` | 68 | Extract the birds frontend and its standalone build out of the frontend root | 13 |
| 15 | `38728986ac1745d49edfd324bf82eaf8` | 66 | Retire the two enumerating registries and re-target the app-name gate | 14, `e278fa5faa10...` |

Four tickets already on the queue are prerequisites and are NOT re-filed here:
`403b3b8875e644fa893a48ad87f1aa7a` (the gate's four blind spots and the CSS
check), `e278fa5faa1d4009a28264944a3db177` (the fresh-clone assertion),
`2c13ef899b084825835a08bb4f50b949` (the app CSS out of the kit and off the legacy
stylesheet), and `86564637054249aab54c668da03e98ea` (stage 5 part one, the loader
to the fixture app).

**A note on the priority column, because it does not survive the store.** This
scope's tickets live in Postgres and priority is banded there:
`{"urgent": 100, "high": 80, "medium": 40, "low": 1, "none": 0}`, with the
integer projected back from the band floor (`services/tasks.py:1199`, ADR 0019).
So every value from 60 to 79 reads back as **40** and all fifteen of these
tickets read `medium`. The numbers above are what was filed, not what
`list-json` will print. **The dependency chain, not the priority, is what carries
the order**, which is why every step past the first two declares one.

One ticket was filed with three words eaten by shell command substitution
(`38728986ac1745d49edfd324bf82eaf8`, part three) and its text was repaired in
place through `services.tasks.update`. It is recorded here rather than fixed
quietly.
