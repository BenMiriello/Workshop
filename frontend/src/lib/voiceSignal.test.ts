import { describe, expect, it } from "vitest";
import { signalVerdict, type SignalReading } from "./voiceSignal";

const read = (over: Partial<SignalReading> = {}): SignalReading => ({
  trackLive: true,
  calibrated: true,
  heardSpeechEver: false,
  peak: 0.002,
  floor: 0.002,
  threshold: 0.06,
  ...over,
});

describe("silence is not a fault", () => {
  it("says nothing when the operator simply has not spoken yet", () => {
    // THE BUG. A quiet room over a working microphone used to print "No signal
    // from the microphone. Check the input device, or that another app isn't
    // holding it." Reported 2026-08-12: "it still prints out no signal from the
    // microphone if i dont speak fast enough".
    expect(signalVerdict(read({ peak: 0.0 }))).toBe("ok");
    expect(signalVerdict(read({ peak: 0.001 }))).toBe("ok");
    expect(signalVerdict(read({ peak: 0.002, floor: 0.002 }))).toBe("ok");
  });

  it("stays quiet through a long pause in a dead-silent room", () => {
    expect(signalVerdict(read({ peak: 0, floor: 0, calibrated: true }))).toBe("ok");
  });

  it("says nothing before the room has been measured", () => {
    expect(signalVerdict(read({ calibrated: false, peak: 0 }))).toBe("ok");
  });
});

describe("a device that actually stopped", () => {
  it("is the only thing that counts as no signal", () => {
    expect(signalVerdict(read({ trackLive: false }))).toBe("device-lost");
  });

  it("is reported even when the level looks fine", () => {
    // Another app taking the device leaves the track muted while the last
    // buffered levels may still look healthy. The track wins.
    expect(signalVerdict(read({ trackLive: false, peak: 0.9 }))).toBe("device-lost");
  });

  it("is reported even after speech has been heard this session", () => {
    // Unplugging mid-session is real, and must not be masked by past success.
    expect(signalVerdict(read({ trackLive: false, heardSpeechEver: true }))).toBe("device-lost");
  });
});

describe("a microphone that is genuinely too quiet", () => {
  it("is flagged when sound is clearly above the room but never reaches speech", () => {
    expect(signalVerdict(read({ floor: 0.002, peak: 0.02, threshold: 0.06 }))).toBe("too-quiet");
  });

  it("is NOT flagged when the peak is just the room itself", () => {
    // The margin is what separates a quiet talker from ordinary room variation.
    expect(signalVerdict(read({ floor: 0.01, peak: 0.015, threshold: 0.06 }))).toBe("ok");
  });

  it("is not flagged once speech has been heard", () => {
    // It demonstrably works, so nothing measured later makes it broken.
    expect(signalVerdict(read({ heardSpeechEver: true, floor: 0.002, peak: 0.02 }))).toBe("ok");
  });

  it("is not flagged when the peak clears the speech threshold", () => {
    expect(signalVerdict(read({ floor: 0.002, peak: 0.09, threshold: 0.06 }))).toBe("ok");
  });
});
