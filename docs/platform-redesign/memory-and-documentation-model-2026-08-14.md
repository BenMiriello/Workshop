# Memory and documentation: reach, governance, and one ref (2026-08-14)

Status: **accepted, ready to build.** Operator direction 2026-08-14, continuing
2026-08-12 ("draw down our complexity ... embedding on our notes and
documentation, and then the chats can query that with a tool call ... but it
should not be injected").

Companion to `notes-system-plan-2026-07-15.md` (chose the substrate) and ADR 0011
(chose the context strategy). Neither is replaced. This doc settles what neither
did: **who can see whose notes, by which mechanism, and what the operator can
change about it.**

Revision note: the first draft of this doc put `development` underneath
`workshop`, which was wrong and is corrected in §4.1. It also designed around the
`~/notes` vault without looking inside it; §9 records what is actually there.
Every file:line, count and timing below was measured on 2026-08-14.

---

## 1. Executive summary

1. The substrate is already right. `services/knowledge.py` is a scoped markdown
   note store; `semantic_index.py` already embeds notes, `docs/` and past chats;
   `scope_version.py` already makes "did anything change" an integer compare. Do
   not build a new store.
2. **Scope is a flat list of strings with no containment**, so "everything below
   it" has no answer. That is the whole defect.
3. **Two mechanisms, deliberately separate, and this is the core of the design:**
   - **Reach** is the tree. You can see into your own scope and everything below
     it. Never siblings, never upward. This is retrieval, on demand, unbounded.
   - **Governance** is not the tree. It is one small named layer (house style,
     operator preferences) plus your own scope, injected into every prompt.
     Bounded and cache-stable regardless of how deep the tree gets.
   Conflating these is what makes memory systems either leak everything or reach
   nothing.
4. **`development` is the root of the tree, above `workshop`.** It is the
   developer's environment and reaches everything. `workshop` is the root of a
   normal install and cannot see into `development`. In a non-developer build
   `development` does not exist at all, so `workshop` is the root.
5. Sharing is one field, `share`, with three values (`inherit` / `open` /
   `private`), on any scope or item, resolved narrowest-wins, changeable at any
   time. Chat transcripts default to `private`, with a global default setting and
   a per-chat override. Curation, not security (§6).
6. One ref format (`workshop:note/...`, `workshop:ticket/...`,
   `workshop:chat/<id>#<msg>`), one resolver, one chip. Backlinks fall out of
   indexing the refs. Nothing ever shows a file path or a URL to copy.
7. **Retrieval must degrade, not fail.** Measured: the embedder 500s on a CUDA
   OOM, and `search` has no non-vector path, so deep query is entirely down right
   now. Also measured: the same model on CPU does 121 ms/chunk, a full 3,800-chunk
   rebuild in ~8 min, a query embed in ~0.1 s. **The GPU dependency is
   accidental; pin the embedder to CPU and add a lexical path.**
8. **Delete first.** The only note in the entire `development` knowledge scope is
   `chat-distilled.md`, an auto-appended accumulator containing lines like
   `- (nothing)`. It is injected by title into every development chat. It and
   `consolidated-memory.md` are exactly the write-only-markdown failure ADR 0002
   exists to prevent.
9. **Structural prerequisite:** scope is parsed independently in four places
   (`knowledge._parse_scope`, `task_scope.root_for_context`/`key_for_root`,
   `chatScope.ts`, `lib/tasks.ts`). ADR 0014 flagged this and deferred a canonical
   scope module as "a migration, not a rename". The tree is exactly the thing
   that needs one home, so this plan does that migration (§7).

---

## 2. What exists today (measured)

### Substrates

- **Knowledge notes** — `services/knowledge.py`, 780 lines. Scopes `workshop`,
  `development`, `project:<slug>`, `app:<name>`, `agent:<slug>`, `role:<role>`;
  one dir each, `MEMORY.md` index plus `<id>.md` per note; atomic writes; index
  cap 200 lines (`enforce_index_cap:333`); per-owner root
  (`_shared_knowledge_root:62`). CRUD `routes/knowledge.py`, cross-scope list
  `routes/notes_all.py`. **20 workshop notes, 1 development note, 5 project
  knowledge dirs, 0 agent knowledge dirs.**
- **Docs** — 163 committed markdown files under `docs/`. Read-only in the app:
  `routes/docs_index.py` exposes `GET ""` and `GET /file` and there is no PUT.
- **Chats** — 18 thread dirs under `.runtime/users/6/chat/`, append-only
  `log.jsonl` plus `meta.json`, mirrored to Postgres (ADR 0019, 0020).
- **Learnings** — `services/learnings.py`, deduped corrections injected as
  guardrails. A distinct concept, correctly separate, not merged here.
- **Vault** — see §9. Not a live substrate; removed from this design.

### Retrieval

`services/semantic_index.py` embeds `note` / `doc` / `chat` / `vault` with
`nomic-embed-text` through Ollama, cosine search, stale-marked on every note
write, rebuilt lazily inside the next search. Exposed as `mcp__workshop__search`.
About 3,800 chunks; `MIN_SCORE = 0.58`, `CHUNK_CHARS = 1200`.

Four defects, all load-bearing:

- **No scope filter reaches the model.** `search()` accepts `scope=`
  (`semantic_index.py:556`) but `SEARCH_TOOL_SCHEMA`
  (`mcp/workshop_query_server.py:63`) exposes only `query`, `kinds`, `limit`. So
  retrieval cannot honour a visibility rule because it cannot express one.
- **It fails closed and loudly.** `embed()` raises on any Ollama error. Today
  that is `CUDA error: out of memory`, so every search 500s, and the entire
  feature is down whenever another workload holds the GPU.
- **Freshness is opportunistic.** `rebuild_if_stale()` runs *inside* a search, so
  a corpus nobody searched is a corpus nobody indexed. On CPU that also means a
  user-facing request can block for ~8 minutes.
- **A scope filter would be applied by fancy-indexing** (`vecs[keep] @ q`), which
  copies the candidate submatrix on every call. See §8 for the fix.

### Injection

`read_layers(scope, role, private)` returns four FIXED layers: `workshop`,
`role:<r>`, the place scope, the private agent scope. `layered_index_block`
renders each as a labelled block of titles and hooks. That is the KNOWLEDGE block
at the top of every chat.

It is small, cache-stable and correct in shape. Its only defect is that the
"place" layer is a single flat string with nothing below it.

---

## 3. Prior art, and what we take

**Scoped agent memory (mem0 and the 2026 surveys).** The converged production
pattern tags each write with identity scopes and *composes them at retrieval
time* rather than at write time. Confirms the split here: write narrow and
precise, resolve breadth when reading.
([mem0 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026),
[Atlan](https://atlan.com/know/agent-memory-architectures/))

**Write policy and consolidation.** The cheapest place to control memory quality
is at write time; everything admitted must be retrieved, reranked and judged
forever. Four levers: importance, merge, decay, eviction. We take importance and
merge; decay and eviction are premature at 21 notes (§5.6).
([Hindsight](https://hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation),
[survey](https://arxiv.org/html/2603.07670v1))

**Notion vs Obsidian.** The useful finding is not which wins: it is that project
work fits a tree and research work fits a graph, and forcing one onto the other
produces either dead folders or an unnavigable web. We take both, assigned
deliberately: **the tree decides reach, the graph decides meaning.** Containment
says who may see a note; links say what it relates to. Neither does the other's
job.
([comparison](https://www.atlasworkspace.ai/blog/notion-vs-obsidian),
[backlinks](https://deepwiki.com/victor-software-house/obsidian-help/3.1-internal-links-and-backlinks))

**Zanzibar / ReBAC.** Inheritance computed from one parent edge plus rewrite
rules, rather than stored per object. We take the shape (one parent edge,
inheritance computed not stored) and reject the machinery: one operator, and per
SEC-A2 nothing at this layer is a security boundary.
([Zanzibar](https://authzed.com/learn/google-zanzibar))

**Deep linking.** Apps with complex navigation resolve an internal URI scheme to
a route; because the link never leaves the app, the fallback and security
problems of public schemes do not apply. Exactly our case.
([Branch](https://www.branch.io/resources/blog/deep-linking-from-url-and-uri-schemes-to-universal-links-app-links-and-beyond/))

---

## 4. The model

### 4.1 The tree

Every scope has exactly one parent, stored as a field rather than parsed out of
the scope string. Storing it is what makes it dynamic: an app can be moved under
a project later without inventing path syntax, which is also the missing bridge
the `apps-and-projects-have-no-bridge` note recorded.

```
development                  ROOT on a developer build. The developer's
│                            environment: the platform's own source, the box,
│                            the installation. Reaches everything.
└── workshop                 root of a normal install. House style, operator
    │                        preferences, cross-cutting facts. Cannot see into
    │                        development.
    ├── project:<slug>
    │   ├── app:<name>       when the app belongs to that project
    │   └── agent:<slug>     when the agent is bound to that project
    ├── app:<name>           unattached
    └── agent:<slug>         unattached
```

**Why `development` is the root and not a child.** ADR 0014 established that
`workshop` had been carrying two unrelated meanings, and split them: `workshop`
keeps its notes, its queue, its chats and its universal-parent role for a normal
install; `development` is the name for work targeting the platform's own source
and the installation itself, and exists only when `WORKSHOP_DEV_MODE=1`. Those
are different *kinds* of authority, and `development` is strictly the larger one:
it is the only scope whose work target is the running application, its tickets
are the host and infra tickets, and it is present for exactly one person. Placing
it under `workshop` would have said a development chat is contained by, and
therefore narrower than, the ordinary root. It is the reverse.

In a build without developer mode, `development` does not exist as a scope at
all, and `workshop` is the root. Absent code needs no permission system; that is
ADR 0014's own reasoning and it carries over unchanged.

`role:<role>` is **not** a node. It is the second axis the store already
documents ("TWO AXES, NOT ONE", `knowledge.py:9-19`): two reviewers in different
projects share a role and not a place. It stays a facet resolved alongside the
tree. Do not fold it in.

Chats, tickets, runs and notes are not scopes. They are *in* a scope.

### 4.2 Reach: the tree, downward only

```
reach(S) = {S} ∪ descendants(S) ∪ {items explicitly marked `open`}
```

You can see into your own scope and everything below it. Never siblings. Never
upward. This is the operator's stated model verbatim, and it is used for exactly
one thing: **retrieval**. On demand, unbounded, never injected.

Consequences, which are the point:

- A `development` chat reaches every scope. That is what the most authoritative
  scope means, and it is now a property of the tree rather than a special case.
- A `workshop` chat reaches projects, apps and agents, but **not** `development`.
- A `project:a` chat cannot reach `project:b`. Siblings are opaque in both
  directions, which is the whole reason to have a tree rather than a list.
- A chat can always reach its own scope, which is the common case and costs
  nothing extra.

### 4.3 Governance: one small named layer, not the ancestor chain

```
govern(S) = {house} ∪ {role:<r>} ∪ {S} ∪ {private agent scope}
```

where `house` is the designated governance scope, `workshop` by default.

Injected into every prompt as titles and hooks only, never bodies, capped.

**Why this is not "every ancestor".** Two reasons, and they are worth stating
because "what contains me governs me" is the obvious design and it is wrong here:

1. On a developer build, `development` is an ancestor of `workshop`. Ancestor
   injection would put platform working notes into a birds chat and a finance
   chat. Governance is house style and operator preferences; it is not everything
   written by anyone more authoritative than you.
2. Governance injected from a chain grows with tree depth, and the injected block
   must stay bounded and cache-stable no matter how the tree is reshaped. A named
   layer is O(1) by construction.

So the tree has exactly one job (reach) and governance has exactly one home. This
is what `read_layers` already does; what changes is that `house` becomes a named,
configurable thing rather than the hardcoded string `workshop`, and that the
place layer is now a real node in a tree for reach purposes.

A future `operator` scope is the cleaner long-term home for preferences, and is
noted rather than built (§12).

---

## 5. What goes in memory

Two operator statements that only look opposed: "note capture should default to
verbose with importance classification, not minimal or dropped" (2026-07), and
"stop automatically writing all this stuff all the time" (2026-08-12). Both are
right; they are about different stages.

**Write generously. Inject almost nothing. Retrieve on demand.**

1. **One fact, one note.** No monolithic accumulators. Each write is its own file
   with its own id, so it can be superseded, corrected, promoted or deleted
   independently. `chat-distilled.md` and `consolidated-memory.md` violate this
   and are migrated in Phase 0.

   Measured before writing this rule: the three accumulators hold **98 lines, of
   which 3 are noise** (`- (nothing)`, `- (no durable facts)`). The rest is
   genuinely useful. So this is a migration, not a deletion, and the earlier
   framing of these files as pure noise was wrong.

   The real harm they do is retrieval, not injection: a 1,200-character chunk of
   unrelated one-liners embeds to a meaningless centroid, so semantic search over
   them returns the same blob for every query and none of it can be superseded,
   attributed or corrected in place.
2. **Every note carries provenance**: author (already at `split_author:546`),
   originating chat ref, turn, timestamp. Requested explicitly on 2026-08-12:
   "some retention of the chat that it came from or the time and date ... so that
   at least that is reconstructable later."
3. **Every note carries a type**: `decision` / `fact` / `reference` /
   `preference`. Type drives display, never access. A decision that will outlive
   the chat still belongs in `docs/decisions/` as an ADR; the note points at it.
4. **Merge on write.** Same id means update in place; history lives in git for
   committed scopes.
5. **The injected index is a ranked digest, not a listing.** This corrects a
   scaling defect in the current store and a contradiction in the first draft of
   this doc.

   `index_block` (`knowledge.py:501`) lists **every** note in the scope, so the
   injected block grows linearly with the corpus and `enforce_index_cap(200)` is
   a band-aid on that growth rather than a design. Splitting the 98 accumulated
   lines into 98 notes under that rule would make the prompt strictly worse than
   the accumulator it replaced, which is the trap this rule exists to avoid.

   So the ambient block becomes a capped, ranked digest: **pinned notes first,
   then recency, then type** (`preference` and `decision` outrank `fact` and
   `reference`), to a token budget rather than a line count. Everything past the
   cap is reachable by search and by search only.

   **The block must state what it omitted** ("42 more notes in this scope; search
   to reach them"). A truncated list that presents itself as complete is worse
   than no list, because the model stops looking.

   Bodies never appear in the ambient block at all. ADR 0011 restated.
6. **Decay and eviction are deferred.** At 21 notes they are theory. Revisit at a
   measured threshold, not on a schedule.

---

## 6. Sharing policy

One field, `share`, on any scope or item. Three values:

- `inherit` — the default; follow §4.2.
- `open` — reachable from any scope, as if it sat at the root.
- `private` — reachable only from inside its own scope; ancestors cannot see it.

Resolution is narrowest-wins: an explicit value on the item beats the value on
its scope, which beats the global default. Visibility is computed, never stored
per item, so a change takes effect immediately.

Defaults, **as directed by the operator on 2026-08-14**:

- notes and docs: `inherit`. They are written to be read.
- **chat transcripts: `private`, as a global default that is itself a setting,
  with a per-chat override.** So: one global control ("new chats are
  private/inherit"), and one control on each chat. A transcript is
  thinking-in-progress and the largest corpus by volume; the notes a chat *files*
  are the intended path, and the raw transcript is the fallback the operator
  described as "not a common or primary way of accessing things".
- agent scopes: `inherit`, so an agent's running notes are reachable by the
  project that contains it. That is what makes promotion (`memory_promotion.py`)
  meaningful.

**This is curation, not confinement.** Everything runs as one uid; per SEC-A2 a
`share` field is advisory in exactly the way `touches` and `latitude` are. It
controls noise and relevance. It must never be described in the UI as security,
and the copy is written at the control itself so a later audit does not have to
unwind it.

### Storage

One file, `.runtime/memory-policy.json`, loaded once and cached against an
mtime check:

```json
{
  "version": 1,
  "defaults": { "note": "inherit", "doc": "inherit", "chat": "private" },
  "scopes":   { "project:foo": "private" },
  "items":    { "workshop:chat/abc123": "open" }
}
```

Deliberately not a field inside each note file: the note format is a plain
markdown body whose first line is the title, and threading a policy field through
it would mean a parser change, a migration, and a second source of truth against
`MEMORY.md`. One keyed file is smaller, atomic, and trivially diffable.

---

## 7. Structure and code quality

The plan is as much a consolidation as a feature. Four things are load-bearing.

### 7.1 One canonical scope module (the prerequisite)

Scope is currently parsed in several independent implementations. ADR 0014 named
this and deferred the fix. Adding a tree to a string parsed in that many places
would pay the tax again and guarantee they drift.

**Correction to ADR 0014's list, found while building Phase 1.** That record cites
`frontend/src/lib/chatScope.ts`, which does not exist. The real sites are
`knowledge._parse_scope`, `task_scope.root_for_context` / `key_for_root`,
`frontend/src/lib/devMode.ts`, `frontend/src/lib/tasks.ts`,
`frontend/src/lib/chatSelection.ts`, and `frontend/src/components/chat/chatScope.ts`
(a different file at a different path, keyed on `context_key` rather than
`scope_key`).

**Two namespaces, not one, and this is the reason the migration is partial.**
`scope_key` (`workshop`, `project:<slug>`, `app:<name>`, `agent:<slug>`,
`role:<role>`) is what the tree models. `context_key` is a superset that also
carries `general` and `build:<slug>` and maps *into* scope keys via
`knowledge.scope_for_context`. `task_scope.root_for_context` and
`components/chat/chatScope.ts` operate on `context_key` and are correctly NOT
folded into `scope_tree`. `task_scope.key_for_root` is also left alone: its own
docstring records that the platform repo must map back to `development` and never
to `workshop`, and that getting it wrong re-keys every ticket in the root queue on
the next write. Unifying those is a further migration and is not this phase.

**Phase 1 introduces `backend/services/scope_tree.py` as the single owner** of:
parsing a scope key, the parent edge, `reach()`, `govern()`, labels, and the
valid-kinds enum. `knowledge`, `task_scope`, `semantic_index` and the routes
become callers. The frontend gets one matching `frontend/src/lib/scope.ts` and
`chatScope.ts`/`tasks.ts` call it. No behaviour change in that phase beyond the
tree itself; it is a migration with tests, which is why it ships alone.

Public surface, small on purpose:

```python
def parse(scope_key: str) -> Scope            # kind, slug, valid or raises
def parent(scope_key: str) -> str | None
def children(scope_key: str) -> list[str]
def reach(scope_key: str) -> list[str]        # self + descendants, ordered
def govern(scope_key, role=None, private=None) -> list[str]
def label(scope_key: str) -> str
```

`knowledge.read_layers` becomes a one-line call to `govern`. `knowledge._can_recall`
(which today denies all cross-scope recall because `permissions.can_recall` does
not exist) becomes a membership test against `reach`.

### 7.2 Performance

- **`reach()` and `govern()` are called per turn and per search.** The tree has
  fewer than 30 nodes. Build it once, cache against `scope_version`, and never
  walk the filesystem inside a request.
- **`list_notes()` globs a directory and parses `MEMORY.md` on every call**, and
  `layered_index_block` calls it once per layer. A tree walk would multiply that.
  Memoize per `(scope, scope_version.current(scope))`; the counter already exists
  and is bumped on every write, so invalidation is free and correct.
- **Scope filtering in search must not copy the matrix.** `vecs[keep] @ q`
  fancy-indexes and allocates a submatrix per call. At 3,800 × 768 float32 the
  full product is one BLAS call over 11 MB and takes well under a millisecond;
  compute `vecs @ q` once and mask the scores. Fewer allocations, less code, and
  it stops being a function of how narrow the filter is.
- **The index rebuild must leave the request path.** It is O(corpus) and on CPU
  that is ~8 minutes for a full build. Background it behind a lock, triggered by
  the existing stale marker, with incremental rebuild per changed scope rather
  than a full pass.
- **The backlink index is an index, not a grep.** Refs are extracted at write
  time into one keyed store; the entity page reads it directly. Never scan the
  corpus at render time.

### 7.3 The embedder

Pin it to CPU (`options.num_gpu = 0`). Measured today on this box: 121 ms/chunk
batched, a 3,800-chunk full rebuild in ~8 minutes, a single query embed in about
0.1 s warm. That is comfortably inside a background rebuild and imperceptible on
a query. The GPU buys nothing at this corpus size and costs the entire feature
whenever another workload holds it, which is the state the system is in right
now.

### 7.4 Tests

Each phase lands with tests in the same commit, run through the venv
(`cd backend && .venv/bin/python -m pytest`). The ones that matter:

- `reach()` and `govern()` over a fixture tree, including: development reaches
  workshop, workshop does not reach development, siblings are mutually opaque,
  and a non-dev build has no development node.
- The three-value `share` resolution, including override precedence.
- Search returns lexical hits with the embedder forced to fail, and says which
  mode answered.
- Ref parse/resolve round-trip for every kind, plus an unresolvable ref.

---

## 8. Retrieval

One tool, `search`, replacing the current pair:

- Parameters: `query`, `kinds` (`note` / `doc` / `chat` / `ticket`), `scope`
  (defaults to the caller's `reach()` set), `limit`.
- **Hybrid, so it degrades.** Vector when the embedder answers; lexical (the
  existing `_plaintext_search:670` plus BM25 over the same chunks) always
  available. When embedding fails the tool returns lexical hits **and says which
  mode answered**, instead of raising a 500 as it does now.
- Every hit carries a `ref` (§10), a scope, a timestamp and the passage body.
  Bodies, not titles: being unable to read what was found is the defect the
  module exists to fix, and that property must survive this change.
- Scope filtering applies to the candidate set by masking scores, per §7.2.
- `kinds: ["ticket"]` is how documentation and tickets integrate without merging:
  separate stores, separate lifecycles, one query surface, one ref format.

---

## 9. The vault: what it actually is, and why it leaves the design

The first draft carried a "unify the vault surface" workstream on the strength of
ADR 0010 calling `~/notes` the operator's personal Obsidian vault. Looking inside
it:

- 964 of the 965 files are under `Apple Notes/Notes/`, a **one-time Apple Notes
  export**, with an `.import` directory beside them.
- Every file's mtime is the import date, **2026-07-14**. Nothing has been written
  or edited since. The 965th file is `Test1.md`, 9 bytes.
- Content spans 2017 to 2026 and is personal miscellany: recipes, song
  observations, travel notes, product ideas.

So it is an archive of a dead import, not a working vault, and it is not
currently indexed by default anyway (`DEFAULT_KINDS = ("note", "doc", "chat")`,
`semantic_index.py:75`; vault is opt-in by name).

**Decision: the vault leaves this design entirely.** The files stay where they
are, the `/apps/notes` app stays as it is, ADR 0010 stays accepted and unchanged
(it is a correct rule about a store agents must not write to; that rule costs
nothing to keep). No shared-surface work, no merge, no supersession. If the
operator later wants a personal notes app, that is its own product decision made
on its own merits, not a memory-architecture question.

This removes a whole workstream and one of the three open questions from the
first draft. It is the largest single simplification in this revision.

---

## 10. Links and backlinks

One ref format, resolvable in-app:

```
workshop:note/<scope>/<id>
workshop:doc/<repo-relative-path>
workshop:ticket/<id>
workshop:chat/<thread-id>#<message-id>
workshop:run/<id>
workshop:project/<slug>    workshop:agent/<slug>    workshop:app/<name>
```

- One backend resolver returns `{kind, title, href, status}` for a ref, and
  refuses unreachable ones rather than rendering a dead chip.
- One frontend `<Ref>` chip: lucide icon, title, click navigates to the route. It
  looks like a link **without hover**, since the operator is often on a phone.
  This is the same defect already ticketed against `.chat-receipt--link`.
- Refs are written by agents in note bodies, ticket bodies and chat receipts, and
  by the operator in the editor.
- **Backlinks fall out**: extract refs at write time into one index; every entity
  page shows "referenced by". This is the graph half of §3 and it is what makes a
  note discoverable without titling it perfectly.

Acceptance test for this section: nothing renders a raw path or a copyable URL.

---

## 11. Surfaces

**One library page** at `/memory` (exists, 1,194 lines, correct design
language). It gains:

- a source badge per row (Note / Doc / Chat) with a filter;
- a scope filter shaped like the tree, not a flat list;
- an editor that can write **docs** as well as notes (`PUT /api/documents`;
  today read-only), starting as a plain markdown textarea;
- a per-item `share` control;
- provenance on every item: who wrote it, from which chat, when, as a `<Ref>`.

**Settings** gets one memory section: the global `share` defaults per kind
(including the chat-transcript default), per-scope overrides, and which kinds
search reaches by default.

---

## 12. Sequence

Each phase is one agent, reviewed and committed before the next starts.

**Phase 0 — migrate the accumulators, and cap the ambient block.** Split
`chat-distilled` and `consolidated-memory` (98 lines across three scopes) into
individual typed notes with provenance; drop the 3 noise lines; stop the
auto-writer from appending to a shared file. **In the same phase**, make
`index_block` a ranked, token-capped digest that declares its own remainder (§5.5)
— because splitting 98 lines into 98 notes under today's list-everything index
would make the prompt worse, these two cannot ship separately. No new features
otherwise. This is the "draw down complexity" instruction, and doing it first is
what stops the new model inheriting the old mess.

**Phase 1 — the scope tree.** `services/scope_tree.py` per §7.1, the stored
parent edge, `reach`/`govern`, `read_layers` and `_can_recall` reimplemented on
it, the frontend `lib/scope.ts` counterpart, tests. Backend and lib only, no UI.
Load-bearing; everything after is a consumer.

**Phase 2 — retrieval that works.** CPU-pinned embedder, lexical fallback with
mode reporting, scope filtering by masking, one merged tool with a `scope`
parameter, background incremental rebuild. Fixes the currently-broken deep query.

**Phase 3 — refs and backlinks.** Ref format, resolver, `<Ref>` chip, backlink
index, receipt restyling.

**Phase 4 — the library surface.** Source badges, tree-shaped scope filter, doc
editing, provenance display.

**Phase 5 — the share field.** `memory-policy.json`, resolution, per-item and
per-scope controls, the settings section. Last, deliberately: policy written
before the mechanism is policy written against a guess.

---

## 13. Explicitly not now

- Decay, eviction and confidence scoring on notes: premature at this corpus size.
- A graph database: the refs index is a table; the graph is a view of it.
- An `operator` scope for preferences: the cleaner long-term home for §4.3's
  `house` layer, but it is a migration of the learnings store and belongs with
  that work, not here.
- Any per-user permission model: single operator, and §6 is curation.
- Automatic chat distillation: retired in Phase 0 and NOT rebuilt here. If it
  returns it returns on top of §5's per-fact provenance model, which is the
  "proper design" it was deferred to on 2026-08-12.
- The `~/notes` vault, per §9.
