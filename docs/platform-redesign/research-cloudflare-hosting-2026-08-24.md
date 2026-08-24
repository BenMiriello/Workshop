# Research: could Cloudflare host an unbounded number of small user-built apps, cheaply, at scale?

Date: 2026-08-24. Status: research only, no code changed. This answers a specific
question the operator raised: he has deployed on Cloudflare personally and
suspects it lets you "spin up any number of apps for any number of users for
pretty cheap or free," but has not checked the contract at scale. This document
checks that contract against primary Cloudflare documentation and Cloudflare's
own blog, current as of August 2026, with dates on anything that has moved
recently.

Everything below is sourced. Where a number could not be independently
confirmed (a doc page didn't state it, or a page couldn't be fetched), it is
marked **NOT VERIFIED** rather than guessed.

---

## 1. Workers for Platforms: the central product

Workers for Platforms (W4P) is Cloudflare's explicit product for "run your
users' code." The mechanism is a **dispatch namespace**: a container that
holds many "user Workers" (one per tenant, typically), fronted by a single
"dynamic dispatch Worker" that you write and control.

**Routing.** Your dispatch Worker receives every request, decides which
tenant it belongs to (hostname, path, header, whatever you choose), and calls:

```js
const userWorker = env.DISPATCHER.get(customerName);
return userWorker.fetch(request);
```

`env.DISPATCHER` is a binding to the dispatch namespace; `.get(name)` resolves
the named user Worker at request time with no separate deploy or routing
config per tenant beyond having uploaded that Worker's script under that name.
Optionally you can also run an "outbound Worker" that sees every outbound
`fetch()` a tenant's code makes, for logging/allowlisting/injecting
credentials without exposing them to tenant code.
[Source: How Workers for Platforms works](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/how-workers-for-platforms-works/)

**How many Workers can a namespace hold.** Cloudflare states scripts inside a
dispatch namespace are **not** subject to the normal per-account Worker-script
cap (500 on a standard Paid account). The Workers for Platforms limits page
says explicitly: "unlimited number of scripts for Workers for Platforms
customers," and separately, Durable Object namespaces created for W4P tenants
have no stated numeric cap either.
[Source: Workers for Platforms limits](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/limits/)
[Source: How Workers for Platforms works](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/how-workers-for-platforms-works/)

Cloudflare's own guidance is to use **one namespace** ("production") for all
customers, not one namespace per customer — namespaces are an organizational
unit (e.g. prod/staging), not a per-tenant unit. **NOT VERIFIED**: an explicit
numeric cap on dispatch namespaces per account was not stated on any fetched
page; the guidance to use a single namespace makes the question largely moot
for this use case.

**Cost — this is the load-bearing number.** Workers for Platforms is a paid
add-on requiring the Workers Paid plan ($5/mo) as a prerequisite, on top of
which:

- **$25/month** subscription
- Included: **20 million requests/month**, **60 million CPU-ms/month**, **1,000 scripts**
- Overage: **$0.30 per additional million requests**, **$0.02 per additional million CPU-ms**, **$0.02 per additional script**
- Billing chain: a request that flows dispatch Worker → user Worker → outbound
  Worker is billed as **one request**, and CPU time is summed across that
  chain. Subrequests your Worker makes internally are not separately billed
  as requests.

[Source: Workers for Platforms pricing](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/pricing/)

**Per-tenant cost at rest, worked out.** A dormant tenant Worker (deployed,
receiving zero traffic) costs **$0.02/month** once you're over the 1,000
included scripts — i.e. tenant #1,001 onward. The first 1,000 tenants are
covered by the base $25/month subscription, so their marginal at-rest cost is
effectively **$0** (folded into the flat fee) until you cross that line, after
which each additional idle tenant is 2 cents a month. There is no per-tenant
minimum beyond that; an idle Worker does not accrue CPU-ms or request charges,
because Workers bill for CPU actually consumed and requests actually served,
not for existing. **NOT VERIFIED**: whether the $0.02/script overage is a
one-time provisioning charge or a recurring monthly line item — the pricing
page lists it in the same table as monthly requests/CPU but does not use the
word "monthly" next to it explicitly. Treat it as monthly (consistent with
how the rest of the table reads) but confirm before relying on it for a
cost model with tens of thousands of tenants.

**Ceiling on tenant count.** No stated hard ceiling. The practical ceiling is
economic (linear $0.02/tenant/month past 1,000) and operational (script size,
KV namespace limits, and other per-primitive caps discussed in §4 bite before
Workers-script count does).

---

## 2. Execution model: V8 isolates, cold start, CPU-time billing

Workers run in **V8 isolates**, not containers or VMs. An isolate is a
lightweight in-process JS execution context; Cloudflare's own writeup puts
isolate warm-up at **under 5 milliseconds**, and independent 2026 benchmarking
(PkgPulse, cited via search, not independently re-verified by fetching that
report directly) puts Workers cold starts under 5ms p95 versus 1.2-2.8s p95
for Node 20 on Lambda. This is the mechanism, not a specific number to build a
budget on — treat the "single-digit ms" claim as Cloudflare-favorable but
directionally well established across multiple independent sources.
[Source: Eliminating cold starts with Cloudflare Workers](https://blog.cloudflare.com/eliminating-cold-starts-with-cloudflare-workers/)
[Source: Unpacking Cloudflare Workers CPU performance benchmarks](https://blog.cloudflare.com/unpacking-cloudflare-workers-cpu-performance-benchmarks/)

**Billing is CPU time, not wall-clock time.** This is the mechanism that makes
the pricing model work for I/O-bound apps: time spent `await`-ing a database
call, an external API, or any network I/O is **not billed and not limited**.
Only actual processor cycles your code consumes are metered and capped. For
an app that is mostly "wait on Postgres/D1, format JSON, return," the CPU-ms
consumed per request can be single-digit milliseconds even though the request
takes 50-200ms of wall time waiting on the database. This is the single
biggest reason the economics can work for small CRUD-shaped apps: you are not
paying for idle time the way you would on a VM or a container that reserves
capacity.
[Source: Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)

Caveat baked into this model: it rewards apps that are I/O-bound and
penalizes apps that are CPU-bound (image processing, template compilation,
crypto, anything that burns cycles synchronously) because that time is both
billed and capped (see §3).

---

## 3. Hard limits that decide whether a real app fits

These are the numbers that matter more than the marketing. Two profiles:
**Free** (no Workers subscription) and **Paid** (Workers Paid $5/mo, which is
also the prerequisite for Workers for Platforms).

| Limit | Free | Paid | Source |
|---|---|---|---|
| CPU time per request | 10ms | 30s default, configurable up to 5 min (300,000ms) | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| CPU time per Cron/Queue consumer invocation | n/a | up to 15 min | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Memory per isolate | 128 MB | 128 MB (same on both) | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Script size (compressed) | 3 MB | 10 MB | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Script size (uncompressed) | 64 MB | 64 MB | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Subrequests per invocation | 50 | 1,000 default, up to 10M configurable | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Simultaneous open connections | 6 | 6 | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Request duration (HTTP, client connected) | no hard limit while connected | no hard limit while connected | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| `ctx.waitUntil()` extension after response | up to 30s | up to 30s | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Workers per account (standard, NOT W4P) | 100 | 500 | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Workers per W4P dispatch namespace | unlimited (no per-account cap applies) | unlimited | [W4P limits](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/limits/) |
| Env vars per Worker | 64 | 128 | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Static asset files per Worker version | 20,000 | 100,000 | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |

**What this means concretely:**

- **CPU time, not memory or duration, is the real ceiling.** A request can
  stay open indefinitely (streaming, SSE, long-poll) as long as the *client*
  stays connected and the Worker's actual CPU consumption during that time
  stays under the cap. Streaming a large response or holding a WebSocket open
  is fine because idle/waiting time is free; a synchronous 31-second JSON
  transform is not.
- **128 MB per isolate is tight** for anything that wants to load a large
  in-memory dataset, do image manipulation in JS, or run a bundled ML model.
  It is fine for typical CRUD/API workloads.
- **The `caches.default` Cache API is disabled for namespaced (W4P tenant)
  scripts** — a specific, easy-to-miss caveat: tenant Workers running inside a
  dispatch namespace cannot use Cloudflare's edge cache API the way a normal
  standalone Worker can.
  [Source: Workers for Platforms limits](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/limits/)
- **Gradual Deployments (canary rollout) are not supported for user
  Workers** in a dispatch namespace — another W4P-specific gap versus normal
  Workers.
  [Source: Workers for Platforms limits](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/limits/)

---

## 4. Storage: an app is not just compute

| Primitive | Purpose | Key limits | Pricing (Paid plan) | Per-tenant via API? | Cap on instances |
|---|---|---|---|---|---|
| **D1** (SQL/SQLite) | Relational data per app | 10 GB max DB size (Paid), 100 columns/table, 100KB max statement, 2MB max row/blob/string | Storage $0.75/GB-mo after 5GB free; reads $0.001/million after 25B free/mo; writes $1.00/million after 50M free/mo; no egress charge | **Yes** — `POST /accounts/{account_id}/d1/database` creates a named DB programmatically | **50,000 databases/account** (Paid), 1TB total storage/account (both negotiable upward on request) |
| **KV** | Fast global key-value, eventually consistent | Key 512 bytes, value 25 MiB, 1,000 ops/invocation | Reads/writes priced per operation past free tier (see KV pricing page — not independently re-fetched here) | Presumably yes via API (namespace create endpoint exists) but **NOT VERIFIED** whether provisioning is friction-free at high volume | **1,000 namespaces/account** — this is a real ceiling if the plan is "one KV namespace per tenant" |
| **R2** (object storage, S3-compatible) | Files/blobs, egress-free | 5 TiB max object size, unlimited objects/bucket | Storage-only pricing, **no egress fee** at all (Cloudflare's marquee differentiator vs S3) | Bucket creation is scriptable via the R2/S3-compatible API; the docs don't explicitly narrate "per-tenant bucket" but nothing blocks it | **1,000,000 buckets/account** — effectively unbounded for this purpose |
| **Durable Objects** | Strongly consistent per-object state, WebSocket coordination, "one instance = one tenant" primitive | 128MB heap per object (isolate limit applies); SQLite-backed: 10GB per object | 1M requests + 400K GB-s included (Paid); overage priced per unit; SQLite storage 5GB included then metered | **Yes**, and this is the primitive Cloudflare is explicitly pushing for "one DB per AI-generated app" via **Durable Object Facets** (Aug 2026 feature — see below) | **500 DO classes/account** (Paid) but **unlimited objects per class** — so tenant count is bounded by object count (unlimited), not class count |
| **Hyperdrive** | Connection pooling / caching in front of an existing Postgres/MySQL you already run elsewhere | ~20 connections pooled (Free), ~100 (Paid), min 5 | Included in Free/Paid Workers plans; Free caps at 100K queries/day, Paid uncapped; no egress charge | Config creation is API-driven, but Hyperdrive fronts **your own external database** — it is not itself a per-tenant data store | n/a (it's a proxy/pool, not a data store) |
| **Queues** | Async work / fan-out per tenant | 128 KB max message, 5,000 msg/s per queue, 25GB backlog/queue, retention up to 14 days (Paid) | 1M ops/mo included (Paid) then $0.40/million ops; no egress charge | Yes, queues are created via API/wrangler | **10,000 queues/account** |

**The one genuinely useful "per-tenant database, provisioned by API, at
scale" answer here is Durable Objects, not D1.** D1's 50,000-database cap
sounds generous but is a real number if the platform is meant to be
"unbounded" — 50K tenants is a ceiling, not infinity, and each DB is capped at
10GB. Durable Objects, by contrast, have **no stated cap on object count**
per class, and Cloudflare shipped a feature in 2026 specifically for this
pattern:

**Durable Object Facets (2026).** A "Dynamic Worker" loads tenant code
on-the-fly into a sandboxed isolate, and a supervising Durable Object can
spawn each tenant's app as a child "facet" that gets **its own SQLite
database**, isolated from the parent and from other tenants, with requests
routed through the supervisor first (so the platform can meter/bill/log
before the tenant's code runs). Cloudflare's framing for this is explicitly
"give each AI-generated app its own database" — i.e., exactly the operator's
scenario (many small apps, likely AI-generated, one per user). No public
numeric ceiling on facet/object count was stated, and no concrete
provisioning-latency or per-facet cost figures were given in the source post;
treat the mechanism as validated and the scale numbers as **NOT VERIFIED**.
[Source: Durable Objects in Dynamic Workers](https://blog.cloudflare.com/durable-object-facets-dynamic-workers/)

**KV's 1,000-namespace cap is a trap for a naive design.** If the instinct is
"one KV namespace per tenant," that ceiling bites at 1,000 tenants, well
before D1 (50,000) or Durable Objects (unbounded) would. The fix is to use
one shared KV namespace with tenant-prefixed keys, not one namespace per
tenant — which is exactly the same shape of correction Cloudflare gives for
dispatch namespaces in §1.

---

## 5. Static asset hosting

Cloudflare has converged Pages and Workers static-asset serving as of March
2026 (per search-result summaries; not independently re-fetched from a
migration doc) — Workers now serves static assets with feature parity to
Pages, including custom domains and SSR.

- **Static asset files**: 20,000/version (Free), **100,000/version** (Paid
  and Workers for Platforms) — raised from a lower number in a **2025-09-02**
  changelog.
  [Source: Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- **Requests to static assets are free and unlimited** — they don't count
  against the CPU/request billing at all.
  [Source: Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- **Custom domains per tenant**: this is really the **Cloudflare for SaaS**
  product (custom hostnames + automated SSL), not a Pages/Workers-specific
  limit. Every plan includes **100 custom hostnames free**; beyond that it's
  **$0.10/hostname/month**, up to a **50,000-hostname pay-as-you-go cap**
  (higher available on Enterprise). SSL certificates are provisioned and
  renewed automatically per hostname with no manual step.
  [Source: Cloudflare for SaaS custom hostnames pricing (via search; primary
  pricing page not independently re-fetched — treat the $0.10/hostname figure
  as needing a direct confirm against
  developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/)]

**Per-tenant cost implication**: if every tenant app gets its own custom
domain (`tenant123.yourplatform.com` is free via wildcard DNS/routes; a
tenant's *own* domain like `tenant.com` is the $0.10/mo hostname), the first
100 tenants with custom domains are free, then 10 cents/month each. A
platform giving every tenant a subdomain of the platform's own domain (no
custom hostname needed) avoids this line item entirely.

---

## 6. Caveats an experienced engineer would raise

**Vendor lock-in.** Dispatch namespaces, Durable Objects, D1's SQLite dialect
quirks, and the `env` binding model are Cloudflare-specific. There is no
directly portable "dispatch namespace" concept on AWS/GCP/Fly.io/Render.
Durable Objects in particular (strongly consistent, co-located compute+state,
WebSocket hibernation) have no drop-in equivalent elsewhere — migrating off
means re-architecting that layer, not just redeploying code. D1 is "just
SQLite," which is comparatively portable (`.dump` and reload elsewhere), and
R2 is S3-API-compatible, so object storage migrates cleanly. The compute
layer (Workers runtime, `workerd`) is open-sourced by Cloudflare, but running
it yourself at Cloudflare's edge scale/latency is not realistic — open-source
availability reduces legal lock-in, not operational lock-in.

**What does NOT run on Workers.** No native binaries, no arbitrary
executables — JS and Wasm only on standard Workers. Concretely: no headless
Chrome, no ImageMagick, no FFmpeg, no arbitrary `pip install` of C-extension
Python packages (Python support is via Pyodide, which only works for
pure-Python or pre-compiled-for-Pyodide packages), no long-running daemons,
no arbitrary Docker images — **unless** you use Cloudflare Containers (§7),
which is a materially different product with different billing and different
guarantees.

**Egress/bandwidth.** Genuinely free across the board: no egress charges for
Workers, R2, D1, or Queues. This is a real and unusual differentiator versus
AWS (where S3 egress is roughly $0.09/GB and often exceeds compute cost for
data-heavy apps). Confirmed via multiple sources including Cloudflare's own
"zero egress fee" announcements.

**Abuse handling.** Cloudflare's Terms of Use reserve the right to suspend or
terminate service "at any time, with or without notice, for any reason,"
and their public commentary specifically calls out abuse patterns like using
the free Workers tier to run unauthorized proxy servers that consume
disproportionate bandwidth. There is **no published, specific numeric
threshold** for "how much is too much" on a legitimate paid account — this
is a "trust and verify" relationship rather than a hard quota you can budget
against precisely. For a platform hosting many small third-party/AI-generated
apps, this matters: a single tenant's app going haywire (infinite loop,
scraper, crypto-miner-shaped CPU pattern) is a *platform-operator* problem to
detect and throttle, and Cloudflare gives you the tools to do that
(per-tenant Custom Limits on CPU/subrequests, described in §1) but expects
you to use them — it will not silently absorb an abusive tenant forever
without eventually flagging the account.
[Source: Cloudflare Website Terms of Use](https://www.cloudflare.com/website-terms/)
[Source: Cloudflare abuse approach](https://www.cloudflare.com/trust-hub/abuse-approach/)

**Migration path off Cloudflare, if it goes wrong.** Roughly in order of
difficulty: static assets and R2 objects are trivially portable (S3-API
compatible, just copy). D1 databases are SQLite and can be dumped/reloaded
into any Postgres/SQLite-compatible target with a translation pass. The
Workers compute layer itself (`workerd`) is open source, so *code* can in
principle run on self-hosted infrastructure, but the platform-specific
bindings (dispatch namespaces, DO facets, KV, Hyperdrive) have no drop-in
replacement — that layer requires a rewrite against whatever the new host's
equivalent primitives are (e.g., a container-per-tenant model on Fly.io/
Render, or a different multi-tenant framework). There is no "one command"
exit; budget for a genuine re-architecture of the multi-tenancy layer
specifically, not just a redeploy.

---

## 7. Cloudflare Containers (closes the Docker gap, different product)

Containers **reached General Availability on 2026-04-13** (moved out of
public beta). This is the answer to "what if a tenant's app genuinely needs
arbitrary code" — a real Docker image, Go/Rust/Python/Java/Ruby, anything a
Dockerfile can express, including native binaries.

- **Requires Workers Paid** ($5/mo minimum).
- **Billing: "Active CPU" pricing** — you pay only for CPU cycles the
  container actively uses, billed in **10ms increments**, at **$0.00002 per
  vCPU-second**. This is architecturally similar to the Workers CPU-time
  model (you don't pay for a container sitting idle waiting on I/O the way
  you would for a reserved-capacity VM), which is notable — Cloudflare
  extended the "pay for compute, not wall-clock" philosophy to containers,
  not just isolates.
- Docker Hub images supported directly; SSH access for debugging live
  containers; can be wired to Workers/other bindings via hostnames.
- Cloudflare states GA raised limits to allow "thousands of containers
  running concurrently" per account — **NOT VERIFIED** as a precise number;
  this is a paraphrase from a changelog/blog summary, not a limits table with
  an exact figure. A hard numeric cap (if any) needs a direct check against
  the Containers limits page before being used in a cost/scale model.
- Containers close the "arbitrary Docker image" gap Workers explicitly does
  not cover, but they are a **different execution model** with **different
  cold-start characteristics** (container cold starts are not V8-isolate-fast
  — no specific ms figure was found and confirmed for Containers cold start;
  treat as materially slower than Workers' sub-5ms until confirmed otherwise)
  and a **different per-unit cost** than Workers CPU-ms billing.

[Source: Containers and Sandboxes are now GA (2026-04-13)](https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga/)
[Source: Containers are available in public beta (background/original announcement)](https://blog.cloudflare.com/containers-are-available-in-public-beta-for-simple-global-and-programmable/)

**Practically**: if "small user-built apps" means JS/TS/Python-in-Pyodide
CRUD apps, stay on Workers + D1/DO/R2/KV — cheaper, faster cold start,
simpler mental model. If some fraction of tenant apps need a real backend
language/runtime Workers can't express, Containers is the escape hatch, but
it is a meaningfully different cost and latency profile, not a drop-in
extension of the Workers-for-Platforms story in §1.

---

## 8. Who is actually running this pattern at scale

Public, named, verifiable examples are thinner than the marketing volume
around Workers for Platforms would suggest. What is confirmed:

- **Shopify** uses Workers for Platforms to power **Oxygen**, the hosting
  layer for Hydrogen storefronts — i.e., each merchant's storefront runs as
  an isolated Worker under W4P. This is a genuine, named, large-scale
  production user (Shopify's merchant count is in the millions, though the
  fraction on Hydrogen/Oxygen specifically was not independently confirmed
  here).
- **Grafbase** uses W4P to deploy per-customer serverless GraphQL backends at
  the edge.
- **Lamatic.ai** is described (per a Cloudflare blog reference surfaced in
  search) as using the per-customer deployment model for customer-specific
  serverless functions.
  [Source: How Cloudflare is powering the next generation of platforms with Workers](https://blog.cloudflare.com/powering-platforms-on-workers/)

- Cloudflare is explicitly marketing W4P plus Dynamic Workers plus Durable
  Object Facets as the substrate for **"AI vibe coding platforms"** — i.e.,
  products where an LLM generates a small app per end-user and each one needs
  its own isolated compute and database. This is Cloudflare's own framing of
  almost exactly the operator's scenario (many small, likely LLM-authored,
  per-user apps), published as a one-click deployable reference architecture.
  [Source: Deploy your own AI vibe coding platform](https://blog.cloudflare.com/deploy-your-own-ai-vibe-coding-platform/)

**What was NOT found**: independent (non-Cloudflare-published) numbers on
requests/second, tenant counts, or cost-at-scale from any of these named
users. Everything above is Cloudflare's own case-study framing rather than a
third party publishing "here's what we actually pay at N tenants." Treat the
economics in §1 and §4 as the vendor's stated contract, verified against
primary pricing/limits docs, but **not** independently confirmed by a
third-party's real production bill at, say, 100,000 tenants.

Also worth noting: some earlier features referenced in the "powering
platforms" post (Outbound Workers, Custom Limits, Tail Workers) were
described there as **enterprise-only, open beta**, with pay-as-you-go
availability "later in the year" — the pricing page fetched for §1 (dated
2026-04-21) shows these as part of the standard $25/mo W4P plan now, so they
appear to have since graduated to general availability. The blog post's own
date was not confirmed, so treat the "enterprise-only" framing as
**historical**, superseded by the pricing page.

---

## Table: hard limits at a glance

| Category | Free | Paid (Workers Paid, prerequisite for W4P) |
|---|---|---|
| CPU time/request | 10ms | 30s default, up to 5 min configurable |
| Memory/isolate | 128 MB | 128 MB |
| Script size (gzip) | 3 MB | 10 MB |
| Subrequests/invocation | 50 | 1,000 default, up to 10M |
| Request duration | unbounded while connected | unbounded while connected |
| Simultaneous connections | 6 | 6 |
| Workers/account (standard) | 100 | 500 |
| Workers/W4P dispatch namespace | n/a (W4P needs Paid) | unlimited |
| Static asset files/version | 20,000 | 100,000 |

## Table: storage primitives, per-tenant provisioning viability

| Primitive | API-provisionable per tenant | Practical per-account cap | Best fit |
|---|---|---|---|
| D1 | Yes (`POST .../d1/database`) | 50,000 databases, 10GB each, 1TB total | Per-tenant relational data, small-to-medium |
| Durable Objects (+ SQLite/Facets) | Yes (Dynamic Workers + Facets, 2026) | No stated object-count cap; 500 classes | The actual "one DB per tenant app" primitive at real scale |
| KV | Yes, but don't do one-namespace-per-tenant | **1,000 namespaces/account** | Shared namespace, tenant-prefixed keys; not per-tenant isolation |
| R2 | Yes | 1,000,000 buckets/account | Per-tenant file/blob storage, or one shared bucket with tenant-prefixed keys |
| Hyperdrive | N/A (fronts external DB) | n/a | Only relevant if tenants share one external Postgres |
| Queues | Yes | 10,000 queues/account | Shared queue with tenant tag, not per-tenant queue, if tenant count exceeds ~10K |

---

## What this means for a platform hosting users' apps

**The operator's instinct is directionally right but the "unbounded/free"
framing needs two corrections.**

1. **It is not free past 1,000 tenants, but it is cheap and linear.** $25/mo
   covers the first 1,000 dormant tenant Workers; each additional idle
   tenant is about $0.02/month. Active usage (requests, CPU-ms) is metered
   on top and is genuinely tiny for I/O-bound CRUD apps because CPU-time
   billing means waiting on a database is free. A platform with 10,000 mostly
   idle small apps is plausibly in the tens of dollars a month for compute
   at-rest, before storage and before any given app gets real traffic. This
   is the number worth re-deriving with real per-request CPU-ms measurements
   once an app exists, not trusted as a promise.

2. **"Unbounded" is true for compute (Workers-in-a-namespace, Durable
   Objects) and mostly true for storage (R2 buckets, D1 databases up to
   50K), but false for the naive per-tenant-KV-namespace design (1,000 cap)
   and false in an absolute sense for D1 database count (50,000, raisable on
   request but not infinite).** The fix in both cases is "shared primitive,
   tenant-prefixed" rather than "one instance per tenant," except for Durable
   Objects, which are specifically designed to be cheap to instantiate one
   per tenant.

**The shape of app that fits well:** a small, mostly-CRUD, I/O-bound app —
static frontend + a JSON API + a modest SQL or key-value store, no heavy
in-process computation, no native dependencies, traffic that's bursty/idle
rather than sustained-high-CPU. This is close to the profile of "user
describes an app, an LLM generates a small CRUD tool," which is exactly the
use case Cloudflare is now building reference architecture for (§8) — Durable
Object Facets giving each generated app its own SQLite database, provisioned
in-band by a supervisor Worker, is a closer fit for "one Worker + one DB per
user app, created on demand by the platform" than D1's account-level database
API is, because it doesn't touch the 50,000-database ceiling and appears to
have no analogous cap.

**The shape of app that does not fit:** anything needing sustained CPU
beyond the 30s (or 5-min configured) ceiling in a single request, native
binaries or C-extension-heavy Python, arbitrary long-running background
processes that aren't expressible as Cron Triggers/Queue consumers/Durable
Object alarms, in-memory datasets or media processing beyond 128MB, or
anything where the team wants to avoid re-architecting the multi-tenancy
layer if they ever need to leave Cloudflare — that layer (dispatch
namespaces, Durable Objects, Facets) is the part with no portable
equivalent elsewhere. For that class of app, Cloudflare's own answer is
Containers (§7), which is real but is a different cost/latency contract, not
a free extension of the Workers-for-Platforms story.

**Bottom line for this repo's context**: if apps installed/generated in this
platform stay in the "small CRUD tool, mostly I/O-bound" shape, Workers for
Platforms plus Durable Object Facets (for per-app state) plus R2 (for any
file storage) is a genuinely cheap, genuinely close-to-unbounded fit, with
the caveats above noted and re-checked once real usage numbers exist. If any
installed app needs a real server process, native deps, or sustained CPU, it
needs Containers or a non-Cloudflare host, and that decision should be made
per-app rather than assumed away.

---

## Sources

- [Workers for Platforms pricing](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/pricing/)
- [How Workers for Platforms works](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/how-workers-for-platforms-works/)
- [Workers for Platforms limits](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/reference/limits/)
- [Workers for Platforms overview](https://developers.cloudflare.com/cloudflare-for-platforms/workers-for-platforms/)
- [Workers platform limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Workers platform pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [D1 limits](https://developers.cloudflare.com/d1/platform/limits)
- [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)
- [D1 database create API](https://developers.cloudflare.com/api/node/resources/d1/subresources/database/methods/create) (and Go/Python equivalents surfaced in search)
- [R2 platform limits](https://developers.cloudflare.com/r2/platform/limits)
- [Workers KV limits](https://developers.cloudflare.com/kv/platform/limits/)
- [Durable Objects platform limits](https://developers.cloudflare.com/durable-objects/platform/limits/)
- [Queues platform limits](https://developers.cloudflare.com/queues/platform/limits/)
- [Durable Objects in Dynamic Workers: give each AI-generated app its own database](https://blog.cloudflare.com/durable-object-facets-dynamic-workers/)
- [How Cloudflare is powering the next generation of platforms with Workers](https://blog.cloudflare.com/powering-platforms-on-workers/)
- [Deploy your own AI vibe coding platform](https://blog.cloudflare.com/deploy-your-own-ai-vibe-coding-platform/)
- [Eliminating cold starts with Cloudflare Workers](https://blog.cloudflare.com/eliminating-cold-starts-with-cloudflare-workers/)
- [Unpacking Cloudflare Workers CPU performance benchmarks](https://blog.cloudflare.com/unpacking-cloudflare-workers-cpu-performance-benchmarks/)
- [Containers and Sandboxes are now GA (2026-04-13)](https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga/)
- [Containers public beta announcement](https://blog.cloudflare.com/containers-are-available-in-public-beta-for-simple-global-and-programmable/)
- [Cloudflare Website Terms of Use](https://www.cloudflare.com/website-terms/)
- [Cloudflare abuse approach](https://www.cloudflare.com/trust-hub/abuse-approach/)
- [Cloudflare for SaaS](https://developers.cloudflare.com/cloudflare-for-platforms/cloudflare-for-saas/) (custom hostname pricing figures via search summary; recommend a direct confirm)
- [Workers, Now Even More Unbound: 15 Minutes, 100 Scripts, and No Egress Fees](https://blog.cloudflare.com/workers-now-even-more-unbound/) (background on egress-fee removal)

### Secondary/aggregator sources used for cross-checking only (not primary Cloudflare docs, treat with more caution)

- [FlareCalc Workers pricing calculator](https://flarecalc.com/calculators/workers/)
- [BudgetForge Cloudflare Workers pricing 2026](https://www.budgetforge.dev/tools/cloudflare-workers-pricing-2026)
- [FreeTier.co: Cloudflare D1 free tier](https://freetier.co/articles/cloudflare-d1-free-tier-limits-pricing-and-alternatives)
