# Index of the platform-redesign corpus

Generated 2026-08-26. Covers all 134 `.md` files directly under
`docs/platform-redesign/` plus `navigation-shell-source/`.

Companion index, different axis: [`INDEX-open-work-2026-08-26.md`](INDEX-open-work-2026-08-26.md)
inventories the open TICKETS. This file inventories the DOCUMENTS.

Status vocabulary used below:

- **MODEL OF RECORD** current authority on its subject
- **CURRENT** accurate, not authoritative
- **SUPERSEDED BY x** replaced, kept for the record
- **HISTORICAL** kept for the record, not a design
- **UNRATIFIED** proposal the operator has not accepted
- `(declared)` the doc's own banner says it. `(judged)` inferred here.

BUILT is judged against the tree at 2026-08-26, never against the doc's own
claim. Several banners in this corpus assert the opposite of the code in both
directions.

---

## Reading order for someone new

Twelve documents carry the current design. Everything else is reference.

1. [`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md)
2. [`entity-model-2026-07-18.md`](entity-model-2026-07-18.md) (skip §2 and §9.4, superseded by 1)
3. [`the-plan-2026-08-23.md`](the-plan-2026-08-23.md)
4. [`work-system-coverage-2026-08-25.md`](work-system-coverage-2026-08-25.md)
5. [`work-status-contract-2026-08-24.md`](work-status-contract-2026-08-24.md)
6. [`changes-and-review-2026-08-22.md`](changes-and-review-2026-08-22.md)
7. [`agent-context-and-toolkit-2026-08-20.md`](agent-context-and-toolkit-2026-08-20.md)
8. [`context-assembly-budget-2026-08-25.md`](context-assembly-budget-2026-08-25.md)
9. [`counting-tokens-2026-08-25.md`](counting-tokens-2026-08-25.md)
10. [`backlog-reconciliation-2026-08-24.md`](backlog-reconciliation-2026-08-24.md)
11. [`app-separation-why-it-recurs-2026-08-24.md`](app-separation-why-it-recurs-2026-08-24.md)
12. [`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md) (§7.1, §8.1, §8.6 are stale, see below)

---

## Contradictions between current documents

Both sides of each pair are non-superseded and both read as authoritative.
Ranked by how much work they can misdirect.

### 1. A learning is a note `kind`, or it is a `reach`. An open ticket builds the losing side.

[`backlog-reconciliation-2026-08-24.md`](backlog-reconciliation-2026-08-24.md), whose banner reads
"Where this disagrees with an earlier doc, this wins":

> **`reach` wins.** ... Correct reassessment section 6.2 in place.

[`agents-and-memory-reassessment-2026-08-23.md`](agents-and-memory-reassessment-2026-08-23.md) §6.2, uncorrected:

> **Decision: fold `learnings` into the knowledge store as a note kind, and delete the separate store**

Open ticket `81198145` is titled after the second. Neither `reach` nor the fold
exists in code.

### 2. The unit of a limit: tokens or money. Two same-day docs, both claiming authority.

[`backlog-reconciliation-2026-08-24.md`](backlog-reconciliation-2026-08-24.md):

> **Money wins for LIMITS. Tokens stay for ACCOUNTING.**

[`limits-plan-audit-2026-08-24.md`](limits-plan-audit-2026-08-24.md) §4:

> **Which unit for the monthly limit on day one: tokens.**

Partly resolved after the fact and not recorded in either: the operator ruling
appended to [`limiting-usage-2026-08-24.md`](limiting-usage-2026-08-24.md)
("The unit is dollars at published API prices") and
[`counting-tokens-2026-08-25.md`](counting-tokens-2026-08-25.md) §5 both take
money. Ticket `a731ec9b` still follows tokens.

### 3. "Nothing queues" versus pause-and-resume.

[`providers-credentials-and-limits-2026-08-23.md`](providers-credentials-and-limits-2026-08-23.md) §7.3:

> **Nothing queues.** A queued turn is a refusal that lies about when it will run.

[`backlog-reconciliation-2026-08-24.md`](backlog-reconciliation-2026-08-24.md) §1.2 ruled
"a pause is resumable work and is not a queue entry" and ordered the correction
into providers §7.3. It was never added. Urgent ticket `70860d58` builds
pause-and-resume against a doc that still forbids it.

### 4. `micro_usd` is 0 on a subscription row, or that zeroes every limit.

[`providers-credentials-and-limits-2026-08-23.md`](providers-credentials-and-limits-2026-08-23.md) §7.2:

> `micro_usd` ... is **0, for local and for subscription**

[`limits-plan-audit-2026-08-24.md`](limits-plan-audit-2026-08-24.md) §4:

> It would zero every row in this installation ... and make a dollar limit non-binding.

Correction ordered by backlog-reconciliation §1.3, never applied.

### 5. The execution boundary. The core work-loop doc is false on it.

[`work-loop-core-design-2026-08-21.md`](work-loop-core-design-2026-08-21.md) §5:

> `ralph.sh` invokes `claude -p` with **no `--permission-mode`**, and the loop has ... **never successfully written a file unattended**

Verified false: `scripts/ralph.sh:538` sets
`PERM_FLAGS=(--permission-mode bypassPermissions)`. Its §3 claim that "no runner
is resident" is also false; `queue_runner.sh` has been resident since 2026-08-23.
No banner. This is the corpus's own named core loop doc.

### 6. App under project, or project under app.

[`memory-and-documentation-model-2026-08-14.md`](memory-and-documentation-model-2026-08-14.md) §4.1 draws
`app:<name>` nested under `project:<slug>`. `backend/services/scope_tree.py:22-27`
says the opposite and calls the doc's nesting "the retired model", per
[`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md).

### 7. Apps and Projects are distinct, or Project is retired.

[`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md) §7.1:

> That contradicts the ratified entity model, which this repo's `CLAUDE.md` states as law: "Apps and Projects are distinct linkable concepts."

[`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md), same date, decided the
opposite and `CLAUDE.md` now states the opposite. §7.2 and §8.2 of the shell doc
argue from the retired premise. The unratified
[`storage-and-structure-model-2026-08-19.md`](storage-and-structure-model-2026-08-19.md) §6 is more
current here than the model-of-record shell doc.

### 8. `.tasks.json` is retired, or it is the live store for every non-relational scope.

[`tickets-one-store-2026-08-14.md`](tickets-one-store-2026-08-14.md):

> Postgres is the ticket store. `.tasks.json` is retired.

`backend/services/tasks.py:1436`: "every other root writes the file exactly as
before". The retirement is two scopes deep (`RELATIONAL_SCOPES =
("development", "workshop")`). No doc says so.

### 9. Two incompatible GitHub designs, neither aware of the other.

[`app-separation-and-github-2026-08-20.md`](app-separation-and-github-2026-08-20.md) §3 decides a
GitHub App plus device flow plus `githubkit` plus Fernet credentials. What
exists is `backend/services/github.py`, a `gh`-CLI wrapper labelled Phase G1 of
[`github-worktree-orchestration-plan-2026-07-15.md`](github-worktree-orchestration-plan-2026-07-15.md).
`githubkit` is not installed and no github router is mounted.

### 10. The app-name gate fails on clean HEAD, or it passes.

[`stage-5-app-loader-plan-2026-08-24.md`](stage-5-app-loader-plan-2026-08-24.md) makes a fixture
rename its commit-0 prerequisite because "the gate FAILS on clean HEAD".
[`stage-7-extraction-plan-2026-08-24.md`](stage-7-extraction-plan-2026-08-24.md), same day, says it
already landed. Measured 2026-08-26: `check_no_app_names.py` returns content
102, imports 20, literals 302, exit 0. Stage 7 is right. Stage 5, stage 4b and
app-separation-why-it-recurs all carry the stale `literals 305`.

### 11. A ticket may not read `done` while unmerged, and 95 open tickets cannot express it.

[`work-status-contract-2026-08-24.md`](work-status-contract-2026-08-24.md) §7.1 makes `acceptance`
the configurable done-condition. [`work-system-coverage-2026-08-25.md`](work-system-coverage-2026-08-25.md)
§0.2 measures `acceptance set: 0, touches set: 0, latitude: {None: 95}`, and no
document assigns a producer to any of the three.

### 12. `work_review.py`'s dead blocker is quoted forward into four documents.

Its docstring names "the lease refactor (B3)" as the reason a write is left
alone. B3 landed at `d4c82846`.
[`lease-and-status-authority-2026-08-24.md`](lease-and-status-authority-2026-08-24.md) and
[`plan-set-review-2026-08-24.md`](plan-set-review-2026-08-24.md) both prove it dead, yet
[`agent-context-plan-2026-08-24.md`](agent-context-plan-2026-08-24.md) still defers D1 on it and
[`merge-policy-implementation-plan-2026-08-24.md`](merge-policy-implementation-plan-2026-08-24.md)
still defers G3 on it. Tickets `791f334e` and `e48268d0` give a worker opposite
instructions.

### 13. Hosting: three same-day docs that reverse direction and leave the product question open.

[`hosting-apps-design-2026-08-24.md`](hosting-apps-design-2026-08-24.md) says nothing starts before
step 1. [`hosting-design-review-2026-08-24.md`](hosting-design-review-2026-08-24.md) says step 1 is
the one thing unspecified and the deletion should not wait.
[`app-artifact-spec-2026-08-24.md`](app-artifact-spec-2026-08-24.md) specifies step 1 and declines
the two questions the review said reorder everything. No ticket filed.

---

## Stale but not marked

A current-looking doc describing retired code. Worse than one marked historical.

- [`work-loop-core-design-2026-08-21.md`](work-loop-core-design-2026-08-21.md): §5 and §3 false on the
  execution boundary and the resident runner. See contradiction 5.
- [`autonomy-execution-boundary-2026-08-14.md`](autonomy-execution-boundary-2026-08-14.md): its
  "What not to do" forbids `bypassPermissions`, which is exactly what shipped on
  2026-08-21. No banner records the reversal.
- [`HANDOFF-app-platform-and-github-2026-08-20.md`](HANDOFF-app-platform-and-github-2026-08-20.md):
  root `CLAUDE.md` sends new agents here. Counts are wrong (314 vs 302), "Stage
  4b is next" is superseded, and its stated blocker is contradicted by
  [`stage-4b-kit-promotions-2026-08-24.md`](stage-4b-kit-promotions-2026-08-24.md).
- [`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md): §8.1 "Do not build a Files
  page" and §13 "Files ... not stubbed anywhere" are both false since
  [`file-viewer-and-editor-2026-08-22.md`](file-viewer-and-editor-2026-08-22.md); §8.6 "No installed
  app has any of it" is false since seven per-app routes landed at `App.tsx:155-161`.
- [`data-model-and-persistence-2026-08-13.md`](data-model-and-persistence-2026-08-13.md) §6:
  "**Nothing reads from Postgres yet.**" Tickets and chat both do.
- [`providers-credentials-and-limits-2026-08-23.md`](providers-credentials-and-limits-2026-08-23.md):
  banner says "nothing built"; stage 1 routes, `model_providers.py`,
  `model_credentials.py` and `secrets_crypto.py` all exist.
- [`tools-are-declared-2026-08-23.md`](tools-are-declared-2026-08-23.md): banner says "not yet
  built"; stages 1 to 4 shipped (`tool_registry.py` 839 lines, `routes/tools.py`,
  `command_tools.py`, MCP dispatch).
- [`agent-interaction-model-2026-07-31.md`](agent-interaction-model-2026-07-31.md): "NOTHING BUILT";
  `services/mentions.py` and `services/inbox.py` implement D1, D2, D6.
- [`agent-definition-and-governance-2026-08-04.md`](agent-definition-and-governance-2026-08-04.md):
  "NOT BUILT" in the banner, "CORRECTED after building P1a" in §1.
- [`chat-work-linkage-2026-07-18.md`](chat-work-linkage-2026-07-18.md): "not built"; card
  supersession, the work panel and the notification drawer all shipped.
- [`agent-roles-research-2026-07-16.md`](agent-roles-research-2026-07-16.md),
  [`scopedness-and-escalation-2026-07-16.md`](scopedness-and-escalation-2026-07-16.md),
  [`synthesis-agent-protocols-2026-07-16.md`](synthesis-agent-protocols-2026-07-16.md): all three
  banners say "NOT BUILT / does not exist" about `triager`, `latitude`, `touches`
  and the dispatcher bug. All four exist. Their 07-18 successors got correction
  banners in the 08-17 and 08-23 passes; these three did not.
- [`unified-agent-model-2026-07-15.md`](unified-agent-model-2026-07-15.md): no banner at all. Its
  agent field list has no `role` and gives autonomy as "manual | continuous",
  which has not been the value set since 07-18.
- [`chat-toolbar-audit-redesign-2026-07-18.md`](chat-toolbar-audit-redesign-2026-07-18.md): every
  line reference points into `Chat.tsx:1102-1211`, where the toolbar no longer is.
- [`coherent-rebuild-2026-07-14.md`](coherent-rebuild-2026-07-14.md): calls itself "the canonical
  plan" and states "Project is the scoping AND quota boundary", retired 08-20.
- [`resource-access-interface-spec-2026-07-18.md`](resource-access-interface-spec-2026-07-18.md):
  bills itself as "the implementable contract" and names `services/resource_policy.py`
  and `resource-policy.json`. Neither exists; they are `services/policy.py` and
  `backend/store-policies.json`. Its §8 broker HTTP API has no route.
- [`audit-what-actually-runs-2026-08-23.md`](audit-what-actually-runs-2026-08-23.md): labelled
  CURRENT, but both headline findings are fixed (the gate is `--since` scoped,
  the cron lines are commented out).
- [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md):
  status line still says "nothing built" after its foundation shipped.
- [`where-apps-run-2026-08-24.md`](where-apps-run-2026-08-24.md): superseded, and only the
  superseding doc says so.
- [`handoff-2026-08-18-execute.md`](handoff-2026-08-18-execute.md): no banner, reads as current
  instructions, measures 90 of 411 routes. It is 341 now.
- [`ticket-lifecycle-and-integration-2026-08-10.md`](ticket-lifecycle-and-integration-2026-08-10.md)
  §7 row 9 records scope review as shipped. Its routes were deleted 2026-08-12.
- [`coherence-overhaul-2026-08-16.md`](coherence-overhaul-2026-08-16.md) T3 says `scope_locked`
  was deleted. The write path was; `db/models.py:1430-1431` still maps the columns.
- [`tickets-in-chat-2026-08-15.md`](tickets-in-chat-2026-08-15.md) package E orders
  `AutonomyPanel.tsx` deleted. It is still rendered at `ProjectDetail.tsx:980`.
- [`concept-schema-2026-07-16.md`](concept-schema-2026-07-16.md): its six-axis table includes
  `depth` and `fanout`, deleted from `agents_registry.py:133` as "a control that lies".
- [`proposal.md`](proposal.md): locks two decisions since reversed, "Naming: Job, not
  Run" and "`runtime` as a field".

---

## Orphans

Nothing in the corpus, and nothing in `CLAUDE.md`, links to these. Two of them
were written yesterday.

- [`context-assembly-budget-2026-08-25.md`](context-assembly-budget-2026-08-25.md)
- [`who-filed-this-and-what-was-decided-2026-08-25.md`](who-filed-this-and-what-was-decided-2026-08-25.md)
- [`ref-preview-thumbnails-2026-08-25.md`](ref-preview-thumbnails-2026-08-25.md)
- [`client-state-management-2026-08-25.md`](client-state-management-2026-08-25.md)
- [`project-context-files-and-flows-2026-08-25.md`](project-context-files-and-flows-2026-08-25.md)
- [`plan-set-review-2026-08-24.md`](plan-set-review-2026-08-24.md)
- [`background-subagents-2026-08-20.md`](background-subagents-2026-08-20.md)
- [`shell-mockup-spec-2026-08-22.md`](shell-mockup-spec-2026-08-22.md)
- [`chat-agent-integration-2026-08-18.md`](chat-agent-integration-2026-08-18.md)
- [`chat-concurrency-model-2026-08-14.md`](chat-concurrency-model-2026-08-14.md)
- [`testing-the-autonomous-loop-2026-08-14.md`](testing-the-autonomous-loop-2026-08-14.md)
- [`turn-recovery-terminal-journal-2026-08-17.md`](turn-recovery-terminal-journal-2026-08-17.md)
- [`handoff-2026-08-17-second-pass.md`](handoff-2026-08-17-second-pass.md)
- [`handoff-2026-08-18-execute.md`](handoff-2026-08-18-execute.md)
- [`queue-dispatch-repair-2026-07-18.md`](queue-dispatch-repair-2026-07-18.md)
- [`work-placement-and-preference-learning-2026-07-18.md`](work-placement-and-preference-learning-2026-07-18.md)
- [`session-handoff-2026-07-14.md`](session-handoff-2026-07-14.md),
  [`session-handoff-2026-07-15.md`](session-handoff-2026-07-15.md),
  [`session-handoff-2026-07-15b.md`](session-handoff-2026-07-15b.md)

`chat-concurrency-model` and `turn-recovery-terminal-journal` are orphaned but
BUILT and load-bearing; the code cites them even though no doc does.

---

## Theme 1: the entity model and what an app is (13)

- [`app-is-the-entity-2026-08-20.md`](app-is-the-entity-2026-08-20.md)
  - MODEL OF RECORD (declared). BUILT: PARTLY.
  - App is the one entity the user names; project is the build view of an app,
    Deployment is a running instance, `scope` is internal addressing. Stage A
    only: `App` table exists, `Project` still exists beside it, registries unlinked.
- [`entity-model-2026-07-18.md`](entity-model-2026-07-18.md)
  - MODEL OF RECORD for vocabulary; §2 and §9.4 SUPERSEDED BY app-is-the-entity. BUILT: PARTLY.
  - Fixes the canonical nouns Chat / Ticket / Run / Agent, their state machines
    and field lists, and "ticket" as the surface word.
- [`target-architecture-2026-08-17.md`](target-architecture-2026-08-17.md)
  - CURRENT (declared accepted, in build). BUILT: PARTLY.
  - Write the four missing contracts (HTTP, package layout, one store per entity,
    generated client) and put a `check_*` ratchet behind each. Eleven ratchets
    exist; `schema.gen.ts` is generated; the per-domain package split is not done.
- [`coherence-overhaul-2026-08-16.md`](coherence-overhaul-2026-08-16.md)
  - CURRENT (declared, all six threads built). BUILT: PARTLY.
  - Collapse duplicate implementations onto one spine, one definition per concept,
    ratchet behind each. `_role_for_runtime` and `RUNTIMES` confirmed gone.
- [`coherence-migration-plan-2026-07-18.md`](coherence-migration-plan-2026-07-18.md)
  - CURRENT (declared accepted). BUILT.
  - The sweep that dragged the frontend off `runtime: builder|assistant` onto
    `role`, and off per-project presets onto the per-agent autonomy overlay.
- [`data-model-and-persistence-2026-08-13.md`](data-model-and-persistence-2026-08-13.md)
  - CURRENT (ADR 0019); §P3 superseded by tickets-one-store. BUILT.
  - Every domain entity leaves flat files for one multi-tenant Postgres schema
    with `org_id` plus RLS. See stale list: its §6 disclaimer is now false.
- [`storage-and-structure-model-2026-08-19.md`](storage-and-structure-model-2026-08-19.md)
  - UNRATIFIED (declared); §5-6 ratified by app-is-the-entity. BUILT: NOT.
  - Blobs plus per-app repos plus records plus a derived node index; the app
    working directory is a materialization. No `Node`, no node index, no blob store.
- [`concept-schema-2026-07-16.md`](concept-schema-2026-07-16.md)
  - SUPERSEDED BY [`entity-model-2026-07-18.md`](entity-model-2026-07-18.md). BUILT: PARTLY.
  - Six orthogonal axes that must not be fused. Five are live; `depth`/`fanout`
    were deleted from the registry as controls that lie.
- [`unified-agent-model-2026-07-15.md`](unified-agent-model-2026-07-15.md)
  - SUPERSEDED BY entity-model §6 and final-plan-agent-generalize (judged, no banner). BUILT: PARTLY.
  - An Agent is slug plus rules plus memory plus triggers plus autonomy plus
    grants; everything else is shared pipeline, so the bridge is an instance.
- [`proposal.md`](proposal.md)
  - SUPERSEDED BY entity-model (judged; banner still says accepted). BUILT.
  - Locks Job/Store/Grant/one-agent-registry. Two of its locks have reversed:
    "Job not Run" and "`runtime` as a field".
- [`implementation-plan.md`](implementation-plan.md)
  - HISTORICAL (declared). BUILT.
  - The five-phase build order executing proposal.md.
- [`review-addendum.md`](review-addendum.md)
  - HISTORICAL (judged). BUILT.
  - Post-phase-5 punch list: mount GrantPicker, build Stores UI, un-orphan Jobs.
- [`coherent-rebuild-2026-07-14.md`](coherent-rebuild-2026-07-14.md)
  - HISTORICAL (judged, no banner). BUILT.
  - Retire the two legacy runners and adopt Project / Agent / Chat / Task plus Run,
    with a Task outliving the Chat that spawned it. Its Project rule is retired.

## Theme 2: app separation, hosting and extraction (16)

Ground truth for this theme is three numbers, not a doc line. Measured
2026-08-26: `python3 scripts/check_no_app_names.py` returns content 102,
imports 20, literals 302, exit 0 at the pinned baseline. `git ls-files apps/`
is 0 against 2.2G on disk.

- [`app-separation-and-github-2026-08-20.md`](app-separation-and-github-2026-08-20.md)
  - CURRENT (declared, stages 0-3 built). BUILT: PARTLY.
  - The stage 0 to 8 list for getting the operator's apps out of the platform.
    Stages 0-4 verifiable; 5, 6 and 7 absent.
- [`app-separation-why-it-recurs-2026-08-24.md`](app-separation-why-it-recurs-2026-08-24.md)
  - CURRENT (declared). BUILT as an instrument.
  - Why separation is repeatedly reported done and is not: the ratchet counts are
    the status of the work, never a doc line.
- [`HANDOFF-app-platform-and-github-2026-08-20.md`](HANDOFF-app-platform-and-github-2026-08-20.md)
  - CURRENT (declared) but stale. BUILT: PARTLY.
  - What is done versus next in the separation program. `CLAUDE.md` points new
    agents here; its counts and its "stage 4b is next" are both out of date.
- [`stage-4b-kit-promotions-2026-08-24.md`](stage-4b-kit-promotions-2026-08-24.md)
  - CURRENT (declared plan). BUILT: PARTLY.
  - The import half already shipped in `754facd9`; the real coupling is ~22 CSS
    class names across ~320 app-page sites, and `.ws-note-*` must leave the kit.
    32 `.ws-note-*` selectors are still in `packages/ui`.
- [`stage-5-app-loader-plan-2026-08-24.md`](stage-5-app-loader-plan-2026-08-24.md)
  - CURRENT (declared plan, partly superseded by app-loader-distribution). BUILT: NOT.
  - Import maps plus dynamic `import()` of a built ESM app bundle. No loader
    exists; `frontend/src/apps/registry.tsx` still names 16 app modules statically.
- [`app-loader-distribution-and-versioning-2026-08-24.md`](app-loader-distribution-and-versioning-2026-08-24.md)
  - CURRENT (declared design; supersedes stage-5 §6, §8.1, §7 rows 5-9). BUILT: NOT.
  - How the kit is versioned and how an app bundle is distributed at build time.
    The `kit` manifest field has writers and zero readers.
- [`stage-7-extraction-plan-2026-08-24.md`](stage-7-extraction-plan-2026-08-24.md)
  - CURRENT (declared plan; "no line in this file may ever read done"). BUILT: NOT.
  - How the operator's 102 app-source files leave the repo via an overlay repo at
    `apps/`. Carries the only current gate numbers in the corpus.
- [`fresh-clone-assertion-design-2026-08-24.md`](fresh-clone-assertion-design-2026-08-24.md)
  - CURRENT (declared design). BUILT: PARTLY, 2026-08-27.
  - The executable definition of "app separation is done" as assertions in
    `scripts/check_fresh_clone.sh`. That script EXISTS now and is wired into
    `reviewer.sh`. Three of five assertions are implemented (no app source, no
    personal data, no hardcoded identity with docs in scope); the two needing a
    build and a Postgres cluster print as NOT IMPLEMENTED on every run and are
    the remaining work. Absolute mode fails today, on 106 app-source files and
    3 identity categories: that is the honest state, not a broken gate.
- [`app-artifact-spec-2026-08-24.md`](app-artifact-spec-2026-08-24.md)
  - CURRENT (declared SPEC). BUILT: NOT.
  - Source tree to Release (immutable content-addressed artifacts) to Deployment
    (a mutable pointer). No `Artifact`, `Release` or `Deployment` class exists.
- [`hosting-design-review-2026-08-24.md`](hosting-design-review-2026-08-24.md)
  - CURRENT (declared REVIEW). BUILT: n/a.
  - The hosting design has exactly one implementable step, and it conflates two
    products: shell-extension apps versus standalone tenant programs.
- [`hosting-apps-design-2026-08-24.md`](hosting-apps-design-2026-08-24.md)
  - UNRATIFIED (declared position paper, "do NOT hand it to a worker"). BUILT: NOT.
  - Delete the never-run deploy subsystem, then rebuild on "an app is an artifact,
    not a directory". Even the deletion has not happened.
- [`deployment-surface-inventory-2026-08-24.md`](deployment-surface-inventory-2026-08-24.md)
  - CURRENT (declared INVENTORY). BUILT: n/a.
  - Measures that the deployment subsystem has never run: 0 of 11,883 jobs are
    `kind=deploy`, with a store-path collision between two rival `Deploy` schemas.
- [`research-cloudflare-hosting-2026-08-24.md`](research-cloudflare-hosting-2026-08-24.md)
  - CURRENT research (declared). BUILT: NOT.
  - Workers for Platforms dispatch namespaces are the mechanism that would make
    per-tenant app hosting near-free.
- [`research-hosting-runtimes-2026-08-24.md`](research-hosting-runtimes-2026-08-24.md)
  - CURRENT research (declared). BUILT: NOT.
  - Idle cost per app per month across Fargate, EC2, App Runner, Lambda, Cloud
    Run, Fly, Railway, Render.
- [`research-peer-platforms-2026-08-24.md`](research-peer-platforms-2026-08-24.md)
  - CURRENT research (declared). BUILT: NOT.
  - What primitives Replit and peers expose, and whether they steer their own
    agents toward those primitives.
- [`where-apps-run-2026-08-24.md`](where-apps-run-2026-08-24.md)
  - SUPERSEDED BY [`hosting-apps-design-2026-08-24.md`](hosting-apps-design-2026-08-24.md). BUILT: NOT.
  - Separates where source lives, where the platform runs, and where a user's
    apps run. Got the tier count wrong.

## Theme 3: branches, worktrees, leases, changes and merge (8)

- [`workspace-ownership-and-leases-2026-08-22.md`](workspace-ownership-and-leases-2026-08-22.md)
  - MODEL OF RECORD (declared design of record, revision 2). BUILT: PARTLY.
  - A branch or worktree is owned by a lease, not by a chat; conflicts resolve by
    warn or takeover with a record. B1's read-write mount ships behind an unset
    env var, so the spawn path is byte-identical to pre-lease.
- [`changes-and-review-2026-08-22.md`](changes-and-review-2026-08-22.md)
  - CURRENT (declared C1-C7 built). BUILT but inert.
  - A ticket says what should be true, a Change says here is the code; the
    reviewable unit is a Change with a diff, not a ticket status flip. The
    `change` table has never held a row.
- [`branches-worktrees-and-environments-2026-08-20.md`](branches-worktrees-and-environments-2026-08-20.md)
  - CURRENT (declared "nothing built", revised 08-22). BUILT: PARTLY.
  - A worktree per writing participant, a pluggable copy-on-write storage driver,
    spin-up on demand. No CoW driver and no Environment model exist.
- [`HANDOFF-branch-isolation-2026-08-22.md`](HANDOFF-branch-isolation-2026-08-22.md)
  - CURRENT (declared build log). BUILT for the items claimed.
  - The build order for branch isolation, plus the corrections the build forced
    (bwrap bind order is load-bearing; a linked worktree's `.git` is a file).
- [`merge-policy-levels-2026-08-24.md`](merge-policy-levels-2026-08-24.md)
  - CURRENT (declared; extends changes-and-review §6). BUILT: PARTLY.
  - Adds the tier above the scope and drops one policy value; classification, not
    author, is what makes auto-merge safe. The added tier and the UI do not exist.
- [`merge-policy-implementation-plan-2026-08-24.md`](merge-policy-implementation-plan-2026-08-24.md)
  - CURRENT (declared, the executable half of the above). BUILT: NOT.
  - The nine-step decision path as it really is, correcting three claims the
    design overstates and naming ten gaps it missed.
- [`lease-and-status-authority-2026-08-24.md`](lease-and-status-authority-2026-08-24.md)
  - CURRENT (declared plan, measured at `10018a9e`). BUILT: PARTLY.
  - B3 is already built, so the remaining work is narrow. Its finding: a stale
    docstring was quoted forward into four docs as a live blocker.
- [`github-worktree-orchestration-plan-2026-07-15.md`](github-worktree-orchestration-plan-2026-07-15.md)
  - HISTORICAL (judged). BUILT.
  - Bind each project to a personal GitHub repo, track `default_branch`,
    push-then-PR, worktree per task. This is what `services/github.py` implements.

## Theme 4: the work loop and autonomy (19)

- [`the-plan-2026-08-23.md`](the-plan-2026-08-23.md)
  - MODEL OF RECORD for sequencing (declared). BUILT: PARTLY.
  - The order of work: the loop defect was whole-tree gates, recall was never
    broken, memory's defect is one store wearing three names, so next is
    connection, deletion and naming, not invention.
- [`work-system-coverage-2026-08-25.md`](work-system-coverage-2026-08-25.md)
  - CURRENT audit (declared). BUILT: n/a.
  - The readiness gate passes 91 of 95 open tickets and disagrees with a human
    read on 7 of 12 sampled; `acceptance`, `touches` and `latitude` are set on
    zero of 95. Its §3(d) is the corpus's own contradiction list.
- [`backlog-reconciliation-2026-08-24.md`](backlog-reconciliation-2026-08-24.md)
  - CURRENT DECISIONS (declared "where this disagrees with an earlier doc, this wins"). BUILT: NOT.
  - Settles eight contradictions between current docs. Four of its ordered
    in-place corrections were never applied. See contradictions 1 to 4.
- [`plan-set-review-2026-08-24.md`](plan-set-review-2026-08-24.md)
  - CURRENT (declared). BUILT: n/a.
  - Retracts two of the reviewer's own claims (median run cost is 107,284, not
    ~800,000; B3 was never a blocker) and names "a docstring is not a test".
- [`audit-what-actually-runs-2026-08-23.md`](audit-what-actually-runs-2026-08-23.md)
  - CURRENT (declared) but a dated snapshot. BUILT: fixes landed.
  - The loop completed nothing for 36 days because whole-tree reviewer ratchets
    rejected inherited debt, and nine approved commits were stranded unmerged.
- [`work-loop-core-design-2026-08-21.md`](work-loop-core-design-2026-08-21.md)
  - UNRATIFIED (declared; §7 lists four decisions still awaiting the operator). BUILT: PARTLY.
  - No swarm: a staged pipeline with one active worker per stage over a durable
    checkpointed ticket store. Two central facts in it are false, see contradiction 5.
- [`autonomy-model-2026-07-18.md`](autonomy-model-2026-07-18.md)
  - CURRENT (declared accepted). BUILT.
  - Autonomy is per-agent (reactive / supervised / autonomous, default reactive)
    and the global off-switch is a non-destructive overlay.
- [`autonomy-turn-on-2026-08-12.md`](autonomy-turn-on-2026-08-12.md)
  - CURRENT (declared PLAN); phase 5 decided elsewhere. BUILT: PARTLY.
  - Keep three real autonomy dials, delete the three controls that do nothing, and
    point the first supervised sweep at `development`, never `workshop`.
- [`autonomy-implementation-2026-08-13.md`](autonomy-implementation-2026-08-13.md)
  - CURRENT (declared SPEC, binding on the code). BUILT phases 0-4.
  - Exactly two new ticket fields: `execution` (code/judgement/external) and
    `triage` (a spec-hash stamp that stops the queue re-judging), plus splitting
    the brake from the runner.
- [`latitude-model-2026-07-18.md`](latitude-model-2026-07-18.md)
  - CURRENT (declared accepted). BUILT.
  - Scopedness is an observation, not a dial; the settable knob is `latitude`
    (strict/standard/open), inherited invocation > task > scope > default.
- [`work-placement-and-preference-learning-2026-07-18.md`](work-placement-and-preference-learning-2026-07-18.md)
  - CURRENT (judged, no banner). BUILT: PARTLY.
  - Chats are not hard-blocked from tool calls; placement is a reasoned principle
    and the operator's wish outranks it. The principle is verbatim in the prompt;
    the preference-learning half is unbuilt.
- [`testing-the-autonomous-loop-2026-08-14.md`](testing-the-autonomous-loop-2026-08-14.md)
  - CURRENT (declared plan, not built). BUILT: PARTLY.
  - The test environment is a disposable `projects/<slug>`; three tiers escalate
    from harness to real repo. Its diagnosis (chats filed 25 tickets in July and
    0 in August) still holds.
- [`autonomy-execution-boundary-2026-08-14.md`](autonomy-execution-boundary-2026-08-14.md)
  - SUPERSEDED BY [`work-loop-core-design-2026-08-21.md`](work-loop-core-design-2026-08-21.md) §S2 (judged). BUILT: NOT as written.
  - Recommends `acceptEdits` plus sandboxed Bash plus a worktree, and forbids
    `bypassPermissions`. The opposite shipped on 2026-08-21.
- [`scopedness-and-escalation-2026-07-16.md`](scopedness-and-escalation-2026-07-16.md)
  - SUPERSEDED BY [`latitude-model-2026-07-18.md`](latitude-model-2026-07-18.md) (judged). BUILT.
  - Threads B and C are one mechanism, so only one new knob is needed: latitude
    is the block rate, and blast radius is already `touches` plus grants.
- [`orchestration-and-autonomy.md`](orchestration-and-autonomy.md)
  - SUPERSEDED BY [`coherent-rebuild-2026-07-14.md`](coherent-rebuild-2026-07-14.md) for sequencing (judged). BUILT.
  - Folds autonomy and task management into agent orchestration over one task
    substrate, with a memory-consolidation organ and one config preset cascade.
- [`autonomy-task-management.md`](autonomy-task-management.md)
  - SUPERSEDED-AND-EXTENDED BY [`orchestration-and-autonomy.md`](orchestration-and-autonomy.md) (declared). BUILT.
  - One task substrate with a single write path: the store is authoritative and
    `TODO.md` is a regenerated export.
- [`task-management-exploration.md`](task-management-exploration.md)
  - SUPERSEDED BY [`autonomy-task-management.md`](autonomy-task-management.md) (declared). BUILT: PARTLY.
  - Durable tasks are per-project, never per-chat; chats hold transient plans
    promoted by an explicit gesture.
- [`system-audit-2026-07-18.md`](system-audit-2026-07-18.md)
  - HISTORICAL (judged, no banner). BUILT: PARTLY.
  - The execution half is real while the orchestration half routes zero agents;
    seven ranked repairs with "populate `depends_on`" first. Still unpopulated.
- [`queue-dispatch-repair-2026-07-18.md`](queue-dispatch-repair-2026-07-18.md)
  - HISTORICAL (judged, an incident record). BUILT.
  - Filed tickets never ran because `ralph.sh` never told the worker to commit,
    nothing dispatched the root scope, and "File and run" filed without running.

## Theme 5: tickets and their lifecycle (17)

- [`work-status-contract-2026-08-24.md`](work-status-contract-2026-08-24.md)
  - CURRENT, with an operator ruling appended 2026-08-25 (declared). BUILT: NOT.
  - Do not add a ninth status. Landed-ness is derived from the Change; `done` is
    REFUSED at the write while the change is unmerged; done-conditions are the
    existing `acceptance` field as a small closed set.
- [`planning-a-run-before-it-runs-2026-08-25.md`](planning-a-run-before-it-runs-2026-08-25.md)
  - CURRENT design, operator answered §8 (declared "nothing implemented"). BUILT: NOT.
  - A planned run is an existing run in the `queued` state: no new table, no new
    status. Measured 0 of 11,883 job lines carry `queued`. The routing decision
    and its source (`policy | agent | operator`) live there, not on the ticket.
    Verified: `jobs.STATUSES` has `queued`; there is no `jobs.plan()`.
- [`who-filed-this-and-what-was-decided-2026-08-25.md`](who-filed-this-and-what-was-decided-2026-08-25.md)
  - CURRENT design (declared). BUILT: NOT.
  - Two unrelated things are spelled `owner`; delete the ticket-level one, keep
    `owner_id` the tenancy key. Three code paths stamp `operator` when nobody
    said who. Add `declined` as a state, because a decision not to build has
    nowhere to live. Verified: `tasks.py:1002` still writes the ticket `owner`;
    `STATUSES` has no `declined`.
- [`ticket-model-and-external-trackers-2026-08-07.md`](ticket-model-and-external-trackers-2026-08-07.md)
  - CURRENT (judged; banner says proposed, the code cites it as ratified). BUILT.
  - A ticket stores `title` and `body` as real fields, and there is exactly ONE
    external tracker connection with per-scope targets. Its §12 GitHub App path
    is not built; the adapter still runs on the `gh` CLI.
- [`ticket-lifecycle-and-integration-2026-08-10.md`](ticket-lifecycle-and-integration-2026-08-10.md)
  - CURRENT (declared partially shipped); one §7 row false. BUILT: PARTLY.
  - Approval is the spine: anything a machine files starts as `draft`, only a
    human's press makes it `todo`, and nothing syncs out before that.
- [`ticket-system-consolidation-2026-08-12.md`](ticket-system-consolidation-2026-08-12.md)
  - CURRENT (judged; extended by ADR 0028). BUILT.
  - There is no second data model: Tasks and Tickets are one store, so the
    duplication is lexical. The bell and the Tasks drawer became one drawer.
- [`tickets-one-store-2026-08-14.md`](tickets-one-store-2026-08-14.md)
  - CURRENT (declared decided). BUILT: PARTLY, two scopes only.
  - Postgres is the ticket store, `.tasks.json` is retired, `TODO.md` survives as
    a one-way export, the read-shadow mirror is deleted. See contradiction 8.
- [`tickets-postgres-cutover-2026-08-16.md`](tickets-postgres-cutover-2026-08-16.md)
  - CURRENT (declared stage 2 shipped; the record for ADR 0019). BUILT.
  - Relational scopes write Postgres only and fail closed; the "skip equals
    success" mirror hole is closed by `ticket_sync.WriteFailed`.
- [`deciding-on-work-from-a-chat-2026-08-22.md`](deciding-on-work-from-a-chat-2026-08-22.md)
  - CURRENT; §6 SUPERSEDED BY [`one-fact-one-place-2026-08-22.md`](one-fact-one-place-2026-08-22.md). BUILT.
  - "Approve" named two opposite outcomes in two routers; unify the decision
    vocabulary and decide inline in the transcript against live ticket state.
- [`one-fact-one-place-2026-08-22.md`](one-fact-one-place-2026-08-22.md)
  - CURRENT (declared). BUILT: PARTLY.
  - One fact gets one rendering: delete the duplicated "needs you" producers
    rather than adding a surface. Its §8 product question is still open.
- [`run-status-view-2026-07-18.md`](run-status-view-2026-07-18.md)
  - CURRENT (declared built; §3.3 superseded 2026-08-18). BUILT.
  - Run detail becomes an action surface with ONE canonical status instead of
    `job.status` and `job.task_status` side by side.
- [`chat-work-linkage-2026-07-18.md`](chat-work-linkage-2026-07-18.md)
  - CURRENT; banner falsely says not built. BUILT differently.
  - Card supersession, a per-kind auto-run preference, an in-chat related-work
    dropdown and a notification drawer. Auto-run shipped as a third chat mode
    instead; the drawer merged into the work panel.
- [`ticket-maker-2026-07-18.md`](ticket-maker-2026-07-18.md)
  - UNRATIFIED (declared accepted, judged not delivered). BUILT: PARTLY.
  - Decompose a complex request into a bounded TREE of atomic sub-tickets before
    any worker fires. `parent_id` exists; drafts are flat; no ticket-maker agent.
- [`blocked-and-escalate-flow-2026-07-18.md`](blocked-and-escalate-flow-2026-07-18.md)
  - UNRATIFIED (declared accepted, judged partly delivered). BUILT: PARTLY.
  - Any unresolvable condition writes a Block to `awaiting`, parks the ticket, and
    is resolved by one of six operator verbs; the blocked run never resumes.
    `awaiting` exists; the six verbs do not.
- [`clarifying-question-flow-2026-07-18.md`](clarifying-question-flow-2026-07-18.md)
  - UNRATIFIED (declared accepted, judged not delivered). BUILT differently.
  - A worker's question surfaces as an inline mini sub-chat card in the
    originating chat. `cardSchema.ts:28` states flatly that no `conversation`
    kind exists; answering happens in the work panel instead.
- [`chat-tickets-orchestrator-plan-2026-07-15.md`](chat-tickets-orchestrator-plan-2026-07-15.md)
  - HISTORICAL (judged; it shipped). BUILT.
  - Task and Job stay separate records, the Task is the one ticket surface, one
    Work nav item with Tickets and Runs tabs, every Job carries a receipt.
- [`notes-system-plan-2026-07-15.md`](notes-system-plan-2026-07-15.md)
  - SUPERSEDED BY [`what-a-memory-is-2026-08-23.md`](what-a-memory-is-2026-08-23.md) (judged). BUILT: NOT.
  - Do not build a new note store; retire `INTERVENTIONS.md` and render the
    project's knowledge scope. `INTERVENTIONS.md` is still live, and a third
    substrate (the Notes app vault) arrived that neither doc names.

## Theme 6: agent roles, definition and governance (11)

- [`primitive-roles-2026-07-18.md`](primitive-roles-2026-07-18.md)
  - CURRENT (declared, corrected 08-17 and 08-23). BUILT: PARTLY.
  - Agent roles are a closed enum keyed by write authority: maker, reviewer,
    triager, keeper, broker. Its §3 `<role>_run.sh` contract is unimplemented.
- [`agent-definition-and-governance-2026-08-04.md`](agent-definition-and-governance-2026-08-04.md)
  - CURRENT (judged; banner says NOT BUILT, §1 says corrected after building). BUILT.
  - There is ONE canonical way to define an agent, `agents/<slug>.md` frontmatter
    plus body, and an agent is defined by its declared contract, never discovered
    from its run history.
- [`agent-interaction-model-2026-07-31.md`](agent-interaction-model-2026-07-31.md)
  - CURRENT (judged; banner says NOTHING BUILT). BUILT: PARTLY.
  - @-mention is the one way to address an agent, agents are opt-in `mentionable`
    and `delegatable`, agent-initiated messages go to a global inbox. Its D11
    Run-to-Session rename never happened.
- [`final-plan-agent-generalize-and-bridge-fold-2026-07-16.md`](final-plan-agent-generalize-and-bridge-fold-2026-07-16.md)
  - CURRENT (declared plan of record). BUILT.
  - Generalize Agent so the bridge stops being a subsystem and becomes an ordinary
    definition, and make an autonomous agent a third `Identity.kind` whose grants
    are minted at dispatch.
- [`dev-mode-and-development-scope-2026-08-09.md`](dev-mode-and-development-scope-2026-08-09.md)
  - CURRENT (judged; ratified as ADR 0014). BUILT.
  - Developer mode gates on the RESOLVED DIRECTORY (`is_self_dir`), not the scope
    name, and `development` becomes the one name for the platform repo.
- [`operator-asks-2026-07-16.md`](operator-asks-2026-07-16.md)
  - CURRENT record (declared). BUILT: PARTLY.
  - The verbatim ask, not any report's distillation, is authoritative for what was
    requested. Its "nothing was implemented" line is now false.
- [`agent-governance-addendum-2026-08-04.md`](agent-governance-addendum-2026-08-04.md)
  - SUPERSEDED on the autonomy half BY [`work-loop-core-design-2026-08-21.md`](work-loop-core-design-2026-08-21.md) (judged). BUILT: PARTLY.
  - A chat is in exactly one of two named modes, and an autonomous agent is
    contained by default with an explicit call graph. There are three modes now.
- [`continuation-2026-07-31.md`](continuation-2026-07-31.md)
  - HISTORICAL (judged, a dated handoff brief). BUILT: n/a.
  - Background autonomy stays OFF until the execution boundary is designed, and
    records P1 to P7 of the interaction-model plan as done except the Run to
    Session rename. Its §5.1 names a flag file that no longer exists and its §2
    probes Letta, decommissioned 2026-08-16.
- [`agent-roles-research-2026-07-16.md`](agent-roles-research-2026-07-16.md)
  - SUPERSEDED BY [`primitive-roles-2026-07-18.md`](primitive-roles-2026-07-18.md) (judged). BUILT.
  - A role taxonomy must be built on output authority, not job title. Its banner
    still says `triager` does not exist. It does.
- [`synthesis-agent-protocols-2026-07-16.md`](synthesis-agent-protocols-2026-07-16.md)
  - SUPERSEDED BY [`final-plan-agent-generalize-and-bridge-fold-2026-07-16.md`](final-plan-agent-generalize-and-bridge-fold-2026-07-16.md) (judged). BUILT.
  - The three 07-16 threads converge on one spine: authority is grants, not
    identity, and phase 1 is bug repair, not features.
- [`_brief-agent-protocols-2026-07-16.md`](_brief-agent-protocols-2026-07-16.md)
  - HISTORICAL (declared). BUILT: n/a.
  - Fixes the 07-16 thread split and rules that the operator-asks record beats
    any paraphrase.

## Theme 7: agent context, prompts and memory (15)

- [`agent-context-and-toolkit-2026-08-20.md`](agent-context-and-toolkit-2026-08-20.md)
  - MODEL OF RECORD for prompt AUTHORITY (declared partially shipped). BUILT: PARTLY.
  - Four ranked layers: L0 kernel (code-owned, confidential), L1 orientation
    manifest (generated), L2 scope persona, L3 operator instructions. Verified:
    all four exist in `chat_context.py`, `orientation.py`, `custom_instructions.py`.
- [`context-assembly-budget-2026-08-25.md`](context-assembly-budget-2026-08-25.md)
  - CURRENT, the COST half of the layer contract (judged; amends the above). BUILT: NOT.
  - The contract assigns authority and never a byte budget, so every block author
    capped their own block and nobody owned the sum. A new `development` chat
    opens at 114,192 chars before a keystroke, 68% of it `live_context()`, 95% of
    THAT the append-only PROJECT.md Log, which the persona instructs agents to
    grow. Verified: `chat_context.py:1904-1906` still inlines PROJECT.md whole
    (now 80,466 chars) and no `check_prompt*` gate exists.
- [`memory-and-documentation-model-2026-08-14.md`](memory-and-documentation-model-2026-08-14.md)
  - CURRENT (declared accepted). BUILT mostly.
  - Two separate mechanisms: REACH is the scope tree, downward only, on demand;
    GOVERNANCE is one small injected layer and NOT the ancestor chain. Its §4.1
    tree diagram is now inverted relative to `scope_tree.py`.
- [`what-a-memory-is-2026-08-23.md`](what-a-memory-is-2026-08-23.md)
  - CURRENT (declared audit and recommendation). BUILT: NOT.
  - A note and a memory are the same object; a learning is a behaviour, not a
    store; collapse to one store whose axis is injection (`reach`), not kind.
- [`finding-and-surfacing-memory-2026-08-23.md`](finding-and-surfacing-memory-2026-08-23.md)
  - CURRENT (declared design only). BUILT: NOT.
  - Awareness is a property of the corpus and belongs in the cache-stable prompt;
    relevance is a property of the query and belongs in a tool call. Never inject
    a passage.
- [`agents-and-memory-reassessment-2026-08-23.md`](agents-and-memory-reassessment-2026-08-23.md)
  - CURRENT (declared); §6.2 overruled by backlog-reconciliation §1.1 and uncorrected. BUILT: n/a.
  - The design corpus was unreachable from every scoped chat, so agents rebuilt
    built subsystems. "Built and unwired is indistinguishable from absent."
- [`memory-consolidation-2026-08-16.md`](memory-consolidation-2026-08-16.md)
  - CURRENT (declared partly implemented, with a dated 08-23 correction). BUILT: PARTLY.
  - Eleven memory substrates collapse to four and Letta's blocks are exported then
    unwired. M3's scheduler and M7 never landed; `prune_stale` has no caller.
    This is the corpus's best example of a self-correcting banner.
- [`agent-context-plan-2026-08-24.md`](agent-context-plan-2026-08-24.md)
  - CURRENT (declared plan; implements work-status-contract §4). BUILT: NOT.
  - Two-tier progressive disclosure for platform vocabulary: a short always-present
    pointer, the reference fetched on demand. Measures the fixed platform prompt
    at 10,768 chars. Its D1 is deferred on the dead B3 blocker.
- [`research-agent-context-and-tool-docs-2026-08-24.md`](research-agent-context-and-tool-docs-2026-08-24.md)
  - CURRENT research (declared). BUILT: NOT.
  - "Aware before calling, not reloaded every turn" is solved by two-tier
    progressive disclosure; and done-as-committed versus done-as-merged exists
    nowhere in the codebase, in code or comment.
- [`project-context-files-audit-2026-08-25.md`](project-context-files-audit-2026-08-25.md)
  - CURRENT audit (declared). BUILT: n/a.
  - The field separates rules, spec-manifest and README; `AGENTS.md` is the
    cross-vendor standard for the first. Workshop collapses all three into PROJECT.md.
- [`project-context-files-and-flows-2026-08-25.md`](project-context-files-and-flows-2026-08-25.md)
  - UNRATIFIED (declared proposal). BUILT: NOT.
  - Split three artifact classes: `AGENTS.md` instructions, an OPTIONAL designated
    manifest with no magic always-injected filename, and agent definitions with
    import and export adapters.
- [`context-compaction-research-2026-08-20.md`](context-compaction-research-2026-08-20.md)
  - CURRENT research (declared). BUILT: n/a.
  - Prompt caching is a strict byte-prefix match, compaction must be a rare
    batched event, and verbatim retrieval beats destructive summarization.
- [`custom-context-management-proposal-2026-08-20.md`](custom-context-management-proposal-2026-08-20.md)
  - UNRATIFIED (declared design only). BUILT: NOT.
  - A Workshop-owned provider-agnostic context layer that appends cheaply and
    reduces in batched events. Reversed in direction by the 08-25 compaction doc.
- [`embeddings-and-local-inference-2026-08-20.md`](embeddings-and-local-inference-2026-08-20.md)
  - CURRENT (declared partly built). BUILT: PARTLY.
  - Ollama leaves production; embeddings run in-process everywhere (bge-small-en-v1.5
    via fastembed) behind one `EmbeddingProvider`. Ollama survives in `routes/relay.py`.
- [`background-subagents-2026-08-20.md`](background-subagents-2026-08-20.md)
  - CURRENT (declared design). BUILT.
  - Background subagents belong at Workshop's orchestration layer as a durable Job
    plus detached runner, not the CLI's `run_in_background`.

## Theme 8: chat, transcripts and compaction (10)

- [`chat-context-and-compaction-2026-08-25.md`](chat-context-and-compaction-2026-08-25.md)
  - MODEL OF RECORD for this question (declared decision, in progress). BUILT: PARTLY.
  - Stop re-injecting polled state every turn (events are appended, never polled);
    use native compaction on the API path and keep the Workshop fold only as the
    CLI adapter's implementation; production must contain no trace of `claude -p`.
    Verified: `todo_summary_block` is gone; the event channel is not built.
- [`chat-concurrency-model-2026-08-14.md`](chat-concurrency-model-2026-08-14.md)
  - CURRENT (declared; ADR-0021). BUILT.
  - A thread admits ONE operation at a time, an operation lands only on a quiet
    thread, and a message arriving mid-flight queues behind it.
- [`chat-message-ordering-audit-2026-08-05.md`](chat-message-ordering-audit-2026-08-05.md)
  - CURRENT (declared audited, measured, built). BUILT.
  - A turn is an INTERVAL and the transcript models points, so a mid-turn message
    is recorded AFTER the turn with `during_turn` and `at_segment`. The reply is
    never spliced. Four of its own sections are struck through as reverted.
- [`transcript-read-from-rows-2026-08-14.md`](transcript-read-from-rows-2026-08-14.md)
  - CURRENT (declared being implemented). BUILT: stage 1 only.
  - `chat_message` rows become the transcript and the JSONL log is retired; the
    interrupted reply is solved by sealing a stretch row, never by re-parenting.
    `chat_log.py` is still the only writer and the read path.
- [`turn-recovery-terminal-journal-2026-08-17.md`](turn-recovery-terminal-journal-2026-08-17.md)
  - CURRENT (declared implemented). BUILT.
  - On the reattach path only, if the journal already ends on a terminal `result`,
    seed from disk and finalize rather than waiting for a result that never comes.
- [`chat-agent-integration-2026-08-18.md`](chat-agent-integration-2026-08-18.md)
  - CURRENT (declared a map, not a build plan). BUILT.
  - A Claude subagent can only ever be an in-reply helper, because the turn's
    process group is killed at retirement, so "background agent" means a Run.
- [`chat-scope-design-2026-07-18.md`](chat-scope-design-2026-07-18.md)
  - CURRENT (declared partially built, corrected 08-17). BUILT: PARTLY.
  - A chat gets a first-class stored `scope` column rather than inferring it from
    a `contextKey` string prefix.
- [`client-state-management-2026-08-25.md`](client-state-management-2026-08-25.md)
  - UNRATIFIED (declared, nothing built). BUILT: NOT.
  - Three stages: a discriminated-union turn lifecycle, then TanStack Query for
    server state, then Zustand for genuine client state. The problem is server
    synchronization, not client state. None of the three packages is installed.
- [`compaction-both-paths-plan-2026-08-20.md`](compaction-both-paths-plan-2026-08-20.md)
  - SUPERSEDED BY [`chat-context-and-compaction-2026-08-25.md`](chat-context-and-compaction-2026-08-25.md) (declared by the successor). BUILT: PARTLY.
  - Headless `/compact` is dead in `-p`, so put compaction behind a provider
    adapter seam. The adapter exists with a CLI implementation only.
- [`chat-toolbar-audit-redesign-2026-07-18.md`](chat-toolbar-audit-redesign-2026-07-18.md)
  - SUPERSEDED BY [`deciding-on-work-from-a-chat-2026-08-22.md`](deciding-on-work-from-a-chat-2026-08-22.md) (judged, no banner). BUILT: NOT.
  - Replace the toolbar's raw selects with `ws-select` primitives. The toolbar was
    extracted and rebuilt around menus instead; every line reference is dangling.

## Theme 9: tools, resource access and files (6)

- [`tools-are-declared-2026-08-23.md`](tools-are-declared-2026-08-23.md)
  - CURRENT; banner understates what shipped. BUILT: PARTLY (stages 1-4).
  - A capability is a declared record with an owner; platform declarations are
    uneditable, any user may declare their own, and declaring grants nothing
    because `llm_router.tool_flags` stays the single enforcement point.
    `tool_registry.py`, `routes/tools.py`, `command_tools.py` and MCP dispatch
    all exist. No UI, no generalized `preview` tool.
- [`managed-file-references-and-path-scoping-2026-08-18.md`](managed-file-references-and-path-scoping-2026-08-18.md)
  - CURRENT (declared ACCEPTED). BUILT.
  - No raw OS path anywhere for anyone: one managed address per resource, enforced
    by the OS (bwrap plus a dedicated turn sub-user), not by a display filter.
- [`file-viewer-and-editor-2026-08-22.md`](file-viewer-and-editor-2026-08-22.md)
  - CURRENT (declared decided). BUILT (P1-P5).
  - CodeMirror 6 as the one editor, Monaco and Tiptap rejected; the real
    distinction is rendered-versus-source form, not file type; one file API behind
    a workspace-root resolver.
- [`resource-access-protocol-2026-07-16.md`](resource-access-protocol-2026-07-16.md)
  - CURRENT (declared largely built, corrected 08-17). BUILT: PARTLY.
  - Every Store has a posture (open / gated / mediated), mediation is fail-closed
    with no adjudicator, and an unattended agent is a third `Identity.kind` with
    zero ambient authority.
- [`resource-access-interface-spec-2026-07-18.md`](resource-access-interface-spec-2026-07-18.md)
  - CURRENT (declared SPEC, largely built) but names modules that do not exist. BUILT: PARTLY.
  - A provider joins with three things: a StoreAdapter, a `check()` before serving,
    and a policy record. Providers never call the broker directly.
- [`ref-preview-thumbnails-2026-08-25.md`](ref-preview-thumbnails-2026-08-25.md)
  - UNRATIFIED (declared PROPOSED, not built). BUILT: NOT.
  - Extend the ref preview card beyond the single image case to thumbnails and
    text excerpts, un-parking the pdf/text rendering that Layer 3 deferred.

## Theme 10: the navigation shell and UI (6)

- [`navigation-shell-2026-08-20.md`](navigation-shell-2026-08-20.md)
  - MODEL OF RECORD on what to build (declared partially built). BUILT: stage 1 only.
  - A 60px dock of places, a 252px page column, phone bar-one plus page toolbar,
    per-place memory, Up-is-not-Back, and no control without a substrate.
    `frontend/src/components/shell/` exists. No palette, no `/search`, `/activity`
    redirects. §7.1, §8.1, §8.6 and §13 are stale, see above.
- [`shell-mockup-spec-2026-08-22.md`](shell-mockup-spec-2026-08-22.md)
  - CURRENT reference (declared); authoritative on geometry and motion only. BUILT: PARTLY.
  - The mockup's numbers extracted as tokens so nobody re-derives geometry from
    prose, plus six tie-breaks between mockup, handoff and design doc.
- [`dashboard-2026-08-25.md`](dashboard-2026-08-25.md)
  - CURRENT SPEC, source of record for Home's first page (declared, nothing built). BUILT: NOT.
  - The dashboard's own content and geometry, extracted from the operator's
    mockup as text before anything is built, because a mockup needing a design
    tool to render goes unread. Defers to the two shell docs on the shell.
- [`tickets-in-chat-2026-08-15.md`](tickets-in-chat-2026-08-15.md)
  - CURRENT on behaviour; appearance SUPERSEDED BY [`tickets-in-chat-visual-fidelity-2026-08-16.md`](tickets-in-chat-visual-fidelity-2026-08-16.md). BUILT: PARTLY.
  - Tickets, runs and questions collapse to two surfaces: a ticket card in the
    answer and one Work panel per chat scope. Adds the third `auto` chat mode.
- [`tickets-in-chat-visual-fidelity-2026-08-16.md`](tickets-in-chat-visual-fidelity-2026-08-16.md)
  - CURRENT (declared BUILT in `83264f9`). BUILT.
  - Rebuild the three surfaces to the design's drawn geometry rather than
    re-deriving them from our own primitives. The PNGs win on appearance.
- [`navigation-shell-source/`](navigation-shell-source/) (3 tracked files; the three
  session transcripts moved to the untracked `docs/masini/` on 2026-08-27)
  - HISTORICAL (declared: "Nothing here was built, and several parts describe a
    system that does not exist"). BUILT: NOT.
  - Verbatim input the two shell docs were derived from, preserved for wording.
    `README.md` is the map; `HANDOFF.md` is the fullest single statement but its
    §3 bar-one was reversed later in the same file; the three transcripts (moved
    2026-08-27 to the untracked `docs/masini/`, being raw operator dictation)
    have the six navigation patterns and the dock choice, motion/per-app
    memory/branch-picker reversals, and a session reconstructed from an operator
    paste after a usage limit; `workshop-shell-mockup.dc.html` is the
    892-line artifact everything is measured against.

## Theme 11: usage, limits, providers and model routing (6)

- [`counting-tokens-2026-08-25.md`](counting-tokens-2026-08-25.md)
  - MODEL OF RECORD for the meter (declared DESIGN, nothing changed). BUILT: NOT.
  - No cap can be trusted until this lands: one `tokens` column, three writers,
    three units, differing by up to 13.6x on the same turn. Decides one seam,
    `backend/services/token_usage.py`, and money as the unit for budget and
    admission. Sequences BEFORE `limiting-usage`. Verified: `token_usage.py`
    does not exist.
- [`limiting-usage-2026-08-24.md`](limiting-usage-2026-08-24.md)
  - CURRENT, with an operator ruling appended 2026-08-25 (declared PROPOSAL). BUILT: NOT.
  - Cost is unpredictable (168 runs, median 107,284, spread 42.6x), so the fix is
    pause-and-resume at the cap, not a better reserve estimate. The cap covers
    autonomous work only, the unit is dollars at published API prices, the plan is
    a setting that starts unset. Contains an in-place retraction of its own
    earlier under-reservation claim.
- [`limits-plan-audit-2026-08-24.md`](limits-plan-audit-2026-08-24.md)
  - CURRENT audit (declared). BUILT: NOT.
  - The limits plan's direction survives but its tickets do not: four of five
    carried a false measured claim, chiefly that an iteration boundary is a
    checkpoint. `maybe_autocommit` fires only after reviewer approval.
- [`routing-work-to-a-model-2026-08-25.md`](routing-work-to-a-model-2026-08-25.md)
  - CURRENT design, §8 settled (declared, nothing implemented). BUILT: NOT.
  - One declared field and it already exists: `effort` (quick/standard/deep),
    read by a settings-owned policy table mapping band to starting tier and
    ceiling tier. A failed run escalates one tier on its next attempt. Dimensions
    do not combine, because any monotone combination of two ordered dimensions
    collapses to one ordered output. Verified: `tasks.EFFORTS` exists; there is no
    `resolve_model` in live backend code.
- [`providers-credentials-and-limits-2026-08-23.md`](providers-credentials-and-limits-2026-08-23.md)
  - CURRENT; banner falsely says nothing built. BUILT: PARTLY (stage 1).
  - Two interfaces (`LlmProvider`, `EmbeddingProvider`) with transport plus wire;
    a subscription credential may only ever serve its owner; one resolver
    returning one `ModelChoice`. §10 business model left open. Stages 3 and 4
    (cost dimension on the ledger, one resolver) are not built.
- [`model-access-and-byok-2026-08-11.md`](model-access-and-byok-2026-08-11.md)
  - SUPERSEDED BY [`providers-credentials-and-limits-2026-08-23.md`](providers-credentials-and-limits-2026-08-23.md) (judged). BUILT.
  - A third party may NOT bring a Claude subscription OAuth credential into this
    system; BYOK means API keys only, enforced in software.

## Theme 12: handoffs, audits and session records (8)

Read only for provenance. None of these is a design.

- [`INDEX-open-work-2026-08-26.md`](INDEX-open-work-2026-08-26.md)
  - CURRENT companion index (declared, generated 2026-08-26). BUILT: n/a.
  - The open TICKETS grouped by theme in build order, with the chats they came
    from. 311 tickets, 159 live at generation.
- [`handoff-2026-08-17-second-pass.md`](handoff-2026-08-17-second-pass.md)
  - HISTORICAL (judged; declares itself in progress with 88 files uncommitted). BUILT: PARTLY.
  - `job` to `run` is blocked by a live `run_id` collision, and before renaming X
    to Y, grep for Y. Its §4 next-work list is mostly still undone.
- [`handoff-2026-08-18-execute.md`](handoff-2026-08-18-execute.md)
  - HISTORICAL (judged, no banner). BUILT: PARTLY.
  - Execute target-architecture item 2: annotate one router per commit, then
    consume `schema.gen.ts` instead of hand-typed `.json()`.
- [`review-findings-2026-07-15.md`](review-findings-2026-07-15.md)
  - HISTORICAL (judged, no status line). BUILT.
  - Five shipped-loop defects: the shared iteration output file, the over-broad
    classifier grep, the learnings scope-key mismatch, environment failures
    burning attempts, dedup merging into parked tasks.
- [`research-findings-2026-07-15.md`](research-findings-2026-07-15.md)
  - HISTORICAL (judged). BUILT: PARTLY.
  - The reviewer gate must be grounded in executable evidence, ralph loops need
    hard iteration caps, the ambient digest sits after the cache breakpoint, and
    0.80-0.90 cosine merges must be non-destructive.
- [`session-handoff-2026-07-14.md`](session-handoff-2026-07-14.md)
  - HISTORICAL (judged). BUILT.
  - The north star in the operator's framing: conversations produce well-scoped
    tickets orchestrated in the background so nothing drops when a chat drops.
- [`session-handoff-2026-07-15.md`](session-handoff-2026-07-15.md)
  - HISTORICAL (judged). BUILT: n/a.
  - Continue coherent-rebuild stages 0-9 autonomously with parallel subagents.
- [`session-handoff-2026-07-15b.md`](session-handoff-2026-07-15b.md)
  - HISTORICAL (judged). BUILT.
  - Records what the chat/tickets/orchestrator wave actually shipped, and that
    the notes and GitHub companion plans were not built at that time.

---

## Counts

- 135 entries (134 `.md` files directly under `docs/platform-redesign/` plus
  the `navigation-shell-source/` group of 6 files as one entry).
- MODEL OF RECORD: 8. CURRENT: 67. UNRATIFIED: 10. SUPERSEDED: 16. HISTORICAL: 17.
- BUILT: 32. PARTLY BUILT: 45. NOT BUILT: 46. Not applicable or unverified: 12.
- 19 orphans. 24 documents stale in a way their own banner does not say.
- Themes: entity model 13, app separation and hosting 16, branches and merge 8,
  work loop and autonomy 19, tickets 17, agent roles 11, agent context and
  memory 15, chat and transcripts 10, tools and files 6, navigation shell 6,
  usage and limits 6, handoffs and audits 8.
