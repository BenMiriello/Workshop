# Hosting a user's apps: the design

**Status: POSITION PAPER, not an implementation plan. 2026-08-24, downgraded
the same day after review.** An independent review
(`hosting-design-review-2026-08-24.md`) found that exactly one step in section 6
is implementable as written (the deletion), and that section 6 step 1, which this
document says everything else depends on, is asserted rather than specified. Read
this for the direction and the research; do NOT hand it to a worker. Supersedes the sketch in
`where-apps-run-2026-08-24.md`, which was written before the research and got
the tier count wrong. Grounded in four research documents produced the same day:
`research-cloudflare-hosting`, `research-hosting-runtimes`,
`research-peer-platforms`, and the measured `deployment-surface-inventory`.

The operator's instruction: design what we would build **if we had never seen
the existing deployment surface**, then decide what to reuse, and migrate
completely, leaving nothing of the old system.

## 0. The measurement that makes this easy

The existing deployment subsystem **has never run**. Measured, not inferred:
0 of 11,883 job records carry `kind=deploy`; `.runtime/deploys/` does not exist;
the 18 Deploy records that do exist are all for a project `gizmo` that does not
exist, with a provider kind that is not registered; `local_runner`'s three deploy
functions have zero callers and share a store path with `deploys.py` under an
**incompatible schema, so the first write would destroy all 18 records**. The
marketplace has never been used. `POST/PATCH/DELETE /apps` has no UI.

So there is no migration. There is a **deletion**, and then a build. No data to
preserve, no users to move, no compatibility to keep. That is the best possible
starting position and it should be used deliberately rather than drifted past.

One app has ever run: gengui, containerised on 5 July, up since 21 July.

## 1. The one assumption that has to die first

`compose_gen.py:213` bind-mounts an **absolute host path** into the container:
`~/workshop/projects/<slug>/frontend:/app`.

That single line makes **"the app" and "a directory in the operator's git working
tree" the same object**. Manifest resolution, the build step, the runtime, the
file API, the Files tab and `is_self_project` all depend on that identity.

Every hosted design breaks it, so it breaks first:

> **An app is an ARTIFACT, not a directory.** A build turns source into an
> artifact; the artifact is what runs. Nothing that serves traffic reads the
> operator's working tree.

This is the same change extraction needs, which is why those two efforts are one
effort. It is also why nothing else in this document can start before it.

## 2. What we would build, knowing nothing of the current code

### 2.1 The primitive set, and why exactly these

From the peer survey, the common denominator across Replit, Vercel, Netlify, Val
Town, Deno Deploy, Railway, Render, Fly, Supabase, Firebase and Cloudflare:

**The five that appear everywhere:**
1. **Static hosting** for the UI.
2. **An HTTP-triggered compute unit** for the backend.
3. **One durable store.** SQL is winning, including on platforms that were
   NoSQL-native: Firebase's own cost curve is the most-cited complaint against
   per-read pricing.
4. **Secrets.**
5. **Idle-cost handling.** Every platform has one; the mechanism differs.

**Common seconds:** object storage, scheduled jobs.

**Genuinely rare, and therefore NOT in v1:** queues (only Cloudflare) and
realtime/pubsub (only Supabase and Firebase). There is no well-trodden pattern to
copy, so building them first would be inventing rather than following.

That is the answer to "how much do we offer": **five primitives, plus object
storage and cron when they are asked for.** Not a general cloud.

### 2.2 Constrain, do not allow anything

This is the crux and the evidence is one-sided.

The platforms with a working story for **predictable AI-generated output** are
the ones with a **fixed primitive set**: Val Town, Supabase, Cloudflare,
Firebase. The "bring any container, do anything" platforms (Railway, Render,
Fly) trade that away, and independently carry the worst public reliability
complaints in the survey.

Val Town's lesson is the sharpest and it is a warning about prompting: their
own postmortem says steering the agent through its system prompt **was not
enough**, and they had to reshape the primitive's API surface itself. Their
phrasing: *adapt your code to the LLM, not the LLM to your code*.

So the operator's instinct is right, and stronger than he put it. **A guided
primitive set is not a convenience, it is the only mechanism that makes an
agent's output predictable**, and the primitives have to be designed for the
agent, not merely documented for it.

### 2.3 Do not build CI/CD

The operator raised this himself and the evidence backs him decisively:
**Lovable and Bolt, which are AI app builders competing directly in this space,
did not build backend primitives or hosting at all. They auto-provision
Supabase.** Anthropic's Artifacts and OpenAI's Sites stop at the frontend.

Nobody in this category won by building a deployment platform. Building one
would be competing with companies whose entire business is that one problem,
using a fraction of the attention.

### 2.4 The spend cap is a v1 requirement, not a nicety

The single most common real complaint across every peer platform is
**unbounded metered billing with no cap**: documented Vercel bills going from
$20 to between $700 and $23,000 from traffic the customer did not cause, and
Firebase per-read pricing turning $50 a month into $5,000.

**CORRECTION, 2026-08-24: an earlier revision of this section claimed "Workshop
already has the mechanism". That is false and is retracted.** The usage limiter
records `{ts, project, agent, tokens, job_id}` keyed on a project, counts TOKENS,
and gates the admission of an agent run. An app spend cap counts MONEY, is keyed
on a tenant and an app, and is enforced against a cloud provider's metered usage
that this platform does not observe. Different unit, different actor, different
enforcement point, different observability. It is a separate mechanism that can
borrow the limiter's SHAPE (AND-composed rules, refusal on breach, a named cause)
and none of its plumbing.

The requirement stands and the reason is stronger than the convenience: every
platform that added a spend cap late has a public incident attached. It must be
designed as its own thing, and its absence is a reason not to accept an external
tenant, not a thing to note and move past.

And the safety lesson, from the platform with the *best* agent steering: Replit's
agent deleted a production database during a declared freeze. Good primitives
reduce architectural drift; they are **not** a safety boundary against a
destructive action taken with legitimate credentials. Those need separate
mechanisms, which this repo already has opinions about.

## 3. The runtime, with real numbers

### 3.1 Cost per idle app per month, which is the deciding number

    ECS Fargate, always on, smallest task      ~$9.00     the wrong default
    AWS App Runner, smallest provisioned       ~$2.52
    EC2 packed by hand                         ~$1 to $3  you own the packing
    Fly machines with auto-stop                 <$1        volumes bill when stopped
    Google Cloud Run, scale to zero              ~$0       image storage only
    AWS Lambda                                    $0
    Cloudflare Workers for Platforms              $0       first 1,000 tenants

Cloudflare is not marginally cheaper, it is **categorically** cheaper at rest:
the $25/month Workers for Platforms plan includes **1,000 tenant scripts**, and
beyond that roughly $0.02 per script. There is no stated cap on Workers per
dispatch namespace. Billing is on **CPU time, not wall time**, so an app waiting
on a database pays nothing while it waits, which is what most CRUD apps do.

### 3.2 What Cloudflare cannot run, stated plainly

Three limits disqualify real apps and they must be said up front, not discovered:

- **128 MB memory per isolate.** No large in-memory dataset, no media processing.
- **CPU time per request** (30s default, 5 min maximum). Fine for I/O-bound work,
  fatal for CPU-bound work.
- **No native binaries and no arbitrary Docker** on standard Workers. No FFmpeg,
  no headless Chrome, no C-extension Python, no long-running server process.

Cloudflare Containers (GA 2026-04-13) is the escape hatch, but it is a different
cost and latency model, not a drop-in extension of Workers.

### 3.3 Per-tenant storage, which is where the real ceilings are

The compute is uncapped; **storage is where a naive per-tenant design breaks**:

    D1 (SQL)              50,000 databases per account, 10 GB each, API-creatable
    Durable Objects       no stated per-class cap; Facets (2026) explicitly
                          marketed for "a database per AI-generated app"
    R2 (object storage)   1,000,000 buckets
    Workers KV            1,000 namespaces per account  <-- THE TRAP

A namespace-per-tenant KV design dies at a thousand tenants. A database-per-
tenant D1 design dies at fifty thousand. Durable Object Facets is the only
primitive with no visible ceiling, and it is the one Cloudflare is marketing at
precisely this use case.

Off Cloudflare, the equivalent answers are **Turso** (unlimited databases,
$4.99/month flat, marketing 10,000 monthly active databases) and **Neon**
(compute suspends after 5 minutes idle, about $1.50 per branch-month).

### 3.4 What not to build, on the evidence

**Self-managed Firecracker is a trap for a team this size.** The VMM is fine;
what costs headcount is the jailer, guest kernel and rootfs maintenance,
networking, snapshot lifecycle, host-fleet patching, and the fact that
Firecracker ships **no scheduler**, so you write one. Lambda and Fargate already
run on Firecracker, so building on them buys the same isolation and density for
free. It is only worth owning if hosting other people's code becomes the product
itself, which is Fly's position and not ours.

Kubernetes, Knative and per-tenant namespaces: same verdict, for the same reason.

## 4. The proposal

### 4.1 Two tiers, not three

The earlier sketch proposed three. The research collapses it to two, and the
third becomes an escape hatch rather than a tier:

**Tier 1, static.** A UI bundle on object storage behind a CDN. Effectively zero
per app. Most apps are only this, and the app loader is what makes it possible.

**Tier 2, on demand.** An HTTP compute unit that scales to zero, plus a durable
store, secrets, and optionally object storage and cron. Effectively zero at rest.

**The escape hatch, not a tier: bring your own host.** An app that needs FFmpeg,
a GPU, a persistent socket or a always-resident process declares an EXTERNAL
address and Workshop proxies to it. The platform does not host it. This is what
keeps the platform honest about what it can do well, and it is exactly how the
bird station should be modelled: it is not a tier-3 tenant, it is an app that
runs on the operator's own hardware and is reached over the network.

That reframing matters, because "resident" as a tier would have obliged us to
build the most expensive thing for the rarest case.

### 4.2 The seam that keeps hosting out of the codebase

The operator asked for hosting to be separable so the platform can offer
different hosting products, or none. So the platform must never name a provider:

    A HOST PROVIDER implements: publish(artifact) -> address,
                                status(app) -> state,
                                destroy(app),
                                logs(app),
                                and provisions the declared primitives.

Providers: `local` (what exists today, honestly relabelled: one operator, one
box, docker), `cloudflare`, and `external` (the app tells us its address; we
proxy and monitor). A provider is selected per deployment, not compiled in.

This is the same rule as "no platform source file may name a specific app",
applied to infrastructure, and it is why the current bind-mount design cannot be
extended: it *is* the provider, welded in.

### 4.3 What an app declares

Extend the manifest that already exists and has one reader
(`app_runner._read_manifest_file`). Do not add a second file:

    ui:        the bundle (this is the app loader's contract, already designed)
    compute:   none | http     and the entrypoint
    storage:   sql | kv | object, each optional, each provisioned by the platform
    secrets:   names only, never values
    schedule:  cron entries, optional
    limits:    the spend cap, per app

Declared, never inferred. The platform reads the declaration and provisions.

## 5. Migration: a deletion and a rebuild

Because none of it has ever run:

**DELETE:** `deploy_runner`, `deploy_watcher`, both deploy providers,
`deploys_override`, `local_runner`'s three deploy functions, the 18 orphan Deploy
records, the marketplace surface. About 1,850 lines that have never executed. The
watcher currently starts on every boot and logs `not-autonomous` 18 times every
five minutes, forever.

**REPLACE:** `compose_gen` (the bind-mount identity is the thing being removed),
`port_registry` (loopback ports are a local-provider detail, not a platform
concept), the proxy path (an app has no public route today at all).

**KEEP-SHAPED:** the manifest and its single reader; `app_registry` as the
catalog; the App entity from `app-is-the-entity-2026-08-20.md`; the usage limiter,
which becomes the spend cap.

## 6. What a founding engineer would actually do, in order

1. **An app is an artifact.** Break the directory identity. Nothing else can
   start. This is also stage 7 of extraction, so it is one piece of work.
2. **Tier 1 only.** Static UI on a CDN through the loader. Most apps are done
   here and it costs nothing.
3. **Delete the dead deploy subsystem** in the same pass, so there is never a
   moment with two of these.
4. **Tier 2 with exactly one provider and one store.** Cloudflare Workers plus
   one SQL primitive. Resist the second provider until an app actually needs it.
5. **The spend cap**, wired to the existing limiter, before the first external
   tenant rather than after the first bill.
6. **The escape hatch**, which is mostly proxy and status and is cheap.

Deliberately not first: queues, realtime, CI/CD, a marketplace, multi-region, a
second provider, and anything Kubernetes.

## 7. Genuinely the operator's

1. ~~Is Workshop ever hosted for other people?~~ **ANSWERED by the operator,
   2026-08-25: yes. "It's a prod web SaaS app, it's gonna have other people use
   it."** This is settled and must not be asked again. It means the hosted case
   is the design target, not a hypothetical: the tiers, the provider seam, the
   per-tenant storage question and the spend cap are all in scope, and
   "self-hosted only" is no longer a live alternative to weigh them against.
   Every document that treats hosting as conditional should be read with this
   applied.
2. **Cloudflare as the first provider?** The evidence favours it strongly on cost
   at rest and it is purpose-built for this shape, at the price of real lock-in
   and three hard limits.
3. **How constrained?** Fixed primitives make agent output predictable and rule
   some apps out. The research says constrain; it is still a product choice.
4. **Tenant data shape**: shared store with a tenant column, or a store per
   tenant. Least reversible decision here, and not urgent until tenant two.
