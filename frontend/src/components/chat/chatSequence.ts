// THE SEQUENCING MODEL. One rule for where a message goes, in one place.
//
// ---------------------------------------------------------------------------
// WHY THIS FILE EXISTS
// ---------------------------------------------------------------------------
//
// "where does this message go" is the single most-repeated defect report this
// product has: 23 reports on 16 occasions across 14 conversations between
// 2026-07-25 and 2026-09-02. It has been fixed, by hand, at the render site,
// at least five times, and it has come back every time. It comes back because
// the answer was never written down as ONE rule: it was an expression spread
// across `composeTranscript`, computed from four inputs captured at four
// different moments, each of which can be individually stale.
//
// So the arrangement of committed events stays where it is (transcriptBlocks:
// the server records the cut positions and the client matches tokens, which is
// exact and is not the problem). What moves here is the ONE decision the client
// still makes for itself, and therefore the only one it can get wrong:
//
//     WHERE DOES A MESSAGE THIS BROWSER HAS SENT, AND THE SERVER HAS NOT YET
//     CONFIRMED, SIT AMONG THE THINGS THE SERVER HAS?
//
// Everything else in the transcript is the server's own order.
//
// ---------------------------------------------------------------------------
// THE INVARIANTS, each named after the report it closes
// ---------------------------------------------------------------------------
//
// I1  NO TIME TRAVEL. A message cannot sort above something that already
//     existed when it was typed. ("if it was already working my new message
//     could not be BEFORE the system message since it wouldn't have seen it")
//
// I2  AN ANCHOR MUST HAVE BEEN OBSERVED. `afterSeq` is the log's tail as this
//     browser saw it at send time, and it is 0 both for "this conversation is
//     empty" and for "the log had not loaded". Those are different facts and
//     only one of them is an anchor. Treating the second as an anchor is what
//     puts a message ABOVE the entire history: an anchor of 0 resolves to
//     index 0, the top. ("why did the chat history just disappear while i was
//     typing ... then the chat reappeared but UNDER my last message that
//     failed to send")
//
// I3  A MESSAGE DOES NOT MOVE ACROSS THE RUN IT STARTED. A send that started
//     the run in flight belongs above that run; a send handed to a run that
//     was already going belongs inside it. Which one it is, is decided by the
//     run's IDENTITY captured at send time, not by a boolean, because a
//     boolean cannot tell "a run was going" from "a run is going now".
//     ("when I send a message to a new chat, my message shows at the very
//     bottom of the chat area at first, and then after some change or some
//     event, it moves to the top")
//
// I5  A RUN CANNOT PREDATE THE CONVERSATION'S FIRST MESSAGE. When the log
//     carries no committed message at all, whatever run is on screen cannot
//     have been running before the operator typed, so his message did not join
//     it and does not go under it. A blank chat CAN carry a run this browser has
//     not cleared yet (one left over from the thread it was opened from, or the
//     new-chat lifecycle race), and believing it put the opening message under
//     the working indicator. ("when I send a message to a new chat, my message
//     shows at the very bottom of the chat area at first")
//
// I4  THE SAME INPUTS GIVE THE SAME ANSWER. Placement is a pure function of
//     one send and one observation of the log. It does not depend on the order
//     the sends are considered in, on how many times a page was re-read, or on
//     anything that a duplicated or late-arriving response can perturb. This is
//     what makes the property tests in chatSequence.test.ts meaningful.
//
// ---------------------------------------------------------------------------
// WHAT THIS FILE IS NOT
// ---------------------------------------------------------------------------
//
// It does not decide where a COMMITTED message goes. The server records that
// (`meta.cut_id` against `seg.cut_after`) and transcriptBlocks matches the
// tokens; there is no judgement left to make and no client state involved.
//
// It does not decide read state, dimming, or what the indicator says. Those are
// display facts about a placement, not the placement.

/** A run in flight, as this browser knew it AT THE MOMENT a send was made.
 *  `null` means "no run was announced to this tab yet", which is what makes the
 *  send the thing that STARTS a run rather than something handed to one. */
export type ObservedTurnId = string | null;

/** What this browser knew when the operator hit enter. Every field is captured
 *  once, at send time, and never recomputed: that is the point. A value read at
 *  render time is a fact about the render, not about the send. */
export type SendObservation = {
  /** Where this browser is with the server on this message. A FAILED send was
   *  never received, so no run can have been handed it. */
  status: "sending" | "sent" | "failed";
  /** The log's last committed seq as this browser saw it at send time. Only a
   *  position when `observedLog` says the log had been read at all — see I2. */
  afterSeq: number;
  /** Had this browser actually read this thread's log when `afterSeq` was
   *  taken? False for a send made against a store that was reset and not
   *  refilled, which records `afterSeq: 0` indistinguishably from a genuinely
   *  empty conversation. See I2. */
  observedLog: boolean;
  /** The run this browser believed was in flight at send time. An entry
   *  restored from a previous session's outbox is normalised to `null` on read
   *  (lib/chatSelection.readOutbox), because the run it was typed under belonged
   *  to a browser session that is gone and cannot be the one on screen now. */
  duringTurnId: ObservedTurnId;
};

/** The log as it is NOW, at render time. Three facts and nothing else. */
export type LogObservation = {
  /** Does the committed log hold a message at all? A run on screen over an
   *  empty log cannot have preceded the message being placed. See I5. */
  hasCommittedMessage: boolean;
  /** The last committed seq the store holds. 0 for an empty or unloaded log. */
  tail: number;
  /** The run whose live block is actually ON SCREEN, or null. Not the same
   *  question as "does the snapshot name an active turn": the server does not
   *  clear `active_turn_id` and append the reply atomically, so a run can be
   *  named while its reply is already committed above. transcriptBlocks'
   *  `isLiveShown` is the answer to the question this field asks. */
  liveTurnId: string | null;
};

/** Where a pending send goes. Three places, and they are exhaustive.
 *
 *  `anchored` — the log has grown past this send, so it sits back among the
 *      messages it was actually typed between, after seq `afterSeq`.
 *  `inRun`    — it was handed to the run in flight, so it renders inside that
 *      run at the run's current tail, below the work already emitted.
 *  `tail`     — it is the newest thing in the conversation. The ordinary case,
 *      and the only honest answer whenever the other two cannot be justified. */
export type SendPlacement =
  | { at: "anchored"; afterSeq: number }
  | { at: "inRun"; turnId: string }
  | { at: "tail" };

/** Was `afterSeq` an OBSERVATION, or the absence of one? See I2.
 *
 *  A send made into a log that held nothing records `afterSeq: 0`, and so does
 *  a send made while the log had not arrived (a transient blank, a thread whose
 *  first page 502'd, a switch caught mid-flight). The first is a real position
 *  meaning "before everything in this conversation". The second is not a
 *  position at all, and treating it as one is what put the whole restored
 *  history UNDERNEATH a message the operator had just typed.
 *
 *  The two cannot be told apart from `afterSeq`, so the store records which one
 *  it was at send time (`threadStore.sendStarted`: a page has landed, or this
 *  browser holds events). An outbox entry written before that field existed
 *  falls back to `afterSeq > 0`, which can only be true of a log that was read
 *  (lib/chatSelection.readOutbox). */
export function hasAnchor(send: SendObservation): boolean {
  return send.observedLog;
}

/** Was this send handed to the run that is on screen now? See I3 and I5.
 *
 *  True only when the run this browser saw at send time IS the run rendering
 *  now. That single comparison replaces four separate conditions:
 *
 *    * "a turn was active" (a boolean, so it could not tell the run apart from
 *      its successor, and a send typed during run A rendered inside run B);
 *    * "this is not the conversation's first message", a guard that existed
 *      only because the boolean could not express "the send STARTED this run" —
 *      which is true of the first message of EVERY turn, not just the first
 *      message of the chat, and was wrong for all the others;
 *    * "a live block is on screen", which is `liveTurnId !== null` here;
 *    * "this send did not fail", below.
 *
 *  A failed send was never received by anything, so no run was handed it. */
export function joinedRun(send: SendObservation, log: LogObservation): boolean {
  if (send.status === "failed") return false;
  if (!log.liveTurnId) return false;
  // I5: nothing can have been running before the first thing said.
  if (!log.hasCommittedMessage) return false;
  return send.duringTurnId === log.liveTurnId;
}

/** THE RULE. One function, three outcomes, in this order.
 *
 *  The order is the rule, not an implementation detail:
 *
 *  1. A send handed to the run on screen goes INSIDE that run. It cannot be
 *     anchored — the run is younger than every committed message, so anchoring
 *     it would lift it above work it was typed under (I1).
 *  2. A send the log has since grown past goes back where it was typed, but
 *     only if it recorded a real position (I2).
 *  3. Everything else is the newest thing in the chat.
 *
 *  Note what is NOT here: nothing consults the number of blocks on screen, the
 *  send's timestamp, its text, or the order sends are considered in. Placement
 *  of one send never depends on another (I4). */
export function placeSend(send: SendObservation, log: LogObservation): SendPlacement {
  if (joinedRun(send, log)) return { at: "inRun", turnId: log.liveTurnId as string };
  if (hasAnchor(send) && send.afterSeq < log.tail) {
    return { at: "anchored", afterSeq: send.afterSeq };
  }
  return { at: "tail" };
}

/** THE ORDER KEY, for anything that needs to compare two placements rather than
 *  act on one. Ascending: anchored (by the seq it is anchored to) below every
 *  committed message above it, then the run in flight, then the tail.
 *
 *  Exported because "is this arrangement monotone" is a property, and a property
 *  needs a number to be stated over. The renderer does not use it; the tests do,
 *  and so would any future surface that has to merge two of these lists. */
export function placementRank(p: SendPlacement): number {
  switch (p.at) {
    case "anchored": return 0;
    case "inRun": return 1;
    case "tail": return 2;
  }
}

/** Does this arrangement satisfy I1 for a whole batch: does every send sit at
 *  or after the position it was typed at?
 *
 *  A send may SETTLE UP (a run keeps streaming, so the cut it was recorded at
 *  lands earlier than the run's current tail) and that is by design. What it may
 *  never do is sort above something that already existed when it was typed. This
 *  states that as one predicate so a test can assert it over generated input
 *  rather than over a hand-written example. */
export function respectsObservedOrder(
  sends: SendObservation[], log: LogObservation,
): boolean {
  return sends.every((s) => {
    const p = placeSend(s, log);
    if (p.at !== "anchored") return true;
    // An anchored send sits after `afterSeq`, which is a position this browser
    // actually observed, so by construction it is at or after where it was
    // typed. Zero is allowed HERE and only here: it means "this browser read
    // the log and it was empty", which is a real position. It is rejected for a
    // send that never read the log at all, which is `hasAnchor`'s job.
    return hasAnchor(s) && p.afterSeq >= 0 && p.afterSeq <= log.tail;
  });
}
