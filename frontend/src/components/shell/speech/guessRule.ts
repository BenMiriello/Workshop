// What to do with what a sentence worded freely most likely meant (the server's
// guesses, best first, and how likely it is no command at all). Only commands
// that make sense here count. A clear winner runs at once, with Cancel as for
// any action; a close call is asked as numbered choices; otherwise nothing is
// done. The thresholds were set on 312 sentences written apart from the
// training phrasings: acting at 0.5 ran the right command 83% of the time and
// a wrong one 2.6% (docs/proposals/voice-control-research-2026-10-31.md).

import type { Guess } from "../../../lib/speech/liveWire";

export const ACT = 0.5;
export const ASK = 0.1;
export const CHOICES = 3;

export type Decision =
  | { kind: "act"; guess: Guess }
  | { kind: "ask"; guesses: Guess[] }
  | { kind: "none" };

export function decide(guesses: readonly Guess[], none: number, fits: (g: Guess) => boolean): Decision {
  const ok = guesses.filter(fits);
  const top = ok[0];
  if (top && top.p >= ACT && top.p > none) return { kind: "act", guess: top };
  const asks = ok.filter((g) => g.p >= ASK).slice(0, CHOICES);
  return asks.length ? { kind: "ask", guesses: asks } : { kind: "none" };
}
