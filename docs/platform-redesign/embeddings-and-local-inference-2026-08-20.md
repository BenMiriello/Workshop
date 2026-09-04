# Embeddings and local inference: get Ollama out of production

- Date: 2026-08-20
- Status: **PARTLY BUILT. Operator approved (section 8): local bge-small
  everywhere including cloud, hosted providers deferred, fastembed dependency
  approved, local-LLM features dropped. Shipped so far: the embedding provider +
  both text embedders on it (Ollama out of the whole data layer), and the dead
  Ollama path removed from llm_router. Remaining: the /relay/local + commute
  local reasoner, the 511 decouple, and health/wearables. See "Progress" below.**

## Progress (2026-08-20)

Shipped and committed:
- `services/embeddings.py`: the local ONNX provider (bge-small-en-v1.5, 384-d,
  in-process via fastembed). `WORKSHOP_EMBED_PROVIDER` defaults to local; a
  non-local value fails loudly. `fastembed==0.8.0` added to core requirements.
- `semantic_index` and `task_dedup` both embed through the provider now. The
  index is tagged with the provider name and rebuilds on a model change, fixing
  the silent model-mixing bug (stages 1-3). `task_dedup` dropped its local-LLM
  same-work gate; a cosine >= MERGE_AT now links (non-destructive) instead of
  auto-merging.
- `llm_router`: the dead `call_local` Ollama apparatus removed; cloud path
  (`claude -p`) untouched.

Not done, and why:
- **`/relay/local` + commute's forced-local reasoner** (`routes/relay.py`,
  `services/commute/llm.py` `decide_interject`, `services/commute/reason.py`'s
  qwen rung) still call Ollama. This is the one LIVE Ollama LLM path. It is
  load-bearing (commute alerting) and mid-migration (two reasoner modules,
  `reason.py` Claude-first but `__init__` wiring `llm.py`'s local path), and the
  commute app is under active refactor in a parallel session. Ripping it out
  blind, unverified against a live commute brief, would risk silencing commute
  alerts. It needs either that refactor to settle or a decision to make commute
  cloud-only, then a verified cut.
- **511 decouple** (§4.1): isolated and doable (a commute-owned config endpoint +
  `CommuteSettings.tsx`), pending; the frontend half needs a bundle rebuild.
- **health/wearables** (§4.3): full decouple from platform source IS the
  app-extraction effort (the AppSpec seams, stage 4 of app-separation), which is
  what the parallel session is building now. Not a separate quick edit; doing it
  here would collide with that work.

- Operator direction, 2026-08-20: "We should not be using Ollama in production."
  On embeddings: "In production it probably makes most sense to use an external
  service... but if we're anticipating local implementations, does it make sense
  to do embeddings locally on people's machines if they're running a desktop
  app? Something we can just run as a self-contained thing within the app that
  people shouldn't have to think about." Plus: move the 511 integration out of
  global services into the Commute app, and finish the health/wearables
  extraction.
- Principle (standing): design for a production, multi-user, cloud deployment
  AND a self-contained local/desktop deployment. Nothing may depend on a daemon
  the operator's particular machine happens to run.

---

## 0. The direct answer

**Yes. Bundle a local embedding model that runs in-process, with zero config and
no separate daemon, as the default. Offer a hosted embedding API as the
production/cloud alternative behind the same interface.** This is a solved,
mainstream pattern: Chroma's default embedding function ships `all-MiniLM-L6-v2`
as ONNX and runs it locally via ONNX Runtime with no API key and no external
call, precisely to avoid a heavy dependency. We should copy that shape.

The current design fails the principle twice: it depends on **Ollama, a separate
server process**, for both embeddings and some LLM calls, and Ollama is a thing
that happens to run on this one machine. It cannot be assumed in a cloud tenant
or a desktop install.

---

## 1. What we have today (measured)

Three embedding subsystems, two of them for text, and they do not agree.

**Semantic search index** (`services/semantic_index.py`): embeds knowledge
notes, docs, chat transcripts, tickets, and the Obsidian vault into a per-owner
`index.vecs.npy` + `index.meta.jsonl`. Backend: Ollama `/api/embed`, model
`nomic-embed-text` (768-d), via `WORKSHOP_EMBED_MODEL`. No HTTP provider seam;
Ollama is the only backend, falling back to BM25 lexical search when it is down.

**Task dedup** (`services/task_dedup.py`): embeds ticket text to detect
duplicates. Prefers an HTTP service if `WORKSHOP_TEXT_EMBED_URL` is set, else
Ollama `/api/embeddings` with model `bge-m3` (1024-d), else a lexical hash
vector (512-d). Vectors are a 600-entry LRU cache, not a durable index.

**BirdNET acoustic embeddings** (`services/birds_analyze.py`): audio, not text.
Out of scope here, but it is the one subsystem that does this correctly: its
SQLite rows tag both `dim` and `model` per vector.

Three real problems fall out:

1. **No provider abstraction.** Two text embedders, two different models
   (`nomic-embed-text` 768-d vs `bge-m3` 1024-d), two different Ollama
   endpoints, and **four different env-var names for the Ollama URL**
   (`OLLAMA_URL`, `WORKSHOP_OLLAMA_URL`, `OLLAMA_BASE_URL`, plus the services
   probe). Ollama is hardcoded as the default in every one.

2. **`semantic_index` silently mixes models across a change.** Its incremental
   build reuses cached vectors keyed on a **content digest that excludes the
   model** (`semantic_index.py:126-134, 825-847`). Change `WORKSHOP_EMBED_MODEL`
   and unchanged notes keep their old-model vectors while edited ones get
   new-model vectors, in one matrix, with no detection. A cosine between two
   models' vectors is a number with no meaning. `task_dedup` avoids this by
   baking the model into its cache key; `semantic_index` does not. This is a
   latent correctness bug independent of the Ollama question.

3. **Ollama is also an LLM backend, and one path is load-bearing.**
   - `llm_router.call_local()` (Ollama `/api/generate`, `qwen2.5:32b`): **dead**,
     no in-tree callers. The redaction classifier it was built for is pure regex.
   - `task_dedup.same_work_llm()` (Ollama, `llama3`): the ">=0.90 cosine, ask an
     LLM if it is really the same work" gate. Degrades cleanly: on failure it
     returns None and the merge is downgraded to create-and-link. Not
     load-bearing.
   - `routes/relay.py` `/relay/local` and `/relay/auto`: a hard proxy to Ollama
     `qwen2.5:32b`. **Load-bearing:** `services/commute/llm.py` and
     `services/commute/reason.py` force their alerting reasoner through
     `/relay/local`. Removing Ollama breaks Commute's local reasoning unless it
     is re-pointed.
   - Voice (faster-whisper, Kokoro TTS) is **not** Ollama. Unaffected.

---

## 2. The target: one embedding provider, two backends

A single interface, config-selected, used by every text embedder:

```
class EmbeddingProvider(Protocol):
    name: str            # "local:bge-small-en-v1.5", "openai:text-embedding-3-small"
    dim: int
    def embed_documents(texts: list[str]) -> list[list[float]]   # indexing
    def embed_query(text: str) -> list[float]                    # search
```

Selected by `WORKSHOP_EMBED_PROVIDER`:

- **`local` (default).** A quantized ONNX embedding model bundled with the app,
  run **in-process** via ONNX Runtime (through `fastembed`, which is ONNX-only
  and pulls no PyTorch). No daemon, no API key, no network, free. This is the
  desktop/self-hosted default and the developer default. Recommended model:
  **`bge-small-en-v1.5`** (MIT, 384-d, ~32 MB int8 ONNX, a few to low-tens of ms
  on one CPU core, retrieval quality above OpenAI ada-002 for its size).
  `all-MiniLM-L6-v2` (Apache-2.0, 384-d, ~80 MB, fastest) is the lighter
  alternative and is Chroma's default. Both are 384-d, which keeps the door open
  to swapping one for the other without a dimension change (though a swap still
  requires a re-embed; see below).

- **`openai` / `voyage` / `gemini` / `http` (hosted).** For a cloud tenant where
  we do not want to run inference on the app server. Default hosted pick:
  **OpenAI `text-embedding-3-small`** (1536-d, ~$0.02 / 1M tokens, cheap and
  ubiquitous). Quality tier: **Voyage `voyage-3.5`/`voyage-3-large`** (Anthropic's
  own recommended embeddings provider) or **Gemini `gemini-embedding-001`**. The
  existing `WORKSHOP_TEXT_EMBED_URL` seam becomes the generic `http` provider.
  **Anthropic has no embeddings endpoint; do not design around one.**

This directly answers the desktop question: the local provider **is** the
self-contained, zero-config, free path. A hosted tenant flips one env var. Same
interface, same call sites.

### 2.1 The invariant we are currently violating

Vectors from different models live in different spaces and different dimensions
and **must never share a similarity index**. The rules, which we must adopt:

1. Tag every collection (and every stored vector) with `{provider, model, dim}`.
2. On a provider/model change, **re-embed that corpus**. There is no in-place
   conversion. This is a real cost: for a large tenant it is a full re-index.
3. Never cross-search vectors from two models. Partition by model, or refuse.
4. A user cannot be silently "upgraded" from the local model to a hosted one
   over an existing index; switching providers = re-embed that user's corpus.

`semantic_index` must start recording the model on each row and rebuild when it
changes. That fix is worth doing on its own merits (it is a live bug), and it is
a prerequisite for having more than one provider.

---

## 3. Getting Ollama out (non-embedding paths)

- **`call_local`**: delete. Dead code, no callers.
- **`same_work_llm`**: the same-work confirmation should call the platform's real
  LLM provider (the `claude -p` cloud path we already use for naming, triage,
  the keeper), not a local Ollama model. Or drop the LLM confirmation entirely
  and keep the cheaper embedding+threshold decision. Either removes the Ollama
  dependency; the second is simpler and the feature already tolerates its
  absence.
- **`/relay/local` + Commute reasoning**: re-point Commute's reasoner at the
  platform LLM provider. "Local relay to a resident 32B model" is itself a
  home-machine assumption; in production this is a normal model call. The
  `/relay/*` proxy as a whole is a candidate for deletion once nothing forces
  local.
- After those three, Ollama is referenced by nothing but the `/services` probe,
  which is removed anyway (section 4).

---

## 4. The adjacent decouplings the operator raised

### 4.1 511 belongs to Commute, not global services

The real 511 integration is already app-scoped in `services/commute/poller.py`
(secret `WORKSHOP_511_API_KEY`). The only coupling is in the **frontend**:
`CommuteSettings.tsx:39` decides whether the key is configured by string-matching
a probe name (`caltrain.gtfs_rt` / `511`) out of global `/api/services/` and
testing `status === "ok"`, a status the services route **never emits** (it emits
`up|degraded|down`), so this check is not just coupled, it is broken.

Fix: give Commute its own endpoint, e.g. `GET /api/<app>/config` returning
`{ realtime_key_set: bool }`, read from its own credential state. Commute stops
consuming global `/services` entirely. This is the general rule: an app answers
"is my integration configured" from its own surface, never by reading a
platform-wide process probe.

### 4.2 The `/services` probe grid

Already removed from the Settings UI (2026-08-20). The route still exists and is
consumed by the Apps launcher and (wrongly) Commute. Once Commute is decoupled
(4.1) and the launcher reads app-declared probes directly, the global
`/services` route and its Ollama/frontend/backend process probes can go. A
process-liveness prober is an ops concern, not a product surface.

### 4.3 Health / wearables extraction

Mostly done already: `routes/health.py`, `services/health.py`, and the
`HealthSettings` panel are app-scoped, and wearables is an external app declared
only through three service probes. What remains is the **enumeration**: both are
named literally in `services/installed_apps.py:8` and `BUILTIN_APPS`
(`app_registry.py:503-505`). Finishing this is the same work as the app-name
ratchet (`check_no_app_names.py`): the default install roster must come from the
installed-apps registry, not a hardcoded list in platform source. Health is one
of the operator's personal apps and should not be a built-in at all.

---

## 5. How we get there (staged, ordered)

Each stage is independently shippable and leaves the app working.

1. **Fix `semantic_index` model-tagging** (correctness, no new dependency). Record
   `{model, dim}` on each row; invalidate the cache and rebuild when the model
   changes; refuse to mix. This is worth doing regardless of everything else.
2. **Introduce the `EmbeddingProvider` interface** and route both text embedders
   through it. Ollama becomes one provider implementation, still the default for
   now. Collapse the four env vars to `WORKSHOP_EMBED_PROVIDER` (+ per-provider
   config). No behavior change yet; this is the seam.
3. **Add the `local` ONNX provider** (fastembed + `bge-small-en-v1.5`) and make it
   the default. Add `openai`/`voyage`/`http` providers. Ollama provider is kept
   only as an optional advanced backend, no longer the default. This is the stage
   that adds a dependency (fastembed/onnxruntime) and triggers a one-time
   re-embed of each corpus (cost noted in 2.1).
4. **Remove the Ollama LLM paths** (section 3): delete `call_local`, re-point or
   drop `same_work_llm`, re-point Commute reasoning off `/relay/local`.
5. **Decouple 511** (4.1) and **retire `/services`** (4.2).
6. **De-enumerate health/wearables** (4.3), folding into the app-name ratchet.

Stages 1 and 2 are pure cleanup and can start immediately. Stage 3 is the one
that needs the operator's decisions below before it starts.

---

## 6. Cost and footprint of the local default

- Dependency: `fastembed` (or `onnxruntime` directly). ONNX-only, no PyTorch.
- Disk: ~32 MB (int8) to ~130 MB (fp32) for the bundled model, fetched-and-cached
  on first use or shipped with the app.
- Latency: single short text, single CPU core, a few to low-tens of ms
  (estimate, must be benchmarked on target hardware).
- Quality: `bge-small-en-v1.5` benches above ada-002 on retrieval for 384-d.
  Lower than a 1536-d hosted model, which is the honest tradeoff for free +
  local. A tenant that wants top quality flips to a hosted provider.
- One-time re-embed of existing corpora when the default model changes from
  `nomic-embed-text` (768-d) to the new local model (384-d). Unavoidable; the
  dimensions differ.

---

## 7. Sources (web research, 2026-08-20)

- Chroma default embedding function ships all-MiniLM-L6-v2 as local ONNX:
  https://github.com/chroma-core/onnx-embedding ,
  https://cookbook.chromadb.dev/integrations/langchain/embeddings/
- fastembed (Qdrant), in-process ONNX, no server:
  https://qdrant.tech/articles/fastembed/
- transformers.js (JS/Electron in-process): https://huggingface.co/docs/transformers.js/index
- bge-small-en-v1.5 (MIT, 384-d): https://huggingface.co/BAAI/bge-small-en-v1.5
- all-MiniLM-L6-v2 (Apache-2.0, 384-d): https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
- nomic-embed-text-v1.5 (768-d): https://huggingface.co/nomic-ai/nomic-embed-text-v1.5
- OpenAI embedding models + pricing: https://openai.com/index/new-embedding-models-and-api-updates/
- Voyage (Anthropic's recommended provider): https://docs.voyageai.com/docs/embeddings ,
  https://docs.claude.com/en/docs/build-with-claude/embeddings
- Gemini embeddings GA: https://developers.googleblog.com/gemini-embedding-available-gemini-api/
- Different models = different spaces, re-embed on change:
  https://medium.com/data-science-collective/different-embedding-models-different-spaces-the-hidden-cost-of-model-upgrades-899db24ad233
- LangChain embeddings abstraction: https://deepwiki.com/langchain-ai/langchain/3.4-embeddings-and-vector-stores

---

## 8. Decisions for the operator

1. **Local default model:** `bge-small-en-v1.5` (MIT, 384-d, better quality) vs
   `all-MiniLM-L6-v2` (Apache-2.0, 384-d, smallest/fastest). Recommend
   `bge-small-en-v1.5`.
2. **Local runtime dependency:** OK to add `fastembed`/`onnxruntime` to the
   backend? (This backend is Python, so fastembed is the clean fit. A future JS
   desktop shell would use transformers.js behind the same interface.)
3. **Hosted default for cloud:** OpenAI `text-embedding-3-small` (cheapest) vs
   Voyage `voyage-3.5` (quality, Anthropic-recommended). Recommend OpenAI as the
   cheap default, Voyage as the quality option, both behind the interface.
4. **`same_work_llm` and `/relay/local`:** re-point onto the real platform LLM
   provider, or drop the local-LLM features entirely? Recommend re-point
   `same_work_llm` (or drop it) and delete the `/relay/*` local proxy.
5. **Order:** start stages 1-2 (pure cleanup, no dependency) now while the above
   is decided, or hold everything until the model/dependency call is made?
