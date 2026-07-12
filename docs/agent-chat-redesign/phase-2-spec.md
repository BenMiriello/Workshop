# Phase 2 build spec — knowledge scopes + Letta memory engine + recall + grants

Authoritative implementation spec for Phase 2 of the redesign (`plan.md`). Grounded in the real code
and a validated probe of the running Letta server (v0.16.8). Build starts after Phase 1 (committed).

## What Phase 2 ships
A per-scope knowledge/memory layer: plaintext markdown files are the source of truth (git-tracked,
inspectable), synced into per-scope Letta archives for semantic **recall** (real vectors, local
embeddings). Chat providers get ambient in-scope knowledge + intent-gated cross-scope recall governed
by the existing sensitivity + grants model. A clean KnowledgeStore interface so a notes app can plug
in. Operator confirmed: store BOTH plaintext AND vectors.

Out of scope (later): Phase 3 context-compaction/distillation (only the distillation write-path
depends on this), a rich knowledge-browser UI (a minimal API is enough here), SKILL.md skills.

## VALIDATED Letta REST contract (probed 2026-07-11, use httpx, NOT the shadowed SDK)
The backend already talks to Letta over raw httpx `/v1` (see `services/global_memory.py`,
`letta_legacy_client.py`, `supervisor.py`). Do the same. Auth header:
`Authorization: Bearer <LETTA_SERVER_PASS or "workshop-local">`. Base `LETTA_URL`
(default `http://127.0.0.1:8283`).

- **Embedding config (local, no external API):** the server embeds via Ollama `nomic-embed-text:latest`
  (768-dim) at `http://host.docker.internal:11434/v1`. Get a known-good `embedding_config` object from
  an existing agent: `GET /v1/agents/?limit=1` -> `[0].embedding_config`. Fall back to this literal if
  there are no agents:
  ```json
  {"embedding_endpoint_type":"openai","embedding_endpoint":"http://host.docker.internal:11434/v1",
   "embedding_model":"nomic-embed-text:latest","embedding_dim":768,"embedding_chunk_size":300,"batch_size":32}
  ```
- **Create archive (MUST pass embedding_config, else passages are never embedded and search returns []):**
  `POST /v1/archives/` body `{"name": "...", "embedding_config": <config>, "description": "..."}` -> `{id, ...}`.
- **List archives:** `GET /v1/archives/`. **Get/patch/delete:** `.../{archive_id}`.
- **Insert passage:** `POST /v1/archives/{archive_id}/passages` body `{"text": "...", "tags": ["<note-id>"], "metadata": {...}}` -> `[{id, text, ...}]`.
  **Batch:** `POST /v1/archives/{archive_id}/passages/batch` body `{"passages": [{text, tags, metadata}, ...]}`.
- **Delete passage:** `DELETE /v1/archives/{archive_id}/passages/{passage_id}`. (There is NO GET-list
  of an archive's passages; track passage ids yourself, keyed by note id, in the sync map.)
- **Search (scope-scoped is fast, ~2s incl. embedding; NEVER do org-wide search, it hangs):**
  `POST /v1/passages/search` body `{"query": "...", "archive_id": "<id>", "limit": N}` ->
  `[{"passage": {"id","text","tags","metadata", ...}, "score"?}]`. Read text via `item["passage"]["text"]`.
  Supports `tags` + `tag_match_mode` filters and `start_date/end_date`.
- **Attach archive to an agent (optional, later):** `PATCH /v1/agents/{agent_id}/archives/attach/{archive_id}`.

## Scope model
`scope_key` (string), one of: `workshop`, `project:<slug>`, `app:<name>`, `agent:<id>`.
Map from a chat `context_key`:
- `general`, `new-project` -> `workshop`
- `project:<slug>` and `build:<slug>` -> `project:<slug>` (a build agent linked to a project shares the
  project scope; a freestanding build agent with no project -> `agent:<slug>`)
- `budget`/`finance`/`commute`/`location` (app providers) -> `app:<key>`
Put this mapping in ONE function `knowledge.scope_for_context(context_key)` and document it.

## Knowledge dirs (plaintext = source of truth)
- `workshop` -> `shared/knowledge/` (git-tracked; the operator's baseline notes)
- `project:<slug>` -> `projects/<slug>/knowledge/` (lives in the project's own git repo; projects/* is
  gitignored at the monorepo level, which is correct)
- `app:<name>` -> `apps/<name>/knowledge/`
- `agent:<id>` -> `.runtime/agents/<id>/knowledge/`
Each scope dir has `MEMORY.md` (the index: one `- [title](note-id.md) — one-line hook` per note) plus
`<note-id>.md` files (the note bodies). `note-id` is a validated kebab/hex slug (no path traversal).

## Part A — `services/knowledge.py` (the KnowledgeStore)
The single source of truth for scope <-> dir <-> archive and all note IO.

Functions:
- `scope_for_context(context_key) -> str` (the mapping above).
- `scope_dir(scope_key) -> Path` (creates on demand).
- `list_notes(scope_key) -> [{id, title, hook, updated}]` (parse MEMORY.md; fall back to a dir scan).
- `get_note(scope_key, note_id) -> str | None` (read `<note-id>.md`; validate note_id).
- `put_note(scope_key, note_id, title, content, hook="")`:
  1. write `<note-id>.md` (atomic temp+rename),
  2. update MEMORY.md index (upsert the pointer line, keep it sorted/stable),
  3. sync to Letta: delete any existing passages tagged `note-id` in the scope archive, then insert
     the note text as a passage tagged `[note-id]` (store the returned passage id in the sync map).
  Plaintext write MUST succeed even if Letta is down (files are source of truth); Letta sync failure is
  logged, not fatal.
- `delete_note(scope_key, note_id)`: remove file + index line + archive passages.
- `search(scope_key, query, limit=5) -> [{note_id, text, score}]`:
  Letta vector search within the scope's archive; on Letta error OR empty result, FALL BACK to a
  plaintext case-insensitive substring/index match over the scope's notes (so recall always returns
  something usable). Mark which path was used in a debug field.
- `recall(from_scope, target_scope, query, limit=5)`: permission check (Part D), then
  `search(target_scope, query, limit)`. Same-scope is always allowed.
- Archive management: `_archive_id(scope_key) -> str` lazily creates a Letta archive named
  `workshop-knowledge:<scope_key>` WITH an embedding_config (borrowed per the contract above),
  persisting the `scope_key -> {archive_id, passages: {note_id: passage_id}}` map in
  `.runtime/knowledge/archives.json` (atomic writes; corruption-tolerant). Reuse an existing archive by
  name if the map is lost.
- A thin Letta archive client: either add methods to `letta_legacy_client.py`
  (`create_archive`, `insert_passage`, `insert_passages`, `delete_passage`, `search_passages`,
  `list_archives`, `default_embedding_config`) or a small `services/letta_archive.py`. Prefer extending
  the existing legacy client so there is one Letta REST surface. Reuse its base_url/headers.

## Part B — `routes/knowledge.py` (the interface surface; operator-gated like other routers)
Mounted under `/knowledge`, `dependencies=_OPERATOR`. This IS the notes-app plug-in contract.
- `GET  /knowledge/{scope_key}/notes` -> list_notes
- `GET  /knowledge/{scope_key}/notes/{note_id}` -> get_note
- `PUT  /knowledge/{scope_key}/notes/{note_id}` body `{title, content, hook}` -> put_note
- `DELETE /knowledge/{scope_key}/notes/{note_id}` -> delete_note
- `POST /knowledge/{scope_key}/search` body `{query, limit}` -> search
- `POST /knowledge/recall` body `{from_scope, target_scope, query, limit}` -> recall (grants-gated)
Validate scope_key against the known shapes; reject unknown. Register in `backend/main.py` next to the
other operator routers.

## Part C — chat_context integration (ambient in-scope + intent cross-scope)
- Add `ContextProvider.knowledge_scope()` returning the provider's `scope_key` (base impl maps from
  `self.key` via `knowledge.scope_for_context`; providers can override).
- **Ambient in-scope (stable):** in `_build_prompt`, inject the scope's `MEMORY.md` index under
  `=== KNOWLEDGE (in scope: <scope_key>) ===` in the STABLE prefix region (near persona/capability,
  BEFORE the transcript). The index is small and changes rarely, so it caches well (aligns with
  Phase 3's cache-stable ordering; do not put volatile retrieval here).
- **Auto-recall for the current turn (volatile, goes at the END):** before the final user message,
  run `knowledge.search(scope, user_message, limit=3)` and, if hits, inject them under
  `=== RELEVANT NOTES (retrieved for this message) ===` as part of the final user turn (the volatile
  tail, never the cached prefix). This is real semantic recall with no tool call. Keep it cheap: skip
  for very short messages; cap injected chars.
- **Cross-scope recall (intent + grants):** expose a `recall` action the model can request via a
  fenced block ` ```recall\n{"scope":"<target-scope>","query":"..."}\n``` ` (mirrors the plan-patch
  pattern). On a turn where the model emits it, the backend runs `knowledge.recall(current_scope,
  target_scope, query)` (grants-gated) and injects the results into the NEXT turn's tail, plus a note
  telling the model the results are available. Document the protocol in a shared `RECALL_PROTOCOL`
  string appended to personas that should use it (general/new-project/project providers). Keep it
  opt-in and clearly "only when the user asks about another scope's knowledge."

## Part D — grants (reuse sensitivity + grants; no new permission system)
- Add capabilities `knowledge.read` and `knowledge.write` to the vocabulary in `services/permissions.py`.
- `knowledge.recall(from_scope, target_scope, ...)` permission rule (single-user, NOT a security wall,
  it is context-hygiene): same scope -> always allow. Cross-scope -> allow if the TARGET scope is
  `public`, or `shareable` WITH a `grants.json` row `{from: <from_scope>, to: <target_scope>,
  capability: "knowledge.read", mode: "read"}`; deny (return empty + a note) otherwise. Scope
  sensitivity: `project:<slug>` reads projects.json `sensitivity`; `workshop`/`app:<name>` default
  `shareable`; `agent:<id>` default `private`. The operator himself is never blocked from explicitly
  reading via the API; the gate only limits what a chat AUTO-pulls across scopes.
- Keep it minimal: a couple of helper functions in permissions.py, no middleware, no UI.

## Part E — migration + docs
- Do NOT break `global_memory.py` (it syncs `shared/global-memory.md` -> the Letta `operator-context`
  block attached to every agent). Keep that as-is.
- Seed `shared/knowledge/` with a `MEMORY.md` and migrate the operator-facing notes from
  `shared/global-memory.md` into one or more workshop-scope notes (leave global-memory.md in place as
  the block source; add a note in the index pointing to it). Do not duplicate-sync the same content
  into two Letta places in a way that double-counts; the operator-context BLOCK and the workshop
  knowledge ARCHIVE are different mechanisms (block = always-in-context; archive = recalled), which is
  fine and intended.
- Write `docs/agent-chat-redesign/knowledge-interface.md`: the KnowledgeStore contract
  (list/get/put/search/recall), the scope model, the file layout, the Letta archive backing, and how a
  notes app (or an Obsidian adapter) implements the same interface / registers as a scope's provider.

## Tests (in-process, like backend/scratch/test_phase1_api.py; scratch/ is gitignored)
- put_note -> file written + MEMORY.md index updated + get_note returns it.
- search: put two distinct notes, then a semantic query returns the relevant one via the Letta path
  (allow a few seconds for embedding; assert the vector path, not just fallback). ALSO force the
  fallback (Letta down / empty) and assert substring recall still returns the note.
- recall gating: cross-scope recall denied without a grant (empty), allowed after adding a grants row.
- API routes: PUT then GET then search then DELETE round-trip via TestClient (auth overridden, temp
  dirs, temp archives cleaned up).
- chat integration: a provider's prompt includes the scope index (ambient); a message that matches a
  note injects it into the tail.
- Regression: Phase 1 API suite still green; existing global_memory block sync untouched.
- Clean up any Letta archives created during tests (delete by id).

## Build order + models
1. `services/knowledge.py` + Letta archive client methods (opus): Parts A + the client. Core engine.
2. `routes/knowledge.py` + main.py registration (sonnet): Part B. Depends on 1.
3. chat_context integration + RECALL_PROTOCOL (opus): Part C. Depends on 1.
4. permissions knowledge.read/write + recall gating (sonnet): Part D. Small.
5. Orchestrator: migration (Part E), interface doc, tests, live verification (put a real note via API,
   search it, confirm the vector hit), commit. Restart backend to load; verify against the live Letta.

Keep it single-user (no defensive cruft), no em-dashes/emojis, reuse primitives, files-as-source-of-
truth always works with Letta as the derived vector layer.
