import { describe, it, expect } from "vitest";
import { PALETTES, MODES, buildScheme, contrast, type Scheme } from "./palette.mjs";
import { PALETTE_META, DEFAULT_PALETTE } from "./palettes.gen";

// The color system's guarantees, executable. A palette is a personal choice, so
// the thing that must not vary is whether the result is READABLE: every one of
// the five schemes, in both modes, has to clear the same bar. Contrast is the
// rule behind the design, extracted so it can be run (frontend/CLAUDE.md) —
// eyeballing ten schemes is exactly how one of them quietly ships at 4.1:1.

const AA_TEXT = 4.5; // WCAG 1.4.3 normal text
const AA_MARK = 3.0; // WCAG 1.4.11 non-text (dots, fills, marks)

const schemes: [string, "light" | "dark", Scheme][] = PALETTES.flatMap((p) =>
  MODES.map((m) => [p.id, m, buildScheme(p.id, m)] as [string, "light" | "dark", Scheme]),
);

const ratio = (a: string, b: string) => Math.round(contrast(a, b) * 100) / 100;

describe("every scheme is readable", () => {
  it.each(schemes)("%s %s — text clears AA on both surfaces", (_id, _mode, s) => {
    for (const surface of [s.bg, s.card, s.bgInset]) {
      for (const token of ["fg", "muted", "accent", "running", "stopped", "ok", "warn", "danger"]) {
        expect
          .soft(ratio(s[token], surface), `--${token} on ${surface}`)
          .toBeGreaterThanOrEqual(AA_TEXT);
      }
    }
  });

  it.each(schemes)("%s %s — marks clear non-text contrast", (_id, _mode, s) => {
    for (const surface of [s.bg, s.card]) {
      for (const token of ["okSolid", "warnSolid", "dangerSolid", "stoppedSolid", "runningSolid"]) {
        expect
          .soft(ratio(s[token], surface), `--${token} on ${surface}`)
          .toBeGreaterThanOrEqual(AA_MARK);
      }
    }
  });

  it.each(schemes)("%s %s — a filled accent button is legible", (_id, _mode, s) => {
    // The on-primary rule: accent-fg is whichever end of the ramp wins. If this
    // fails the accent tone is wrong, not the foreground.
    expect(ratio(s.accentFg, s.accent)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(ratio(s.accentFg, s.accentHover)).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(schemes)("%s %s — surfaces are separable from each other", (_id, _mode, s) => {
    // Hierarchy comes from tonal surfaces, so they must actually differ.
    expect(s.card).not.toBe(s.bg);
    expect(s.cardHover).not.toBe(s.card);
    expect(s.border).not.toBe(s.card);
  });
});

describe("status means the same thing in every palette", () => {
  const statusTokens = ["ok", "okSolid", "okSoft", "warn", "warnSolid", "warnSoft", "danger", "dangerSolid", "dangerSoft", "dangerHover"];

  it.each(MODES)("%s — ok/warn/danger are identical across all five palettes", (mode) => {
    const [first, ...rest] = PALETTES.map((p) => buildScheme(p.id, mode));
    for (const s of rest) {
      for (const t of statusTokens) expect(s[t], t).toBe(first[t]);
    }
  });

  it.each(MODES)("%s — running follows the accent, stopped follows the neutral", (mode) => {
    for (const p of PALETTES) {
      const s = buildScheme(p.id, mode);
      expect(s.runningSolid).toBe(s.accent);
      expect(s.stopped).not.toBe(s.ok);
    }
  });

  it.each(MODES)("%s — the five status kinds are distinguishable from one another", (mode) => {
    for (const p of PALETTES) {
      const s = buildScheme(p.id, mode);
      const kinds = [s.okSolid, s.runningSolid, s.stoppedSolid, s.warnSolid, s.dangerSolid];
      expect(new Set(kinds).size).toBe(5);
    }
  });
});

describe("the palette set is coherent", () => {
  it("the generated menu matches the model", () => {
    expect(PALETTE_META.map((p) => p.id)).toEqual(PALETTES.map((p) => p.id));
    expect(DEFAULT_PALETTE).toBe("violet");
    expect(PALETTES).toHaveLength(5);
  });

  it("every scheme defines exactly the same tokens", () => {
    const keys = Object.keys(buildScheme("violet", "dark")).sort();
    for (const [id, mode, s] of schemes) {
      expect(Object.keys(s).sort(), `${id} ${mode}`).toEqual(keys);
    }
  });

  it("every value is a real css color", () => {
    for (const [id, mode, s] of schemes) {
      for (const [k, v] of Object.entries(s)) {
        expect(v, `${id} ${mode} ${k}`).toMatch(/^(#[0-9a-f]{6}|rgba\(|transparent$|0 |none$)/);
      }
    }
  });

  it("the five accents are actually five different colors", () => {
    for (const mode of MODES) {
      const accents = PALETTES.map((p) => buildScheme(p.id, mode).accent);
      expect(new Set(accents).size).toBe(PALETTES.length);
    }
  });
});

describe("Violet still is the theme this system started as", () => {
  // The operator asked to keep the current look available. These are the
  // hand-tuned values from before the model existed; the ramp was derived FROM
  // them, so a drift here means the ramp moved, not that a palette changed.
  it("light anchors are unchanged", () => {
    const s = buildScheme("violet", "light");
    expect(s.bg).toBe("#f5f5fa");
    expect(s.fg).toBe("#1a1923");
    expect(s.card).toBe("#ffffff");
    expect(s.border).toBe("#e7e7f0");
    expect(s.accentFg).toBe("#ffffff");
  });

  it("the light accent is the same violet, one step darker to clear AA", () => {
    // The original #7a57e0 reads 4.28:1 on --bg-inset, under AA. The solver
    // takes it down a step. Pin BOTH facts: the exact tone, and that it is
    // still perceptually the same color as the violet this system shipped with.
    const s = buildScheme("violet", "light");
    expect(s.accent).toBe("#7551da");
    expect(contrast(s.accent, "#7a57e0")).toBeLessThan(1.1);
  });

  it("dark anchors are unchanged", () => {
    const s = buildScheme("violet", "dark");
    expect(s.bg).toBe("#0f0e17");
    expect(s.card).toBe("#191824");
    expect(s.cardRing).toBe("#272636");
    expect(s.border).toBe("#262534");
    expect(s.ok).toBe("#62d29d");
  });
});
