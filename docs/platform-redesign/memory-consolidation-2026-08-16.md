# Memory consolidation: eleven substrates to four, and a keeper that runs (2026-08-16)

Status: **plan, being implemented.** Build log at the end of this doc records what
has landed and in which commit; per R1 of the coherence overhaul, a doc that does not
say when it landed is how the next pass ends up rebuilding it beside itself.

Written 2026-08-16 at the operator's direction
("a proper system for memory management without junk extra old systems or
mismanagement is critical ... Including that Letta is still part of it").

Companion to, and NOT a replacement for:
- ADR 0002 (the knowledge store is the notes substrate) — upheld.
- ADR 0010 (the `~/notes` vault is read-only to agents) — upheld, and this doc
  explains why the vault therefore stays out of the default index.
- ADR 0011 (the model manages the conversation; we manage what enters it) — upheld,
  and sharpened: this doc is entirely about "what enters it".
- `memory-and-documentation-model-2026-08-14.md` (accepted, ready to build) — its
  reach/governance split is adopted wholesale. What it did not cover is the
  **learnings** substrate and the **maintenance** story, which is where the actual
  rot is. See §2.

Every count and `file:line` below was measured on 2026-08-16 in the live tree.

---

## 1. The finding

The 08-14 model doc counted five substrates. The 08-12 audit counted nine. A fresh
count today finds **eleven** durable-text substrates. Four are dead or vestigial, and
the two that reach every prompt are the two nobody maintains.

But the headline is not the count. It is this:

> **The garbage collector cannot fire, because the injector falsifies its
> precondition.**

`learnings.memory_block()` (`learnings.py:639`) renders a whole scope, ranked, capped
at 60 entries and 900 tokens, and stamps `retrieval_count` and `last_retrieved` on
every entry it renders. The `global` scope holds 15 entries, so every entry fits under
both caps and is therefore rendered — and thus "retrieved" — on **every single turn**.

`learnings.prune_stale()` (`learnings.py:847`) keeps an entry when it is
`recent OR reinforced OR retrieved`. Since `memory_block` guarantees `recent` and
`retrieved` are permanently true for all 15, **`prune_stale` can never archive
anything**. It also has zero non-test callers (`learnings.py:952`, its own CLI), so it
has never been asked to try.

The consequence is visible in this very session's system prompt. Two of the fifteen
`global` learnings are:

```
Ralph rejected: py_typecheck_fail
Ralph rejected: ts_typecheck_fail
```

Thirty-three characters each, zero instructional content, written by an autonomous
loop on 2026-07-15, retrieved 75 times, injected into every agent prompt for a month.
This is precisely the failure arXiv 2603.23064 names: **background execution writes to
shared memory with no attribution, and nothing downstream can tell an agent-written
memory from an operator-written one.**

A second instance, same store: entries 9 and 14 are the **same guardrail** ("DO NOT
claim a service restart succeeded..."), created 2026-07-15 and 2026-08-14, both
carrying the same 2026-08-13 revision note, both live, both injected. A revision
*added* a record instead of superseding one, because dedupe is a hash of the text and
the text was edited. This is the "stacked contradictions" failure mem0's write-time
reconciliation exists to prevent.

And a third, which is almost funny: `shared/knowledge/deep-search-dies-with-the-gpu-no-lexical-fallbac-1a001d05335.md`
is a `workshop`-scope note asserting that `search()` has no non-vector path. BM25 was
wired at `semantic_index.py:743-814`, reached at `:986`, in commit `aa457c8`. **The
note is false, and its title is injected into every chat turn's KNOWLEDGE block.**

Memory that is wrong is worse than memory that is missing, because it is confidently
consumed. All three cases are the same defect: **nothing reconciles a write against
what is already there, and nothing ever removes anything.**

---

## 2. What the 08-14 doc did not cover

That doc is good and its core split is adopted here unchanged:

> **Reach** is the tree — you see your own scope and everything below it, on demand,
> unbounded. **Governance** is a small named layer injected into every prompt, bounded
> and cache-stable. Conflating them makes a memory system either leak everything or
> reach nothing.

It explicitly scoped out the one store that is actually rotting:

> "**Learnings** — `services/learnings.py`, deduped corrections injected as guardrails.
> A distinct concept, correctly separate, not merged here."

Learnings is separate, and that was the right call. But "separate" was taken to mean
"out of scope", and it is the substrate that (a) is injected **first and
unconditionally** into every prompt, (b) has **no frontend surface at all** (grep
`learnings` across `frontend/src` and `mobile/` returns nothing), and (c) has five
functions with zero callers. It is the only always-injected store the operator cannot
see, edit, or prune from the app.

Its second conclusion also expired. It measured "20 workshop notes, 1 development
note" and concluded **"decay and eviction are premature at 21 notes"**. Today:
**88 workshop notes, 19 development notes, 110 total**, and by mtime **82 notes were
written on 2026-08-14 alone** — the same day the doc was written. The store grew 5x in
two days with no write-time merge, no dedupe, and no importance filter. The premise no
longer holds.

Credit where due: the doc's "delete first" item was executed. `chat-distilled.md` and
`consolidated-memory.md` are gone from the tree.

---

## 3. What is actually there, measured 2026-08-16

| # | Substrate | Live data | Reaches a prompt? | Verdict |
|---|---|---|---|---|
| 1 | Knowledge notes (`knowledge.py`) | 110 notes / 468K | **yes**, titles+hooks, `chat.py:1500` | **KEEP** — the healthy one |
| 2 | Learnings (`learnings.py`) | 29 rows / 18.6K | **yes**, unconditional, `chat.py:1471` | **KEEP, GOVERN** |
| 3 | Semantic index (`semantic_index.py`) | 4,600 chunks / 19.5M | no — **pulled** by tool | **KEEP, REPAIR** |
| 4 | Chat + ticket stores | 2,403 events / 26M; 133 tickets | own thread only | **KEEP** — the episodes |
| 5 | `~/notes` vault | **965 files / 5.5M** | no; 0 chunks indexed | **KEEP, out of scope by ADR 0010** |
| 6 | `docs/` | 170 files / 4.3M | no; 3,119 chunks | **KEEP** |
| 7 | `memory_tree.py` | 42-line YAML, **0 clients** | no | **DELETE** |
| 8 | Letta `:8283` | **7 agents / 23 memory blocks** | no | **EXPORT, THEN DECOMMISSION** |
| 9 | `memory_promotion.py` | can never fire | no | **DELETE** |
| 10 | learnings promotion lobe | `promoted.jsonl` never written | no | **DELETE** |
| 11 | `backlinks.json` | 7 rows, **all chat**; 0 note, 0 ticket | no | **REPAIR** |
| — | `memory_keeper.py` | watermark frozen **29 days** | no | **REPURPOSE** |
| — | `memory-policy.json` | **file absent**, read on every recall | no | **KEEP defaults, document** |
| — | CLI auto-memory | **125 files / 148K**, grew 4x in 4 days | **yes**, every session | **OUT OF OUR CONTROL — flag** |

Target: **four substrates the platform owns** (notes, learnings, index, episodes),
plus two it reads but does not own (vault, docs).

---

## 4. Decisions

**D1. Memory has three tiers and we already have all three. We do not build a graph.**

| Tier | What it is | Where it lives | Mutability |
|---|---|---|---|
| **Episode** | what happened: messages, tickets, runs, docs | chat log + Postgres + `.tasks` | append-only |
| **Retrieval** | how you find an episode | `semantic_index` | derived, rebuildable |
| **Context block** | small curated text always in the prompt | knowledge notes + learnings | curated, superseding |

No knowledge graph. arXiv 2602.19320 measures the "Agency Tax" on elaborate memory:
MemoryOS at 32.4 s per turn, AMem at 15 h of offline construction, Nemori at 7.04 M
tokens to index. It also names the **Context Saturation Gap** — most memory benchmarks
now fit inside a 128k window, so the machinery frequently buys nothing. We have 4,600
chunks and a 0.58 s query. Adding a graph would cost a great deal and answer questions
nobody has asked.

**D2. The write path reconciles. Nothing is admitted unexamined.**

Adopt mem0's four-way resolution at write time for learnings: compare each candidate
against semantically similar existing entries and resolve to **ADD / UPDATE / SUPERSEDE
/ NOOP**. A revision of an existing rule must supersede it, not sit beside it. This is
the specific, targeted fix for the duplicated restart guardrail.

**D3. Every memory write carries who wrote it, and machine noise is refused.**

Per arXiv 2603.23064, the root cause of silent pollution is missing attribution. Every
learning already carries `source`; nothing acts on it. Two changes: an autonomous
writer's entries are tagged and are prunable on a shorter clock than an operator's, and
a **quality gate at write time** refuses entries that are not instructional (a bare
status token like `Ralph rejected: ts_typecheck_fail` is a log line, not a guardrail).

**D4. `retrieval_count` must mean "this was chosen", not "the block was rendered".**

Today it means the latter, which is why it is uniformly 75 and why the GC is dead.
Split the counter: `render_count` for how often the block was emitted, `retrieval_count`
bumped only when an entry was selected **under pressure** (a cap actually bound and it
beat a competitor). Then `prune_stale`'s precondition becomes satisfiable.

**D5. Decay re-ranks; it does not delete. A salience floor protects pinned rules.**

Untouched entries are damped in ranking rather than removed. Anything the operator
pinned, and anything with high provenance, never ages out on time alone. Demotion
appends to an archive, which `prune_stale` already does correctly.

**D6. The index must know when anything it indexes changes.**

`mark_stale` has exactly two call sites, both note writes (`knowledge.py:1038`,
`:1079`). Chats, docs and tickets are all in `DEFAULT_KINDS` and **nothing marks them
stale**. The index was built 2026-08-15 23:47 and has been stale since 00:12 — 23 hours
— which also proves the backend has served no search in that window, because a search
is what kicks the rebuild. A chat you had today is not findable. This is the single
biggest functional gap between the memory system as designed and as experienced.

**D7. The vault stays out of the default corpus, deliberately.**

965 files of the operator's own thinking. ADR 0010 makes it read-only to agents; the
08-14 doc removed it from the design after looking inside. Leaving it out of
`DEFAULT_KINDS` is a decision, not an omission, and the search tool already documents
that it is reachable by name. **No change.** Recorded here so it stops being
rediscovered as a defect.

**D8. Letta is decommissioned, but its memory is exported first.**

No code has depended on Letta since the 2026-07-15 teardown, yet `:8283` is running
with **7 agents holding 23 memory blocks**, created 2026-05-20 to 2026-06-04, root-owned
since Jul 21. That is real operator-adjacent content (`masini`, `workshop-overseer`,
`email-triager`, `plaid-watcher`) that survived the teardown and that nothing reads.
Export the 23 blocks to `agent:<slug>` knowledge notes with provenance, **then** remove
the residue: the health probe (`routes/services.py:49-51`), the two env vars injected
on every restart (`restart_backend.sh:334-335`, `workshop-backend.service:43-44`), the
port reservation (`port_registry.py:58`), the `letta_agent_id` / `memory: letta` schema
fields, the unreachable `letta_tools/__init__.py` registry and `sandbox_wrappers.py`.

Stopping the root-owned process itself needs root and is the operator's call. Deleting
the platform's belief in it does not.

**D9. Delete what can never work rather than leaving it to be rediscovered.**

- `memory_tree.py` + `GET /agents/memory/tree-nodes` + `GET /agents/memory/effective`:
  zero clients; the docstring describes multi-selects that do not exist; already
  condemned in writing at `coherence-migration-plan-2026-07-18.md:722`.
- `memory_promotion`: `PROMOTE_AT = 3` requires a note held by 3+ narrow scopes.
  `.runtime/agents/` is empty and exactly one narrow scope has any notes. It runs on
  every inbox read and cannot produce a proposal.
- The learnings promotion lobe (`promotion_candidates`, `mark_promoted`,
  `_write_promoted`): `promoted.jsonl` has never existed.

**D10. `memory_keeper` becomes the maintenance pass it should always have been.**

Its watermark has not advanced in 29 days, nothing schedules it, and its output note
(`consolidated-memory.md`) does not exist anywhere in the tree — and would violate
ADR 0002 if it did, being exactly the write-only accumulator that ADR exists to
prevent. Repoint it: **the keeper's job is merge, supersede, prune and re-index**, not
consolidation-into-a-file. Then schedule it.

---

## 5. The correctness bugs found on the way

Each is independently real and gets fixed regardless of the above.

- **B1 — cross-owner knowledge leak (needs runtime confirmation).**
  `knowledge._shared_knowledge_root()` (`:62-82`) exists so each owner reads their own
  `workshop` layer, but `routes/notes_all.py:77` and `knowledge.py:1188`/`:1204`
  hardcode the tracked path. The Notes list and Store registry therefore appear to
  serve the operator's `shared/knowledge` to every owner. Confirm at runtime before
  changing behaviour.
- **B2 — backlinks lose note and ticket rows.** `knowledge.py:1048` and `tasks.py:1403`
  both write backlinks; `.runtime/backlinks.json` holds 7 rows, every one chat-sourced,
  against 110 notes and 133 tickets. Either the writes fail into the silent
  `except Exception: log.debug` at `knowledge.py:1052-1054`, or `set_source` replaces
  rather than accumulates.
- **B3 — the general persona contradicts the prompt it is in.**
  `chat_context.py:1404-1406` tells the model "nothing is injected into this chat for
  you any more, so what you have not looked up, you do not know", while
  `routes/chat.py:1471`, `:1482` and `:1500` unconditionally append LEARNINGS, ONGOING
  WORK and KNOWLEDGE to that same prompt. Only *auto-recall* was removed. The sentence
  over-claims and should say what is true.
- **B4 — `role:<r>` is injected and unlistable.** It is a valid scope, is rendered into
  every reviewer's prompt, and `routes/notes_all.py:_scope_roots` (`:53-98`) contains
  the string "role" zero times. `shared/knowledge/roles/reviewer/review-evidence.md`
  cannot be listed, edited or found in the Notes UI.
- **B5 — the false note.** Correct
  `deep-search-dies-with-the-gpu-no-lexical-fallbac-1a001d05335.md` against the code.

---

## 6. On `role:` being a scope at all

The ontology review (2026-08-17) argues `role:<r>` is a category error: a role is a
class, not an address, and every other part of the system treats a scope as a place.
That is correct, and the measurement supports it — the axis holds **one note**.

But renaming an axis is a migration, and the axis currently works. This plan therefore
**surfaces `role:` in the Notes UI (B4) and leaves the rename to the vocabulary
thread.** Making an injected layer visible is worth doing today; renaming it is worth
doing once, deliberately, with the rest of the vocabulary.

---

## 7. Sequence

Ordered so each step stands alone and nothing is left half-migrated.

| # | Step | Why here |
|---|---|---|
| **M1** | `mark_stale` on chat commit, ticket write and doc write; verify the index self-heals. | D6. The biggest functional gap, and it blocks nothing. |
| **M2** | Learnings quality gate + write-time reconciliation (ADD/UPDATE/SUPERSEDE/NOOP); purge the two `Ralph rejected:` rows, merge the duplicated restart rule, cap `provenance`, drop the `.bak` and the two smoke scopes from the store dir. | D2, D3. Fixes what is in the prompt today. |
| **M3** | Split `render_count` from `retrieval_count`; schedule `prune_stale` with a salience floor. | D4, D5. Only meaningful after M2. |
| **M4** | Delete `memory_tree`, `memory_promotion`, the learnings promotion lobe, and their routes. | D9. Pure deletion. |
| **M5** | Export the 23 Letta memory blocks to `agent:<slug>` notes; remove the Letta residue. | D8. Export must precede removal. |
| **M6** | Fix B1, B2, B3, B4, B5. | Correctness, independent. |
| **M7** | Repoint `memory_keeper` to merge/prune/re-index and schedule it. | D10. Needs M2/M3 to have something correct to run. |

---

## 8. How this is verified

Not "it builds". Each step names an observable:

- **M1**: write a chat message and a ticket, then assert `index.stale` is marked and a
  search finds the new content without a manual rebuild.
- **M2**: assert the two `Ralph rejected:` entries are gone, the restart guardrail
  appears exactly once, and a re-record of an edited existing rule supersedes rather
  than appends.
- **M3**: assert `prune_stale` can archive a genuinely stale low-provenance entry on a
  fixture, which it demonstrably cannot do today.
- **M4/M5**: assert the deleted routes 404 and the backend imports and serves.
- **M6**: assert each bug's specific symptom is gone.
- Full backend suite green, `npm run typecheck` and `bash scripts/check_design.sh`
  clean for any UI touched, then `bash scripts/apply.sh`.

---

## 9. Build log

Per R1: what landed, when, and in which commit. A step is listed only once it is
committed and independently verified, not when a worker reported it done.

| Step | Commit | Landed | Verified by |
|---|---|---|---|
| **M1** — `mark_stale` on chat, ticket and doc writes | `c9c99de` | 2026-08-16 | `mark_stale` now reached from `chat_log.py`, `tasks.py`, `docs_index.py` and `knowledge.py` (was 2 sites, both note writes). 50/50 `test_semantic_index.py` pass. Live: index rebuilt 4,600 to 4,760 chunks; a newly written chat and ticket were each found by exact marker at 0.93 and 0.90. |
| Acceptance harness | `c863ff7`, `565c45e` | 2026-08-16 | `scripts/verify_memory.py`. Caught its own green-but-hollow bug: `put_note` is a coroutine and was never awaited. |
| Occupancy tool (R2) | `7177146` | 2026-08-16 | `scripts/field_occupancy.py`. Reproduces the audit numbers independently: ticket `parent`/`latitude`/`awaiting`/`max_attempts` at 0 of 197; `assignee` and `owner` always the literal `operator`. |

**Known gap, not in the M1 spec:** `chat_log.delete_thread` does not mark the index
stale, so a deleted thread's chunks linger until some other write triggers a rebuild.
The rebuild re-enumerates live thread ids, so they drop then. Low harm, recorded rather
than fixed silently.
