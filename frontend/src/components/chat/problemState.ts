// THE CHAT'S PROBLEM SLOT, and the one thing it never had: a way off the
// screen.
//
// One `useState<ChatProblem | null>` used to hold every chat-scoped failure in
// this component, written by six hooks and a dozen inline handlers. Setting it
// was easy and clearing it was nobody's job: the only two calls that ever
// passed `null` were "start a new chat" and the top of the thread resolver. So
// a failure raised by a blip stayed on screen after the blip healed, and the
// operator reported exactly that on 2026-09-04 -- "warnings will come up about
// a connection being lost ... and then those warnings will get STUCK."
//
// A slot cannot fix this, because a slot cannot tell whose failure it is
// holding. The reader that raised "couldn't refresh the list of conversations"
// is the only one that can ever know the list came back, and if it writes
// `null` into a shared slot it also erases a refused rename that has nothing to
// do with it. So problems are held BY SOURCE, each source owns its own entry,
// and recovery is a source reporting `null` for itself.
//
// Two rules fall out and they are the whole module:
//
//   RAISE AND RESOLVE ARE THE SAME CALL. A source reports its current problem,
//   or `null` for "nothing wrong here now". Every reader that can fail can
//   therefore also recover, using the callback it already has, and a reader
//   that reports success on every success cannot leave a stale banner behind.
//
//   THE NEWEST ONE IS THE ONE ON SCREEN. There is one line above the composer
//   and there should be: two failures stacked is a wall of text, and the most
//   recent is the one the operator is looking for an explanation of. The
//   others are still held, so dismissing or resolving the top one reveals the
//   next rather than losing it.
//
// Dismissal is the third path, and it is why the line grew a control. A
// connection failure resolves itself; a one-shot notice pushed by the run (a
// blocked write, a tree taken from another session) has no condition to
// recover, so without a dismiss it is stuck by construction.

import type { ChatProblem } from "./chatProblem";

/** Who is reporting. A closed set, because "which reader owns this failure" is
 *  a fixed question about this component and an open string would let two
 *  callers pick names that differ only by spelling and never clear each other.
 *
 *  - `scope`   the provider/model list (useChatScope)
 *  - `threads` the conversation list and the thread resolver (useChatThread)
 *  - `log`     this thread's own event reads (useThreadLog)
 *  - `turn`    something the run in flight pushed over its stream
 *  - `action`  a write the operator asked for: stop, rename, delete, compact */
export type ProblemSource = "scope" | "threads" | "log" | "turn" | "action";

export type ProblemEntry = { source: ProblemSource; problem: ChatProblem };

/** Oldest first. The list is short by construction (at most one per source). */
export type ProblemState = readonly ProblemEntry[];

export const NO_PROBLEMS: ProblemState = [];

/** Report `source`'s current problem, or `null` because it no longer has one.
 *
 *  Re-reporting moves the entry to the end, so a failure that is still
 *  happening keeps saying so rather than being buried under an older one. A
 *  report identical to the one already held is returned unchanged, so a poll
 *  that keeps failing the same way does not re-render the chat every tick. */
export function reportProblem(
  state: ProblemState,
  source: ProblemSource,
  problem: ChatProblem | null,
): ProblemState {
  const held = state.find((e) => e.source === source);
  if (!problem) return held ? state.filter((e) => e.source !== source) : state;
  if (held && sameProblem(held.problem, problem)
      && state[state.length - 1]?.source === source) {
    return state;
  }
  return [...state.filter((e) => e.source !== source), { source, problem }];
}

/** Everything this chat is currently complaining about is about the chat that
 *  was open. Switching conversations drops all of it. */
export function clearProblems(state: ProblemState): ProblemState {
  return state.length ? NO_PROBLEMS : state;
}

/** What the line above the composer shows, with the source that owns it so the
 *  dismiss control knows what it is dismissing. */
export function currentProblem(state: ProblemState): ProblemEntry | null {
  return state.length ? state[state.length - 1] : null;
}

/** Same failure, said again. Compared on what the reader can SEE, not by
 *  identity: every writer builds a fresh object each time, and a `retry`
 *  closure is new on every render, so identity would call every repeat a
 *  change. */
function sameProblem(a: ChatProblem, b: ChatProblem): boolean {
  return a.text === b.text
    && (a.endpoint ?? null) === (b.endpoint ?? null)
    && (a.tone ?? "error") === (b.tone ?? "error")
    && !a.retry === !b.retry;
}
