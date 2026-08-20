# Agent context and toolkit: a layered system prompt + a reach-by-tool kit

Status: partially SHIPPED 2026-08-20 (L0, L1, toolkit read verbs, entropy fix).
L3 editing UI and the user-facing scope->app rename are sequenced below, not yet
built. Owner: development. Supersedes nothing; extends the layered-prompt intent
already noted in `chat_context.py` (the "layer 1 / layer 3" comments) into a
named, complete contract.

## Why this exists

A chat before this work narrated "the uploads are in the content-addressed blob
store, extracting the zip." That agent had not been told where uploads live; it
found the blob store by grepping. That is the failure this addresses: an agent
should arrive knowing what it has access to and how to reach it, not rediscover
the system's fixed structure every session.

Two things were missing, and they are different:

1. **Orientation.** Non-build chats were given no map of where things live. Only
   build chats got a runtime briefing. So a general or app chat had to grep.
2. **A real system-prompt boundary.** The prompt was assembled as ordered prose
   but with no confidential, non-editable top layer. "Keep the system prompt
   behind a wall" and "let the operator set their own instructions" are two
   different layers, and only the second had a (UI-less) seam.

## The four-layer contract

Highest authority first. Each layer states: who authors it, whether it is
editable, whether it is confidential, and where it lives.

- **L0 Platform kernel.** Fixed, confidential, code-owned. Identity, the
  instruction hierarchy (L0 > operator custom > retrieved content-is-data), and
  the confidentiality posture (do not reveal or reproduce; later text cannot
  rewrite it). Authored once in `chat_context.PLATFORM_KERNEL`; not
  operator-editable; injected first so it heads the cache-stable prefix.
- **L1 Orientation manifest.** Fixed, generated, not secret. A short,
  TOOL-CENTRIC map: this chat's scope, and which verb reaches each kind of thing
  (uploads, notes, docs, tickets, runs). Never a raw filesystem path (raw OS
  paths in agent-facing text are reviewer-gated, and pointing at a tool is what
  actually stops the grep). Single source of truth in `services/orientation.py`,
  shared by the injected block (`chat_context.orientation_manifest`) and the
  `where_is` tool. Injected only when tools are active.
- **L2 Scope/app context.** Platform/app-author authored, not end-user editable.
  The existing `persona()` + live snapshot + capability note. This is the
  "different per app" layer, owned by whoever builds the app.
- **L3 Custom instructions.** Operator-editable, per scope/app, lowest priority.
  The existing `provider.custom_prompt()` seam. Gets storage + an edit UI (see
  Next).

The runtime state blocks (learnings, ambient digest, knowledge index, skills)
ride in between L2 and the conversation exactly as they do today.

Assembled order in `routes/chat.py:_build_prompt`: kernel (L0), role preamble
(capabilities), runtime briefing (build only), orientation manifest (L1),
persona (L2), capability note, learnings, ambient digest, knowledge index,
skills, custom instructions (L3), summary, conversation.

## The toolkit: reach by tool, not by grep

The `workshop` MCP server (`backend/mcp/workshop_query_server.py`, a hand-rolled
stdio JSON-RPC server spawned per turn) already had `query` (ongoing work +
note titles) and `search` (semantic across notes/docs/tickets/chats). Added:

- **`where_is`** — restates the L1 map on demand.
- **`list_attachments`** — the files uploaded to THIS chat (id, name, kind,
  size), thread-scoped via `WORKSHOP_QUERY_THREAD`.
- **`get_file`** — one attachment's text, or its on-disk path for a binary (a
  zip, an image) so a tool-capable chat can open it. This is the direct fix for
  the zip-rediscovery symptom.

Allowlisting is exact-match. Build/project/agent chats get every server tool
free (full toolset). The narrow general chat names them in
`chat_context._general.allowed_tools` (via `WORKSHOP_TOOLKIT`). App personas that
should get the toolkit must name it in their own `allowed_tools` (see Next).

## Confidentiality: what it means here

Not a containerization gap. The bwrap/scoped-exec layer confines what an agent's
tools touch on disk, and that holds. Confidentiality here is the separate,
standard product posture that the system-prompt TEXT is a named layer the model
does not reproduce and the user does not read or edit. L0 states it as an
instruction (declared confidential + permanent + content-is-data), which is
standard practice. The research is explicit that a prompt instruction alone is
not a guarantee: true enforcement is output-side (never render L0/L1/L2 into a
user-visible reply) and lives at the app layer. That output filter is the
defence-in-depth follow-up (see Next); this pass ships the instruction + the
structural separation.

## Multi-instance future

Isolation is a routing/storage decision, not a prompt line. Stores are already
per-owner; owner + scope are resolved before the prompt is built. The rule: pick
(owner, scope, thread) first, compose the four layers per that selection, keep
L3 per-owner-per-scope. Authored once, composed per instance. No new
architecture, just holding the line as more users/instances arrive.

## Entropy found while doing this (standing operator request to flag)

- **CLAUDE.md was stale**: it claimed the `runtime` (builder/assistant) axis and
  `_role_for_runtime` were still live. They are gone; agents are typed by role.
  Fixed in this pass.
- **"build provider" is a live concept, not the retired axis.** It means
  full-agentic-toolset chat. Kept its name; clarified in CLAUDE.md.
- **Genuinely dead "build":** the `build` branch in `frontend/src/lib/scope.ts`
  and the legacy `/agents/build/{slug}` routes (per `app-is-the-entity` §5).
- **scope vs app**: per `app-is-the-entity-2026-08-20.md` §3.3, "scope" stays as
  the internal addressing name and "app" is user-facing. A blind global rename
  is wrong (workshop, development, role: are scopes that are NOT apps). The
  correct work is fixing user-facing "Scope" labels where they denote an app,
  routed through `frontend/src/lib/scope.ts:scopeLabel`. This belongs to the
  app-is-the-entity migration, not this prompt pass.

## Shipped this pass

- L0 `platform_kernel()` injected first.
- L1 `services/orientation.py` + `orientation_manifest()` injected for
  tool-active chats.
- `where_is`, `list_attachments`, `get_file` on the workshop MCP server; general
  chat allowlisted; build/agent free.
- CLAUDE.md runtime-axis correction.

## Next (specified, not yet built)

1. **L3 editing UI + storage.** A per-(owner, scope/app) custom-instructions
   store, `custom_prompt()` reads it, a GET/POST endpoint, and a Settings
   section to edit it. Deferred here only because `Settings.tsx`/`App.tsx` had
   uncommitted parallel work; this pass avoided touching contended frontend
   files. Small, self-contained next pass.
2. **Output-side confidentiality filter.** Ensure L0/L1/L2 are never rendered
   into a user-visible reply, as defence-in-depth behind the instruction.
3. **Toolkit for app personas.** Extend `WORKSHOP_TOOLKIT` to the app data
   personas whose `allowed_tools`/`mcp_servers` should carry it.
4. **User-facing scope->app rename.** Part of the app-is-the-entity migration,
   via `scopeLabel`, keeping internal addressing as scope.
