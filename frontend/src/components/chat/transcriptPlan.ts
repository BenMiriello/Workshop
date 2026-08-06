import type { ChatMsg, ChatSegment } from "../../lib/chat";

// WHERE A MID-TURN MESSAGE SITS ONCE THE TURN IS OVER.
//
// A turn is an interval. A message sent at minute twelve of a twenty-minute
// reply is neither before that reply nor after it, so the store files it AFTER
// (causally true — the reply had not seen it) and records `at_segment`: how much
// of the reply had streamed when it landed.
//
// That was only half the design. The live overlay split the streaming reply at
// `at_segment` and showed the message in place; the PERSISTED transcript ignored
// it and rendered the stored order flat. So the instant a turn ended and the log
// reloaded, the operator's message jumped from the middle of the reply to below
// all of it — with the reply's acknowledgement of it now sitting ABOVE it.
//
// That is not a cosmetic difference. It reads as "you never saw my message", and
// on 2026-08-06 it cost exactly that: a message was delivered, acted on, and
// acknowledged mid-turn, and the operator re-sent it because the finished
// transcript showed the whole reply above their message. They asked "why did you
// not act or respond?" — the answer was that the renderer had moved their words.
//
// So the split lives here, computed once from the stored messages, and both
// views use the same rule. One truth, two views — which is what §3.2 of
// docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md claimed and
// did not yet do.

export type TranscriptRow =
  // An ordinary message, rendered whole.
  | { kind: "message"; index: number; msg: ChatMsg }
  // Part of an assistant reply, cut where a message arrived. `index` is still
  // the assistant message's index so callers can key and address it.
  | {
      kind: "slice";
      index: number;
      msg: ChatMsg;
      segments: ChatSegment[];
      // The first slice carries what belongs to the reply as a whole (byline,
      // timeout tag); the last is the one recency treats as "latest".
      head: boolean;
      tail: boolean;
    };

/** The user messages filed as having arrived during `turnId`, taken as the
 *  contiguous run immediately after the reply. Contiguous on purpose: anything
 *  further down belongs to a later exchange and must not be pulled backwards. */
function arrivalsDuring(messages: ChatMsg[], from: number, turnId: string) {
  const out: { index: number; msg: ChatMsg }[] = [];
  for (let j = from; j < messages.length; j++) {
    const m = messages[j];
    if (m.role !== "user" || m.meta?.during_turn !== turnId) break;
    out.push({ index: j, msg: m });
  }
  return out;
}

/** Where to cut a reply, given where each arrival says it landed.
 *
 *  Shared by the live overlay and the persisted list so the two cannot disagree
 *  about where a message goes — the whole point being that a message must not
 *  move when the turn it arrived during finishes.
 *
 *  Clamped into the reply, and monotonic: a later arrival can never be shown
 *  above an earlier one, and a backwards cut would produce a negative-length
 *  slice that silently drops output. A missing position means the end, which is
 *  the honest default — it arrived, we do not know where. */
export function cutPoints(ats: (number | undefined)[], total: number): number[] {
  let floor = 0;
  return ats.map((at) => {
    const c = Math.min(Math.max(typeof at === "number" ? at : total, floor), total);
    floor = c;
    return c;
  });
}

export function planTranscript(messages: ChatMsg[]): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  const consumed = new Set<number>();

  for (let i = 0; i < messages.length; i++) {
    if (consumed.has(i)) continue;
    const msg = messages[i];
    const turnId = msg.role === "assistant" ? msg.meta?.turn_id : undefined;
    const segments = msg.meta?.segments;

    if (!turnId || !segments || !segments.length) {
      rows.push({ kind: "message", index: i, msg });
      continue;
    }
    const during = arrivalsDuring(messages, i + 1, turnId);
    if (!during.length) {
      rows.push({ kind: "message", index: i, msg });
      continue;
    }

    const cuts = cutPoints(during.map((d) => d.msg.meta?.at_segment), segments.length);
    const bounds = [0, ...cuts, segments.length];
    const produced: TranscriptRow[] = [];
    for (let k = 0; k < bounds.length - 1; k++) {
      const slice = segments.slice(bounds[k], bounds[k + 1]);
      // An empty slice is not rendered, but it must not cost the reply its
      // byline or its recency: head/tail are assigned after, to the slices that
      // actually exist.
      if (slice.length) {
        produced.push({ kind: "slice", index: i, msg, segments: slice, head: false, tail: false });
      }
      if (k < during.length) {
        produced.push({ kind: "message", index: during[k].index, msg: during[k].msg });
        consumed.add(during[k].index);
      }
    }
    const slices = produced.filter((r): r is Extract<TranscriptRow, { kind: "slice" }> => r.kind === "slice");
    if (slices.length) {
      slices[0].head = true;
      slices[slices.length - 1].tail = true;
    }
    rows.push(...produced);
  }
  return rows;
}

/** Index of the row recency should expand: the last assistant content in the
 *  log. Computed over ROWS, not messages, so a split reply expands its final
 *  slice rather than nothing. Returns -1 when there is no assistant content. */
export function latestAssistantRow(rows: TranscriptRow[]): number {
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (r.kind === "slice" && r.tail) return i;
    if (r.kind === "message" && r.msg.role === "assistant") return i;
  }
  return -1;
}
