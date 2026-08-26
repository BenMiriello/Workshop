// ONE ANSWER TO "IS A TURN RUNNING, AND WHAT IS IT DOING".
//
// Nine pieces of state used to answer that question, spread over six modules —
// three read from the server, six local to this tab — and they were combined ad
// hoc at each point of use. `Chat.tsx` wrote `turnActive || pipeline.starting`
// twice, and `ChatLog.tsx` then ran `logPlaceholder` on the same inputs a THIRD
// time. Two derivations of one fact are two things that can disagree, and that
// is precisely the class of defect fixed in 2277cc60 (the new-chat send flashing
// through five states, the message that disappeared for a frame, the "Still
// running" flash at the top of a turn the operator had just started). Those were
// fixed one expression at a time. This module removes the shape that produced
// them: the union below is constructed in exactly one place, and every question
// about the turn is a selector over it.
//
// PURE — no React, no I/O — because that is the only verification this tree has.
// There is no DOM/render harness and `frontend/CLAUDE.md` says none is planned:
// "the target is the PURE RULE behind a behaviour, extracted into its own module
// so it can be executed." Neither `useThreadLog` nor `useTurnStream` has a test
// at all, so anything that stays inside a hook is checkable only by driving a
// browser. Everything here is executed by turnLifecycle.test.ts instead.

import type { ChatSegment } from "../../lib/chat";
import { currentActivity } from "./turnStatus";

/** How closely this tab is following the run. Only ever meaningful while a run
 *  exists, which is why it is nested inside `running` rather than being a
 *  sibling field that has to carry a "not applicable" case. */
type WatchState = "attached" | "reconnecting" | "detached";

export type TurnLifecycle =
  | { kind: "idle" }
  /** A send is out and the server has not named a turn yet. Client-local: no
   *  server state says anything is coming during this window, which is why the
   *  busy checks have to include it or the composer goes idle mid-send. */
  | { kind: "starting" }
  | {
      kind: "running";
      turnId: string;
      watching: WatchState;
      segments: ChatSegment[];
      startedMs: number | null;
      /** The transcript is showing a live block for this run. False when the
       *  committed log ALREADY holds this turn's reply — `transcriptBlocks`'
       *  `liveShown` correction, which exists because the server does not write
       *  the reply and the cleared `active_turn_id` atomically. So a run being
       *  active does not imply anything is on screen for it, and display-only
       *  concerns (the 1 Hz clock, the composer's streaming affordances) must
       *  read `showIndicator`, not `isBusy`. */
      shown: boolean;
    }
  | { kind: "stopping"; turnId: string };

/** The ONLY place a `TurnLifecycle` is constructed.
 *
 *  DELIBERATELY NOT WIRED INTO THE SEND PATH. `planSend` (sendPlan.ts, and its
 *  caller useSendPipeline) keeps reading the server's `activeTurn` directly: it
 *  decides whether a message starts a new turn or is injected into a live one,
 *  which is a correctness decision about the SERVER's state, not a display
 *  decision. Folding `shown`/`liveShown` into it would change send semantics —
 *  a message would start a second turn against a run the server still considers
 *  live. This is not an oversight and it is not half-finished work. */
export function deriveTurnLifecycle(input: {
  activeTurn: { turnId: string; segments: ChatSegment[]; startedMs: number | null } | null;
  liveShown: boolean;
  /** Client-local: the POST is out and the server has not answered. */
  starting: boolean;
  /** Client-local: a stop request is in flight. */
  stopping: boolean;
  reconnecting: boolean;
  detached: boolean;
}): TurnLifecycle {
  const run = input.activeTurn;
  // FIRST, because a stop outranks everything else the turn could be doing. A
  // reconnect during a stop is still a stop as far as the reader is concerned:
  // they asked for it to end and are waiting to hear that it did.
  if (run && input.stopping) return { kind: "stopping", turnId: run.turnId };
  if (run) {
    return {
      kind: "running",
      turnId: run.turnId,
      // RECONNECTING BEFORE DETACHED, because that is the order the deleted
      // `turnPhase` used and this stage is a representation change, not a
      // wording change. The old test pinned it explicitly
      // (`turnPhase({detached, reconnecting}) === "reconnecting"`), and that
      // test moved into this file rather than being dropped.
      //
      // The precedence has to be applied HERE rather than in the label
      // selector, which is where the plan originally put it: once the two
      // flags collapse into one `watching` field, `"detached"` is lossy and no
      // selector downstream could recover "Reconnecting" from it. (An earlier
      // draft of this comment justified the order by claiming the two flags can
      // be true at once. They cannot: the give-up path sets `finishingRef`
      // in the same block that sets `detached`, and `handleDrop` returns early
      // on `finishingRef`, so `setReconnecting(true)` is unreachable while
      // detached. The order is right; that reason for it was not.)
      watching: input.reconnecting ? "reconnecting" : input.detached ? "detached" : "attached",
      segments: run.segments,
      startedMs: run.startedMs,
      shown: input.liveShown,
    };
  }
  if (input.starting) return { kind: "starting" };
  return { kind: "idle" };
}

/** Is anything at all in flight — for the placeholder rule, and for anything
 *  that must not read as idle mid-send.
 *
 *  Replaces `turnActive || pipeline.starting`, which was written out twice.
 *  Deliberately true across the whole of starting → running with no gap: the
 *  frame in which it went false between the two is the frame the operator's own
 *  message vanished from the transcript (2277cc60). */
export function isBusy(lc: TurnLifecycle): boolean {
  return lc.kind !== "idle";
}

/** Is there a live block on screen for this run — the DISPLAY question, which
 *  is not the same as `isBusy`.
 *
 *  A run whose reply is already committed is still active per the snapshot but
 *  has nothing left to animate; driving the 1 Hz clock or the composer's
 *  streaming affordances off `isBusy` ran a timer over nothing and told the
 *  operator their next message would queue behind a turn that had finished. */
export function showIndicator(lc: TurnLifecycle): boolean {
  if (lc.kind === "stopping") return true;
  return lc.kind === "running" && lc.shown;
}

/** What the working indicator says. Replaces `turnLabel`, with the same
 *  precedence and the same words.
 *
 *  The non-working states describe the CONNECTION, so they use their own fixed
 *  wording; only a plainly attached run asks the segments what it is doing.
 *  Idle and starting fall through to "Working" because the label is computed
 *  unconditionally by the caller and is simply unused when nothing is shown. */
export function indicatorLabel(lc: TurnLifecycle): string {
  if (lc.kind === "stopping") {
    // Ending a turn is not instant: the server has to reach the run, signal its
    // process group and commit what it had produced. Until that returns the
    // indicator used to still say "Working", which reads as the press having
    // done nothing. Operator, 2026-08-14: "there's no styling indicating the
    // state of it or if it's been clicked or what's happening."
    return "Stopping";
  }
  if (lc.kind === "running") {
    if (lc.watching === "reconnecting") return "Reconnecting";
    // Running, but this tab is not the one reading its output. Says the true
    // thing ("it has not stopped") without implying a stream that is not open.
    // NEVER said for a run this tab has not attached to YET — that window is
    // `attached`, not `detached`, and saying it there flashed the wording for
    // an ENDED socket at the top of a turn the operator had just started.
    if (lc.watching === "detached") return "Still running";
    return currentActivity(lc.segments);
  }
  return "Working";
}

/** The run this lifecycle is about, or null. `stopping` still names its run:
 *  the turn exists until the server says it does not, and a pending approval on
 *  it is still answerable while the stop is in flight. */
export function runningTurnId(lc: TurnLifecycle): string | null {
  return lc.kind === "running" || lc.kind === "stopping" ? lc.turnId : null;
}
