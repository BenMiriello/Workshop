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
// subagent. The count advances with every step a delegate takes, so a cut can
// fall INSIDE a chip — which is why splitAtUnits knows how to divide one.
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

/** The segments covering units [from, to), with a subagent chip divided when the
 *  range falls inside one.
 *
 *  A divided chip appears once on each side of the message. The earlier piece is
 *  forced back to not-done and carries no result: when the message landed, that
 *  delegation had not reported back, and showing its conclusion above the
 *  message would be the same lie in miniature that this whole file exists to
 *  end. The piece holding the chip's last unit keeps `done` and `result`. */
function sliceUnits(segments: ChatSegment[], from: number, to: number): ChatSegment[] {
  const out: ChatSegment[] = [];
  let u = 0;
  for (const seg of segments) {
    const head = u;
    u += 1;
    if (!isAgent(seg)) {
      if (head >= from && head < to) out.push(seg);
      continue;
    }
    const stepsStart = u;
    u += seg.steps.length;
    const end = u; // exclusive
    if (end <= from || head >= to) continue;
    const steps = seg.steps.slice(
      Math.max(0, from - stepsStart),
      Math.max(0, Math.min(to, end) - stepsStart),
    );
    const takesHead = head >= from && head < to;
    if (!takesHead && !steps.length) continue;
    const tail = to >= end;
    out.push(tail ? { ...seg, steps } : { ...seg, steps, done: false, result: "" });
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
    const at = Math.max(prev, Math.min(Number.isFinite(raw) ? raw : total, total));
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
