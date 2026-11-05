// Where Previous and Next go in a read, sentence by sentence. Previous restarts
// the sentence being read once it is a moment in, as a music player restarts a
// track, and steps back a sentence before that. Next at the last sentence has
// nowhere to go.

export const RESTART_AFTER_S = 2;

/** The sentence to read after a skip of `delta`, or null when there is none.
 *  `elapsedS` is how far into the current sentence the voice is. */
export function skipTarget(index: number, count: number, delta: number, elapsedS: number): number | null {
  if (count <= 0 || delta === 0) return null;
  const step = delta < 0 && elapsedS > RESTART_AFTER_S ? delta + 1 : delta;
  const target = Math.min(count - 1, Math.max(0, index + step));
  return delta > 0 && target === index ? null : target;
}

/** "3 / 12" for the player, counting from one. */
export function positionLabel(index: number, count: number): string {
  return count > 0 ? `${Math.min(index, count - 1) + 1} / ${count}` : "";
}
