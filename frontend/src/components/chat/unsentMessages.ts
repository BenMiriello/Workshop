import type { PendingSend } from "./threadStore";

// AN UNSENT MESSAGE IS AN OUTSTANDING ACTION, NOT A HISTORICAL FACT.
//
// A send that failed stays in `pending` forever, is mirrored to localStorage,
// and IS rendered. None of that was the problem. The problem is WHERE it is
// rendered: `chatSequence.placeSend` anchors a pending send after the log seq it
// was made at, and `transcriptBlocks` splices it into the committed blocks at
// that point. So the moment anything commits after the failure (the reply of the
// turn that was already running, the next successful message, a queue drain) the
// failed bubble is spliced ABOVE that reply, and the log's bottom-follower
// carries the reader away from it.
//
// That placement is correct for ORDER and was fixed deliberately on 2026-08-11,
// so this does not undo it. What it adds is the thing an anchored bubble cannot
// be: a fixed place to find out that you have unsent text at all.
//
// Operator, 2026-09-17: "it seems to never have sent and I could see a show of
// as an error with like a yellow border around it briefly, but then it goes away
// and then whenever I close and reopen the app here I see it show up for a
// moment in this chat, but then it disappears". Every clause of that is this
// mechanism. The yellow border is `.chat-bubble--unsent`, drawn at the tail
// while `afterSeq === tail`. "It goes away" is the first commit after it moving
// it into history. On reopen the log is empty for a frame, so `tail` is 0 and it
// draws at the bottom again -- then the first page lands, `tail` jumps past it,
// and it is spliced back up.
//
// AND IT DIES SILENTLY. Nothing ever removes it from `pending`, so the outbox
// keeps re-writing it until `OUTBOX_MAX_AGE_MS` (24h) drops it with no notice.
// A message the operator believes they sent is deleted a day later without
// anything having said so. That is what makes this data loss rather than a
// layout complaint.

/** The sends that failed and are still owed an answer. */
export function unsentSends(pending: PendingSend[]): PendingSend[] {
  return pending.filter((s) => s.status === "failed");
}

/** The line above the composer, or null when there is nothing outstanding.
 *
 *  Counts rather than quoting: the text is in the bubble, which the Show link
 *  goes to, and repeating it here would put the same message on screen twice in
 *  two different places -- the exact two-owners shape this chat has been bitten
 *  by before. */
export function unsentLabel(pending: PendingSend[]): string | null {
  const n = unsentSends(pending).length;
  if (n === 0) return null;
  return n === 1 ? "1 message was not sent." : `${n} messages were not sent.`;
}

/** Which send a single Retry should address: the oldest outstanding one, so a
 *  run of failures is cleared in the order they were typed rather than newest
 *  first. `pendingSends` already sorts by `at`. */
export function firstUnsent(pending: PendingSend[]): PendingSend | null {
  return unsentSends(pending)[0] ?? null;
}
