// What the inline decision under a `ticket_question` message should show.
//
// The rule, not the render, so it can be executed: this repo has no DOM harness
// and is not getting one, so the target of a test is always the pure rule behind
// a behaviour (`turnBatching`, `transcriptBlocks`, `workRows`).
//
// WHY IT READS LIVE STATE RATHER THAN REMEMBERING WHAT THIS CARD DID. The same
// question can be answered from three places: here, the toolbar's Tickets panel,
// and the ticket page. A control that remembered its own click would keep
// offering buttons for a question somebody already answered elsewhere. So the
// view is derived from the chat's one work payload every time, which is the same
// source the panel reads, and answering anywhere resolves everywhere.
//
// Design: docs/platform-redesign/deciding-on-work-from-a-chat-2026-08-22.md

export type DecisionView =
  /** Draw nothing: no ticket, nothing read yet, or a ticket this chat cannot see. */
  | { kind: "hidden" }
  /** Waiting on the operator. `finished` picks which pair of answers applies. */
  | { kind: "pending"; itemId: string; question: string; finished: boolean }
  /** Already decided, kept in place so the transcript still reads as history. */
  | { kind: "resolved"; said: string };

export type DecisionInput = {
  /** From the message's own `meta.ticket`. */
  ticketId: string | null | undefined;
  /** True until the work payload has been read once. */
  cold: boolean;
  /** Open rows, from the chat's work payload. */
  inbox: { id: string; ticket_id: string | null; body: string }[];
  /** The ticket's current status, or undefined if this chat does not carry it. */
  status: string | undefined;
};

export function decisionView(input: DecisionInput): DecisionView {
  const { ticketId, cold, inbox, status } = input;
  if (!ticketId) return { kind: "hidden" };
  // Nothing has been read yet, which is NOT the same as having read an empty
  // payload. Guessing here would flash "Accepted" under a live question.
  if (cold) return { kind: "hidden" };

  const item = inbox.find((i) => i.ticket_id === ticketId);
  if (item) {
    return {
      kind: "pending",
      itemId: item.id,
      question: item.body,
      // A finished ticket has two answers and both are decisions. A stuck one
      // wants prose, and may want a grant or a rule with it, so it is handed to
      // the panel that already knows how to offer those.
      finished: status === "in_review",
    };
  }

  // No open row. Either it was decided, or this chat cannot see the ticket at
  // all, and those must not look the same.
  if (status === undefined) return { kind: "hidden" };
  if (status === "done") return { kind: "resolved", said: "Accepted" };
  if (status === "cancelled") return { kind: "resolved", said: "Cancelled" };
  // Still waiting, but the row has not arrived yet: say nothing rather than
  // claim a decision that has not happened.
  if (status === "in_review" || status === "blocked") return { kind: "hidden" };
  // Back in the queue. It reached here by being answered or sent back, and
  // nothing on the record distinguishes those two after the fact, so the word
  // covers both honestly rather than guessing which.
  return { kind: "resolved", said: "Answered" };
}
