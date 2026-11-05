// What a spoken "cancel" does to a message just sent, decided from one reading
// of the chat. The cases and their order are the design doc's
// ("Send and cancel" in docs/dictation-and-clipboard.md); this is the table
// and nothing else, so it runs in a test. chatTargetsSend.ts carries it out.

import type { LogEvent, ThreadEventsPage } from "../chat";

export type CancelSnapshot = {
  /** The send request has answered. */
  answered: boolean;
  /** It answered with the server holding the message. */
  sendOk: boolean;
  /** The message is on the thread's queue, not in the log yet. */
  queued: boolean;
  /** The message as the log recorded it, when it did. */
  message: { id: string; seq: number } | null;
  /** Nothing in the chat comes before the message. The server cannot go back
   *  from there, so the chat itself is unsent: stopped, archived, and its
   *  words returned to the scope's new chat. */
  firstMessage: boolean;
  /** The turn in flight, when one is. */
  activeTurn: { turnId: string; startedSeq: number | null } | null;
  /** A turn ended after the message was recorded. With a turn in flight as
   *  well, that one is not the message's own. */
  turnEndedSinceMessage: boolean;
  /** An operation (/compact, /clear) holds the thread. */
  busy: string | null;
};

export type CancelStep =
  | { kind: "wait" }
  | { kind: "notSent" }
  | { kind: "unqueue" }
  | { kind: "stop"; turnId: string }
  | { kind: "goBack"; anchor: string }
  | { kind: "unsendChat"; turnId: string | null }
  | { kind: "refuse"; reason: string };

export function cancelStep(s: CancelSnapshot): CancelStep {
  if (!s.answered) return { kind: "wait" };
  if (!s.sendOk) return { kind: "notSent" };
  if (s.queued) return { kind: "unqueue" };
  if (!s.message) return { kind: "refuse", reason: "The message is not in the chat yet." };
  if (s.firstMessage) return { kind: "unsendChat", turnId: s.activeTurn?.turnId ?? null };
  if (s.activeTurn) {
    if (s.turnEndedSinceMessage) return { kind: "refuse", reason: "Another turn started." };
    return { kind: "stop", turnId: s.activeTurn.turnId };
  }
  if (s.busy) return { kind: "refuse", reason: `The chat is busy (${s.busy}).` };
  return { kind: "goBack", anchor: s.message.id };
}

/** The snapshot's chat half, read from one page of the sync endpoint. */
export function readChatState(
  page: Pick<ThreadEventsPage, "events" | "queued" | "active_turn" | "pending_operation" | "has_more">,
  clientId: string,
): Omit<CancelSnapshot, "answered" | "sendOk"> {
  const events: LogEvent[] = page.events || [];
  const msg = events.find((e) => e.kind === "message" && e.client_id === clientId) ?? null;
  const ended = !!msg && events.some((e) => (
    e.kind === "turn" && e.seq > msg.seq && (e.state === "finished" || e.state === "lost")
  ));
  const t = page.active_turn;
  return {
    queued: (page.queued || []).some((q) => q.client_id === clientId),
    message: msg ? { id: msg.id, seq: msg.seq } : null,
    firstMessage: !!msg && !page.has_more
      && !events.some((e) => e.kind === "message" && e.seq < msg.seq),
    activeTurn: t ? { turnId: t.turn_id, startedSeq: t.started_seq ?? null } : null,
    turnEndedSinceMessage: ended,
    busy: page.pending_operation ?? null,
  };
}
