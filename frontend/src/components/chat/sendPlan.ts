import { isCommand } from "./turnBatching";

// What happens to a message the operator just hit enter on. ONE decision, made
// in one place, against the store's state.
//
// This replaces a four-branch guard ladder in the send path (compacting /
// logBusy && isSlashCommand / streaming && threadId / logBusy) plus a 250ms
// timer in the queue's drain. Every branch was added for a real incident and
// each was correct on its own; together they asked two different questions
// ("is a turn streaming" and "does a turn own the tail of the log") whose
// answers diverged for one network round trip on every single turn, and a
// message sent in that window took the wrong branch.
//
// The state below has ONE question in it, `runActive`, plus `starting`, which is
// the window the timer used to paper over: a turn has been asked for and the
// POST has not come back yet. Setting it synchronously at send time is what
// makes the timer unnecessary — there is no interval in which the chat is
// honestly idle but a turn is on its way.

export type SendPlan =
  // Hand it to the run in flight. It lands at that run's next input boundary.
  | "inject"
  // Hold it for the next turn. The queue drains itself when nothing is running.
  | "queue"
  // Nothing is running: this message becomes a turn.
  | "newTurn";

export type SendState = {
  // A /compact fold or a /clear round trip is in flight. Whatever is typed now
  // must not become a turn ahead of the command that was asked for first.
  compacting: boolean;
  // A turn has been requested and has not been confirmed yet.
  starting: boolean;
  // The server says a run is in flight on this thread.
  runActive: boolean;
};

export function planSend(text: string, s: SendState): SendPlan {
  if (s.compacting) return "queue";
  const busy = s.starting || s.runActive;
  if (!busy) return "newTurn";
  // A SLASH COMMAND CANNOT BE INJECTED. Injection hands text to the MODEL;
  // commands are handled by the CLI's own command layer, which only runs
  // BETWEEN turns. Measured against the CLI directly (2.1.220): "/compact" sent
  // mid-turn produced no user message, no command, no fold and no error — it was
  // swallowed. The same message sent while idle ran normally. So a command waits
  // and goes out as its own turn, which is the only timing it acts on.
  if (isCommand(text)) return "queue";
  return "inject";
}

/** May the queue send its next batch right now?
 *
 *  The queue used to answer this on a 250ms timer, re-checking a ref when it
 *  fired, because a turn that was in the act of starting looked idle. `starting`
 *  is that fact stated directly, so the answer is available immediately and the
 *  race it was covering does not exist. */
export function canDrainQueue(s: SendState & { loadingThread: boolean }): boolean {
  return !s.compacting && !s.starting && !s.runActive && !s.loadingThread;
}
