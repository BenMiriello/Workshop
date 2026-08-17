# Coherence overhaul: the spine and the ratchet (2026-08-16)

Status: **accepted; all six threads built 2026-08-16/17.** Build log in §10 records
what landed, in which commit, and the four places this plan's own estimates turned out
to be wrong. Open items are listed at the end of §10; the largest is `Thread.state`,
deliberately not built. Written at the operator's
direction on 2026-08-16: *"research into this and the overhaul or redesign we could do
to make this system much more clear and coherent ... Do not be shy about overhauling and
don't use the way things are already as an excuse not to do it right."*

Companion to, and partly a correction of,
`ontology-review-2026-08-17.md` (unmerged, on branch
`claude/schema-hierarchy-review-6gu6t4`). That doc is good and most of its spine is
adopted here. Where this doc contradicts it, the contradiction is called out with a
measurement.

Method: four parallel audits of the live tree on 2026-08-16 (memory substrates;
chat-vs-agent; work execution; a repo-wide sweep), plus independent research. Every
claim carries a `file:line` or a count taken today.

---

## 0. The finding that reframed it

The ontology review argues Workshop has an **ontology** problem: the nouns sit at the
wrong level. That is true and its spine is largely right.

But the measurements say the deeper problem is **entropy**, not taxonomy. The concepts
are mostly fine. What is broken is that each one has two to five implementations, they
have drifted apart, and **nothing in this system has ever removed anything.**

Counted today:

- **11** durable memory substrates, of which 4 are dead or vestigial.
- **5** independent `context_key → scope` mappings, and **they disagree**: for
  `context_key="development"`, `chat_store` yields `app:development` while `knowledge`
  yields `development`. **27 of 94 live threads** carry the stored value
  `scope: "app:development"`, an address no other module in the system recognises.
- **16** independent date formatters in the frontend, with real behavioural divergence:
  `CommutePages.tsx:84-93` has no day tier, so 48 hours renders `"48h ago"` there and
  `"2d ago"` everywhere else. There is no `lib/time.ts`.
- **6** status-to-StatusKind mappers, **2 already drifted** (`DeploysPanel.tsx:61` maps
  `queued → running`; everything else maps it to `stopped`) — in a domain whose
  canonical file, `lib/jobs.ts:1-5`, carries a comment warning that exactly this drift
  already happened once.
- **4** definitions of the autonomy enum; **3** session stores; **2** agent registries
  plus a third, dead, in Postgres.
- **62** design docs in one flat directory, **22** with no status banner.

That last number is the mechanism. A design doc lands, nothing records that it landed,
a later pass re-derives the same thing beside it, and neither is deleted.

**So this proposal has two pillars, and the second matters more.** The spine (§2)
reduces the nouns. The ratchet (§7) is what stops them growing back. An overhaul
without a ratchet buys about six weeks.

---

## 1. Where the ontology review is right, and where it is out of date

**Right, and my audits strengthen it.**

Its central claim is that "run" is doing three unrelated jobs and that a ticket attempt
should be a conversation. The evidence is stronger than it knew:

- The Postgres `run` table (`db/models.py:553`) has **2,753 rows and zero readers**. It
  is written only by the one-shot `db/backfill.py:432`. It has already diverged:
  `max(started_at)` is 2026-08-05 while `jobs.jsonl` ran to 2026-08-15. **Deleting Run
  as a schema noun costs nothing, because nothing is using it.**
- The Postgres `agent` table (`:534`) has **zero reads and zero writes**, with three
  inbound foreign keys that can never be non-NULL.
- Its claim that the escalation path is unreachable is confirmed exactly: **`awaiting`
  is populated on 0 of 197 live tickets**, and `job.awaiting_input` was written **3
  times in the system's life** (July 14, 16, 18), all three later overwritten with a
  terminal status. `run_inbox.sync` has raised **one** item ever, now dismissed.
- Its claim about "chat with an agent" understates the problem. The button is not
  merely mis-wired. `chat_store._promotion_patch` (`chat_store.py:233-278`) rewrites
  any `agent:<slug>` or `build:<slug>` context key to `project:` or `general` **at
  creation and on every subsequent patch**, and its only escape hatch (`scope_locked`)
  is not settable through any API. So `AssistantAgentContextProvider` and
  `BuildAgentContextProvider` are **both unreachable**, and **0 of 94 live threads**
  have an `agent:` key. There is currently **no way to chat with an agent directly at
  all**. The only path that reaches an agent is `@mention`.

**Out of date, corrected here.**

- *"Threads and tickets are in no index at all"* — **false.**
  `semantic_index.KINDS` is `("note","doc","chat","ticket","vault")` and the live index
  holds **4,600 chunks**: doc 3,119 / chat 1,088 / ticket 244 / note 149, across 79
  chats and 195 tickets. A live query returns mixed ticket+chat+doc hits in 0.58 s. The
  review's "single highest-value change in this whole document" is already built.
- *"`search` cannot express a visibility rule"* — **false as of today.** The MCP schema
  exposes `scope`, documented as "one scope and everything below it in the tree", and
  `search()` carries `scope=` and `from_scope=`.
- *"no non-vector fallback"* — **false.** BM25 is at `semantic_index.py:743-814`,
  reached at `:986`, and the return value names the mode.
- It never cites `memory-and-documentation-model-2026-08-14.md`, which is marked
  **accepted, ready to build** and already covers most of its memory section.

**Where I disagree on judgement.**

- **"Ticket sequencing is a DAG" is overstated, and the operator said so.** The data
  agrees: **8 of 197 tickets** carry a `depends_on`, **11 edges total**, forming four
  disjoint fragments (one 4-deep chain and three 2-level fans), all in one scope.
  `parent` is **0 of 197** — the sub-ticket tree, its depth cap, its ADR and its
  Postgres depth-guard trigger have never processed a single record. Sequencing is
  real, correct, tested code exercised by 4% of the corpus. Treat it as **an edge on a
  ticket**, not as a scheduling substrate, and do not build a canvas for it.
- **Do not rename `role:` to a facet yet.** The review is right that a role is a class
  and not an address. But the axis holds exactly **one note**, and renaming an axis is a
  migration. Make it *visible* now (it is injected into every reviewer's prompt and
  cannot be listed in the Notes UI), rename it once, later, with the rest of the
  vocabulary.

---

## 2. The spine

```
Workspace                     the installation
 └── Container                Project | App | workshop | development
      ├── Ticket              durable intent; the unit of approval
      ├── Thread              durable transcript: a chat OR an agent's work log
      │    └── Turn           one execution of one agent over this thread (internal)
      ├── Note                curated knowledge
      └── Store               a named data surface

Agent      declared at the workspace, granted into Containers
Trigger    (schedule | event) -> opens a Thread
Inbox      a derived view: every Thread awaiting you
```

Adopted from the ontology review, with the deletions re-scoped by what the audits found
to be already dead.

| Change | Cost, measured |
|---|---|
| **Delete the Postgres `run` table** | Zero readers, backfill-only writer, already stale. Free. |
| **Delete the Postgres `agent` table** | Zero reads, zero writes. Free (3 FKs to drop). |
| **Delete `participants`** | No code path reads it. Its only consumers are a search predicate and a filter chip in `Chats.tsx`. Replaced by a real `responder`. |
| **Make `agent:` context keys reachable** | Requires removing the `_promotion_patch` rewrite. This is the fix that makes "chat with an agent" exist. |
| **Retire `build:`** | Last load-bearing remnant of the retired builder/assistant axis, and its own chat path already does not function: all 8 build agents are `retired`, and `AgentBuild.tsx:703` hands `build:<slug>` to a `Chat` whose first send rewrites it away. |
| **Add `Thread.state`** | `idle / working / awaiting / done / failed`. The one new field. It is what makes an inbox derivable and an escalation reachable. |

**One correction to the review's plan.** It proposes `job_ids → thread_ids` and moving
ticket dispatch onto threads as step S5. That is right, but it must be sequenced after
the data-loss bug in §3, or the migration will carry the loss forward.

---

## 3. The bug nobody was looking for

The relational ticket write path **silently discards five fields**.

`db/ticket_sync.py:580-589` and `:621-627` enumerate every mirrored column.
`latitude`, `max_attempts`, `attempts`, `awaiting` and `assignee` appear in **neither**.
Meanwhile:

- `latitude` has its own module and a documented four-level cascade
  (`services/latitude.py:69-75`) and is a first-class field in the canonical record.
- `awaiting` is described as "the general escalation primitive"
  (`services/run_inbox.py:3`).
- `attempts` and `max_attempts` drive the retry cap at `tasks.py:1741-1744` and
  `scripts/project_ralph.sh:833`.

The drop was justified at `db/repo.py:21-25` as "0% occupancy or a mirror of another
field", which was true of the July snapshot. It is **no longer true of `attempts`**,
which is non-zero on 31 of 114 records in the frozen file. Worse, the compatibility
shape re-derives `attempts` from the dead `run` table (`repo.py:118-119`), so for the
two relational scopes it is **permanently 0** and the attempt cap is structurally
unenforceable. `assignee`/`owner` are hardcoded to the literal `"operator"`
(`repo.py:201-202`).

This is not an ontology question. It is live data loss on the store that became
authoritative on 2026-08-15, and it goes first.

---

## 4. The other thing to fix immediately

`PLAID_ENV` is advertised in `infra/systemd/secrets.env.example`, listed as a required
secret at `app_registry.py:299`, and checked by `scripts/security_audit.sh:84-85`.
**Nothing reads it.** The code that picks the Plaid host reads `PLAID_ENV_URL`
(`finance_plaid.py:33`, `plaid_verify.py:30`), defaulting to
`https://production.plaid.com`. Setting `PLAID_ENV=sandbox` still hits production
against the operator's real bank connections. Unrelated to this overhaul; found on the
way; must not wait for it.

---

## 5. Threads of work

Each stands alone and can ship without the others.

| # | Thread | Contents |
|---|---|---|
| **T1** | **Memory** | `memory-consolidation-2026-08-16.md`. 11 substrates to 4; the index learns when chats/tickets/docs change; the learnings store gets write-time reconciliation and a GC that can actually fire; Letta's 23 orphaned memory blocks are exported, then its residue removed. **In flight.** |
| **T2** | **Chat is the session** | Make `agent:` context keys reachable so "chat with an agent" exists. Delete `participants`, replace with `responder`. Add `Thread.state`. Retire `build:`. Fold the standalone run view into the thread view. |
| **T3** | **One scope module** | Collapse the 5 disagreeing `context_key → scope` mappings into one canonical module, and repair the 27 threads stamped `app:development`. This is the prerequisite the 08-14 memory doc already identified and deferred. |
| **T4** | **Delete the dead schema** | The `run` and `agent` tables, the write-never lease columns, `ticket.sort_order`, `run.attempt_no`, and the four dead `chat_thread` columns (`title_source`, `mcp_servers_enabled`, `pre_plan_permission_mode`, plus the four documented-but-unwritten `state` keys). Fix §3 first. |
| **T5** | **One definition per concept** | One date formatter, one status mapper, one autonomy enum, one participant normaliser. Delete zero-caller routes (`/redact`, `/restore`, `/skills`, `/backlog`, `/auto-pr`, `GET|POST /inbox`) and `ReviewDialogs.tsx`. Fix `RUN_TRIGGERS` missing the live `discord` kind. |
| **T6** | **The ratchet** | §7. Without this the rest regrows. |

---

## 6. What we are deliberately NOT building

Stated so it stops being rediscovered.

- **No knowledge graph.** arXiv 2602.19320 measures the "Agency Tax": MemoryOS at 32.4 s
  per turn, AMem at 15 h of offline construction, Nemori at 7.04 M tokens to index. It
  also names the **Context Saturation Gap** — most memory benchmarks now fit inside a
  128k window, so the machinery frequently buys nothing. We have 4,600 chunks and a
  0.58 s query.
- **No control-flow authoring surface.** Not a pipeline builder, not a DAG canvas. The
  argument for structure is that the plan should live "somewhere it can't tire, grade
  itself, or forget"; in Workshop that place is the **ticket**, which is already
  operator-legible in a way a flowchart is not.
- **No compaction or summarisation.** ADR 0011 settled this and it remains correct.
- **The `~/notes` vault stays out of the default retrieval corpus.** 965 files, ADR 0010
  makes it read-only to agents, and the 08-14 doc removed it from the design after
  looking inside. This is a decision, not an omission.
- **No multi-tenant guardrails.** Single operator, per CLAUDE.md.

---

## 7. The ratchet

The reason this document is the fourth of its kind.

**R1. A design doc must record when it lands.** 62 docs, 22 with no status. Three docs
carry a "NOT BUILT" banner for things that **are** built (`primitive-roles-2026-07-18`,
`resource-access-protocol-2026-07-16`, `chat-scope-design-2026-07-18`), which actively
misleads the next reader. Require a status line, and add the commit sha when it ships.

**R2. Measure occupancy before designing on a field.** The single most effective tool in
all four audits was counting how many live records actually carry a field. It found
`parent` at 0/197 with an ADR behind it, `latitude` at 0/197 with a module behind it,
and `attempts` at 31/114 being silently dropped. Make this a script, run it in the
reviewer gate, and print what is at zero.

**R3. One definition per enum, enforced.** A test that fails when a status vocabulary is
declared in two places. Six mappers with two already drifted is not a discipline
problem; it is a missing test.

**R4. A deletion is part of the work, not a follow-up.** Every one of the four audits
found code whose replacement had already shipped beside it. `queue_runner.sh` exists
because `dispatch_triggers.sh` dispatched nothing; both are now disabled and both remain.

**R5. Correct the instructions.** Root `CLAUDE.md` is wrong in three places, and it is
loaded into every agent's context:
- `:46` directs all frontend work to `src/ui` and `src/styles/tokens.css`. **Neither
  path exists**; the design system is the `packages/ui` package, which
  `frontend/CLAUDE.md` documents correctly.
- `:10` says `projects.json` and `build_agents.json` are tracked. Neither is in git;
  both live under gitignored `.runtime/`.
- `:44` says agents are keyed by `role`, not `runtime`. `runtime` is still the dispatch
  key at `chat_context.py:2033` and still branches the frontend at `AgentDetail.tsx:632`.

A wrong instruction in an always-loaded file is the highest-leverage defect in the
system, because every agent inherits it.

---

## 8. Sequence

| # | Step | Gate |
|---|---|---|
| **S0** | §3 ticket field loss; §4 `PLAID_ENV`. | Data loss and a production-credential footgun. Nothing waits for these. |
| **S1** | T1 memory. | The operator's stated critical path. In flight. |
| **S2** | T3 one scope module, and repair the 27 `app:development` threads. | T2 needs one scope vocabulary to build on. |
| **S3** | T2 chat is the session: `agent:` reachable, `participants` deleted, `Thread.state` added. | The operator-facing broken feature. |
| **S4** | T4 delete the dead schema. | After S0, so the field loss is fixed before the migration. |
| **S5** | T5 one definition per concept. | Independent; can run alongside anything. |
| **S6** | T6 the ratchet. | Last, because it encodes what the earlier steps taught. |

---

## 9. Sources

Research done for this doc, beyond the ontology review's own list.

**Memory** ·
[Anatomy of Agentic Memory: taxonomy and system limitations](https://arxiv.org/html/2602.19320v1) (Context Saturation Gap; the Agency Tax) ·
[Silent memory pollution in background agent execution](https://arxiv.org/pdf/2603.23064) ·
[Memory eviction and forgetting](https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents) (write-time ADD/UPDATE/DELETE/NOOP; salience floor; decay as re-ranking) ·
[STALE: can agents know when their memories are invalid](https://arxiv.org/pdf/2605.06527) ·
[TOKI: bitemporal contradiction resolution](https://arxiv.org/pdf/2606.06240) ·
[Letta memory blocks](https://www.letta.com/blog/memory-blocks/) ·
[Context engineering for agents](https://www.langchain.com/blog/context-engineering-for-agents)

**Chat and background sessions** ·
[VS Code 1.107: Agent HQ folded into the Chat view, standalone Agent Sessions view slated for removal](https://4sysops.com/archives/vs-code-1107-brings-chat-integrated-background-agents-and-agent-hq-for-multi-agent-orchestration/) — independent, very recent confirmation that a background session is a chat.

**Orchestration structure** ·
[Claude Code dynamic workflows](https://alexop.dev/posts/claude-code-workflows-deterministic-orchestration/) — the plan belongs somewhere it cannot tire, grade itself, or forget.

**Migration** ·
[Strangler fig](https://learn.microsoft.com/en-us/azure/architecture/patterns/strangler-fig)

---

## 10. Build log

Per R1. A thread is listed only once committed AND independently verified, not when
a worker reported it done.

| Thread | Commits | Landed | What it actually did |
|---|---|---|---|
| **S0** ticket field loss | `fe4719b` | 08-16 | Five fields (`latitude`, `max_attempts`, `attempts`, `awaiting`, `assignee`) were silently dropped by the relational write path. Migration `e9c4a71f0d38`. Round-trip test failed before, passes after. Kept all five: occupancy at rest is the wrong test when a live writer exists, and `PATCH /tasks` accepts all five. |
| **S0** PLAID_ENV | `565c45e` (swept) | 08-16 | The documented, required knob was read by nothing while the real switch defaulted to production. Now resolves, and refuses to guess when unset. Operator was already on production and intended to be. |
| **T1** memory | see `memory-consolidation-2026-08-16.md` §9 | 08-16 | 11 substrates to 4. The GC could never fire; the index never learned about chat/ticket/doc writes; Letta's 18 blocks exported before teardown. 15/15 acceptance. |
| **T3** one scope module | `13294b2` | 08-17 | Five disagreeing mappings collapsed into `scope_tree.scope_for_context`. Damage was **33 threads, not the 27 estimated here**: 27 stamped `app:development` plus 6 development chats stamped `workshop`. `thread.scope` is now derived on read and never stored, so staleness after a rescope is unrepresentable rather than fixed. `scope_locked` deleted (no API could set it; one reader). |
| **T2** chat is the session | `007c44c` | 08-17 | `_promotion_patch` deleted, so `agent:<slug>` is a durable context key answered by that agent. Verified independently: the key survives creation and two patches and resolves to `AssistantAgentContextProvider`, not the general assistant. `participants` deleted end to end; the 15 threads carrying one had their slug moved to `agent_id` rather than dropped. `build:` folds to `agent:` at the store and the provider; `chat_context.py:2033`'s `runtime == "builder"` is gone. |
| **T4** dead schema | `eb66a7c` | 08-17 | `run` (2,753 rows, zero readers) and `agent` (zero reads, zero writes) tables dropped, migration `def2cbf58df8`, proven reversible on a throwaway database. `job_ids`, `lease_until`, `last_heartbeat` and `claim_pid` were being dropped by the mirror exactly like the S0 five: made real tracked columns rather than derived from a table nothing wrote. `db/backfill.py` updated, since it was a loaded gun for the next fresh install. |
| **T5** one definition | `61afeb9`, `c50cf35`, `53fa1a0`, `b9b7c1d`, `604a38d` | 08-17 | 17 date implementations collapsed into `frontend/src/lib/time.ts` (CommutePages' missing day tier fixed, so 48h no longer reads "48h ago"). 3 status mappers to 1, including the `queued -> running` drift. Zero-caller routes deleted (`/redact`, `/restore`, `/skills`, `/backlog`, `/auto-pr`, two `/environment`, `GET|POST /inbox`). Autonomy enum 4 declarations to 1. `ReviewDialogs.tsx` deleted after tracing that its bug cannot recur. |
| **T6** the ratchet | (with T5 commits) | 08-17 | `scripts/doc_status.py`, `scripts/check_one_definition.py`, `field_occupancy.py` extended with a per-store split and `--fail-on-new-zero` against a committed baseline. Wired into `reviewer.sh`. Docs with no status: **22 to 14**. Four misleading NOT-BUILT banners corrected; none now disputes an artifact that exists. |

### Corrections this work forced on the plan above

- §3 said the five dropped fields were the bug. T4 found **four more** on the same
  path (`job_ids`, `lease_until`, `last_heartbeat`, `claim_pid`). Same defect class,
  missed by this doc.
- §5 estimated 27 damaged threads. It was 33.
- §5's T5 line named `RUN_TRIGGERS` missing `discord` as a live constraint bug. It
  resolved itself: the `run` table and its enum were deleted by T4.
- §2's table implies `participants` could simply be deleted. It could, but 15 threads
  carried real agent identity in it; that had to be migrated to `agent_id`, not dropped.

### Still open

- **`AgentDetail.tsx:699`** still branches on `agent.runtime === "builder"`, gating five
  UI branches including navigation. The honest conversion is
  `role === "maker" && !!project_slug`, not a bare role check, which would send a
  project-less maker to a work pane that does not exist. Its own pass.
- **`AgentBuild.tsx:703`** still sends `contextKey="build:<slug>"`. The server folds it
  so the page works, but retiring the spelling needs `chatScope.deriveMode` to learn
  maker-ness some other way.
- **`Thread.state`** (`idle | working | awaiting | done | failed`) is deliberately NOT
  built. It is the one new field that makes the inbox derivable and the escalation
  reachable, and it deserves its own pass rather than riding T2.
- **`tasks.STATE_TYPES` vs `db/models.STATE_CATEGORIES`** remain two identical tuples.
  Consolidating forces SQLAlchemy into every `tasks` import, which costs more than the
  duplicate. Recorded rather than forced.
- The root-owned **Letta process on :8283** is still running. Needs the operator.
