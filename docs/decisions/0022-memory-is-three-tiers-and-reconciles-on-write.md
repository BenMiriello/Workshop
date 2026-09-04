# 0022. Memory is three tiers we already have, and it reconciles on write

- Status: accepted
- Date: 2026-08-16
- Depends on: ADR 0002 (the knowledge store is the notes substrate), ADR 0010
  (the vault is read-only to agents), ADR 0011 (the model manages the
  conversation; we manage what enters it)
- Supersedes: nothing. ADR 0002's substrate choice and ADR 0011's context
  strategy are both upheld and this record sits underneath them.
- Plan: `docs/platform-redesign/memory-consolidation-2026-08-16.md`

## Context

An audit on 2026-08-16 found **eleven** durable-text memory substrates, four of
them dead or vestigial. ADR 0002 consolidated six substrates in July and the
count went up, which is the fact that motivates this record: a consolidation
that names a winner but installs no rule about what may be added is not a
consolidation, it is a snapshot.

Three measurements set the terms.

**Nothing removes anything.** `learnings.memory_block()` renders a whole scope
and stamps `last_retrieved` on every entry it emits. `learnings.prune_stale()`
keeps any entry that is `recent OR reinforced OR retrieved`. So the injector
falsifies the collector's precondition and **the garbage collector can never
archive a row**. It also has no caller outside its own CLI. The observable
result is that `Ralph rejected: ts_typecheck_fail` — 33 characters of machine
status, written by an autonomous loop on 2026-07-15 — has been injected into
every agent prompt for a month.

**Nothing reconciles a write against what is already stored.** The same store
holds two copies of one guardrail, created a month apart, because the rule was
edited and dedupe is a hash of exact text. Both are live and both are injected.

**Elaborate memory machinery is expensive and frequently buys nothing.** The
2026 survey work names a *Context Saturation Gap*: most memory benchmarks now
fit inside a 128k window. It also measures an *Agency Tax* on the systems that
would tempt us — MemoryOS at 32.4 s per turn, AMem at 15 h of offline
construction, Nemori at 7.04 M tokens to index. Workshop's whole corpus is 4,600
chunks and a query returns in under a second.

## Decision

**Memory is three tiers, all of which already exist. We do not add a fourth
substrate, and we do not build a knowledge graph.**

| Tier | What it is | Where it lives | Mutability |
|---|---|---|---|
| **Episode** | what happened: messages, tickets, runs, docs | chat log + Postgres + the ticket store | append-only |
| **Retrieval** | how you find an episode | `semantic_index` | derived, rebuildable, disposable |
| **Context block** | small curated text always in the prompt | knowledge notes + learnings | curated, superseding |

Four rules follow, and they are the whole of this decision.

**1. Retrieval is pulled, never pushed; governance is pushed, and is small.**
The distinction from `memory-and-documentation-model-2026-08-14.md` is adopted:
*reach* is the scope tree, on demand and unbounded; *governance* is one bounded
cache-stable layer injected every turn. Conflating them makes a memory system
either leak everything or reach nothing.

**2. The index must learn about everything it indexes.** If a kind is in
`DEFAULT_KINDS`, a write of that kind marks the index stale. Anything else means
a chat you had today is not findable, which was literally true until this ADR.

**3. A write reconciles against what is already there.** Every candidate
learning is resolved against semantically similar existing entries as **ADD /
UPDATE / SUPERSEDE / NOOP**. A revision supersedes; it does not sit beside the
thing it revises. A write that carries no instruction — a bare status token — is
refused at the door. The cheapest place to control memory quality is the write.

**4. Forgetting is real, and it is re-ranking plus a floor, not deletion.**
Untouched entries are damped in ranking. Demotion archives rather than deletes.
A **salience floor** protects operator-authored and well-reinforced entries so a
durable guardrail never ages out on time alone. And any counter that a collector
depends on must measure *selection*, not *rendering* — otherwise the collector
is decorative, which is exactly what happened here.

## Consequences

The substrate count goes from eleven to four the platform owns (notes,
learnings, index, episodes) plus two it reads and does not own (the `$WORKSHOP_NOTES_VAULT`
vault, `docs/`). `memory_tree`, `memory_promotion` and the learnings promotion
lobe are deleted rather than left to be rediscovered. Letta's 23 orphaned memory
blocks are exported to notes before its wiring is removed, so decommissioning
does not destroy content.

The vault stays out of the default retrieval corpus. That is a decision under
ADR 0010, not an omission, and it is written down here so it stops being
re-filed as a bug.

We accept that retrieval quality is capped by embeddings plus BM25 and that a
temporal knowledge graph would raise that ceiling. We are declining the ceiling
in exchange for not paying the Agency Tax on a 4,600-chunk corpus. Revisit if
the corpus grows by an order of magnitude or if retrieval is measurably the
thing failing, and revisit with a measurement rather than an intuition.

The risk we are taking on is that write-time reconciliation can merge two things
that only looked alike. The mitigation is to bias toward ADD over a wrong merge,
to archive rather than delete on supersession so any bad call stays recoverable,
and to keep the similarity threshold a tunable constant rather than a judgement
buried in code.
