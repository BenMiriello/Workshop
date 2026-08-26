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
// which is server state (`meta.active_turn_id`), not socket state. `attached`
// only chooses the WORDING.

// A FOLD IS NOT A PHASE OF A TURN, and it used to be listed here as one. When
// /compact ran as an ordinary turn, "Compacting" was this indicator's label for
// it; the fold is a server call with no turn now, so nothing could ever set that
// phase and the word was unreachable. It said the thing the operator was owed
// while apparently still saying it. The fold announces itself at the foot of the
// log instead, for as long as it runs. See FoldingNote.
import type { ChatSegment } from "../../lib/chat";
import { isRunning } from "./grouping";

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

// Maps a tool call name to a short active-verb phrase for the indicator.
//
// VERBS, NOT TOOL NAMES. The indicator is the live read on "what is happening
// right now" — a verb phrase fits that register; a raw identifier ("Bash", "mcp__workshop__search")
// fits the settled transcript. Unknown names fall back to the name itself so the
// label is never blank, even for a tool added after this table was written.
const TOOL_VERB: Record<string, string> = {
  Read: "Reading",
  Write: "Writing",
  Edit: "Editing",
  Bash: "Running",
  Grep: "Searching",
  Glob: "Scanning",
  WebFetch: "Fetching",
  WebSearch: "Searching",
  Agent: "Delegating",
  NotebookEdit: "Editing",
  EnterPlanMode: "Planning",
  ExitPlanMode: "Planning",
  EnterWorktree: "Setting up",
  ExitWorktree: "Wrapping up",
  TaskCreate: "Tracking",
  TaskUpdate: "Tracking",
  TaskGet: "Tracking",
  TaskList: "Tracking",
  TaskStop: "Tracking",
  TaskOutput: "Tracking",
  CronCreate: "Scheduling",
  CronDelete: "Scheduling",
  CronList: "Scheduling",
  RemoteTrigger: "Triggering",
  PushNotification: "Notifying",
  ScheduleWakeup: "Scheduling",
  Monitor: "Monitoring",
};

function toolVerb(name: string): string {
  if (TOOL_VERB[name]) return TOOL_VERB[name];
  // mcp__server__function → capitalize the function part
  if (name.startsWith("mcp__")) {
    const parts = name.split("__");
    const fn = parts[parts.length - 1].replace(/_/g, " ");
    return fn.charAt(0).toUpperCase() + fn.slice(1);
  }
  return name;
}

/** What a live turn is doing right now, based on its current segments.
 *
 *  Scans backwards to find the most recently issued step that is still in
 *  flight — a tool with `at` set but no result yet, or a subagent that has not
 *  reported back. Falls back to "Working" when the model is thinking between
 *  steps, which is the case where there is genuinely nothing more specific to
 *  say. */
export function currentActivity(segments: ChatSegment[]): string {
  for (let i = segments.length - 1; i >= 0; i--) {
    const seg = segments[i];
    if (seg.t === "tool" && isRunning(seg)) return toolVerb(seg.name);
    if (seg.t === "agent" && !seg.done) return "Delegating";
    if (seg.t === "approval" && seg.state === "pending") return "Waiting";
  }
  return "Working";
}

export function turnPhase(o: {
  // This tab has GIVEN UP watching this run: the reconnect budget is spent, or
  // the socket was closed and will not be silently re-opened.
  //
  // NOT `!attached`, which is what this used to be. A turn is briefly
  // un-attached at its very start — the server reports the run active on the
  // events page, and this tab's attach effect runs a tick later — and reading
  // that window as "detached" made the indicator say "Still running" for a
  // frame or two at the top of a turn the operator had just started, before
  // switching to "Working". "Still running" is the wording for a socket that
  // ENDED; saying it before one has ever opened is simply false, and it is the
  // first of the states the operator caught on a screen recording.
  detached: boolean;
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
  if (o.detached) return "detached";
  return "working";
}

/** Label for the working indicator.
 *
 *  When the phase is "working" and segments are provided, uses `currentActivity`
 *  to reflect what the turn is doing right now rather than the flat "Working".
 *  Non-working phases (stopping, reconnecting, detached) always use their own
 *  fixed label because those describe the CONNECTION state, not the work. */
export function turnLabel(o: Parameters<typeof turnPhase>[0] & { segments?: ChatSegment[] }): string {
  const phase = turnPhase(o);
  if (phase === "working" && o.segments) return currentActivity(o.segments);
  return TURN_LABEL[phase];
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
