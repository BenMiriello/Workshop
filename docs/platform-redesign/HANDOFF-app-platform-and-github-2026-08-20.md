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

## 2. State as of 2026-08-20

```
content   95  tracked files that ARE app source      (was 143)
imports   24  platform files importing an app module (unchanged)
literals 315  platform lines naming an app           (was 331)
```

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

### Not done, in dependency order

**Stage 4 — the six AppSpec seams. This is the blocker and it is the next job.**

Extraction is not "move files". The platform imports FROM apps in six places,
each needing a different extension point that does not exist:

| Platform site | Needs |
|---|---|
| `routes/events.py` `VERIFIERS = {"plaid": ...}` | `AppSpec.event_verifiers` |
| `services/stores.py` (`NoteAdapter`, `FinanceAdapter`) | `AppSpec.store_adapters` |
| `services/semantic_index.py` imports `notes_vault` | `AppSpec.index_collectors` |
| `services/event_dispatch.py` `if source == "plaid"` | app-supplied idempotency derivation |
| `routes/birds.py` imports `_load_subscriptions`, appends `EVENTS_LOG` | a public event-publish API |
| `services/app_personas.py` (560 lines, four apps' logic) | an out-of-tree persona loader |

And `_CATALOG_FACTORIES` is a literal tuple: no entry-point scan, no plugin
path. **Nothing outside `backend/routes/` and `backend/services/` can become an
app today.** Tolerant (a missing module degrades) is not the same as extensible.

Also in this stage: promote the 11 platform-internal frontend modules app pages
reach for (`lib/usePoll` 18 uses, `lib/time`, `components/appNav`,
`components/AppSettings`, `lib/storage`, `components/AppChatDock`) into
`@workshop/ui`. `notes-app` already imports nothing outside the kit, which is
the proof this is achievable.

And fix the two cross-app edges that block independent extraction: **budget
imports finance** (`routes/budget.py`, `services/categorizer.py`) and **commute
imports location** (`services/commute/scoring.py`), both at load time, both
ignoring the capability their own AppSpec declares.

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
`~/birdnet/data/environment.db`. `backend/services/categorizer.py` is
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
sandbox** because `WORKSHOP_ROOT` resolves to `/home/clawd-a/workshop`, which is
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
