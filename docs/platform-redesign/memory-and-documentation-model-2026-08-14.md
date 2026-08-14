# Memory and documentation: one tree, two directions (2026-08-14)

Status: proposed. Operator direction 2026-08-14, continuing the direction given
2026-08-12 ("draw down our complexity ... embedding on our notes and
documentation, and then the chats can query that with a tool call ... but it
should not be injected").

Companion to `notes-system-plan-2026-07-15.md` (which chose the substrate) and
ADR 0011 (which chose the context strategy). This doc does not replace either.
It fixes the thing neither settled: **who can see whose notes, and by which
mechanism.**

Every file:line and count below was measured on 2026-08-14, not assumed.

---

## 1. Executive summary

1. The substrate is already right and is not the problem. `services/knowledge.py`
   is a scoped markdown note store; `services/semantic_index.py` already embeds
   notes, `docs/`, past chats and the vault; `services/scope_version.py` already
   makes "did anything change" an integer compare. Do not build a new store.
2. The problem is that **scope is a flat list of strings with no containment**,
   so there is no answer to "everything below it". `read_layers()`
   (`knowledge.py:431`) hands out four FIXED layers (workshop, role, place,
   private). It flows one way only: a narrow chat reads the wide layers. There is
   no path by which a workshop chat reads a project's notes.
3. The resolution is one sentence: **you are governed by what contains you, and
   you can see into what you contain.** Those are two different mechanisms, not
   one. Containment flows *into your prompt* (injection, tiny, always on).
   Containment flows *out to your search* (retrieval, unbounded, on demand).
   Getting these backwards is the entire cost of the current design.
4. Sharing is one field with three values (`inherit` / `open` / `private`) on any
   scope, note, doc or chat, settable at any time, resolved narrowest-wins. Not a
   permission system: a single-operator curation control (see §7).
5. Linking is one resolvable ref (`workshop:note/...`, `workshop:ticket/...`,
   `workshop:chat/...#msg`) with one frontend resolver and one chip component.
   Backlinks fall out of indexing the refs. No file paths, no URLs to copy.
6. Retrieval must **degrade, not fail**. Measured today: `POST /api/embed` on the
   local Ollama returns HTTP 500 (`CUDA ... out of memory`), so `search` raises
   and the whole deep-query feature is down. The index on disk was last built
   2026-08-13 00:36. A retrieval layer with a hard GPU dependency and no fallback
   is not a memory system.
7. Delete before adding. Two monolithic accumulator notes
   (`shared/knowledge/chat-distilled.md`, `consolidated-memory.md`) are exactly
   the write-only-markdown failure ADR 0002 exists to prevent, they are injected
   by title into every chat, and they currently contain machine noise
   (`- (nothing)`, `- (no durable facts — these are in-flight bugs...)`).

---

## 2. What exists today (measured)

### Substrates

- **Knowledge notes** — `services/knowledge.py`, 780 lines. Scopes: `workshop`,
  `development`, `project:<slug>`, `app:<name>`, `agent:<slug>`, `role:<role>`.
  One dir per scope, `MEMORY.md` index plus `<id>.md` per note. Atomic writes,
  index cap at 200 lines (`enforce_index_cap:333`), per-owner root
  (`_shared_knowledge_root:62`). CRUD at `routes/knowledge.py`, cross-scope list
  at `routes/notes_all.py`. **20 workshop notes, 5 project knowledge dirs, 0
  agent knowledge dirs.**
- **Docs** — 163 markdown files under `docs/`, committed, read-only in the app
  (`routes/docs_index.py` exposes `GET ""` and `GET /file`; there is no PUT).
- **Chats** — 18 thread dirs under `.runtime/users/6/chat/`, append-only
  `log.jsonl` plus `meta.json`, mirrored to Postgres (ADR 0019, 0020).
- **Vault** — the operator's Obsidian vault at `~/notes`, **965 notes**, its own
  app at `/apps/notes`, its own store (`notes_vault.py`), read-only to agents by
  ADR 0010.
- **Learnings** — `services/learnings.py`, deduped corrections, separate concept,
  injected as guardrails. Correctly distinct; not merged here.

### Retrieval

`services/semantic_index.py` embeds four kinds (`note`, `doc`, `chat`, `vault`)
with `nomic-embed-text` via Ollama, cosine search, stale-marked on every note
write, rebuilt lazily on the next search. Exposed to models as the MCP tool
`mcp__workshop__search`. Roughly 3,800 chunks indexed.

Three defects:

- **No scope filter reaches the model.** `search()` accepts `scope=`
  (`semantic_index.py:556`) but `SEARCH_TOOL_SCHEMA`
  (`mcp/workshop_query_server.py:63`) exposes only `query`, `kinds`, `limit`. So
  retrieval cannot honour any visibility rule, because it cannot express one.
- **It fails closed and loudly.** `embed()` raises on any Ollama error; today
  that is a CUDA OOM, so every search 500s. There is no lexical fallback, even
  though `knowledge._plaintext_search:670` already exists and needs no GPU.
- **Freshness is opportunistic.** `rebuild_if_stale()` runs inside a search, so a
  corpus nobody searched is a corpus nobody indexed.

### Injection

`read_layers(scope, role, private)` returns `["workshop", "role:<r>", <place>,
"agent:<slug>"]`, deduped, and `layered_index_block` renders each labelled block
of titles and hooks into the prompt. That is the KNOWLEDGE block visible at the
top of every chat in this system.

It is correct as far as it goes, and it goes exactly one way.

---

## 3. Prior art, and what we take from it

**Scoped agent memory (mem0, and the 2026 surveys).** The converged production
pattern tags every write with identity scopes (user / session / agent / app) and
*composes them at retrieval time* rather than at write time. Confirms the split
this doc makes: write narrow and precise, resolve breadth when reading.
([mem0 state of agent memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026),
[Atlan, agent memory architectures](https://atlan.com/know/agent-memory-architectures/))

**Write policy and consolidation.** The cheapest place to control memory quality
is at write time; everything admitted must be retrieved, reranked and judged
forever. Four levers: importance, merge, decay, eviction. We take importance and
merge; we defer decay and eviction (§6) because at 20 notes they are premature.
([Hindsight, the consolidation problem](https://hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation),
[Memory for autonomous LLM agents](https://arxiv.org/html/2603.07670v1))

**Notion vs Obsidian.** The relevant finding is not which is better, it is that
project work fits a tree and research work fits a graph, and forcing one onto
the other is what produces either deep dead folders or an unnavigable web. We
take both, assigned deliberately: **the tree is for visibility, the graph is for
meaning.** Containment decides who can see a note; links decide what it relates
to. Neither does the other's job.
([Notion vs Obsidian 2026](https://www.atlasworkspace.ai/blog/notion-vs-obsidian),
[Obsidian internal links and backlinks](https://deepwiki.com/victor-software-house/obsidian-help/3.1-internal-links-and-backlinks))

**Zanzibar / ReBAC.** Permission inheritance down an object hierarchy, computed
from relationship tuples plus rewrite rules, rather than stored per object.
We take the *shape* (one parent edge, inheritance computed not stored) and
explicitly reject the machinery: this is one operator, and per SEC-A2 nothing at
this layer is a security boundary.
([Zanzibar intro](https://authzed.com/learn/google-zanzibar))

**Deep linking.** Apps with complex navigation resolve an internal URI scheme to
a route; because the link never leaves the app, the fallback and security
problems of public URI schemes do not apply. That is precisely our case.
([Branch, deep linking](https://www.branch.io/resources/blog/deep-linking-from-url-and-uri-schemes-to-universal-links-app-links-and-beyond/))

---

## 4. The model

### 4.1 One tree, one parent edge

Every scope gets exactly one parent. The tree is shallow by rule (root, context,
sub-context) and the parent is a stored field, not parsed out of the scope
string. Storing it is what makes it dynamic: an app can be moved under a project
later without inventing path syntax, which is also the missing bridge the
`apps-and-projects-have-no-bridge` note recorded.

```
workshop                        the root; house style, preferences, cross-cutting facts
├── development                 the Workshop's own source (ADR 0014)
├── project:<slug>
│   ├── app:<name>              when an app belongs to a project
│   └── agent:<slug>            when an agent is bound to a project
├── app:<name>                  unattached
└── agent:<slug>                unattached
```

`role:<role>` is **not** a node. It is the second axis the store already
documents ("TWO AXES, NOT ONE", `knowledge.py:9-19`): two reviewers in different
projects share a role and not a place. It stays a facet resolved alongside the
tree, exactly as `read_layers` does today. Do not fold it into the tree.

Chats, tickets, runs and notes are not scopes. They are *in* a scope.

### 4.2 Two directions, two mechanisms

> You are governed by what contains you. You can see into what you contain.

**Injection — ancestors → you. Always on, tiny, cache-stable.**
`injected_scopes(S) = ancestors(S) + S + role facet`. Titles and hooks only,
never bodies, capped. This is the existing `read_layers`, generalised from four
hardcoded layers to a walk up the parent edge. A project chat still gets house
style and design-system rules, because those contain it.

**Retrieval — you → descendants. On demand, unbounded, never injected.**
`visible_scopes(S) = S + descendants(S) + open items anywhere`. This is the
direction that does not exist today and is what the operator asked for: a
workshop-level chat can search into every project; a project chat cannot search
its sibling project. Siblings are excluded in both directions, which is the whole
point of having a tree.

The asymmetry is deliberate and it is what keeps the prompt small: the set that
flows *into* the prompt is bounded by tree depth (three), and the set that flows
*out* to search is unbounded but costs nothing until asked.

### 4.3 Where it lands in code

Two functions, and everything else consumes them:

```python
# services/scope_tree.py  (new, small)
def injected_scopes(scope: str, role: str | None = None) -> list[str]: ...
def visible_scopes(scope: str) -> list[str]: ...
```

`knowledge.read_layers` becomes a thin caller of `injected_scopes`.
`knowledge.recall` and `semantic_index.search` take the visible set instead of a
single scope. `_can_recall:695` (which today denies all cross-scope recall
because `permissions.can_recall` does not exist) is replaced by a set membership
test against `visible_scopes`.

---

## 5. Sharing policy

One field, `share`, on any scope, note, doc or chat. Three values:

- `inherit` — the default; follow §4.2.
- `open` — visible from any scope, as if it sat at the root.
- `private` — visible only inside its own scope; ancestors cannot see it.

Resolution is narrowest-wins: an explicit value on the item beats the value on
its scope, which beats the global default. Settable at any time from the item,
from the scope's settings, or globally; changing it changes visibility
immediately because visibility is computed, never stored per item.

Recommended defaults, and the reasoning:

- notes and docs: `inherit`. They are written to be read.
- **chat transcripts: `private`.** A transcript is thinking-in-progress, it is
  the largest corpus by volume, and it is the one an ancestor chat will most
  often surface something half-finished from. The notes a chat *files* are
  `inherit` and are the intended path; the raw transcript is the fallback the
  operator described as "not a common or primary way of accessing things".
  Flipping one chat to `open` is one click. **This is the one default worth
  disagreeing with; it is called out as a decision in §9.**
- agent scopes: `inherit`. An agent's running notes should be visible to the
  project that contains it; that is the point of promotion.

**This is curation, not confinement.** Everything runs as one uid; per SEC-A2 a
`share` field is advisory, exactly like `touches` and `latitude`. It controls
noise and relevance, and it must never be described in the UI as security.

---

## 6. What goes in memory

The operator has asked for two things that only look opposed: "note capture
should default to verbose with importance classification, not minimal or
dropped" (2026-07) and "stop automatically writing all this stuff all the time"
(2026-08-12). Both are right, because they are about different stages.

**Write generously. Inject almost nothing. Retrieve on demand.**

Rules:

1. **One fact, one note.** No monolithic accumulators. Every write is its own
   file with its own id, so it can be superseded, corrected, promoted or deleted
   independently. `chat-distilled.md` and `consolidated-memory.md` violate this
   and are retired in Phase 0.
2. **Every note carries provenance**: author (already at
   `knowledge.split_author:546`), originating chat ref, turn, timestamp. The
   operator asked for this explicitly on 2026-08-12: "some retention of the chat
   that it came from or the time and date ... so that at least that is
   reconstructable later."
3. **Every note carries a type**: `decision` / `fact` / `reference` /
   `preference`. Type drives display, not access. A `decision` that will outlive
   the chat still belongs in `docs/decisions/` as an ADR; the note is its pointer.
4. **Merge on write, do not accumulate.** Same id means update in place, with the
   old body kept in git for committed scopes.
5. **The injected set is titles and hooks only, capped, and it shrinks.** Bodies
   arrive through retrieval. This is ADR 0011 restated and it is the rule the
   accumulators broke.
6. **Decay and eviction are deferred.** At 20 workshop notes they are theory.
   Revisit at a measured threshold, not on a schedule.

---

## 7. Links and backlinks

One ref format, resolvable in-app:

```
workshop:note/<scope>/<id>
workshop:doc/<repo-relative-path>
workshop:ticket/<id>
workshop:chat/<thread-id>#<message-id>
workshop:run/<id>
workshop:project/<slug>       workshop:agent/<slug>       workshop:app/<name>
```

- One backend resolver returns `{kind, title, href, status}` for a ref.
- One frontend `<Ref>` chip renders it: lucide icon, title, click navigates to
  the route. It is a link, and it looks like one on a touch screen with no hover
  (this is the same defect already ticketed against `.chat-receipt--link`).
- Refs are written by agents in note bodies, ticket bodies and chat receipts, and
  by the operator in the editor. The vault's existing `[[wikilink]]` syntax
  (`frontend/src/pages/apps/notes/wikilinks.ts`) resolves to a note ref, so the
  two link languages converge on one resolver.
- **Backlinks fall out**: index every ref at write time, and every entity page
  shows "referenced by". This is the graph half of §3, and it is what makes a
  note discoverable without titling it perfectly.

Nothing renders a raw path or a copyable URL to the operator. That is the
acceptance test for this section.

---

## 8. Retrieval

One tool, `search`, replacing the current pair:

- Parameters: `query`, `kinds` (`note` / `doc` / `chat` / `ticket` / `vault`),
  `scope` (defaults to the caller's visible set), `limit`.
- **Hybrid, so it degrades.** Vector search when the embedder answers; lexical
  (the existing `_plaintext_search`, plus BM25 over the same chunks) always. When
  the GPU is busy the tool returns lexical hits and says so, instead of raising a
  500 as it does right now.
- Every hit carries a `ref` (§7), a scope, a timestamp and the passage body.
  Bodies, not titles: being unable to read what was found is the defect the
  module was written to fix, and that property must survive this change.
- Scope filtering is applied to the *candidate set*, not post-hoc, so a denied
  scope costs nothing.
- Index freshness moves off "whoever searches next pays for it" onto the write
  path's existing stale marker plus a small debounced background rebuild.

`kinds: ["ticket"]` is how documentation and tickets integrate without merging:
separate stores, separate lifecycles, one query surface, one ref format.

---

## 9. Surfaces

**One library page** at `/memory` (already exists, 1,194 lines, correct design
language). It gains:

- a source badge per row: Note / Doc / Chat / Vault, with a filter;
- a scope filter that reflects the tree, not a flat list;
- an editor that can write **docs** as well as notes (add `PUT /api/documents`;
  today the route is read-only), starting as a plain markdown textarea;
- a per-item `share` control (`inherit` / `open` / `private`);
- provenance on every item: who wrote it, from which chat, when, as a `<Ref>`.

**The vault stays a separate store and gains a shared surface.** ADR 0010 keeps
`~/notes` read-only to agents and separate from the knowledge substrate; that
decision holds and the 965 notes do not move. What changes is that the library
page shows them with a Vault badge, so the operator has one place to look. This
is the honest reading of "if we have multiple places doing the same thing we
probably want to consolidate": consolidate the *surface*, not the storage.

**Settings** gets one memory section: the global default `share`, per-scope
overrides, and which kinds the search tool reaches by default.

---

## 10. Sequence

**Phase 0 — delete first.** Retire `chat-distilled` and `consolidated-memory` as
accumulators (keep the file contents in git history; migrate anything still true
into individual notes with provenance). Stop the auto-writers that append
`(nothing)` lines. Confirm no injected block carries a body. No new features in
this phase; this is the "draw down complexity" the operator asked for on
2026-08-12, and doing it first is what stops the new model inheriting the old
mess.

**Phase 1 — the tree.** `services/scope_tree.py` with `injected_scopes` and
`visible_scopes`, a stored parent edge on scope records, `read_layers`
reimplemented on top of it, `_can_recall` replaced. Backend only, no UI. This is
the load-bearing phase; everything after it is a consumer.

**Phase 2 — retrieval that works.** Hybrid search with lexical fallback, scope
filtering wired to `visible_scopes`, one merged tool, refs in every hit,
background rebuild. Fixes the currently-broken deep query.

**Phase 3 — refs and backlinks.** Ref format, backend resolver, `<Ref>` chip,
backlink index, wikilink convergence, receipt restyling.

**Phase 4 — the library surface.** Source badges, tree-shaped scope filter, doc
editing, provenance display.

**Phase 5 — the share field.** Global default, per-scope, per-item, and the
settings section. Last, deliberately: it is the only part that is pure policy,
and policy written before the mechanism is policy written against a guess.

---

## 11. Decisions the operator owns

1. **Chat transcript default: `private` or `inherit`?** Recommendation
   `private`, per §5. It is the largest corpus and the least finished writing.
2. **Vault: shared surface only (recommended), or actually merge the 965 notes
   into the knowledge store?** Merging contradicts ADR 0010 and would need it
   superseded.
3. **Does `development` sit under `workshop`, or beside it as a second root?**
   Recommendation: under it, so a development chat inherits house style; ADR 0014
   makes it conditional on developer mode either way.

## 12. Explicitly not now

- Decay, eviction, and confidence scoring on notes (§6.6): premature at this
  corpus size.
- A graph database or a knowledge graph layer: the refs index is a table; the
  graph is a view of it.
- Any per-user permission model: single operator, and §5 is curation.
- Automatic chat distillation: retired in Phase 0 and NOT rebuilt in this plan.
  It returns, if it returns, on top of §6's per-fact provenance model, which is
  the "proper design" the operator deferred it to.
