# Retrieval in production: what gets embedded, when, and who queries it

- Date: 2026-08-27
- Status: **DECISIONS. Design only; nothing here is built and nothing in the
  live index changes because of this document.** Every numbered decision states
  what was chosen, what was rejected, and why.
- Owner boundary, because three threads are touching adjacent things this week:
  - **This doc owns the retrieval SUBSTRATE**: the corpus, the trigger, the
    store, the model, the query surface, and what all four do on an
    installation that is not this one.
  - [`finding-and-surfacing-memory-2026-08-23.md`](finding-and-surfacing-memory-2026-08-23.md)
    owns the **surfacing** half: the corpus map, the relevance hint, `open_ref`,
    the operator's search route, and the follow-through measurement. Its
    sequence is not restated or contradicted here; section 8 places it.
  - [`memory-model-and-document-kinds-2026-08-27.md`](memory-model-and-document-kinds-2026-08-27.md)
    owns **what a document is and where it lives**. This design consumes
    whatever that thread settles on and must not pre-empt it. Section 7.2 states
    exactly what it needs from that thread and nothing more.
  - Ticket `88b8c60764364b7d82c799c5d44058cb` owns collapsing the five
    addressing grammars. Section 7.1 states what this design needs from it.
- Satisfies: the push/pull law in
  [`chat-context-and-compaction-2026-08-25.md`](chat-context-and-compaction-2026-08-25.md)
  section 2 -- "anything the agent needs to KNOW rather than be TOLD, it pulls
  on demand." Retrieval is the pull half, and section 5.4 makes that a rule with
  a named enforcement point rather than a habit.

---

## 0. The five decisions, in one line each

1. **Corpus.** Index authored prose in four kinds -- note, doc, chat, ticket --
   at passage granularity, keyed on a `workshop:` **ref**, not on a filesystem
   path. Code is not embedded. Apps contribute more corpora by declaration.
2. **Trigger.** On write, via a marker, with a stat-fingerprint covering any
   corpus that has no write choke point, healed by one background sweep. Never
   on the request path, never on a cron. First run is a **batched, checkpointed
   backfill in kind order**, and search answers honestly while it is partial.
3. **Store and model.** Two files per owner (a JSONL of chunks, a float32
   matrix) searched by brute force, and one in-process local ONNX model. No
   vector database, no hosted embedding API, **$0 per installation at any user
   count**. The index is derived state: never backed up, never migrated,
   never committed.
4. **Query surface.** One tool, `search`, keeps one name and its `kinds`
   parameter. It does not split by corpus. It gains exactly one sibling,
   `open_ref`, because a tool that can only ever show a passage teaches agents
   to reconstruct rather than read.
5. **Production.** Nothing about the shape changes; three things about the
   plumbing do, and one of them is a live defect: **only the operator's index is
   ever swept**, so on a two-user installation the second user's index is built
   once and then rots. Per-owner isolation stays, duplication is accepted, and
   a shared index with a tenant column is rejected on the grounds that its
   failure mode is a data leak.

**First step of the build sequence, which ships alone:** key every chunk on a
ref and resolve a chat's scope at collection time (section 8, step 1). One
dataclass field, one moved function call, no new tool, no caller-visible
behaviour change, and it is what stops the imminent memory-tree restructure from
invalidating the note and doc corpora and silently breaking per-item share
overrides.

---

## 0.1 How to read the numbers in this document

The operator's standing correction, 2026-08-27: *"our usage does not necessarily
reflect the design or intended usage. Look at the design, look at the intended
usage. Assume we've never done or used any of this. Does the design make sense?"*

So: **no count below is an argument.** Counts appear in exactly two roles, and
each is labelled where it appears.

- **Sizing.** "A chunk costs 2.7 KB, so a million chunks is 2.7 GB" is
  arithmetic about the design, valid on any installation with any history.
- **Migration cost.** "Changing chunk identity re-embeds everything currently
  indexed" is a statement about what a change costs *here, once*.

A count is never a reason to keep or drop a corpus. The reasons for those are in
the intended use of each kind, stated in section 1.

---

## 1. Decision 1 -- the corpus

### 1.1 What is indexed

**Decision: four prose corpora, all indexed by default, plus any corpus an
installed app declares.**

| Kind | What it is | Why it is in the corpus |
|---|---|---|
| `note` | A durable scoped fact, preference or decision | It is the thing written *in order to be found later*. If this were not indexed the store would have no reader. |
| `doc` | A page of argument: research, plan, design, reference | It answers "why is this built this way" and "was this already designed". This is the corpus whose absence made an agent rebuild a finished subsystem inside a conversation. |
| `ticket` | The durable work item | It answers "is there already a ticket for this", which is a different question from "did we discuss this" and cannot be answered from the other three. |
| `chat` | The verbatim transcript | It answers "did we decide this", which is the question most often asked and least often written up. |

**The contested one is `chat`, and it is kept.** The argument for dropping it is
real: it is the fastest-growing corpus, the most conversational, the least
edited, and the most self-similar, and a naive relevance mechanism computed
inside a chat will mostly retrieve that chat.

It is kept anyway, for one reason that does not depend on any count: **the
alternative assumes the write-up happens.** A corpus of notes/docs/tickets only
can answer "what did we write down", never "what did we decide". A platform
whose retrieval can only see curated writing has quietly made "someone
remembered to write a note" a precondition for institutional memory, and that
precondition is exactly what fails. The transcript is the only artifact that
exists whether or not anyone was diligent.

**Rejected: index only durable, curated writing (note/doc/ticket) and treat
chat as ephemeral.** Rejected because it makes findability a function of
discipline. The self-similarity problem it was meant to solve is a *surfacing*
problem and is solved where it belongs: the relevance hint excludes `kind=chat`
(finding-and-surfacing sec 4.2), while `search` still reaches chats when a model
asks for them. Restricting the store to fix a ranking problem is the wrong
layer.

### 1.2 Code is not embedded

**Decision: source code is not in the semantic corpus, and no `code` collector
is built.**

Three reasons, none of them about size:

1. **The question code answers is exact, and semantic search is not.** "Where is
   this symbol defined", "what calls this", "does this string appear anywhere"
   are answered correctly and completely by grep over the working tree, in
   milliseconds, with zero index and zero staleness. An embedding gives a
   plausible neighbourhood instead of an answer.
2. **Code is the corpus that goes stale fastest and matters most when current.**
   Every other corpus tolerates a five-minute lag. A retrieval hit on a function
   that was renamed twenty minutes ago is not a slightly-wrong answer, it is a
   wrong one, and the agent has no way to tell.
3. **The tree is already the index.** A maker turn has filesystem tools. It does
   not need a second, lossy, lagging copy of the thing it can read directly.

**Rejected: embed the repository so a narrow chat can ask about code.** The need
is real -- a data chat has no grep -- but the answer to "a narrow chat cannot
read the tree" is a tool that reads the tree under the same scope rules, not a
vector copy of it. That is a different ticket and it is named in section 8 as an
explicit non-goal of this one.

**Rejected: embed code documentation and comments only.** A doc that is worth
retrieving belongs under `docs/` where it is already indexed. Extracting
docstrings into a fifth corpus creates a second home for the same sentence and
guarantees the two disagree.

### 1.3 Granularity

**Decision: the indexed unit is a passage of about 1,200 characters with a 150
character overlap, produced by one paragraph-aware splitter shared by every
collector. The addressable unit returned to a caller is a ref plus that passage,
never a whole document and never a bare title.**

Three sub-decisions inside that, each with its rejected alternative:

- **Not whole-document granularity.** The embedding model has a 512-token
  window. A 40,000-character design doc embedded as one vector is semantically
  dead past its first few paragraphs, and the resulting hit says "this document
  is vaguely about your question" rather than "this paragraph answers it".
- **Not per-message granularity for chat.** One vector per chat line ranks
  "sounds good" alongside a design argument, and its cost is dominated by
  chunking overhead rather than by content. Messages are grouped in log order up
  to the same passage size, and a message *longer* than a passage is split, not
  truncated. (Truncating was a real defect: it discarded the long messages,
  which are the designs and the operator's direction.)
- **At most two passages from one source per answer.** Adjacent passages of one
  file are near-identical in embedding space, so without a cap one document
  takes most of the slots and the answer is one document with the illusion of
  corroboration.

### 1.4 Identity: a chunk is keyed on a ref

**Decision: every chunk carries a `workshop:` ref as its identity, computed by
its collector at collection time. The filesystem path, where one exists, is a
detail of the collector and is not the key.**

This is the decision the ticket calls urgent, and it is urgent for a reason that
is not a count. Today a note chunk's key is the repo-relative path of the file
and a doc chunk's key is its `docs/` path. That makes the index a function of
the directory layout, so **a restructure of the memory tree is indistinguishable
from a deletion followed by an insertion**: every affected chunk loses its
digest, gets re-embedded, and -- the part that actually hurts -- every per-item
share override and every `SUPERSEDED` judgement keyed on the old ref silently
stops applying, without an error, to a corpus that still returns confident hits.

The ref vocabulary already exists (`workshop:note/<scope>/<id>`,
`workshop:doc/<path>`, `workshop:ticket/<id>`, `workshop:chat/<thread>`), is
already what `search` returns to a model, is already what the policy layer keys
on, and is already what the operator's chips render. The only thing wrong with
it is that the index computes it at *query* time from a path, instead of the
collector recording it at *write* time.

- **A note is a scope and an id, never a path.** So a note keeps its identity
  across any tree the memory thread chooses, and this design does not need to
  know what that tree is.
- **A doc is still keyed by path**, and that is honest, because a doc's path
  *is* its identity today. When the memory thread renames folders, the doc
  corpus re-embeds once. That is a one-time cost measured in minutes and is
  accepted rather than designed around.

**Rejected: a second, index-local id (a UUID per source).** It would survive
renames for both notes and docs, but it needs a persistent path-to-id map that
is itself state to keep, back up and repair -- state that can disagree with the
store, in a subsystem whose entire virtue is that it is derived and disposable.
The ref grammar is the addressing scheme this platform already committed to;
inventing a second one inside the index is the exact failure ticket
`88b8c607` was filed to stop.

### 1.5 What `scope` means on an index row

**Decision: `scope` on an index row is the scope that governs *reach* for that
chunk's source object. It holds one value from the `scope_tree` grammar, or the
empty string meaning "placeless". It holds nothing else, and in particular it
never holds a `context_key`.**

Two concrete consequences, both small:

- **A chat's scope is resolved at collection time, not at query time.** The chat
  collector currently records the thread's raw `context_key` and the resolution
  to a real scope happens per-row on the query path. That is why a `context_key`
  value sits in a column called `scope`. Moving the resolution into the
  collector makes the column mean one thing, and removes a per-row Python call
  from the hot path (section 3.4).
- **"Placeless" is a real state and stays.** A platform doc sits in no scope, so
  the tree has nothing to say about it and it is reachable from everywhere. That
  is correct for platform documentation and would be wrong for an app's own
  docs, so a collector that knows its corpus belongs to a scope must say so
  rather than emitting the empty string by default.

**Rejected: make `scope` nullable-and-free-text and let the query layer sort it
out.** That is the status quo, and the status quo is a column with at least two
grammars in it that produces a filter nobody can reason about.

**Rejected: solve this by picking the winning grammar here.** Not this ticket's
call. Section 7.1 states the dependency.

### 1.6 Apps extend the corpus by declaration

**Decision: unchanged and reaffirmed -- an app adds a corpus by declaring a
collector, and may declare it opt-in so it is searched only when named.** No
platform source file names an app's corpus. An opt-in corpus survives a rebuild
it did not ask for, because "which kinds to rebuild" is the defaults plus
whatever is already indexed, never the kinds of the query in hand.

---

## 2. Decision 2 -- the trigger, and the first run

### 2.1 Steady state

**Decision: write-marks-stale, plus a fingerprint for corpora with no choke
point, healed by a single background sweep. A search never rebuilds in-band.**

The four parts, and why each exists:

1. **A write leaves a marker.** Every store that accepts a write stamps a
   per-owner marker file at the tail of a write that already succeeded. Costs
   one `exists()` when nothing is stale, which is the common case. Never raises:
   failing a note write because a marker could not be touched is strictly worse
   than a stale index.
2. **A corpus with no choke point gets a stat fingerprint.** Notes, chats and
   tickets each have exactly one store that every write goes through. Documents
   do not -- the overwhelming majority are written straight to disk by an editor
   or an agent's filesystem tools, none of which know the marker exists. So the
   doc tree is fingerprinted by *which files exist and each one's size and
   mtime* (never a body), compared against what the last successful build
   observed. A mismatch means the disk moved, regardless of what moved it.
   **This is the general case, not a doc special case**: any corpus reachable
   through the filesystem gets it, including an app-declared one.
3. **One sweep, in the long-lived process, on a short interval.** It is the only
   thing that runs a build.
4. **A search answers from disk, always.** If the index is stale it says so in
   its envelope and the caller labels its hits accordingly. It never blocks.

**Rejected: a scheduled rebuild (cron).** It rebuilds when nothing changed and
lags when something did, and it adds a scheduler to the list of things a
self-hosted installation has to have working. A marker costs nothing when
nothing was written and is exact when something was.

**Rejected: rebuild on the request path.** This was the behaviour and it was
measured to fail hard: the process that calls `search` is very often a
short-lived per-turn MCP subprocess, so a cold build ran inside a turn's process
group and the turn was killed at the memory ceiling for the crime of searching.
The index is platform state; rebuilding it is a platform background job.

**Rejected: rebuild only when somebody searches.** A chat that never searches
never heals the index for the chat that does, so freshness becomes a function of
who happened to ask a question recently.

### 2.2 The backlog on first run

This is where the current design is genuinely incomplete, and the gap is not
subtle.

**The facts, as migration cost.** A cold build of this installation's corpus was
measured at roughly **8 minutes and a 9.2 GB peak** (2026-08-22). The peak is
not the vectors -- 15,909 rows of 384 float32 is 24 MB. The peak is that
`build()` collects the whole corpus, hands **every** un-cached passage to the
provider in one call, and the provider materialises the entire result as Python
lists before numpy ever sees it. Peak memory is therefore proportional to the
*corpus*, when it should be proportional to a *batch*. And because nothing is
written to disk until the very end, an interrupted 8-minute build loses all 8
minutes and starts over.

**Decision: the first run is a batched, checkpointed backfill in kind order, and
it is the same code path as any other build.**

- **Batch.** The provider grows a batched contract (embed N passages, yield
  rows) so peak memory is a function of batch size and nothing else. This
  extends the existing provider seam; it does not add a second one.
- **Checkpoint.** The build persists after each kind, in the order `note`,
  `ticket`, `doc`, `chat` -- smallest and most decision-dense first, largest and
  most conversational last. An interrupted backfill resumes from the digest
  cache instead of restarting, because the partial result is already on disk.
- **Order is a product decision, not an implementation detail.** It means that
  five minutes into a fresh installation the agent can already answer "did we
  decide this" and "is there a ticket" against everything curated, and is only
  missing transcripts.
- **Search is honest while it is partial.** The tool already reports how many
  passages were searchable versus how many are indexed, and already flags that a
  rebuild is in flight. During a backfill both fire, and that is the correct
  behaviour: an agent told "the index is still filling" widens its search or
  asks, rather than concluding the workshop is silent.
- **Lexical search covers the gap.** BM25 runs over the same passage text the
  index already stores, so it needs no embedder at all. A half-built index still
  answers; a cold one answers as soon as the first kind lands.

**Rejected: block installation until the index is complete.** Minutes of a
progress bar before a person can use the product, to populate a store that is
useful the moment its first kind lands.

**Rejected: index only from now on, no backfill.** It makes findability a
function of install date, which for a self-hosted platform that people adopt
onto an existing tree of notes and docs is the same as not shipping retrieval.

**Rejected: backfill lazily, on first search.** That is the request-path rebuild
under another name, with the added defect that the first person to search pays
for everyone.

---

## 3. Decision 3 -- the store, the model, and what it costs

### 3.1 The store

**Decision: keep the two-file, per-owner, brute-force store. A JSONL of chunk
records and a float32 matrix, loaded and dot-producted with numpy. No vector
database.**

The arithmetic, which is design sizing and holds on any installation:

- A row costs **1,536 bytes of vector** (384 dimensions, float32) plus about
  **1.2 KB of record** (the passage text, its ref, title, scope, timestamp,
  status and digest). Call it **2.7 KB per passage**, of which the text is
  slightly less than half.
- So **10,000 passages is 27 MB; 100,000 is 270 MB; a million is 2.7 GB.**
- A query is one 384-dimension dot product per row. At 100,000 rows that is 38
  million multiply-adds in a single BLAS call: a few milliseconds. At a million
  rows it is 384 million, still well under a second, and at that point the
  matrix is memory-mapped rather than read (`mmap_mode="r"`, one argument) so
  resident memory stops tracking corpus size.

A vector database buys approximate nearest-neighbour search, which matters when
the exact scan stops being fast. The scan does not stop being fast at any size a
personal installation reaches, and in exchange the database is a service to
install, run, monitor, upgrade and back up, on every self-hosted box, forever.

**Rejected: a vector database (or an embedded ANN index) now.** Nothing
measurable is bought until roughly two orders of magnitude beyond a normal
corpus, and the cost is paid by every installation on day one.

**Rejected: SQLite with a vector extension.** Closer to acceptable -- no daemon
-- but it trades a `numpy.load` for a compiled extension that has to be present
and correct on every platform the app ships to, in exchange for the same
sub-second scan. Revisit it if and only if the trigger in 3.4 fires.

### 3.2 The index is derived state

**Decision: the index is never backed up, never migrated, never committed, and
never repaired. It is rebuilt.**

This is what makes every other decision in section 3 cheap and reversible. A
corrupt or half-written index reads as empty rather than as an error. An index
tagged with a different embedding model than the one now running is discarded
wholesale rather than converted. A schema change to the chunk record is a
rebuild, not a migration. Nothing is ever lost, because the stores are the truth
and the index is a cache of them.

The corollary is a rule for deployment: **an installation with no persistent
disk is not broken, it is slow once per boot.** State the cost (minutes of
background CPU) and move on.

### 3.3 The model

**Decision: one embedding provider ships -- a small quantized ONNX model run
in-process, with no daemon, no API key, and no network at inference time. It is
the default in every deployment, cloud included. The provider interface stays,
so a hosted backend can be added, but none ships and setting the provider to
anything else fails loudly rather than pretending.**

**Cost per installation: $0 in recurring spend, at any number of users, forever.**
That is the whole argument and it is not close. The rest is footprint:

- **Disk:** roughly 32 MB for the model, fetched and cached once, plus 2.7 KB
  per passage.
- **CPU:** single-core, milliseconds per query, off the request path for
  indexing.
- **Egress:** none. A person's private notes are never sent anywhere to make
  them searchable, which for a self-hosted personal platform is a product
  property, not an optimisation.
- **Quality:** a 384-dimension local model ranks below a large hosted one. That
  is the honest trade for free, private and offline, and it is the right trade
  when the alternative is that retrieval does not work without somebody's API
  key.

**Rejected: a hosted embedding API as the default.** It makes a self-hosted
personal platform depend on a third-party account to have a working search box,
and it makes the operator's private writing an outbound request. The ticket
asked whether a design that needs a hosted service to work at all is wrong for
this product: it is, and this is the clearest case of it.

**Rejected: a local model served by a separate daemon.** A daemon is a thing
that happens to run on one machine. It cannot be assumed in a cloud tenant or a
desktop install, which is the same standing principle that got the previous one
removed.

**The invariant that constrains all of this: vectors from different models live
in different spaces and are not comparable.** The index records the provider
identity alongside its vectors and refuses to mix; changing the model is a full
re-embed and never an in-place conversion. A user is never silently upgraded
from the local model to a hosted one over an existing index.

### 3.4 What actually breaks first, and the trigger to revisit

**The dot product is not the scaling risk. The reach mask is.**

Before ranking, every row is tested for whether this caller may see it: is it
one of the requested kinds, is its scope within reach, and does the per-item
share policy admit it. That test is a **Python loop with a policy call per row**,
and on this installation's index it is roughly **0.36 seconds of a 0.41 second
search** -- about 88% of the cost, and the only part that grows linearly in
Python rather than in BLAS.

**Decision: the mask is precomputed, vectorized and cached, before anything else
about the store is reconsidered.**

- Resolve each row's scope to a small integer id **at collection time** (which
  section 1.5 already requires), so reach becomes a numpy set-membership test
  over an integer column rather than a per-row dictionary walk.
- Cache the resulting boolean mask per `(owner, reach set, from_scope, index
  generation)`. It changes only when the index is rebuilt or the policy is
  edited.

**The trigger to revisit the store:** a single owner's index passes ~500,000
passages, **or** p95 search latency exceeds 250 ms with the mask cached. The
escalation, in order, is: memory-map the matrix, then quantize the vectors to
int8, then and only then consider an ANN index. Each step is smaller than the
one after it, and the first two are one line each.

---

## 4. Decision 4 -- the query surface

### 4.1 One tool, not one per corpus

**Decision: `search` stays a single tool with a `kinds` filter. It is not split
into `search_notes` / `search_chats` / `search_docs` / `search_tickets`.**

The reason is the premise of the feature: **the caller does not know which
corpus holds the answer.** "Have we decided X before" might be answered by a
note, by the tail of a design doc, by a ticket body, or by the conversation
where it was actually decided. A split surface forces the model to guess the
container before it is allowed to ask the question, and the common failure is
that it guesses one, gets an empty result, and reads the empty result as "no
prior art" -- which is precisely the failure the empty-result contract was
written to prevent.

The secondary reason is budget. Every tool costs a permanent slot in every
agent's prompt, on every turn, forever. Four tools that differ only in a filter
value is four descriptions to keep consistent and four ways to spell the same
question, in exchange for a parameter that already exists.

**Rejected: split by corpus.** As above.

**Rejected: a `more_like_this` tool.** It is `search` called with the passage
text as the query. A permanent tool slot for a composition that already works.

**Rejected: a separate `rerank` step exposed to the model.** Ranking is the
tool's job, and a model asked to re-rank its own retrieval will confidently
re-rank noise.

### 4.2 The one sibling that is justified

**Decision: add `open_ref(ref, part=None)` and nothing else.**

Retrieval that can only ever return a bounded passage -- at most two passages
from any one source -- while the source is a 40,000-character design document,
teaches agents to reconstruct arguments from fragments rather than read them. A
maker chat papers over this by falling back to filesystem tools for a `doc` ref,
which is why the hole has gone unnoticed; a narrow chat cannot, and **no** chat
can read a `chat` or `ticket` ref that way.

Constraints on it, all of which fall out of decisions above:

- It takes the ref vocabulary that already exists and **does not invent a second
  addressing scheme**. It takes exactly what `search` returned.
- It applies the **same scope and share checks** the search applied, so it can
  never be used to walk around a boundary a search respected.
- It is **bounded and continuable**: over the bound it returns a head plus a
  stated character offset and accepts `part` to continue.

### 4.3 Who queries the index

**Decision: three callers, one implementation.**

| Caller | Path | Scoping |
|---|---|---|
| An agent | MCP `search` / `open_ref` | The chat's own reach as the allowed set, plus the chat's own scope as `from_scope` so the share policy can evaluate "private to its own scope". |
| The operator | One HTTP route | **Unscoped.** The person who owns every scope is not a scoped agent, and passing no `from_scope` skips the policy layer, which is the correct behaviour for them. |
| The platform | Direct call (e.g. ticket dedup) | Whatever the calling job's owner is. Never for injection. |

The operator route is also the diagnostic surface for the worst class of bug in
this subsystem: a hit the operator can see and an agent cannot is now a visible,
reproducible difference rather than an invisible one. That class of bug once
erased the entire document corpus for scoped callers, and nothing noticed.

### 4.4 The law, and where it is enforced

**Retrieved content reaches a model only as the result of a tool the model
chose to call.** Not in the opening prefix, not appended per turn, not as a
snippet, not as a hook, not as the first line of a passage.

This is the pull half of the push/pull law, and it is the reason this ticket
exists: the previous answer to "how does an agent find prior art" was to load
everything into the opening context.

Two things that are *not* violations, and the distinction is the whole design:

- **A statement about the corpus** -- how much exists, of what kinds, over what
  date range, and that none of it is in this prompt -- is a fact about the
  store, is stable, and is cache-friendly. Awareness is a property of the
  corpus.
- **A statement about this turn** -- these two documents look relevant to what
  you just said -- is a fact about the query. Relevance is a property of the
  query and belongs in a tool call, or, if it is ever pushed at all, in the
  volatile tail after the cache breakpoint, as titles and scores with no body.

The surfacing thread owns both mechanisms and their measurement. This document
owns the boundary: **anything carrying passage text is a tool result, always.**

---

## 5. Decision 5 -- what changes when this is not one operator on one box

### 5.1 What already generalizes, and needs no change

The store is per data owner, under that owner's runtime directory, and the owner
is pinned in a contextvar that **raises when unset** rather than defaulting to
anyone. A missing set-point is a crash, not a cross-owner read. There is no
shared file across owners, so there is no cross-owner read path to get wrong.

That is the right foundation and it stays. The retrieval design does not need a
tenant model, an ACL table, or a row-level security scheme, because the
partition is the directory.

### 5.2 The live defect: only one owner's index is ever swept

**Only the operator's index is maintained.** The background sweep pins the
operator as its owner, and the only other thing that starts a build is a search
arriving inside the process that declared itself the maintenance owner -- and
the tool that calls search is a per-turn subprocess, which by design declares
itself *not* the maintenance owner and does not do the work.

So on an installation with a second user, that user's index is built once, if at
all, and then rots quietly while their searches keep returning confident hits
from a snapshot. It fails silently and it fails in the direction that is hardest
to notice.

**Decision: the sweep iterates owners.** Each pass enumerates owners with a
stale index, pins each in turn, and rebuilds one at a time -- serially, never in
parallel, because the embedding runtime's memory is the constraint and N
concurrent builds multiply it. One loop, one interval, N owners.

**Rejected: a maintenance process per owner.** N processes each holding a copy
of the model, for a job that is idle almost always.

### 5.3 What is duplicated across owners, and why that is accepted

Two users on one installation who can both read the same document tree will each
embed it, each store it, and each pay for it. That is real duplication: on the
sizing in 3.1, tens of megabytes and a few minutes of one-time CPU per user.

**Decision: accept the duplication. Do not build a shared corpus with a tenant
column.**

A shared index needs the reach and share policy evaluated per row **across an
owner boundary**, on every query, forever. Getting it right is not hard; getting
it right *every time it is touched again* is, and the failure mode is that one
person's private notes appear in another person's search results. Per-owner
directories make that failure structurally impossible rather than
conditionally absent. Tens of megabytes is a good price.

There is also a standing warning about this exact choice: the shape of
per-tenant data is the least reversible decision in the hosting design, and not
urgent until tenant two. Choosing the shape whose failure mode is a leak, before
tenant two exists, would be the worst of both.

**Rejected: one shared index with a tenant column.** As above.

**Rejected: a shared read-only index for platform documentation plus a private
one per owner.** Tempting, because platform docs genuinely are common. Rejected
because it splits every query into a fan-out over two stores with two
generations and two staleness states, to save a duplication whose cost is
already accounted for and small.

### 5.4 What changes when the box is a container

Three things, all of them deployment facts rather than design changes:

1. **Exactly one process must own maintenance.** Today that is "the backend
   process, because its startup says so". With more than one worker process --
   which any real deployment has -- every worker's startup says so, and N loops
   sweep the same index. Writes are atomic renames so nothing corrupts, but the
   CPU is spent N times. **Decision: maintenance ownership becomes a declared
   installation fact** (an env flag or a lock file, one holder, fail-closed to
   "nobody owns it" plus a loud status) rather than an accident of which process
   ran a line of startup code.
2. **Embedding competes with request handling.** It already runs off the request
   path in a background thread; in a container that thread's CPU is the same CPU
   serving requests. Batching (2.2) is what makes this tolerable, because it
   converts one long spike into a series of short ones that can yield.
3. **No persistent disk means a rebuild per boot.** Acceptable, per 3.2, because
   nothing is lost. It is the cost of a derived store and it is stated here so
   nobody discovers it as a surprise.

### 5.5 What does *not* change

Corpus, granularity, ref identity, trigger, store shape, model, query surface,
and the injection law. All five decisions above hold identically for one
operator on a laptop, five people on one server, and a cloud tenant. That is the
test the design was written against, and it is the answer to "what would a second
installation do": **the same thing, with more owners in the sweep.**

---

## 6. Failure modes this design accepts, named

A design that does not say how it fails is not finished.

- **The index drifts and lies quietly.** Everything downstream trusts the
  snapshot. Mitigation: the index reports its own build time and health, and any
  surface that states corpus facts renders that timestamp beside them. This
  is the failure mode that most needs a visible clock, because a wrong count is
  indistinguishable from a right one.
- **The embedder is unavailable.** The build fails, the stale marker survives so
  the next pass retries rather than silently accepting staleness forever, and
  search degrades to lexical BM25 over the same stored passage text. Degraded,
  never dark.
- **A search returns nothing.** This is an answer, not a malfunction, and the
  tool says so in words -- along with how many passages were actually searchable
  from this scope versus how many are indexed, so a policy-narrowed search does
  not read as a whole-corpus miss.
- **The reach mask is wrong.** The worst failure here is invisible: an agent is
  told nothing has been written about something that has been. The operator's
  unscoped route (4.3) is what makes that difference visible.

---

## 7. What this design needs from other threads

### 7.1 From the addressing grammar ticket (`88b8c607`)

Exactly two things, and nothing more:

1. **One grammar for the value in the `scope` column**, with a parser that
   accepts it and rejects everything else. This design pins that column to the
   scope grammar in the meantime (1.5) and will follow whatever replaces it.
2. **A statement on whether `""` (placeless) survives.** This design uses it to
   mean "reachable from everywhere, belongs to no scope", which is correct for
   platform documentation. If the unified grammar gives placelessness a name,
   the collectors emit that name instead. One-line change.

Neither is blocking. Every decision here holds under either answer, and the
migration is a rebuild.

### 7.2 From the memory-model thread

Exactly one thing: **the note collector needs a way to enumerate notes as
`(scope, id)` pairs, whatever the on-disk tree turns out to be.** That is the
entire coupling. This design deliberately does not know, and must not know,
whether notes live in one flat directory per scope, in typed folders, or
somewhere else; whether the learnings store survives as a separate store or
becomes a note type; or what the document folder names are.

If a folder restructure lands, the doc corpus re-embeds once and the note corpus
does not, which is the whole point of 1.4.

---

## 8. Build sequence

Ordered so that each step is shippable on its own and leaves the system working.
Steps 2 through 5 belong to the surfacing thread's sequence and are listed here
only to place this work relative to it; they are not re-decided.

**Step 1 -- Key every chunk on a ref, and resolve chat scope at collection time.
This ships alone and is the first thing to do.**

- Add the ref to the chunk record, computed by each collector.
- Move chat scope resolution from the query path into the chat collector, so the
  `scope` column holds a scope and nothing else (1.5).
- Chunk identity becomes the ref plus content, so a rename that preserves the
  ref preserves the vector.
- Cost: one full rebuild, which is the routine already used for a model change.
- No new tool, no schema migration, no caller-visible behaviour change.
- Why first: the memory tree is being restructured now. Doing this after the
  restructure means paying the invalidation and silently dropping per-item share
  overrides in between; doing it before means the restructure is a no-op for
  notes.

**Step 2 -- Bound the build's memory and checkpoint it** (2.2). Batched provider
contract, persist per kind, backfill order `note`, `ticket`, `doc`, `chat`. This
is what makes a first run on a new installation survivable, and it is
independently a fix for the peak that once killed a turn.

**Step 3 -- Precompute and cache the reach mask** (3.4). Integer scope ids from
step 1 make this a numpy operation. Removes the large majority of search latency
and is the prerequisite for the store choice staying correct as a corpus grows.

**Step 4 -- Sweep every owner, and declare the maintenance owner** (5.2, 5.4).
The point at which a second user's index stops rotting. Small, and it is the
only step whose absence is a correctness bug rather than a performance one.

**Step 5 -- `open_ref`** (4.2). Closes the largest gap in the query surface and
is a precondition for measuring whether any surfacing mechanism works.

**Step 6 -- The operator's search route** (4.3). Independent of everything
above.

**Steps 7+ -- The surfacing mechanisms** (corpus map, baseline logging, and only
then the conditional relevance hint), owned and sequenced by
[`finding-and-surfacing-memory-2026-08-23.md`](finding-and-surfacing-memory-2026-08-23.md)
section 7. They are last because they are the only part that can make things
worse, and because steps 1 through 4 are worth doing whatever their outcome.

**Explicit non-goals, filed as separate work rather than dropped:**

- A tool that lets a narrow (non-maker) chat read the source tree under its own
  scope rules. Named in 1.2; it is not a retrieval feature and must not be
  solved by embedding code.
- A hosted embedding backend. The seam exists and is correct; nothing ships
  behind it until an installation asks for it, and the first thing that asks
  will state why.
