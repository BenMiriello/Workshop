# Phase 1 build spec — general in-chat UI primitives + project creation + builder cleanup

Status: BUILT + verified 2026-07-11 (commit 37f1e39, branch agent-chat-phase-1), then a design-review
refinement pass the same day. The spec below is the build contract as written; the "As built"
section at the end records where the implementation deliberately diverges and what the refinement
pass changed. Read both before extending Phase 1 code.

Authoritative implementation spec for Phase 1 of the agent/chat/knowledge/builder redesign
(`plan.md`). Complete and specific enough to hand to implementation subagents. Grounded in the real
code as of 2026-07-11.

## Scope (what Phase 1 ships)

1. **The general interactive in-chat UI primitive layer** — a `ui-card` block any provider's model can
   emit, that the chat frontend renders as an interactive widget (fields + buttons) using the design
   system, NOT auto-applied server-side. This is the reusable primitive; everything else is an
   application of it.
2. **Project creation from chat** — a `project-proposal` card (an application of `ui-card`) with
   Create / Open / Move-chat-into-project actions.
3. **Build-with-chat** — a general-chat entry point for discussing/developing a project idea before
   committing, which emits the `project-proposal` card when the user is ready.
4. **Builder page cleanup** — kill the hardcoded "Noetic" placeholders, unify the template system
   (backend authoritative), stop discarding template briefs, real templates for the two useful dead
   slugs, fix false copy, responsive two-column redesign, land on "Start building".
5. **Chat re-scope endpoint** — the small re-key path used by Move-chat-into-project.

Out of scope (later phases): knowledge scopes/Letta memory, context compaction, per-project Ralph,
SKILL.md skills, voice toggle. Do NOT touch those systems.

## Non-negotiable constraints
- Design system law (`frontend/CLAUDE.md`) is binding: build from `src/ui` primitives + tokens, no
  inline hex, lucide icons only (never emoji), five status kinds, all four list states, Edit-mode for
  writes. Run `bash scripts/check_design.sh` and `cd frontend && npx tsc --noEmit` before done.
- No em-dashes anywhere (copy, code strings, comments). No emojis in UI.
- Single-user: no allowlists/audit/rate-limiting/confirm-cruft. The `ui-card` action registry is a
  typed client registry (good design), NOT a security gate.
- No committed stubs (`TODO`/`FIXME`/`NotImplementedError`/`pass # stub`). Too big for a chunk ->
  surface it, don't stub.
- Delete dead code you replace (the 7 hardcoded frontend template slugs, the false subtext). Do not
  leave old + new systems side by side.

---

## Part A — the `ui-card` primitive (backend contract + frontend renderer)

### A.1 The wire contract

The model emits, inside its normal reply text, one fenced block tagged `ui-card` whose body is JSON:

    ```ui-card
    {
      "id": "proj-<stable-slug-or-nonce>",
      "kind": "project-proposal",
      "title": "New project",
      "fields": [
        {"name": "title",       "label": "Title",       "type": "text",     "value": "Notes"},
        {"name": "slug",        "label": "Slug",        "type": "slug",     "value": "notes"},
        {"name": "purpose",     "label": "Purpose",     "type": "text",     "value": "..."},
        {"name": "template",    "label": "Template",    "type": "template", "value": "blank"},
        {"name": "sensitivity", "label": "Sensitivity", "type": "sensitivity", "value": "shareable"},
        {"name": "brief",       "label": "Initial brief","type": "textarea", "value": "..."}
      ],
      "actions": [
        {"id": "createProject", "label": "Create project", "variant": "primary"},
        {"id": "dismiss",       "label": "Dismiss",        "variant": "ghost"}
      ]
    }
    ```

Rules:
- `kind` selects a registered card behavior on the client. Unknown `kind` or unknown action `id`
  renders an inert fallback (a plain card showing the parsed fields, no live buttons) — never an
  arbitrary request.
- `actions[].id` maps to a **registered client handler**, NOT an endpoint. The model cannot name an
  endpoint. This is the whole safety model and it is just a registry.
- Field `type` ∈ `text | slug | textarea | template | sensitivity | select`. `select` carries
  `options: [{value,label}]`. `template`/`sensitivity` are convenience types that render the same
  DS controls the builder page uses (Select fed by `/api/projects/templates`; Segmented Private/
  Shareable/Public).
- The block is part of the assistant message content and PERSISTS in the thread JSON, so it must
  survive reload. Backend does NOT strip it and does NOT act on it.

### A.2 Backend: do not strip, do not apply

- `ui-card` is a NEW fence tag; the existing `_PATCH_BLOCK = ```plan-patch...``` regex in
  `chat_context.py` does not match it, so Budget's plan-patch path is unaffected. Confirm no other
  `apply_actions` or strip step touches `ui-card`. (It must reach the client verbatim.)
- Add a shared, provider-agnostic protocol doc to the prompt so ANY provider's model knows the format
  and, critically, WHEN to use it (only when the user is actually ready to take the action; never
  unprompted). Put this in a single reusable string (e.g. `UI_CARD_PROTOCOL` in `chat_context.py`)
  and append it to the role preamble or the base `ContextProvider.persona()` composition so it is
  available platform-wide. Keep it short; the per-provider persona decides when to emit.

### A.3 Frontend: renderer + registry

Files: `frontend/src/components/Chat.tsx` (answer rendering path around `AssistantTurn` /
`ElidedContent` / `<Markdown>` at lines ~246-270 and the stored-message render at ~862-880), plus a
new `frontend/src/components/chat/UiCard.tsx` and a small registry module.

- **Extraction**: before handing answer text to `<Markdown>`, split the text on ```ui-card fenced
  blocks. Render the surrounding markdown normally and each `ui-card` as `<UiCard/>`. Interleave in
  order. A malformed JSON body renders as the inert fallback (plain card, no actions) plus keeps the
  raw text visible; never throw.
- **UiCard component**: renders `title`, the fields as DS primitives (`Field` + `Input`/`Textarea`/
  `Select`/`Segmented`; `slug` type uses the same kebab slugify as ProjectNew; `template` type fetches
  `/api/projects/templates`), and the actions as `Button`s. Local editable state seeded from
  `fields[].value`. All DS-compliant, responsive, no inline hex, lucide icons where an icon is used.
- **Action registry**: `frontend/src/components/chat/uiCardActions.ts` maps `action.id` ->
  `(cardState, ctx) => Promise<CardResult>`. `ctx` provides `{ navigate, contextKey, threadId,
  rescopeThread }`. Phase 1 handlers:
  - `createProject`: POST `/api/projects/` with `{slug,title,purpose,template,sensitivity,brief}`
    from card state. On 200 -> card switches to a success state showing the project name + an
    `openProject` button and a `moveChatToProject` button. On 409 (slug exists) -> success-ish state
    "Project already exists" + Open button (idempotent reload behavior). On other error -> inline
    `StateBlock variant="error"` inside the card with the failing status, card stays editable.
  - `openProject`: `navigate('/projects/<slug>')`.
  - `moveChatToProject`: call `rescopeThread(threadId, 'project:<slug>')` (Part D), then navigate to
    the project (or to the build chat). Show a brief success note.
  - `dismiss`: collapse the card to a one-line "Dismissed" note (does not delete the message).
- **State persistence across reload**: the card is stateless server-side in Phase 1. On reload it
  re-renders from the stored block in edit state; `createProject`'s 409 handling makes a second submit
  safe. Note this in a comment; do not build server-side card state now.

### A.4 Acceptance (Part A)
- A hand-crafted assistant message containing a ```ui-card project-proposal block renders as an
  editable card in the chat, not as a code block.
- Editing a field then clicking Create actually creates the project (verified via `/api/projects/`
  and on disk), and the card flips to the success state with a working Open button.
- Budget's plan-patch still works unchanged (regression check).
- `check_design.sh` + `tsc --noEmit` clean.

---

## Part B — project-proposal skill (the general provider emits the card)

- Add a lightweight provider for the discuss-then-create flow. Two options; pick the simpler:
  - **Preferred**: a new provider key `new-project` (subclass of base `ContextProvider`) whose
    `persona()` = a general helpful assistant PLUS explicit guidance: help the user shape a project
    (name, purpose, template choice among the real templates, initial brief with goals/constraints/
    open questions); when the user says they are ready (or asks to create it), emit exactly one
    `ui-card` of kind `project-proposal` prefilled from the discussion. Never emit it unprompted.
    Register in `PROVIDERS` (`chat_context.py` ~1292) and expose it in the provider list the frontend
    picker reads (so `showProviderPicker` shows it). context_key = `new-project`.
  - Alternative: bake the same guidance into the existing `general` provider. Rejected because it
    pollutes every general chat with project-creation framing; keep `general` clean and add
    `new-project`.
- The `UI_CARD_PROTOCOL` (A.2) supplies the block FORMAT; this provider persona supplies the WHEN +
  the field semantics (valid template slugs from `/api/projects/templates`, sensitivity default
  `shareable`).

### B.1 Acceptance
- Opening a `new-project` chat and describing an idea, then saying "create it", yields a filled
  `project-proposal` card whose fields match the discussion. Creating it works end to end.

---

## Part C — build-with-chat entry points

- On `/projects` (`Projects.tsx`, near the existing `+ New project` and Import buttons, line ~187):
  add a **"Build with chat"** button (lucide `MessagesSquare` or `Sparkles`, DS Button) that opens the
  `new-project` chat (route to the focused chat for context_key `new-project`, e.g.
  `/chat/new-project`, matching how FocusedChat/`/voice/:key` route by context key — verify the
  existing chat route pattern in `App.tsx`).
- On `/projects/new` (`ProjectNew.tsx`): add a secondary link/button "Prefer to talk it through first?
  Build with chat" that routes to the same `new-project` chat. Non-destructive, just navigation.
- Keep the direct form as the primary fast path; build-with-chat is the alternative, not a replacement.

### C.1 Acceptance
- Both entry points land in a working `new-project` chat. No dead links.

---

## Part D — chat re-scope endpoint (the re-key conversion)

- New endpoint in `backend/routes/chat.py`: `POST /threads/{id}/rescope` body `{context_key}`.
  - Load the thread via `chat_store`, validate the new `context_key` resolves to a real provider
    (`chat_context.get_provider`) — reject unknown keys 400.
  - Update the thread's `context_key` (and set `converted_from` = old key + a timestamp field) and
    save. Thread id and history unchanged (the JSON file stays; only the field changes). Collision is
    not a concern because the thread is identified by id, not by key; multiple threads can share a
    context_key (list endpoints already filter by key, not assume uniqueness).
  - Return the updated thread summary.
- Frontend `chatApi.rescopeThread(id, contextKey)` wrapper; wired into the `moveChatToProject` handler.

### D.1 Acceptance
- Calling rescope moves a `new-project` thread to `project:<slug>`; reloading the project's build chat
  shows the prior conversation; the project provider's PROJECT.md context now layers on top.

---

## Part E — builder page + template system cleanup

### E.1 `ProjectNew.tsx`
- Delete `TEMPLATE_HINTS` and `TEMPLATE_LABELS` hardcoded maps (lines 23-41) and the overlay logic in
  the fetch effect (lines 76-80). Render templates purely from `GET /api/projects/templates`
  (`{slug,label,hint}`). The four dead slugs disappear because the backend never returns them.
- Placeholders: Title -> `"What are you building?"`; Purpose -> `"One sentence: what this is and who
  it is for"`; Slug placeholder stays derived. Remove every product name ("Noetic") from the file.
- Fix false copy: `PageHeader` sub "Scaffold + auto-git + open vibe session" -> "Scaffold, git-init,
  and open it". After create, keep the nav to `/projects/<slug>` but ensure the detail page offers a
  clear "Start building" affordance (it already links to AgentBuild via the build-agent card; confirm
  or add a primary "Start building" button on the detail page's empty-agents state). Primary button
  label -> "Create project".
- Responsive two-column on wide (form left; a live preview panel right showing the selected template's
  hint + a preview of the PROJECT.md that will be written from title/purpose/brief), single column
  stacked on narrow. Test at ~1440 / 1024 / 768 / 390 per the multi-width rule. All four states on the
  template Select fetch (loading skeleton, error StateBlock with the endpoint, empty -> at least
  `blank`).

### E.2 Template system (`backend/routes/projects.py`)
- Add a `template.json` manifest per on-disk template dir: `{ "label": "...", "hint": "...",
  "substitutions": ["slug","title","purpose"] }`. `GET /templates` reads each dir's `template.json`
  for `label`/`hint` (fall back to dir name + "" if absent). `blank` stays programmatic with a real
  label/hint.
- **Stop discarding the template brief**: in `_scaffold_from_template` (line 205-208), when a brief is
  supplied, do NOT overwrite PROJECT.md. Instead append/merge the brief under a `## Brief` section of
  the template's existing PROJECT.md (preserve the template's richer scaffold). For `blank`,
  `_scaffold_blank` already puts the brief under `## Goal`; keep that.
- Author two real templates under `projects/_templates/` (operator decision #9):
  - `fastapi-capability-provider/` — a backend service skeleton declaring `provides: [...]`: a minimal
    FastAPI router module, a `workshop.app.json`/manifest stub as used by the base-apps runner (match
    the real manifest shape used elsewhere in the repo), PROJECT.md (goal/constraints/open-questions),
    AGENTS.md, TODO.md, .gitignore, .claude/CLAUDE.md, and a `template.json`. Use `{{slug}}`/`{{title}}`
    /`{{purpose}}` tokens. No stubs that violate the no-stub rule; a minimal working router is fine.
  - `agent-tool/` — a Letta tool registration skeleton matching `services/letta_tools` + how
    `letta_register.py` expects `TOOL_REGISTRY` entries: a tool module with one real example function,
    PROJECT.md/AGENTS.md/TODO.md/.gitignore/.claude/CLAUDE.md/template.json. Ground this in the actual
    `letta_register.py` / `letta_tools` conventions before authoring; do not invent an API.
- `projects/_templates/` is git-tracked (per repo CLAUDE.md), so these are committed.

### E.3 Acceptance
- `/projects/new` shows only real templates, no "Noetic", correct copy, works at all widths, creates a
  project from each template with the brief merged (not overwriting) and git-init succeeding.
- Creating from `static-site`, `integrated-sub-app`, `fastapi-capability-provider`, `agent-tool`,
  and `blank` all produce a valid project dir + registry row.

---

## Confirmed integration facts (verified 2026-07-11, do not re-derive)
- Focused chat route is `/chat/:contextKey` -> `FocusedChat` (App.tsx:120). Build-with-chat opens
  `/chat/new-project`.
- Providers are listed via `GET /api/chat/providers[?scope=]` -> `chat_context.list_providers(scope)`
  (routes/chat.py:109). Registering `new-project` in `PROVIDERS` so `list_providers` returns it is
  sufficient for the picker to show it (picker shows data-mode providers, Chat.tsx:766).
- Frontend chat API is `frontend/src/lib/chat.ts` (exported as `chatApi`, has `listProviders`,
  `createThread`, `sendMessage`); add `rescopeThread(id, contextKey)` there.
- Voice already routes `/voice/:contextKey` -> `VoiceMode` (App.tsx:121) via the same context-key
  pattern (relevant to Phase 6, not Phase 1).

## Build order + model assignment (for the orchestrator)

1. **Backend contract first** (opus subagent): `UI_CARD_PROTOCOL` string + confirm no strip; the
   `new-project` provider (Part B); `/threads/{id}/rescope` (Part D); template.json manifests + brief
   merge + two real templates (Part E.2). This defines the contract the frontend consumes.
2. **Frontend primitive** (opus subagent): `UiCard.tsx` + extraction in `Chat.tsx` + action registry +
   `chatApi.rescopeThread` (Parts A.3, D frontend). Depends on step 1's contract.
3. **Builder page + entry points** (sonnet subagent): `ProjectNew.tsx` redesign, `Projects.tsx` +
   ProjectNew build-with-chat buttons, detail-page "Start building" affordance (Parts C, E.1).
   Depends on step 1's template endpoint shape.
4. **Orchestrator integration + testing** (me): wire, run `check_design.sh` + `tsc`, targeted API
   tests (create via each template; rescope; ui-card create path via a crafted message), and a
   Playwright creative pass (open new-project chat, discuss, create from card, open project; open
   /projects/new at 1440/768/390, create from a template; verify Budget plan-patch regression). Commit
   at meaningful checkpoints and at phase end.

## Testing checklist (must actually work, not just not-error)
- API: POST /api/projects/ for each of the 5 templates -> valid dir + registry row + git log has the
  scaffold commit + PROJECT.md contains the merged brief (not overwritten). 409 on duplicate slug.
- API: POST /threads/{id}/rescope -> field updated, history intact, unknown key 400.
- ui-card: craft an assistant message with a project-proposal block (or drive the new-project
  provider) -> card renders, edit + Create makes the project, success state + Open works, Move-chat
  re-scopes.
- Regression: Budget chat plan-patch still applies + strips.
- Browser (Playwright): new-project chat conversation -> card -> create -> open; /projects/new at 3
  widths with real content, create from a template, land on detail with Start building.
- Design gate: `bash scripts/check_design.sh` clean; `cd frontend && npx tsc --noEmit` clean.

---

## As built (deviations from the spec above + the review-pass refinements)

Deliberate divergences made during the build:
- **Blank scaffold brief stands alone.** Spec E.2 said keep the brief under `## Goal` for `blank`;
  as built, a supplied brief becomes the PROJECT.md body on its own (the new-project chat already
  writes a structured Goals/Constraints/Open-questions brief, so forcing the skeleton duplicated
  headings). The skeleton is only written when no brief is given.
- **Field schema has an optional `help`** (one-line hint rendered under the control). The A.1 table
  omits it; `UI_CARD_PROTOCOL` and `UiCardField` both carry it.
- **`UI_CARD_PROTOCOL` is appended to BOTH role preambles** (data-mode and builder), so builder chats
  can also emit cards. Any provider without card guidance in its persona is told never to emit
  unprompted and never to invent a `kind` (unknown kinds render as an inert read-only card).
- **`template.json` carries a `substitutions` key.** Informational only: the scaffold always
  substitutes all three tokens. The manifest file itself is deleted from the scaffolded copy.
- **`new-project` defaults to the sonnet tier** (proposal shaping wants the reasoning), selectable
  down to haiku in the composer.
- **Rescope response** returns a full thread summary (id, keys, provenance, counts), duplicating the
  chat_store summary shape rather than reusing it (candidate refactor, see report).

Review-pass refinements (2026-07-11, after 37f1e39):
- **Streaming placeholder:** a not-yet-closed ```ui-card fence in a streaming answer renders as a
  skeleton card (`UiCardPending`) instead of flashing the half-written JSON; on a stored message an
  unclosed fence falls back to plain markdown so nothing is hidden.
- **409 parity:** `createProject` on 409 offers the same follow-ups as a fresh create (Open + Move
  chat), since the common 409 is a resubmit-after-reload of a card that already created the project.
- **Slug rules:** shared `slugify` (uiCardActions.ts, imported by ProjectNew so the two can't drift)
  collapses runs of separators; backend `SLUG_RE` accepts a leading digit (`3d-printer`).
- **Rescope validation:** `project:<slug>` targets must exist on disk (400 otherwise); a provider is
  constructible for any slug, so provider resolution alone was not enough.
- **`help` rendered** in every card field; template option labels use `·` separators (no em-dashes in
  UI copy).
