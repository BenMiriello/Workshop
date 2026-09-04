# Providers, credentials, and limits: one seam for every model

**Status:** design only, nothing built. Written 2026-08-23 from an operator
instruction (recorded verbatim in substance in §1, unfinished and subject to
change) plus a full read of the live tree. Supersedes nothing. It extends
[`model-access-and-byok-2026-08-11.md`](model-access-and-byok-2026-08-11.md)
(the licensing rule and stages 1 to 6) and
[`embeddings-and-local-inference-2026-08-20.md`](embeddings-and-local-inference-2026-08-20.md)
(the local embedding provider, shipped). Read both first; this doc does not
repeat their arguments.

**The business model is NOT decided here.** §10 records the options and their
consequences and stops. Stages 6 and 7 in §11 are blocked on it.

---

## 0. The direct answer

Six recommendations, one per question, before the evidence.

**(a) Provider model.** Two interfaces, `LlmProvider` and `EmbeddingProvider`,
and a provider record carrying `transport` (`cli_subprocess` or `http`) and
`wire` (`anthropic_messages` or `openai_chat`). **OpenAI-compatible `/v1` is NOT
the right universal adapter for this platform.** It is the right *floor*. Since
January 2026 the three local runtimes that matter (Ollama 0.14.0, llama.cpp's
`llama-server`, LM Studio 0.4.1) all serve a native `/v1/messages`, and this
platform's entire turn model is Anthropic-shaped. Prefer `anthropic_messages`
wherever the endpoint offers it, keep `openai_chat` as a declared-degraded
fallback, and treat local-vs-cloud as `base_url` plus `auth_mode` rather than as
two interfaces.

**(b) Credentials.** Keep `services/model_credentials.py` exactly as written and
make it reachable. A credential belongs to a **principal** and to nothing else;
project and agent scopes hold a *reference* to a provider id, never a second
copy of a key. Turn on the already-built envelope encryption
(`services/secrets_crypto.py`) and make a KEK **mandatory at boot** whenever
`WORKSHOP_DEPLOYMENT_MODE=multi_user`. Enforce the subscription rule in the UI
by rendering nothing at all for a prohibited provider, never a disabled control,
which is what `model_credentials.available_providers()` already does at
`model_credentials.py:144-153`.

**(c) Selection.** One resolver returning one `ModelChoice`, extending the
existing chain at `routes/chat.py:211-232` rather than adding a second. Order,
most specific first: explicit request > thread or job pin > agent declaration >
app or project default > principal default > deployment default. Effort resolves
**after** the model, which is already how `_resolve_effort` works
(`routes/chat.py:235-270`). The hardcoded six-model list at
`chat_context.py:49-57` becomes a catalog derived from configured providers.

**(d) Limits.** **Extend `usage_ledger`, do not replace it, and do not add a
second budget system.** Three changes: put `provider`, `model` and `micro_usd`
on the row; generalise the single config to a list of limit rules keyed
`{scope_kind, scope_id, window, unit, amount, on_breach}`; keep today's
reserve-fraction admission as one rule in that list so nothing regresses. All
rules must pass, most specific reported first. Autonomous work refuses on
breach (today's behaviour), interactive chat degrades only if the principal
opted in, nothing queues silently.

**(e) Settings IA.** A seventh Settings tab, `/settings/models`, with four
sections: Models, Providers and keys, Local models (advanced), Limits. The
default view is two controls: which model, and add a key. Everything else sits
behind one "Advanced" disclosure per section. The token cap editor **moves out of
`Usage.tsx`** so intent lives in Settings and measurement lives in Usage.

**(f) Deployment matrix.** Fully-local desktop: local LLM plus local embeddings,
zero keys, zero dollars, and reasoning quality is what breaks. Self-hosted: one
principal, own keys, spend limits in dollars. Hosted multi-user: BYO API key
only, embeddings paid by the platform out of the local provider (that is, free),
and the real blocker is not credentials but execution isolation
(`model-access-and-byok-2026-08-11.md` §6).

---

## 1. The operator's direction, as stated

Recorded because it is the input, not because it is settled. Operator,
2026-08-23, paraphrased close to his words and explicitly **rough, unfinished
and subject to change**:

- Any number of AI providers should be pluggable: cloud APIs (Anthropic,
  OpenAI, others) **and** custom/local models, for **LLMs and embeddings both**.
  Any agent, chat or process can use any of them, as configured.
- There should be a system for registering your own keys, usable across any
  number of services.
- It must be possible to run the whole thing **fully local**, local LLM plus
  local embeddings, alongside cloud services. Both need their own setup,
  registration and configuration in settings. They may share some settings but
  probably need different configuration shapes.
- Token and spend limits at **multiple layers**: global, per timeframe, per
  project, per model, per provider. He wants a design for how those layers
  compose and where they are set, integrated with existing settings but as a new
  system.
- He does not want to interface only with Ollama. Other local model
  servers/providers too.
- Production and business model, **explicitly undecided**: on web and mobile
  users may have to use their own OpenAI or Anthropic account for embeddings,
  but he does not want that to be something they think or worry about. Options
  he floated: manage embeddings ourselves and not charge, or fold it into a
  blanket fee or subscription. Free versus paid, for both the web product and a
  local distribution, is open. The analogy he used was Docker: a free ubiquitous
  utility with premium paid tiers.
- Hardware reality: not everyone has an NVIDIA GPU, some are on a MacBook,
  management differs, and he does not want to manage that directly.
- He asked whether Anthropic even has an embeddings API. Answered in §3.1.
- "Cheapest, or best cost-for-value and capability" should be configurable.
- There must be a clear split between what an **end user** must configure and
  what is an **advanced** option, and where those settings live.

---

## 2. What is actually there today, measured

Audited 2026-08-23 against the working tree. Read this before proposing
something simpler, because three of the five things the brief assumed are not
quite what is on disk.

### 2.1 The credential store exists and is unreachable

`services/model_providers.py` (210 lines) and `services/model_credentials.py`
(257 lines) are built and correct. Three providers are declared at
`model_providers.py:151`: `claude_cli` (subscription OAuth, `multi_user=False`),
`anthropic_api` and `openai_api` (both `multi_user=True`). Both API-key
providers carry `runtime_ready=False` (`model_providers.py:116` and `:128`), so
the catalog is already honest that nothing can execute a turn on them.
`DEFAULT_PROVIDER_ID` is the CLI (`model_providers.py:155`).

**Confirmed: there is exactly one non-test importer of either module in the
whole tree, and zero HTTP routes and zero frontend files.** The importer is
`services/llm_router.py:108-116`, which calls `model_credentials.is_multi_user()`
and refuses the `claude -p` path in multi-user mode. Nothing calls `resolve()`,
`set_credential()`, `status()` or `available_providers()` outside
`backend/tests/test_model_credentials.py` and `backend/tests/test_secrets_crypto.py`.
`grep -rn model_credentials backend/routes frontend/src` is empty.

So the store is inert in the strongest sense: a key cannot be entered, and if
one were entered it could not be spent, because no runtime reads it.

### 2.2 Encryption at rest is built and dormant

`services/secrets_crypto.py` (181 lines) is envelope encryption, AES-256-GCM
throughout: a fresh 256-bit DEK per write wrapped by a KEK from
`WORKSHOP_SECRETS_KEK` (`secrets_crypto.py:50`), with the slug bound in as AAD
on **both** layers (`secrets_crypto.py:127-128`) so a file cannot be renamed to
impersonate another principal. It is off unless the env var is set
(`secrets_crypto.py:100`), and `secrets_store._write` picks encrypt or plaintext
per write (`secrets_store.py:88-91`), so turning it on strands nothing. An
encrypted file read with no KEK raises rather than reading as empty
(`secrets_store.py:64-65`). `backend/tests/test_secrets_crypto.py` covers it.

One thing to fix when it is used for real: `model_credentials._slug()`
(`model_credentials.py:128-129`) writes to `.runtime/secrets/principal:<id>.json`,
which is the **same flat directory** as per-project app secrets
(`secrets_store._path`, `:32-35`). Every other per-owner store in this tree is
partitioned under `.runtime/users/<owner>/` via `ownership.user_runtime_dir()`
(see `budget_store.py:26-36`). Principal credentials should move there.

### 2.3 Embeddings: verified, local, and the numbers check out

`services/embeddings.py` (132 lines) is exactly as the brief describes. One
in-process ONNX provider through fastembed, `BAAI/bge-small-en-v1.5`, 384-d
(`embeddings.py:32-43`), lazy-loaded and held for process lifetime
(`embeddings.py:64-70`). `WORKSHOP_EMBED_PROVIDER` defaults to `local` and any
other value raises with a message saying hosted providers were deferred
(`embeddings.py:95-103`). No daemon, no key, no network at inference.

Measured on disk, owner 6:

```
.runtime/users/6/semantic/index.model          local:BAAI/bge-small-en-v1.5
.runtime/users/6/semantic/index.meta.jsonl     12,691 lines
.runtime/users/6/semantic/index.vecs.npy       19,493,504 bytes
```

12,691 x 384 x 4 bytes + a 128-byte npy header is 19,493,504 exactly. The index
is one model, one dimension, and self-describing. Two of the three owner
directories hold only an `index.stale` marker, so there is one real index.

**That 384-d width is a design constraint, not a detail.** The same corpus on
OpenAI `text-embedding-3-large` (3072-d) would be 156 MB of float32 instead of
19.5 MB, an 8x storage and scan cost for a few points of retrieval quality.

### 2.4 Limits: what usage is actually keyed and capped by

`services/usage_ledger.py` (618 lines) is a JSONL append log plus a weighted
max-min allocator. Measured:

**The row.** `record()` (`usage_ledger.py:120-153`) writes
`{ts, project, tokens}` plus optional `agent`, `job_id`, `task_id`, `run`,
`role`, `chat`. **There is no `provider`, no `model`, and no currency field.**
Usage is keyed by one string, `project`, which is a project slug for sweeps and
the literal `agent:<slug>` for agent triggers (`scripts/dispatch_triggers.sh:411`
and `:461`), or `unbound`. `tokens` is a single undifferentiated integer, and
for chat compaction it is an **estimate** from a chars/4 proxy, not a billed
figure (`routes/chat.py:2195-2207`).

**The window.** One fixed rolling 5 hours, `WINDOW_S` at `usage_ledger.py:45`,
chosen to match Claude subscription usage windows. `routes/usage.py:22-28` adds
24h/7d/30d/all as **reporting-only** lookbacks with `capped: false`; only the 5h
window has a cap (`usage_ledger.py:455-491`).

**The config.** `_DEFAULT_CONFIG` at `usage_ledger.py:78-92`: `app_total`
2,000,000 tokens, `nominal_task_tokens` 150,000, `autonomy_budget_fraction`
0.65, plus per-project `{weight, floor, ceil}`.

**The admission logic**, `can_admit()` at `usage_ledger.py:521-577`, in order:

1. Window usage is recomputed **excluding** rows whose `role` is `chat` or
   `compaction` (`ADMISSION_EXCLUDED_ROLES`, `usage_ledger.py:68`). Chat is
   accounted but never gated, and never gates. The comment at `:61-67` records
   the measured incident: on 2026-08-22 compaction was 83% of the 3,374,976
   "autonomous" tokens counted against a 3,250,000 allowance, and a long
   conversation folding its own history starved the autonomous loop to a stop.
2. If `app_total <= 0`, admit (uncapped).
3. `reserve = nominal_task_tokens * (1 + in-flight coordinator sweeps)`
   (`:549`). The in-flight count is running Jobs with no `task_id` and no
   `parent_id`, minus the caller's own (`:498-518`), so N concurrent sweeps
   cannot collectively overshoot before any of them bills.
4. `autonomous_ceiling = app_total * autonomy_budget_fraction` (`:554`). Refuse
   if `used + reserve > autonomous_ceiling`. The remaining 35% is interactive
   headroom that autonomous work may never touch.
5. Per-scope hard `ceil` if configured (`:562-564`).
6. Per-scope `allowance` from the weighted max-min fill `plan_budgets()`
   (`:376-441`, floors first, then the remainder in proportion to weight,
   clamped at each ceil). Refuse if the scope has spent it.

**Callers.** Five Python (`services/voice_worker.py:395`,
`services/build_streaming.py:3345`, `routes/jobs.py:280`, `routes/usage.py`,
`routes/chat.py:2200`) and four shell (`scripts/dispatch_triggers.sh`,
`scripts/reviewer.sh:1045`, `scripts/project_ralph.sh:1101`,
`scripts/ralph.sh:1171`). The brief said seven non-test callers; nine is closer,
depending on whether the shell writers count.

**The UI.** `frontend/src/pages/Usage.tsx` (538 lines) reads `/api/usage` for a
selectable window and can edit exactly one knob, `app_total`
(`Usage.tsx:141-143`, `:441-470`). The API accepts a second,
`autonomy_budget_fraction` (`routes/usage.py:126-129`), and **no UI sets it**;
it is env-or-file only (`usage_ledger.py:354-367`).

### 2.5 Model selection today

Six Claude model ids, hardcoded (`chat_context.py:49-57`), sourced from
`llm_router.TIERS` (`llm_router.py:61-68`) so declarations can say `sonnet` and
survive a handle rotation. `normalize_model()` **raises** on anything not in
that list (`chat_context.py:69-85`), which is correct today and is the exact
line a second provider has to change.

Resolution for a chat is `body.model > thread.model > provider.default_model`
(`routes/chat.py:211-232`). `ContextProvider.default_model` is Haiku
(`chat_context.py:1584`), overridden to Sonnet by the build and project
providers (`:1791`, `:1916`). Agents may pin their own model
(`agents_registry.py:232-260`, `_coerce_model`), and app personas resolve theirs
through `llm_router.tier()` (`app_persona.py:57`). Effort resolves against the
settled model and is validated per-model (`model_caps.EFFORT_BY_MODEL`), because
the CLI accepts `--effort` for models that silently ignore it.

Every one of these paths ends at `llm_router._claude_env()`
(`llm_router.py:90-136`), which refuses multi-user outright (`:110-116`), strips
app-owned secret env names (`:126-129`), overlays the auth file, and then pops
`ANTHROPIC_API_KEY` **after** the overlay (`:135`) so a key sitting in that file
cannot bill a subscription install. That function is the single place a second
credential has to arrive.

### 2.6 Five things that contradict the brief

1. **`budget_engine.py` and `budget_store.py` are not model-spend machinery at
   all.** They are the personal-finance planner: Plaid balances, debts, savings
   goals, a monthly allocation waterfall (`budget_engine.py:1-13`,
   `budget_store.py:1-12`, `default_plan()` at `:50-147`). They share the word
   "<slug>" and nothing else. Nothing in this design touches them.
2. **The live index holds 12,691 chunks, not 12,652.** It grew. Same owner, same
   model, same 384 dimensions.
3. **`usage_ledger` has no model or provider dimension whatsoever**, so "what
   dimension usage is keyed and capped by" is: project-slug scope, tokens, one
   rolling 5h window, and role only as an admission filter. Any per-model or
   per-provider limit is new schema, not new config.
4. **`Settings.tsx:101-106` still claims "Ollama stays only as the embeddings
   backend behind the `WORKSHOP_TEXT_EMBED_URL` config seam."** That was true
   before 2026-08-20 and is now false: embeddings are in-process ONNX and
   `WORKSHOP_TEXT_EMBED_URL` is dead. Stale comment, worth deleting when that
   file is next touched.
5. **One live Ollama LLM path remains, as the embeddings doc says.**
   `routes/relay.py:20` still pins `qwen2.5:32b` and proxies to Ollama, mounted
   at `main.py:434` behind a loopback dependency. It is the commute reasoner's
   backend. Consistent with the "not done" list; noted here because it is the
   first thing a real local-LLM provider replaces.

---

## 3. The factual questions

### 3.1 Does Anthropic have an embeddings API? No.

Verified 2026-08-23 by fetching
`https://platform.claude.com/docs/en/build-with-claude/embeddings`. Current
text, verbatim:

> Anthropic does not offer its own embedding model. One embeddings provider that
> has a wide variety of options and capabilities encompassing all of the
> preceding considerations is Voyage AI.

Anthropic has never shipped an embeddings endpoint; there is no `/v1/embeddings`
in the API reference and that docs page has pointed at Voyage since 2023. Voyage
AI was acquired by MongoDB on 2025-02-24 for about $220M, so Anthropic's
recommended embeddings vendor is now a MongoDB subsidiary. The recommendation is
editorial; no commercial partnership was verifiable.

**Do not design around an Anthropic embeddings API. There is not one and there
is no sign of one.** `embeddings-and-local-inference-2026-08-20.md` already says
this and it is still correct.

### 3.2 Hosted embedding options, dimensions, and cost

Price is USD per million input tokens. Verified against vendor pricing pages
2026-08-23.

| Provider / model | Dims (default, truncatable) | Max input | $/1M | Free tier |
|---|---|---|---|---|
| OpenAI `text-embedding-3-small` | 1536, reducible | 8,192 | **0.02** | none |
| OpenAI `text-embedding-3-large` | 3072, reducible | 8,192 | 0.13 | none |
| Voyage `voyage-4-lite` | 1024 / 256 / 512 / 2048 MRL | 32,768 | **0.02** | 200M tokens |
| Voyage `voyage-4` | same | 32,768 | 0.06 | 200M tokens |
| Voyage `voyage-4-large` | same | 32,768 | 0.12 | 200M tokens |
| Voyage `voyage-code-4` | same | 32,768 | 0.12 | 200M tokens |
| Cohere `embed-v4.0` | 1536, also 256/512/1024 | 128,000 | 0.12 | rate-limited trial |
| Google `gemini-embedding-001` | 3072, MRL 128 to 3072 | 2,048 | 0.15 | yes |
| Google `gemini-embedding-2` | 3072, MRL 128 to 3072 | 8,192 | 0.20 | yes |
| Mistral `mistral-embed` | 1024 | 8,192 | 0.10 | none |

Notes. OpenAI has shipped no successor to the 3-series as of 2026-08-23. Voyage
shipped a version 4 generation on 2026-01-15, so the `voyage-3.x` names in the
2026-08-20 doc are now previous-generation; do not write new code against them.
OpenAI, Google and Mistral offer 50% batch discounts.

**Quality.** `bge-small-en-v1.5` benches MTEB v1 English 62.17 average, 51.68
retrieval, at 384-d and 33M params. OpenAI 3-small is 62.3 and 3-large 64.6 on
the same board. Cohere embed-v4 is around 65.2 (secondary source). Gemini's
68.32 and 69.9 are on the multilingual board and are **not** comparable to those
numbers. Voyage publishes no MTEB and claims, on its own RTEB benchmark, +14%
relative over 3-large (vendor benchmark, blog fetch blocked, treat as
unverified). **The honest read: for English retrieval, the bundled local model
is within a few points of the cheap hosted tier.** The frontier gains are in
code, long documents and non-English.

**Cost at this platform's actual scale.** 12,691 chunks at roughly 400 tokens is
about 5.1M tokens to ingest, plus a generous 50,000 queries a month at 30 tokens
is 1.5M a year and a half. Year one, all in:

| Option | Ingest | Per month | Year 1 |
|---|---|---|---|
| local bge-small | 0 | 0 | **$0** |
| OpenAI 3-small | $0.10 | $0.03 | $0.46 |
| voyage-4-lite | $0.10 | $0.03 | **$0** (free tier) |
| OpenAI 3-large | $0.68 | $0.20 | $3.02 |
| gemini-embedding-2 | $1.04 | $0.30 | $4.64 |

**The cost axis is dead.** Every option is under five dollars a year for this
corpus, and a full re-embed after a model change costs about one dollar. So
model lock-in is not a real risk and price is not a real decision variable at
single-operator or small-tenant scale. The real variables are latency, whether
an external dependency and an API key exist at all, and the 8x index size of a
3072-d model. **That is why local stays the default even in the cloud, and why
the operator's instinct that users should not have to think about embeddings is
also the cheap option.** At one million users the arithmetic changes and §10
covers it.

**Recommendation:** keep `local` as the default everywhere. When a hosted
embedding provider is finally built, ship exactly two: OpenAI
`text-embedding-3-small` (because a user who has any key at all has that one)
and Voyage `voyage-4-lite` (same price, 32k context, 200M free tokens). Do not
ship five.

### 3.3 Local model serving beyond Ollama, and the one seam

Verified 2026-08-23. "Anthropic" here means a native `/v1/messages`.

| Runtime | OpenAI `/v1` | `/v1/messages` | `/v1/embeddings` | CUDA | Metal | CPU | ROCm |
|---|---|---|---|---|---|---|---|
| Ollama | yes | yes, since 0.14.0 (16 Jan 2026) | yes | yes | yes (MLX engine preview 0.19) | yes | yes |
| llama.cpp `llama-server` | yes | yes (Jan 2026), plus `count_tokens` | yes | yes | yes | best in class | yes + Vulkan |
| LM Studio (+ `lms` headless) | yes | yes, since 0.4.1 (~30 Jan 2026) | yes | yes | yes, own MLX engine | yes | yes |
| vLLM | yes, plus pooling/rerank | yes, plus `count_tokens` | yes | primary | plugins only | poor | first class |
| SGLang | yes | not found | yes | primary | no | no | yes |
| `mlx_lm.server` | chat + models only | no | **no** | n/a | yes | n/a | n/a |
| Jan | yes | unverified | yes | yes | yes, native MLX since 0.7.7 | yes | likely |
| KoboldCpp | yes | yes, since 1.115.2 | yes | yes | yes | yes | yes |
| LocalAI | yes, broadest | unverified | yes | yes | yes | yes | yes |
| llamafile | inherits llama.cpp | inherits | inherits | yes | yes | yes | yes |
| GPT4All | stalled since Feb 2025. Do not build on it. | | | | | | |

**The one integration seam that covers most of them is an HTTP base URL plus a
declared wire format**, and as of 2026 that is two formats, not one. Every
runtime above speaks OpenAI `/v1`. The three that matter most for a desktop
install also speak Anthropic Messages, and they all shipped it within two weeks
of each other in January 2026 for exactly this platform's use case: point
`ANTHROPIC_BASE_URL` at localhost and run a Claude-Code-shaped agent loop with
no proxy.

**What OpenAI-compatible `/v1` fails to cover**, concretely, and why this
platform in particular cares:

- **Prompt caching.** No request field exists. There is no way to place or
  observe a cache breakpoint. `routes/chat.py:235-270` already reasons about
  prompt-cache prefixes when deciding whether to emit `--effort`; that whole
  concern is inexpressible over Chat Completions.
- **Reasoning and thinking.** No standard field. Ollama uses `thinking` plus
  `reasoning_effort`; vLLM and SGLang use a `reasoning_content` convention.
  Anthropic Messages has typed `thinking` blocks with `budget_tokens` and
  signatures preserved across turns. The effort ladder in `model_caps.py` maps
  onto that and onto nothing in the OpenAI shape.
- **Tool calls.** `function.arguments` is a JSON **string** you parse yourself;
  Anthropic gives already-parsed JSON in `input`. `tool_choice` is unsupported
  in Ollama. Streaming tool-call deltas (index-keyed partial JSON) are the most
  commonly broken thing across runtimes.
- **Typed content blocks.** `tool_use` / `tool_result` / `thinking` /
  `document` versus flat strings plus a side channel.
- **Token counting.** OpenAI has no count endpoint. Anthropic does, and
  llama.cpp and vLLM implement it. `routes/chat.py:2195-2207` currently
  estimates compaction spend at chars/4 precisely because it has no counter.
- **Capability and context discovery.** `/v1/models` returns ids and nothing
  else: no context length, no quantisation, no tools/vision/embedding flags.
  Every runtime invented its own richer route (`/props`, `/api/show`,
  `/api/v0/models`). **This is the genuinely unstandardised gap** and it must be
  a per-runtime probe, not an assumption. A too-small default context is the
  number one failure mode for agent loops.
- **GBNF grammars and logprobs.** Not in the schema at all; Ollama silently
  drops `logprobs`.

**Apple Silicon versus NVIDIA, concretely.** Third-party aggregate figures, not
measured here. A 7B/8B at Q4 runs about 12 to 13 tok/s on an M1 16GB, 20 to 25
on an M3, 45 on an M4 Pro, 91 to 102 on an M5 Max. A 30B MoE at Q4 (Qwen3
30B-A3B class) runs about 35 tok/s on an M4 Pro 24GB and 62 on an M5 Max 64GB;
MoE sparsity is what turns unified memory capacity into usable speed, and it is
the sweet spot on a Mac. A consumer RTX 5090 32GB does about 40 to 55 tok/s on
the same 30B Q4 single-stream, so it is comparable interactively; NVIDIA's real
wins are prefill and concurrency, and the headline thousands-of-tokens-per-second
numbers are batched aggregate throughput under vLLM, not what one user feels.
The Mac's win is capacity: 128GB unified runs models a 32GB card cannot load.
MLX is now the default recommendation on Mac but it is not a category
difference: reported 20 to 87% faster under about 14B, converging above 27B
where memory bandwidth dominates, and at least one M4 Max test has llama.cpp
winning on Llama-3-8B Q4.

**The practical floor for agentic work is a 30B-class MoE at 4-bit on 32GB of
unified memory or a 24GB+ card.** Dense 7 to 8B models chat fine and drop or
mangle tool calls. This is the fact that makes "fully local" a real deployment
target for the operator's machine and a bad default for a stranger's laptop.

---

## 4. The provider model

### 4.1 Two interfaces, and they are not symmetrical

```
class LlmProvider(Protocol):
    id: str                 # "anthropic_api", "local_llamacpp", "claude_cli"
    transport: str          # "http" | "cli_subprocess"
    wire: str               # "anthropic_messages" | "openai_chat"
    base_url: str | None    # None for cli_subprocess
    auth_mode: str          # AUTH_API_KEY | AUTH_SUBSCRIPTION | AUTH_PLATFORM | AUTH_NONE
    def models() -> list[ModelInfo]      # catalog or probe
    def run_turn(choice, messages, tools, env) -> TurnResult

class EmbeddingProvider(Protocol):       # already exists, embeddings.py
    name: str                            # "local:BAAI/bge-small-en-v1.5"
    dim: int
    def embed_documents(texts) -> list[list[float]]
    def embed_query(text) -> list[float]
```

The embedding interface is already shipped and correct; the only change it needs
is a second and third implementation and a config shape (§4.3). **The LLM
interface is the new work**, and the reason it is not just "a base URL" is
`transport`.

`claude_cli` is not an HTTP endpoint. It is a subprocess where tools, permission
modes and effort are **CLI flags** built by `llm_router.tool_flags()` and
`_build_cmd`, not API fields. Any design that models it as "an Anthropic
endpoint with a different base URL" will discover this at the worst moment. So
`transport` is a first-class axis with exactly two values, and today exactly one
provider uses `cli_subprocess`.

### 4.2 Local versus cloud is configuration shape, not interface

They differ in four fields and nothing else:

| | Cloud provider | Local provider |
|---|---|---|
| Endpoint | implied by provider id | **user-supplied `base_url`**, e.g. `http://127.0.0.1:11434/v1` |
| Auth | API key, `looks_like_key()` shape check, verify before store | none |
| Model list | static catalog with prices | **probed** from `/v1/models`, plus a runtime-specific capability probe |
| Health | assume up, handle 429/5xx | **reachability is a real state**: unreachable is the normal case, not an error |
| Cost | dollars per million tokens | zero dollars, non-zero latency and memory |

Two consequences the settings design has to honour. First, a local provider
needs an "is it there right now" probe and a visible not-configured state, which
a cloud provider does not. Second, a local provider's model list is **discovered
and unstable**, so a pinned local model id can vanish between restarts; the
resolver must degrade to the deployment default with a named reason rather than
failing the turn.

### 4.3 Is OpenAI-compatible the right universal adapter? No, it is the floor

**Recommendation: implement `anthropic_messages` first and `openai_chat`
second, and record on each provider which capabilities are lost.** Reasons, in
order of weight:

1. This platform's turn model **is** the Anthropic one. Effort ladders
   (`model_caps.EFFORT_BY_MODEL`), permission modes, typed thinking, tool_use
   blocks and the streaming event parser
   (`llm_router._extract_segments_from_stream_json`, `:187`) are all shaped by
   it. Translating out to Chat Completions and back loses information at both
   ends.
2. As of January 2026 the three local runtimes a desktop install would actually
   bundle all serve `/v1/messages` natively. The thing that used to force
   OpenAI-compat is gone.
3. `openai_chat` is still required, because vLLM-on-a-server, SGLang, and every
   hosted non-Anthropic API speak it and some speak nothing else.

What `openai_chat` costs, declared per provider so the UI can say it: no cache
control, no typed thinking with budget, no token counting, tool arguments as
strings, and no capability discovery. A provider record carries
`capabilities: {cache: bool, thinking: bool, tools: bool, vision: bool,
count_tokens: bool, context_tokens: int}`, populated from the catalog for cloud
and from a probe for local. **Nothing may infer a capability from the wire
format alone**, because Ollama's Messages implementation lacks `count_tokens`,
`cache_control` and `tool_choice`, and llama.cpp makes no strong claim of full
spec compatibility.

Two things explicitly **not** recommended: LiteLLM or a similar translation
layer as a dependency (it moves the two-format problem into a third party's
release cycle and this platform already has the Anthropic path natively), and
Ollama's native `/api/*` as a target (nothing else implements it).

---

## 5. Credentials

### 5.1 Where keys live and how they are protected

Keep the existing store. A principal's credential is one key in
`secrets_store` under the slug `principal:<id>` (`model_credentials.py:128-135`),
encrypted by `secrets_crypto` when a KEK is configured. Three changes:

1. **Move the file** from the flat `.runtime/secrets/` directory into
   `.runtime/users/<id>/credentials.json`, matching every other per-owner store
   (`budget_store.py:26`). Today a principal's credentials sit in the same
   namespace as per-project app secrets, which is an accident waiting for a slug
   collision.
2. **Make the KEK mandatory in multi-user mode.** `deployment_mode()` already
   fails closed to multi-user on an unrecognised value
   (`model_credentials.py:109-121`). Add the matching boot check: if
   `is_multi_user()` and not `secrets_crypto.enabled()`, refuse to start. A pile
   of other people's API keys in plaintext on a hosted box is the failure this
   was built to prevent, and leaving it opt-in means it is off.
3. **Verify before store**, the pattern already shipped for tracker tokens
   (`routes/trackers.py:108-121`: `verify()` then `set_token()`, write-only,
   presence-only on the way out). Copy it exactly. `looks_like_key()`
   (`model_providers.py:183-197`) is the cheap paste-error check that runs
   first; a real call is the second.

### 5.2 Scoping: a key has exactly one owner

**A credential is scoped to a principal and to nothing else.** Projects, agents
and chats hold a *selection* (`provider_id` plus `model_id`), never a key. This
is not a simplification, it is the licensing rule: `resolve()` refuses to return
a credential belonging to a different principal and never falls back
(`model_credentials.py:217-257`), and a per-project key would be a second copy
with no principal attached, which is exactly the fallback that was designed out.

Where per-scope selection lives: an agent already declares a model
(`agents_registry.py:232`), and that field grows a sibling `provider`. A project
or app default is a pair on the app record. A chat's pin is on the thread. None
of them store a secret.

### 5.3 Enforcing the subscription rule at the UI layer

The rule is data in `model_providers.py:102` (`multi_user=False` on
`claude_cli`) and enforced twice in the backend: `permitted()` refuses to store
a prohibited credential at all (`model_credentials.py:156-170`) and
`_claude_env()` refuses to spend one (`llm_router.py:110-116`). The UI layer
adds one rule and one only:

**In multi-user mode, a prohibited provider renders as nothing. Not a disabled
control, not a greyed toggle, not a "coming soon".** `available_providers()`
already omits it from the payload (`model_credentials.py:144-153`) with the
reason written in the docstring: offering a control that cannot legally work
invites someone to ask for it to be enabled. The frontend must therefore render
**only** what `GET /model-providers` returns and must not carry its own list of
providers. If a user arrives expecting it (from a blog post, from the desktop
build), the empty state links to the API-key path and quotes
`provider.restriction` (`model_providers.py:90-95`), which is written to be
shown to a person.

In single-operator mode the subscription provider appears, because it is the
operator's own subscription and ordinary first-party use.

---

## 6. Selection and resolution

One function, one return type, used by chats, agents and background jobs alike:

```
ModelChoice = {provider_id, model_id, wire, effort, permission_mode, source}
resolve_model(principal_id, scope, request) -> ModelChoice
```

`source` names which rung won, so an operator can see why a turn ran where it
ran. Resolution order, most specific first:

1. **Explicit request.** `body.model` today (`routes/chat.py:219`), extended to
   a `provider:model` pair. An invalid explicit value is a 400, as now.
2. **The thread or job pin.** `thread.model` (`routes/chat.py:225-230`), or the
   Job's recorded choice for a background run.
3. **The agent declaration.** `agents_registry._coerce_model`
   (`agents_registry.py:232-260`), which already pins a model per agent and
   already tolerates a model this build cannot pick by warning and falling
   through (`routes/chat.py:3258-3270`). Keep that tolerance: it is what lets a
   local model id vanish without breaking a turn.
4. **The app or project default.** New. Today the nearest thing is
   `ContextProvider.default_model` per provider class
   (`chat_context.py:1584`, `:1791`, `:1916`), which is a code constant. It
   becomes a per-app field with those constants as seed values.
5. **The principal default.** New, and this is the one an end user sets: "which
   model do my chats use". Stored beside the existing operator preferences
   (`chat_store.get_default_effort`, `get_default_permission_mode`).
6. **The deployment default.** `model_providers.DEFAULT_PROVIDER_ID`
   (`:155`) plus a default model id.

Then, and only then, effort resolves against the settled model, unchanged
(`routes/chat.py:235-270`), because which rungs exist is a fact about the model
and validating it earlier is wrong.

**What has to change to make this real:** `chat_context.normalize_model()`
(`:69-85`) currently raises for anything outside the six hardcoded Claude ids.
It becomes a lookup against the configured providers' catalogs. `PICKABLE_MODELS`
(`:49-57`) stops being a list and becomes a function of what is configured, and
`describe_provider()` (`:2340-2358`) already returns `models` and `default_model`
per provider to the composer, so the picker's payload shape survives intact.
That is the smallest possible blast radius for adding a second vendor.

**Do not add a second resolver.** There is one enforcement point for tools today
(`llm_router.tool_flags()`, per `tools-are-declared-2026-08-23.md` §1) and the
same discipline applies here: one resolver, or the answer to "which model ran"
depends on which caller you ask.

---

## 7. Limits

### 7.1 Extend `usage_ledger`. Do not build a second budget system.

Stated plainly because the brief asked for it: **`usage_ledger` is extended, not
replaced, and no parallel budget engine is created.**

The reason is measured, not aesthetic. The one recorded failure of this
subsystem (`usage_ledger.py:61-67`) was a cost attributed to the wrong budget:
compaction billed to the autonomous ceiling, 83% of the window, and the
autonomous loop silently did nothing for hours. A second accounting system with
its own rows and its own windows is the same class of bug with more surface. One
ledger, one admission call, more dimensions on the row.

(`budget_engine.py` and `budget_store.py` are the personal-finance planner and
are unrelated. See §2.6.)

### 7.2 The row grows three fields

```
{ts, project, tokens, role, agent, job_id, task_id, run, chat,
 provider,        # NEW: "anthropic_api" | "claude_cli" | "local_llamacpp"
 model,           # NEW: the resolved model id
 micro_usd}       # NEW: integer micro-dollars, 0 for local and for subscription
```

`micro_usd` is an integer so it never accumulates float error, and it is **0,
not null, for a local or subscription turn**, because those genuinely cost no
marginal dollars and a null would make every sum ambiguous. Tokens remain the
universal unit; dollars are the unit that only exists when a metered provider
served the turn.

Backfill is unnecessary: `_read_entries()` already tolerates rows missing any
optional field (`usage_ledger.py:156-168`), and `prune()` drops rows past the
window anyway (`:289-305`).

### 7.3 Limit rules, and how the five layers compose

Replace the two-knob config with a list. The current config becomes exactly two
rules, so the default behaviour is byte-identical on day one.

```
{ "rules": [
    {"id":"autonomy",  "scope":{"kind":"deployment"}, "window":"rolling_5h",
     "unit":"tokens", "amount":2000000, "fraction":0.65,
     "applies_to_roles":"autonomous", "reserve":"nominal_task",
     "on_breach":"refuse"},
    {"id":"project-x", "scope":{"kind":"project","id":"<slug>"},
     "window":"rolling_5h", "unit":"tokens",
     "weight":1.0, "floor":0, "ceil":null, "on_breach":"refuse"},
    {"id":"month-cap", "scope":{"kind":"deployment"}, "window":"calendar_month",
     "unit":"usd", "amount":50.00, "on_breach":"refuse"},
    {"id":"opus-cap",  "scope":{"kind":"model","id":"claude-opus-5"},
     "window":"calendar_day", "unit":"usd", "amount":5.00,
     "on_breach":"degrade", "degrade_to":"claude-sonnet-5"}
] }
```

`scope.kind` is a closed enum: `deployment`, `provider`, `project`, `app`,
`agent`, `model`, `principal`. `window` is a closed enum: `rolling_5h`,
`calendar_day`, `calendar_week`, `calendar_month`. `unit` is `tokens` or `usd`.

**Why `calendar_month` has to exist.** The current 5h window was chosen to match
Claude **subscription** usage windows (`usage_ledger.py:45`). That is the right
window for a credential that resets every five hours and the wrong one for an
API key that bills monthly. The window is a property of how the credential is
metered, so it belongs on the rule, not on the module.

**Evaluation order.** All rules that match must pass; the first refusal wins and
is reported by rule id. Match order is most specific first, purely so the
reported reason is the useful one:

`model > agent > app/project > provider > principal > deployment`

**On breach**, three behaviours and only three:

- `refuse` (default, and today's only behaviour). Autonomous work is refused
  with a named reason, exactly as `can_admit()` does now
  (`usage_ledger.py:555-576`).
- `degrade`. Re-resolve to a cheaper model within the same provider and run.
  **Opt-in per rule and never the default**, because a turn that silently ran on
  a weaker model and produced worse work is harder to debug than a turn that
  refused.
- `warn`. Record and surface, do not block. This is what interactive chat gets,
  because chat is declared unmetered and gating it was the 2026-08-22 bug.

**Nothing queues.** A queued turn is a refusal that lies about when it will run.
Autonomous work already re-checks admission on the next sweep, which is a queue
with an honest name.

> **CORRECTED 2026-08-26.** The paragraph above is right about a *queue* and was
> being read as forbidding a *pause*, which is a different thing.
> `backlog-reconciliation-2026-08-24.md` §1.2 ruled that "a pause is resumable
> work and is not a queue entry", and the operator settled the same point again
> on 2026-08-25 by choosing pause-at-cap over fail-on-cap, on the grounds that a
> paused run has committed its work and resumes with `SESSION_ID`, so nothing is
> lost. A refusal still does not queue. A run that hits the cap mid-flight stops,
> commits, and is resumable; it is not holding a promise about when it will run.
> Ticket `70860d58` builds that and is not in conflict with this section. The
> 2026-08-24 correction was ordered and never applied, which is why an urgent
> ticket appeared to contradict its own design doc for two days.

### 7.4 How this composes with the existing reserve-fraction admission

The `autonomy` rule above **is** today's admission, unchanged: the deployment
rule with `fraction: 0.65`, `applies_to_roles: autonomous` (which is the
existing `ADMISSION_EXCLUDED_ROLES` inverted), and
`reserve: nominal_task` (the `nominal * (1 + in-flight sweeps)` headroom at
`usage_ledger.py:549`). `can_admit()` keeps its signature and gains a loop over
matching rules. The weighted max-min allocator `plan_budgets()`
(`:376-441`) keeps running for `project`-kind rules and is where `weight`,
`floor` and `ceil` continue to live.

**The reserve concept generalises to dollars unchanged**: reserve a nominal
turn's estimated cost rather than a nominal turn's tokens. That estimate needs a
price table per model, which is the same table `micro_usd` is computed from, so
it is one new piece of data serving both.

**The one thing that must not regress:** interactive chat is accounted and never
gated. That is a rule-level property now (`applies_to_roles`), and the
regression test is already written (`test_chat_compact.py:287-303`).

---

## 8. Settings information architecture

### 8.1 What exists

Settings is a place with one page per section, six tabs, one route each
(`Settings.tsx:45-59`, `App.tsx:171-176`): Account, Appearance, Chat, Memory,
Apps, Agents. Four more pages hang off `/settings/*` without being tabs (Grants,
Trackers, MCP servers, Tools, Custom instructions;
`App.tsx:140-144`). `Usage.tsx` is a top-level page at `/usage`, not under
Settings, and it currently owns the only limits control in the product.

### 8.2 What to add: one tab, four sections

**`/settings/models`, titled "Models".** Seventh entry in `SettingsTab`
(`Settings.tsx:45`), seventh route (`App.tsx:171`).

**Section 1: Models.** *End user.* Two controls.
- "Default model for chats" - one picker, the same list the composer shows.
- "Default effort" - already exists as an operator preference
  (`chat_store.get_default_effort`), currently reachable only from the composer.
  It belongs here too.
- Below, a read-only table of overrides that are set elsewhere (this agent, this
  app, this chat) with a link to each, so "why did that turn use Opus" has one
  place to answer it. Read-only on purpose: the override belongs to the thing it
  overrides.

**Section 2: Providers and keys.** *End user.* One card per provider from
`GET /model-providers`, rendered from the payload and never from a client-side
list (§5.3). Each card: label, presence badge ("Key added" / "Not configured"),
a paste field that verifies before storing, and a Remove action behind
`ConfirmDialog`. Presence only, never a value, never a masked prefix. Providers
the deployment may not offer do not appear.

**Section 3: Local models.** *Advanced, behind one disclosure.* Empty by default
with a one-line explanation, not a wall of fields. Add-endpoint form: a name, a
base URL, a wire-format select defaulting to Anthropic Messages, and a **Test**
button that probes reachability and lists discovered models. Once added, a row
per endpoint with a live reachable/unreachable state, the discovered model list,
and the probed context length. This is the section that must not leak into the
default view: an end user who has never heard of llama.cpp should not see a port
number.

**Section 4: Limits.** *Mixed.* Default view is one control, the same one that
exists today: the app-wide token cap. Behind Advanced: the rule table from §7.3,
add/edit/delete, with the unit toggle (tokens or dollars), the window select,
and the on-breach select. The autonomy fraction gets a control here for the
first time (it has an API and no UI today, §2.4).

### 8.3 What moves, and what does not

**The cap editor moves out of `Usage.tsx`** (`:441-470`) into Settings, leaving
Usage as a pure reporting surface. Intent is configured in Settings; consumption
is measured in Usage; a link connects them. Usage gains two columns from the new
row fields, provider and cost, and a "By model" view alongside its existing
"By app" and "By chat" views (`Usage.tsx:98`).

**Embedding configuration does not get a control at all**, in the default view
or the advanced one, until a hosted provider exists. It is one env var
(`WORKSHOP_EMBED_PROVIDER`), it has one legal value, and the operator's own
stated goal is that nobody thinks about embeddings. When a hosted provider is
built it becomes a single select under Advanced in Section 3, with the re-embed
cost stated on the control, because switching is a full re-index
(`embeddings.py:13-17`).

**The stale Ollama comment at `Settings.tsx:101-106` gets deleted** whenever
that file is next touched (§2.6).

---

## 9. The deployment matrix

| | Fully-local desktop | Self-hosted | Hosted multi-user |
|---|---|---|---|
| Deployment mode | `single_operator` | `single_operator` | `multi_user` |
| LLM | local endpoint, 30B-class MoE at 4-bit | `claude_cli` **or** own API key | BYO API key only, or platform tier (§10) |
| Embeddings | bundled local ONNX | bundled local ONNX | bundled local ONNX, platform pays (which is zero) |
| Credentials configured by user | none | one key, or `claude auth login` | one key, mandatory |
| Encryption at rest | not required (0600 in own home) | not required | **required at boot** (§5.1) |
| Limits that matter | none, spend is zero | dollars per calendar month | per-principal quota, and it is the whole feature |
| Bundled | ONNX model (~32MB int8), no LLM weights | nothing | nothing |
| Who pays for embeddings | nobody | nobody | nobody |
| Who pays for the LLM | nobody, hardware only | the operator | the user, via their own key |
| What breaks | **reasoning quality.** Tool-call reliability below a 30B MoE; no prompt caching; no `count_tokens`; the effort ladder is meaningless | nothing structural | **execution isolation**, not credentials |

Three notes the table cannot carry.

**Fully-local is a real target and a bad default.** It works on the operator's
own hardware and on any 32GB+ Mac or 24GB+ card, and it does not work on a
2020 laptop. So the desktop build ships local embeddings as the default (32MB,
in-process, works everywhere) and local **LLM** as an opt-in that names its
hardware requirement before the user commits to a download. The operator said he
does not want to manage the hardware difference; the way not to manage it is to
probe once, state the requirement, and let the user choose, rather than to
auto-select a model that will disappoint them.

**Hosted multi-user is still blocked on the thing the 2026-08-11 doc named.**
Credentials are stages 1 to 4; per-user execution isolation for agentic turns
with shell access is the actual gate
(`model-access-and-byok-2026-08-11.md` §6). Nothing in this design changes that,
and a working BYOK screen must not be read as the door being open.

**Self-hosted is the deployment this design most improves today**, because it is
the one where an operator has an API key, wants a monthly dollar cap, and
currently has neither a place to put the key nor a way to express the cap.

---

## 10. Business model: OPEN, not decided

The operator has not decided this and this document does not decide it. What
follows is the option set and what each one forces technically, so that whoever
decides can see the cost.

**The framing he used** was Docker: a free ubiquitous utility with premium paid
tiers. That framing has a specific consequence worth naming: Docker's free tier
is free because running it costs Docker nothing, the user's own machine does the
work. The equivalent here is the fully-local deployment, and it is the only tier
that is actually free to serve.

**On embeddings specifically, the arithmetic makes this easy and it should be
taken off the table.** At the measured corpus size the entire hosted cost is
under five dollars per user per year (§3.2), and the local provider makes it
zero. **Recommendation, and this one is cheap enough to make now: never ask a
user for an embedding key.** Bundle the local model, absorb the nothing it
costs, and let "embeddings" be a word users never see. That satisfies the
operator's stated goal directly and closes the only sub-question here that has a
clear answer.

**The open question is the LLM tier**, and the options are:

**(A) BYOK only, free product.** The user brings an API key. Costs the platform
nothing per turn, needs no metering beyond what §7 builds, and is the only
option available today because it is the only one that is licensed and built.
Consequence: a hard onboarding wall. The user must have a Console account and a
key before the product does anything, which is a real conversion cost, and it is
the wall the operator's "should not have to think about it" instinct is pushing
against.

**(B) BYOK plus a platform trial tier.** The platform's own commercial API
account backs a small per-principal quota so someone can start with no key.
Permitted by Anthropic Commercial Terms §A.1 and bounded by §D.4's prohibition
on reselling the Services as such (`model_providers.py:132-149` records both,
and marks where the provider entry plugs in). Consequence: **the quota is the
whole feature**, and it needs exactly what §7 builds, per-principal dollar
accounting with a hard refuse. This is why stage 7 is sequenced after the limits
work rather than beside it.

**(C) Subscription with model access folded in.** A blanket fee that includes
some amount of platform-served model use. Same technical requirement as (B) plus
billing. Consequence: the §D.4 line matters. Selling a product with a model
inside it is fine; selling metered model access is not, so the pricing cannot be
denominated in tokens and the product has to be worth the fee without them.

**(D) Free local, paid cloud.** The Docker shape most literally: the desktop
build is free and complete, the hosted product is paid. Consequence: it makes
the local LLM path a **product commitment**, not a nice-to-have, which means
owning the hardware-variance problem the operator said he does not want to
manage.

**What is genuinely undecided and blocks work:** whether (B) or (C) exists at
all. Everything in §11 stages 1 to 5 is needed under every option and should
proceed. Stages 6 and 7 are blocked.

---

## 11. Staged plan

Each stage is independently shippable and leaves the tree working. Stage 1 is
the smallest thing that makes the existing inert credential store reachable.

**Stage 1. Make the store reachable. Not blocked.**
A read-only route and a settings section. `GET /model-providers` returning
`model_credentials.status(principal_id)` (presence and readiness booleans, never
values, `model_credentials.py:187-202`), `PUT /model-providers/{id}/credential`
(verify then store, copying `routes/trackers.py:108-121`), `DELETE` to clear.
Section 2 of `/settings/models` renders only what the API returns. Nothing spends
the key yet; `runtime_ready=False` is displayed honestly. This is stage 3 of the
2026-08-11 plan, unchanged, and it is small: the backend already exists.

**Stage 2. Move principal credentials under the owner partition, and make the
KEK mandatory in multi-user mode. Not blocked.**
`.runtime/users/<id>/credentials.json`, plus the boot check from §5.1. Pure
correctness, no user-visible change in single-operator mode.

**Stage 3. Ledger dimensions. Not blocked.**
Add `provider`, `model`, `micro_usd` to the row and a per-model price table.
Populate from the resolver at every existing call site (five Python, four
shell). Usage gains a "By model" view and a cost column. No behaviour change to
admission. **This is the stage that makes every later limit expressible**, and
it is worth doing on its own merits: today the product cannot answer "what did
Opus cost me this month".

**Stage 4. The provider and resolution seam. Not blocked.**
`LlmProvider` with `transport` and `wire`; `claude_cli` becomes the first
implementation with no behaviour change; `resolve_model()` replaces the ad-hoc
chain at `routes/chat.py:211`; `normalize_model()` and `PICKABLE_MODELS` become
functions of the configured catalog. Still one provider, so still nothing new
runs. This is the stage that costs the most and shows the least, and skipping it
is how the two-provider version becomes a rewrite.

**Stage 5. The second and third providers. Not blocked.**
`anthropic_api` over HTTP `anthropic_messages` (flip `runtime_ready` to True),
then a local HTTP provider with a user-supplied base URL, a reachability probe,
and model discovery. Section 3 of `/settings/models`. **This is the stage that
delivers what the operator asked for**, and it is fifth because stages 3 and 4
are what make it a configuration change rather than a fork of the turn path.
`openai_chat` follows as a fourth provider once the two-wire abstraction has one
real user.

**Stage 6. The limits rule system. Not blocked technically, but see below.**
Rules table, evaluation order, `degrade` and `warn` behaviours, Section 4 of
`/settings/models`, cap editor moves out of `Usage.tsx`. Today's config becomes
two rules with identical behaviour. **Partially business-blocked**: the rule
engine itself is needed under every business model, but *which* rules ship as
defaults (a monthly dollar cap? a per-principal quota?) depends on §10, so build
the engine and ship no new default rules.

**Stage 7. The platform default tier. BLOCKED on the business decision (§10 B
or C).**
One `Provider` entry with `auth_mode=AUTH_PLATFORM` whose credential resolves
from the deployment's own account, plus a per-principal quota rule from stage 6.
The plug point is already marked in code
(`model_providers.py:132-149`). Four lines of provider and a whole product
decision behind it. Do not build speculatively.

**Stage 8. Hosted embedding providers. BLOCKED, and probably should never
unblock.**
Two implementations behind the existing `EmbeddingProvider` interface and one
select under Advanced. §3.2 says the cost axis is dead and §10 says never ask a
user for an embedding key. This stage exists in the plan so that the decision to
skip it is a decision and not an omission.

**Not in this plan, deliberately:** replacing `/relay/local` and the commute
reasoner's Ollama dependency (`routes/relay.py:20`). Stage 5 gives it a real
local provider to move to, but that cut is owned by the embeddings doc's
outstanding list and needs verification against a live commute brief.

---

## 12. What this document does not settle

- **The business model.** §10, open, blocks stages 7 and 8.
- **Execution isolation for multi-user.** Named as the real gate in
  `model-access-and-byok-2026-08-11.md` §6 and unchanged by anything here.
- **Whether the desktop build ships a local LLM at all**, as opposed to
  supporting one the user already runs. That is a distribution decision (model
  weights are gigabytes) and it interacts with §10 option D.
- **Price table maintenance.** Stage 3 needs per-model prices, and prices move.
  Whether that table is vendored, fetched, or operator-edited is unresolved; the
  smallest honest version is vendored with a visible "as of" date.

## Sources

- Anthropic, Embeddings: https://platform.claude.com/docs/en/build-with-claude/embeddings
- Claude Code, Legal and compliance: https://code.claude.com/docs/en/legal-and-compliance
- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- Voyage AI pricing: https://docs.voyageai.com/docs/pricing
- Cohere Embed: https://docs.cohere.com/docs/cohere-embed
- Gemini embeddings and pricing: https://ai.google.dev/gemini-api/docs/embeddings
- MongoDB acquires Voyage AI (2025-02-24): https://investors.mongodb.com/news-releases/news-release-details/mongodb-announces-acquisition-voyage-ai-enable-organizations
- Ollama OpenAI compatibility: https://docs.ollama.com/api/openai-compatibility
- Ollama Anthropic compatibility: https://docs.ollama.com/api/anthropic-compatibility
- llama.cpp server: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- llama.cpp Anthropic Messages: https://huggingface.co/blog/ggml-org/anthropic-messages-api-in-llamacpp
- LM Studio Anthropic compatibility: https://lmstudio.ai/docs/developer/anthropic-compat
- LM Studio headless mode: https://lmstudio.ai/docs/developer/core/headless
- vLLM online serving: https://docs.vllm.ai/en/latest/serving/online_serving/
- mlx-lm server: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
