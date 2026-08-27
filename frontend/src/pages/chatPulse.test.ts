import { describe, it, expect } from "vitest";
import { pulseDelayMs, PULSE_MS, PULSE_SLOTS } from "./chatPulse";

const STEP = PULSE_MS / PULSE_SLOTS; // 300ms

describe("pulseDelayMs", () => {
  it("offsets each consecutive dot by exactly 1/6 of the cycle", () => {
    const now = 0;
    for (let i = 1; i < PULSE_SLOTS; i++) {
      expect(pulseDelayMs(i - 1, now) - pulseDelayMs(i, now)).toBe(STEP);
    }
  });

  it("wraps: the seventh dot lands on the first's phase", () => {
    const now = 12345;
    expect(pulseDelayMs(PULSE_SLOTS, now)).toBe(pulseDelayMs(0, now));
    expect(pulseDelayMs(PULSE_SLOTS + 2, now)).toBe(pulseDelayMs(2, now));
  });

  it("shares one wall-clock phase, so a given ordinal is identical everywhere at a given time", () => {
    // Two dots that started running at different real times but read the same
    // clock get the same delay for the same ordinal: they pulse together.
    const now = 999;
    expect(pulseDelayMs(3, now)).toBe(pulseDelayMs(3, now));
  });

  it("is always negative (the cycle starts already in progress, no ramp-in)", () => {
    for (const now of [0, 100, 900, 1799, 5000]) {
      for (let i = 0; i < 8; i++) expect(pulseDelayMs(i, now)).toBeLessThanOrEqual(0);
    }
  });

  it("advances the phase with the clock within a cycle", () => {
    // Later in the same cycle means a more-negative delay (further into the
    // animation), for a fixed ordinal.
    expect(pulseDelayMs(0, 500)).toBeLessThan(pulseDelayMs(0, 100));
  });
});
