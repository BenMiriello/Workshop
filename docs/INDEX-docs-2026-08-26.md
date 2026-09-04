# Index of Workshop documentation outside `platform-redesign/`

Built 2026-08-26. Covers the 50 `.md` files directly in `docs/`, the 31 files in
`docs/decisions/`, the 38 files in the other `docs/` subdirectories, and the
operative instruction files at the repo root.

`docs/platform-redesign/` is NOT covered here. It has its own index.

Status values: CURRENT, HISTORICAL, SUPERSEDED BY <path>, UNRATIFIED PROPOSAL,
UNKNOWN. A status read from the doc's own banner is unmarked. A status I judged
is marked `(judged)`.

---

## 1. Read before you act

These govern how work is done. An agent that skips them does the wrong thing.

- [`applying-changes.md`](applying-changes.md) - 2026-08-13 - CURRENT
  How to make a change take effect: `bash scripts/apply.sh`, nothing else.
  CLAUDE.md's claim that this is the ONLY current source is TRUE as a claim of
  authority but FALSE as a claim of uniqueness. Competing instructions still
  exist in seven docs, listed in section 8.
- [`ticket-writing.md`](ticket-writing.md) - 2026-08-25 - CURRENT
  How to write a ticket title and body. A ticket has two readers, the worker
  (which sees only the body) and the operator (deciding on a phone weeks later).
  Enforced by `services/ticket_format.py`, `ticket_readiness.py`,
  `tasks.title_warnings`. No competing doc.
- [`previewing-an-app.md`](previewing-an-app.md) - 2026-08-23 - CURRENT
  `scripts/shoot.sh`: one preview verb for every app, particulars declared by
  the app. Implements section 3 of `platform-redesign/tools-are-declared-2026-08-23.md`.
  No competing doc.
- [`agent-tooling-and-wakeups.md`](agent-tooling-and-wakeups.md) - 2026-08-22 - CURRENT
  What an agent has for waiting, watching and being woken. A turn ends the moment
  you emit text with no tool call. Pick by what you are waiting for. No competing doc.
- [`ui-origins.md`](ui-origins.md) - 2026-08-13 - CURRENT with one stale line
  Three UIs on this box serve different code; the operator uses the BUILT public
  origin. Still names `scripts/build_workshop_public.sh` as the by-hand rebuild.
- [`instances-and-forking.md`](instances-and-forking.md) - date not stated - CURRENT (judged)
  Classify every change as Platform or Local before committing; when unsure, Local.
  Links `simonsays-local-instance.md`, which does not exist in the tree.
- [`deploying.md`](deploying.md) - date not stated - CURRENT for bring-up, STALE for updates
  `scripts/bootstrap.sh` brings up a new machine; explains why systemd not a
  container. Its "Updating an existing install" block contradicts `apply.sh`.

Instruction files, not under `docs/`:

- [`../CLAUDE.md`](../CLAUDE.md) - CURRENT. Repo conventions, the platform design
  pointers, the four prompt layers, the no-app-names rule, the apply.sh rule.
- [`../frontend/CLAUDE.md`](../frontend/CLAUDE.md) - CURRENT. Design System law,
  binding for every change under `frontend/` and `mobile/`.
- [`../packages/ui/CONTRIBUTING.md`](../packages/ui/CONTRIBUTING.md) - CURRENT.
  How to change `@workshop/ui`: global scope, platform development only, a
  version bump is the only signal an out-of-repo consumer gets.
- [`../PROJECT.md`](../PROJECT.md) - HISTORICAL (judged). Scoped to the 2026-06-22
  "Agents & Chat overhaul" and points at a root `TODO.md` and `src/ui` primitives
  that no longer describe the tree.
- `../projects/_templates/*/AGENTS.md` (4 files: `agent-tool`,
  `fastapi-capability-provider`, `integrated-sub-app`, `static-site`) - CURRENT.
  The only tracked `AGENTS.md` files. Per-scope agent rules copied into a new
  project; read by `provider.persona()` and injected as "Project-local agent rules".
  Every other `AGENTS.md` in the tree is under gitignored `projects/`,
  `build-agents/`, `branches/` or `infra/`.

---

## 2. Decision records (`docs/decisions/`, 31 files)

The durable decision layer. 19 accepted, 9 proposed, 1 template, 1 stray.

- [`0000-template.md`](decisions/0000-template.md) - template. Context, Decision,
  Consequences. Use it.
- [`0001-task-store-is-canonical.md`](decisions/0001-task-store-is-canonical.md)
  2026-07-15 - accepted, storage claim SUPERSEDED BY `decisions/0019`.
  The Task store (`services/tasks.py`, per-scope `.tasks.json`) is the one record
  for work items; first line of `text` is the title, no schema change.
- [`0002-notes-substrate-is-knowledge.md`](decisions/0002-notes-substrate-is-knowledge.md)
  2026-07-15 - accepted. `services/knowledge.py` is the canonical Notes
  substrate; `INTERVENTIONS.md` retired as the Notes surface.
- [`0003-github-is-per-project-binding.md`](decisions/0003-github-is-per-project-binding.md)
  2026-07-15 - accepted. Each project binds to its own GitHub repo via `gh`;
  concurrent work in disjoint worktrees, rebase before merge.
- [`0004-agent-is-a-third-identity-kind.md`](decisions/0004-agent-is-a-third-identity-kind.md)
  2026-07-18 - proposed. Add `Identity.kind == "agent"` beside operator and
  guest, holding zero ambient authority.
- [`0005-resource-policy-rules-engine.md`](decisions/0005-resource-policy-rules-engine.md)
  2026-07-18 - proposed. Resource policy is a homegrown JSON plus prose record in
  `backend/resource-policy.json`, not OPA/Rego.
- [`0006-auto-run-learning-mechanism.md`](decisions/0006-auto-run-learning-mechanism.md)
  2026-07-18 - proposed, SUPERSEDED for the chat-policy case by
  `platform-redesign/tickets-in-chat-2026-08-15.md` section 7. Auto-run
  preferences learned into the learnings store; the learning half is unbuilt.
- [`0007-entity-model-as-first-class-backend-concepts.md`](decisions/0007-entity-model-as-first-class-backend-concepts.md)
  2026-07-18 - accepted. `platform-redesign/entity-model-2026-07-18.md` is the
  single authoritative design reference for vocabulary, scope addressing and
  identity kinds.
- [`0008-autonomy-override-semantics.md`](decisions/0008-autonomy-override-semantics.md)
  2026-07-18 - accepted. The global autonomy off-switch is a file-flag overlay at
  `.runtime/autonomy-override.json`, never a per-agent mutation.
- [`0009-single-canonical-status-source.md`](decisions/0009-single-canonical-status-source.md)
  2026-07-18 - accepted. One entity, one status, one source; never two status
  indicators for the same entity in one view.
- [`0010-notes-vault-is-read-only-to-agents.md`](decisions/0010-notes-vault-is-read-only-to-agents.md)
  2026-07-18 - accepted. `$WORKSHOP_NOTES_VAULT` is read-only to any `agent` identity, enforced
  at three independent layers.
- [`0011-context-memory-is-managed-by-the-model.md`](decisions/0011-context-memory-is-managed-by-the-model.md)
  2026-08-04 - accepted. Do not build compaction, summarisation or eviction; the
  CLI does it. We own only what enters the context and what persists outside it.
- [`0012-color-schemes-are-generated-status-is-fixed.md`](decisions/0012-color-schemes-are-generated-status-is-fixed.md)
  2026-08-07 - accepted. Color is generated from `frontend/src/styles/palette.mjs`
  as an OKLCh tonal ramp; status color is fixed across all schemes.
- [`0013-public-autodeploy-is-pre-launch.md`](decisions/0013-public-autodeploy-is-pre-launch.md)
  2026-08-09 - accepted. Committing to `main` publishes; keep it, but record it as
  pre-launch with an explicit expiry, never as precedent for releases.
- [`0014-developer-mode-gates-the-resolved-directory.md`](decisions/0014-developer-mode-gates-the-resolved-directory.md)
  2026-08-10 - accepted. Developer mode is whether `is_self_dir(cwd)` is a
  permitted run target, gating the resolved directory rather than a scope name.
- [`0015-transcript-structure-and-content-provenance.md`](decisions/0015-transcript-structure-and-content-provenance.md)
  2026-08-12 - accepted. Transcript structure is Turn/Group/Step; a tool step
  carries its own outcome and injected content carries an origin.
- [`0016-tickets-is-the-surface-name.md`](decisions/0016-tickets-is-the-surface-name.md)
  2026-08-12 - accepted, its "rename stops at the label" clause SUPERSEDED BY
  `decisions/0028`. The nav item says Tickets.
- [`0017-spoken-text-is-recorded-at-capture.md`](decisions/0017-spoken-text-is-recorded-at-capture.md)
  2026-08-12 - accepted. A spoken utterance is written to the thread when it is
  captured, before generation starts, by the same store call typing uses.
- [`0018-transcript-order-is-recorded-not-reconstructed.md`](decisions/0018-transcript-order-is-recorded-not-reconstructed.md)
  2026-08-12 - accepted. The server records order as it happens (`cut_after`);
  the client splits on the recorded position and does no arithmetic.
- [`0019-work-data-lives-in-postgres.md`](decisions/0019-work-data-lives-in-postgres.md)
  2026-08-13 - proposed. Work data moves to PostgreSQL with `org_id` on every row
  and Row-Level Security; supersedes 0001's storage claim only.
- [`0020-a-transcript-is-a-tree.md`](decisions/0020-a-transcript-is-a-tree.md)
  2026-08-13 - accepted. A transcript is a tree in PostgreSQL, four tables,
  `parent_id` IS the render order, branches are git-style named pointers.
- [`0021-a-thread-admits-one-operation-at-a-time.md`](decisions/0021-a-thread-admits-one-operation-at-a-time.md)
  2026-08-14 - accepted. `services/thread_ops` admits one operation per thread;
  a refusal reports `busy`, distinct from `deferred`.
- [`0022-memory-is-three-tiers-and-reconciles-on-write.md`](decisions/0022-memory-is-three-tiers-and-reconciles-on-write.md)
  2026-08-16 - accepted. Memory is Episode, Retrieval and Belief, all already
  present; no fourth substrate and no knowledge graph.
- [`0023-the-http-contract.md`](decisions/0023-the-http-contract.md)
  2026-08-17 - proposed. One HTTP contract on every route, enforced by
  `scripts/check_http_contract.py`; existing surface is a backlog that may only shrink.
- [`0024-a-domain-is-a-package.md`](decisions/0024-a-domain-is-a-package.md)
  2026-08-17 - proposed. Every domain is a Python package whose `__init__.py` is
  its public API; four backend layers; the import graph must be a DAG.
- [`0025-one-owner-per-server-entity.md`](decisions/0025-one-owner-per-server-entity.md)
  2026-08-17 - proposed. Every server entity has exactly one client owner, a
  cache entry; a write invalidates the key rather than reloading by hand.
- [`0026-one-identity-root-and-tenancy-is-a-column.md`](decisions/0026-one-identity-root-and-tenancy-is-a-column.md)
  2026-08-17 - proposed. One identity root (Postgres `user_account`, UUIDv7),
  `.runtime/auth.db` retired, tenancy is a column.
- [`0027-agent-triggered-input-in-chat.md`](decisions/0027-agent-triggered-input-in-chat.md)
  2026-08-18 - accepted. Agent-triggered input is a `ui-card` of kind `prompt`,
  not a blocking `AskUserQuestion` tool, which fails silently under `claude -p`.
- [`0028-tickets-is-the-name-everywhere-the-operator-can-see.md`](decisions/0028-tickets-is-the-name-everywhere-the-operator-can-see.md)
  2026-08-20 - accepted. The route is `/tickets`, `/work` redirects preserving the
  query string; code identifiers are not covered.
- [`0029-app-used-styling-consolidates-into-the-kit.md`](decisions/0029-app-used-styling-consolidates-into-the-kit.md)
  2026-08-25 - accepted. Generic app-used classes move into
  `packages/ui/src/styles/components.css` under `.ws-`; app-specific styling stays
  with the app; the kit stays publishable standalone.
- [`loop-demo-2026-08-21.md`](decisions/loop-demo-2026-08-21.md) - not an ADR.
  One line, "loop verified through the chat". Test artifact.

---

## 3. Operating the system

- [`applying-changes.md`](applying-changes.md), [`previewing-an-app.md`](previewing-an-app.md),
  [`agent-tooling-and-wakeups.md`](agent-tooling-and-wakeups.md),
  [`ticket-writing.md`](ticket-writing.md), [`ui-origins.md`](ui-origins.md),
  [`deploying.md`](deploying.md), [`instances-and-forking.md`](instances-and-forking.md)
  are in section 1.
- [`applying-changes-plan-2026-08-13.md`](applying-changes-plan-2026-08-13.md)
  2026-08-13 - HISTORICAL. The plan that produced `apply.sh`; the doc says
  explicitly it is the record of what was wrong, not instructions.
- [`app-platform-runner.md`](app-platform-runner.md) - 2026-06-15 - CURRENT design,
  stale ops line. Port registry, multi-service runner, autonomous monitor;
  `services/port_registry.py` is the single allocation authority.
- [`apps-are-pluggable.md`](apps-are-pluggable.md) - date not stated - CURRENT.
  Adding or removing an app must not edit any platform-core file. The rule behind
  CLAUDE.md's "no platform source file may name a specific app".
- [`handoff-pluggable-apps-migration.md`](handoff-pluggable-apps-migration.md)
  2026-07-12 - HISTORICAL, marked DONE. Records the incident where seven apps and
  the operator's real data were compiled into the platform.
- [`agency-color-system-2026-08-11.md`](agency-color-system-2026-08-11.md)
  2026-08-11 - CURRENT. The you/system/autonomous agency tiers; agency is FILLED,
  status is OUTLINE. Binding rules live in `frontend/CLAUDE.md`.
- [`system-honesty-plan-2026-08-10.md`](system-honesty-plan-2026-08-10.md)
  2026-08-10 - HISTORICAL, 26 of 28 items closed per `chat-plan-review-2026-08-10.md`.
  Every control the operator touches must be traced to its actual effect.
- [`session-followups-2026-07-12.md`](session-followups-2026-07-12.md)
  2026-07-12 - HISTORICAL. A dated to-do list. Its deploy-discipline header is wrong now.
- [`app-manager-ui.md`](app-manager-ui.md) - 2026-06-13 - UNRATIFIED PROPOSAL.
  A manifest-driven per-app editor at two zoom levels. Nothing built.
- [`business-plan.md`](business-plan.md) - 2026-06-14 - HISTORICAL strategy draft.
  Workshop as a personal software operating system; market sizing from a June 2026 pass.
- [`archive/root-todo-legacy-2026-07-15.md`](archive/root-todo-legacy-2026-07-15.md)
  2026-07-15 - HISTORICAL. The prose root `TODO.md` preserved at the cutover to
  `.tasks.json`. History, not a queue.
- [`proposals/`](proposals/) - 3 files, all UNRATIFIED PROPOSAL, 2026-06-17 and
  2026-07-12: `app-versioning-rollback.md` (browse and roll back app versions from
  the builder), `in-app-updates-and-migrations.md` (how a downstream instance pulls
  the platform forward), `workshop-self-hosting-and-panel.md` (Workshop versus the
  `control-panel` systemd supervisor). None implemented.
- [`deployments/proposal-2026-07-18.md`](deployments/proposal-2026-07-18.md)
  2026-07-18 - UNRATIFIED PROPOSAL. A Deployment as a hosted target above
  `app_runner`; sits above the entity model. Note that
  `platform-redesign/app-is-the-entity-2026-08-20.md` later redefined Deployment as
  a running instance of an app, so read that first.

---

## 4. Security

Five audits plus one living posture doc plus two runbooks. Open items in section 9.

> **Everything under `instance-audits/` is UNTRACKED** (`.gitignore`: `docs/instance-audits/`). Those
> files audit one operator's own box -- real account names, home directory modes,
> ACLs, known-fixed weaknesses -- so they are kept on that box and are not shipped
> as platform documentation. The links below resolve on the instance that wrote
> them and are dead in a fresh clone, deliberately. Moved there 2026-08-27.

- [`security-posture.md`](security-posture.md) - last reviewed 2026-07-18 -
  CURRENT, living reference. The trust model and the invariants: identity, network
  minimization, blast-radius containment, and no multi-tenant guardrails.
- [`instance-audits/security-audit-2026-07-18.md`](instance-audits/security-audit-2026-07-18.md) - 2026-07-18 -
  HISTORICAL point-in-time, findings partly open. Host, network, web, edge, mobile.
  Internet surface good; the problems are on the LAN/tailnet plane and in host config.
  Supersedes `instance-audits/security-audit-2026-06-02.md`.
- [`instance-audits/security-audit-2026-07-18-agents.md`](instance-audits/security-audit-2026-07-18-agents.md)
  2026-07-18 - HISTORICAL point-in-time, A1 still open. What an executing agent
  turn can reach: same `claude -p`, no sandbox, `docker` group, full tree.
- [`instance-audits/security-audit-2026-07-18-connectors.md`](instance-audits/security-audit-2026-07-18-connectors.md)
  2026-07-18 - HISTORICAL, no clear issues. Project-to-GitHub separation is clean;
  a project can never commit into the Workshop repo.
- [`instance-audits/security-audit-2026-08-11-scope-separation.md`](instance-audits/security-audit-2026-08-11-scope-separation.md)
  2026-08-11/12 - CURRENT for its open list, section 6 explicitly HISTORICAL.
  Application-layer separation between chat scopes and between users. Section 8 is
  what shipped; sections "Still open" are the live backlog.
- [`security-remediation-runbook.md`](security-remediation-runbook.md) - 2026-07-18 -
  CURRENT as a backlog. The items from the 07-18 audit needing root, another stack
  owner, or an architecture decision. Exact commands.
- [`instance-audits/security-fixes-for-sister-instances.md`](instance-audits/security-fixes-for-sister-instances.md)
  2026-07-18 - CURRENT if a sister instance exists. What travels by git and what
  each instance must apply by hand.
- [`instance-audits/security-audit-2026-06-02.md`](instance-audits/security-audit-2026-06-02.md) -
  SUPERSEDED BY `instance-audits/security-audit-2026-07-18.md`. The <service-user> financial/MCP pass.
- [`instance-audits/security-invariants.md`](instance-audits/security-invariants.md) - CURRENT for
  <bridge-agent> only. The rules the nightly `<bridge-checkout>/security/audit.sh` enforces.
- [`instance-audits/bringup-playbook.md`](instance-audits/bringup-playbook.md) - 2026-05-17 -
  HISTORICAL (judged). Per-tenant clawdbot bring-up, drafted for promotion elsewhere.
- [`instance-audits/security-audit-install-cron.txt`](instance-audits/security-audit-install-cron.txt) -
  the cron install snippet for that audit.

---

## 5. Chat, voice and memory

Chat has the densest doc set and most of it is closed. The two to read are
`chat-state-ownership` and `chat-durability-handoff`.

- [`chat-state-ownership-2026-08-12.md`](chat-state-ownership-2026-08-12.md)
  2026-08-12 - CURRENT, binding. The server owns the conversation, the browser
  renders it; a table of every piece of chat state and its owner.
- [`chat-durability-handoff-2026-08-12.md`](chat-durability-handoff-2026-08-12.md)
  2026-08-12 - CURRENT handoff. The incident where two long operator messages
  ceased to exist, every defect found and fixed, and what is still open.
- [`chat-baseline-2026-08-09.md`](chat-baseline-2026-08-09.md) - 2026-08-09 -
  HISTORICAL, all items closed. External research plus audit: twelve load-bearing
  decisions every solid chat system converges on.
- [`chat-rebuild-spec-2026-08-09.md`](chat-rebuild-spec-2026-08-09.md) - 2026-08-09 -
  HISTORICAL, all three passes closed. A thread is an append-only sequence-numbered
  event log; everything else is a view.
- [`chat-remaining-plan-2026-08-10.md`](chat-remaining-plan-2026-08-10.md)
  2026-08-10 - HISTORICAL, fully closed. The message-above-work ordering bug and
  the rest of the wave.
- [`chat-plan-review-2026-08-10.md`](chat-plan-review-2026-08-10.md) - 2026-08-10 -
  CURRENT as a disposition record. Which chat plans are closed, verified against
  code rather than commit messages. Read this before reopening any chat plan.
- [`chat-effort-and-mode-2026-08-09.md`](chat-effort-and-mode-2026-08-09.md)
  2026-08-09 - HISTORICAL, built. Per-chat effort ladder and mode selector,
  vendor-declared rather than hardcoded.
- [`chat-command-surface-proposal-2026-08-10.md`](chat-command-surface-proposal-2026-08-10.md)
  2026-08-10 - UNRATIFIED PROPOSAL. Most passthrough slash commands were measured
  dead under `claude -p`; what should replace them.
- [`chat-system-overhaul-handoff-2026-07-25.md`](chat-system-overhaul-handoff-2026-07-25.md)
  2026-07-25 - HISTORICAL, DONE. Section 8 is the as-built record and overrides
  sections 1 to 7. Contains stale restart commands.
- [`chat/chat-header-restructure-2026-07-30.md`](chat/chat-header-restructure-2026-07-30.md)
  2026-07-30 - sections 1 to 3 HISTORICAL built, section 4 UNRATIFIED PROPOSAL.
  Why there were three header selectors and which one earned its place.
- [`chat/chat-state-audit-2026-07-31.md`](chat/chat-state-audit-2026-07-31.md)
  2026-07-31 - HISTORICAL. The `/compact` reporting itself as "Filed as a ticket
  in general" incident, four defects meeting in one place.
- [`handoff-2026-08-14-autonomy-and-chat-storage.md`](handoff-2026-08-14-autonomy-and-chat-storage.md)
  2026-08-14 - CURRENT. The autonomous loop had never been able to write a file;
  chat storage stage 2 was reverted. States plainly that older dated handoffs must
  not be used for restart or build instructions.
- [`tickets-and-autonomy-handoff-2026-08-12.md`](tickets-and-autonomy-handoff-2026-08-12.md)
  2026-08-12 - CURRENT as a reading order. The tickets surface is largely done, the
  automation loop behind it is not. Its section 0 is the shortest path into
  `platform-redesign/`.
- [`memory-stores-audit-2026-08-12.md`](memory-stores-audit-2026-08-12.md)
  2026-08-12 - HISTORICAL, superseded in effect by `decisions/0022` and
  `platform-redesign/memory-consolidation-2026-08-16.md`. Nine durable-text stores;
  the axis that matters is PUSH versus PULL.
- [`memory-and-transcript-simplification-2026-08-12.md`](memory-and-transcript-simplification-2026-08-12.md)
  2026-08-12 - UNRATIFIED PROPOSAL, overtaken by `decisions/0022`. Delete both the
  transcript truncation path and the memory push path, add one search tool.
- [`voice-mode/architecture-2026-08-12.md`](voice-mode/architecture-2026-08-12.md)
  2026-08-12 - CURRENT. The voice system designed from the ground up; section 14 is
  what actually shipped. Supersedes `voice-mode/design.md`.
- [`voice-mode-handoff-2026-08-12.md`](voice-mode-handoff-2026-08-12.md) and
  [`voice-mode/handoff-2026-08-12-evening.md`](voice-mode/handoff-2026-08-12-evening.md)
  2026-08-12 - HISTORICAL handoffs. Working-tree state and the live dictation-loss
  bug that `decisions/0017` then settled. Both carry stale build commands.
- [`voice-mode/`](voice-mode/) remainder, 4 files, all HISTORICAL:
  `00-overview.md` (2026-07-10, the turn machine and heard-vs-generated model,
  still conceptually valid), `next-gen-brief.md` (2026-07-11 starter brief),
  `design.md` (2026-07-11, SUPERSEDED BY `voice-mode/architecture-2026-08-12.md`),
  `redesign-2026-07-13.md` (built, SUPERSEDED BY `redesign-2026-07-25.md`),
  `redesign-2026-07-25.md` (the visible conversation loop; last of the pre-rebuild line).

---

## 6. The apps

Installed on this instance per `.runtime/installed-apps.json`: finance, budget,
birds, location, commute, health, wearables, notes-app. gengui is a project used as
the multi-service runner test case, not an installed app.

Birds (live):
- [`birdnet-accuracy.md`](birdnet-accuracy.md) - CURRENT. Four filters over raw
  BirdNET so it stops reporting birds it never heard; the range filter is the big win.
- [`birds-atlas.md`](birds-atlas.md) - CURRENT. Call-type clustering from BirdNET
  embeddings: clip to embedding to cluster to one hand label to auto-tag.
- [`birds-recordings-plan.md`](birds-recordings-plan.md) - 2026-06-16 - UNRATIFIED
  PROPOSAL (judged). Keep the audio clip per detection under per-bird rules and a
  hard storage budget.
- [`birds-presence-vs-count.md`](birds-presence-vs-count.md) - 2026-06-09 - WIP,
  not scheduled, says so in its banner. Reframe detections as vocalization events
  with intensity, separating presence from talkativeness.
- [`birds-redesign/`](birds-redesign/) - 5 files, 2026-06-18 - HISTORICAL, COMPLETE.
  All 8 phases shipped on the `birds-redesign` branch. `MASTER_SPEC.md` and
  `CHARTS_SPEC.md` are still the reference for birds chart encoding (presence
  first); `PLAN.md` carries the completion banner; the two `.html` files are the
  design-team visual references.

Commute and location (live):
- [`commute-v2-plan.md`](commute-v2-plan.md) - 2026-06-13 - approved to build,
  status of completion UNKNOWN from the doc. Prime directive: zero hardcoding of the
  operator's commute; everything learned from observed location plus transit feeds.
- [`owntracks-ios.md`](owntracks-ios.md) - CURRENT setup doc. Owntracks streams
  background location to a tailnet-only endpoint under Basic auth. The web Location
  tab is gone because iOS suspends backgrounded browser geolocation.

Health and wearables (live):
- [`health-auto-export-ios.md`](health-auto-export-ios.md) - CURRENT setup doc.
  Health Auto Export (paid iOS app) posts Apple Health to a tailnet endpoint.
  States that Open Wearables was poorly built and out of date and was replaced.

Notes (live):
- [`notes-app/apple-notes-import.md`](notes-app/apple-notes-import.md) - 2026-07-14 -
  CURRENT. Two committed stdlib-only idempotent scripts import Apple Notes into the
  Workshop Notes vault as Markdown, re-runnable without duplicating.

Finance and budget (live): no dedicated doc under `docs/` outside
`platform-redesign/`. Their security-relevant behaviour is in `security-posture.md`
and the Plaid items in section 9.

Platform-level app docs: [`app-platform-plan.md`](app-platform-plan.md) - 2026-06-06 -
HISTORICAL proposal, largely realised by `apps-are-pluggable.md` and the pluggable
migration. Per-app isolation, directional opt-in sharing, standalone versus
integrated, Protomaps PMTiles.

---

## 7. Mobile

- [`mobile-build-runbook.md`](mobile-build-runbook.md) - 2026-07-11 - CURRENT.
  `scripts/mobile-deploy.sh` on the Mac does pull, deps, Release build, install,
  launch, and the weekly 7-day re-sign. Expo SDK 51 / RN 0.74.5.
- [`mobile-app-proposal.md`](mobile-app-proposal.md) - 2026-06-17 - HISTORICAL.
  Why an Expo plus WebView shell rather than native rewrites; the cost reality.
- [`mobile-app-implementation-plan.md`](mobile-app-implementation-plan.md) -
  HISTORICAL blueprint. The iOS background-execution model is the load-bearing fact.
- [`mobile-handoff-2026-06-25.md`](mobile-handoff-2026-06-25.md) - HISTORICAL, auth
  section SUPERSEDED BY `mobile-handoff-2026-07-11.md`. Says to delete itself once
  the Mac bring-up is done; it was not deleted.
- [`mobile-handoff-2026-07-11.md`](mobile-handoff-2026-07-11.md) - HISTORICAL.
  Resume doc at the Xcode build step; superseded in practice by the runbook.

---

## 8. Docs that contradict CLAUDE.md or the current design

CLAUDE.md: restart and rebuild instructions in dated handoff docs are historical,
`applying-changes.md` is the only current source, and `npx tsc` is a no-op gate.
These docs still tell an agent otherwise.

Wrong apply instructions (`restart_backend.sh` / `build_workshop_public.sh` given
as the thing to run):

- [`deploying.md`](deploying.md) line 109 - the "Updating an existing install" block.
  The one that is not a dated handoff, so the CLAUDE.md carve-out does not cover it.
- [`session-followups-2026-07-12.md`](session-followups-2026-07-12.md) lines 7 to 9 -
  a "Deploy discipline (this repo)" header, phrased as standing policy.
- [`app-platform-runner.md`](app-platform-runner.md) line 142 - "backend changes need
  `bash scripts/restart_backend.sh`".
- [`voice-mode-handoff-2026-08-12.md`](voice-mode-handoff-2026-08-12.md) line 259,
  [`voice-mode/handoff-2026-08-12-evening.md`](voice-mode/handoff-2026-08-12-evening.md)
  line 222, [`voice-mode/architecture-2026-08-12.md`](voice-mode/architecture-2026-08-12.md)
  line 731 - all three give `build_workshop_public.sh` plus `ui_sync.sh` as the
  finish step.
- [`chat-durability-handoff-2026-08-12.md`](chat-durability-handoff-2026-08-12.md)
  line 410, [`chat-system-overhaul-handoff-2026-07-25.md`](chat-system-overhaul-handoff-2026-07-25.md)
  lines 99 and 102, [`mobile-handoff-2026-06-25.md`](mobile-handoff-2026-06-25.md)
  line 90 - same, inside dated handoffs.
- [`ui-origins.md`](ui-origins.md) - names `build_workshop_public.sh` as the by-hand
  rebuild. Otherwise correct and still worth reading.

Wrong typecheck gate (`npx tsc --noEmit`, which exits 0 having checked nothing):

- [`session-followups-2026-07-12.md`](session-followups-2026-07-12.md) line 9,
  [`chat-system-overhaul-handoff-2026-07-25.md`](chat-system-overhaul-handoff-2026-07-25.md)
  lines 125 and 269, [`chat/chat-header-restructure-2026-07-30.md`](chat/chat-header-restructure-2026-07-30.md)
  line 213, [`agent-chat-redesign/phase-1-spec.md`](agent-chat-redesign/phase-1-spec.md)
  lines 33 and 276, [`workshop-v1/04-implementation-plan.md`](workshop-v1/04-implementation-plan.md),
  [`birds-redesign/PLAN.md`](birds-redesign/PLAN.md). Use `npm run typecheck` from the root.

Contradicts the current design model:

- [`workshop-v1/01-agents-and-chat.md`](workshop-v1/01-agents-and-chat.md) and the
  rest of `workshop-v1/` - type agents on the assistant/builder axis. That axis is
  gone; agents are keyed by `role`. The set carries a supersession banner.
- [`agent-chat-redesign/plan.md`](agent-chat-redesign/plan.md) - same axis, same banner.
- [`deployments/proposal-2026-07-18.md`](deployments/proposal-2026-07-18.md) and
  [`app-platform-plan.md`](app-platform-plan.md) - treat Project and App as separate
  entities. Superseded by `platform-redesign/app-is-the-entity-2026-08-20.md`.
- [`PROJECT.md`](../PROJECT.md) - points at a root `TODO.md` work queue retired at the
  2026-07-15 `.tasks.json` cutover, and at `src/ui` primitives now at `packages/ui`.
- [`instances-and-forking.md`](instances-and-forking.md),
  [`proposals/in-app-updates-and-migrations.md`](proposals/in-app-updates-and-migrations.md),
  [`proposals/workshop-self-hosting-and-panel.md`](proposals/workshop-self-hosting-and-panel.md) -
  all link `docs/simonsays-local-instance.md`, which is not in the tree. Dead link.

---

## 9. Open security remediation items

Extracted from the audits. Nothing here is marked done in its source doc.

From `instance-audits/security-audit-2026-07-18.md` (prioritized action list) and
`security-remediation-runbook.md`:

1. C3 - inbound IPv6 firewall policy unverified. Decides the blast radius of
   everything bound on `[::]`. Root.
2. C2 - `lovelace-redis` unauthenticated on `0.0.0.0` plus IPv6, known RCE path.
   Needs `requirepass` and a loopback bind. Stack owner plus root.
3. C1 / A1 - the service user is in the `docker` group, which is root-equivalent,
   and the app runner depends on it. Runbook Part 1 proposes rootless Docker plus
   dropping the group. Architecture decision plus root.
4. H2 - backend binds `0.0.0.0:8000` with RFC1918 trust, so LAN-reachable host code
   execution. Bind to `127.0.0.1`.
5. H4 / H7 - ComfyUI `:8188` and Ollama `:11434` / `:11435` unauthenticated on
   `0.0.0.0`. Loopback-bind. Stack owners plus root.
6. H5 - unauthenticated control-panel service manager on `:9000` exposed to the
   tailnet. Auth wall or drop the `8449` Serve mapping.
7. H3 - `NOPASSWD` sudo to `git` as `clawd-house` is code execution as that user.
   Drop or wrap. Root.
8. M3 - sshd password authentication effectively enabled on `0.0.0.0:22`. Key-only.
9. M2 - open-wearables dev frontend on `0.0.0.0:3100`; its backend env holds many
   third-party secrets in a committed file.
10. L2 - the Vite dev server `:5173` is served to the tailnet via Serve `8443`.
11. L5 - Samba anonymous share enumeration.

Closed in that pass and listed only so they are not re-reported: H1, H6, M4, L1,
L3, L4.

From `instance-audits/security-audit-2026-07-18-agents.md`:

12. A2 - `touches`, `latitude` and `grants` are advisory and the agent can rewrite them.
13. A3 - exfiltration channels remain open despite the egress guard.
14. A4 - recursive `claude` spawn with no recursion or token budget.
15. A5 - the personal-data partition is application-layer only.
16. A6 - app containers run unconfined.

From `instance-audits/security-audit-2026-08-11-scope-separation.md`, "Still open" plus "Still open
after phase two":

17. `shared/knowledge/` is gated, not partitioned. A second user gets none of the
    workshop knowledge layer rather than their own. Blocked on `projects/`, `apps/`
    and `build-agents/` having no `owner_id`.
18. `WORKSHOP_CHAT_CWD` is set on this deployment and points into the workshop tree.
    Operator config, deliberately not changed in that pass.
19. A non-operator project or build turn still runs the full built-in toolset under
    `bypassPermissions` at its cwd. The fix underneath everything else is a dedicated
    lower-privilege OS execution identity for chat-driven turns. Same item as C1/A1.
20. `POST /chat/threads` still stores an arbitrary `context_key` verbatim.
21. Background sweeps have no owner axis. `dispatch_triggers.sh`, `project_ralph.sh`,
    `liveness_watchdog.sh` and `memory_keeper_run.sh` iterate every project as one OS
    user with the operator's `gh` login and OAuth token. Named as the next thing to build.
22. `stores.py`'s `per_owner` flag is unimplemented; `_operator_runtime_dir()` resolves
    once at registration. The Store-registry door is not per-owner even though the REST
    path is.
23. The Plaid MCP (`mcp/plaid/server.js`) reads one global `plaid-tokens.json` with no
    owner parameter. Currently closed only by denying non-operators MCP wholesale.
24. Single-`domain_owner` ingest paths (location, health, commute) each need a per-user
    credential before they can be per-user.
25. `apps/<key>/` is tracked source shared by everyone, which is wrong for
    `apps/birds/birdnet.db`, one database for all users.

Cross-reference: `decisions/0014` cites `[SEC-A1]` (execution identity) and `[SEC-A2]`
(stop representing scope as a security boundary). Those are items 3 and 19 above.

---

## 10. Systems described here that no longer exist

- **Letta** - removed 2026-08-23. Still described as live in:
  [`agent-chat-redesign/phase-2-spec.md`](agent-chat-redesign/phase-2-spec.md) (an
  entire phase spec built on per-scope Letta archives, probed against Letta v0.16.8),
  [`workshop-v1/00-overview.md`](workshop-v1/00-overview.md) and
  [`workshop-v1/01-agents-and-chat.md`](workshop-v1/01-agents-and-chat.md),
  [`auth-redesign/00-overview.md`](auth-redesign/00-overview.md),
  [`auth-redesign/01-auth-and-ownership.md`](auth-redesign/01-auth-and-ownership.md),
  [`auth-redesign/03-execution-plan.md`](auth-redesign/03-execution-plan.md),
  [`auth-redesign/04-critique-and-refinements.md`](auth-redesign/04-critique-and-refinements.md),
  [`proposals/in-app-updates-and-migrations.md`](proposals/in-app-updates-and-migrations.md),
  [`archive/root-todo-legacy-2026-07-15.md`](archive/root-todo-legacy-2026-07-15.md).
  Security finding M1 in [`instance-audits/security-audit-2026-07-18.md`](instance-audits/security-audit-2026-07-18.md)
  and its runbook entry are about a Letta server that is gone, as is the
  `infra/letta/.env` chmod in L1. `agent-chat-redesign/plan.md` already notes the
  2026-07-15 Stage 9 teardown.
- **Open Wearables** - replaced. [`health-auto-export-ios.md`](health-auto-export-ios.md)
  says so directly; `instance-audits/security-audit-2026-07-18.md` finding M2 is still written against
  a running open-wearables dev server.
- **The web Location tab** - removed. [`owntracks-ios.md`](owntracks-ios.md) records why.
- **The root `TODO.md` prose work queue** - retired 2026-07-15 into `.tasks.json`.
  [`PROJECT.md`](../PROJECT.md) still directs work through it.
- **`INTERVENTIONS.md` as the Notes surface** - retired by `decisions/0002`.
- **The assistant/builder runtime axis** - gone, verified 2026-08-20. See section 8.
- **The `build` branch in `frontend/src/lib/scope.ts` and the `/agents/build/{slug}`
  routes** - named dead by CLAUDE.md, retire per
  `platform-redesign/app-is-the-entity-2026-08-20.md` section 5.

---

## 11. Historical design generations

Whole directories, all superseded. Do not update them and do not build from them.

- [`workshop-v1/`](workshop-v1/) - 7 files, 2026-06-14. The first full platform
  design: agents and chat, workbench and launcher, UI kit and marketplace, staged
  implementation, deployment and isolation, a review pass. SUPERSEDED BY
  `platform-redesign/entity-model-2026-07-18.md` and its phase docs, per the banner
  in `00-overview.md`. Historical because it types agents on the assistant/builder
  axis that no longer exists. `05-deployment-isolation-safety.md` is the one still
  cited forward, by `app-platform-runner.md`, for the `LocalRunner` it generalizes.
- [`agent-chat-redesign/`](agent-chat-redesign/) - 4 files, 2026-07-11. The second
  generation: `plan.md` (SUPERSEDED 2026-07-18, banner), `phase-1-spec.md` (built and
  verified, commit `37f1e39`), `phase-2-spec.md` (never built, and its substrate Letta
  is gone), `knowledge-interface.md` (the contract a notes store plugs into; the
  scope model in it survives in `services/knowledge.py` and `decisions/0002`).
  Historical for the same axis reason plus the Letta teardown.
- [`auth-redesign/`](auth-redesign/) - 6 files, 2026-06-25. Cookie plus device-token
  auth, per-user data ownership, and the Cloudflare public surface. Phases 0 to 4
  IMPLEMENTED and live-verified per `05-implementation-status.md`; phase 5 deployed
  bar one DNS command. `04-critique-and-refinements.md` wins over the earlier four
  where they disagree. Historical as a plan, but this is the design the shipped auth
  model came from, so read it if you touch auth.
- [`birds-redesign/`](birds-redesign/) - see section 6. Complete, but the two SPEC
  files remain the reference for birds charts.
- [`chat/`](chat/) - 2 files, July 2026. Pre-rebuild chat audits, both overtaken by
  the August rebuild set in section 5.
- [`archive/`](archive/) - 1 file. History by definition.
