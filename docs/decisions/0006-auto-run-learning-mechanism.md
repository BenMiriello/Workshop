# ADR 0006 — Auto-run learning mechanism

Date: 2026-07-18
Status: Proposed
Deciders: the operator
Context doc: `docs/platform-redesign/chat-work-linkage-2026-07-18.md` §3

---

## Context

The operator frequently files-and-runs task-proposals from certain chat contexts (build chats,
project chats). The two-click flow (review card → File and run) becomes repetitive when the
choice is always the same.

The linkage design (§3) introduces a per-kind auto-run toggle: after a File-and-run, the
operator can express "always run proposals from this context kind immediately." Subsequent
proposals from that kind bypass the edit step and auto-invoke the run, with a brief undo window.

This ADR decides: where is the preference stored, how is it checked, and how is it scoped.

---

## Decision

Store auto-run preferences in the existing learnings store (`backend/services/learnings.py`)
as structured pref entries, and inject the preference into the `task-proposal` card spec
server-side at emit time.

The preference record shape:
```python
{
    "kind": "preference",
    "key": "auto_run_proposals",
    "scope": "build",                # "general" | "build" | "project" | "project:<slug>"
    "value": True,                   # bool
    "set_at": "2026-07-18T...",
}
```

`learnings.py` gains two methods alongside the existing `record_feedback` / `memory_block`:
- `set_preference(key, scope, value)` — upserts a pref record, keyed by `(key, scope)`
- `get_preference(key, scope, specific="")` — returns the preference for the most specific
  matching scope; `specific` is a sub-scope like a project slug; most specific wins

Scope specificity order (most → least): `"project:<slug>"` > `"project"` > `"build"` > `"general"`.
If no preference is set at any scope, returns `None` (falsy → edit mode, no auto-run).

`chat_context.py` calls `get_preference("auto_run_proposals", context_kind, specific=project_slug)`
when building a `task-proposal` card spec, and injects `"auto_run": True` if the result is truthy.

The frontend, seeing `spec.auto_run === true`, auto-invokes `addTasksAndRun` on card mount with
a 10-second undo window before the run actually fires.

A new `PATCH /api/chat/{ctx}/preferences` endpoint writes to the learnings store. Called by the
client when the operator uses the auto-run toggle on a settled tracking card.

---

## Rationale

### Why the learnings store (not a new table or config file)?

The learnings store already exists, is already injected into both chat prompts and ralph runs,
and already has a CLI and a UI affordance. Adding structured `preference` entries to it costs
two methods (~20 lines) and zero new files or infrastructure. The alternative would be a
separate `preferences.json` or a database table — more infrastructure for the same capability.

The learnings store was designed for "things the operator has expressed that future runs should
know." A run-mode preference is exactly that. The `scope` field already maps to the same scope
keys used throughout the task and context layers.

### Why server-injected (not client-discovered)?

The client does not have direct access to the learnings store. Options were:
1. Client fetches preferences from a dedicated endpoint at card-render time (adds a render-time
   async dependency; race between preference load and card auto-invocation).
2. Server injects `auto_run` flag into the card spec at emit time (no client-side async; the
   card has the answer when it renders).
3. Chat context includes a preferences block that the client reads (possible but over-general;
   the client would need to parse and apply context rules itself).

Option 2 is cleanest. The card spec is already server-controlled (the model emits it, the
server validates it). Adding one server-injected field follows the existing pattern of injecting
`scope`, `title`, and field defaults.

### Why scope at "context kind" granularity?

The operator's natural mental model is "proposals from build chats" or "proposals from this
project" — not per-thread. The `context_key` is already structured (`build:<slug>`,
`project:<slug>`, `general`). The kind prefix (`build`, `project`, `general`) is the natural
coarse-grain; the full slug is the fine-grain. The specificity ladder (`project:my-project` >
`project` > `general`) lets the operator set a broad default and override it per-project.

### Why an undo window instead of a confirmation dialog?

The undo window (10 seconds before the run fires) is consistent with the single-operator
posture: no confirmation gates, but a clear escape hatch. A confirmation dialog before every
auto-run would defeat the purpose of the toggle. The operator turned it on knowing what it does.

---

## Alternatives rejected

### A: Store in the chat thread's metadata

The preference would be scoped to the thread, not the context kind. An operator would have to
set it in every new thread. Rejected: too fine-grained; defeats the "learn from behavior" goal.

### B: Use the model to learn preferences from conversation text

Instead of an explicit toggle, the model reads "always run this kind" from conversation and
emits a `learning-note` block that the server applies. This is the "chat-side preference
capture" proposed in Phase 4 of the main plan (`learning-note` fenced blocks → `record_feedback`).

The `learning-note` mechanism is designed for free-text operator corrections and preferences
that the model observes. It is already proposed for general use. BUT: for a boolean toggle
(auto-run: on/off), a structured preference is strictly better than natural-language feedback:
- Reliable detection (the operator clicked a toggle, not said "always run these")
- Reliable reversal (toggle off → write `value: False`, unambiguous)
- Reliable lookup (key-based, not embedding-similarity-based)

Auto-run is NOT rejected for the `learning-note` mechanism — it is just the wrong tool for a
binary switch. The toggle writes structured prefs; conversational feedback writes learning-notes.
Both mechanisms can coexist.

### C: Per-chat-context "Run all proposals immediately" flag in chat settings

A chat-level setting in the UI settings panel. Cleaner UX but requires a full settings surface
for the chat context (not built). The toggle on the tracking card achieves the same result
without a separate settings flow; it is surfaced at the moment the operator has just made the
filing choice, which is the right time to learn from it.

---

## Consequences

**Good:**
- Zero new files: the learnings store gains two methods; the rest is wired through existing
  chat_context and routes.
- The preference is visible to ralph runs (learnings block is injected there too), so a worker
  that emits a task-proposal in an orchestrated flow also benefits from the operator's stated
  preference.
- Reversible: toggling off writes `value: False` (falsy); next card renders in edit mode again.

**Accepted:**
- The `get_preference` lookup runs at card-emit time (per chat turn). The learnings store is a
  flat JSON read; this is not a meaningful perf concern.
- If `set_preference` and `record_feedback` write to the same file, they share the same flock.
  Acceptable for a single-operator system with low concurrency on the learnings path.
- The undo window (10s) means auto-run proposals need the client to be JS-active for those 10s.
  A page load mid-countdown aborts cleanly (the run never fired; the card renders in tracking
  phase if it already went through or in edit phase if it didn't).

---

## Implementation checklist (not built yet)

- [ ] `learnings.py`: `set_preference(key, scope, value)` + `get_preference(key, scope, specific="")`
- [ ] `chat_context.py`: call `get_preference("auto_run_proposals", ...)` and inject into card spec
- [ ] `routes/chat.py`: `PATCH /api/chat/{ctx}/preferences` endpoint
- [ ] `UiCard.tsx`: handle `spec.auto_run === true` on mount; render countdown + undo link
- [ ] `uiCardActions.ts`: preference toggle handler calling the preferences endpoint
- [ ] `chat-work-linkage-2026-07-18.md` tracking card design includes the toggle row
