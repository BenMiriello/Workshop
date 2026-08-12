import type { ChatSegment } from "../../lib/chat";

// The coordinate system a mid-turn message is placed in.
//
// A reply is cut open where the operator's message landed (see
// transcriptBlocks.ts). The question is what "where" is measured in, and the
// first answer was wrong: it counted TOP-LEVEL SEGMENTS, and a subagent's steps
// nest inside a single `agent` segment. So for the entire duration of a
// delegation — which on this system can be hundreds of steps and many minutes —
// the coordinate did not move at all.
//
// Measured on the operator's own thread (d899901fe4fe) on 2026-08-10:
//
//   reply seq32   128 top-level segments   672 nested steps   biggest chip 175
//   message seq37 recorded index 42
//   message seq38 recorded index 42        (sent minutes later)
//
// Two messages sent minutes apart got the same coordinate, so the second could
// only render in the same place as the first, and everything the delegate did
// between them rendered below both. That is the "my message shows ABOVE the last
// system block" report, exactly.
//
// A UNIT is one rendered thing: a top-level segment, or a step nested inside a
// subagent. The count advances with every step a delegate takes, so two messages
// sent minutes apart during one delegation get different coordinates, which is
// the whole point. A cut can therefore fall INSIDE a chip — see snapCut, which
// moves it to the chip's end rather than dividing the delegation.
//
// Old messages carry only `at_segment`. `unitOfSegmentIndex` converts those to
// the same coordinate space, and because it converts a segment boundary to the
// unit boundary at the same place, a transcript recorded before this existed
// renders exactly where it always did. Nothing shifts under the operator.

const isAgent = (s: ChatSegment): s is Extract<ChatSegment, { t: "agent" }> => s.t === "agent";

/** How many rendered units a segment list holds. Mirrors the backend's
 *  build_streaming.unit_count; the two must agree or a reload moves messages. */
export function unitCount(segments: ChatSegment[]): number {
  let n = 0;
  for (const s of segments) n += isAgent(s) ? 1 + s.steps.length : 1;
  return n;
}

/** The unit index at the start of top-level segment `segIndex` — how a legacy
 *  `at_segment` reads in unit space. */
export function unitOfSegmentIndex(segments: ChatSegment[], segIndex: number): number {
  let n = 0;
  for (let i = 0; i < segments.length && i < segIndex; i++) {
    const s = segments[i];
    n += isAgent(s) ? 1 + s.steps.length : 1;
  }
  return n;
}

/** A DELEGATION IS ONE THING AND IS NEVER DIVIDED.
 *
 *  A cut that lands inside a subagent chip moves to the chip's end, so the whole
 *  delegation stays in the slice where it was LAUNCHED and the message renders
 *  below it.
 *
 *  This used to divide the chip instead, showing it once on each side with the
 *  earlier piece forced back to not-done. Every consequence of that was wrong.
 *  The two pieces are the same delegation, so it read as a duplicate; both were
 *  not-done, so both pulsed as though two agents were running; the lower one's
 *  step count climbed while the upper one sat frozen at a number that never
 *  matched anything. And because the forcing was unconditional, a reply from
 *  days ago that had been interrupted mid-delegation still carried a pulsing
 *  chip for an agent that finished long before.
 *
 *  Snapping to the end and not the head is what keeps the message from drifting.
 *  The chip is one collapsed row while it runs, growing only in its step count,
 *  so nothing below it moves as the delegate works. Snapping to the head instead
 *  would put the message above a delegation that was already running when it was
 *  sent, which is the exact placement error this file exists to prevent.
 *
 *  No product surveyed divides a running delegation; they all keep one card and
 *  update it in place. See docs/decisions/0015. */
function snapCut(segments: ChatSegment[], cut: number): number {
  let u = 0;
  for (const seg of segments) {
    const head = u;
    u += 1;
    if (!isAgent(seg)) continue;
    u += seg.steps.length;
    // Strictly inside: the delegation was launched before the message and had
    // not reported back when it arrived. `head` itself is a clean boundary —
    // the message beat the launch, so the chip belongs below it.
    if (cut > head && cut < u) return u;
  }
  return cut;
}

/** The segments covering units [from, to). Chips are whole by construction:
 *  every bound has been through snapCut. */
function sliceUnits(segments: ChatSegment[], from: number, to: number): ChatSegment[] {
  const out: ChatSegment[] = [];
  let u = 0;
  for (const seg of segments) {
    const head = u;
    u += isAgent(seg) ? 1 + seg.steps.length : 1;
    if (head >= from && head < to) out.push(seg);
  }
  return out;
}

/** Split a segment list at the given UNIT indices.
 *
 *  Cuts are clamped into range and forced non-decreasing, so a stale or
 *  out-of-order coordinate can only make a block empty, never reorder one.
 *  Returns cuts.length + 1 slices. */
export function splitAtUnits(segments: ChatSegment[], cuts: number[]): ChatSegment[][] {
  const total = unitCount(segments);
  const bounds: number[] = [0];
  let prev = 0;
  for (const raw of cuts) {
    const clamped = Math.max(prev, Math.min(Number.isFinite(raw) ? raw : total, total));
    // Snap AFTER clamping, so a cut pushed forward past a delegation still can't
    // exceed the total or fall behind the cut before it.
    const at = Math.max(prev, Math.min(snapCut(segments, clamped), total));
    bounds.push(at);
    prev = at;
  }
  bounds.push(total);
  const out: ChatSegment[][] = [];
  for (let i = 0; i + 1 < bounds.length; i++) {
    out.push(sliceUnits(segments, bounds[i], bounds[i + 1]));
  }
  return out;
}

/** Where a message cuts the reply it interrupted, in unit space.
 *
 *  `at_unit` when the server recorded one; otherwise the legacy `at_segment`
 *  converted; otherwise the end, which puts the message below the whole reply —
 *  the honest answer when nothing says where it landed. */
export function cutPointOf(
  meta: { at_unit?: number; at_segment?: number } | undefined,
  segments: ChatSegment[],
): number {
  if (typeof meta?.at_unit === "number") return meta.at_unit;
  if (typeof meta?.at_segment === "number") return unitOfSegmentIndex(segments, meta.at_segment);
  return unitCount(segments);
}
