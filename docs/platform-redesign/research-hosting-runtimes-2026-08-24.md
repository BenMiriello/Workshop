# Research: hosting many small user-app backends efficiently (2026-08-24)

Status: research only, no code or architecture changed by this document. Written to answer one
question: if a platform must host many small user-built app backends (arbitrary code, arbitrary
language, sometimes a real long-running server, not just static assets), what is the most
resource-efficient way to keep them all reachable, and what does an idle app actually cost.
Baseline is AWS; alternatives covered where they change the answer.

All prices are on-demand, single-region (us-east-1 unless noted), pulled August 2026. Cloud
pricing pages change without notice; anything not fetched from a vendor's own pricing page is
marked accordingly. No numbers here are guaranteed current beyond this month.

---

## 1. The idle problem, quantified

Premise: most user-built apps on a platform like this get near-zero traffic. The dominant cost
of the fleet is not the busy apps, it's the number of apps sitting at zero requests/day. So the
question that matters isn't "what does compute cost per vCPU-hour," it's "what does one app cost
while nobody is using it."

| Option | Idle cost / app / month | Why | Verified |
|---|---|---|---|
| **ECS Fargate**, always-on, smallest task (0.25 vCPU / 0.5 GB, x86) | **~$9.00** | No scale-to-zero at the task level; you pay full vCPU+memory rate 24/7 regardless of traffic. ARM/Graviton task: ~$7.20. | Computed from AWS's published per-second rates ($0.000011244/vCPU-sec, $0.000001235/GB-sec x86; Graviton ~20% less) x 2,628,000 sec/month. |
| **EC2, many apps packed on one box** | **~$1-3** (varies hugely with packing ratio) | You own the packing. A t4g.xlarge (4 vCPU/16 GB, on-demand $98/mo, ~$0.134/hr) holding 40-50 minimal idle Node/Python processes under memory overcommit puts idle cost per app around $2/mo before you've done anything clever; 1-yr Reserved or Spot pricing (see §2) pushes this lower. This is the cheapest floor available, and also the most engineering to run. | Instance pricing verified via vendor cost-comparison sites; packing ratio is this document's own estimate, not sourced. |
| **AWS App Runner**, smallest provisioned instance (0.25 vCPU / 0.5 GB) | **~$2.52** | App Runner bills the idle ("provisioned") state at $0.007/GB-hour, memory only, no vCPU charge while idle: 0.5 GB x $0.007 x 730 hr = $2.55/mo. Active traffic bills vCPU+memory at request-serving rates on top. | Fetched from aws.amazon.com/apprunner/pricing. |
| **AWS Lambda** | **$0** | True pay-per-invocation; zero requests bills zero, no idle charge exists as a line item. First 1M requests + 400,000 GB-seconds/month are free tier. | Verified against AWS Lambda pricing summaries; official page not re-fetched but rate ($0.20/1M requests, $0.0000166667/GB-sec) is the long-standing published rate. |
| **Google Cloud Run**, scale-to-zero (default, min-instances=0) | **~$0** (compute) | Removes instances entirely at idle rather than keeping a warm minimum; only cost is container image storage in Artifact Registry, low cents/GB/month. If you pin `min-instances >= 1` you re-introduce an idle charge (billed as idle CPU/memory rate) similar to Fargate. | WebSearch of Google's own pricing/cost-optimization docs. |
| **Fly.io machines**, auto-stop enabled | **<$1**, often a few cents | Stopped machines aren't billed for compute; only their root filesystem is billed, at $0.15/GB-month (e.g. 1 GB rootfs stopped the whole month = $0.15). Attached persistent **volumes bill hourly whether the machine is running or not** - that's the trap: an idle app with a volume still costs real money. | fly.io/docs/about/cost-management and fly.io/docs/about/billing. |
| **Railway** | **effectively $0 marginal, but $5/mo account floor** | Railway meters per-second and sleeps idle services, so an idle app itself burns close to nothing, but the Hobby plan has a $5/mo base fee that includes $5 of usage credit - that floor is per-account, not per-app, so it amortizes down as you add idle apps under one account. | WebSearch summarizing Railway's published plan pricing. |
| **Render** | **$0 (free tier, degraded)** or **$7/mo flat (Starter, no scale-to-zero)** | Free web services spin down after 15 min idle (30-60s cold start on wake) and free Postgres expires after 30 days / caps at 1 GB. The cheapest *always-available* paid tier does not scale to zero at all - you pay $7/mo per service whether it's used or not. | WebSearch of Render's pricing page summaries. |

**Reading the table**: true scale-to-zero request-driven platforms (Lambda, Cloud Run default,
Fly with auto-stop) get idle cost close to zero. Anything that keeps a container or VM
provisioned even in an "idle" state (Fargate always-on, Render Starter) charges close to
list-price memory rent whether or not anyone visits. App Runner's idle-priced provisioned state
is the one AWS-native product that behaves like a hosted version of the "many apps packed
cheaply" goal without you doing any packing yourself - cheap because it only charges for memory
at idle, not CPU. Fargate has no such idle discount; it is priced for the always-on model, not
the low-traffic-app model, which is the biggest single reason it's the wrong default answer to
this question at any real app count.

---

## 2. Density and packing

If you decide to own packing yourself (the EC2 row above), the actual techniques in use:

- **Memory overcommit.** Idle Node/Python/Ruby processes typically use tens to a couple hundred
  MB of resident memory. cgroup memory limits can be set above physical RAM on the assumption
  that not all apps spike simultaneously; the risk is the OOM killer taking out a neighbor's app
  when the assumption fails. This is standard practice on shared hosting and PaaS platforms but
  requires real monitoring, not a set-and-forget config.
- **cgroups v2** for CPU/memory/IO isolation between many processes on one kernel - the substrate
  underneath Docker, containerd, and Kubernetes; used regardless of which higher-level tool sits
  on top.
- **Shared base layers.** Container image layers (OS + language runtime) are content-addressed
  and shared across images on the same host, so 1,000 containers built `FROM node:20-slim` share
  one copy of that layer on disk and largely share it in the page cache in memory too - real
  density lever for a platform hosting apps that are mostly similar language runtimes.
- **Idle hibernation via CRIU** (Checkpoint/Restore In Userspace): freezes a running process tree
  to disk, releasing its RAM, and restores it later at near-instant speed relative to a cold
  boot. The `zeropod` project (eBPF + CRIU) applies this specifically to hibernate idle
  Kubernetes pods on a timeout and wake them on first packet, i.e. it makes *any* existing
  container workload behave like scale-to-zero without rewriting it as a serverless function.
  This is the single most relevant packing technique for "arbitrary server code" (CRIU works on
  ordinary processes; it doesn't require the app be written in a request/response serverless
  shape).
- **ECS bin-packing / ENI trunking**: ECS's own placement strategy is a first-fit-decreasing bin
  packer across CPU/memory/ports; ENI trunking lifts the default cap on how many task ENIs (and
  therefore tasks) one EC2 instance can host, which otherwise becomes the real density ceiling on
  awsvpc-networked ECS long before CPU/memory does.
- **Snapshotting** (VM- or microVM-level memory snapshot, distinct from CRIU's process-level
  checkpoint): used to resume a whole guest, including its kernel state, from disk in
  milliseconds. This is what Firecracker and Lambda SnapStart use (§3, §4).

What platforms that actually do this at scale use: **Fly.io** runs one Firecracker microVM per
app/machine and leans on Firecracker's low per-VM overhead plus its own scheduler for bin
packing across a fleet of bare-metal hosts. **AWS Lambda/Fargate** run on Firecracker internally
 -  this is worth restating because it means "run on Fargate/Lambda" already buys you Firecracker's
density and isolation properties without operating the VMM yourself. **Replit**, hosting millions
of user apps, historically ran one Docker container per active session on preemptible GCE VMs
behind their own container manager ("conman"), and their own engineering blog ("Killing
Containers at Scale") describes the ongoing work of keeping that container fleet's lifecycle
(especially teardown of abandoned/idle containers) under control - i.e. even a well-resourced
team treats idle-container reclamation as a continuous engineering problem, not a solved one.

---

## 3. MicroVMs - Firecracker

- **Boot time**: ≤125ms from the `InstanceStart` API call to the guest's `/sbin/init`, per AWS's
  own Firecracker documentation.
- **Memory overhead**: less than 5 MiB per microVM (VMM thread overhead on top of guest-allocated
  RAM, measured on a 1 vCPU / 128 MiB guest). This is what makes "thousands of microVMs on one
  host" plausible as a design target rather than marketing.
- **Density / creation rate**: up to 150 microVM creations/second on a single host; a 36-core
  server was cited hitting 180/sec. Actual steady-state density per host is not a fixed number  - 
  AWS's own docs say it's bounded by available hardware resources (CPU, RAM, disk), not by
  Firecracker itself. Fly.io runs its fleet on bare-metal hosts in the 8-32 core / 32-256 GB RAM
  range and packs many machines per host within that envelope.
- **Who runs it in production**: AWS Lambda and AWS Fargate, at a scale described as trillions of
  invocations monthly; Fly.io, as the literal substrate for Fly Machines.

### Is self-managed Firecracker realistic for a small team? No - and the honest reason isn't the technology, it's the fleet.

Firecracker itself (the VMM binary) is well-documented and not hard to run standalone. The trap
is everything around it that a real multi-tenant hosting product needs and that AWS/Fly built and
maintain as ongoing platform work:

- the **jailer** security-sandboxing layer around each VMM process,
- building and patching **guest kernel images and root filesystems** for every language runtime
  you support,
- **networking** (per-microVM TAP devices, IP allocation, NAT/routing at fleet scale),
- an **orchestrator** to schedule, place, and bin-pack microVMs across hosts (Firecracker has no
  built-in scheduler - that's a from-scratch project),
- **snapshot lifecycle management** if you want fast resume rather than cold boot every time,
- and **fleet lifecycle** - patching the host kernel/KVM stack across every bare-metal node
  without dropping tenant VMs, which is a genuine on-call surface.

The pattern in the sources is consistent: running Firecracker directly is described as "real
platform work with real headcount," appropriate when your company's core business *is* the
hosting infrastructure itself (Fly.io, AWS) because the operational burden becomes your moat. For
a small team building app-hosting as a feature of a broader product, the same isolation and
density properties are already available by building on **Fargate or Lambda directly** (both are
Firecracker under the hood, managed by AWS) or on **Fly Machines** (Firecracker, managed by Fly).
Self-hosting the VMM layer yourself buys marginal cost savings at best and a second full-time
infrastructure team's worth of work at worst. Verdict: a trap for this use case, not a serious
option, unless the roadmap explicitly turns "run other people's containers well" into the
product's core differentiator.

---

## 4. Scale to zero: honest cold-start numbers

| Platform | Cold start (first request after idle) | Mitigation | Mitigated latency |
|---|---|---|---|
| Cloud Run | ~300ms-1s typical (Go/Node); Java/Spring can be 5-15s unmitigated | `--min-instances=1` + `--cpu-boost`; GraalVM native image for JVM | Java down to <500ms with native image; min-instances=1 removes cold start entirely at the cost of reintroducing an idle charge |
| AWS Lambda (zip/interpreted) | Node/Python roughly 200-400ms at p50; p95 can run 1.2-2.8s | Provisioned concurrency (paid, keeps warm instances); Graviton shaves another 15-40% | Effectively eliminated with provisioned concurrency, at the cost of paying for warm capacity - i.e. it stops being scale-to-zero |
| AWS Lambda SnapStart (Java) | Sub-100ms vs. 2-5s unmitigated Java cold start | Snapshot of a fully-initialized execution environment, restored on invoke | <100ms, and AWS states **zero additional charge** for SnapStart itself (no provisioned-capacity fee, no snapshot storage fee) - this is the standout free lunch in the whole table |
| Render free tier | 30-60 seconds | None on free tier; Starter plan avoids this by not scaling to zero at all | N/A (upgrade removes the cold start by removing scale-to-zero) |
| Fly.io machines (auto-start) | Not independently verified here; Fly markets sub-second machine starts for pre-built images, but this was not confirmed against a primary source in this pass | Fly Machines' own snapshot/start path | **Not verified** |

Techniques that reduce cold start, ranked by how much engineering they cost you:
1. **Snapshot/restore** (Lambda SnapStart, Firecracper snapshot-resume): restores a
   fully-initialized memory image instead of re-running startup code; the cheapest win when the
   platform gives it to you for free, as Lambda does for Java.
2. **Pre-warmed pools**: keep N instances started but idle, rotate requests onto them, spin up
   replacements in the background. This is what Val Town's runtime does - "a pool of
   pre-initialized processes to avoid startup time penalties" - and is also the standard
   provisioned-concurrency pattern. It reintroduces a real idle cost (you're paying to keep the
   pool warm), so it's a dial, not a free win: you're trading some of the idle-cost savings from
   §1 back for lower tail latency.
3. **Provisioned concurrency / min-instances=1**: the blunt version of a warm pool, applied
   per-service; removes cold start entirely but removes the whole point of scale-to-zero pricing
   for that app.

The honest summary: for typical interpreted-language HTTP backends (Node/Python), user-visible
latency on first hit after idle is in the **low hundreds of milliseconds to low single-digit
seconds**, and it gets meaningfully worse (5-15s) for JVM-family stacks unless you specifically
pay for SnapStart or a warm pool. If the product can tolerate "first request in a while is a
little slow," true scale-to-zero is the right default. If it can't (e.g. an app someone is
actively poking at in a demo), a warm pool or min-instances=1 for that one app is the correct
scoped fix - not making every app always-on.

---

## 5. gVisor and sandboxes for untrusted code

gVisor implements a user-space kernel that intercepts application syscalls rather than letting
them hit the host kernel directly (unlike a standard container, which shares the host kernel with
much thinner isolation). Reported overhead: **roughly 10-30%** on I/O-heavy or syscall-heavy
workloads, and comparatively small overhead on compute-heavy, syscall-light workloads. The
practical guidance found across multiple 2026 sources: gVisor is a reasonable choice when
workloads are compute-bound with limited I/O and you want stronger isolation than a bare
container without paying for a full VM boot; Firecracker (or another microVM) is the better
choice when workloads are I/O-heavy, since gVisor's syscall interception is exactly what costs
you there. gVisor's integration story is also simpler operationally than Firecracker - it's
usable as a drop-in `runsc` OCI-compatible container runtime (e.g. GKE Sandbox), not a
from-scratch VMM/orchestrator project - which matters if the team doesn't want to take on the
Firecracker fleet burden from §3 but still needs stronger-than-default isolation for arbitrary
user code.

---

## 6. Kubernetes-based options: Knative, KEDA, per-tenant namespaces

- **Knative Serving** gives request-driven scale-to-zero on top of Kubernetes, including cold
  start handling via an "Activator" that buffers the first request while a pod comes up. Sources
  are consistent that it reduces manual scaling/routing toil but adds real platform-maintenance
  surface: control-plane and autoscaler health become part of on-call, cold-start tuning is an
  ongoing job (worse for heavier runtimes), and you've taken on Kubernetes itself as a
  prerequisite.
- **KEDA** is narrower: it scales existing Kubernetes workloads (including to zero) based on
  external event sources (queue depth, Kafka lag, etc.) rather than solving "run arbitrary
  user-submitted code as isolated tenants." It's the right tool once you already have
  event-driven workers on Kubernetes and want them to scale on signal instead of raw CPU; it does
  not by itself solve packaging or isolating many small arbitrary apps, and it doesn't touch node
  capacity - a separate node autoscaler is still needed for real cost savings.
- **Per-tenant namespaces** provide RBAC/network-policy isolation boundaries per tenant inside one
  cluster, which is a reasonable mid-scale isolation primitive, but namespace count and the
  per-namespace object overhead (RBAC objects, network policies, quotas, service accounts) become
  their own operational and etcd-load concern well before you'd hit thousands of tenants.

**Verdict for a small team**: not worth it as a starting point. Kubernetes-based scale-to-zero
(Knative) buys you flexibility and vendor neutrality at the cost of running Kubernetes itself plus
an additional serving control plane, which is more moving parts than the problem ("keep many
small idle apps cheap and reachable") requires when you have few users. It becomes worth
reconsidering once you're already committed to Kubernetes for other reasons (a platform-team
mandate, multi-cloud requirement) and have enough tenants that per-tenant namespace isolation is
solving a real, already-felt problem rather than a hypothetical one.

---

## 7. The storage side: a database per tenant

An app is not just compute - most will want persistent state. Three shapes, compared on cost and
blast radius:

| Model | Cost shape | Blast radius | Notes |
|---|---|---|---|
| **Database per tenant** | Highest - each tenant is a full instance (compute + storage floor), even a "serverless" one has some baseline cost unless it truly scales to zero | Smallest - one tenant's bad migration, bad query, or breach touches only that tenant | Only cheap if the underlying database product has real per-instance scale-to-zero (see below); otherwise this model doesn't survive contact with "most tenants are idle" |
| **Schema per tenant** | Middle - one database instance, N schemas; shared compute cost amortized across tenants | Middle - a bad query can still starve shared compute/IO for everyone even though table access is namespaced; sources cite roughly 5,000-10,000 active schemas before per-schema operational overhead (migrations, monitoring, connection/memory overhead) becomes prohibitive on typical engines | Commonly flagged as the "worst of both worlds" middle ground by multiple sources - cleaner isolation than shared-table without the true isolation of database-per-tenant, while still capping out on schema count |
| **Shared database, tenant-ID column** | Lowest - one instance serves everyone, cost scales with total data/traffic not tenant count | Largest - a missing `WHERE tenant_id = ?` predicate leaks data across tenants; a noisy tenant can degrade shared tables/indexes for everyone | The default recommendation in the general multi-tenant SaaS literature "wherever possible," reserving database-per-tenant for cases where compliance, scale, or per-tenant customization specifically require it |

### Serverless database options and per-tenant economics

| Product | Per-idle-tenant cost | Scale-to-zero | Cap on instance/database count | Verified |
|---|---|---|---|---|
| **Aurora Serverless v2** | **$0** compute at true idle (storage only) *if* scale-to-zero is enabled; **~$43.80/mo** if you stay on the old 0.5 ACU floor without it | Yes, GA since November 2024: pauses after a configurable idle period, ~15s resume on first new connection, requires Aurora PostgreSQL 13.15+/14.12+/15.7+/16.3+ or Aurora MySQL 3.08+ | Not a per-tenant-database product by design - it's one cluster; "database per tenant" on Aurora means one *cluster* per tenant, and cluster count against account/region quotas becomes the real ceiling, not per-unit pricing | AWS's own Nov 2024 announcement + 2026 follow-up coverage |
| **Neon** | Near-zero - compute suspends after 5 minutes idle, storage billed separately at $0.35/GB-month, no monthly minimum since Dec 2025 | Yes, default behavior | Branches (not full databases) are the scaling primitive and are cheap because they share storage - Neon's own pricing frames "database per tenant" as "branch per tenant," at roughly $1.50/branch-month beyond plan-included branches, which is the cheapest true per-tenant-isolation story found in this research | WebSearch of Neon's pricing summaries, not the raw pricing page |
| **Cloudflare D1** | Effectively $0 idle - billed on rows read/written, not instance-hours; "no compute charge if you're not running queries" | Yes, request-driven, no persistent instance to idle | 10 GB cap per individual database; no stated hard cap on number of databases per account, and pricing carries no per-database fee, so "one D1 database per tenant" is plausible at low-to-mid tenant counts | WebSearch of Cloudflare's own D1 pricing docs |
| **Turso (libSQL)** | Near $0 - Developer plan is $4.99/mo flat for **unlimited databases**, 9 GB pooled storage | Databases are lightweight SQLite-based instances designed for exactly this shape | Pro plan explicitly markets "10,000 monthly active databases" as a tier - i.e. Turso is the one product in this table that names thousands-of-tenants-as-databases as an intended use case | WebSearch of Turso's pricing page summary |
| **PlanetScale** | Not scale-to-zero-cheap - cheapest single-node Postgres tier is $5/mo per database, production HA is $15-39/mo | No true scale-to-zero found in sources | Priced per cluster; a database-per-tenant model on PlanetScale means $5-39/mo x tenant count, which only works at low tenant counts or high-value tenants | WebSearch of PlanetScale's pricing summary |
| **RDS Postgres (non-serverless), smallest instance** | ~$11.68/mo (db.t4g.micro, single-AZ) regardless of use | No | One instance = one tenant if used that way; 1 GiB of RAM is described as the real bottleneck under any load, not CPU | vendor cost-comparison sites, not RDS's own page |

**Reading this**: for a "database per tenant" model to be cost-viable at low-traffic scale, the
database product itself has to scale to zero or be priced per-operation rather than
per-instance-hour. Aurora Serverless v2 (now that scale-to-zero shipped), Neon, D1, and Turso all
qualify; RDS and PlanetScale's cheapest tiers do not (you pay the instance floor whether the
tenant is active or not). Turso is the standout for "genuinely built for thousands of small
per-tenant databases" - it's the only product here that names that exact use case in its pricing
tier. Neon is the standout if you specifically want Postgres semantics per tenant with the same
economics. D1 is the standout if SQLite-over-HTTP semantics are acceptable and you're already on
Cloudflare Workers.

For a shared-database model, cost is dominated by total data + total query volume, not tenant
count, which is a much flatter and more predictable cost curve early on, at the cost of needing
real tenant-ID discipline in every query from day one (missing predicate = cross-tenant leak).

---

## 8. What an experienced founding engineer would actually do

**Starting point, near-zero users.** Don't build a fleet, a scheduler, or a custom sandbox layer.
Run user backends on **Cloud Run** (or **Fly Machines** if you want VM-level isolation with a
similar developer-facing simplicity) with default scale-to-zero, one service per app. Idle cost
per app is near $0 either way (§1), cold start is low-hundreds-of-ms to a couple seconds for
ordinary interpreted-language backends (§4), which is an acceptable trade at low user count. Put
every tenant's data in **one shared Postgres with a tenant_id column** (Neon or RDS, whichever
matches the rest of the stack), not one database per tenant - the isolation gap doesn't matter
yet because blast radius is bounded by "you have very few tenants," and the operational savings
(one database to migrate, monitor, and back up) matter a great deal at this stage. If arbitrary
user code needs to be genuinely untrusted (not just "a customer's own app," but "code someone
else's user could have written to attack you"), put it behind a request-level sandbox like
gVisor or the platform's own microVM boundary (i.e. lean on Cloud Run/Fargate/Fly's existing
Firecracker isolation) rather than writing a custom jail.

**What to deliberately not build first**: a self-managed Firecracker fleet (§3), a
Kubernetes cluster with Knative/KEDA (§6), a custom bin-packing scheduler on raw EC2 (§2), or a
database-per-tenant architecture (§7) before there's a concrete tenant asking for the isolation
it buys. Every one of these is the right answer at a scale this team doesn't have yet, and every
one of them is a multi-week-to-multi-quarter distraction if built speculatively. The one
genuinely free win worth taking immediately regardless of scale is anything with **no cost and no
architecture commitment** - e.g. enabling Lambda SnapStart where applicable, or Aurora/Neon
scale-to-zero - because "turn on a flag" isn't the same category of decision as "adopt a new
control plane."

**What to grow into, in order, with the trigger for each move:**

1. **Move hot/latency-sensitive apps off pure scale-to-zero and onto a small warm pool or
   min-instances=1**, per app, the moment a specific app's cold start becomes a user complaint  - 
   not proactively. Trigger: a support ticket or observed churn tied to first-hit latency on a
   named app, not a hypothesis.
2. **Move from "container per app on a managed serverless platform" to "many apps packed on
   self-managed EC2 with cgroup-based density"** only when the managed platform's per-app pricing
   (Cloud Run/App Runner/Fargate) is demonstrably the largest line item in the infra bill and the
   team has the headcount to own instance patching, packing, and the neighbor-noise problem.
   Trigger: infra cost per idle app under the managed platform, multiplied by app count, exceeds
   what one engineer's time is worth to build and operate the packed alternative - this is a
   spreadsheet decision, not a technology preference, and for most teams it doesn't cross over
   until app count is in the thousands.
3. **Move from shared-database-with-tenant-column to schema- or database-per-tenant** the moment
   a specific tenant needs it for a real reason: a compliance requirement (data residency,
   contractual isolation), a single tenant's query load measurably degrading others, or a tenant
   large enough to warrant dedicated resources. Trigger is per-tenant, not fleet-wide - most
   platforms end up in a hybrid where 99% of tenants stay on the shared database and a handful of
   large/regulated tenants get dedicated ones, rather than migrating everyone at once.
4. **Consider Kubernetes (Knative/KEDA/per-tenant namespaces) or a microVM fleet (Firecracker)**
   only if the roadmap has turned "hosting other people's arbitrary code well" into a core product
   differentiator rather than infrastructure plumbing, or if a platform-level requirement (e.g.
   multi-cloud, on-prem, a specific compliance posture unavailable from a managed vendor) forces
   it. Trigger: a business reason to own the primitive, not a desire for control over the
   primitive.

One line: **start on Cloud Run (or Fly Machines) with scale-to-zero and a shared tenant-column
database, and don't touch Kubernetes, Firecracker, or database-per-tenant until a specific,
named app or tenant forces the issue.**

---

## Sources

- [AWS Fargate Pricing](https://aws.amazon.com/fargate/pricing/) - official, fetched directly; per-second vCPU/GB rates, 0.25 vCPU / 0.5-1-2 GB minimum config
- [AWS Fargate Pricing: Real Costs Per Environment (2026 Breakdown)](https://fortem.dev/blog/aws-fargate-pricing-real-costs/)
- [A Guide to AWS Fargate Pricing in 2026 - CloudToggle](https://www.cloudtoggle.com/blog-en/aws-fargate-pricing/)
- [AWS Fargate Spot: Pricing & Real Savings - fortem.dev](https://fortem.dev/fargate-spot/)
- [AWS App Runner Pricing](https://aws.amazon.com/apprunner/pricing/) - official, fetched directly; $0.007-0.009/GB-hour idle rate
- [AWS App Runner: How It Works, Pricing, And Best Practices - CloudZero](https://www.cloudzero.com/blog/aws-app-runner/)
- [Fly.io Cost Management](https://fly.io/docs/about/cost-management/)
- [Fly.io Billing](https://fly.io/docs/about/billing/)
- [Fly.io Resource Pricing](https://fly.io/docs/about/pricing/)
- [What Is a Firecracker VM? - Fly.io Learn](https://fly.io/learn/firecracker-vm/)
- [Fly Machines: an API for fast-booting VMs - The Fly Blog](https://fly.io/blog/fly-machines/)
- [The Fly.io Architecture](https://fly.io/docs/reference/architecture/)
- [Google Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [Google Cloud Run Pricing Savings Guide - pump.co](https://www.pump.co/blog/google-cloud-run-pricing/)
- [Best practices for cost-optimized Cloud Run services](https://docs.cloud.google.com/run/docs/tips/services-cost-optimization)
- [Cloud Run Scaling Explained - CloudWebSchool](https://cloudwebschool.com/docs/gcp/compute/cloud-run-scaling-behaviour/)
- [Startup CPU boost for Cloud Run and Cloud Functions - Google Cloud Blog](https://cloud.google.com/blog/products/serverless/announcing-startup-cpu-boost-for-cloud-run--cloud-functions)
- [Railway Pricing Calculator (2026) - MakerKit](https://makerkit.dev/pricing-calculator/railway)
- [Railway pricing explained - livemy.app](https://livemy.app/blog/railway-pricing)
- [Render vs Railway 2026 - Encore](https://encore.dev/articles/render-vs-railway)
- [Render pricing explained - livemy.app](https://livemy.app/blog/render-pricing)
- [Render Pricing Guide - Kuberns](https://kuberns.com/blogs/render-pricing/)
- [AWS Lambda Pricing 2026 Guide - DEV Community](https://dev.to/tomerbendavid/aws-lambda-pricing-2026-guide-5dnf)
- [AWS Lambda is Your Worst-Performing "Cost-Saver" - Medium/InfraDecodedOps](https://medium.com/infradecodedops/aws-lambda-is-your-worst-performing-cost-saver-the-2026-cold-start-data-will-shock-you-0822418e57c8) (August 2025 INIT-phase billing change)
- [Cold Starts in AWS Lambda - Mikhail Shilkov](https://mikhail.io/serverless/coldstarts/aws/)
- [Cold Starts Are Dead - DEV Community](https://dev.to/aws/cold-starts-are-dead-5fod)
- [AWS Lambda Cold Start Optimization in 2026: SnapStart, Graviton](https://viprasol.com/blog/aws-lambda-cold-start-optimization/)
- [Firecracker microVMs (project site)](https://firecracker-microvm.github.io/)
- [firecracker/SPECIFICATION.md - GitHub](https://github.com/firecracker-microvm/firecracker/blob/main/SPECIFICATION.md)
- [What is AWS Firecracker? - Northflank](https://northflank.com/blog/what-is-aws-firecracker)
- [How Firecracker MicroVMs Power AgentCore Runtime](https://www.akshayparkhi.net/2026/Mar/11/how-firecracker-microvms-power-agentcore-runtime-from-125ms-boot/)
- [How to sandbox AI agents in 2026: MicroVMs, gVisor & isolation strategies - Northflank](https://northflank.com/blog/how-to-sandbox-ai-agents)
- [gVisor Production Guide](https://gvisor.dev/docs/user_guide/production/)
- [How to Use GKE Sandbox (gVisor) - oneuptime](https://oneuptime.com/blog/post/2026-02-09-gke-sandbox-gvisor-workload-isolation/view)
- [Firecracker vs gVisor: Which Sandbox in 2026?](https://www.alekseialeinikov.com/en/blog/topics/devops/microvms-firecracker-vs-gvisor-secure-workloads-2026)
- [How to Use Docker Checkpoint and Restore (CRIU) - oneuptime](https://oneuptime.com/blog/post/2026-01-16-docker-checkpoint-restore/view)
- [How to Implement Container Checkpoint and Restore with CRIU on Kubernetes - oneuptime](https://oneuptime.com/blog/post/2026-02-09-container-checkpoint-restore-criu/view)
- [Reducing Cloud Costs with Zeropod: Hibernating Pods via eBPF and CRIU - Medium](https://medium.com/@krish.arava/reducing-cloud-costs-with-zeropod-hibernating-pods-in-kubernetes-using-ebpf-and-criu-d8456ab5495b)
- [CRIU project](https://criu.org/Main_Page)
- [Optimizing Amazon ECS task density using awsvpc network mode - AWS](https://aws.amazon.com/blogs/compute/optimizing-amazon-ecs-task-density-using-awsvpc-network-mode/)
- [Amazon ECS Task Placement - AWS](https://aws.amazon.com/blogs/compute/amazon-ecs-task-placement/)
- [Maximizing Container Density on Amazon ECS: ENI Trunking - DEV Community](https://dev.to/deepakp/maximizing-container-density-on-amazon-ecs-a-guide-to-eni-trunking-2nn5)
- [What is Knative? - NoOps School](https://noopsschool.com/blog/knative/)
- [Scaling - Knative Tutorial](https://redhat-developer-demos.github.io/knative-tutorial/knative-tutorial/serving/scaling.html)
- [KEDA: How Event-Driven Autoscaling Cuts Kubernetes Cost - CAST AI](https://cast.ai/blog/keda-kubernetes-event-driven-autoscaling/)
- [Karpenter vs Cluster Autoscaler vs KEDA - tech-insider.org](https://tech-insider.org/karpenter-vs-cluster-autoscaler-vs-keda-2026/)
- [RDS Serverless / Aurora Serverless v2 Complete 2026 Guide - Usage.ai](https://www.usage.ai/blogs/aws/rds/aurora-serverless-v2/)
- [Amazon Aurora Serverless v2 supports scaling to zero capacity - AWS What's New (Nov 2024)](https://aws.amazon.com/about-aws/whats-new/2024/11/amazon-aurora-serverless-v2-scaling-zero-capacity)
- [Aurora Serverless: Faster performance, enhanced scaling, still scales to zero - AWS News Feed (Apr 2026)](https://aws-news.com/article/2026-04-20-aurora-serverless-faster-performance-enhanced-scaling-and-still-scales-down-to-zero)
- [Neon Pricing Calculator (2026) - MakerKit](https://makerkit.dev/pricing-calculator/neon)
- [Neon Serverless Postgres Pricing 2026 - vela.run](https://vela.run/articles/neon-serverless-postgres-pricing-2026/)
- [Neon Pricing: The Honest Cost of Serverless Postgres - selfhost.dev](https://selfhost.dev/blog/neon-pricing-cost-of-serverless-postgres/)
- [Turso Database Pricing (official)](https://turso.tech/pricing)
- [Cloudflare D1 Pricing (official)](https://developers.cloudflare.com/d1/platform/pricing/)
- [PlanetScale plans (official)](https://planetscale.com/docs/planetscale-plans)
- [PlanetScale pricing and plans (official)](https://planetscale.com/pricing)
- [Multi-Tenant Architecture: Database Per Tenant vs Shared Schema - DEV Community](https://dev.to/young_gao/multi-tenant-architecture-database-per-tenant-vs-shared-schema-1n2e)
- [Designing Multi-Tenant SaaS Systems - Alok](https://aloknecessary.github.io/blogs/designing-multi-tenant-saas-systems/)
- [Multi-Tenant Database Architecture Patterns Explained - Bytebase](https://www.bytebase.com/blog/multi-tenant-database-architecture-patterns-explained/)
- [db.t4g.micro pricing - economize.cloud](https://www.economize.cloud/resources/aws/pricing/rds/db.t4g.micro/)
- [t4g.xlarge pricing - economize.cloud](https://www.economize.cloud/resources/aws/pricing/ec2/t4g.xlarge/)
- [t4g.small / t4g.micro pricing - economize.cloud](https://www.economize.cloud/resources/aws/pricing/ec2/t4g.small/)
- [Val Town: The first four Val Town runtimes](https://blog.val.town/blog/first-four-val-town-runtimes/)
- [Replit - Killing Containers at Scale](https://blog.replit.com/killing-containers-at-scale)
- [Replit - Worldwide Repls, part 3: Firing Up The Engines](https://blog.replit.com/geo-part-3-deploy)
- [Startup Infrastructure: Scaling from Zero to Enterprise - vadimkravcenko.com](https://vadimkravcenko.com/shorts/infrastructure-from-zero-to-enterprise/)
- [Kubernetes for Startups: When It Actually Makes Sense (2026) - Tasrie IT Services](https://tasrieit.com/blog/kubernetes-for-startups-when-to-adopt-2026)
- [Looking at using Kubernetes at your Series A startup? Read this first. - Maven Solutions](https://www.mavensolutions.tech/blog/leveraging-kubernetes-to-enhance-product-reliability-and-user-experience-at-series-a-startups)

### Not independently verified in this pass

- Fly.io Machines' cold-start latency for auto-started (previously stopped) machines - Fly
  markets this as fast, but no primary-source number was confirmed here.
- The exact packing ratio ("40-50 apps per t4g.xlarge") in §1/§2 is this document's own estimate
  based on typical idle-process memory footprints, not a sourced benchmark from any platform
  operator.
- App Runner's "automated deployment" monthly per-app fee mentioned in passing in one source was
  ambiguous and not confirmed against AWS's pricing page; treat App Runner's idle number as
  memory-rent only, and verify the deployment fee separately before budgeting on it.
- Aurora Serverless v2 scale-to-zero resume time (~15 seconds) came from a secondary summary, not
  AWS's own announcement text directly quoted.
