# Tickets in chat: build the design that was drawn (2026-08-16)

**Status: PLAN, not built. Nothing in this document has been implemented.**

Implement option **4a** of the `tickets_in_chat` handoff. The behaviour shipped on
2026-08-15; the VISUALS did not. This plan is only about closing that gap.

## Read these first, in this order

1. `docs/platform-redesign/tickets-in-chat-design/f-4a-desktop.png` — the chosen
   option, desktop. **This one image settles most questions.** Open it.
2. `docs/platform-redesign/tickets-in-chat-design/f-4a-s3.png` — the expanded
   ticket card, phone.
3. `docs/platform-redesign/tickets-in-chat-design/f-4a-s5.png` — answering a
   question in the Work panel, phone.
4. `docs/platform-redesign/tickets-in-chat-design/4a-outline.txt` — every screen
   above as an indented outline carrying the exact values. Easier to read than
   the HTML. Line 229 is the card, line 408 the question view, line 620 the panel.
5. `docs/platform-redesign/tickets-in-chat-design/HANDOFF-README.md` — the
   designer's own summary.
6. `docs/platform-redesign/tickets-in-chat-2026-08-15.md` — what was built and
   why. Still accurate about BEHAVIOUR; this document supersedes it wherever the
   two disagree about APPEARANCE.

`Tickets-in-Chat.dc.html` is the full original. It holds older options (1a, 2a,
3a) as well. **Ignore them.** 4a is the newest and the chosen one; it is first in
the handoff README's list. 2a in particular draws a different panel (inline
answer buttons on the list item) that was NOT chosen.

## Why this plan exists

The 2026-08-15 pass treated the handoff as a source of requirements and then
re-derived the visuals from our own primitives. The result is behaviourally right
and visually not the design: `SectionLabel` uppercase where the design has
sentence case, bare `Props` lists where the design has inset panels, ghost
buttons where the design has chips, a second meta line where the design puts the
state word right-aligned on the first. Operator, 2026-08-16: "the whole point of
it was to style it and redesign it and you basically like changed it all".

**So the default is now inverted. Build what the design draws.** Deviate only
where the DATA does not exist, and when you do, say so in a comment naming the
field that is missing. Do not deviate because our system usually does it another
way. Three deviations are pre-approved and listed at the end; there should be
very few others.

## Token mapping (do this before you write any CSS)

The design's markup uses `--st-*` and `--inset`, which **do not exist in this
repo**. Map them once:

- `--inset` is our `--bg-inset`.
- `--st-doing` / `--st-review` / `--st-blocked` / `--st-done` are our status
  tones: `--running` / `--warn` / `--danger` / `--ok`. Prefer going through
  `StatusDot` / `taskStatusKind()` rather than naming a tone directly.
- `--field`, `--border`, `--card`, `--bg`, `--faint`, `--muted`, `--fg`,
  `--accent`, `--accent-fg` all exist as-is.
- The design's 9.5 / 10.5 / 11.5 / 12.5 / 13 / 14.5px land on `--fs-xs`,
  `--fs-sm`, `--fs-md`. **Use the nearest token; never paste the px value.**
  `frontend/CLAUDE.md` rule 2 and `check_design.sh` are binding: no inline hex,
  no inline styles, lucide icons only, no em-dashes in user-visible copy.

New shared visuals belong in the `.chat-*` blocks of `frontend/src/styles.css`
next to the rules they extend, not at the call site.

## 1. The expanded ticket card

File: `frontend/src/components/chat/work/TicketCard.tsx` plus the
`.chat-ticket-card*` block in `frontend/src/styles.css`.
Reference: `f-4a-s3.png`, outline line 229.

The COLLAPSED card (glyph, title, status pill, chevron) already matches. Leave it.

1. **Group labels are sentence case.** "Task", "Ticket", "Run" in
   `color: var(--fg)`, `--fs-xs`, `font-weight: 500`. Today they are
   `<SectionLabel>`, which renders uppercase. Use a card-local label class.
   This is a deliberate, operator-directed departure from the uppercase
   `SectionLabel` motif in `frontend/CLAUDE.md` rule 6, for this dense in-
   transcript card only. Do not change `SectionLabel` itself or any other screen.
2. **"Ticket" fields sit in an inset panel.** `background: var(--bg-inset)`,
   `border-radius: var(--radius-md)`, padding about `var(--space-2) var(--space-3)`,
   rows stacked with a small gap. Each row is a muted label ("Key", "Blocks",
   "Labels") then the value. Today it is a bare `Props`/`Prop` list with no panel.
   Keep the existing rule that an empty field is dropped rather than rendered.
3. **"Run" sits in the same inset panel treatment**, first line: a square agent
   avatar (about 18px, `--accent` background, `--accent-fg` text,
   `border-radius: var(--radius-sm)`, weight 600, the agent's initial), the agent
   name, the run's short id in `--faint`, then the elapsed or final duration
   pushed RIGHT in `--muted`. Keep the run's status word in that line; it is not
   the ticket's status and ADR 0009 requires the two stay separate.
4. **Actions are chips.** `background: var(--field)`,
   `border-radius: var(--radius-md)`, about `7px 11px`, `--fs-sm`, weight 500,
   with a lucide `ArrowUpRight` after the label on the two navigating actions.
   A secondary action has no background, just `--muted` ink. Today they are
   `ws-btn--ghost`.

`ticketCard.ts` is a PURE RULE module with 39 tests. It decides WHAT renders.
**Do not change its action gating, its note ranking, or its tests.** The avatar
initial and the run short id are presentation and can be derived in the component.

## 2. The Work panel

Files: `frontend/src/components/chat/work/TicketRow.tsx`,
`WorkPanel.tsx`, and the `.chat-workpanel*` block in `styles.css`.
Reference: `f-4a-desktop.png` (the panel is on the right), outline line 620.

1. **A row's state word is right-aligned on the title's line.** Line 1 is: status
   dot, the key in `--faint`, the title taking the free space, then the state word
   in `--muted` pushed right. Today `TicketRow` puts a second line under the title
   reading "todo · now"; the relative time is dropped from a non-running row.
2. **A RUNNING row becomes a bordered card** (`background: var(--bg)`,
   `1px solid var(--border)`, `border-radius` about 10px) and gains a second line:
   a small agent avatar (about 16px), then `<agent> · <elapsed>` in `--muted`.
   Non-running rows stay plain, with no card and no border.
3. **Needs-you items are cards, not rows.** Same card treatment. Line 1: a square
   avatar carrying the agent's initial, tinted by kind (question/review tone vs
   `--accent`), then `<agent> asks` / `<agent> proposes` / `<agent> says` in weight
   600, a spacer, the ticket key (or the state word when there is no ticket) in
   `--faint`, then a `ChevronRight`. Line 2: the item's first body line in
   `--muted`. Derive the verb from `item.kind` (question -> asks, review ->
   proposes, notify -> says).
4. **The group heading carries its count**: `This chat · 3`, `--faint`,
   `--fs-xs`, `text-transform: uppercase`.
5. **The footer states the policy value alone** ("Draft, then ask me") with
   `Change` as the accent link. Drop the "When work comes up:" prefix.

Constraints that are load-bearing and must survive:

- A ticket row stays a `<Link>` and a needs-you item stays a `<button>`. That is
  what gives middle-click and open-in-new-tab on one and the question view on the
  other.
- Keep `ws-truncate` on the title AND the `min-width: 0` on `.chat-workpanel` and
  `.chat-workpanel__scroll`. Removing either brings back the bug fixed in
  `877b385`: the title sizes past the popover's cap and is cut mid-word with no
  ellipsis. Re-verify by measuring `scrollWidth > clientWidth` on a long title.
- Do not touch the popover/sheet geometry, the scrim, Escape focus-return, or the
  answer-draft handling.
- `workRows.ts` is a pure rule module with 30 tests. Grouping and the 8-row cap
  do not change. A heading's count is a rendering concern.

## 3. The question view

File: `frontend/src/components/chat/work/QuestionView.tsx`.
Reference: `f-4a-s5.png`, outline line 408.

1. **The offered answers are full-width stacked buttons**, `background:
   var(--field)`, `border-radius: var(--radius-md)`, about `10px 12px`, weight
   500. Today they are inline `Button size="sm"` in a row.
2. **The question body gets the asking agent's avatar** to its left, the same
   square as the panel's needs-you card, tinted by kind.
3. **"Answer" and "Or write one" are sentence-case labels**, matching the card.
   Today they are uppercase `SectionLabel`.
4. **The send row sits BELOW the input**: the muted hint ("Sends to `<agent>` and
   unblocks the run") on the left, the primary `Send` on the right. Today the
   input and Send share a row and the hint is underneath.
5. **The foot is text-weight actions**: "Open ticket" and "View run" muted on the
   left, a spacer, "Later" on the right. Keep Snooze; give it the same treatment
   rather than a bordered ghost button.

Do not change: Enter submitting and never reaching the composer, the per-item
draft that survives closing, the "1 of N" pager, or the resolve call.

## Deviations that are pre-approved

Take these and nothing else without saying why in a code comment:

1. **No tool-call line.** The design puts a live tool call on the run (card) and
   on a running panel row ("grep · open\(.\*\.wav"). There is no such data: tool
   events belong to chat turns, a job record carries no step data and there is no
   run stream. Omit the line. On the CARD, where a receipt exists, render
   "4 files changed, 2 commits" in that slot instead.
2. **No `WS-412` keys.** We have no per-scope ticket numbering; the handle is the
   id's first six hex, which `ticketKey()` already returns. Use it wherever the
   design shows `WS-412`.
3. **No Stop action.** The design's card has one. There is no endpoint that stops
   a run. Leave it out rather than drawing a dead control.

Everything else in the design is buildable from data we have. If you find a
fourth case, put the reason in a comment naming the missing field.

## Definition of done

- `npm run typecheck` (repo root) and `bash scripts/check_design.sh` clean.
- `cd frontend && npm test` green, including the 69 tests in
  `src/components/chat/work/`.
- **Compared against the design by eye, not by reading the diff.** Drive the real
  app, open the panel and expand a filed card, and put your screenshot beside
  `f-4a-desktop.png` and `f-4a-s3.png`. The 2026-08-15 pass passed every gate and
  still did not look like the design, so the gates are necessary and not
  sufficient here.
- `bash scripts/apply.sh` last, because the operator reads the built origin.

There is a working recipe for driving the real app: mint a session with
`auth_store.create_session` the way `scripts/smoke_chat.sh` does, drive
`http://127.0.0.1:8002` with the chromium under `~/.cache/ms-playwright`, and
revoke the session afterwards. Filing repeatedly from the chat will MERGE into
one ticket rather than creating new ones (`task_dedup`, cosine >= 0.90), so vary
the ticket text between runs or the surface will look broken when it is not.
