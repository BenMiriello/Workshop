# Chat toolbar: audit + redesign

Date: 2026-07-18  
Source file: `frontend/src/components/Chat.tsx` (toolbar lines 1102–1211)

---

## Part 1 — Current behavior (every control)

The toolbar renders as `<div className="chat-toolbar">`. All buttons use the
bespoke `.chat-tb__btn` class; all selects use `.chat-tb__select` (NOT the
`ws-select` Design System primitive). Controls are listed left-to-right as they
appear in the DOM.

### 1. Back button
- **Icon**: `<ChevronLeft size={16}>`
- **When**: `variant === "full"` only (the `/chat/:contextKey` route — FocusedChat)
- **Action**: `navigate(-1)` or falls back to `/agents` when there is no history
- **Label**: `title="Back" aria-label="Back"`

### 2. Workbench button
- **Icon**: `<PanelsTopLeft size={16}>`
- **When**: `effectiveMode === "build"` AND a build slug exists AND `variant === "full"`
- **Action**: `navigate(`/agents/${slug}/chat`)` — opens the full IDE workbench (files/editor/preview/diff) for the build agent
- **Label**: `title="Open workbench …" aria-label="Open workbench"`

### 3. Provider / assistant select  ← the first "double chat-name field"
- **Element**: raw `<select>` with class `chat-tb__select chat-tb__provider`
- **When**: `variant === "full"` OR `effectiveMode === "data"`, providers loaded,
  and `effectiveMode === "data"` (never shown for build agents)
- **Action**: `setActiveProvider(value)` — switches which assistant the next
  message goes to; triggers a thread-list reload for the new scope
- **Options**: every `Provider` object returned by `GET /api/chat/providers`
  (e.g. "Clawd — general", "Finance assistant", "Project: foo")
- **Commit**: on change — a view preference, NOT a config write
- **Problem**: A provider can be any context key. The dropdown label shows the
  provider's `.label` string, which may look identical to a thread title in the
  adjacent thread select. Visually these two selects sit flush together with no
  separator or label; users cannot tell them apart.

### 4. Thread / conversation select  ← the second "double chat-name field"
- **Element**: raw `<select>` with class `chat-tb__select chat-tb__thread`
- **When**: Always rendered (even when `threads` is empty — the "New chat"
  placeholder option is always present)
- **Action**: `openThread(value)` when an existing thread is chosen;
  `newChat()` when the "New chat" placeholder (value `""`) is chosen
- **Options**: a static "New chat" (value `""`) followed by all `ThreadSummary`
  objects for the current scope, in server-returned order (most-recent first)
- **Commit**: on change — immediately loads the selected thread
- **Problem 1**: When `threads.length === 0` this renders a single-option select
  showing only "New chat". A native `<select>` with one item is a dead control
  that wastes space and still shows the OS arrow.
- **Problem 2**: When threads exist but the option text is long, the select
  truncates with `…` and the only way to see the full list is the OS picker —
  no indication of how many threads exist or what the others are named.

### 5. New chat button
- **Icon**: `<Plus size={16}>`
- **When**: Always rendered
- **Action**: `newChat()` — clears messages, resets `activeId`, no HTTP call
- **Problem**: Duplicates the "New chat" option at the top of the thread select.
  Two affordances for the same action, neither labeled.

### 6. Delete chat button
- **Icon**: `<Trash2 size={16}>`
- **When**: `activeId != null` (a real thread is loaded)
- **Action**: `setConfirmDeleteId(activeId)` → opens `<ConfirmDialog>` (correct
  Design System treatment — destructive action behind a named dialog)

### 7. File ticket button  ← the "list" button
- **Icon**: `<ListPlus size={16}>` (or `<Loader>` spinning while in-flight)
- **When**: `activeId != null`
- **Action**: `spawnTaskFromChat(false)` — `POST /api/chat/threads/{id}/spawn`
  with `{ run: false }`. Creates a task ticket scoped to the chat's project/
  agent context without starting a worker. On success shows a `chat-spawn-note`
  banner above the log.
- **Label**: `title="File this chat as a tracked ticket" aria-label="File ticket"`
- **Problem**: `ListPlus` is a list icon with a plus. Without a text label it reads
  as "add to list" — its relationship to ticketing is non-obvious. Sits directly
  next to the Play button; together they look like a media player pair.

### 8. File-and-run button  ← the "play" button
- **Icon**: `<Play size={16}>` (or `<Loader>` while in-flight)
- **When**: `activeId != null`
- **Action**: `spawnTaskFromChat(true)` — same endpoint with `{ run: true }`.
  Creates the task ticket AND immediately launches a worker job. After success
  it reloads the thread (the worker writes a persistent job-status card into
  the thread). The `spawnMsg` banner reflects both the filing and the run
  result, including any `run_error`.
- **Label**: `title="File this chat as a ticket and run it now" aria-label="File and run"`
- **Problem**: `<Play>` is the most generic "go" icon in UI vocabulary. Next to
  `<ListPlus>` it reads as play/pause for audio or a run button for something
  already selected — not "also run a job." The distinction between buttons 7 and
  8 is invisible at a glance: same disabled state, adjacent placement, no group
  boundary.

### 9. Tasks toggle button
- **Icon**: `<ListTodo size={16}>` + numeric badge when `openTaskCount > 0`
- **When**: `taskScope != null` (build/project/agent chats only — not general or app docks)
- **Action**: `setTasksOpen(o => !o)` — shows/hides the `<TaskPanel>` drawer
- **State**: `.is-active` class when open; `aria-expanded`
- **Note**: Logically unrelated to the file/run pair but sits immediately after them

### 10. Spacer
- `<span className="chat-tb__spacer" />` — flexbox spacer; pushes the model
  select and expand button to the right end of the bar

### 11. Model select
- **Element**: raw `<select>` with class `chat-tb__select chat-tb__model`
- **When**: `modelOptions.length > 0` (almost always true)
- **Action**: `setSelectedModel(value)` — the chosen model is sent with every
  subsequent turn (`body: { model: selectedModel }`)
- **Options**: derived from `activeProviderObj?.models` (the selected provider's
  supported models) or falls back to `Object.keys(MODEL_LABELS)` (all known
  Claude models)
- **Commit**: on change — view preference, not a config write
- **Problem**: When the provider exposes exactly one model, this is a 1-option
  select that consumes toolbar space and still renders the OS dropdown arrow.

### 12. Expand / pop-out button
- **Icon**: `<Maximize2 size={16}>`
- **When**: `variant !== "full"` AND `!hideExpand`
- **Action**: `navigate(`/chat/${encodeURIComponent(contextKey)}`)` — opens the
  FocusedChat route in place of the current page

---

## Part 2 — Problems summary

| # | Control | Problem |
|---|---------|---------|
| 3 + 4 | Provider + thread selects | Two unlabeled selects side by side look identical; no visual cue distinguishes "which agent" from "which conversation" |
| 4 | Thread select | Renders a 1-option select when there are no threads — dead control, wasted space, OS arrow still present |
| 5 | New chat button | Duplicates the first option of the thread select; two affordances for one action, neither labeled |
| 7 + 8 | File + Run buttons | Adjacent icon-only buttons (`ListPlus`, `Play`) look like a media pair, not a two-step ticket action |
| 3, 4, 11 | All three selects | Use raw `<select>` instead of the `ws-select` Design System primitive |
| 11 | Model select | 1-option select when a provider exposes only one model — wasted space + OS arrow |
| 3–9 | Left cluster | No visual grouping; nine controls in a flat row with no section boundary or label |

---

## Part 3 — Redesign

### Principle

Every control must have a visible text label **or** be in a context where its
icon is unambiguous (Back, Trash2, Maximize2 are universal; ListPlus/Play are
not). Picker controls use `ws-select`; a 1-option picker is suppressed or shown
as read-only text. The file+run pair collapse into a single labeled dropdown so
the distinction is named, not guessed.

---

### 3.1 Provider select → labeled `ws-select`, hidden when irrelevant

```tsx
// Replace raw <select> with ws-select primitive.
// Show only when providers.length > 1 (no picker for a single option).
// When providers.length === 1, render a plain Tag (read-only context cue, no dropdown).

{showProviderPicker && effectiveMode === "data" && providers.length > 1 && (
  <Select
    className="chat-tb__select chat-tb__provider"
    value={activeProvider}
    onChange={(e) => setActiveProvider(e.target.value)}
    aria-label="Assistant"
    title="Which assistant to talk to"
  >
    {providers.map((p) => (
      <option key={p.key} value={p.key}>{p.label}</option>
    ))}
  </Select>
)}

{showProviderPicker && effectiveMode === "data" && providers.length === 1 && (
  <span className="chat-tb__label ws-muted">{providers[0].label}</span>
)}
```

The two adjacent dropdowns that looked identical now differ visually:
the provider select only appears when there is a real choice; the thread select
always appears (it IS the conversation).

Add a compact inline label "Assistant" before the select (using `.chat-tb__label`
at `font-size: var(--text-xs); color: var(--muted);`) so the two pickers are
self-annotating.

---

### 3.2 Thread select → `ws-select` + remove duplicate New-chat button

The "New chat" `<option>` IS a valid first option in the select (it's the
correct affordance for starting a fresh conversation). The separate `<Plus>`
button duplicates it. **Remove the Plus button; keep the select.**

When the thread list is empty (just loaded or no history), suppress the
entire select and render a plain text label instead:

```tsx
{threads.length > 0 ? (
  <Select
    className="chat-tb__select chat-tb__thread"
    value={activeId ?? ""}
    onChange={(e) => (e.target.value ? openThread(e.target.value) : newChat())}
    aria-label="Conversation"
    title="Conversation"
  >
    <option value="">New chat</option>
    {threads.map((t) => (
      <option key={t.id} value={t.id}>{t.title}</option>
    ))}
  </Select>
) : (
  <Button size="sm" variant="ghost" onClick={newChat}>
    <Plus size={16} aria-hidden /> New chat
  </Button>
)}
```

This eliminates both the dead 1-option select and the duplicate Plus button in
one move. When threads exist, the single select lists them all; "New chat" is the
blank/top option inside the same picker — discoverable without a second button.

---

### 3.3 File + Run → single labeled split-button

Replace the two icon-only buttons with one labeled control:

```tsx
// Primary action: "File ticket" — the safer, more common operation.
// Secondary (via a ChevronDown dropdown): "File and run".
// The combined affordance makes the distinction legible.

{activeId && (
  <div className="chat-tb__split">
    <Button
      size="sm"
      variant="ghost"
      disabled={spawning !== false}
      onClick={() => void spawnTaskFromChat(false)}
      aria-busy={spawning === "file"}
    >
      {spawning === "file"
        ? <Loader size={14} aria-hidden className="ws-spin" />
        : <ListPlus size={14} aria-hidden />}
      File ticket
    </Button>
    <Button
      size="sm"
      variant="ghost"
      icon
      disabled={spawning !== false}
      onClick={() => void spawnTaskFromChat(true)}
      aria-label="File and run now"
      title="File this chat as a ticket and run it now"
      aria-busy={spawning === "run"}
    >
      {spawning === "run"
        ? <Loader size={14} aria-hidden className="ws-spin" />
        : <Play size={14} aria-hidden />}
    </Button>
  </div>
)}
```

The split form keeps both actions one click away (no extra step) while naming
the primary one. A `.chat-tb__split` wrapper gives them a shared border so they
read as one grouped control, not two unrelated buttons.

Add a CSS rule for `.chat-tb__split`:

```css
.chat-tb__split {
  display: flex;
  align-items: stretch;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.chat-tb__split .ws-btn {
  border-radius: 0;
  border: none;
}
.chat-tb__split .ws-btn + .ws-btn {
  border-left: 1px solid var(--border);
}
```

---

### 3.4 Model select → `ws-select`, hidden when single option

```tsx
{modelOptions.length > 1 ? (
  <Select
    className="chat-tb__select chat-tb__model"
    value={selectedModel}
    onChange={(e) => setSelectedModel(e.target.value)}
    aria-label="Model"
    title="Model for this conversation"
  >
    {modelOptions.map((id) => (
      <option key={id} value={id}>{modelLabel(id)}</option>
    ))}
  </Select>
) : modelOptions.length === 1 ? (
  <span className="chat-tb__label ws-muted" title="Model">
    {modelLabel(modelOptions[0])}
  </span>
) : null}
```

When there is only one model the dropdown arrow vanishes; a muted text label
carries the model name as a passive indicator without consuming click target.

---

### 3.5 Toolbar visual grouping

Proposed left-to-right groups with thin `var(--border)` dividers between them:

```
[Back] [Workbench]  |  [Assistant▾] [Conversation▾]  |  [Tasks] [Delete]  |  [File ticket] [▶]
                 spacer
                                                        [Sonnet 4.6]  [⤢]
```

- **Nav group** (back, workbench): orientation controls
- **Context group** (assistant select, conversation select): "where am I" pickers; compact labels "Assistant" / "Conversation" above or beside each select  
- **Chat actions group** (tasks, delete): operations on the current thread
- **Ticket group** (file ticket split-button): spawn work from the chat
- spacer
- **Config group** (model, expand): ephemeral preference + navigation out

Each group is separated by a `1px solid var(--border)` vertical rule. No group
takes more than its natural content width; the spacer absorbs the rest.

---

### 3.6 CSS changes needed

1. Replace every `.chat-tb__select` rule with the appropriate `ws-select`
   override (sizing + compact height, identical to what was there before but
   using the DS token stack so dark/light themes apply automatically).

2. Add `.chat-tb__label` — an inline, muted, `text-xs` label span used for
   both group annotations and the read-only single-option state.

3. Add `.chat-tb__split` group rule (above).

4. Add `.chat-tb__divider` — `1px` vertical rule between groups:
   ```css
   .chat-tb__divider {
     width: 1px;
     align-self: stretch;
     background: var(--border);
     margin: var(--space-1) 0;
   }
   ```

---

### 3.7 What does NOT change

- Delete still goes through `<ConfirmDialog>` — correct, keep it.
- Back, Workbench, Tasks, Expand are icon-only buttons; their icons are
  unambiguous and their placement/context is self-documenting.
- The slash command menu, voice cluster, and composer row are out of scope.
- No behavioral changes, no backend changes — this redesign is purely
  presentation and widget-type fixes.

---

## Part 4 — Implementation checklist

These are the discrete changes an implementation tick would execute:

- [ ] Swap all three `<select className="chat-tb__select …">` for `<Select className="chat-tb__select …">` (ws-select primitive — 3 callsites)
- [ ] Remove the bare `<Plus>` New-chat button; add "New chat" Button fallback when threads is empty
- [ ] Replace the provider `<select>` with a hidden-when-single-option pattern
- [ ] Wrap file + run buttons in `.chat-tb__split`; add "File ticket" text label to the primary button
- [ ] Replace model `<select>` with hidden-when-single-option pattern (read-only text)
- [ ] Add `.chat-tb__label`, `.chat-tb__split`, `.chat-tb__divider` CSS rules to `styles.css` (or a dedicated `chat.css`)
- [ ] Insert `<span className="chat-tb__divider">` between the four logical groups
- [ ] Run `bash scripts/check_design.sh` + `cd frontend && npx tsc --noEmit`
