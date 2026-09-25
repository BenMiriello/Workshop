# What an app artifact is

- Date: 2026-08-24
- Status: SPEC. This is the missing content of step 1 of
  `hosting-apps-design-2026-08-24.md`, which asserted "an app is an ARTIFACT, not
  a directory" and then defined none of it. The independent review
  (`hosting-design-review-2026-08-24.md`) listed twelve unanswered questions in
  its section 4 and called this "the hole the whole plan rests on". This document
  answers those twelve, plus the origin/plugin conflation the review named as the
  deeper problem.
- Measured against: `deployment-surface-inventory-2026-08-24.md`, and against the
  tree on 2026-08-24. Every number below labelled "measured" was read out of the
  working tree, `.runtime/`, or `/proc` today.
- Does NOT decide: whether Workshop is ever hosted for other people, whether
  Cloudflare is the second provider, or whether the four in-process Python apps
  ever leave the platform process. Those are section 8 and no ticket is filed for
  them.

---

## 0. The three-part relationship this replaces a slogan with

The hosting design's slogan was two sentences that say different things:

> "Nothing that serves traffic reads the operator's working tree." Correct.
>
> "Break the directory identity." Not the same statement, and taken literally it
> breaks the editing surface.

Measured: four modules resolve an app to a directory and three of them are about
EDITING, not serving. `app_files.py:140` and `:210` (the Files tab),
`worktrees.py:72` (branch worktrees), `chat_context.py:1198-1235` (the L2 build
prompt), plus `scripts/preview_config.py:170`. An agent edits source. It cannot
edit an artifact. Those call sites must keep resolving a directory.

So the model is three objects and two arrows, not one identity swap:

    SOURCE TREE  --build-->  RELEASE (a set of ARTIFACTS)  --deploy-->  DEPLOYMENT
    editable, mutable        immutable, content-addressed     mutable pointer
    the working tree         the artifact store               has an address

- **Source tree** is what the operator and agents edit. It stays exactly where it
  is, resolved exactly as it is today. Nothing in this spec moves it.
- **Artifact** is an immutable blob produced by a build. Defined in section 1.
- **Release** is the immutable set of artifacts one build produced, plus the
  manifest snapshot that produced them. This is the unit you deploy and roll back
  to. Defined in section 4.
- **Deployment** is a mutable pointer from (app, environment, provider) to a
  release, carrying an address. It is the first-class Deployment that
  `app-is-the-entity-2026-08-20.md` line 171 already declares and the current
  provider seam skips.

Everything below fills those in.

---

## 1. What an artifact is

### 1.1 The format

**An artifact is a reproducible tar stream of a file tree, plus a typed
descriptor, addressed by the sha256 of the uncompressed tar.**

Concretely, and this is the whole format:

    digest      sha256 over the UNCOMPRESSED tar byte stream, so a change to the
                compression level never changes identity
    archive     POSIX pax tar, then zstd. Normalized so two builds of identical
                content produce identical bytes:
                  - entries sorted by path, bytewise
                  - mtime 0 on every entry
                  - uid 0, gid 0, uname "", gname ""
                  - mode masked to 0644 for files, 0755 for directories and for
                    files whose source mode had any execute bit
                  - no xattrs, no ACLs, no device nodes, no hardlinks, no symlinks
                    that escape the root
    descriptor  a JSON object stored beside the archive, and hashed into the
                RELEASE digest but not into the artifact digest:
                  { "kind": "<one of the closed set below>",
                    "digest": "sha256:...",
                    "size": <bytes of the uncompressed tar>,
                    "entry": "<kind-specific, see 1.2>",
                    "built_from": { "source_ref": "...", "command": "..." } }

Reproducible normalization is not decoration. It is what makes "did anything
actually change" a digest comparison rather than a rebuild, and it is what makes
section 4's dedupe (a UI-only change reuses the previous service artifact
verbatim) true instead of aspirational.

### 1.2 Why tar and not an OCI image

Both were considered against this repo's actual apps.

**OCI image, rejected as the platform-level format.** Three measured reasons:

1. There is no image build in the tree today to extend. `compose_gen.generate`
   writes no `build:` key for any app service and no Dockerfile exists for one;
   the `build=True` passed to `container_runtime.up` at `app_runner.py:960` has
   nothing to build (inventory 1.1.5). Choosing OCI means writing a Dockerfile
   generator, a local registry or a `docker save`/`load` path, and a base-image
   policy, all before the first artifact exists.
2. It cannot express a UI bundle. The shell's loader does `await import(url)` on
   an ESM file (`stage-5-app-loader-plan-2026-08-24.md` 2.4). You cannot
   `import()` an image layer. An OCI-only answer strands the one artifact kind
   this repo is closest to actually building.
3. It is not portable to the provider the hosting design prefers. Cloudflare
   Workers take a script upload with bindings, not an image.

**Tar plus descriptor, chosen.** One mechanism covers all three kinds; it is
trivially content-addressable; the shell's asset route can serve files straight
out of an extracted tree with no new machinery; and a provider that wants an OCI
image or a Worker upload can produce one FROM the tar. The review guessed at this
shape in its section 4.1 ("a content-addressed manifest of files plus an
entrypoint descriptor, and each provider knows how to turn that into its own
thing"). This spec commits to it and pins the details the guess left open.

The rule that follows, and it is the point of the seam: **the platform produces
and stores tars. A provider may translate a tar into its own native shape at
publish time. The translation output is provider-private and is never the
artifact.**

### 1.3 The closed set of artifact kinds

One artifact shape cannot cover a Python module that `backend/main.py` mounts and
a static frontend bundle, and pretending otherwise is what the review caught. So
the kind is declared, the set is closed, and each kind has a different consumer.

| kind | tree contains | `entry` | who consumes it | has an origin? |
|---|---|---|---|---|
| `ui-bundle` | the built ESM module, its stylesheets, and nothing else | the ESM file, relative to the tree root | the SHELL, via `loadApp()` and `GET /api/base-apps/ui/{slug}/{path}` | **no.** It runs inside the shell's document, on the shell's origin, under `require_operator` |
| `service` | the runnable file tree of one long-lived HTTP program | the start command | a HOST PROVIDER, which runs it | yes |
| `site` | a static file tree served over HTTP | the index document, and the SPA fallback flag | a HOST PROVIDER or a CDN | yes |

Three kinds. No fourth. A `worker` kind is deliberately absent: a Cloudflare
Worker is a `service` artifact that a cloudflare provider translates at publish
time, not a separate thing the platform knows about.

### 1.4 The fourth thing, which is not an artifact, stated plainly

Measured: `.runtime/installed-apps.json` holds eight ids
(`finance, budget, birds, location, commute, health, wearables, notes-app`).
`app_registry.py` builds an `AppSpec` for each, and `main.py` mounts their
routers into the platform process. Four of them (`birds`, `budget`, `commute`,
`location`) have a directory under `apps/`; the others are route modules only.
Measured: `apps/<slug>/workshop.app.json` declares NO services at all; its whole
content is a name and a comment explaining that it exists so the Files tab
resolves the right directory.

**An in-process Python module is not a hostable artifact and this spec does not
make it one.** An `AppSpec` contributes routers, MCP servers, event verifiers,
store adapters, index collectors, lifespan hooks and background loops
(`app_registry.py:70-160`) into the platform's own process. There is no artifact
format that runs any of that on a host provider without first making it an
out-of-process program, which is a separate and large piece of work
(review 7.7).

What this spec does instead, and it is the honest move:

- The manifest declares `deploy.kinds`, the list of artifact kinds this app
  produces. An app that produces none is a **platform extension** and the
  platform can now TELL that from "this app's build has not run yet", which today
  it cannot.
- An extension app may still declare a `ui-bundle`. That is the actual near-term
  path for `birds`, `budget`, `commute`, `location`, `finance`, `health` and
  `notes-app`: their UI becomes an artifact under stage 5 while their backend
  stays a Python module in the platform process. Section 5 is where that lands.
- Nothing in this spec strands them, because an app's artifact set is a set. An
  app with only a `ui-bundle` is fully served. An app with only a `service` is
  fully served. `wearables` (measured: an `AppSpec` with an external `href`, no
  route module, and three loopback service probes) has neither, and is the
  escape-hatch case: a deployment whose provider is `external` and whose release
  is empty.

---

## 2. What builds an artifact

### 2.1 Input: a source snapshot, never the live working tree

The builder takes `(app, source_ref)` where `source_ref` is one of:

    git:<40-hex sha>        a commit in the app's repo. The canonical form.
    tree:<12-hex content-id>  an uncommitted working tree, snapshotted by content

`tree:` exists because the operator builds uncommitted work, and refusing that
would make the artifact path useless for the case it is needed in most. The
content-id is computed exactly the way `scripts/ui_source_id.sh` already computes
one for the frontend bundle: a sha over relative paths and contents of the
declared inputs, INCLUDING untracked files, because a new file that has not been
committed still changes what the build emits. That script's header already
argues this case; reuse the argument and the shape, do not invent a second one.

The builder materializes the snapshot into a **disposable worktree** at
`.runtime/builds/<build_id>/src`:

- `git:` -> `git worktree add --detach <path> <sha>`. The tree already knows how:
  `worktrees.py` owns branch worktrees, and the deleted `deploy_runner._prepare_workdir`
  used the same call. Reuse `worktrees.py`, do not resurrect `deploy_runner`.
- `tree:` -> a copy of the declared inputs into the same path.

The build never runs in `projects/<slug>` or `apps/<slug>`. That is what makes a
build repeatable, and it is also what stops a build from writing into the
operator's checkout, which is what a `bun install` or `pip install` in the
current `_run_build` does today.

### 2.2 Where the build runs

Measured today: `_run_build` (`app_runner.py:570`) is `subprocess.run(["/bin/bash",
"-c", spec.build], cwd=<in the tree>)` on a daemon thread inside the API process,
with the FULL host environment plus the app's secrets, capped at
`BUILD_TIMEOUT` (measured default 1800s).

For the local provider, phase 1: **the build runs as a subprocess of the API
process, in the disposable worktree, with the same 1800s cap.** Not in a
container.

That is a deliberate scoping call and it needs its reason stated, because the
review flagged it: "If the build still runs there, the artifact is a cosmetic
change."

It is not cosmetic, and the distinction is which property you actually need:

- The property the hosting design's slogan demands is **nothing that SERVES
  traffic reads the operator's working tree**. Building out of a disposable
  worktree and serving out of the artifact store satisfies that completely.
- Containerizing the BUILD is a different property (build isolation), and buying
  it in phase 1 costs a base-image policy, a toolchain-cache design and a
  network-egress policy for `bun install`, none of which the artifact model needs
  and all of which are the actual hard parts of a build service.

So: phase 1 builds in a subprocess out of a throwaway worktree. The builder is a
module with one function and no knowledge of where it runs, so the container
version is a later swap of one call, not a rewrite. State this in the ticket so
nobody "improves" it into a container in the same change.

Build environment: the scoped env (`local_runner.ENV_ALLOWLIST`, measured: 11
vars plus PORT) plus the manifest's non-secret `env` plus the declared secrets
for that service, exactly as `_run_build` resolves them today. Do not widen it to
`os.environ` the way the current code does; a build that needs a registry
credential declares it as an `env_requirement`.

### 2.3 Output

One build produces one **build record** and zero or more artifacts:

    { "build_id":       "bld_<ms>_<hex>",
      "app":            "<slug>",
      "source_ref":     "git:<sha>" | "tree:<id>",
      "started_at":     iso8601,
      "ended_at":       iso8601 | null,
      "status":         "running" | "succeeded" | "failed" | "timeout" | "cancelled",
      "steps":          [ { "kind": "...", "command": "...", "status": "...",
                            "exit_code": int|null, "duration_s": float } ],
      "artifacts":      [ <descriptor from 1.1, one per kind> ],
      "manifest_digest":"sha256:...",   // over the normalized manifest bytes
      "resolved_deps":  [ <sanitized dep blocks, see 2.5> ],
      "log_digest":     "sha256:...",   // the build log, itself content-addressed
      "release":        "<app>@<n>" | null,
      "failure":        { "step": "...", "reason": "...", "log_offset": int } | null }

A release is created only when EVERY declared kind produced an artifact. A
partial build has `release: null` and is not deployable. That single invariant is
most of section 3.

### 2.4 Identity: content hash AND version, both, for different readers

- The **address** is the content hash. `sha256:9f2c1b7e...` for an artifact,
  `release:sha256:...` for a release (section 4.1). Machines use this. It is what
  makes immutability structural and dedupe free.
- The **name** is a per-app monotonic release number: `notes-app@r7`. Humans use
  this. The operator reads it on a phone; nobody says a sha out loud.

Both, not one. The repo already does exactly this and it works: `build-info.json`
carries `source_id` (content) alongside `commit_short` and `branch` (names), and
`scripts/stamp_build.sh`'s own header explains why the content id is the one that
cannot lie. Same reasoning, same pair.

### 2.5 Where artifacts are stored

Measured: the platform has no object storage of any kind today. This introduces
the first one, and for the local provider it is a directory.

    .runtime/artifacts/blobs/sha256/<first-2-hex>/<full-hex>.tar.zst
    .runtime/artifacts/descriptors/sha256/<first-2-hex>/<full-hex>.json
    .runtime/artifacts/releases/<app-slug>/<n>.json          the release record
    .runtime/artifacts/releases/<app-slug>/index.json        n -> release digest, latest
    .runtime/builds/<build_id>/{src,out,log}                 disposable; src and out
                                                             are deleted on success

Blobs are written to a temp path and `os.replace`d into position, so a torn write
never becomes a valid-looking digest. A blob whose path already exists is not
rewritten: content addressing means the write is a no-op, and skipping it is what
makes an unchanged rebuild cheap.

The store is reached through one module with four operations (`put`, `get`,
`exists`, `delete`) so a provider that stores blobs in R2 or S3 swaps the module
and nothing else. Do not spread `Path` joins over the artifact root across the
codebase; that is how `.runtime/deploys.json` ended up with two writers and two
schemas (measured: `local_runner.RUNTIME_STORE` and `deploys.STORE_PATH` are the
same file).

**Resolved deps are captured at build time, not read at runtime.** Today
`compose_gen._load_dep_block` reads the project's own `docker-compose.yml` from
the working tree every time an app starts (measured: <app>'s manifest points at
`core/docker-compose.yml` for postgres, redis and falkordb). That is a serving-path
read of the operator's tree and it must stop. The builder reads that file once,
sanitizes each block exactly as `_sanitize_dep_block` does, and stores the result
in the release record. The runtime reads the release.

### 2.6 Which manifest is authoritative at runtime

**The one in the release. Always.**

Measured today, this is the field the current code gets most wrong:
`resolve_manifest` re-reads `workshop.app.json` off disk on every `app_status`,
`manifest_dict`, `env_overview`, `start_app`, `stop_app` and `restart_service`
call (measured: 8 call sites of `_project_dir` inside `app_runner`). An app's
running behaviour therefore changes the moment someone edits a file, with no
build and no deploy.

After this change: the serving path reads `release.manifest`. The source manifest
is the INPUT to the next build and the thing the Environment panel and the L2
chat prompt describe, which is correct, because those surfaces are about what you
are editing.

---

## 3. Failure semantics

### 3.1 What a failed build produces

A build record with `status: failed` (or `timeout`), a populated `failure` object
naming the step, and a retained log blob. **No release.** No artifact descriptor
is published for a step that did not complete, even if earlier steps of the same
build succeeded: the artifacts of a failed build are written to
`.runtime/builds/<id>/out` and deleted with it, never promoted into the blob
store.

Reason: the alternative is a release whose `ui-bundle` is new and whose `service`
is old, which is the exact shape of "the frontend calls an endpoint the backend
does not have yet" and is not detectable after the fact.

### 3.2 What the previously good release does

**It keeps serving, untouched.** This is structural rather than a policy anyone
has to remember: a deployment points at a release digest, and a failed build
never writes a deployment. Nothing is stopped, nothing is regenerated, no
container is recreated. The proxy keeps targeting the same address.

For a `ui-bundle` specifically, the shell keeps serving the previously deployed
bundle at its existing hashed URL, and because stage 5's contract is
declare-then-activate, the app's dock tile, launcher card, breadcrumbs and icon
were never at risk in the first place.

### 3.3 What the operator sees

On the app's page, a Builds list, newest first. The failed build shows:

- the release it did NOT become, as an absence: "no release produced"
- the failing step by artifact kind and command, verbatim
- the exit code, or "timed out after 1800s"
- the last 40 lines of the log inline, and a link to the whole log
- beside it, the still-serving release: `notes-app@r6, serving since 2026-08-19`

That last line is the one that matters and it is the one every platform gets
wrong by omission. A failed build with no statement of what is still up reads as
an outage.

Design-system note for whoever builds the screen: this is `StateBlock
variant="error"` for the region plus the five status kinds for the row, per
`frontend/CLAUDE.md`. Not a toast. The failure persists until a later build
succeeds, so it is state, not an event.

### 3.4 Where build logs live

`.runtime/builds/<build_id>/log` while running, then hashed into the blob store
and the directory deleted. Retained with the build record for as long as the
build record is retained (section 4.4). NOT in `.runtime/app-logs/`, which is
runtime output with a different lifetime and, measured, no rotation at all.

A build log is a build artifact in the informal sense but NOT an artifact in this
spec's sense: it has no kind, it is never deployed, and it does not gate a
release. It is a blob in the same store, referenced by digest from the build
record.

---

## 4. Identity, immutability and rollback

### 4.1 How a release is addressed

    release_digest = sha256( canonical_json({
        "app": <slug>,
        "artifacts": [ {"kind": k, "digest": d} sorted by kind ],
        "manifest_digest": <sha256 of normalized manifest bytes>,
        "resolved_deps_digest": <sha256 of the canonicalized dep blocks>
    }) )

Note what is NOT in it: the build id, the timestamps, the source ref, the log.
Two builds of the same source at different times produce the same release digest,
which is the property that makes "nothing changed" observable. The build record
records which build produced it first.

Note what IS in it: the manifest and the resolved deps, because a release whose
service artifact is identical but whose start command or postgres block differs
is a different release and must not collide.

### 4.2 Immutability

An artifact is immutable by construction: its name is the hash of its bytes. A
release is immutable by policy and by the same construction. Neither is ever
rewritten. The blob store refuses a write to an existing digest with different
bytes, which cannot happen, and treats a write with identical bytes as a no-op.

A **deployment** is mutable, and it is the only mutable thing in this model:

    { "deployment_id": "dep_<ms>_<hex>",
      "app":           "<slug>",
      "environment":   "prod" | "preview",
      "provider":      "local" | "external",
      "release":       "release:sha256:..." ,
      "release_name":  "<app>@<n>",
      "address":       "<provider-shaped, see below>",
      "state":         "<see 4.5>",
      "deployed_at":   iso8601,
      "previous":      "<deployment_id>" | null }

Two deployments may reference one release (a preview and a prod of the same
bytes). Two releases may reference one artifact (a UI-only change reuses the
`service` artifact digest verbatim). Both fall out of content addressing and both
are the payoff.

### 4.3 Rollback

Rollback is: write a new deployment row pointing at an older release digest, then
have the provider publish it. No rebuild, no source checkout, no git operation.
It is O(1) and it is available for every retained release.

This works only because the seam is keyed on a deployment rather than on an app.
The hosting design's proposed seam (`publish(artifact) -> address`,
`status(app)`, `destroy(app)`, `logs(app)`) cannot express it, which is the
review's finding 5.1. The corrected shape, stated here because the artifact model
is what makes it possible and not because this document owns the seam:

    build(app, source_ref)          -> build_record            (platform, not provider)
    provision(app, requirements)    -> bindings                 (before publish; its
                                                                 outputs are publish inputs)
    publish(deployment, release, bindings) -> address
    status(deployment)              -> state
    destroy(deployment)
    logs(deployment, since)         -> stream

`rollback` is deliberately NOT a verb. It is `publish` with an older release, and
having it as its own verb is how a provider ends up with two code paths that
diverge. The deleted `deploy_providers/base.py` had a separate `rollback` and
that is the one piece of its prior art not worth keeping.

### 4.4 Retention and garbage collection

Keep a release if any of:

1. a live deployment references it, in any environment, or
2. it is one of the most recent N releases of its app (N default 10), or
3. it is younger than 30 days.

Sweep everything else: the release record, the build records that produced only
it, and then any blob with no remaining referent. Blob sweep is refcount-by-scan,
not a stored refcount, because a stored refcount is a second source of truth and
this store is small enough that a scan is cheap. Run it on demand from an
endpoint, not on a timer: measured, the last timer this subsystem had
(`deploy_watcher`) has logged `not-autonomous` 18 times every five minutes since
it was written and has never done anything else.

N and the 30 days are operator numbers. Section 8.

### 4.5 The status vocabulary both providers can populate

The review's finding 5.5 is that the current seven-status service vocabulary
(measured at `app_runner.py:690`: `pending, building, starting, running, failed,
stopped, disabled`, plus `crashlooping` from the container mapping at `:832`) is
populatable only by a process supervisor. A Worker has no restart count and no
OOM kill.

Split it, and the artifact model is what makes the split natural:

- **Deployment state**, which every provider can answer:
  `pending | publishing | live | failed | destroyed`. Five words. This is what the
  app page shows.
- **Runtime detail**, optional, provider-shaped, rendered only when present: the
  per-service rows, restart counts, OOM kills, exit codes and health that the
  local provider genuinely has. A provider that has none returns none and the UI
  shows nothing rather than structural nulls.

Keep the distinction the inventory calls hard-won: restart counts mean CRASHES,
never operator restarts (`app_runner.py:1125-1129`). It belongs in runtime detail.

---

## 5. The UI bundle and the hosted program: two artifacts, one release

**This is the question the review called the most important, and the answer is:
they are two different artifacts, bound together by one release, and they must
never be merged into one.**

### 5.1 Why they are not one artifact

Measured, from `stage-5-app-loader-plan-2026-08-24.md` sections 2.1 to 2.4 and
3.2, against what a hosted program is:

| | `ui-bundle` | `service` / `site` |
|---|---|---|
| what it is | an ESM module whose default export is `{contract: 1, exports: Record<string, ComponentType>}` | a program, or a tree of files |
| how it is built | `defineWorkshopApp` lib build, `formats: ["es"]`, `cssCodeSplit: false`, React / router / lucide / `@workshop/ui` **externalized** | its own toolchain, everything bundled in |
| how it resolves imports | only through the single import map injected into the SHELL's `index.html`. It cannot resolve its own imports in any other document | independently. It has no import map |
| who loads it | the shell, `await import(url)`, memoized per app id | a host provider, which starts a process or uploads a script |
| what origin it is on | the shell's. It has none of its own | its own hostname |
| how it is reached | `GET /api/base-apps/ui/{slug}/{path}?v=<hash>`, inheriting `require_operator` from the `/base-apps` mount | a public or tailnet address, with its own auth |
| how it gets platform services | `useAppHost()`, a React context the shell provides | an HTTP call with a token it was issued |
| blast radius when broken | one route region shows `StateBlock variant="error"`; the shell, its nav and every other app are untouched | an API is down |
| what a version bump means | `contract` mismatch or a `kit` semver miss refuses the bundle BEFORE fetching it | nothing; it is a separate program |

Nine rows and they disagree in every one. A single artifact shape that covered
both would have to be the union of a plugin contract and a program contract, and
the first thing anyone would do with it is ignore half the fields.

### 5.2 Why they are one release

Because the UI calls the API. A `ui-bundle` built against a `service` that
declares `GET /widgets/summary` must not be deployed next to a `service` that
does not. Binding them into one release makes that impossible to get wrong:
they are built from one source snapshot, they are promoted together, and rollback
takes both back.

That is the entire justification for the Release object. Without a multi-kind
app, Release would be a synonym for Artifact and should be deleted.

### 5.3 What this corrects in the hosting design

The hosting design's section 4.1 "Tier 1, static: a UI bundle on object storage
behind a CDN. Effectively zero per app. Most apps are only this, and the app
loader is what makes it possible" collapses two different things, and the review
was right that it is incoherent as written.

Corrected:

- **Serving a `ui-bundle` is not hosting.** It is the shell fetching a plugin
  asset from its own authenticated origin. There is no tenant, no origin, no
  compute, no idle cost, and no per-app economics, because the thing being served
  is part of the operator's own Workshop page. The "effectively zero per app"
  claim is true and vacuous.
- **Serving a `site` IS hosting**, and it is what tier 1 should have meant: a
  static tree at its own origin, reachable by people who are not the operator.
  Measured: no app in this repo has one today. <app> is closest and currently
  runs a vite DEV server, not a built site (section 7).
- Therefore the hosting design's step 2 ("Tier 1 only") is two separate pieces of
  work with almost nothing in common, and the one it names (the loader) is the
  one that is not hosting.

### 5.4 Where the ui-bundle bytes come from, once releases exist

Stage 5 as planned serves from `apps/<slug>/dist/ui/`, hashing the file bytes
server side and returning `?v=<hash>` URLs
(`stage-5-app-loader-plan-2026-08-24.md` 2.3 and 3.3). That is correct and should
ship as planned, because it is not blocked on any of this.

The change this spec makes to it, later and small: `GET
/api/base-apps/ui/{slug}/{path}` resolves its root from **the app's live
deployment's `ui-bundle` artifact** instead of from `scope_root("app:<slug>")/<ui.dir>`.
Everything else about the route is unchanged: same URL shape, same `?v=`, same
`immutable` cache header, same containment check, same content-type refusal. The
`?v=` becomes the artifact digest instead of a per-file hash, which is strictly
better because it changes atomically for the whole bundle.

That is one function body. Say so in the ticket, because it is the reason stage 5
does not need to wait for this spec and this spec does not need to wait for
stage 5.

### 5.5 The consequence for auth, named and not solved

A `ui-bundle` runs in the shell's document and therefore inherits the operator's
session for free. A `service` or `site` runs on its own origin and therefore
inherits nothing, so it needs a token exchange.

This spec does not design that exchange. It does state the invariant that the
artifact model makes checkable: **an artifact of kind `service` or `site` never
inherits the shell's session, and a provider must never be given one.** The
current proxy violates the spirit of this by serving apps from the platform's
origin under a path prefix while stripping CSP and `X-Frame-Options`
(measured, `base_apps.py:667-670`). That is a known defect, it is out of scope
here, and it must not be extended to a release-served app.

---

## 6. What breaks when directory identity goes, call site by call site

Every row is measured from the inventory or from a grep run today. "Replaced by"
is what the call site does after the change, not a promise that it is easy.

### 6.1 The serving path: these must stop reading the working tree

| # | call site | what it assumes today | replaced by |
|---|---|---|---|
| 1 | `compose_gen.py:213`, `volumes: [f"{abs_cwd}:/app"]` | the app IS `~/workshop/projects/<slug>/<cwd>` | the release's `service` artifact, extracted once to `.runtime/releases/<digest>/`, bind-mounted **read-only** at `/app`, plus a named volume for the app's writable scratch. Same compose mechanics, immutable source |
| 2 | `compose_gen._load_dep_block` (`:118`) reading the project's own `docker-compose.yml` | the dep blocks live in the working tree at start time | `release.resolved_deps`, captured and sanitized at build time (2.5) |
| 3 | `app_runner._project_dir` at `:708`, `:911`, `:973` (bring-up), `:1088`, `:1147`, `:1216` | one function answers both "where do I edit" and "where do I run" | split into `source_dir(slug)` (unchanged body, used by the build input and the editing surfaces) and `release_root(release_digest)` (used by everything in the serving path). These six call sites take `release_root` |
| 4 | `app_runner.resolve_manifest` at `:461`, `:990`, `:1084`, `:1146`, `:1260`, `:1304` | the live manifest on disk governs a running app | `release.manifest` for the serving path. `resolve_manifest` survives for the build path and the editing surfaces (2.6) |
| 5 | `app_runner._run_build` (`:570`) | build happens in the tree, on a daemon thread in the API process, with the full host env | the builder module (2.2), in a disposable worktree, with the scoped env |
| 6 | `app_runner._reap_project_orphans` (`:641-680`) | processes are identifiable by a `/proc` cwd prefix under the project dir | **deleted.** A release-run process never has a cwd in the tree, so the scan would match nothing. `docker compose down` on the release's compose project replaces it |
| 7 | `app_runner._reflect_primary_surface` (`:796`) writing `surfaces.standalone` into `.runtime/projects.json` | "where is this app running" is a field on the projects registry | the Deployment record's `address`. The `integrated` and `public` slots, measured as never written by anything, are deleted |
| 8 | `base_apps.py:658`, the proxy targeting `http://127.0.0.1:{port}` from `app_status` | an app is a loopback port found by reading live runner state | the Deployment's `address`. For the local provider that is still a loopback port, but it is read from the deployment, not derived from a manifest re-read |
| 9 | `.runtime/app-deploys.json`, keyed by slug | one app, one running thing, forever | deployment records keyed by `deployment_id`, so preview and prod coexist. Measured: the live file has exactly one app, frozen since 2026-07-05 |
| 10 | `port_registry` `app` window 8700-8799 | ports are the addressing scheme | **kept, narrowed.** It becomes a local-provider implementation detail that mints the local address. The hosting design's "REPLACE port_registry" is wrong: measured, `dev_servers.py` (the 8500-8599 preview window) and `presence.py` (`process_alive`) import it for reasons unrelated to hosting. Only the dead `deploy` window 8600-8699 goes |
| 11 | `AppServicesPanel.proxyHref` (`:72-78`), the hardcoded 8443->8446 and 5173->8000 map | one machine's tailscale layout, compiled into the frontend | the Deployment's `address`, returned by the API. Two magic numbers deleted |
| 12 | `app_runner.is_self_project` (`:58`) and the `_SELF_MSG` 409, at `:988`, `:1046`, `:1082`, `:1138`, `:1254`, `:1298` | the platform and the workload share a checkout, so one app must be special-cased by a registry flag | narrowed and re-justified. The structural answer becomes "this app declares no deployable artifact kinds, so there is nothing to deploy". Keep the explicit refusal for the error message; it stops being the safety mechanism |

### 6.2 The editing path: these must NOT change, and the slogan would have broken them

| # | call site | why it keeps resolving a directory |
|---|---|---|
| 13 | `app_files.py:140` `project_root()` -> `build_agents._project_dir` | the Files tab edits source. An artifact is not editable |
| 14 | `app_files.py:210` `_app_declared_root`, `paths.scope_root("app:<slug>")` + `_read_manifest_file` | this is the fix for the measured `app:<slug>` two-roots defect (`projects/<slug>` 118 files vs `apps/<slug>` 30,150 files). It reads the manifest's PRESENCE as a declaration of which directory the app is. Unchanged |
| 15 | `worktrees.py:72`, `_read_manifest_file` and `AppError` | branch worktrees are source. It also becomes the build's input provider (2.1) |
| 16 | `chat_context.py:1198-1235`, `manifest_dict` / `app_status` / `env_overview` | the L2 build-provider prompt describes what the agent is editing. Change only the OUTPUT: it should name the live release and the deployment address instead of a loopback port, and the "To SEE it" line becomes the address |
| 17 | `scripts/preview_config.py:170`, `_read_manifest_file` | reads the manifest of a source tree that `app_runner` does not start (its own header says so). Unchanged |

Seventeen call sites. Five of them (13 to 17) are why "break the directory
identity" was the wrong sentence, and they are a third of the surface.

### 6.3 Registries

The two enumerating registries stay exactly as broken as they are today. This
spec neither fixes nor worsens the measured split (`.runtime/projects.json` 7
rows, `.runtime/installed-apps.json` 8 rows, 5 slugs in both). The Deployment
record is a NEW store and it keys on the app slug, which is the one identifier
both registries agree on. Collapsing them is stage E of
`app-is-the-entity-2026-08-20.md` section 9 and it is not this work.

Do not add a fifth registry. The release index lives under
`.runtime/artifacts/releases/`, keyed by app slug, and it is a store, not a
catalog: it never enumerates which apps exist.

---

## 7. The local provider, and how <app> survives

### 7.1 What is running right now, measured

- One container, `64008f127ca3`, up since 2026-07-21, running `bun run dev --
  --port 8080 --strictPort --host 0.0.0.0 --base /base-apps/run/<app>/proxy/frontend/`.
- Compose project `ws_<app>`, from `.runtime/compose/<app>.yml`, mtime
  2026-07-05 21:48.
- Bind mount `~/workshop/projects/<slug>/frontend:/app`.
- Published to `127.0.0.1:8799`. One port allocation in the whole registry.
- Two of <app>'s three services are `enabled: false` and all three deps are
  `ok: false`.
- The generated compose file **predates** the `security_opt` / `cap_drop` /
  `user: nobody` hardening that is in `compose_gen` today.

### 7.2 The finding that decides section 7

**<app>'s one enabled service is a development server.** `bun run dev` with
`--strictPort` and a `--base` derived from `$WORKSHOP_PROXY_BASE`. A vite dev
server exists to watch a source tree and rebuild on change. An immutable artifact
of a dev server is a contradiction: freeze the tree and the only feature it has
stops working.

So the honest answer is not "migrate <app> to an artifact". It is: **the
platform has two run modes and it has always had two, and the current code hides
that behind one Start button.**

    DEV RUN      binds the source tree, hot reloads, never produces a release,
                 never gets an address beyond loopback and the tailnet.
                 This is what <app> is doing and it is legitimate.

    RELEASE RUN  builds a release, extracts it read-only, runs it, gets a
                 deployment record and an address.

This is the same distinction the tree already draws between `dev_servers.py` (the
8500-8599 preview window, per-agent, explicitly ephemeral) and the app runner. It
was never drawn for the app runner, so the app runner has been doing dev runs and
calling them deployments. `.runtime/app-deploys.json` is literally named after
the confusion.

Making the mode explicit and declared is the smallest change that lets the
artifact path exist without breaking the one thing that is running.

### 7.3 <app>'s migration, step by step, with nothing stopped

1. **Nothing changes on day one.** The running container is a dev run. Classify
   it as one. `mode: "dev"` is recorded on its runner state; no build, no release,
   no artifact. The container is not touched, not recreated, not regenerated.
   This is the whole of the compatibility story and it costs one field.
2. <app>'s manifest gains `deploy: { kinds: ["site"], site: { build: "bun run
   build", dir: "frontend/dist", index: "index.html", spa: true } }`. Declared,
   not inferred. Nothing acts on it yet.
3. A build runs: worktree at `.runtime/builds/<id>/src`, `bun install && bun run
   build` in `frontend/`, tar `frontend/dist`, store the blob, write `<app>@r1`.
   The dev container is unaffected throughout; the build does not touch
   `projects/<app>`.
4. A release run deploys `<app>@r1` to a NEW compose project (`ws_<app>__<n>`)
   on a NEW port. Both run at once, because they are different compose projects
   with different published ports. Compare them side by side.
5. When it looks right, `docker compose -p ws_<app> down`. If it does not, do
   nothing; the dev run is still up.

Step 4 is also the rollback mechanism, which is the point: a release run is
brought up beside the current one, not in place of it.

### 7.4 The concrete hazard, stated so it is not discovered at 2am

The first time anything regenerates a compose file for <app>, the new file will
carry `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]` and
`user: nobody`, which the running container does not have. Measured. Two
consequences:

- Under `user: nobody`, a bind-mounted `node_modules` written by uid 1003 during
  a previous `bun install` may be unreadable or unwritable. A dev run that has
  worked for seven weeks can fail on its first restart for a reason that has
  nothing to do with any of this work.
- This is a live latent defect today, independent of the artifact change. Anyone
  who presses Restart on <app> hits it now.

Mitigation, and it is one line in the plan: do not regenerate <app>'s compose
file as a side effect of anything. When a regeneration is genuinely wanted, do it
deliberately, expect the uid mismatch, and fix it with an explicit `user:` from
the manifest rather than by removing the hardening.

Read-only release mounts make this strictly better in the long run: a read-only
`/app` plus a named volume for scratch removes the uid question for release runs
entirely. It does not help dev runs, which is another reason dev runs stay a
declared, second-class mode rather than the default.

### 7.5 What the local provider is, in one paragraph

`local` is: build with a subprocess in a throwaway worktree; store blobs in
`.runtime/artifacts/`; extract a release to `.runtime/releases/<digest>/`;
generate a compose file that bind-mounts that directory read-only; allocate one
loopback port from the existing `app` window; bring it up with the existing
`container_runtime` docker driver; return `http://127.0.0.1:<port>` as the
address. Every one of those pieces exists today except the store, the extract and
the read-only mount. That is the measure of how much of this is new: three
things, and the rest is rewiring.

---

## 8. Genuinely the operator's decisions

No ticket is filed for any of these. Each one changes what gets built, not how.

1. **Is Workshop ever hosted for other people, or always self-hosted?** The
   review calls this gate 0 and it is right. What changes with the answer:
   *nothing in sections 1 to 4*. The artifact model is worth building for the
   local provider alone, because it buys reproducible builds, rollback, and an
   app that stops depending on the operator's uncommitted edits. What the answer
   does gate is whether a second provider, an origin model, a token exchange and
   a spend cap are ever worth designing. If the answer is "no" or "not this
   year", build sections 1 to 4 and 7, and shelve tier 2 with the research
   attached.
2. **Retention numbers.** N = 10 releases per app and 30 days are proposals, not
   measurements. The whole artifact store on this box is currently zero bytes; a
   <app> `site` release is likely single-digit MB and a `ui-bundle` is likely
   hundreds of KB, so a generous N costs little. Pick numbers, or accept these.
3. **Do the four in-process Python apps ever become `service` artifacts?**
   Measured: `birds`, `budget`, `commute` and `location` are Python modules that
   `main.py` mounts, contributing routers, background loops, store adapters,
   event verifiers and index collectors through `AppSpec`. Making any of them an
   artifact means giving the `AppSpec` seam an out-of-process form, which is a
   large piece of work with no partial version. This spec lets them stay
   extensions indefinitely and does not strand them. Whether they should is a
   product call about how much of the current product is meant to be hostable.
4. **Does the dev-run mode survive long term, or is it a migration affordance?**
   Section 7 makes it a declared first-class mode because <app> needs it today.
   A defensible alternative is to fold dev runs into `dev_servers.py` (which
   already owns exactly this concept, for agents) and let the app runner do
   release runs only. That is a bigger change and it would move <app>, so it is
   not proposed here.
5. **Does the `ui-bundle` ever move to a CDN?** Stage 5 section 9.2 already flags
   where UI assets are served from as his call. This spec's answer (serve from
   the release store through the existing backend route) is compatible with both
   options and does not foreclose either.

---

## 9. What this document does not specify, and where it goes instead

Named so nobody mistakes silence for a decision:

- **The provider seam's full shape.** Section 4.3 gives the verb list the
  artifact model requires and the reason `rollback` is not one of them. The seam
  itself should be extracted from two working providers, per the review's
  recommendation, not declared here.
- **Origins, custom domains, TLS.** Review 7.1. Prerequisite for tier 2, not for
  the local provider, and not for `ui-bundle`.
- **Auth between the shell and a hosted app backend.** Review 7.2. Section 5.5
  states the invariant and stops.
- **Provisioned storage, migrations, secrets-as-bindings.** Review 7.3 and 6.
  The existing `env_requirements[]` schema is kept verbatim, per the inventory's
  KEEP-SHAPED, and the hosting design's poorer `secrets: names only` proposal is
  rejected.
- **The spend cap.** Review 7.5 and the hosting design's own retraction. A
  Cloudflare-provider concern; it has nothing to do with artifacts.
- **The deletion of the dead deploy subsystem.** Fully specified by the
  inventory's DELETE table, independent of all of this, and the review is right
  that it should ship first and alone.

---

## 10. Tickets filed

Nine, all `--execution code`, filed 2026-08-24. Filed at 70 to 85; measured, this
repo's ticket store bands priority by design (ADR 0019, `tasks._BAND_TO_INT`), so
82 through 85 all store as `high` (80) and 70 through 79 all store as `medium`
(40). Within a band the queue orders by creation, oldest first, which matches the
order below. Nothing in section 8 has a ticket, on purpose.

| # | id | title | depends on |
|---|---|---|---|
| 1 | `fb93c4d2bef44e308151f21105846fcf` | Add a content-addressed artifact store under .runtime/artifacts | none |
| 2 | `9ed7a34b136a4fccbe4a37f1bd8b6f28` | Parse a deploy block in workshop.app.json declaring artifact kinds | none |
| 3 | `80709722a52c41f084a773cc326ba28f` | Add release and deployment records with content-addressed digests | 1 |
| 4 | `0cae7ab2eb9845fda22ac81788ebf9bd` | Build an app release from a disposable worktree, not the source tree | 3 |
| 5 | `ffa91fb58a6541ed99979b19d3d376ab` | Split app_runner._project_dir into source_dir and release_root | none |
| 6 | `484b46cb59fb4217b1f45d117d1fa326` | Record a run mode on each app so a bind-mounted dev container is named | none |
| 7 | `967eaa2ad4054bf2a76ff3b6a2a055cd` | Generate compose from an extracted release, mounted read-only | 4 |
| 8 | `4d6814118e8e4dffbff50127853d947d` | Show an app's builds, its failing step and the release still serving | 4 |
| 9 | `989ea7cc0a7c4a6bb79e6230c7ca66aa` | Sweep unreferenced artifacts and releases on request | 3 |

Tickets 1, 2, 5 and 6 have no prerequisite and can start in parallel today.
Ticket 6 is the one that protects the running container and should not wait.

**Not ticketed, and each for a stated reason:**

- **The deletion of the dead deploy subsystem.** Fully specified by the
  inventory's DELETE table, independent of everything here, and the review is
  right that it should ship first and alone rather than bundled with a build.
  It needs its own ticket from its own document, not this one.
- **Pointing the stage-5 UI asset route at a release's `ui-bundle` artifact.**
  One function body (section 5.4), but stage 5 is not built, so a worker would
  have nothing to edit. File it when stage 5 lands.
- **Deleting `_reap_project_orphans`** (section 6.1 row 6). It is only safe once
  no run path has a cwd in the tree, which means after ticket 7 and after dev
  runs are decided (section 8, item 4).
- **Everything in section 8.** Those are decisions, not work.
