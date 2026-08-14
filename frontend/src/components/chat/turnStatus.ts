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
// which is server state (`meta.active_run_id`), not socket state. `attached`
// only chooses the WORDING.

// A FOLD IS NOT A PHASE OF A TURN, and it used to be listed here as one. When
// /compact ran as an ordinary turn, "Compacting" was this indicator's label for
// it; the fold is a server call with no turn now, so nothing could ever set that
// phase and the word was unreachable. It said the thing the operator was owed
// while apparently still saying it. The fold announces itself at the foot of the
// log instead, for as long as it runs. See FoldingNote.
export type TurnPhase = "working" | "reconnecting" | "detached" | "stopping";

export const TURN_LABEL: Record<TurnPhase, string> = {
  working: "Working",
  reconnecting: "Reconnecting",
  // Running, but this tab is not the one reading its output. Says the true
  // thing ("it has not stopped") without implying a stream that is not open.
  detached: "Still running",
  // Stop was pressed and the request is in flight. Ending a turn is not
  // instant: the server has to reach the run, signal its process group and
  // commit whatever it had produced, and until that returns the indicator was
  // still saying "Working", which reads as the press having done nothing.
  // Operator, 2026-08-14: "there's no styling indicating the state of it or if
  // it's been clicked or what's happening."
  stopping: "Stopping",
};

export function turnPhase(o: {
  // An SSE socket for this run is open here.
  attached: boolean;
  // The socket dropped and is being retried.
  reconnecting: boolean;
  // Stop was pressed and the server has not answered yet.
  stopping?: boolean;
}): TurnPhase {
  // FIRST, because it outranks every other thing the turn could be doing. A
  // reconnect during a stop is still a stop as far as the reader is concerned:
  // they asked for it to end and are waiting to hear that it did.
  if (o.stopping) return "stopping";
  if (o.reconnecting) return "reconnecting";
  if (!o.attached) return "detached";
  return "working";
}

export function turnLabel(o: Parameters<typeof turnPhase>[0]): string {
  return TURN_LABEL[turnPhase(o)];
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
