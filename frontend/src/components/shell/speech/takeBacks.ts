// Which action a spoken "cancel" undoes. Decided by
// WHERE IN THE AUDIO it was said, not by what the bar happens to show when the
// page gets round to it: a page that was suspended (an app in the background,
// a laptop lid) reads a cancel late, and by then the bar may show the next send
// or nothing at all. So each operation keeps the stream position of the
// command that made it, and a spoken answer goes to the latest one said before
// it, within five seconds of audio from when it applied. One made from a button
// has no position, and an answer to it is timed by the clock.

import { MIC_RATE } from "../../../lib/voice/capture";

export const ANSWER_WINDOW_MS = 5000;

export type TakeBack = {
  /** Stream sample where the command that made it ended; null from a button. */
  at: number | null;
  /** When it applied (ms since the epoch). */
  made: number;
  /** How long after the command was heard it applied (a model looked up, a
   *  chat read), in ms. Its five seconds start then, so the window is longer
   *  by this much. */
  lag?: number;
};

/** A spoken command: where in the stream it ended, and when it was heard. */
export type Said = { to: number; heard: number };

export type Answer = { from: number; heardAt: number };

const WINDOW = (ANSWER_WINDOW_MS / 1000) * MIC_RATE;

/** The operation `a` answers, or null when it answers none (so the words are
 *  ordinary words). `ops`: those that can still be undone, in the order they
 *  were made. */
export function answered<T extends TakeBack>(ops: readonly T[], a: Answer): T | null {
  for (let i = ops.length - 1; i >= 0; i--) {
    const op = ops[i];
    if (op.at === null) {
      if (a.heardAt - op.made <= ANSWER_WINDOW_MS) return op;
      continue;
    }
    if (op.at > a.from) continue;
    return a.from - op.at <= WINDOW + ((op.lag ?? 0) / 1000) * MIC_RATE ? op : null;
  }
  return null;
}
