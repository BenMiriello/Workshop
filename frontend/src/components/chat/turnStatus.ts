// What the working indicator says, and when it is shown at all.
//
// A LIVE TURN IS NEVER SILENT. This exists because it was: the operator ran a
// fifty-minute turn with nothing on screen saying anything was happening. The
// indicator was rendered on `streaming`, which is "this tab has an open SSE
// socket" — so every way a socket can end while the TURN keeps running left the
// transcript looking idle:
//
//   - the reconnect budget ran out (8 tries): the code set streaming false, said
//     "it is still running; reattaching" in the error line, and removed the only
//     moving thing on the page for however long the reattach took;
//   - Stop used to mean "stop watching", and the turn genuinely kept going. It
//     ends the run now (POST /chat/turns/{id}/stop), so this case is gone at the
//     source rather than papered over here;
//   - the turn was started in another tab or on another device, so this one
//     never had a socket to begin with.
//
// The question the indicator answers is "is a run in flight on this thread",
// which is server state (`meta.active_turn_id`), not socket state. How closely
// this tab is watching only chooses the WORDING.
//
// WHAT LIVES HERE NOW: wording and time. The LIFECYCLE — is a run in flight,
// which run, is it being stopped, is it on screen — moved to turnLifecycle.ts,
// where it is one discriminated union built in one place. `turnPhase` and
// `turnLabel` were this file's half of that question, answered from loose flags
// the caller had to assemble correctly at each of its call sites; they are
// replaced by `indicatorLabel` and its siblings. `currentActivity` below is
// what that selector calls for the plain working case.

// A FOLD IS NOT A PHASE OF A TURN, and it used to be listed here as one. When
// /compact ran as an ordinary turn, "Compacting" was this indicator's label for
// it; the fold is a server call with no turn now, so nothing could ever set that
// phase and the word was unreachable. It said the thing the operator was owed
// while apparently still saying it. The fold announces itself at the foot of the
// log instead, for as long as it runs. See FoldingNote.
import type { ChatSegment } from "../../lib/chat";
import { isRunning } from "./grouping";

// ONE WORD FOR A RUNNING TURN. There is no per-tool verb table any more.
//
// There was: a `TOOL_VERB` map of ~30 entries turning each tool name into its
// own present participle, so the indicator read "Reading", then "Running", then
// "Searching", then "Scanning", changing every few hundred milliseconds for the
// whole turn.
//
// It is deleted rather than trimmed, and deleted rather than reduced to a
// shorter map, because a smaller table is the same design and drifts back one
// entry at a time. That already happened: the operator asked for the custom
// verbs to go on 2026-08-27 ("why did you start doing custom working text I
// never asked for... what determines that?"), and what changed was ONE entry
// (Agent, from "Delegating" to "Working") while the table stayed. He asked
// again. There is now nothing left to shrink.
//
// The reasoning that justified it was that a verb phrase fits the live register
// better than a raw identifier. True, and beside the point: the question is
// whether the indicator should carry tool detail AT ALL. It should not. The step
// list directly underneath already names every tool exactly, in order, with its
// own duration, so the indicator was a second, blurrier copy of information that
// was already on screen, and the only thing its churn communicated was churn.
// One steady word plus the elapsed clock is what every mature chat UI shows, and
// it is what `indicatorElapsed` below is already built for.
//
// TWO STATES SURVIVE, and neither is a tool verb:
//   "Waiting"  the turn is blocked on the operator answering an approval. That
//              is a different STATE, not a description of work, and it is the
//              one case where the operator has to do something. Dropping it
//              would leave a blocked turn looking like a busy one.
//   "Working"  everything else that is running.
// The other wordings a running turn can take ("Reconnecting", "Still running",
// "Stopping") live in turnLifecycle.indicatorLabel and are about the socket and
// the stop, not about the work.

/** What a live turn is doing right now.
 *
 *  One word, plus the one exception that is a state rather than an activity: a
 *  turn blocked on a pending approval says so, because the operator is the only
 *  thing that can unblock it. Everything else is "Working".
 *
 *  Still scans backwards through the segments rather than returning a constant,
 *  because finding the pending approval is what the scan is for. */
export function currentActivity(segments: ChatSegment[]): string {
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.t === "approval" && seg.state === "pending") return "Waiting";
  }
  return "Working";
}

/** Seconds a run has been going, from the epoch ms it started.
 *
 *  Anchored to the run's real start (the server's `live.ts`), so a reload
 *  mid-turn shows the true elapsed time instead of restarting from zero, and it
 *  keeps counting across a reconnect because the anchor never moves. */
export function elapsedSeconds(startedMs: number | null | undefined, nowMs: number): number {
  if (!startedMs || !Number.isFinite(startedMs)) return 0;
  return Math.max(0, Math.floor((nowMs - startedMs) / 1000));
}

/** Elapsed seconds for the in-flight indicator.
 *
 *  When a specific step is running, counts from when THAT STEP started so the
 *  counter resets at each step boundary and the reader sees how long the current
 *  activity has been going, not how long the whole turn has run. Falls back to
 *  turn elapsed when the model is thinking between steps. */
export function indicatorElapsed(
  segments: ChatSegment[] | undefined,
  startedMs: number | null | undefined,
  nowMs: number,
): number {
  if (segments) {
    for (let i = segments.length - 1; i >= 0; i--) {
      const seg = segments[i];
      if (seg.t === "tool" && isRunning(seg) && typeof seg.at === "number") {
        return Math.max(0, Math.floor((nowMs - seg.at * 1000) / 1000));
      }
    }
  }
  return elapsedSeconds(startedMs, nowMs);
}
