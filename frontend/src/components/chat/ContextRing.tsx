import { contextLabel, contextReading } from "./contextReading";

// HOW FULL A CONTEXT WINDOW IS, drawn. Used and window in, an arc and a
// percentage out.
//
// Extracted from `ContextMeter` when the dashboard grew a per-chat reading. The
// meter owns WHICH number to render (the server's count, the run's live report,
// or the client estimate, in that order, plus the unsent draft) and that logic
// is genuinely the composer's. The DRAWING is not: a second ring would be a
// second rounding rule and a second arc geometry for one quantity, and the
// first thing that drifts is which of them says 0%.
//
// A single accent tone, no threshold colours: this is a reading, not a status.
// The arc is a dash on a circle the stylesheet rotates, so it fills clockwise
// from 12 o'clock.

const RING_R = 7;
const RING_C = 2 * Math.PI * RING_R;

export function ContextRing({
  used, win, size = 18, label = true,
}: {
  used: number;
  win: number;
  /** 18 in the composer, 15 on a dashboard row. The viewBox is fixed, so this
   *  scales the whole drawing rather than changing its proportions. */
  size?: number;
  /** The percentage beside the arc. Off when the caller places its own. */
  label?: boolean;
}) {
  const reading = contextReading(used, win);
  return (
    <>
      <svg
        className="chat-context-meter__ring"
        width={size} height={size} viewBox="0 0 18 18" aria-hidden
      >
        <circle className="chat-context-meter__track" cx="9" cy="9" r={RING_R} />
        <circle
          className="chat-context-meter__arc"
          cx="9" cy="9" r={RING_R}
          style={{ strokeDasharray: `${reading.fraction * RING_C} ${RING_C}` }}
        />
      </svg>
      {label && <span className="chat-context-meter__pct">{contextLabel(reading)}</span>}
    </>
  );
}
