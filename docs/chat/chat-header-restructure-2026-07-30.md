# Chat header restructure + the open questions behind it (2026-07-30)

Status: **§1–§3 built and shipped this session. §4 is a proposal — nothing in it
has been implemented.**

The report: three selectors in the header plus a second row of four unlabelled
icons. This document answers *why* there were three, says which one earned its
place, and then works through what the confusing icons are actually for.

---

## 1. Why there were three selectors, and which one to keep

| # | Control | What it actually did | Verdict |
|---|---|---|---|
| 1 | **Assistant** | Picks the *provider* — `general` or a per-app assistant. This sets `scope`, which decides what the chat can see and which thread list exists. | **Keep.** The only one whose meaning is structural. |
| 2 | **Chat** | Picks which thread in that scope, with `New chat` smuggled in as option zero. | **Remove.** |
| 3 | **Model** | Which model answers this turn. | **Keep, move down.** |

The bad reasoning was in #2, and the code comment above it said so out loud:
it existed because an earlier pass deleted a redundant `+` button and folded
"new chat" into the dropdown. That inverted the hierarchy — a *navigation*
control (which conversation am I in) was living inside the conversation it
navigates away from, and the one thing it uniquely offered was an unrelated
*create* action hidden as a list item.

Navigation belongs outside. `/chats` already exists and is a better version of
that dropdown: cross-context, filterable, shows every thread regardless of
scope. The header only ever needed to point at it.

So the rule now is: **the header holds what scopes the conversation; the
composer holds what shapes the turn; navigation and low-frequency actions leave
the header entirely.**

- Assistant → header, restyled as a quiet text trigger with one caret.
- Model → composer, immediately left of the slash/voice/send cluster.
- Thread → gone. Back / **All chats** reaches the list; **+** starts a new one.

## 2. What shipped

**Header, one row:**

`[back] [workbench] [Assistant ▾] [chat title]  ······  [agent tags] [+agent] [tasks•3] [+] [⋮]`

- **Assistant ▾** — `Menu`, not `<select>`. Checkmark on the current one.
- **chat title** — a label, not a control. You are told where you are; you
  change it from the list. Hidden under 640px so the row never wraps.
- **+** — new chat, opens a confirm dialog ("this leaves X and opens a blank one
  in <scope>. Nothing is deleted"). Non-destructive styling.
- **⋮** — All chats · Open full screen (dock only) · File as a ticket · File as a
  ticket and run it now · **Delete chat…** (danger).

The second row is gone. Everything in it that was a real action is in the kebab
with a written label; nothing was dropped.

**Composer action row:**

`[context meter] ······ [Model ▾] [voice] [slash] [send] [stop]`

**Restyling.** Both pickers are the same idiom: transparent, no box at rest,
one 14px `ChevronDown`, background on hover, border + background while open.
No stock double-arrow anywhere. The popover is card language one elevation step
below a modal.

**New primitive.** `src/ui/Menu.tsx` (`Menu` / `MenuItem` / `MenuSeparator`) +
`.ws-menu*` in `components.css`. Escape, outside-pointerdown, arrow keys, Home /
End, focus returns to the trigger. `checked` marks a menu as a single-choice
picker and reserves the check column so labels stay aligned. `Select` remains
the right answer for a full-width form field — `Menu` is for control rows.

**Back** now falls back to `/chats` rather than `/agents` when there's no
history, since the chat list is this surface's real parent.

## 3. What the icons were

For the record, since two of them were unguessable:

- **person-plus** — correct guess: adds an agent to this chat (`agent:<slug>`).
  Kept in the header; see §4.1 for whether it should stay a chat-level action.
- **lines-plus** (`ListPlus`) — "file this conversation as a tracked ticket".
- **play** — "file it as a ticket **and start a run on it now**".
- **trash** — delete the thread. Never a first-order action; now in the kebab.
- **list-with-checkmarks** (`ListTodo`, with the count badge) — toggles the task
  panel for this scope. This one stayed visible: it carries live state.

`ListPlus` and `Play` are the same action with a boolean. Two icons for one verb
plus a flag is exactly the kind of thing a menu with sentences fixes.

---

## 4. Proposal — the open questions (NOT implemented)

### 4.1 What is "adding an agent to a chat" really doing?

Today: `participants` is a list of `agent:<slug>` on the thread. That is a
mechanism without a stated meaning, which is why it's hard to place in the UI.
Three candidate meanings, and they are genuinely different products:

**(a) Participants — the chat is a room.**
Agents are addressable speakers. You @-mention one, it answers with its own
memory and tools, and everyone in the room sees it. The chat is a shared
transcript; the agent is a guest.
*Implies:* per-message attribution, mention routing, a decision about whether
guests see history from before they joined. Familiar (Slack/Discord), and the
current data model already points here.
*Cost:* multi-speaker turn-taking is real work, and most of the time you don't
want three agents answering at once.

**(b) Scope composition — the chat is a lens.**
"Adding an agent" doesn't add a speaker, it adds that agent's *context* to the
one assistant you're talking to: its memory, its files, its tools. One voice,
wider view.
*Implies:* the control is really a context/memory picker and belongs next to the
Assistant picker as a set of scope chips, not as a person icon.
*This is closest to what the operator described* ("it's kind of like a scoping
thing right, it's kinda like a scope and which memory we're gonna give it").
*Cost:* needs an answer for conflicting memories and for tool-name collisions.

**(c) Delegation — the chat is a control room.**
Agents aren't in the chat at all. They're *working* — on tickets this chat
filed — and what the chat shows is their live status, with the ability to
interject. "Add agent" becomes "assign this work to X".
*Implies:* the header shouldn't have an add-agent button; it should have a
**presence strip** listing agents currently running work linked to this chat,
each expandable to its run. This is the operator's own reframe: "instead of
adding one to the chat… maybe it's like you want to view the agents that are
executing the plans."

**Recommendation: (b) as the header control, (c) as a strip below it, (a) not
now.** They compose cleanly — (b) says what this conversation can see, (c) says
what's happening because of it, and (a) is a heavier model that can be layered
later if a real need for multi-speaker rooms shows up. Under this split the
person-plus icon is wrong twice over: it should be a scope chip row
(`Workshop · finance · birds  +`), and the agents you actually watch appear on
their own line only when something is running.

### 4.2 File-as-ticket / file-and-run

Both are "promote this conversation to tracked work". The split between them is
a scheduling choice, not two features. Options:

1. **One action, one dialog.** "Create a ticket from this chat" → editable
   title/scope + a **Run it now** checkbox. Honest, one icon, and it lets you
   fix the auto-derived title before filing — which today you can't.
2. **One action, no dialog, undo instead.** File it, show a toast with "Run now"
   and "Undo". Fastest; fits the operator's stated dislike of confirmation
   gating.
3. Status quo in a menu (what shipped — the interim).

**Recommendation: (2), falling back to (1) when the chat is long enough that
title derivation is a coin flip.** Note the standing constraint from
`chat-distilled`: *filing a ticket is not progress until a run completes*, and
the loop has not yet proven itself end-to-end. Until it has, the run variant
should be the deliberate one, not the fast one.

### 4.3 The task list

`ListTodo` + badge already toggles a scope-filtered task panel. It is the one
header control that is unambiguously right, because it carries state (the
count) and toggles a surface rather than performing an action.

What's missing is the link back: a ticket filed from this chat, and any run
against it, should appear **inline in the transcript** as a live card at the
point it was filed — not only in a side panel. That is already an explicit
operator want ("chat should display related tasks/runs with live progress
auto-updating in the originating chat") and the `UiCard` machinery in
`components/chat/UiCard.tsx` is the right vehicle. Proposal: filing emits a
persistent card message; the card subscribes to the run and updates in place.
The panel then becomes the *index*, and the transcript is the *timeline*.

### 4.4 Notes, and what else wants a place

Inventory of things that exist and have no home in the chat surface:

| Thing | Where it lives now | Proposal |
|---|---|---|
| **Notes** (`shared/knowledge/*`) | written by background distillation; invisible in chat | A **"remember this"** action on a message → writes a note scoped to the chat's scope. Retrieved notes already ride in as context; show them as a collapsed "3 notes in context" line above the composer, expandable. Read-back is what makes the memory trustworthy. |
| **Runs** | `/work` | Inline card (§4.3) + presence strip (§4.1c). |
| **Projects / apps** | `/projects`, `/apps` | Already the Assistant picker's scope. Becomes the scope-chip row under (b). |
| **Context usage** | ring under the composer | Correct as-is. |
| **Voice** | composer cluster | Correct as-is; separate track (`docs/voice-mode/`). |

The shape this converges on, top to bottom:

```
scope chips  ·  where you are            [tasks] [+] [⋮]
agents running work from this chat        ← only when non-empty
────────────────────────────────────────────────────────────
transcript, with ticket/run cards inline at the point they were filed
────────────────────────────────────────────────────────────
3 notes in context  ▸
[context ring]                    [Model ▾] [voice] [/] [send]
```

Header answers *what can this conversation see*. Strip answers *what is
happening because of it*. Transcript answers *what was said and what it
produced*. Composer answers *how this turn goes out*. Nothing in the header
performs work, and nothing that performs work is unlabelled.

---

## Files touched (§1–§3)

- `frontend/src/ui/Menu.tsx` — new primitive
- `frontend/src/ui/index.ts` — export it
- `frontend/src/styles/components.css` — `.ws-menu*`
- `frontend/src/components/Chat.tsx` — header restructure, composer model
  picker, new-chat confirm, kebab; `openThread` removed (dead once the thread
  dropdown went)
- `frontend/src/styles.css` — `.chat-tb__picker`, `.chat-tb__title`,
  `.chat-model`; dead `.chat-tb__thread/provider/model/label` rules deleted

Verified: `npx tsc --noEmit` clean, `scripts/check_design.sh` clean, `vite build`
clean, public bundle rebuilt.
