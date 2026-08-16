# Tickets in chat — build plan

Date: 2026-08-15. Source: the operator's design handoff `Tickets in Chat.dc.html`,
option **4a** (with the Work panel and policy screens from 2a). This document is the
build spec. It states what the handoff asked for, what our system actually has, and
which of the two wins in every place they disagree.

**Nothing here is additive.** Every surface it introduces replaces a surface that is
deleted in the same change. A duplicate left behind is a defect, not a leftover.

---

## 1. What this changes, in one paragraph

Tickets, runs and agent questions stop being three things scattered across a toolbar
menu, a drawer and a modal, and become two: **a card in the answer** (one ticket, the
work it names, and how that work is going) and **the Work panel** (everything in this
chat's scope that is waiting, running, or was filed here, plus the chat's ticket
policy). One toolbar button opens the panel. The three-dot menu stops carrying work
controls entirely.

---

## 2. The handoff's assumptions that are wrong, and what we build instead

Measured against the code, not assumed. Each of these would have produced a screen
with no data behind it.

| The handoff draws | Reality | We build |
|---|---|---|
| `WS-412` ticket keys | No such field on the wire. Postgres has `project.key`+`number`, and `tasks._normalize` discards it before serialization. The live store is the file store, which has no counter | `ticketKey(id)` — the first 6 hex of the uuid, already the app's handle (`lib/tasks.ts:453`) |
| `Blocks WS-413` | `blocks` does not exist: not stored, not derived, not sent (`tasks.py:867`) | `Blocked by`, from `blocked_by` (the real inbound edge, non-terminal subset of `depends_on`), rendered only when non-empty |
| Run's "latest tool call", one line on the card | Does not exist on a run. Tool events belong to chat turns only; there is no run SSE and no step data on a job | Nothing. The Run group shows what is real: agent, trigger, elapsed or duration, and on completion the receipt's files-changed and commits |
| `Labels: infra, audit` | The field exists and is 97% empty (3 distinct labels across 109 tickets) | Render only when non-empty. Never a placeholder row |
| Filled status pills in a custom `--st-*` palette | The kit has five status kinds, status is **outline**, `running` is the only filled one | `StatusPill` / `StatusDot` with `taskStatusKind`. No new colour, no `--st-*` |
| Work settings: "Interrupt me", "Show ticket cards in chat", "Run in parallel" | None of the three exists as a setting anywhere | Cut all three. A settings row with nothing behind it is placeholder UI |
| `Stop` button on a running card | There is no endpoint that stops a run. `POST /jobs/{id}/tasks/{tid}/action` cancels a *waiting task within* a job, not the run | No Stop. Recorded as a real gap in §9 |

Two of the handoff's own open questions are answered by the code:

- **"Does the latest tool call stream to the card, or is it polled?"** Neither. It does
  not exist. See above.
- **"Answering another agent's question needs an endpoint."** It already exists.
  `POST /inbox/{id}/resolve {answer, dismissed}` takes arbitrary free text and writes
  through to `POST /tasks/{scope}/{id}/review`, refusing to close the row if that write
  fails (`routes/inbox.py:141-193`). No new endpoint.

The remaining two we decide here:

- **Policy per chat, per scope, or both?** Per chat only. No per-scope ticket policy
  exists and inventing one is a second governance axis nobody asked for. The
  workshop-wide autonomy override stays the ceiling above it, stated in the footer.
- **Does "All" paginate?** No. It caps at 8 rows and links to the board. A popover is
  not a list page.

---

## 3. What gets deleted

The point of the exercise. Each line is a deletion, not a hide.

| Deleted | Replaced by |
|---|---|
| `chat/WorkDrawer.tsx` (212) | `chat/work/WorkPanel.tsx` |
| `chat/InboxStrip.tsx` (414) — component | Panel rows + `QuestionView`. The `useInboxItems` hook's *job* moves into `useChatWork`; the component goes |
| `chat/FiledNotice.tsx` (60) and the `FiledBar` slot in `Chat.tsx` | The ticket card in the answer, which says the same thing in the place the filing happened |
| `JobStatusCard` + the `job-status` card kind (`UiCard.tsx:377-487`, `uiCardActions.ts:74`) | `TicketCard`. The kind is already dead: the backend stopped appending it |
| The `success`-phase card body (`UiCard.tsx:628-643`) | `TicketCard` in its place |
| Toolbar overflow: the `Run work as tickets` **Toggle** row | The three-way policy in Work settings |
| Toolbar overflow: `File this chat as a ticket` | The panel header action |
| ~~`components/AutonomyPanel.tsx` (737), zero importers~~ **Wrong, and kept.** | It has an importer: `ProjectDetail` renders it. Two greps said otherwise because `ProjectDetail.tsx` held literal NUL bytes, so every text search treated the file as binary and skipped it **without saying so**. That is the second time this repo has been bitten by it (398948e). The bytes are now written `\0` and the file is searchable; the panel stays |
| `.chat-inbox*` and `.chat-spawn-note*` in `styles.css` | `.chat-work*` |

Kept deliberately, and why:

- **`TicketDraftsDialog`** stays. Reviewing N proposals with editable bodies is modal
  work and the handoff has no surface for it. It moves entry point (panel header, not
  the three-dot) and its rows adopt the new ticket row idiom. It is not a duplicate of
  the card: the card tracks one filed ticket, the dialog drafts several unfiled ones.
- **`chatModes.ts`** stays and gains a third value. It is shared with Settings.
- **`TaskPanel.tsx`** stays untouched: it is `AgentDetail`'s, not the chat's.

---

## 3a. What is editable where

One rule, because "can I change this here" should not be a per-screen discovery:

**The chat shows and acts. The ticket page edits.**

- A **card** is read-only about the ticket and carries only actions that move work
  along: open it, view its run, run it now.
- A **panel row** is read-only. Same reason the drawer it replaces was
  (`WorkDrawer.tsx:30-37`): `TicketDetail` edits every field, and a second editor of the
  same fields is a second thing to keep correct and a second place for them to disagree.
- The two things the chat surface genuinely owns, it edits: **the answer to a question**
  (which is a write the ticket page cannot do as well, because the question is addressed
  to this conversation) and **this chat's work policy**.
- **Ticket drafts** are the exception that proves it: their bodies are editable in the
  drafts dialog because an unapproved draft is not yet a ticket, and its body is the
  entire spec a worker will read.

---

## 4. The card

A filed ticket renders as a `ui-card` **inside the assistant's answer**, which is what
the handoff's 4a is built on and what `chat-work-linkage-2026-07-18.md:70-85` decided
and never built. The card is the subject's home: one ticket, one card, for the life of
the thread.

**Collapsed** is one row: ticket glyph, title, status pill, chevron. Nothing else. It
is a line in a paragraph of prose and must read as one.

**Expanded** is labelled groups, in this order, each omitted entirely when it has no
data:

- **Task** — the ticket body, clamped.
- **Ticket** — `Key` (scope / ticketKey), `Blocked by` (when non-empty), `Labels`
  (when non-empty), `Effort` (when set).
- **Run** — the latest run only: agent name, trigger, and either a live elapsed clock
  or the final duration; on a finished run with a receipt, files-changed and commits.

Actions, all of which resolve to routes that exist: **Open ticket**
(`ticketHref`), **View run** (`runHref`, only when there is a run), **Run now**
(`POST /tasks/{scope}/{id}/run`, only when the ticket has never run and the scope can
run work).

### ADR 0009 and the two statuses

ADR 0009 forbids two status indicators for one entity in one view component, and
requires that a view showing two entities separate them into labelled sections. A card
that shows ticket status and run status is exactly the case it names. The resolution,
stated so nobody has to re-derive it:

**The card's subject is the Ticket.** The header pill is the ticket's status and is
canonical for the ticket. The Run group is a separate, labelled section whose status
belongs to the run. They are never in the same row. The failed-run-but-ticket-still-todo
case therefore reads correctly instead of being masked by a merged status, which is the
alternative 0009 explicitly rejected.

### Every state the card can be in

1. **Proposal, not yet filed** — the `task-proposal` edit form: **scope, title,
   description, and nothing else**. This section used to say "unchanged", and that was
   the leftover the operator caught on 2026-08-15: the form still asked for objective,
   output, entry_points, boundaries, priority and effort as six separate controls, which
   is the pre-010a9a4 shape (a ticket is a title and a description) and a second editor
   of fields §3 gives to `TicketDetail`. The four spec sections are labelled lines
   inside the description, which is where the readiness checker reads them from anyway.
2. **Filed, never run** — pill `to do`. No Run group. Actions: Open ticket, Run now.
3. **Running** — pill `in progress`. Run group with a live elapsed clock in the LIVE
   tone. Actions: Open ticket, View run.
4. **Blocked / awaiting an answer** — pill `blocked`, plus one `chat-note` line under
   the card: `<agent> is waiting on an answer` and an **Answer** link that opens the
   Work panel on that question. The line is client-rendered from tracked state, not a
   transcript message, so it does not violate D4 (agent messages do not land in the
   transcript).
5. **In review** — pill `in review`.
6. **Done** — pill `done`; the Run group keeps the receipt counts.
7. **Run failed, ticket not done** — ticket pill unchanged, Run group says `failed`.
   Both true, both shown, separately labelled.
8. **Merged into an existing ticket** — the card tracks the surviving ticket and carries
   one muted line saying it merged. `dedup_add` already returns the winner's id.
9. **Ticket deleted** — a `Notice` line inside the card: `This ticket no longer exists.`
   Never a `StateBlock`: the card around it is still on screen.
10. **Scope cannot run work** — no Run now action; the existing pickup warning stands.

---

## 5. The Work panel

One button, the one that is already there: the `Ticket` icon in the toolbar, badge =
**needs-you count only**. That rule is load-bearing and stays: *"An open-ticket count is
ambient information wearing an alarm's clothing"* (`WorkDrawer.tsx:25-28`). Forty open
tickets is a normal queue, not an alarm.

**Geometry.** Wide (`useIsWide`, the existing 900px line): a popover anchored to the
button. Narrow: the top sheet over the log that exists today, with a scrim. Top-anchored
on purpose: an on-screen keyboard shrinks the viewport from the bottom, so a
top-anchored panel with a text field in it never fights the keyboard. One component,
two hosts.

**Body.** Filter chips with counts: `Needs you` · `Running` · `This chat` · `All`, then
rows grouped by section, then a link to the board. Rows are one line: status dot, key,
title, and the state word as plain text. A needs-you row shows the asking agent and the
question's first line.

**Footer.** The current policy in words, and `Change`, which swaps the panel to Work
settings. A gear in the header does the same thing.

**Work settings** (the whole surface, nothing else):

- **When work comes up** — three exclusive options:
  - `Keep it in chat` — no tickets filed from here. (mode `direct`)
  - `Draft, then ask me` — tickets appear as cards and wait for approval. (mode
    `orchestrated`)
  - `File and run on its own` — only stops for questions it cannot answer. (mode `auto`,
    new; see §7)
- **Files into** — read-only, derived from the chat's scope. Not a second scope picker;
  the toolbar already owns that control and two of them would disagree.
- A footer line stating the workshop-wide autonomy state, from
  `GET /api/agents/autonomy/status`, linking to `/settings`.

### Answering a question, and how it stays out of the composer's way

The rules, because this is the interaction most likely to be got wrong:

- **The answer field is never autofocused.** Opening the panel must not raise a phone
  keyboard or steal the caret from the composer.
- **Enter in the answer field submits the answer.** It never reaches the chat composer.
- **Escape closes the panel** and returns focus to the Tickets button.
- **A half-typed answer survives closing.** Draft text lives in the hook keyed by item
  id, which is the existing decision (`InboxStrip.tsx:33-39`) and the reason the panel
  can close on outside click without a confirm.
- **Narrow: the scrim makes it exclusive.** The composer is behind it, so only one text
  entry is reachable at a time. Wide: both are visible, and the rules above keep them
  from competing.
- **No layout writes inside the tap handler.** The panel opens on the next frame. A tap
  lost to our own reflow is a bug this app has had before.
- Multiple questions get `1 of N` with Next, and switching does not discard a draft.
- **No "discuss in chat".** It would post into the thread, which D4 rules out and ADR
  0021 would refuse mid-turn. There is no endpoint for it. Recorded in §9.

---

## 6. One poll, one truth

Today the drawer polls `/api/inbox` at 20s and `/api/tasks` at 15s, and each tracking
card would add two more. Instead: **one aggregated read, one client store.**

`GET /api/chat/threads/{thread_id}/work` returns, in one payload:

- `inbox` — the chat's open inbox items (existing `inbox.list_items` + reconcilers)
- `filed` — tickets whose `source.kind == "chat"` and `source.ref == thread_id`, any
  scope, each with its **latest run** summarised (`id, status, started_at, ended_at,
  agent_name, trigger, exit_code`, and receipt `files`/`commits` counts when present)
- `scope` — active tickets in the chat's task scope, capped
- `counts` — the four chip counts

`useChatWork(threadId, scope)` polls it and is the **only** source for the badge, the
panel and every card. A card whose ticket is absent from the payload falls back to a
direct fetch; that is the only second reader.

This is what makes the single-canonical-status rule true in practice rather than by
assertion: the badge, a panel row and a card cannot disagree about a ticket, because
there is one copy of it.

---

## 7. The one backend behaviour change: a third chat mode

`CHAT_MODES` becomes `("direct", "orchestrated", "auto")`. `direct` stays the default.
`auto` means: file and run without asking, stop only for a question that cannot be
answered. It is the third policy option the handoff draws, and without it that control
is a two-way switch pretending to be a three-way one.

Implementation is deliberately narrow: the mode enum, the check constraint plus its
migration, one persona fragment in `chat_context` (the mode line is already re-stated
every turn, so a mid-chat change takes effect on the next one), and the Settings
default-mode control.

**This supersedes ADR 0006 for the chat-policy case.** 0006 (Proposed, unbuilt) designed
auto-run as a per-kind preference *learned* from File-and-run choices, stored in the
learnings store and scoped project > build > general. We are not building learning here:
this is an explicit operator choice on one chat, which is also what
`work-placement-and-preference-learning:151-165` requires of anything implicit
(proposal-only, never silent install). 0006 stays Proposed; the ADR gets a note pointing
at this document.

---

## 8. Pages: the smallest set of edges that stops the dead ends

Extrapolated from the chat work, not a page redesign. Every one of these is a link whose
data is **already on the wire and thrown away**.

- **Runs feed rows** link to their ticket (`job.task_id` is fetched and only
  `job.title` is used) and to their chat (`job.chat_context_key` is fetched and used
  only to pick an icon).
- **RunDetail**: the Overview cross-links are `Row onClick` with a pointer cursor, so
  they have no middle-click and no keyboard focus. They become `Link`s.
- **Agents**: the last-run line is printed and not linked. It links to `runHref(id)`.
- **TicketDetail**: links to chats and runs already; gains the agent link it lacks.
- `runHref` replaces nine hand-spelled `/runs/${id}` and `/jobs/${id}` strings.

Out of scope: any restructuring of those pages, the `Run` → `Session` rename (P5), and
the inline-style debt on `RunDetail`/`Agents`.

---

## 9. Gaps this work does not close, recorded rather than papered over

- **No run-stop endpoint.** Hence no Stop on the card. A general "cancel this run" is
  worth having and is not this change.
- **No "discuss in chat".** Named as the key differentiator in
  `clarifying-question-flow-2026-07-18.md:140-142`, removed in fact by D4's inbox-first
  rule, with nothing resolving the contradiction.
- **`awaiting` is empty on all 109 live tickets.** The question surface is built and
  correct and will mostly render empty until something asks. Do not "fix" it by
  inventing data.
- **`job.chat_id` is declared and never written** (0/2761). A run reaches its chat only
  through its ticket's `source`. `filed` in §6 relies on the ticket edge, not this one.

---

## 10. Work packages

Ordered by dependency. A, B and E are independent; C precedes D because D consumes C's
shared row and store.

- **A — kit.** A `Popover` primitive: an anchored surface reusing `menuPlacement`, with
  no roving focus and no close-on-activate, because `Menu`'s keyboard model fights a
  panel containing a text field. `.ws-popover` rules, MINOR bump, CHANGELOG, api
  snapshot.
- **B — backend.** `GET /chat/threads/{id}/work` and its service; the `auto` chat mode
  with its migration and persona fragment. Tests.
- **C — the panel.** `chat/work/`: `WorkPanel`, `QuestionView`, `WorkSettings`,
  `TicketRow`, `useChatWork`, and the pure rule modules `workRows.ts` (chips, grouping,
  counts) and their tests. Toolbar edits. Deletes `WorkDrawer` and `InboxStrip`.
- **D — the card.** `chat/work/TicketCard.tsx` and `ticketCard.ts` (which groups and
  actions a given ticket+run state renders) with tests. Rewires `UiCard`, deletes
  `JobStatusCard`, the `job-status` kind and `FiledNotice`.
- **E — pages.** §8, plus deleting `AutonomyPanel.tsx`.

Every package: `npm run typecheck` and `bash scripts/check_design.sh` clean, frontend
tests pass, four list states on anything that lists, no inline hex, lucide only, no
em-dash in any string a person reads.

---

**Superseded on appearance (2026-08-16).** This document is still accurate about
what each surface DOES and why. Its visual decisions re-derived the handoff from
our own primitives rather than building what option 4a drew, which the operator
rejected: "the whole point of it was to style it and redesign it and you
basically like changed it all". The visual spec now lives in
`tickets-in-chat-visual-fidelity-2026-08-16.md`, with the design files kept
alongside it in `tickets-in-chat-design/`. Where the two disagree about how
something LOOKS, that one wins.
