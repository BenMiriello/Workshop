// What the context meter says, given a token count and a window.
//
// Two separate things used to render as the same string, "0%":
//
//  1. No reading at all. The meter mounted during a turn, its fetch was gated on
//     `streaming` and never ran, and it printed a confident 0% about a chat with
//     tens of thousands of tokens in it. That was the bug the operator reported.
//  2. A real reading that rounds down. Every current Claude model has a 1M
//     window, so a fresh thread carrying only its preamble is genuinely under
//     0.5% and rounds to zero. The number is honest and still reads as broken,
//     because "0%" is what a broken meter says.
//
// So: no reading renders nothing (`null`), and a reading that rounds to zero
// floors to "<1%". The meter never claims a chat is empty.
export type ContextReading = { label: string; fraction: number };

export function contextReading(used: number, win: number): ContextReading | null {
  if (!Number.isFinite(used) || !Number.isFinite(win)) return null;
  if (used <= 0 || win <= 0) return null;
  const pct = Math.min(100, Math.round((used / win) * 100));
  return {
    label: pct < 1 ? "<1%" : `${pct}%`,
    // The arc is drawn from the true ratio, not the rounded percent, so a
    // sliver still shows for a reading that displays as "<1%".
    fraction: Math.min(1, used / win),
  };
}
