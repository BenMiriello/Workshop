# Review: `hosting-apps-design-2026-08-24.md`

- Date: 2026-08-24
- Status: REVIEW. Nothing here changes code or supersedes a design. It is a
  read of one proposal against what an implementable plan needs.
- Reviewed: `hosting-apps-design-2026-08-24.md` (286 lines) against
  `deployment-surface-inventory-2026-08-24.md`, `research-cloudflare-hosting-2026-08-24.md`,
  `research-hosting-runtimes-2026-08-24.md`, `research-peer-platforms-2026-08-24.md`,
  and the tree as it stands today.
- Everything labelled measured was run against the working tree on 2026-08-24.

---

## 0. The verdict, and where the solid ground ends

**It is a well-researched position paper with one implementable step in it, and
that step is the deletion. Everything the document calls a build is a sketch.**

The boundary is precise and it is earlier than the document implies:

- **Solid ground: step 3, the deletion.** Fully specified, by the inventory
  rather than by this doc. No data to preserve, no design decisions left, and it
  fixes three live defects. It does not depend on step 1 and should not wait for it.
- **The plan ends at step 1.** "An app is an artifact" is asserted, never
  specified. The document says nothing else can start until it is done, and it
  is the one thing the document does not define. That is the whole finding.
- Steps 4, 5 and 6 each rest on a decision the document defers to its own
  section 7, which means the sequence is not a sequence, it is a wish list with
  numbers on it.

There is a second, deeper problem that reorders everything: **the document is
about two different objects and never notices.** Step 2 ("most apps are only a
static UI") is about apps as shell extensions living inside the operator's
Workshop page. Step 4 (Workers for Platforms, 1,000 tenant scripts, $0.02 per
idle tenant) is about apps as standalone tenant programs with their own origins
and their own users. Those are not two tiers of one thing. They are two products.
Measured: of the eight apps in the catalog, four (`birds`, `budget`, `commute`,
`location`) are Python modules that `backend/main.py` mounts into the platform
process plus TSX pages compiled into the shell bundle. Under this design those
four apps have no artifact and no host. The design never says which kind of app
it is hosting, and the answer changes every subsequent step.

## 1. What the document gets right, so the rest is credible

- **The measurement base is accurate.** Every number in section 0 checks out
  against the inventory: 0 of 11,883 job records with `kind=deploy`, 18 orphan
  Deploy records for a nonexistent project with an unregistered provider kind,
  the `local_runner` / `deploys.py` store-path collision, `.runtime/deploys/`
  absent, the marketplace never used. The design did not invent evidence.
- **"There is no migration, there is a deletion" is the right call** and the
  right thing to act on first.
- **`compose_gen.py:213` is genuinely the load-bearing line.** The inventory
  independently reaches the same conclusion. Naming it as the first thing to
  break is correct.
- **The cost analysis is real and the conclusion (idle cost per app is the
  deciding number) is right.** The Firecracker and Kubernetes verdicts are
  correct and well argued.
- **"Constrain, do not allow anything" is the correct product instinct** and the
  Val Town evidence for it is the strongest thing in the research set.
- **The spend-cap-is-v1 instinct is right**, even though the mechanism it
  proposes does not exist (section 7.5 below).

None of that makes it a plan. A plan is a thing an engineer can start.

---

## 2. Section-by-section verdict

| Section | Claim | Verdict |
|---|---|---|
| 0 | The deploy subsystem has never run, so delete it | **SOUND. Implementable now.** |
| 1 | An app is an artifact, not a directory | **THE HOLE.** Asserted, not specified. See section 4. |
| 2.1 | Five primitives plus object storage and cron | Sound, but slightly misreports its own source. The peer research puts **cron in the minimal set** and idle-cost-handling in the seconds; the design swaps them. Low stakes, but it means the ranking is the author's, not the evidence's. |
| 2.2 | Constrain the primitive set | Sound as doctrine. **Empty as a spec**: it never names the constraint. Which languages? Which runtime? The one decision that makes this section real is not made anywhere in the document. |
| 2.3 | Do not build CI/CD | Sound, and consistent with the peers. |
| 2.4 | Spend cap is v1 | Right requirement, **wrong mechanism**. See 7.5. |
| 3.1-3.4 | Runtime cost and limits | Sound and well sourced. The three Cloudflare disqualifiers are stated honestly and then never reconciled with the fact that every app in this repo is Python. |
| 4.1 | Two tiers plus an escape hatch | **INCOHERENT.** Tier 1 is not hosting. See section 3, step 2. |
| 4.2 | The provider seam | **BREAKS ON THE SECOND PROVIDER.** It is `app_runner`'s current verb list with a provider label. See section 5. |
| 4.3 | The manifest extension | **UNPARSEABLE AS WRITTEN.** Six one-line field names, one of which contradicts the inventory's own KEEP-SHAPED. See section 6. |
| 5 | Delete / replace / keep-shaped | Mostly sound; three scope errors. See section 8. |
| 6 | The six-step sequence | **Ends at step 1.** See section 3. |
| 7 | Four open questions | These are not appendix items. **Q1 gates steps 4 to 6 entirely** and the document knows it: "nearly everything above is only worth building in the hosted case." A design whose own open question can delete two thirds of its plan has put the questions in the wrong place. |

---

## 3. Is the sequence real? Step by step

The test is: could a competent engineer open an editor tomorrow and start,
without coming back for more design?

### Step 1. "An app is an artifact." **NO. This is where the plan ends.**

The document devotes one blockquote to it, says nothing else can start before
it, and then does not say what an artifact is. See section 4 for the full list
of what is unspecified. Two further things make step 1 much larger than it looks:

- **It inherits an unacknowledged dependency queue.** The doc says this is "also
  stage 7 of extraction, so it is one piece of work". Stage 7 is blocked on
  stage 5 (the loader), which is blocked on stage 4b, whose own plan measures
  twelve remaining platform modules that app pages still reach for and roughly
  320 uses of ~22 class names defined only in the platform's legacy
  `frontend/src/styles.css`. Measured today: `python3 scripts/check_no_app_names.py`
  reports content 102, imports 20, literals 302, exit 0 (at baseline, gate
  passes). Content goes to zero only at stage 7. So "start with step 1" means
  "first finish 4b, then 5, then 7." The hosting doc never mentions this.
- **The slogan is stated too broadly.** "Nothing that serves traffic reads the
  operator's working tree" is right. "Break the directory identity" is not the
  same statement, and taken literally it breaks the editing surface: the Files
  tab (`app_files.py:210`), `worktrees.py:72` and `chat_context.py:1198` all
  resolve an app through `_read_manifest_file` and a directory, and they must
  keep doing so, because an agent edits source, not artifacts. The real design
  is a three-part relationship (source tree for editing, artifact for serving,
  build as the arrow between them) and it is the missing content of step 1.

### Step 2. "Tier 1 only. Static UI on a CDN through the loader." **NO, and it is incoherent as written.**

The design says the app loader is what makes tier 1 possible. Read what the
loader actually is (`stage-5-app-loader-plan-2026-08-24.md`, sections 2.1 to 2.4):

- The bundle is an ESM module whose default export is
  `{ contract: 1, exports: Record<string, ComponentType> }`. It is a set of
  React components, not a website.
- It shares the shell's React, router, lucide and `@workshop/ui` instances
  through a **single import map injected into the shell's `index.html`**. It
  cannot resolve its own imports outside that document.
- It reaches host services through `useAppHost()`, a context the shell provides.
- It is served from `GET /api/base-apps/ui/{slug}/{path}`, which inherits
  `require_operator` from the `/base-apps` mount.

So the loader bundle has **no independent origin, no independent runtime, and no
public reachability**. It is a plugin for one page of one operator's Workshop.
Putting it "on object storage behind a CDN" changes where the bytes come from
and changes nothing about hosting. Under the only reading that parses, tier 1 is
a CDN for the shell's plugin assets, and the claim "most apps are only this and
it costs nothing" is true only because tier 1 is not hosting an app.

That collides head-on with step 4, where the unit of hosting is a tenant Worker
with its own hostname under a dispatch namespace, and with section 3.1's whole
economic argument, which is priced per tenant. **Decide which one an app is.**
Until that is decided, step 2 cannot start, because you do not know whether you
are building a CDN origin for plugin assets or a public origin for a website.

### Step 3. "Delete the dead deploy subsystem." **YES. Startable today. This is the solid ground.**

Fully specified by the inventory's DELETE table: `deploy_runner.py` (407),
`deploy_watcher.py` (207), `deploys_override.py` (81), `deploy_providers/` (617),
the dead half of `local_runner.py`, `registry.py`'s publish/install/update, the
zero-caller endpoints, the 18 `gizmo` records, the leaked pytest compose files.
It fixes three live defects: the watcher logging `not-autonomous` 18 times every
five minutes forever, the store-path collision that would silently destroy the
Deploy records on the first `local_runner` write, and `compose_gen`'s tests
writing into the real `.runtime/compose/`.

Two scoping notes the design misses:

- **There is a frontend half it does not name.** `Deploys.tsx` (305),
  `DeployDetail.tsx` (451), `DeploysPanel.tsx` (517), `AppDeploysTab.tsx` (86),
  `Marketplace.tsx` (203), the `/deploys` and `/marketplace` routes, the
  `APP_TABS` deploys row, `lib/marketplace.ts`, and the `GET /apps/{id}/deploys`
  call in `lib/apps.ts`. Deleting the backend without these leaves dead screens.
- **`cloudflare_pages.py` is the tree's only Cloudflare API integration.**
  Deleting it before writing a Cloudflare provider is defensible (Pages upload is
  not Workers script upload) but it throws away the only prior art for token
  handling and API error shapes. Read it before deleting it.

**Recommendation: do this step first, not third.** It has no dependency on steps
1 or 2, it is independently valuable, and the doc's own reason for putting it
third ("so there is never a moment with two of these") is inverted: there is no
risk of two, because the thing being deleted has never run.

### Step 4. "Tier 2 with exactly one provider and one store." **NO.**

Three unmade decisions, each of which is the decision:

1. **Which store.** The document's own section 3.3 argues that D1 dies at 50,000
   and that Durable Object Facets is "the only primitive with no visible ceiling,
   and it is the one Cloudflare is marketing at precisely this use case." Then
   step 4 says "one SQL primitive" and does not pick. D1 and a DO Facet are not
   interchangeable: different provisioning API, different binding, different
   consistency model, different migration story, different exit path.
2. **Which runtime, which is to say: what language may an app be written in.**
   Section 3.2 states the three Cloudflare disqualifiers honestly (128 MB, CPU
   ceiling, no native binaries, no C-extension Python) and then never applies
   them to this repo. Measured: `notes-app`'s manifest starts
   `uvicorn app.main:app`; `<app>` is bun plus a Python core plus an inference
   worker plus three compose deps; `birds`, `budget`, `commute` and `location`
   are Python modules inside the backend process. **Zero existing apps can run on
   a Worker.** Step 4 as written strands every app the platform has. This is the
   exact decision section 2.2 says must be made deliberately and then does not make.
3. **The dispatch Worker.** Workers for Platforms routes every request through a
   dynamic dispatch Worker that the platform writes, deploys and owns
   (`env.DISPATCHER.get(name)`). It is a required, singleton, platform-owned
   piece of infrastructure and it is not a provider method, so it has no place
   in the seam. It appears nowhere in the design. Whoever starts step 4 hits it
   on day one.

### Step 5. "The spend cap, wired to the existing limiter." **NO. The mechanism named does not fit.**

`limiting-usage-2026-08-24.md` is about **LLM token spend that Workshop itself
incurs**: a sliding window log over `.runtime/usage.jsonl`, an admission
controller that reserves a task's estimated tokens before starting it, and
refusal on breach. It works because Workshop is the party making the spend and
can therefore refuse to make it. Its own status line reads "PROPOSAL. Nothing
changed yet."

Hosting spend is a different animal in four ways, none addressed:

- **Different unit.** Dollars across several metered dimensions (requests,
  CPU-ms, D1 rows read and written, R2 storage and class-A operations, custom
  hostnames), not tokens.
- **Different actor.** The spend is caused by third-party traffic arriving at
  Cloudflare's edge. Workshop never sees the request.
- **Different observability.** Workshop learns the number after the fact from
  Cloudflare's GraphQL Analytics API, with reporting lag. There is no in-band
  event to meter.
- **Different enforcement point.** Refusal is not available. The only levers are
  Cloudflare-side per-tenant Custom Limits (a W4P feature, mentioned in the
  research, absent from the design) and unbinding or deleting the tenant script.

Saying "Workshop already has the mechanism" is the weakest sentence in the
document, and it is load-bearing for the section that argues hardest (2.4).
The requirement is right. The mechanism has to be designed from scratch, and it
is a Cloudflare-provider concern, not a limiter concern.

### Step 6. "The escape hatch, mostly proxy and status, and it is cheap." **NO, and "cheap" is wrong.**

The inventory measured what the current proxy is: it strips
`content-security-policy` and `x-frame-options` from every response
(`base_apps.py:667-670`), does not carry WebSockets, serves the app from the
platform's own origin under a path prefix, and is reachable only from loopback
or the tailnet because the public edge forwards `/api/*` only. Its own comment
says "a dev-preview proxy, not a production origin."

Proxying to a declared EXTERNAL address therefore needs, at minimum: an origin
model, a hostname or route, TLS, an authentication story for the external
address, and an inbound path for a host that is not publicly routable. For the
bird station specifically (measured: `apps/<slug>`, 30,150 files, 2.2 GB, a
Raspberry Pi rig on the operator's LAN) that is a tunnel, not a proxy. The
reframing of the bird station as an escape-hatch app rather than a tier is
genuinely good. The cost estimate attached to it is not.

---

## 4. "An app is an artifact": what is actually unspecified

The design gives this one blockquote, calls it the prerequisite for everything,
and specifies none of it. Here is the list an engineer would need answered
before writing a line. Not one of these is answered anywhere in the document.

1. **Format.** Tarball, OCI image, a directory of files in object storage, a
   Worker script upload, a zip? The right answer differs per provider, which
   means the platform-level answer is probably "an artifact is a content-addressed
   manifest of files plus an entrypoint descriptor, and each provider knows how
   to turn that into its own thing". That is a design decision, and it is unmade.
2. **Identity and addressing.** Is an artifact `app@sha256:...`? Is it versioned?
   Immutable? Can two deployments reference one artifact?
3. **Where the build runs.** The whole point is that nothing serving traffic
   reads the operator's tree. Today `_run_build` is `/bin/bash -c <build>` in the
   tree on a daemon thread inside the API process, capped at 1800s. If the build
   still runs there, the artifact is a cosmetic change. If it does not, where?
   A container? A worktree? Cloudflare's build service? Unstated.
4. **Build input.** A git ref, a worktree, the app directory as it sits? The repo
   already has a worktree and branch model
   (`branches-worktrees-and-environments-2026-08-20.md`) that this must agree with.
5. **Build output shape.** What does the builder hand back? A digest, a file
   list, an entrypoint, a set of provisioning requirements, a build log?
6. **Where artifacts are stored, and for how long.** Measured: the platform has
   **no object storage of any kind** today. Step 1 introduces the first one, and
   the design never names it, budgets it, or says who runs it.
7. **Failure.** What is the state of an app whose build failed? The previous
   deployment must keep serving; nothing in the design says so. Where does the
   build log live? Who sees it? This is not a step-6 detail; it is half of step 1.
8. **Which copy of the manifest is authoritative at runtime**, the one in source
   or the one baked into the artifact.
9. **Garbage collection.** Artifacts accumulate. Retention policy, and who deletes.
10. **What replaces `_project_dir` for the four callers that resolve an app to a
    directory** (`app_runner`, `app_files`, `worktrees`, `chat_context`).
11. **How a deployment references an artifact.** The old model had
    `current.upload_ref` on the Deploy record. That record is being deleted and
    nothing is proposed in its place. Measured: the Postgres schema has no
    deployment table and no app-storage tables at all.
12. **Reproducibility and caching**, which decide whether a rebuild of an
    unchanged app is free or expensive, and therefore whether step 2 is cheap.

**Verdict: the plan rests on a hole, exactly as the operator suspected.** A
single sentence in the design ("A build turns source into an artifact; the
artifact is what runs") is doing the work of a document.

---

## 5. The provider seam: writing both providers against it

The proposed interface:

```
publish(artifact) -> address
status(app) -> state
destroy(app)
logs(app)
and provisions the declared primitives
```

Writing `local` against it is easy, because **it is a description of what
`app_runner` already does**. Writing `cloudflare` against it breaks in seven
places, five of them structural. An interface that fits one provider and not the
other is not a seam, and this is that.

**1. There is no deployment identity, so the interface cannot express the
product's own model.** `publish` takes an artifact but `status`, `destroy` and
`logs` take an app. You cannot say "publish v3 while v2 still serves", cannot
have a preview and a prod deployment of one app, and cannot roll back.
`app-is-the-entity-2026-08-20.md` makes a Deployment a first-class thing (a
running instance of an app) and this seam skips it. Fix: `publish` returns a
deployment handle and every other verb takes one.

**2. Provisioning is prose, not a method, and its ordering is backwards for
Cloudflare.** Locally you can bring up a postgres container after or alongside
the app. On Cloudflare, a D1 database or R2 bucket or DO namespace must exist
*before* the script upload, because its id becomes a **binding in the upload
payload**. So provisioning is not a side effect of publish; its outputs are
inputs to publish. As written the seam gets the dependency direction wrong.

**3. Secrets are not in the interface at all.** Local: a 0600 env file next to
the compose file. Cloudflare: a separate per-script secrets API producing
bindings, with its own update-and-redeploy semantics. This needs a verb and it
needs a statement about whether changing a secret requires a republish.

**4. `logs(app)` fits local and cannot be implemented on Cloudflare.** Local logs
are a pull: `docker compose logs`, full history, immediate. Cloudflare has no
log store. You get `wrangler tail` / Tail Workers, which is a **live push stream
with no history**, and history exists only if the platform stands up a sink
(Logpush to R2, or its own collector) and pays for it. A pull-shaped `logs()`
either returns nothing on Cloudflare or silently requires a whole logging
subsystem that the design never mentions.

**5. `status(app) -> state` returns a vocabulary only one provider can
populate.** The inventory marks the five-status service vocabulary
(`pending/building/starting/running/failed/stopped/disabled` plus
`crashlooping`, plus restart counts, OOM kills, exit codes) as KEEP-SHAPED. A
Worker has none of that. It is uploaded or it is not. There is no "running", no
restart count, no crash loop, no OOM. Either the platform vocabulary collapses to
something like `{unpublished, published, failing}` with provider-specific extras,
or the UI shows fields that are structurally null on the provider the design
wants to standardise on.

**6. `external` is a degenerate implementer.** `publish` is a no-op record
write, `destroy` deletes a row, `logs` is impossible, `status` is an HTTP probe.
Putting it behind the same interface makes the contract "any of these may be a
no-op", which is not a contract. It is better modelled as a different kind of
deployment, not a third provider.

**7. Verbs a working system needs on day one and this does not have:** `build`
(where does the artifact come from?), `rollback` (the deleted provider base
already had it, so this is a regression against the tree's own prior art),
`attach_domain` / route management, `list` and `reconcile` (with Cloudflare the
provider is the source of truth and the platform's records drift), `usage` or
`cost` (step 5 cannot exist without it), and environments.

Also note the address problem: `local` returns `http://127.0.0.1:8731`,
`cloudflare` returns a hostname that only resolves because the platform's own
dispatch Worker routes it. "Address" is not the same kind of value in the two
providers, and the second one implies a platform component the design does not
have.

**Verdict on the seam: not sufficient, and shaped by the provider it already
has.** The correct order is the opposite of the one taken here: implement two
providers concretely, then extract the seam from what both actually needed.

---

## 6. The manifest extension, field by field

The design proposes six fields as six one-line names. None is specified well
enough to parse or act on.

| Field | As specified | What is missing |
|---|---|---|
| `ui` | "the bundle (this is the app loader's contract, already designed)" | The pointer is honest and the loader plan is genuinely detailed (`ui.contract`, `kit`, `dir`, `entry`, `styles`, `provider_entry`, `pages`, `layouts`, `settings`, a closed mount vocabulary). **But it is the contract for a shell plugin, not for a hosted site.** Reusing the name for a CDN-hosted static app is where step 2 goes wrong. Pick one meaning. |
| `compute` | `none \| http` and the entrypoint | Entrypoint of what, in what language, at what runtime version, against what request interface? A Worker needs `export default { fetch }`; a container needs a start command and a port; uvicorn needs an ASGI path. Also: `none` plus a non-empty `schedule` is unrepresentable, and `http` has no notion of the multiple services every real manifest here declares (<app> has three). |
| `storage` | `sql \| kv \| object`, each optional, each provisioned by the platform | No name, no size, no multiplicity (two buckets?), no binding name or env var by which the app reaches it, no schema, no migration hook, no backup or retention. And **`sql` is a category, not a primitive**: D1 is SQLite, the local dep is Postgres, and an app's SQL is not portable between them. This field is the one most likely to be wrong in a way that is expensive to reverse. |
| `secrets` | "names only, never values" | **This contradicts the inventory.** `env_requirements[]` already exists with `key`, `description`, `required`, `secret`, `example`, `how_to_obtain`, `service`, `group`, and the inventory calls it "the best-designed thing in the surface... Keep the schema verbatim", singling out `how_to_obtain` as the AI-builder differentiator. The design proposes a strictly poorer field and never says what happens to the existing one. |
| `schedule` | "cron entries, optional" | No timezone, no per-entry handler, no overlap policy, no retry, no timeout, no failure surface. Local cron and Cloudflare Cron Triggers are different mechanisms with different limits (15 min CPU on the latter). |
| `limits` | "the spend cap, per app" | No unit, no enforcement point, no behaviour on breach. See 3, step 5. |

**Missing entirely, and needed by any real app on day one:**

- **`runtime`**: language and version. Its absence is precisely why step 4 is
  unstartable, and its presence is what would make section 2.2's "constrain"
  doctrine real rather than rhetorical.
- **`build`**: command, output directory, toolchain. The whole design rests on a
  build and the manifest has no build field, while today's manifest does
  (`services[].build`).
- **`routes` / `domain` / visibility**: what hostname or path the app answers on,
  and whether it is public or operator-only.
- **`auth`**: whether the app receives a platform identity, and in what form.
- **`migrations`**: how the provisioned SQL store gets a schema and how it
  evolves.
- **`health`**: exists today, silently dropped.
- **outbound / egress policy**: a constrained platform on W4P gets outbound
  Workers for free and should use them.
- **a contract integer** for the hosting block, as the loader plan already does
  for `ui`. Manifests are hand-edited; you need a version.
- **an explicit statement that many-services collapses to one compute unit**, if
  that is the intent. It silently invalidates `depends_on`, `${svc.url}` peer
  substitution and `deps[]`, all of which the inventory marks KEEP-SHAPED.

---

## 7. What the design does not address at all

For each: is omitting it deferrable, or does it make the design wrong?

### 7.1 Origins, custom domains and TLS. **WRONG, not incomplete.**

Custom domains are deferrable. **An origin model is not.** The inventory measured
that an app has no origin today: it is served from the platform's origin under a
path prefix by a proxy that strips CSP and `X-Frame-Options`, and it concludes,
correctly, that "any app can read the platform's cookies for that origin". The
moment a hosted app runs third-party or agent-generated code and is reachable
from a browser on the platform's origin, that is a cross-tenant security defect,
not a missing feature. Deciding the origin model is a prerequisite for tier 2,
not a follow-up. The good news is that Cloudflare makes the answer cheap
(wildcard subdomain per app, no custom-hostname line item until a tenant brings
their own domain), so this is a decision to make, not a project.

### 7.2 Auth between the shell and an app backend. **WRONG, not incomplete.**

Not mentioned anywhere. Measured today: an app gets "no user identity, no
session, no token, no service account". This is the same decision as 7.1: give
an app its own origin and the shell's cookie stops working, so you need a token
exchange; do not give it one and you have 7.1. There is no third option and no
version of tier 2 that ships without answering it.

### 7.3 Migrations for a per-tenant database. **Deferrable to build, NOT deferrable to design.**

If the platform provisions the store, the platform owns "who creates the schema
and who evolves it", across N databases. The design's own section 7 flags tenant
data shape as "the least reversible decision here". A migration story is the
concrete form of that decision. Decide it before step 4; it does not need to
ship in step 2.

### 7.4 Logs and debugging for the app author. **Effectively wrong for THIS product.**

`logs(app)` is one word in the seam and there is nothing about build logs,
request traces, or errors. For a platform whose entire thesis (section 2.2) is
that an **agent** writes the app, the log is the feedback loop that lets the
agent fix its own output. Observability is a primitive here, not an operational
nicety. On the provider the design prefers it is also the hardest one, because
Cloudflare gives you a live stream and no history (see 5.4).

### 7.5 Rollback. **Deferrable to build; the seam must be shaped for it now.**

With immutable artifacts and deployment-keyed verbs, rollback is nearly free:
re-point at a prior artifact. With the seam as proposed it is impossible.
So the cost of deferring rollback is zero if you fix the seam and unbounded if
you do not. Note also that Cloudflare's own gradual-deployment feature is
explicitly **not supported for user Workers in a dispatch namespace**, per the
research, so the platform owns cutover semantics itself.

### 7.6 What happens when a build fails. **NOT deferrable. It is part of step 1.**

No state, no log destination, no policy on whether the previous deployment keeps
serving. Every hosting platform's most common user-visible event is a failed
build, and this design has no word for it.

### 7.7 App-to-app and app-to-platform API access. **WRONG, and it is the third big hole.**

This is not merely omitted; the design **silently deletes the mechanism the
current apps use**. Stage 4 shipped an AppSpec seam (`event_verifiers`,
`event_dedup`, store adapters, `provides` / `needs` in `.runtime/apps.json`) that
lets an app plug into the platform **as an in-process Python module that
`main.py` mounts**. Measured: `backend/main.py` has 38 router mounts, and
`backend/services/` contains `birds_store.py`, `budget_engine.py`,
`finance_events.py`, `notes_store_adapter.py`, `commute/`, `location.py` and
more. An app that becomes an artifact on a Worker cannot be any of those. The
design therefore requires either that these apps stay in-process forever (in
which case they are not what is being hosted, and step 2's "most apps" claim is
about a different set) or that the AppSpec seam gets an out-of-process form,
which is a large unnamed piece of work. `resource-access-interface-spec-*`
exists and this design does not reference it.

### 7.8 The tenancy question itself. **The gate on everything.**

Section 7's Q1 ("Is Workshop ever hosted for other people, or always
self-hosted?") admits that "nearly everything above is only worth building in
the hosted case. Self-hosted, the honest answer is the escape hatch and nothing
else." That is the correct analysis and it means the document's own sequence is
conditional on an unanswered question. If the answer is self-hosted, steps 2, 4
and 5 should not be built at all, the Cloudflare research becomes background,
and the real work is the deletion plus the escape hatch. **This question belongs
in section 1, not section 7.**

---

## 8. Does the design contradict the measured inventory?

The factual base is accurate. Six discrepancies, mostly of scope and emphasis:

1. **`secrets: names only` versus KEEP-SHAPED `env_requirements[]` verbatim.**
   Direct contradiction, and the design proposes the weaker schema. (Section 6.)
2. **"the manifest that already exists and has one reader."** The inventory says
   `_read_manifest_file` is already reached by `app_files.py:210` and
   `worktrees.py:72`, and `chat_context.py:1198-1235` consumes `manifest_dict`,
   `app_status` and `env_overview`, and it flags this explicitly: "the manifest
   reader is already load-bearing outside the runner." The design uses "one
   reader" to imply the change is contained. It is not.
3. **"REPLACE `port_registry`"** on the grounds that loopback ports are a
   local-provider detail. Measured importers include `services/dev_servers.py`
   (the 8500-8599 preview window, which is the live dev-preview feature and has
   nothing to do with hosting) and `services/presence.py` (`process_alive`).
   Replacing `port_registry` is not in scope for hosting work, and the local
   provider still needs a port allocator.
4. **"KEEP-SHAPED: `app_registry` as the catalog."** The inventory measured four
   unlinked registries and `app_registry` is the reader of registries (2) and
   (3), that is, the built-in monorepo catalog of apps whose routers `main.py`
   mounts. That is precisely the registry that cannot describe a tenant's hosted
   app. The design picks one of four without argument, and doing so quietly
   contradicts `app-is-the-entity-2026-08-20.md` section 9, which the design
   claims to honour.
5. **Step 6 calls the escape hatch "mostly proxy and status and cheap"** while
   the inventory documents the proxy as a CSP-stripping, WebSocket-less,
   wrong-origin dev preview that the public edge does not even route. Internal
   contradiction between the design's own section 5 (REPLACE the proxy) and its
   section 6 (the proxy is cheap).
6. **Tier 1 assumes object storage and a CDN**, and the inventory measured that
   the platform has neither, and that the public edge serves `frontend/dist`
   from local disk. Not a contradiction, but an unacknowledged new external
   dependency introduced in the step described as costing nothing.

One fidelity note against the research rather than the inventory: section 2.2
says the container platforms "independently carry the worst public reliability
complaints in the survey". The peer research says Railway and Fly, "to a lesser
extent Render", and states plainly that this is "correlational, not proof". The
design drops the caveat. Minor, but it is the kind of hardening that makes a
synthesis read more settled than its sources.

---

## 9. What must be decided or specified before ANY implementation starts

Ordered. Nothing below the line can start until the thing above it is answered.

**Gate 0, product, one sentence:**

1. **Is Workshop hosted for other people, or self-hosted?** (The design's own
   Q1.) If self-hosted, stop after the deletion and the escape hatch and delete
   two thirds of this plan.

**Gate 1, the object:**

2. **Which kind of thing is "an app" for hosting purposes?** A shell plugin
   loaded into the operator's Workshop page, or a standalone tenant program with
   its own origin and its own users? The design assumes both in different steps.
   Answer decides whether "tier 1" exists.
3. **What language and runtime may a hosted app be written in?** The one
   decision that makes "constrain, do not allow anything" real. If the answer is
   JS/Wasm only, say so and say what happens to the five Python apps that exist.
4. **What happens to the four in-process apps** (`birds`, `budget`, `commute`,
   `location`, plus `finance`, `health`, `wearables`, `notes-app` in the
   catalog) that are Python modules `main.py` mounts. Hosted, kept in-process
   forever, or migrated. This is not a detail; it is most of the current product.

**Gate 2, the artifact (the whole of section 4 above):**

5. Artifact format, identity, immutability, storage location, retention.
6. Where the build runs and on what input.
7. Build output shape, build failure semantics, build log destination.
8. What replaces directory identity for the four modules that resolve an app to
   a directory, given that source-for-editing must survive.

**Gate 3, the seam and the model:**

9. Deployment as a first-class entity with an id, and every provider verb keyed
   to it.
10. Provisioning as explicit methods that run before publish and whose outputs
    are publish inputs.
11. Secrets, rollback, domains, reconcile and usage as first-class verbs.
12. The status vocabulary that both providers can actually populate.
13. The dispatch Worker: who owns it, where its source lives, how it is deployed.

**Gate 4, the things that make it wrong rather than incomplete:**

14. The origin model (per-app subdomain vs path prefix) and its CSP consequences.
15. Auth between the shell and an app backend.
16. The spend-cap mechanism, designed from scratch against Cloudflare's metering
    and Custom Limits, not hand-waved to the token limiter.
17. Log collection and retention for a provider that gives you a live stream and
    no history.

**Gate 5, before step 4 specifically:**

18. D1 versus Durable Object Facets, and the migration story for whichever wins.

---

## 10. Recommendation: what the genuine next step is

**Not this plan. Three things, in this order.**

**A. Answer the two gate questions. Today, in a paragraph each.** "Hosted for
others, yes or no" and "a hosted app is a standalone program, yes or no". They
are product decisions, not research, and every remaining hour of design is
wasted until they are made. If hosted-for-others is a "no" or a "not for a
year", this review's recommendation collapses to: do B, build the escape hatch,
and shelve the rest with the research attached.

**B. Ship the deletion now, standalone, as its own change.** It is the one part
of this document that is a plan. It is fully specified by the inventory, it has
no prerequisites, it removes about 1,850 lines that have never executed plus
their frontend surfaces, and it fixes three live defects. Do not wait for step 1
and do not bundle it with anything. It is also the best possible way to keep the
momentum this research earned while the gate questions are open.

**C. Then a spike, not a design, and certainly not this design's step 1.**
Write the artifact spec by discovering it, not by declaring it:

> Take `notes-app` (measured: one service, 645-byte manifest, `uvicorn
> app.main:app`, one optional non-secret env requirement, a real health path).
> Produce an immutable artifact from its source. Run that artifact under the
> local provider with nothing in the serving path reading
> `~/workshop/projects/<slug>`. Then do the same app a second
> time against Cloudflare, and let it fail, because it is Python, and record
> exactly what it cost to make it not fail (rewrite? Containers? refuse?).

That spike answers, empirically and in days: artifact format, where the build
runs, build input and output, build failure, secret injection, status vocabulary,
and the real cost of the constrain decision. It also produces the only honest
input to the provider seam, which is two working providers. **Write the seam
after the spike, from what both providers actually needed.** Writing it first is
how it ended up being `app_runner`'s verb list with a label on it.

What a spike must NOT be allowed to become: a partial implementation that ships.
Time-box it, expect to throw the code away, keep the findings.

**One-line summary for the operator:** the research is good, the deletion is
real, and the build plan is a sketch whose first step is undefined. Delete the
dead subsystem now, answer the two gate questions, and replace step 1 with a
two-provider spike on one small app rather than a document.
