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
// So: no reading renders nothing (`null`). A real reading shows the percent
// ROUNDED to the nearest whole number, which on a fresh 1M-window thread is
// simply "0%" or "1%". Operator directive 2026-08-11: the earlier "<1%" floor
// read as clutter; a plain rounded percent is what the meter shows. (Case 1
// above stays fixed by returning null, not by dressing up the zero.)
export type ContextReading = { label: string; fraction: number };

export function contextReading(used: number, win: number): ContextReading | null {
  if (!Number.isFinite(used) || !Number.isFinite(win)) return null;
  if (used <= 0 || win <= 0) return null;
  const pct = Math.min(100, Math.round((used / win) * 100));
  return {
    label: `${pct}%`,
    // The arc is drawn from the true ratio, not the rounded percent, so a
    // sliver still shows for a reading that displays as "0%".
    fraction: Math.min(1, used / win),
  };
}

// WHERE THE NUMBER CAME FROM, which the meter has to say.
//
// Only the backend can count this: it assembles the actual prompt (persona, live
// app context, compacted history), none of which the client can see. The client
// estimate is characters divided by four over the transcript, which is a
// different quantity that happens to render in the same units.
//
// The meter used to swallow a failed `/context` read (`.catch(() => {})`, with a
// comment saying "keep the client estimate") and go on printing a confident
// percentage from that estimate, indistinguishable from the real one. So the
// operator could not tell "this chat is 40% full" from "the server did not
// answer and this is a guess about a prompt I cannot see".
//
//   server    — the backend's own count. Unmarked, because it is the truth.
//   estimate  — no server count yet. Normal before a thread's first turn.
//   unavailable — the server count could not be read. NOT normal, and said.
//
// Both estimate cases carry a leading "~" on the label. One character, no new
// color, and it is the conventional mark for "about".
export type ContextSource = "server" | "estimate" | "unavailable";

export function contextLabel(reading: ContextReading, source: ContextSource): string {
  return source === "server" ? reading.label : `~${reading.label}`;
}

export function contextTooltip(used: number, win: number, source: ContextSource): string {
  const of = `${used.toLocaleString()} of ${win.toLocaleString()} tokens`;
  if (source === "server") return `${of} used in this chat, counted by the server.`;
  const why = source === "unavailable"
    ? "The server's own count could not be read, so this is the browser's estimate"
    : "Estimated in the browser until the server has counted this chat's prompt";
  return `About ${of}. ${why}, which does not include the persona or any live context the model is sent.`;
}
