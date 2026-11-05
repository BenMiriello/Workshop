// The text half of find-in-page: where a query occurs in a string and how the
// "current match" moves. Pure, so the rules are executable in vitest, which has
// no DOM.

/** The most matches one search tracks. A one-letter query on a long transcript
 *  would otherwise build tens of thousands of ranges. */
export const MAX_MATCHES = 2000;

/** Start offsets of every non-overlapping, case-insensitive occurrence. */
export function matchOffsets(text: string, query: string): number[] {
  if (!query) return [];
  const hay = text.toLowerCase();
  const needle = query.toLowerCase();
  // Lowercasing can change a string's length (a few characters expand), which
  // would put every offset after one out of step with `text`. Such a string is
  // skipped rather than highlighted in the wrong place.
  if (hay.length !== text.length) return [];
  const out: number[] = [];
  let from = 0;
  for (;;) {
    const at = hay.indexOf(needle, from);
    if (at < 0) return out;
    out.push(at);
    from = at + needle.length;
  }
}

/** The match after (dir 1) or before (dir -1) `current`, wrapping at both ends.
 *  -1 means there is none. */
export function stepIndex(current: number, count: number, dir: 1 | -1): number {
  if (count <= 0) return -1;
  if (current < 0 || current >= count) return dir === 1 ? 0 : count - 1;
  return (current + dir + count) % count;
}

/** "3 of 12", "No matches", or nothing before a query is typed. */
export function countLabel(current: number, count: number, query: string): string {
  if (!query) return "";
  if (count === 0) return "No matches";
  return `${current + 1} of ${count}${count >= MAX_MATCHES ? "+" : ""}`;
}
