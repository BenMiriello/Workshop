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
// THE METER ALWAYS RENDERS. This used to return null for a zero reading, and
// the meter returned null with it, so the whole element vanished from the
// composer. On a new chat that is exactly what happens: an empty thread with no
// draft estimates to zero tokens, so the control was missing, then appeared as
// soon as a character was typed, then disappeared again when it was deleted.
// Operator, 2026-08-14: "it stops showing the context percentage at all, it just
// removes the element. That's not something we ever want."
//
// Case 1 above is not what null was protecting against anyway. That bug was a
// confident "0%" printed over a chat with tens of thousands of tokens in it,
// because the fetch was gated on `streaming` and never ran; it is fixed by the
// meter always having an estimate to fall back on, not by hiding the control. A
// genuinely empty chat IS at zero, and saying so is correct.
//
// A real reading shows the percent ROUNDED to the nearest whole number, which on
// a fresh 1M-window thread is simply "0%" or "1%". Operator directive
// 2026-08-11: the earlier "<1%" floor read as clutter.
export type ContextReading = { label: string; fraction: number };

export function contextReading(used: number, win: number): ContextReading {
  const u = Number.isFinite(used) && used > 0 ? used : 0;
  const w = Number.isFinite(win) && win > 0 ? win : 0;
  const ratio = w > 0 ? Math.min(1, u / w) : 0;
  return {
    label: `${Math.min(100, Math.round(ratio * 100))}%`,
    // The arc is drawn from the true ratio, not the rounded percent, so a
    // sliver still shows for a reading that displays as "0%".
    fraction: ratio,
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
//   server    — the backend's own count. The truth.
//   estimate  — no server count yet. Normal before a thread's first turn.
//   unavailable — the server count could not be read. NOT normal, and said.
//
// The label itself is a PLAIN percent in every case. The "~" the label used to
// carry for an estimate read as clutter on every new chat (operator, 2026-08-11:
// "why ~? why not just 0?"), and the meter already answers "measured or guessed"
// where that question actually gets asked, in the tooltip. `source` still drives
// the tooltip and the meter's own marking; it no longer decorates the number.
export type ContextSource = "server" | "estimate" | "unavailable";

export function contextLabel(reading: ContextReading): string {
  return reading.label;
}

// `Context: 120,000/1,000,000 tokens used`.
//
// It used to be up to two sentences explaining where the count came from and
// what it did not include. The shape was dictated on 2026-08-14; the digits in
// that dictation were unseparated and I took them literally, which was reading
// an example as a spec. Grouped, no spaces around the slash.
//
// The standing instruction behind it is broader than this tooltip: do not write
// explanatory copy that was not asked for.
export function contextTooltip(used: number, win: number): string {
  const u = Number.isFinite(used) && used > 0 ? Math.round(used) : 0;
  const w = Number.isFinite(win) && win > 0 ? Math.round(win) : 0;
  return `Context: ${u.toLocaleString("en-US")}/${w.toLocaleString("en-US")} tokens used`;
}
