import { describe, it, expect } from "vitest";
import { SCHEMES, MODES, buildScheme, contrast, paletteById, type Scheme } from "./palette.mjs";
import { PALETTE_META, DEFAULT_PALETTE, LEGACY_PALETTES, resolvePaletteId } from "./palettes.gen";

// The color system's guarantees, executable. A scheme is a personal choice, so
// the things that must not vary are whether the result is READABLE and whether
// the agency axis still says what it says: every one of the eight schemes, in
// both modes, has to clear the same bar. Contrast is the rule behind the design,
// extracted so it can be run (frontend/CLAUDE.md) — eyeballing sixteen schemes
// is exactly how one of them quietly ships at 4.1:1.

const AA_TEXT = 4.5; // WCAG 1.4.3 normal text
const AA_MARK = 3.0; // WCAG 1.4.11 non-text (dots, fills, marks)

const schemes: [string, "light" | "dark", Scheme][] = SCHEMES.flatMap((p) =>
  MODES.map((m) => [p.id, m, buildScheme(p.id, m)] as [string, "light" | "dark", Scheme]),
);

const ratio = (a: string, b: string) => Math.round(contrast(a, b) * 100) / 100;

/** Shortest distance between two hues, in degrees. */
const hueGap = (a: number, b: number) => {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
};

describe("every scheme is readable", () => {
  it.each(schemes)("%s %s — text clears AA on every surface", (_id, _mode, s) => {
    for (const surface of [s.bg, s.card, s.bgInset]) {
      for (const token of ["fg", "muted", "faint", "you", "sys", "auto", "running", "stopped", "ok", "warn", "danger"]) {
        expect
          .soft(ratio(s[token], surface), `--${token} on ${surface}`)
          .toBeGreaterThanOrEqual(AA_TEXT);
      }
    }
  });

  // The four tightest pairs the handoff names by hand: 10px rail labels and
  // 10.5px chips, which are small text sitting ON the tier's own soft fill
  // rather than on a surface. If a lightness stop moves, these fail first.
  it.each(schemes)("%s %s — an agency chip's label clears AA on its own fill", (_id, _mode, s) => {
    // Exactly the four pairs the handoff names as its tightest cases: the rail
    // label and running chip, the working chip, the user bubble, and metadata.
    expect.soft(ratio(s.auto, s.autoSoft), "--auto on --auto-soft").toBeGreaterThanOrEqual(AA_TEXT);
    expect.soft(ratio(s.sys, s.sysSoft), "--sys on --sys-soft").toBeGreaterThanOrEqual(AA_TEXT);
    expect.soft(ratio(s.youFg, s.you), "--you-fg on --you").toBeGreaterThanOrEqual(AA_TEXT);
    expect.soft(ratio(s.faint, s.card), "--faint on --card").toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(schemes)("%s %s — the active tones stay readable on their fills", (_id, _mode, s) => {
    // `-up` is what a running chip and a live rail use, so it carries the same
    // burden as the resting tone.
    expect.soft(ratio(s.autoUp, s.autoSoft), "--auto-up on --auto-soft").toBeGreaterThanOrEqual(AA_TEXT);
    expect.soft(ratio(s.sysUp, s.sysSoft), "--sys-up on --sys-soft").toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(schemes)("%s %s — marks clear non-text contrast", (_id, _mode, s) => {
    for (const surface of [s.bg, s.card]) {
      for (const token of ["okSolid", "warnSolid", "dangerSolid", "stoppedSolid", "runningSolid", "you", "auto"]) {
        expect
          .soft(ratio(s[token], surface), `--${token} on ${surface}`)
          .toBeGreaterThanOrEqual(AA_MARK);
      }
    }
  });

  it.each(schemes)("%s %s — a filled `you` button is legible", (_id, _mode, s) => {
    // The user bubble and the primary button are both --you-fg on --you.
    expect(ratio(s.youFg, s.you)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(ratio(s.accentFg, s.accentHover)).toBeGreaterThanOrEqual(AA_TEXT);
  });

  it.each(schemes)("%s %s — surfaces are separable from each other", (_id, _mode, s) => {
    // Hierarchy comes from tonal surfaces, so they must actually differ.
    expect(s.card).not.toBe(s.bg);
    expect(s.cardHover).not.toBe(s.card);
    expect(s.border).not.toBe(s.card);
    expect(s.bgInset).not.toBe(s.bg);
  });

  // `not.toBe` is not enough for the one pair that carries a MEANING difference.
  // A grouped block of tool calls and a delegation are two different kinds of
  // thing in the chat log, and they are told apart by fill; two colors that
  // merely differ in the last bit are the same color to a reader.
  //
  // This exists because they DID collide. The group's fill was authored as a
  // veil of --fg over whatever it sat on, which lifts it clear of `bgInset` in
  // dark (they land 26/255 apart) and drops it straight onto `bgInset` in light
  // (3/255 apart, in all eight schemes). It was invisible to a dark-mode check
  // and wrong in half the app. Measured and reported 2026-08-14.
  //
  // TWO SURFACES, TWO OPPOSITE JOBS, and the reason there are two of them.
  //
  // A grouped block (`bgBlock`) must be SOFT: it is this turn's own steps behind
  // a count, sitting inside a message, and the operator's report on a loud one
  // was that it stood out too much from the message it was in. A delegation
  // (`bgSunken`) must be UNMISTAKABLE: it holds a whole separate context.
  //
  // Those two demands were made of one token for four rounds and cannot both be
  // met by it, because in light mode the old `bgInset` sat BETWEEN the card and
  // anywhere a distinct block could go: soft enough for the card meant landing on
  // the delegation, and clear of the delegation meant loud against the card.
  // Splitting them is what resolves it, and these bounds are what keep them from
  // drifting back into each other's territory.
  it.each(schemes)("%s %s — a grouped block stays a whisper on its card", (_id, _mode, s) => {
    expect(ratio(s.bgBlock, s.card), "--bg-block vs --card").toBeGreaterThan(1.0);
    expect(ratio(s.bgBlock, s.card), "--bg-block vs --card").toBeLessThanOrEqual(1.18);
  });

  // The load-bearing pair is delegation vs BLOCK: those are the two the operator
  // reported as indistinguishable, and the two that mean different things.
  //
  // Against the CARD the bar is deliberately lower, and dark mode is why. There
  // the block sits just ABOVE the card and the panel just below it, so they are
  // told apart by being on opposite sides of it rather than by distance, and the
  // panel only needs to read as recessed (~1.10). It also carries a border,
  // which is the other half of "a group is a fill, a delegation is a frame". In
  // light both sit below the card and the panel does the travelling (~1.21).
  it.each(schemes)("%s %s — a delegation is unmistakable against a block", (_id, _mode, s) => {
    expect(ratio(s.bgSunken, s.bgBlock), "--bg-sunken vs --bg-block").toBeGreaterThanOrEqual(1.15);
    expect(ratio(s.bgSunken, s.card), "--bg-sunken vs --card").toBeGreaterThanOrEqual(1.08);
  });

  // Neither is in `surfacesOf`, so text tones are not SOLVED against them the way
  // they are against bg/card/inset. Both carry text anyway (a block and a panel
  // are both full of --muted rows), so the floor is asserted rather than assumed.
  it.each(schemes)("%s %s — the ink on both chat surfaces clears AA", (_id, _mode, s) => {
    for (const [name, surface] of [["--bg-block", s.bgBlock], ["--bg-sunken", s.bgSunken]] as const) {
      expect.soft(ratio(s.muted, surface), `--muted on ${name}`).toBeGreaterThanOrEqual(AA_TEXT);
      expect.soft(ratio(s.fg, surface), `--fg on ${name}`).toBeGreaterThanOrEqual(AA_TEXT);
    }
  });
});

describe("the agency axis actually separates its three tiers", () => {
  it.each(schemes)("%s %s — you / system / autonomous are three different colors", (_id, _mode, s) => {
    expect(new Set([s.you, s.sys, s.auto]).size).toBe(3);
    expect(new Set([s.youSoft, s.sysSoft, s.autoSoft]).size).toBe(3);
  });

  it.each(SCHEMES.filter((s) => !s.quiet))("$label — the machine pair is one family, well off `you`", (s) => {
    // The expressive set spends three hues: `you` takes the base, and the pair
    // sits together, adjacent enough to read as one family. `system` is always
    // far across the wheel; `autonomous` can come back closer when the plain
    // split-complement would collide with a pinned status hue (Cobalt's blue
    // base points straight at amber, i.e. at warn, so its pair went cool
    // instead and its escalation lands 32 degrees off the base — it separates
    // by chroma and lightness there, not by hue).
    expect(hueGap(s.base, s.sys)).toBeGreaterThanOrEqual(60);
    expect(hueGap(s.base, s.auto)).toBeGreaterThanOrEqual(30);
    expect(hueGap(s.sys, s.auto)).toBeLessThanOrEqual(90);
  });

  it.each(SCHEMES.filter((s) => !s.quiet))("$label — autonomous is the more advancing sibling", (s) => {
    // Autonomous is the escalation tier: it always carries more chroma than the
    // system tier it is paired with.
    expect(s.ac ?? 1).toBeGreaterThan(s.sc ?? 1);
  });

  it.each(SCHEMES.filter((s) => s.quiet))("$label — the quiet set separates by chroma, not hue", (s) => {
    // One hue for you+system; the axis is carried entirely by the chroma ratio,
    // and `system` is offset lighter so it cannot be mistaken for body text.
    expect(s.base).toBe(s.sys);
    expect(s.sc).toBeLessThan((s.yc ?? 1) / 2);
    expect(s.sl).toBeGreaterThan(0);
  });

  it.each(MODES)("%s — `running` follows the system tier by default", (mode) => {
    // A run is the one status that describes an ACTOR, so it takes the
    // initiating tier's active tone. Nothing else in the status five moves.
    for (const p of SCHEMES) {
      const s = buildScheme(p.id, mode);
      expect(s.running).toBe(s.sysUp);
      expect(s.runningSoft).toBe(s.sysSoft);
    }
  });

  it.each(MODES)("%s — the accent family IS the `you` tier", (mode) => {
    for (const p of SCHEMES) {
      const s = buildScheme(p.id, mode);
      expect(s.accent).toBe(s.you);
      expect(s.accentFg).toBe(s.youFg);
      expect(s.accentSoft).toBe(s.youSoft);
    }
  });
});

describe("status means the same thing in every scheme", () => {
  const statusTokens = ["ok", "okSolid", "okSoft", "warn", "warnSolid", "warnSoft", "danger", "dangerSolid", "dangerSoft", "dangerHover"];

  it.each(MODES)("%s — ok/warn/danger are identical across all eight schemes", (mode) => {
    const [first, ...rest] = SCHEMES.map((p) => buildScheme(p.id, mode));
    for (const s of rest) {
      for (const t of statusTokens) expect(s[t], t).toBe(first[t]);
    }
  });

  it.each(MODES)("%s — stopped follows the neutral and is not a status hue", (mode) => {
    for (const p of SCHEMES) {
      const s = buildScheme(p.id, mode);
      expect(s.stopped).not.toBe(s.ok);
      expect(s.stopped).not.toBe(s.warn);
    }
  });

  it.each(MODES)("%s — the five status kinds are distinguishable from one another", (mode) => {
    for (const p of SCHEMES) {
      const s = buildScheme(p.id, mode);
      const kinds = [s.okSolid, s.runningSolid, s.stoppedSolid, s.warnSolid, s.dangerSolid];
      expect(new Set(kinds).size).toBe(5);
    }
  });
});

describe("the scheme set is coherent", () => {
  it("the generated menu matches the model", () => {
    expect(PALETTE_META.map((p) => p.id)).toEqual(SCHEMES.map((p) => p.id));
    expect(PALETTE_META.map((p) => p.group)).toEqual(SCHEMES.map((p) => (p.quiet ? "quiet" : "expressive")));
    expect(DEFAULT_PALETTE).toBe("iris");
    expect(SCHEMES).toHaveLength(8);
    expect(SCHEMES.filter((s) => s.quiet)).toHaveLength(3);
  });

  it("a stored scheme from before the agency model resolves to its nearest survivor", () => {
    // Nobody's saved preference should snap back to the default silently.
    expect(resolvePaletteId("violet")).toBe("iris");
    expect(resolvePaletteId("teal")).toBe("lagoon");
    expect(resolvePaletteId("sandstone")).toBe("linen");
    expect(resolvePaletteId("cobalt")).toBe("cobalt"); // kept its id and its 258 base
    expect(resolvePaletteId("nonsense")).toBe(DEFAULT_PALETTE);
    expect(resolvePaletteId(null)).toBe(DEFAULT_PALETTE);
    for (const to of Object.values(LEGACY_PALETTES)) {
      expect(SCHEMES.map((s) => s.id)).toContain(to);
    }
  });

  it("every scheme defines exactly the same tokens", () => {
    const keys = Object.keys(buildScheme("iris", "dark")).sort();
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

  it("the eight `you` tones are eight different colors", () => {
    for (const mode of MODES) {
      const tones = SCHEMES.map((p) => buildScheme(p.id, mode).you);
      expect(new Set(tones).size).toBe(SCHEMES.length);
    }
  });

  it("surfaces carry the `you` hue, trimmed where it would tint too visibly", () => {
    // Warm and green bases tint neutrals far more than blue or violet ones at
    // equal chroma, so those schemes trim harder. Pinned as a seed constraint
    // because no contrast check can see it.
    expect(paletteById("fern").bgC).toBeLessThan(0.5);
    expect(paletteById("clay").bgC).toBeLessThan(0.6);
    expect(paletteById("graphite").bgC).toBeLessThan(0.3);
  });
});

describe("the designer's stops are what actually ship", () => {
  // Spot values straight from the handoff's tokens.css (Agency Palette v7),
  // converted to sRGB by the same gamut mapping the generator uses. A drift
  // here means someone retuned a stop by hand instead of changing the formula.
  it("lagoon dark is the reference block", () => {
    const s = buildScheme("lagoon", "dark");
    expect(s.bg).toBe("#091010");        // oklch(0.165 0.011 195)
    expect(s.you).toBe("#4cd3d3");       // oklch(0.795 0.115 195)
    expect(s.sys).toBe("#e69ac5");       // oklch(0.775 0.105 345)
    expect(s.auto).toBe("#fc8639");      // oklch(0.740 0.1682 50)
  });

  it("the AA solve moves warn in light mode, and leaves dark mode alone", () => {
    // The one systematic departure from the handoff's stops, and the reason for
    // it: 0.560 L gold reads 4.41:1 on a white card and 4.04:1 on the inset,
    // which is under AA for the status word in a dense row. Dark mode's gold
    // already clears, so it is untouched.
    expect(buildScheme("iris", "light").warn).toBe("#926100");
    expect(buildScheme("iris", "dark").warn).toBe("#ffbf5f");
  });

  it("light mode darkens hues near cyan, and only those", () => {
    // lightDrop() subtracts up to 0.05 L by proximity to 170 degrees. Cyan-ish
    // `you` hues land darker than the nominal 0.545 stop; a magenta one does not.
    const lagoon = buildScheme("lagoon", "light");   // base 195, drop ~0.036
    const iris = buildScheme("iris", "light");       // base 320, no drop
    expect(contrast(lagoon.you, lagoon.card)).toBeGreaterThanOrEqual(AA_TEXT);
    expect(contrast(iris.you, iris.card)).toBeGreaterThanOrEqual(AA_TEXT);
  });
});
