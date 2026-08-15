import { describe, expect, it } from "vitest";
import { keyboardInset, updateBaseline, type ViewportBaseline } from "./keyboardInset";

/** Feed a sequence of readings through the rule the way the hook does. */
function track(readings: { viewportHeight: number; innerHeight: number; width?: number }[]) {
  let baseline: ViewportBaseline = null;
  return readings.map((raw) => {
    const r = { width: 390, ...raw };
    baseline = updateBaseline(baseline, r);
    return keyboardInset(r, baseline);
  });
}

describe("keyboardInset", () => {
  // THE MODEL THIS DOCUMENT ACTUALLY ASKS FOR. index.html sets
  // `interactive-widget=resizes-content`, so the keyboard shrinks the layout
  // viewport too and innerHeight tracks the visual viewport exactly. The old
  // rule was `innerHeight - visualViewport.height`, which is 0 here for every
  // reading — the whole of the 2026-08-14 regression.
  it("measures the keyboard when both viewports shrink (resizes-content)", () => {
    expect(track([
      { viewportHeight: 844, innerHeight: 844 },   // at rest
      { viewportHeight: 508, innerHeight: 508 },   // keyboard up
      { viewportHeight: 844, innerHeight: 844 },   // and down again
    ])).toEqual([0, 336, 0]);
  });

  it("measures it when only the visual viewport shrinks (resizes-visual)", () => {
    expect(track([
      { viewportHeight: 844, innerHeight: 844 },
      { viewportHeight: 508, innerHeight: 844 },
      { viewportHeight: 844, innerHeight: 844 },
    ])).toEqual([0, 336, 0]);
  });

  // Mounting a chat from a screen that already had something focused. There is
  // no resting reading yet, so the baseline is the shrunken height; innerHeight
  // is what answers this, and only under resizes-visual can it.
  it("answers immediately when it mounts with the keyboard already open", () => {
    expect(track([{ viewportHeight: 508, innerHeight: 844 }])).toEqual([336]);
  });

  // ...and under resizes-content that case genuinely cannot be told apart from a
  // short window, so it reports nothing rather than guessing. The first resting
  // reading fixes it, which is the next keystroke away.
  it("reports nothing it cannot know, rather than a guess", () => {
    expect(track([
      { viewportHeight: 508, innerHeight: 508 },
      { viewportHeight: 844, innerHeight: 844 },
      { viewportHeight: 508, innerHeight: 508 },
    ])).toEqual([0, 0, 336]);
  });

  // Hiding the URL bar grows the viewport by tens of pixels and showing it
  // shrinks it back. Neither is a keyboard, and treating the second as one would
  // collapse the composer's padding at rest.
  it("does not read browser chrome as a keyboard", () => {
    expect(track([
      { viewportHeight: 790, innerHeight: 790 },   // URL bar showing
      { viewportHeight: 844, innerHeight: 844 },   // scrolled, URL bar hidden
      { viewportHeight: 790, innerHeight: 790 },   // back again: 54px, not a keyboard
    ])).toEqual([0, 0, 0]);
  });

  it("still sees a keyboard opening under the taller chrome baseline", () => {
    expect(track([
      { viewportHeight: 790, innerHeight: 790 },
      { viewportHeight: 844, innerHeight: 844 },
      { viewportHeight: 508, innerHeight: 508 },
    ])).toEqual([0, 0, 336]);
  });

  // A rotation changes the resting height outright. Carrying the portrait
  // baseline into landscape would report a permanent 400px keyboard.
  it("starts a new baseline when the orientation changes", () => {
    let baseline: ViewportBaseline = null;
    const portrait = { viewportHeight: 844, innerHeight: 844, width: 390 };
    baseline = updateBaseline(baseline, portrait);
    const landscape = { viewportHeight: 390, innerHeight: 390, width: 844 };
    baseline = updateBaseline(baseline, landscape);
    expect(keyboardInset(landscape, baseline)).toBe(0);
    const typing = { viewportHeight: 190, innerHeight: 190, width: 844 };
    expect(keyboardInset(typing, updateBaseline(baseline, typing))).toBe(200);
  });

  it("never returns a negative inset", () => {
    expect(keyboardInset(
      { viewportHeight: 900, innerHeight: 800, width: 390 },
      { width: 390, height: 844 },
    )).toBe(0);
  });
});
