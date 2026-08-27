// Orchestrated pulse for the /chats running dots.
//
// Every running dot pulses on ONE shared clock so the page reads as coordinated
// rather than a field of dots blinking at random phases, and each dot is offset
// from the one above it by 1/6 of the cycle, the seventh wrapping back onto the
// first. The caller freezes the delay the moment a dot goes live and never
// recomputes it (see RunningDot in Chats.tsx): changing animation-delay mid-cycle
// snaps the dot to a new phase, and that snap is the flicker to avoid.

export const PULSE_MS = 1800; // matches ws-pulse's duration in components.css
export const PULSE_SLOTS = 6; // a 1/6 offset per dot, wrapping every sixth

// The negative animation-delay for a dot at `ordinal` among the running dots.
// Negative so the cycle starts already in progress (the dot shows its phase on
// first paint, no ramp-in). The phase term comes from the WALL CLOCK, not the
// element's own start time, which is what makes two dots that began running
// seconds apart still pulse together. `now` is injected so the rule is testable.
export function pulseDelayMs(ordinal: number, now: number): number {
  const slot = ((ordinal % PULSE_SLOTS) + PULSE_SLOTS) % PULSE_SLOTS;
  const phaseNow = ((now % PULSE_MS) + PULSE_MS) % PULSE_MS;
  return -(phaseNow + slot * (PULSE_MS / PULSE_SLOTS));
}

// The CSS value, sampled at call time. Called once per dot, on the transition
// into running.
export function pulseDelay(ordinal: number): string {
  return `${pulseDelayMs(ordinal, Date.now())}ms`;
}
