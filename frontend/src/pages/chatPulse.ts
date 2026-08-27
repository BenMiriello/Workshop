// Orchestrated pulse for the /chats running dots.
//
// Every running dot pulses on ONE shared clock so the page reads as coordinated
// rather than a field of dots blinking at random phases, and each dot down the
// list LAGS the one above it by 1/6 of the cycle, so the ripple travels DOWN,
// the seventh dot wrapping back onto the first. The caller freezes the delay the
// moment a dot goes live and never recomputes it (see RunningDot in Chats.tsx):
// changing animation-delay mid-cycle snaps the dot to a new phase, and that snap
// is the flicker to avoid.

export const PULSE_MS = 1800; // matches ws-pulse's duration in components.css
export const PULSE_SLOTS = 6; // a 1/6 lag per dot, wrapping every sixth

// The negative animation-delay for a dot at `ordinal` among the running dots.
// Each dot lags the one above it by one 1/6 step, so the pulse runs top to
// bottom. The phase term comes from the WALL CLOCK, not the element's own start
// time, which is what makes two dots that began running seconds apart still
// pulse together. Subtracting the lag and wrapping into [0, PULSE_MS) keeps the
// delay negative, so the cycle starts already in progress (the dot shows its
// phase on first paint, with no ramp-in). `now` is injected so the rule is
// testable.
export function pulseDelayMs(ordinal: number, now: number): number {
  const step = PULSE_MS / PULSE_SLOTS;
  const slot = ((ordinal % PULSE_SLOTS) + PULSE_SLOTS) % PULSE_SLOTS;
  const phaseNow = ((now % PULSE_MS) + PULSE_MS) % PULSE_MS;
  const lag = (((phaseNow - slot * step) % PULSE_MS) + PULSE_MS) % PULSE_MS;
  return -lag;
}

// The CSS value, sampled at call time. Called once per dot, on the transition
// into running.
export function pulseDelay(ordinal: number): string {
  return `${pulseDelayMs(ordinal, Date.now())}ms`;
}
