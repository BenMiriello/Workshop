# Knowledge interface

The contract a notes app (or an Obsidian-vault adapter, or any store) plugs into so its content
becomes recallable knowledge in Workshop chats. Built in Phase 2. Backend:
`backend/services/knowledge.py` + `backend/routes/knowledge.py`.

## Model
- **Scope**: where knowledge lives. `scope_key` is one of `workshop`, `project:<slug>`, `app:<name>`,
  `agent:<id>`. A chat's `context_key` maps to a scope via `knowledge.scope_for_context` (general /
  new-project -> workshop; project:/build: -> the project scope; budget/finance/commute/location ->
  `app:<key>`).
- **Note**: one markdown file `<note-id>.md` in the scope dir, indexed by a line in that scope's
  `MEMORY.md`. `note_id` is a kebab/hex slug (no path traversal).
- **Plaintext is the source of truth.** Files are authoritative, git-inspectable, and every write
  lands on disk first (succeeds even if Letta is down). The Letta archive is the DERIVED vector index.
- **Vectors**: each scope has one Letta archive (`workshop-knowledge:<scope_key>`) with a local
  embedder (Ollama `nomic-embed-text`, 768-dim). A note is one passage tagged with its `note_id`.

## Scope -> directory
- `workshop`       -> `shared/knowledge/` (git-tracked; the operator's baseline notes)
- `project:<slug>` -> `projects/<slug>/knowledge/` (in the project's own git repo)
- `app:<name>`     -> `apps/<name>/knowledge/`
- `agent:<id>`     -> `.runtime/agents/<id>/knowledge/`

## The interface (services/knowledge.py)
- `scope_for_context(context_key) -> scope_key`
- `list_notes(scope_key) -> [{id, title, hook, updated}]`
- `get_note(scope_key, note_id) -> str | None`
- `put_note(scope_key, note_id, title, content, hook="")` (async) -> writes file + index + syncs a
  passage. Returns `{scope, note_id, letta}`.
- `delete_note(scope_key, note_id)` (async)
- `search(scope_key, query, limit=5)` (async) -> `[{note_id, text, score, debug}]`. Scope-scoped Letta
  vector search; falls back to plaintext substring match on Letta error/empty (`debug` = "letta" or
  "fallback"), so recall always returns something usable.
- `recall(from_scope, target_scope, query, limit=5)` (async) -> gated by `permissions.can_recall`;
  same-scope always allowed, cross-scope needs sensitivity + a grant (below). Returns `[]` when denied.
- `index_block(scope_key) -> str` -> the compact ambient index injected into chat prompts.

## HTTP surface (routes/knowledge.py, operator-gated, mounted at /api/knowledge)
- `GET  /knowledge/{scope_key}/notes`
- `GET  /knowledge/{scope_key}/notes/{note_id}`
- `PUT  /knowledge/{scope_key}/notes/{note_id}`  body `{title, content, hook}`
- `DELETE /knowledge/{scope_key}/notes/{note_id}`
- `POST /knowledge/{scope_key}/search`  body `{query, limit}`
- `POST /knowledge/recall`  body `{from_scope, target_scope, query, limit}`

## Sharing (reuse sensitivity + grants; not a security wall)
`permissions.can_recall(from_scope, target_scope)` decides what a chat in one scope may AUTO-pull from
another. It is context hygiene / token budget, not access control: the operator always reads any scope
directly via the API. Rules: same scope always; a `public` target always; a `shareable` target only
with a `grants.json` row `{from, to, capability: "knowledge.read"}`; a `private` target never (auto).
Scope sensitivity: `project:<slug>` reads projects.json; `workshop`/`app:<name>` default `shareable`;
`agent:<id>` default `private`.

## How chat uses it (chat_context + routes/chat.py)
- **Ambient (in-scope):** the scope's `MEMORY.md` index is injected into the stable prompt prefix so
  the model knows what notes exist. Small and cache-stable; bodies are not in the prefix.
- **Auto-recall (this message):** for data-mode assistants, the user's message runs
  `search(scope, message)` and the top notes are injected into the reply tail (volatile, so it never
  invalidates the cached prefix). Build providers read files directly and are skipped.
- **Cross-scope (intent):** out-of-scope knowledge takes intent + a grant. (A model-driven recall
  protocol is the next increment; the store + gating already support it via `recall`.)

## Plugging in a different store (e.g. a notes app or Obsidian)
Implement the same functions over your storage (list/get/put/delete/search) and register as the
scope's provider. `knowledge` = anything exposed through this interface (injectable + recallable,
governed by knowledge.read/write). `app data` = everything else behind an app's own API. An app
exposes a slice of its data as knowledge by implementing this interface over that slice; chat never
knows the difference.

## Not yet built (later increments)
- A model-driven cross-scope `recall` protocol (fenced block) so a chat can explicitly pull another
  scope on the operator's ask.
- Migrating `shared/global-memory.md` fully into `shared/knowledge/` (kept separate for now: the
  `operator-context` Letta block that global_memory.py attaches to every agent is a different, always
  in-context mechanism from the recalled archive, and both are intended to coexist).
- A knowledge-browser UI (the API above is the surface; no dedicated page yet).
