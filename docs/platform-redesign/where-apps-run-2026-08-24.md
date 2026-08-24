# Where the code lives and where the apps run

**Status: proposal, 2026-08-24.** Written in response to the operator's
question, which was explicitly open ("I hadn't thought this through"). Nothing
here is filed as a ticket, because the decisions are his. It exists to make the
choice concrete.

## 1. Three questions, currently tangled

The question mixed three things that have different answers:

1. **Where the SOURCE lives.** The platform's repo, and each app's repo.
2. **Where the PLATFORM runs.** Workshop itself: the API and the shell.
3. **Where a USER'S APPS run.** Their builds, their backends, their data.

Separating them is most of the design, because the answer to 3 is not one answer
either.

## 2. The fact that reshapes everything: an app is two halves

An app is a **UI bundle** and, sometimes, a **backend**. Their hosting economics
are not merely different, they are different by orders of magnitude.

The UI half is a static ESM file and some CSS. Once the app loader lands
(`stage-5-app-loader-plan-2026-08-24.md`), the shell fetches it by URL and
imports it. Static files on object storage behind a CDN cost approximately
nothing per app and scale to millions of apps without a scheduler, a container
or a cold start. **A hundred thousand static apps is a storage bill, not an
architecture.**

The backend half needs compute, and only some apps have one. Of the four apps on
this box, the two that matter (a bird station, a commute reasoner) are backends
with real state; a notes app is mostly not.

So the loader work already answers half the hosting question. That is worth
stating plainly: **the UI never needs a deployed instance per user.**

## 3. The cost fact that decides the backend half

At scale, **idle dominates**. The distribution of user-built apps is not uniform:
most get near-zero traffic, indefinitely. So the number that matters is not cost
per request, it is **cost per idle app per month**.

That single number eliminates the default answer:

- **A container per app, always on** (ECS/Fargate, a VM, a dyno). Roughly 10 to
  30 dollars per app per month whether or not anyone visits. At a thousand apps
  that is 10k to 30k a month for mostly nothing. This is the option that feels
  obvious and does not survive contact with scale.
- **Scale-to-zero container** (Cloud Run, Fly machines with auto-stop, Knative).
  About zero at idle, roughly a second of cold start on first hit. Keeps a normal
  container programming model, so an app is a Dockerfile.
- **Function per app** (Lambda). About zero at idle, 100ms to about a second cold
  start, and the 2025 SnapStart work narrowed that. The cost is that it imposes a
  programming model on the app author.
- **V8 isolate per app** (Cloudflare Workers for Platforms, whose dispatch
  namespaces exist precisely for "run your users' code"). Sub-millisecond cold
  start, billed on CPU time rather than wall time, so waiting on a database is
  free. Measured comparisons put it 10 to 200 percent cheaper than Lambda for
  I/O-bound work. The cost is that the app must be JS or WASM.

**None of these is the answer for every app, and picking one for all apps is the
mistake.** A bird station that ingests audio continuously and a notes app that
serves a page are not the same workload and should not pay the same way.

## 4. The proposal: the app declares its tier, the platform provides three

The platform already has the right mechanism: an app declares what it is in its
manifest, and the platform reads the declaration. This is the same rule as
"no platform source file may name a specific app", applied to runtime.

**Tier 0, static.** A UI bundle and nothing else. Object storage plus CDN. Cost
per idle app is effectively zero. **This should be the default and most apps
should land here**, because the loader makes it possible.

**Tier 1, on demand.** A backend that scales to zero. Cost per idle app is
effectively zero; the app pays a cold start on the first request after idling.
This is the right default for anything with a server.

**Tier 2, resident.** Always on, because the app's job is to be always on: it
ingests a stream, holds a device connection, runs a schedule. Costs real money
per month and **must be an explicit declaration**, not a fallback, so the
expensive case is always a choice someone made.

The bird station is Tier 2 and is the reason Tier 2 exists. Most apps are Tier 0.

## 5. One instance per user, or one shared platform?

The standard answer, and the right one here, is **a shared control plane and a
per-tenant data plane**. The control plane is the Workshop API, the shell, the
registry, the scheduler. The data plane is a tenant's apps, their containers,
their storage, their database rows.

Concretely: **one Workshop deployment serving all users**, not one per user.
Per-user isolation lives below it, in where their apps run and where their data
sits, not in a duplicated copy of the platform.

His current setup is the degenerate case where control plane and data plane are
one box, and that is a legitimate deployment mode to keep supporting: it is what
"self-hosted Workshop" means, and it is the mode this repo is developed in.

## 6. How it integrates back into the served app

This was the operator's own question: if an app runs somewhere else, how does it
appear inside Workshop?

- **Its UI** is imported by the shell from a URL. This is exactly the app loader.
  Nothing about the app being hosted elsewhere changes the shell's job.
- **Its backend** is reached over HTTP at a per-app address, proxied through the
  platform so it inherits auth and so the app never has to implement it.
- **Its data** stays in the tenant's partition.

So "separate but integrated" is not a compromise, it is the normal shape, and the
loader is the piece that makes it work.

## 7. Where source lives, which is question 1

- **The platform repo** contains the platform. No app source, which is stage 7.
- **Each app is its own repo.** That is what makes an app installable by someone
  else, forkable, and updatable, and it is the same unit the marketplace needs.
- **Branches and worktrees** are per-repo and unchanged by any of this.

This is worth noticing: the extraction question and the hosting question have the
**same answer**. An app becomes a repo that builds to an artifact, and the
artifact is what gets installed and served. Extraction is not merely tidying; it
is the prerequisite for an app being deployable at all.

## 8. AWS specifically, since he named it

If the answer must be AWS: S3 plus CloudFront for Tier 0, App Runner or ECS with
scale-to-zero for Tier 1, ECS/Fargate for Tier 2, ECR for images, RDS with a
schema or database per tenant.

It is worth saying honestly that **AWS is the weakest of the options on exactly
the axis that matters here**, cost per idle tenant, because its scale-to-zero
story is the least mature and its per-tenant floor is the highest. Cloudflare
Workers for Platforms is purpose-built for this shape and bills CPU rather than
wall time; Fly and Cloud Run give scale-to-zero containers without forcing a
programming model.

**A reasonable split, if cost at scale is the constraint:** Tier 0 on any CDN
(portable, no lock-in), Tier 1 on a scale-to-zero container runtime, and the
control plane wherever the team is comfortable. The control plane is the part
where AWS is genuinely good and the part whose cost does not scale with tenant
count.

## 9. What is decided and what is his

**Follows from the design, not really optional:** the UI half is static and
CDN-served; the tier is declared by the app; the control plane is shared and the
data plane is per tenant; each app is its own repo.

**His, and not answerable from here:**

1. Whether Workshop is ever a hosted product or stays self-hosted. Everything
   above only pays for itself in the hosted case.
2. Which runtime backs Tier 1, which is a cost and lock-in decision.
3. Whether Tier 2 exists at launch or apps that need it stay self-hosted.
4. Whether tenant data is one database with a tenant column, a schema per tenant,
   or a database per tenant. This is the highest-consequence and least reversible
   choice on the list, and it is not urgent until there is a second tenant.

## 10. What NOT to do now

Nothing in this document should be built yet. The prerequisite is the app loader
plus extraction, and both are filed. Building a deployment tier for apps that are
still inside the platform repo would be building the roof first.

The one thing worth doing early, when the loader lands, is **making the manifest
carry the tier**, so the declaration exists before anything reads it.
