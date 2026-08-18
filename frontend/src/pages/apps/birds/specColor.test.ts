// The spectrogram ramp, which two canvases share. The assertions are about the
// SHAPE of the ramp (continuous, monotone in brightness, in range) rather than
// exact triples: a palette tweak should be free, a discontinuity should not.
import { describe, expect, it } from "vitest";
import { specColor } from "./specColor";

const rgb = (s: string): [number, number, number] => {
  const m = s.match(/^rgb\(([^,]+),([^,]+),([^)]+)\)$/);
  if (!m) throw new Error(`not an rgb() string: ${s}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
};

describe("specColor", () => {
  it("returns a parseable rgb() string across the whole 0..255 range", () => {
    for (let v = 0; v <= 255; v++) {
      const [r, g, b] = rgb(specColor(v));
      for (const c of [r, g, b]) {
        expect(Number.isFinite(c)).toBe(true);
        expect(c).toBeGreaterThanOrEqual(0);
        expect(c).toBeLessThanOrEqual(255);
      }
    }
  });

  it("is continuous at every stop boundary", () => {
    // A canvas fills one column per sample, so a jump at a stop shows up as a
    // visible band across the spectrogram.
    for (const t of [0.25, 0.55, 0.8]) {
      const before = rgb(specColor(Math.floor(t * 255) - 1));
      const after = rgb(specColor(Math.ceil(t * 255) + 1));
      for (let i = 0; i < 3; i++) expect(Math.abs(after[i] - before[i])).toBeLessThan(24);
    }
  });

  it("gets brighter with magnitude, so a loud band reads as loud", () => {
    const lum = (v: number) => { const [r, g, b] = rgb(specColor(v)); return r + g + b; };
    let prev = -1;
    for (let v = 0; v <= 255; v += 5) {
      const cur = lum(v);
      expect(cur).toBeGreaterThan(prev);
      prev = cur;
    }
  });

  it("floors near black and tops out near white", () => {
    const [r0, g0, b0] = rgb(specColor(0));
    expect(r0 + g0 + b0).toBeLessThan(90);
    const [r1, g1, b1] = rgb(specColor(255));
    expect(Math.min(r1, g1, b1)).toBeGreaterThan(200);
  });
});
