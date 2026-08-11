/* =============================================================================
   Workshop Design System — COLOR MODEL (canonical)
   -----------------------------------------------------------------------------
   This module is the single source of truth for every color in the system.
   `packages/ui/scripts/gen-tokens.mjs` reads it and emits BOTH:
     • packages/ui/src/styles/palettes.css      (the CSS custom properties)
     • packages/ui/src/styles/tokens.generated.js (the same values for Expo/RN)
   Nothing else may define a color. Edit here, then `npm run gen:tokens`.

   ---------------------------------------------------------------------------
   THE MODEL (OKLCh tonal ramp, Material-3 shaped)
   ---------------------------------------------------------------------------
   Material 3 builds a scheme by taking a few *key hues* and reading fixed
   *tones* (lightness steps) off each one; the neutral palette is the same hue
   at near-zero chroma. We do exactly that, but in OKLCh rather than HCT —
   OKLCh is perceptually uniform, ships in every browser, and is what Tailwind
   v4 and Radix moved to, so equal steps of L look like equal steps of light.

   A scheme is therefore (ramp × palette × mode):

     ramp     fixed tones. Lifted from the hand-tuned violet theme this system
              started as, so "the current look" is now just one instance of the
              model rather than a special case.
     palette  five seeds. Each supplies a brand hue, a neutral hue and two
              chroma scales. That is the ONLY thing that varies between schemes.
     mode     light | dark. Separate ramps — dark is not an inversion.

   Rules the model encodes:
     • Neutrals are the neutral hue at very low chroma, so surfaces are tinted,
       never dead gray, and the tint agrees with the accent.
     • Status hues are FIXED across all five palettes (ok 159° / warn 75° /
       danger 14°). Status is meaning; meaning must not move when a personal
       color scheme changes. Only `running` (the accent) and `stopped` (neutral)
       follow the palette — by design, since those two ARE brand/quiet.
     • `accent-fg` is chosen per scheme as whichever of near-white / near-black
       contrasts more with the accent (M3's on-primary rule), then asserted at
       WCAG AA in palette.test.ts. That is what lets a light-heavy hue like teal
       carry an accent at all.
     • Chroma is gamut-mapped by reduction at constant L and H — same intent as
       CSS's own oklch() gamut mapping, but done here so the emitted CSS and the
       emitted RN hexes are identical on every engine.
   ========================================================================== */

/* ---- sRGB <-> OKLab/OKLCh (Björn Ottosson) + WCAG contrast ---------------- */

const srgbToLin = (c) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
const linToSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055);

/** @param {string} hex @returns {[number,number,number]} 0..1 sRGB */
export function hexToRgb(hex) {
  const h = hex.replace("#", "");
  const s = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return /** @type {[number,number,number]} */ ([0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16) / 255));
}

function rgbToHex([r, g, b]) {
  const to = (v) => Math.round(Math.min(1, Math.max(0, v)) * 255).toString(16).padStart(2, "0");
  return "#" + to(r) + to(g) + to(b);
}

function oklchToRgb(L, C, H) {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  return [
    linToSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    linToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    linToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

const inGamut = (rgb) => rgb.every((v) => v >= -0.0001 && v <= 1.0001);

/**
 * OKLCh -> sRGB hex, reducing chroma at constant L/H until the color fits.
 * @param {number} L 0..1 @param {number} C @param {number} H degrees
 */
export function oklch(L, C, H) {
  if (inGamut(oklchToRgb(L, C, H))) return rgbToHex(oklchToRgb(L, C, H));
  let lo = 0, hi = C;
  for (let i = 0; i < 28; i++) {
    const mid = (lo + hi) / 2;
    if (inGamut(oklchToRgb(L, mid, H))) lo = mid; else hi = mid;
  }
  return rgbToHex(oklchToRgb(L, lo, H));
}

/** WCAG 2.1 relative luminance. @param {string} hex */
export function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map(srgbToLin);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio, 1..21. @param {string} a @param {string} b */
export function contrast(a, b) {
  const la = luminance(a), lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/**
 * Walk lightness until a tone clears `target` contrast against every surface it
 * has to sit on, then stop. This is the one place the model is not a fixed tone:
 * how dark an accent must be to be readable depends on its hue (a teal at L .57
 * is far lighter to the eye than a violet at L .57), so the ramp gives the tone
 * we *want* and this gives the tone we can *have*. Radix and M3 both do this;
 * without it, half a palette set quietly fails AA.
 * @param {number} startL @param {number} C @param {number} H
 * @param {string[]} against surfaces the tone must be readable on
 * @param {number} target contrast ratio
 * @param {number} dir -1 to darken (light mode), +1 to lighten (dark mode)
 */
export function solveTone(startL, C, H, against, target, dir) {
  let L = startL;
  for (let i = 0; i < 200; i++) {
    const hex = oklch(L, C, H);
    if (against.every((s) => contrast(hex, s) >= target)) return L;
    L += dir * 0.002;
    if (L <= 0.02 || L >= 0.99) break;
  }
  return L;
}

/** `rgba(r, g, b, alpha)` for a hex — used for tints and the focus ring. */
export function alpha(hex, a) {
  const [r, g, b] = hexToRgb(hex).map((v) => Math.round(v * 255));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/* ---- THE RAMP — fixed tones, one set per mode ----------------------------- */
/* [L, C] pairs. C is a BASE chroma, multiplied by the palette's scale and
   applied at the palette's hue. Read these as M3 tones: light surfaces live in
   0.93–1.00, dark surfaces in 0.17–0.34, text lands where contrast demands. */

const RAMP = {
  light: {
    // neutrals — [lightness, base chroma]
    card:         [1.000, 0.000],
    cardHover:    [0.989, 0.005],
    cardRing:     null,            // light cards float on shadow, not a ring
    bg:           [0.972, 0.007],
    fieldBg:      [0.954, 0.009],
    bgInset:      [0.945, 0.009],
    border:       [0.931, 0.012],
    borderStrong: [0.880, 0.018],
    faint:        [0.690, 0.026],
    muted:        [0.525, 0.027],
    fg:           [0.219, 0.019],
    shadow:       [0.220, 0.030],
    scrim:        [0.180, 0.030],
    scrimAlpha:   0.55,
    // accent family — [lightness, base chroma] at the brand hue. Chroma was
    // taken down from the original hand-tuned violet (0.199) to 0.175 — the
    // original read a bit hot against the muted rest of the UI, most visibly
    // in the chat bubble, which fills with --accent at full strength (see
    // ADR 0012 addendum). `p.brandChroma` scales this further per palette.
    accent:       [0.566, 0.175],
    accentHover:  [0.508, 0.175],
    accentActive: [0.456, 0.155],
    accentSoft:   [0.946, 0.025],
    // running = the accent, one step down so it reads as text
    running:      [0.488, 0.171],
    // stopped = neutral, never branded loud
    stopped:      [0.466, 0.026],
    stoppedSolid: [0.673, 0.026],
    stoppedSoft:  [0.948, 0.007],
    shadows: {
      sm:   (s) => `0 1px 2px ${alpha(s, 0.05)}`,
      card: (s) => `0 1px 2px ${alpha(s, 0.04)}, 0 4px 16px ${alpha(s, 0.05)}`,
      md:   (s) => `0 8px 28px ${alpha(s, 0.1)}`,
      lg:   (s) => `0 24px 60px ${alpha(s, 0.16)}`,
    },
    focusAlpha: 0.5,
    softAlpha: null, // light soft tints are opaque tones, not alpha
  },
  dark: {
    bg:           [0.170, 0.019],
    fieldBg:      [0.193, 0.020],
    bgInset:      [0.202, 0.020],
    card:         [0.216, 0.023],
    cardHover:    [0.247, 0.026],
    border:       [0.272, 0.028],
    cardRing:     [0.277, 0.029],
    borderStrong: [0.337, 0.038],
    faint:        [0.513, 0.035],
    muted:        [0.688, 0.029],
    fg:           [0.943, 0.011],
    shadow:       null,            // dark elevation is a ring + true black shadow
    scrim:        [0.120, 0.030],
    scrimAlpha:   0.62,
    accent:       [0.689, 0.140],
    accentHover:  [0.761, 0.108],
    accentActive: [0.827, 0.078],
    accentSoft:   null,            // dark soft tints are alpha over the surface
    running:      [0.761, 0.108],
    stopped:      [0.673, 0.026],
    stoppedSolid: [0.545, 0.028],
    stoppedSoft:  null,
    shadows: {
      sm:   () => "0 1px 2px rgba(0, 0, 0, 0.4)",
      card: () => "none",
      md:   () => "0 8px 28px rgba(0, 0, 0, 0.5)",
      lg:   () => "0 24px 60px rgba(0, 0, 0, 0.62)",
    },
    focusAlpha: 0.55,
    softAlpha: 0.16,
  },
};

/* ---- STATUS — its own tonal table, identical in all five palettes ---------
   Three roles per kind: `text` (on a surface), `solid` (the dot / fill) and
   `soft` (the chip tint; dark mode derives it as alpha over the surface
   instead). Each is an explicit [L, C, H] tone. They are pinned rather than
   derived because status must be learnable: green-159 / gold-75 / clay-14 stay
   put no matter which scheme is on, they differ in LIGHTNESS as well as hue so
   they survive color-vision deficiency, and they are always paired with an icon
   or label in the UI. `running` and `stopped` are absent here on purpose — they
   come from the palette's accent and neutral. */

const STATUS = {
  light: {
    ok:     { text: [0.516, 0.107, 159.2], solid: [0.627, 0.1255, 159.6], soft: [0.938, 0.0298, 161.1] },
    warn:   { text: [0.516, 0.1035, 75.3], solid: [0.711, 0.1427, 73.8],  soft: [0.944, 0.0399, 89.7] },
    danger: { text: [0.518, 0.1369, 14.6], solid: [0.613, 0.1440, 13.6],  soft: [0.930, 0.0222, 7.2],
              hover: [0.551, 0.1369, 14.1] },
  },
  dark: {
    ok:     { text: [0.784, 0.1291, 160.7], solid: [0.725, 0.1362, 160.5], soft: null },
    warn:   { text: [0.792, 0.1334, 81.8],  solid: [0.711, 0.1427, 73.8],  soft: null },
    danger: { text: [0.735, 0.1197, 8.5],   solid: [0.651, 0.1460, 12.9],  soft: null,
              hover: [0.766, 0.1035, 8.3] },
  },
};

/* ---- THE FIVE PALETTES ---------------------------------------------------- */
/* Four cool seeds spaced across the arc that cannot be mistaken for a status
   hue (197 · 258 · 290 · 334), plus one warm-neutral scheme for temperature
   contrast. `neutralHue` is normally the brand hue (analogous tint, M3's
   neutral-from-primary); Sandstone deliberately splits it — warm surfaces,
   cool accent, the one complementary scheme in the set. */

export const PALETTES = [
  {
    id: "violet",
    label: "Violet",
    blurb: "The Workshop default. Refined violet on inky violet-navy.",
    brandHue: 290.4,
    neutralHue: 287.5,
    brandChroma: 1.0,
    neutralChroma: 1.0,
  },
  {
    id: "cobalt",
    label: "Cobalt",
    blurb: "A calm, conventional blue on cool slate surfaces.",
    brandHue: 258.0,
    neutralHue: 262.0,
    // OKLCh is not perceptually uniform across hue: a blue in the 250-265°
    // range reads distinctly more vivid than a violet at the SAME chroma (this
    // is the one hue band the model's own contrast solver can't fix, because
    // it optimizes for legibility, not for "does this look calm"). Pulled back
    // hardest of the five for that reason — confirmed against the measured
    // resolved chroma, which was landing almost fully unclipped (~0.199, i.e.
    // no gamut relief at all) while violet at the same request looks calmer.
    brandChroma: 0.74,
    neutralChroma: 0.95,
  },
  {
    id: "teal",
    label: "Teal",
    blurb: "Blue-green accent over marine neutrals. The quietest scheme.",
    brandHue: 197.0,
    neutralHue: 213.0,
    // No pull-back needed: sRGB's gamut is naturally narrow around 197°, so a
    // full-chroma request here already gets gamut-clipped to roughly half the
    // asked-for chroma — teal was the one hue that came out calm on its own.
    brandChroma: 1.0,
    neutralChroma: 0.9,
  },
  {
    id: "plum",
    label: "Plum",
    blurb: "Warm magenta accent on plum-tinted neutrals.",
    brandHue: 334.0,
    neutralHue: 328.0,
    // Magenta has the same non-uniformity as blue, milder: pulled back less.
    brandChroma: 0.86,
    neutralChroma: 0.8,
  },
  {
    id: "sandstone",
    label: "Sandstone",
    blurb: "Warm paper and charcoal with a cool indigo accent.",
    brandHue: 268.0,
    neutralHue: 72.0,
    // Same blue-family hue band as Cobalt (268° vs 258°) — same treatment.
    brandChroma: 0.78,
    neutralChroma: 0.9,
  },
];

export const DEFAULT_PALETTE = "violet";
export const MODES = /** @type {const} */ (["light", "dark"]);

/** @param {string} id */
export function paletteById(id) {
  return PALETTES.find((p) => p.id === id) || PALETTES[0];
}

/**
 * The surfaces a color has to be readable on, for one palette. `bgInset` is in
 * here and it matters: in light mode it is the DARKEST surface, so it — not the
 * page background — is what a dark accent has to clear.
 * @param {PaletteSeed} p @param {"light"|"dark"} mode
 */
function surfacesOf(p, mode) {
  const r = RAMP[mode];
  const N = ([L, C]) => oklch(L, C * p.neutralChroma, p.neutralHue);
  return [N(r.bg), N(r.card), N(r.bgInset)];
}

/**
 * Every surface in the whole system for a mode. Status tones are solved against
 * this, not against one palette's surfaces: ok/warn/danger must come out to the
 * SAME hex in all five schemes (meaning does not move), which is only true if
 * they are solved once against the worst case any scheme presents.
 * @param {"light"|"dark"} mode
 */
const allSurfaces = (() => {
  /** @type {Record<string, string[]>} */
  const cache = {};
  return (mode) => (cache[mode] ||= PALETTES.flatMap((p) => surfacesOf(p, mode)));
})();

/* ---- SCHEME CONSTRUCTION -------------------------------------------------- */

/**
 * Build every semantic color for one palette in one mode.
 * @param {string} paletteId
 * @param {"light"|"dark"} mode
 * @returns {Record<string, string>} camelCase token -> css color string
 */
export function buildScheme(paletteId, mode) {
  const p = paletteById(paletteId);
  let r = RAMP[mode];

  const N = ([L, C]) => oklch(L, C * p.neutralChroma, p.neutralHue);
  const A = ([L, C]) => oklch(L, C * p.brandChroma, p.brandHue);
  const T = ([L, C, H]) => oklch(L, C, H);

  // The accent has to be readable AS TEXT on both surfaces it lands on (links,
  // ghost-button labels, the `running` word). Solve its tone against the worse
  // of bg/card, then carry the same offset to hover/active so the interaction
  // ladder keeps its shape whatever the hue demanded.
  const surfaces = surfacesOf(p, mode);
  const dir = mode === "light" ? -1 : +1;
  const accentL = solveTone(r.accent[0], r.accent[1] * p.brandChroma, p.brandHue, surfaces, 4.5, dir);
  const shift = accentL - r.accent[0];
  r = { ...r, accent: [accentL, r.accent[1]],
        accentHover: [r.accentHover[0] + shift, r.accentHover[1]],
        accentActive: [r.accentActive[0] + shift, r.accentActive[1]],
        running: [r.running[0] + shift, r.running[1]] };

  const accent = A(r.accent);
  const accentSoft = r.accentSoft ? A(r.accentSoft) : alpha(accent, r.softAlpha);

  // on-accent: whichever end of the ramp contrasts more (M3's on-primary rule).
  const nearWhite = "#ffffff";
  const nearBlack = oklch(0.14, 0.04 * p.brandChroma, p.brandHue);
  const accentFg = contrast(accent, nearWhite) >= contrast(accent, nearBlack) ? nearWhite : nearBlack;

  // A `solid` is a mark, not text: dots, fills, the pip on a pill. WCAG 1.4.11
  // wants 3:1 for a meaningful non-text object, and the hand-tuned gold missed
  // it on white (2.63:1) — the one place the old theme was actually wrong.
  // Solve it the same way as the accent instead of eyeballing a new gold.
  // Status solves against EVERY palette's surfaces so the answer is one color;
  // the neutral `stopped` solves against its own, since it is palette-tinted.
  const solidTone = (against) => ([L, C, H]) => oklch(solveTone(L, C, H, against, 3.0, dir), C, H);
  const statusSolid = solidTone(allSurfaces(mode));
  const neutralSolid = solidTone(surfaces);

  /** the three roles of one status kind, from the pinned tone table */
  const status = (kind) => {
    const t = STATUS[mode][kind];
    const solid = statusSolid(t.solid);
    return {
      text: T(t.text),
      solid,
      soft: t.soft ? T(t.soft) : alpha(solid, r.softAlpha),
      hover: t.hover ? T(t.hover) : solid,
    };
  };

  const ok = status("ok");
  const warn = status("warn");
  const danger = status("danger");

  const scrim = alpha(N(r.scrim), r.scrimAlpha);
  const shadowSeed = r.shadow ? N(r.shadow) : "#000000";

  return {
    bg: N(r.bg),
    bgInset: N(r.bgInset),
    fg: N(r.fg),
    muted: N(r.muted),
    faint: N(r.faint),
    card: N(r.card),
    cardHover: N(r.cardHover),
    cardRing: r.cardRing ? N(r.cardRing) : "transparent",
    border: N(r.border),
    borderStrong: N(r.borderStrong),
    fieldBg: N(r.fieldBg),

    accent,
    accentHover: A(r.accentHover),
    accentActive: A(r.accentActive),
    accentFg,
    accentSoft,

    ok: ok.text,
    okSolid: ok.solid,
    okSoft: ok.soft,

    running: A(r.running),
    runningSolid: accent,
    runningSoft: r.accentSoft ? A(r.accentSoft) : alpha(accent, 0.18),

    stopped: N(r.stopped),
    stoppedSolid: neutralSolid([r.stoppedSolid[0], r.stoppedSolid[1] * p.neutralChroma, p.neutralHue]),
    stoppedSoft: r.stoppedSoft ? N(r.stoppedSoft) : alpha(N(r.stoppedSolid), 0.15),

    warn: warn.text,
    warnSolid: warn.solid,
    warnSoft: warn.soft,

    danger: danger.text,
    dangerSolid: danger.solid,
    dangerSoft: danger.soft,
    dangerHover: danger.hover,

    scrim,
    focusRing: alpha(accent, r.focusAlpha),
    shadowSm: r.shadows.sm(shadowSeed),
    shadowCard: r.shadows.card(shadowSeed),
    shadowMd: r.shadows.md(shadowSeed),
    shadowLg: r.shadows.lg(shadowSeed),
  };
}

/**
 * A single, mode-independent "identity" swatch for a palette — used ONLY by
 * the Settings picker, never by app UI. The picker's live preview renders the
 * scheme's real (very low-chroma) neutrals, which is correct but means a warm
 * scheme like Sandstone and a cool one like Cobalt can look nearly identical
 * in a 76x50px chip: near-black and near-white don't show hue well at any
 * chroma, and the one bright element in the preview (the accent bar) is
 * deliberately a COOL brand hue even in a warm scheme, so it doesn't carry the
 * scheme's identity either. This is a moderate, always-visible tone at the
 * palette's neutral hue, so "warm vs cool" reads at a glance regardless of
 * which mode is being previewed.
 * @param {string} paletteId
 */
export function identitySwatch(paletteId) {
  const p = paletteById(paletteId);
  return oklch(0.66, 0.1 * p.neutralChroma, p.neutralHue);
}

/** Every scheme, keyed `paletteId.mode`. */
export function allSchemes() {
  /** @type {Record<string, Record<string,string>>} */
  const out = {};
  for (const p of PALETTES) for (const m of MODES) out[`${p.id}.${m}`] = buildScheme(p.id, m);
  return out;
}

/* Categorical suite — DIFFERENTIATION, not status and not brand. Deliberately
   the same in every palette: a tag color that moved when you changed scheme
   would stop being a stable identity for an agent or a source. Cool tones only,
   so none of them can be read as ok/warn/danger. */
export const CATEGORICAL = {
  violet: "#7a57e0",
  turquoise: "#10b4bf",
  blue: "#3f7df0",
  indigo: "#5160ee",
  orchid: "#b35fd6",
  slate: "#7c8aa0",
};
