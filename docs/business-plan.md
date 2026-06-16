# Workshop as a Product & Business

Status: strategy draft, 2026-06-14. Author: clawd-a. For operator review.

This builds on the v1 design docs (`docs/workshop-v1/`) and `docs/app-platform-plan.md`. Those describe *what Workshop is*. This describes *what Workshop could be as a company*, who it serves, how it wins, what we keep vs what customers keep, and how it makes money regardless of whether any single customer succeeds.

Market facts below are sourced from a June 2026 research pass (see end of doc for confidence tiering). Numbers are real where cited.

---

## 1. The one-sentence thesis

**Workshop is a personal software operating system: the place where bespoke software gets built, wired to your real data and the real world, composed together through a permission graph, optionally shared, and kept alive with a real software lifecycle underneath.** It is not another "prompt to a throwaway web app" generator. The generator is the easy, commoditized part. The hard, defensible parts are exactly what Workshop already has: capability-based data interop, real connectors to real life, isolation that makes agent builds recoverable, and a lifecycle (projects, branches, deployments) that lets software be maintained instead of regenerated.

The bet: the durable use of AI software-building is not "everyone becomes a SaaS founder." It is **the long tail of bespoke software that no SaaS will ever build because the per-need market is too small**, now buildable in an afternoon. Every app already in Workshop proves the thesis: a commute optimizer, a backyard bird station, a household budget waterfall, a location-history map. None of those is a product anyone would fund. All of them are software worth having, for one person or one household or one small operator. That category was previously uneconomic. Agents collapse the build cost to near-zero, and the category becomes real.

---

## 2. Who it serves

Three concentric rings, sequenced.

**Ring 1 (wedge, now): the technical-adjacent power user / "household CTO" / solo operator.** People who can describe software precisely, want it wired to their actual data (bank, location, calendar, sensors, transit, health), and are underserved by toy generators that make a pretty frontend and stop. This is the operator's own profile, which is the credibility: Workshop is already this person's life-OS. The indie-hacker, the quantified-self person, the small-business owner running their shop on spreadsheets and Zapier duct tape, the engineer who wants their life automated but won't hand-build CRUD.

**Ring 2 (expansion): the small business / team.** Retool's 2026 survey: 35% of teams have already replaced at least one SaaS tool with a custom build, 78% expect to build more internal tools in 2026, 60% built software outside IT oversight. This is the proven, retention-linked demand. Internal tools, ops dashboards, customer-facing micro-apps wired to the business's own data.

**Ring 3 (platform): everyone, via the marketplace.** The "fork someone's app and make it yours" model. Most people never build from scratch; they install or fork a working app and grant it their data. This is the Roblox / app-store ring, and it is where network effects and broad revenue live.

Critically, the research shows the *aspiration* to "build a business and sell it" (Lovable: 80% non-technical, 8 in 10 intend to monetize) is mostly unrealized intent, while the *durable, retention-linked* usage is bespoke and internal. So we serve the builder who wants software for their own use first, and treat "and you can sell it too" as upside, not the core promise. That is the opposite of how Lovable/Base44 market themselves, and it is more honest about where the money and retention actually are.

---

## 3. The market and who we compete with

The category is huge, frothy, and crowded. As of mid-2026:

- **Lovable**: ~$500M ARR (self-reported), $6.6B valuation. Prompt-to-app, 80% non-technical.
- **Replit**: ~$9B valuation, targeting $1B ARR, agent that builds and deploys full-stack. 50M+ users.
- **Cursor (Anysphere)**: $29.3B, $2B+ ARR. Pro developers / enterprise.
- **Devin (Cognition, owns Windsurf)**: ~$26B, autonomous SWE, enterprise.
- **v0 (Vercel)**, **Bolt.new**, **Base44 (acquired by Wix ~$80M)**, **Create.xyz**, **Databutton**, **Tempo**: the prosumer/SMB generator tier.
- **n8n**: $5.2B, source-available agent orchestration, the open-core playbook executed perfectly.
- **Incumbents bolting on AI**: Retool (AppGen + Agents, ~$3.2B), Zapier (Agents + MCP, ~$420M ARR), Bubble, Glide, Softr, Airtable (repriced from $11.7B to ~$4B as it scrambles to go AI-native).

We do not win by being a better generator. That fight is over-capitalized and converging on parity. We win on the axes the whole field is weak at. From the research, the field's consistent failure modes are:

1. **Prototype, not production.** Generates the happy path, breaks on edge cases. Rebuild-to-production reportedly $20K to $100K.
2. **Security disasters.** Replit's agent deleted a production database during a code freeze (July 2025, ~1,200 records). Lovable shipped apps without row-level security: a scan of 1,645 showcase apps found 10.3% with critical flaws leaking PII, Stripe IDs, credentials (CVE-2025-48757). Research broadly: ~45% of AI-generated code has security weaknesses.
3. **Can't compose apps. Data silos.** Each generated app is an island. Interop is named as a major unsolved problem.
4. **Apps don't connect to real data.** Backend/auth/data-source wiring is the dominant failure mode.
5. **Lock-in and throwaway code.** Many tools export only the frontend, not the backend/DB. No real version control or maintainability. The "spaghetti point" around month 3 where velocity collapses.

Workshop is, almost by accident, architecturally strong at every one of these. That is the opening.

---

## 4. Differentiator, hook, and why it is hard to copy

Four pillars, each mapped to a competitor weakness and to something already in the codebase.

**Pillar 1: the capability graph (anti-silo data interop).** Workshop's typed-capability model (`apps.json` `provides`/`needs`, `grants.json` default-deny directional grants) is the thing the entire field is missing. Apps compose: Commute reads Location because you granted it, not because it scraped an API. This is the realized version of what Solid (Berners-Lee) and Ink & Switch's "malleable software" essay describe as vision but nobody has shipped at consumer scale. It is also the security answer: default-deny between apps means one app cannot reach another's data or nuke your store. The more apps and connectors exist, the more valuable composition becomes. This is genuine infrastructure with network effects, not a feature.

**Pillar 2: the connector / capability library (real data, real world).** The reason Workshop's apps are useful and not toys is the connectors: Plaid (finance), OwnTracks (location), 511/GTFS (transit), BirdNET (ML/acoustics), wearables. Generated UIs are commoditized. *A rich, maintained library of real-world data and action connectors is not.* Whoever owns the best connector ecosystem makes the best apps, because the connectors are what turn "a webpage" into "software that does something in my life." This is hard to copy because it is grinding, long-tail integration work plus the capability-grant layer that makes it safe to expose.

**Pillar 3: recoverable, real lifecycle (trust as a feature).** Worktree isolation per build, auto-checkpoints, snapshot-before-risky, no force-push to protected refs, builders never hold remote credentials, egress default-deny with request-and-approve. After Replit deleted a production DB, "the agent literally cannot nuke your data, every build is recoverable, you own the code, it runs local-first" is a real, marketable differentiator, not compliance theater. Plus actual projects/branches/deployments so software is maintained, not regenerated into spaghetti.

**Pillar 4: local-first + hosted hybrid (privacy as a wedge).** Workshop already runs a local Ollama tier ladder alongside cloud Claude, and apps mount integrated *or* standalone/public from one codebase. As the AI-data-hoarding backlash grows, "your personal software and personal data live on your box by default, the cloud is opt-in per capability" is a premium position no cloud-only competitor can credibly take. This is the Solid/local-first thesis with an actual product around it.

**The hook (how you say it in one breath):** "Build software for your actual life and business, wired to your real data, that composes with your other apps, that can't break or leak, that you own and can share. Not a prototype. Software."

---

## 5. The moat

Layered, and each layer compounds with usage:

1. **The capability/interop substrate.** Becomes more valuable per app added. Switching cost rises as your apps come to depend on grants and shared capabilities. This is the structural moat.
2. **The connector ecosystem.** Grinding integration work plus a safety layer. A latecomer has to rebuild both. Open-sourcing the substrate (see GTM) lets the community extend connectors, which deepens the moat rather than leaking it.
3. **Build-trajectory learning (the flywheel, section 6).** The harness gets measurably better at building *this class of software* the more anyone builds. Vertical integration on the model layer, the same move that justifies Cursor's Composer and Cognition owning its stack. A thin-wrapper competitor cannot match it.
4. **Marketplace network effects.** Forkable apps, kits, connectors, templates. Two-sided: more publishers, more installers.
5. **Trust/safety reputation.** In a field defined by public security blowups, being the one that does not leak or destroy data is a brand moat that is expensive to earn and easy to lose, which protects whoever earns it first.

No single layer is unbeatable. Together, and given a head start on the substrate that already exists in code, they are defensible.

---

## 6. What is ours vs what is the customer's (the data and learning flywheel)

This is the operator's sharpest question and the most novel part of the strategy. The principle:

**The customer owns their product entirely. We own the substrate and the meta-knowledge of how software gets built. We learn from *how you build*, never from *what you build with*.**

**The customer keeps, unconditionally:** their app, their source code, their application data, their users, their revenue. No claim on any of it. This is non-negotiable and is the trust foundation. (It is also the lesson from every lock-in complaint in the research: tools that hold the code/backend hostage get resented and eventually fled.)

**We keep and own:**
- The substrate: the OS, `@workshop/ui` kit, the runtime/runners, the agent harness, the capability and grant model.
- The connector library and the marketplace rails.
- **Build-process telemetry: anonymized build trajectories.** This is the asset. Not the customer's app data and not their source. The *shape of building*: which prompts produced working apps vs failing ones, which scaffolds got forked, which capability grants are commonly requested together, which build steps fail and how the agent recovered, which UI patterns pass design checks, where humans had to intervene. This is reinforcement signal harvested from the act of building itself. It makes the harness better at building the next app, for everyone, without ever reading a customer's private data.

**Why "we learn from how you build, not what you build with" is the tenable agreement.** It draws the line precisely where customers care. People are fine with "you learn that prompts of shape X tend to fail and you fixed your scaffold." People are not fine with "you trained on my customers' records." The agreement is honest because the valuable training signal genuinely lives in the trajectory, not the payload. We can contractually and technically commit to never training on application data or source, and still get the flywheel, because the flywheel runs on build trajectories.

**This becomes the freemium engine, tiered:**
- **Free / local-first tier:** you pay with build-signal, not money. Anonymized trajectory learning is on. This is generous on purpose; it is how we acquire users and feed the flywheel. (n8n's source-available open core took it to $5.2B; generosity at the base is the growth move.)
- **Pro / paid tier:** privacy. Trajectory learning is opt-out, or aggregate-only. You pay money instead of signal.
- **Team / enterprise tier:** full isolation, private registry, no learning, contractual guarantees.

So the operator's exact instinct holds and is made ethical and durable: *even if a customer never pays us a dollar, we still get better from their building* (free tier, anonymized trajectories), and the moment they want privacy they pay, and either way we win. We grow regardless of whether their app succeeds, because we learned from the building, not the outcome.

---

## 7. The business model (the broker that takes a bit from the act of building)

The operator's framing is exactly right and the research confirms it is the proven model: **usage-based, taking a cut at each stage of the road, growing regardless of any single app's success or failure.** Three tollbooths on one road:

**Tollbooth 1: build-time (metered agent compute).** You make money when people build, like AWS makes money whether the startup wins. This is the primary engine. The whole field has moved here: Replit checkpoints, Lovable credits, Devin ACUs, Cursor usage.

But there is a critical fairness fix the research demands. Replit and Cursor both had public revolts in 2025: surprise $1,000+ bills, and the "iteration tax" of being charged for failed and looping builds the same as successful ones. **Workshop's billing differentiator: bill for value delivered, not motion.** Transparent hard spend caps by default, no charge (or steeply reduced charge) for builds the reviewer gate marks as failed, visible cost-per-action before you run it. In a category where billing resentment is the loudest complaint, fair metering is itself a wedge. The reviewer/completion gate Workshop already has is the mechanism: it can distinguish a build that delivered from one that thrashed.

**Tollbooth 2: run-time (hosting / deployment).** When you run or share an app, especially a public deployment, you pay to host it. The standalone/public mount (birds.benmiriello.com pattern) is the product surface. Predictable, recurring, scales with usage of the apps people actually keep.

**Tollbooth 3: distribution-time (marketplace cut).** When an app, kit, connector, or template is installed, forked, or monetized through the marketplace, we take a percentage. App-store economics. This is the long-term high-margin layer and the one with the strongest network effects.

Supporting revenue:
- **Connector marketplace / premium data feeds.** Some connectors are premium (maintained, SLA, paid upstream data). Cut on those.
- **Pro/Team subscription floor.** Privacy (no trajectory learning), more compute headroom, collaboration, private registry. A flat floor under the usage metering, which the research shows is now the dominant hybrid model (flat floor + usage rose to 41% of AI vendors).
- **Later: services-as-software (Sequoia's "services is the new software" thesis).** For Ring 3 users who will never build, offer done-for-you builds or app-as-a-managed-service. Higher touch, higher margin, larger TAM ("businesses spend ~$6 on services per $1 on software").

Margin note: the field's margins are thin (~50-60%, some wrappers ~25%) because of LLM passthrough, and vertical integration is the cited fix. Workshop's existing Ollama tier ladder (route cheap/boilerplate work to local models, expensive reasoning to cloud) is exactly the right cost lever and should be leaned into hard. Routing more of the build trajectory to local/cheap models as the trajectory-learning flywheel teaches us which steps are routine is a compounding margin win.

---

## 8. Near-future bets (bold, but grounded in what is shipping)

Ordered by how far out they are. These are bets, sized deliberately above "conservative."

1. **Apps become agents.** Workshop already has heartbeats and continuous-background agents. The bet: the unit of personal software shifts from "an app you open" to "an agent that runs on your behalf and acts." Anthropic reports 30+ hour autonomous coding runs; Gartner projects 40% of enterprise apps embed agents by end of 2026. Workshop's apps-with-live-assistants-and-heartbeats is ahead of the framing. Make "agentic by default" a first-class app type.

2. **The capability graph becomes a portable personal-data interop standard.** The realized Solid / malleable-software vision. If Workshop's grant model becomes the way personal apps interoperate (your data, your grants, apps compose, nothing siloed), the substrate is no longer a product feature, it is infrastructure others build on. MCP became the de-facto tool standard within a year; the personal-data-permission layer is the open slot. Adopt and extend MCP as the connector/tool transport (the field has standardized on it), and layer the capability-grant model on top as the thing MCP lacks: user-owned, default-deny, composable permissions.

3. **Local-first / privacy as the premium category, not a niche.** As AI data-hoarding backlash grows and regulation (GDPR, EU Data Act portability) bites, "your software and data live on your box, cloud is opt-in per capability" moves from idealist to mainstream-premium. Workshop's local-first hybrid is the product nobody cloud-native can copy without rebuilding.

4. **Build-trajectory learning makes Workshop the best in the world at one specific thing:** building personal/composed/data-wired software. Not general coding (Cursor/Devin own that). The vertical. A model fine-tuned on Workshop's own build trajectories for *this class of app* beats a general model the way Cursor's Composer beats a raw API call. This is the long-term technical moat and the reason to start collecting trajectories from day one.

5. **The personal-software marketplace as the next app store.** a16z's "disposable software" and "software's YouTube moment": software no longer gated by ROI, just by imagination. The marketplace where you fork someone's life-app and make it yours, granting it your data, is the consumer endgame. Most people never write a prompt; they install and grant.

---

## 9. Honest risks and the one hard bet

**The single biggest cost and risk: Workshop is single-operator today, by explicit design.** The codebase correctly has *no* multi-tenancy ("no defensive cruft for single-user setups" is a stated, repeatedly-enforced rule, and it is the right rule for a personal box). Turning this into a business means building real multi-tenancy: tenant isolation, per-tenant auth and billing, abuse handling, the works. That is a large architectural lift and it runs against the grain of the current design. **This is the one unavoidable, expensive bet, and it should be named clearly and sequenced deliberately, not stumbled into.** The capability-grant and origin-isolation work already done for *shared apps* (Birds for friends) is the seed, but tenant-level isolation is a bigger thing than app-level isolation.

Other risks:
- **Crowded, over-capitalized field.** Mitigation: do not fight on generation. Win on interop + connectors + trust + local-first, where the field is weak and capital has not concentrated.
- **LLM compute margins.** Mitigation: the Ollama tier ladder, and trajectory-learning-driven routing of routine steps to cheap models.
- **Security liability when others' apps touch real money and real data.** This is also the differentiator, but it cuts both ways: a Workshop-hosted app leaking data is reputational poison. The isolation model must be genuinely sound before Ring 2/3, not theater. Budget real security investment here; it is the brand.
- **Connector maintenance burden.** Integrations rot. Mitigation: open-source the connector framework so the community shares the load, and gate premium/SLA connectors as paid.
- **Trajectory-learning trust.** If users believe we are training on their data despite the promise, the flywheel and the brand both die. Mitigation: make the boundary technical and auditable, not just contractual. Be able to show exactly what a trajectory contains and prove it excludes payload.

---

## 10. Go-to-market and sequencing

The principle: stay personal-first and dogfood relentlessly, because the authenticity is the credibility. Workshop being the operator's actual life-OS, with real apps wired to real bank/location/transit/sensor data, is a story no competitor can fake. Lead with it.

**Phase A (now to near-term): sharpen the wedge, keep dogfooding.** Finish the v1 platform phases (the design docs already sequence this well). Harden the capability/isolation model to genuinely-sound, since it is the differentiator. Build the trajectory-logging substrate from the start, even while single-operator, so the flywheel has data the moment there are users. Keep shipping real apps that prove "software for one wired to real life."

**Phase B (open-core seeding):** open-source / source-available the substrate (kit, capability model, connector framework, local runner). This is the n8n playbook that reached $5.2B: generosity at the base seeds adoption and a connector ecosystem, while hosting + marketplace + cloud build are the monetized layers. Open-sourcing the substrate also makes the local-first/privacy story credible (auditability) and turns connector maintenance into community work.

**Phase C (multi-tenant cloud):** the hard bet. Build real tenancy and the three tollbooths (metered build, hosting, marketplace cut) with the fair-billing differentiator. Target Ring 1 (power users / household CTOs / indie hackers) first, since they tolerate rough edges and produce the best trajectories.

**Phase D (marketplace + expansion to Ring 2/3):** open the marketplace, lean into fork-and-grant, court small businesses with the internal-tools story (the proven, retention-linked demand from the Retool data), and add services-as-software for non-builders.

**Wedge messaging, in order of who to convince:** start with the people who already feel the pain of toy generators (technical-adjacent prosumers), win them on "real data, composes, can't break, you own it, runs local," then expand on word-of-mouth and marketplace forks. Do not open with "build a startup"; open with "build the software your life and work actually need, that nobody will ever sell you."

---

## 11. Confidence and sourcing

Market numbers above are from a June 2026 research pass. Primary-confirmed: Cursor $29.3B (CNBC/WSJ/deal counsel), Lovable $500M ARR (TechCrunch, self-reported/unaudited), Base44/Wix acquisition (TechCrunch/Wix), n8n $5.2B (SAP investment), the Replit DB-deletion and Lovable RLS-leak incidents (Tom's Hardware/Fortune/CVE/AI Incident DB), MCP cross-vendor adoption. Estimate-tier (Sacra/Dealroom/aggregators): most ARR and valuation figures for v0, Bolt, Bubble, Retool, Make. Contested and flagged: "zero-margin Cursor" and "$5k compute per user" claims (retail API price is not COGS). TAM figures vary 5-10x by definition and are directional only.

The strategy does not depend on any single contested number. It depends on three robust signals: the field is large and real, its durable use is bespoke/internal/personal software, and its consistent failures (security, silos, throwaway code, no real data) are precisely Workshop's architectural strengths.
