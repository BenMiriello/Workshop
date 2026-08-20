# App separation, the app loader, and GitHub integration

- Date: 2026-08-20
- Status: **research complete, design decided, staged. Stages 0-3 built, plus the
  first extraction (55 files). Handoff for the rest:
  [`HANDOFF-app-platform-and-github-2026-08-20.md`](HANDOFF-app-platform-and-github-2026-08-20.md).**
- Builds on [`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md)
  (the container model) and closes the seam
  [`apps-are-pluggable.md`](../apps-are-pluggable.md) deferred on 2026-07-12.
- Supersedes the "one deferred seam" framing in `apps-are-pluggable.md`.

---

## 0. Why this document exists, and the correction it opens with

The operator, 2026-08-20:

> "The main priority is making our code not literally code in my fucking apps.
> This is insane that we still have this. I've already planned out and we've
> already scoped out how this whole system is supposed to work. So if you
> haven't found that yet, then you're just not searching well enough."

> "All of the actual apps and projects that a user includes are gonna be
> completely unique to them and only to them, just like the ones that I have
> made are only specific to me and would never ever in a million years be in the
> source code or in anybody else's implementation. So if there's any confusion
> about that or any lack of urgency for fixing that, then you're mistaken for
> not understanding how basic and central this requirement is to the whole
> concept of this application."

> "The fact that you stage it out like a gradual optional process ... indicates
> that you don't actually understand what we're doing here."

**He is right on both counts and the record supports him.**

### 0.1 He was told it was done

`docs/handoff-pluggable-apps-migration.md` line 3, verbatim:

> **Status: DONE on `main` (2026-07-12).**

...followed by a "What was done" list and "Verified: fresh install list -> zero
app routes/cards/MCP, shell intact." Anyone reading that concludes app
separation shipped.

What was actually true is narrower, and the same document says so 21 lines
later in a single sentence:

> Remaining (intentionally deferred...): integrated in-shell frontend route
> injection; and git-history scrubbing of the already-committed personal data.

`apps-are-pluggable.md` then actively minimises that line:

> What is manifest-driven is its **visibility and backend wiring**, which is the
> security-critical part.

That framing is defensible for *security* and wrong for the requirement the
operator actually has, which is that his apps must not be in the product. The
deferred line is not a footnote; it is the whole of the frontend.

### 0.2 The gate that kept saying "clean"

`scripts/check_pluggable_apps.sh` prints `pluggable-apps: clean` and is wired
into `reviewer.sh`. It inspects **five files**: `main.py`, `llm_router.py`,
`chat_context.py`, `app_registry.py`, `Apps.tsx`. It does not look at
`frontend/src/pages/apps/` (61 tracked files, 14,613 lines), `apps/` (43 files,
8,752 lines), `backend/mcp/plaid/`, `agents/apps/*.md`, or the 42 app-named
modules in `backend/services/` and `backend/routes/`. It also hardcodes the app
loop names (`:41`) and commute's data paths (`:120-127`).

**So the green light was real and measured the wrong thing.** A gate that
cannot see the violation is worse than no gate, because it converts an unsolved
problem into a solved-looking one. That is the specific mechanism by which this
survived five weeks of review.

### 0.3 What was in fact planned, and where

The operator is right that this was scoped. The prior body of work, found:

- `docs/app-platform-plan.md` (2026-06-06) — identity manifest, capability
  grants, sensitivity, standalone-vs-integrated.
- `docs/app-platform-runner.md` (2026-06-15, **built**) — `workshop.app.json`,
  port registry, build/spawn/health, reverse proxy. Running a project as an app
  works today.
- `docs/workshop-v1/02,03,05` — Integrated / Standalone / Public surfaces, the
  marketplace with publish/install/**fork**/update, five isolation layers.
- `docs/apps-are-pluggable.md` + the handoff (2026-07-12) — the backend
  migration, done.
- `docs/decisions/0003-github-is-per-project-binding.md` (accepted 2026-07-15)
  and `github-worktree-orchestration-plan-2026-07-15.md` — GitHub G0-G3.
- `docs/app-manager-ui.md` (2026-06-13) — per-app settings, explicitly "must not
  contain per-app conditionals".

**Three things were scoped and never specified**, and they are exactly the three
that block us now:

1. **Dynamic frontend bundle injection.** Named as the blocker in eight places
   across docs and code. Designed in **zero**. No mention anywhere of module
   federation, import maps, or any mechanism. §2 of this document closes it.
2. **How the operator's apps physically leave the repo.** §4.
3. **Adopt-existing-code.** Both project-creation paths 409 on an existing
   directory, so an in-tree app cannot become a project without extraction
   first. §5.3.

---

## 1. What extraction actually requires (measured)

A full dependency graph was built in both directions. The result reframes the
work: **extraction is not "move files", it is "build the extension points that
do not exist."**

### 1.1 What apps need FROM the platform: small, and nearly done

Backend: **11 modules, ~30 import sites.** `services.ownership` (10) and
`wsplatform.paths` (7) are 17 of the 30. An app essentially needs "who owns
this" and "where is my data". That is a publishable SDK surface, not a problem.

Frontend: **11 platform-internal modules** reached by app pages, none exported
from the kit. Top: `lib/usePoll` (18 uses), `lib/time` (8), `components/appNav`
(8), `components/AppSettings` (7), `lib/storage` (6), `components/AppChatDock` (5).

**`notes-app` imports nothing outside `@workshop/ui`.** That is the proof the
rest is avoidable, and it sets the target: promote those 11 modules into the kit
and the frontend seam is a published package.

### 1.2 What the platform needs FROM apps: this is the blocker

Six inbound edges, each needing a *different* extension point that does not
exist on `AppSpec`:

| Platform site | Needs |
|---|---|
| `routes/events.py:70,93` `VERIFIERS = {"plaid": ...}` | `AppSpec.event_verifiers` |
| `services/stores.py:451,536-591` (`NoteAdapter`, `FinanceAdapter`) | `AppSpec.store_adapters` |
| `services/semantic_index.py:376` imports `notes_vault` | `AppSpec.index_collectors` |
| `services/event_dispatch.py:64-68` `if source == "plaid"` | app-supplied idempotency derivation |
| `routes/birds.py:673` imports `_load_subscriptions`, appends to `EVENTS_LOG` | a public event-publish API |
| `services/app_personas.py` (560 lines) | an out-of-tree persona loader |

And the catalog itself is closed: `_CATALOG_FACTORIES` (`app_registry.py:497`)
is a literal tuple. There is no entry-point scan, no plugin path, no
manifest-driven loader. `_build_catalog` is *tolerant* (a missing module
degrades gracefully) but not *extensible*: **nothing outside `backend/routes/`
and `backend/services/` can ever become an app.**

`services/app_personas.py` is the single worst file: 560 lines that are four
apps' business logic (snapshots, the budget plan-patch applier, four persona
classes) sitting in platform `services/`. `AppSpec.chat_provider` takes a
callable, but the callable's *code* has nowhere to live.

### 1.3 Frontend build coupling: one real bug, then mechanical

`frontend/src/apps/registry.tsx:72-74` imports three birds modules **eagerly**,
and `registry.tsx` is itself statically imported by `App.tsx`, `appNav.tsx`,
`useShell.ts`, `Apps.tsx` and `Settings.tsx`. So it is unconditionally in the
main entry chunk, and it drags in:

`birds/scope` -> `birds/BirdsShell` -> `birds/BirdsHeader` -> `birds/ui`
(**which pulls recharts**) -> `birds/api`, `birds/settings`, `birds/birdinfo`,
`birds/geocode`, `birds/specColor`, `birds/specLocalize`, and
**`birds/birds.css` (349 lines)**.

The file's own comment claims lazy imports keep an uninstalled app off the
critical path. **That claim is false for birds.** Every visitor to `/` downloads
the birds shell, header, recorder, a charting library and a stylesheet.

`vite.config.ts` has no `manualChunks` and no per-app entry, so the split is
entirely incidental and nothing would catch the regression.

### 1.4 Ranking, easiest to hardest

wearables (no code) -> health (clean leaf, zero inbound) -> notes-app (perfect
frontend, 3 inbound backend importers) -> budget (**top-level imports finance**)
-> commute (**top-level imports location**, 90 tests hard-fail on collection) ->
location (commute imports it) -> finance (deepest inbound web, in-tree MCP
server, 13 env vars) -> **birds** (eagerly bundled, data lives *inside the source
tree* at `apps/birds/birdnet.db`, reaches a private platform symbol).

Two genuine cross-app edges block independent extraction: **budget -> finance**
and **commute -> location**, both load-time, both ignoring the capability
contract their own AppSpec already declares (`"needs": ["finance.read"]`,
`"needs": ["location.read"]`).

---

## 2. The app loader

The decision no prior document made.

### 2.1 Decision: import maps + dynamic `import()` of a built ESM bundle

Not module federation. Not iframes.

**Mechanism.** An app ships a manifest and one built ESM file. Its Vite config
is `build.lib.formats: ['es']` with
`rollupOptions.external: ['react', 'react-dom', 'react/jsx-runtime', '@workshop/ui']`.
Rollup leaves those as bare specifiers. The host publishes its own React and its
own kit at stable URLs and declares them in `<script type="importmap">`. The host
does `const mod = await import(url)` and mounts `mod.default`.

**Why this and not the alternatives.**

The load-bearing reason is specific to this repo: **externalising
`@workshop/ui` alongside React is what makes "Design System is mandatory"
enforceable across code we do not compile.** The app cannot ship a divergent
copy of the kit; it physically imports the host's. There is exactly one React
instance and exactly one kit instance, with no version negotiation to get wrong.

- **Iframes cannot do this at all** (separate JS realm). The industry answer for
  *untrusted* third-party code, and correct there, but it costs the design
  system (Atlassian and MCP Apps both ship consolation-prize token vocabularies
  over postMessage), plus bidirectional URL sync with loop guards, modals that
  must be declared by the app and rendered by the host, focus escape via
  sentinels, and drag-and-drop only through a transparent overlay. **These are
  the operator's own apps. We are not paying for a trust boundary we do not
  need.**
- **Module Federation** is mature (`@module-federation/vite@1.20.7`) and
  `registerRemotes()` genuinely supports server-discovered URLs. But it buys
  version negotiation between independently-versioned remotes, and there is one
  operator building all the apps. It charges a plugin in every app build, an
  `mf-manifest.json`, a mandatory `type: 'module'`, a forced placeholder remote
  in the host config, a known `RUNTIME-009` first-load bug, and a ban on
  `manualChunks`. Revisit only if apps drift on React versions.
- **Proxied separate origin** already exists (the runner) and stays as the
  *standalone* surface. It is not the integrated one.

**Browser support is settled.** Import maps are Baseline since March 2023
(Chrome 89, Firefox 108, Safari 16.4). Two gaps, both avoidable: multiple import
maps in one document is Chrome 133+/Safari 18.4+ and **not Firefox**; external
maps via `src=` are not standardised anywhere. So: build **one** map at page
load, injected from a blocking classic script in `<head>` before the module
entry, rendered server-side from the app registry. `es-module-shims@2.8.4` is
the escape hatch if post-hoc mutation is ever needed (passes through natively
for ~94% of users, ~5ms).

**Two traps, both cheap and both fatal if missed.**
1. Vite's dev pre-bundler creates a second React that bypasses the map. Set
   `optimizeDeps: { exclude: ['react', 'react-dom'] }`.
2. Vite splits CSS into files a dynamic `import()` never loads, so the app
   renders unstyled **in production only**. The app build template must set
   `cssCodeSplit: false` or use `vite-plugin-css-injected-by-js`.

Tooling: `vite-import-maps` (Vite 6+) keeps the map in sync with content-hashed
chunk names and supports SRI. Its own caveat: every subpath needs an entry, so
`react/jsx-runtime` is not covered by `react`.

### 2.2 Steal: declare-then-activate, and named mount points

Two patterns from the survey are adopted as design rules, independent of the
loader.

**Declare-then-activate (VS Code).** The manifest describes every UI surface in
static JSON, and the host renders all of it **without executing a line of app
code**. Since 1.74 the contribution *is* the activation event, so most
extensions ship `"activationEvents": []`. This is what lets 60k extensions
coexist without a startup catastrophe. Our manifest therefore declares pages,
icon, nav entries and settings schema statically; the bundle loads only when the
user opens the app.

**Named mount points (Red Hat Developer Hub).** The host publishes a closed
vocabulary of mount points; the app's config says
`{mountPoint, importName, config.if}`. **No host source file ever names a
plugin.** This is a proven implementation of our own §6 rule, and it is the
shape the manifest should take.

Sobering data point: **Backstage did not solve this upstream.** `app.packages:
all` only filters what webpack already scanned, and their docs say plainly that
"feature discovery needs to interact with the compilation process." Runtime
loading exists only in the Red Hat fork, and even there the page must be
reloaded and the backend restarted. If Spotify could not make build-time
discovery go away, we should not assume it is easy, and we should not gold-plate
it.

### 2.3 Fallback worth knowing

Home Assistant's Lovelace custom cards are the true zero-coupling model: a JS
file calls `customElements.define("my-card", ...)`, referenced as
`type: "custom:my-card"`, resource URL in storage. The contract is the DOM
custom-element registry: no import, no bundler, no shared React. If an app ever
needs to be framework-agnostic, that is the path.

---

## 3. GitHub integration

### 3.1 The one fact that determines the architecture

**`POST /user/repos` is user-access-token only.** GitHub's permissions reference
lists it under Repository permissions -> Administration, write, **UAT only**. An
installation token (`ghs_`) gets 403 `Resource not accessible by integration` no
matter what permissions are granted. `POST /orgs/{org}/repos` accepts both.

So **a pure server-to-server GitHub App cannot create a repo in a personal
account.** Everything below follows from that.

### 3.2 Decision: a private GitHub App created via the App Manifest flow, dual-token

**Setup: two clicks, zero copy-paste.** The frontend renders a form POSTing a
JSON manifest to `https://github.com/settings/apps/new?state=...`. GitHub
redirects back with a one-time `code`. The backend calls
`POST /app-manifests/{code}/conversions` (**no auth required, the code is the
credential**) and receives `id`, `pem`, `client_id`, `client_secret`,
`webhook_secret`. **Localhost redirect URLs work.**

This is what `npx @backstage/cli create-github-app`, Coolify and Dokploy all do,
and it eliminates the single largest support cost of the manual path: users
botching a multi-line PEM paste. It directly satisfies the operator's "front end
friendly easy to handle GitHub sign-in process".

Manifest settings that matter: `public: false`,
`request_oauth_on_install: true`, `hook_attributes: {active: false}` (no
webhooks needed on a single-user box), `setup_on_update: true`. App names are
**globally unique** and the flow is **not idempotent**: generate
`workshop-{host}-{6 random}` and handle "name taken" by bouncing back with the
manifest pre-filled.

**Permissions, minimum:** `metadata: read` (mandatory), `contents: write`,
`pull_requests: write`, `administration: write` (repo creation only),
`profile: read`. Add `workflows: write` **only if** we ever scaffold
`.github/workflows/` — without it the push is rejected at the git layer with a
confusing message, not at the API layer.

**Install on "All repositories."** `PUT /user/installations/{id}/repositories/{id}`
works **only with classic PATs**, so building selective install into an App flow
forces a `ghp_` to stay alive. Repos the App creates are auto-added.

**Two tokens at runtime.**
- `ghu_` user token for `POST /user/repos`, obtained by **device flow**
  (`POST /login/device/code` -> user visits `github.com/login/device` -> poll
  with `grant_type=urn:ietf:params:oauth:grant-type:device_code`). Handles
  `authorization_pending`, `slow_down` (**add 5s**), `expired_token` at 900s.
  Needs no callback URL, which matters for a self-hosted box. **Must be
  explicitly enabled** in app settings or the endpoints return 400. Opt in to
  expiry: 8h token + 6-month `ghr_` refresh. **Refresh tokens are single-use:
  persist the new one before discarding the old or you lock yourself out.**
- `ghs_` installation token for everything else. RS256 JWT, `exp` <= 10 min,
  `iat` 60s in the past. Steal Backstage's cache policy: **reissue at 50
  minutes, never hand out a token with under 10 minutes left.**

**Storage schema warning:** installation tokens move to a stateless
`ghs_APPID_JWT` format, **~520 chars and variable length**, rolling out
April-June 2026. Use TEXT columns. No length checks, no `ghs_[A-Za-z0-9]{36}`
regexes.

### 3.3 Credential storage

What the comparables do is sobering: **Dokploy stores the private key and client
secret as plain text columns. CapRover stores plaintext env vars and echoes them
to the dashboard. Coolify encrypts only the PEM.** Nobody in this category runs
Vault; nobody uses an OS keyring.

**Decision: field-level `cryptography.fernet.MultiFernet`, master key delivered
by systemd `LoadCredentialEncrypted=`.**

- `MultiFernet` from day one so rotation is a re-encrypt loop, not a migration.
- Encrypt the fields (PEM, client secret, webhook secret, `ghr_`), not the
  database, so queries, indexes and backups still work.
- `systemd-creds encrypt --with-key=host+tpm2`, then `LoadCredentialEncrypted=`
  in the unit; read from `$CREDENTIALS_DIRECTORY` at startup. No passphrase at
  boot, key sealed to the TPM, memory non-swappable.
- **The reason this beats an env var here specifically:** systemd credentials are
  **not inherited by child processes**, and this platform shells out to `git`
  constantly. An env-var master key leaks into every `git`, `npm` and build
  subprocess and their crash dumps.
- Skip Python `keyring` on a headless box: SecretService needs D-Bus and
  gnome-keyring-daemon and *still* needs an unlock password from somewhere.
- **Persist as little as possible.** Store the PEM and installation id; mint
  60-minute tokens on demand.

### 3.4 Libraries

- **`githubkit[auth-app,auth-oauth-device]`** (0.16.1). The only Python client
  with native async **and** first-class `AppAuthStrategy`,
  `AppInstallationAuthStrategy` and `OAuthDeviceAuthStrategy` — exactly our two
  auth requirements, including the device-flow polling loop with `slow_down`
  backoff. Generated from GitHub's OpenAPI spec, Pydantic models, `py.typed`.
  Note 0.16.0 was breaking (schemas split into `githubkit-schemas`).
  PyGithub has no async (open since 2020) and no device-flow helper.
- **Git operations: shell out via `asyncio.create_subprocess_exec`.** The only
  genuinely non-blocking option, and the git binary is needed anyway.
  **Do not use GitPython.** Its README says it is in maintenance mode and "not
  suited for long-running processes (like daemons) as it tends to leak system
  resources", and **ten security advisories were published in the three weeks to
  2026-08-10** (CVE-2026-76218 through -76221 and six more), all the same class:
  kwargs forwarded to `git` as options behind an incomplete denylist. Repo
  names, branch names and URLs from our UI would flow straight into those
  kwargs. `pygit2` is fine for in-process inspection, but if you push with it
  you **must** implement `push_update_reference()` — `Remote.push()` returns
  `None` and a server-side rejection looks exactly like success.

**Passing the token to git without leaking it.** `/proc/<pid>/cmdline` is
world-readable; `/proc/<pid>/environ` is not. And a token in a remote URL
persists to `.git/config` forever (the documented `actions/checkout` leak). So:

```
GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=http.extraHeader
GIT_CONFIG_VALUE_0=Authorization: Basic <b64 of x-access-token:TOKEN>
GIT_TERMINAL_PROMPT=0
GIT_ASKPASS=/bin/true
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_SYSTEM=/dev/null
```

Remote URL stays credential-free. Never `create_subprocess_shell`. Never inherit
`os.environ` wholesale.

### 3.5 Binding lifecycle

**"Create new" and "adopt existing" are two separate, explicitly named
operations.** That is Backstage's design (`publish:github` vs
`github:repo:push`) and it is the one that holds up. Every tool that fused them
ends up telling users to delete their remote by hand.

Traps, each of which has bitten a shipped product:

- **`auto_init` / `gitignore_template` / `license_template` create an initial
  commit**, making the remote non-empty and rejecting the first push as
  non-fast-forward. For adopt-existing always `auto_init: false`.
- **Default branch.** Git's built-in default is still `master`; GitHub's default
  for repos *it* initializes is `main`. The first branch pushed to an empty repo
  becomes the remote default, so an unset `init.defaultBranch` silently produces
  `master` repos. Always `git init -b main` explicitly.
- **Check `origin` before creating anything remote.** `gh repo create` creates
  the GitHub repo *first*, then adds the remote, and on a name clash prints
  `✗ Unable to add remote` leaving an orphan repo on GitHub. Probe
  `git remote get-url origin` first; if taken, bind under a distinct name and
  **store that name**.
- **Bind on the repo's node id, not `owner/repo`.** This is exactly the Vercel
  "Git Disconnected" failure after a rename or transfer. Store
  `{local_path, remote_name, remote_url, repo_node_id, installation_id, default_branch}`.
- **Disconnect is non-destructive:** remove our remote, delete the binding
  record, remove the webhook. Leave the local repo and the GitHub repo alone.
- **Reconnect** re-resolves by stored id to the current `owner/repo` and repairs
  the remote URL. Netlify's documented repair is "link to a different repo, then
  pick the same one again" — make that idempotent repair a first-class button.
- Pre-flight: `git rev-parse --verify HEAD` (zero commits),
  `git status --porcelain` (dirty), `git ls-remote --heads <url>` (non-empty
  remote, and it proves auth and existence in one round trip). If forcing ever
  becomes necessary, `--force-with-lease --force-if-includes`, never bare.
- **Repository-level push protection is off by default.** Scan before the first
  commit; do not treat GitHub as the safety net. 50 MiB warn, 100 MiB block.

### 3.6 Settings model

Per the operator: global defaults plus per-app overrides, connectable and
disconnectable at any time, working from any direction.

**Global** (`/settings/integrations`):
- Connection state: not connected / connected as `<login>` / needs reauth.
- Connect (App Manifest flow) and Disconnect.
- `auto_create_repo` — every new app gets its own repo. Default **on**.
- `default_visibility` — private / public. Default **private**, matching
  `services/github.py`'s existing hard rule ("Platform-created repos are ALWAYS
  private. There is no public path.").
- `default_branch_name` — default `main`.

**Per app** (`/apps/<id>/settings`):
- Connected / not connected, with the bound repo and its ref.
- Connect: **Create new repo** or **Link existing repo** (two operations).
- Disconnect (non-destructive), Reconnect/Repair.
- Override visibility for this app.

**The system must work with GitHub entirely absent.** No flow blocks on it; an
unconnected app is a normal, fully functional local app.

### 3.7 Deferred, deliberately

Branches, worktrees and forks. The operator: "we will do some redesigning and
thinking about branches and work trees later. Let's not get super deep into
that. Let's just get a basic version of it working here." G2/G3 of the
2026-07-15 plan (fetch-before-work, worktree-per-task, rebase + `rerere`, the
Repo panel) stay designed and unbuilt.

**Noted for later, not now:** "sign in with GitHub" as an auth provider should
plug into the same integration, alongside sign-in-with-Google.

---

## 4. How the operator's apps leave the repo

**Decision: a private overlay repo mounted at `apps/`, then per-app repos as
each earns one.** Taken rather than escalated, per instruction.

Reasoning: it removes every app name from the tracked platform in one move,
which is the actual requirement, without paying for seven repos up front and
without losing the birds Pi rig's git history (an untracked tree would).
Per-app repos remain the end state and each app splits out when it gets its own
GitHub binding under §3, which is the natural moment.

Ordering constraint from §1.4: **budget cannot leave without finance, and
commute cannot leave without location**, because both import their peer at
load time despite declaring the capability in their own AppSpec. Those two edges
get fixed (routed through the Store/capability layer) before or during
extraction, not after.

Special case: **birds' data lives inside the source tree**
(`apps/birds/birdnet.db`, `apps/birds/clips/`, via `birds_store.py:30-31`).
`BIRDNET_DB` and `BIRDNET_CLIPS` overrides exist, so this is an ops step, but it
must be done in the same change or the detection database moves with the code.

---

## 5. Sketch: things the operator raised that are NOT this pass

Recorded so none of it is lost, per instruction. Each becomes its own phase.

### 5.1 Settings redesign (partly started, see §6)

The operator's audit list, verbatim in substance: the **Health** panel is "a
leftover old thing from when this was a one person app" and belongs to the
health/wearables app; **events, agents, base apps and services** all need
revisiting, moving, integrating or removing; **"the system"** section may not be
relevant to a cloud-hosted production version at all; **GPU memory** and **the
broker** are "a specific thing to my particular distribution and should not be
included in this at all."

He is right about the broker: `gpubrokerd` is an external daemon at
`127.0.0.1:8447` that Workshop is only a client of, with a hardcoded 22500 MiB
budget. It cannot exist in a cloud deploy.

**And Events and Services are already dead on his origin, which settles them.**
Measured 2026-08-20 against the built origin with a real session: `/api/services/`,
`/api/events/recent` and `/api/events/subscriptions` all return **404**. They
return 200 on loopback. The cause is deliberate and correct:
`workshop_public_app.py:56-67` blocks `/services` and `/events` at the public
edge because they are loopback-trusted with no user auth, and proxying them
would hand them the edge's loopback trust (the same class of bug as the
security-audit H1 `/auto-pr` typo, which that comment records).

So those three panels have never worked on the origin the operator actually
uses. They rendered an error state inside the Settings blob and nobody saw it,
because the blob was long and they were at the bottom. That is a stronger
argument for removing them than the product one: they are not a section that
might not belong in a cloud deploy, they are a section that does not function
in this one.

Not removed in this pass, because the operator was explicit that the
section-by-section decisions belong to the settings phase and not to this one.
They are quarantined under the honestly-named "This host" tab in the meantime.

Full audit already done (§6 below covers the structural half). The
section-by-section product decisions are a later phase: "we shouldn't dive into
that when we really do settings."

### 5.2 App import by folder or zip drop

New, and the operator marks it low priority: "we probably should also add in a
way to sort of import them by like a folder drop or a zip file drop or some
other kind of handoff format." Fits cleanly as a third source alongside
create-new and import-from-GitHub, once §5.3 exists.

### 5.3 Adopt-existing-code

Today `POST /projects/` scaffolds into a new dir and `POST /projects/import`
clones into a new dir; **both 409 on an existing directory**, and there is no
app<->project link field. So an in-tree app cannot become an app record without
extraction first. This is a genuine gap in the prior plan and a prerequisite for
both §4 and §5.2.

### 5.4 Marketplace

Designed in `workshop-v1/03` with publish/install/**fork**/update over git
bundles, built locally in `services/registry.py`, remote deferred pending
hosting. Untouched here.

---

## 6. Stages

**0. Measure.** `scripts/check_no_app_names.py`: a ratchet that reads the app
roster from the registries rather than hardcoding one, scans the whole tracked
tree, and reports a count by category. Pinned at the measured baseline, wired
into `reviewer.sh`. **This lands first**, because §0.2 is the reason this
problem survived: without honest measurement, every later stage can claim
success. *(Built in this pass.)*

**1. Stop the bleeding in the bundle.** Lazy-wrap `BirdsLayout` so birds leaves
the main chunk; move `.num-in`, `.sec-h`, `.tabs` out of the platform stylesheet
into their apps; add `manualChunks` so the split is asserted rather than
incidental. *(Built in this pass.)*

**2. Icons become data.** Remove every `import { Bird } from "lucide-react"`
from platform source. An icon is a declared value: a named token, an uploaded
asset, or a 1-2 letter monogram fallback derived from the name. The operator:
"in no way is the specific app icon choice something that should be built in."
*(Built in this pass.)*

**3. Settings becomes a place.** Its own dock tile and page column, one route
per tab, with `Integrations` created as the home for §3's GitHub surface and
`Advanced / This host` created as the quarantine for everything in §5.1 that a
cloud deploy must not render.

**4. The six AppSpec seams** (§1.2), plus promoting the 11 frontend modules into
`@workshop/ui`. This is the platform-design project that unblocks everything
else. Fix budget->finance and commute->location here.

**5. The loader** (§2): manifest schema, host import map, registry endpoint,
dynamic mount. Declare-then-activate, named mount points.

**6. GitHub** (§3): App Manifest flow, device flow, `githubkit`, encrypted
credential fields, create/adopt/disconnect/reconnect, global and per-app
settings.

**7. Extraction** (§4): the overlay repo. Ratchet the gate to zero.

**8. Per-app page column** including per-app Settings, which is the operator's
outstanding complaint and depends on stages 3 and 4.

Stages 0-3 are built in this pass. Stage 4 (the six AppSpec seams) is next, and
it is the one that unblocks extraction.

---

## 7. Quality bar (applies to every stage)

- **Correctness.** No stage may leave a control that does not work. The operator's
  standing rule: if it does not work, do not build it, flag it.
- **Performance.** Stage 1 is itself a performance fix. The loader must not
  regress first paint: declare-then-activate means no app code runs until an app
  is opened.
- **Security.** §3.3 for credentials. Apps stay same-origin and are therefore
  *trusted* code; that is a deliberate decision (§2.1) and it means the app
  registry is an admin-only write surface. Revisit only if third-party apps
  become installable, which is the point an iframe boundary earns its cost.
- **No new stubs, no fake wiring, single-operator** (no multi-tenant guardrails).
