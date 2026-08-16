# Tickets in chat — design handoff

`Tickets in Chat.dc.html` — open in a browser. Light/dark toggle at the top. Each option scrolls sideways: phone frames first, desktop after.

## Options, newest first

- **4a — current proposal.** Built on the real transcript structure: one assistant bubble, `chat-work` (steps toggle) above the answer, ticket cards as `ui-card`s in the answer. Five states: resting, steps open, card open, another agent asking, answering in the Work panel. Plus desktop.
- **3a** — right data model (ticket / run / agent), wrong turn rendering. Kept for reference.
- **2a** — first consolidation of the Work button + question surface.
- **1a–1d** — first round. 1a is the baseline as shipped today.

## The proposal in short

**One toolbar button.** Tickets and tasks are one object — a running ticket *is* the task — so the bell, ticket menu and task drawer collapse into a single ticket-icon button with one badge. It opens the Work panel: a top sheet on mobile, a popover on desktop. Filter pills (Needs you / Running / This chat / All), rows grouped by section, a link to the board, and the chat's ticket policy in the footer with a Change link. A gear in the header swaps the panel to Work settings (policy: keep it in chat / draft then ask / file and run; where it files; when it interrupts; parallelism).

**Ticket cards live in the answer.** A newly filed ticket renders as a `ui-card` inside the assistant's bubble, collapsed to one row (icon, title, status pill, chevron). Expanded it shows labelled groups — Task, Ticket (key, blocks, labels), Run (agent, run id, duration, latest tool call) — and the real actions: Open ticket → `/tickets/{scope}/{id}`, View job → `/runs/{id}`, Stop.

**Questions.** If this chat is running the work, the agent asks in the thread as it already does (ApprovalBlock). If another agent is running it, the question belongs to the ticket and is answered in the Work panel — option buttons *and* a free-text field. The in-chat card carries only a one-line `chat-note` with an Answer link.

## Grounded in

| Piece | Source |
| --- | --- |
| Turn structure, work/answer split | `frontend/src/components/chat/Transcript.tsx` |
| Bubble, blocks, notes | `frontend/src/components/chat/MessageList.tsx` |
| Segment kinds, agent chips | `frontend/src/components/chat/segments.ts` |
| Ticket cards, job status, receipts | `frontend/src/components/chat/UiCard.tsx` |
| Ticket model, hrefs, scope labels | `frontend/src/lib/tasks.ts` |
| Inbox questions | `frontend/src/components/chat/InboxStrip.tsx` |
| Chat/ticket styles | `frontend/src/styles.css`, `packages/ui/src/styles/components.css` |

## Open questions

1. Does a run's latest tool call stream to the ticket card, or is it polled with the job (`GET /api/jobs/{id}`)? The card shows one line of it.
2. Answering another agent's question from the Work panel needs an endpoint — the inbox has the question, but not a reply path for a free-text answer.
3. Do ticket policy settings live per chat, per scope, or both? The panel footer assumes per chat with a global fallback.
4. Should the Work panel's "All" tab paginate, or just link out to the board past N rows?
