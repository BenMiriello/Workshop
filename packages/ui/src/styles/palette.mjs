/* =============================================================================
   Workshop Design System — COLOR MODEL (canonical)
   -----------------------------------------------------------------------------
   This module is the single source of truth for every color in the system.
   `packages/ui/scripts/gen-tokens.mjs` reads it and emits BOTH:
     • packages/ui/src/styles/palettes.css      (the CSS custom properties)
     • packages/ui/src/styles/tokens.generated.js (the same values for Expo/RN)
   Nothing else may define a color. Edit here, then `npm run gen:tokens`.

   ---------------------------------------------------------------------------
   THE MODEL — AGENCY (2026-08-11)
   ---------------------------------------------------------------------------
   The palette used to answer one question: *is it healthy?* (the status five).
   It now answers a second, which for a system that acts on its own is the more
   important one: *who did this, me or something moving without me?*

   That axis is AGENCY, and it has three tiers:

     you          your hand on it: typed, clicked, confirmed
     system       machine work inside a conversation you are present for
     autonomous   it moved with nobody watching (cron, sweeps, watchdogs)

   Each tier has a RESTING and an ACTIVE (`-up`) intensity. Active means
   happening right now. It is never read/unread and never severity, and it is
   the only tone allowed to animate.

   Two rules hold everywhere, and they are what keep the two vocabularies apart:

     1. AGENCY IS FILLED, STATUS IS OUTLINE. An agency chip has a soft fill and
        no border; a status chip has a transparent background and a hairline.
        Shape and fill carry the separation, so a hue coincidence between an
        agency tone and a status tone is survivable. This is why the status hues
        no longer need to dodge anything (see below), and it is why a status
        element must never take a soft fill.
     2. ONE AGENCY COLOR PER SURFACE. A card is never both system and
        autonomous. If an autonomous agent calls a tool, the card stays
        autonomous: provenance beats mechanism.

   A scheme is (scheme x mode):

     scheme   eight of them, in two groups. EXPRESSIVE (5) spends three hues:
              `you` takes the base and the machine pair sits together on the far
              side of the wheel, at least 60 degrees away, adjacent enough to
              read as one family. QUIET (3) spends one hue and separates the
              tiers by CHROMA RATIO instead, for far less color in the room.
     mode     light | dark. Separate stops — dark is not an inversion.

   Everything below is derived from the designer's stops in `palette()`, which
   is a verbatim port of the generator in the handoff prototype (Agency Palette
   v7). Those numbers were iterated over many rounds and contrast-checked; do
   not hand-tune one. If a tone has to change, change the formula and let all
   sixteen schemes move together.

   Two non-obvious rules baked into `palette()` and worth preserving:

     • Surfaces take the `you` hue at ~10% of accent chroma, trimmed per scheme
       by `bgC`. Warm and green bases tint neutrals far more visibly than blue
       or violet ones at equal chroma, so those schemes trim harder (Fern 0.45,
       Clay 0.55, Graphite 0.25).
     • Light mode darkens hues near cyan/green. Hues around 170 degrees are
       intrinsically lighter at a given OKLCh L, so a fixed lightness stop that
       clears 4.5:1 for vermilion will not clear it for cyan. `lightDrop()`
       subtracts up to 0.05 L by proximity to 170.

   `palette()` supplies the 24 tokens the design defines. The UI needs about
   twenty more (hover ladders, field backgrounds, rings, scrims, shadows) and
   those are DERIVED here, in `engineering()`, so they can never drift from the
   designer's stops. Status `-solid` marks are solved to 3:1 rather than
   eyeballed; text tones are asserted at 4.5:1 in palette.test.ts.
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
 * The emitted CSS is hex rather than `oklch()` on purpose: the RN tokens and
 * the web tokens then carry byte-identical values, and the contrast assertions
 * in palette.test.ts measure the color that is actually painted rather than the
 * one that was asked for.
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
 * has to sit on, then stop. How dark a mark must be to be legible depends on
 * its hue (a teal at L .57 is far lighter to the eye than a violet at L .57),
 * so a fixed stop gives the tone we WANT and this gives the tone we can HAVE.
 * Used only for `-solid` marks; the designer's text stops are already solved.
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

/** `rgba(r, g, b, alpha)` for a hex — used for scrims and the focus ring. */
export function alpha(hex, a) {
  const [r, g, b] = hexToRgb(hex).map((v) => Math.round(v * 255));
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

/* ---- THE EIGHT SCHEMES ----------------------------------------------------
   Ported from the handoff prototype's SCHEMES array. Each scheme is three hues
   plus per-scheme trims the hue math cannot know:

     base/sys/auto   the three agency hues, in OKLCh degrees
     yc/sc/ac        chroma multipliers per tier
     yl/sl/al        lightness offsets per tier (DARK MODE ONLY — see palette())
     bgC             surface-chroma multiplier
     quiet           member of the one-hue set

   Bases are spaced around the wheel FIRST: two schemes with near-identical
   bases force their machine pairs into the same opposite region, which is what
   produced duplicate-looking schemes. Autonomous is the more advancing sibling
   of its pair and always carries more chroma, because it is the escalation
   tier. Flanks skip the 95-135 muddy yellow-green zone, which reads sickly at
   UI lightness. */

export const SCHEMES = [
  /* Expressive — three hues. Machine pairs are allocated so no two schemes
     share a tier, and every pair clears the pinned warn (75) / danger (25)
     hues, except Lagoon, whose orange escalation is far enough in RGB terms to
     read distinctly. Cobalt's blue base points straight at amber, i.e. at warn,
     so its pair goes to the cool side instead. */
  { id: "lagoon", label: "Lagoon", blurb: "Cyan base, pink system, orange escalation.",
    base: 195, sys: 345, auto: 50, ac: 1.16, al: -0.02 },
  { id: "iris", label: "Iris", blurb: "Orchid base, blue system, indigo escalation.",
    base: 320, sys: 220, auto: 264, yc: 0.88, sc: 0.96, ac: 1.12, al: -0.01, bgC: 0.75 },
  { id: "cobalt", label: "Cobalt", blurb: "Blue base, teal system, azure escalation.",
    base: 258, sys: 180, auto: 226, yc: 0.80, sc: 0.96, ac: 1.12, al: -0.01 },
  { id: "fern", label: "Fern", blurb: "Green base, violet system, rose escalation.",
    base: 160, sys: 296, auto: 325, yc: 0.80, yl: 0.01, ac: 1.06, bgC: 0.45 },
  { id: "clay", label: "Clay", blurb: "Terracotta base, blue system, electric-cyan escalation.",
    base: 40, sys: 252, auto: 196, yc: 0.86, yl: 0.02, ac: 1.10, bgC: 0.55 },

  /* Quiet — one hue, no opposed pair. The tiers separate by CHROMA RATIO: `you`
     keeps real chroma, `system` drops to a tint and is offset lighter so it
     cannot be mistaken for body text, `autonomous` drifts and stays modest.
     Same three roles, same fill-vs-outline vocabulary, far less color. */
  { id: "slate", label: "Slate", blurb: "Cool blue-gray. One hue, tiers by chroma.",
    base: 248, sys: 248, auto: 205, yc: 0.58, sc: 0.26, sl: 0.07, ac: 0.78, bgC: 0.75, quiet: true },
  { id: "linen", label: "Linen", blurb: "Warm sand. One hue, tiers by chroma.",
    base: 58, sys: 58, auto: 52, yc: 0.54, sc: 0.25, sl: 0.07, ac: 1.00, bgC: 0.35, quiet: true },
  { id: "graphite", label: "Graphite", blurb: "Near-achromatic with a violet cast.",
    base: 268, sys: 268, auto: 312, yc: 0.58, sc: 0.25, sl: 0.07, ac: 0.78, bgC: 0.25, quiet: true },
];

/* Status hues are PINNED: identical in every scheme and both modes. Semantics
   must not shift when someone changes theme, and they no longer need to dodge
   the agency hues because fill-vs-outline already separates the vocabularies. */
const OK_H = 148, WARN_H = 75, DANGER_H = 25;

/* ---- THE CODE LAYER: `--syn-*` -------------------------------------------
   Nine roles, not the thirty a TextMate grammar offers, decided in
   docs/platform-redesign/file-viewer-and-editor-2026-08-22.md section 5.2. Two
   of them are not a hue at all and one of them is not a new color:

     comment / punctuation   MUTED. They are the parts of a file you read past,
                             so they take a neutral at the base hue and the
                             surface chroma, exactly like --muted and --faint.
     invalid                 THE EXISTING DANGER TONE, emitted as an alias. A
                             tenth hue for "this is broken" would be a second
                             vocabulary for something status already says.

   The other six DERIVE FROM THE SCHEME rather than importing somebody else's
   theme, in two ways:

     • The ring is ANCHORED AT THE SCHEME'S BASE HUE. `keyword` sits exactly on
       it, because keywords are the most frequent colored token in a file and
       that is what decides whether the file looks like it belongs in the
       palette it is being read in. Everything else is an offset from there, so
       the whole ring rotates with the scheme: the same file is cyan-led in
       Lagoon and terracotta-led in Clay.
     • The CHROMA is the scheme's own `you` trim, so a quiet scheme reads code
       as quietly as it reads everything else.

   The offsets are FIXED (roughly even, 55-65 degrees apart) and that is
   deliberate: six roles have to stay separable in all sixteen scheme x modes,
   and a file whose strings and numbers look alike is not legible in any of
   them. Anchoring carries the identity; spacing carries the legibility.

   Lightness and chroma are one stop per mode, at the `you` tier's own stops,
   then walked to 4.5:1 by the same solver every other text tone uses. Equal L
   across the ring is what makes a theme read as one thing (it is why Solarized
   works), so the tones are levelled first and made legible second. */
const SYN_HUES = { keyword: 0, type: 55, function: 115, string: 180, number: 240, property: 300 };

/* Six roles need more separation than three agency tiers do. A quiet scheme
   trims `you` chroma to ~0.55, and at 0.115 x 0.55 the six hues stop being six
   and become one gray with opinions. The trim still applies, floored. */
const SYN_C_FLOOR = 0.75;

/* The two muted roles, as [L, chroma] at the base hue, per mode. `comment` is
   dimmer than `punctuation` because a comment is skippable and a brace is not;
   both sit below --fg, and both are solved to AA rather than left at the stop,
   since they are still text somebody has to read. */
const SYN_NEUTRAL = {
  dark: { comment: [0.615, 0.020], punctuation: [0.735, 0.018] },
  light: { comment: [0.545, 0.018], punctuation: [0.475, 0.016] },
};

export const DEFAULT_PALETTE = "iris";
export const MODES = /** @type {const} */ (["light", "dark"]);

/* Ids that existed before the agency model, mapped to their nearest scheme, so
   a stored preference survives the change instead of silently snapping to the
   default. Duplicated in the index.html bootstrap, which runs before modules. */
export const LEGACY_PALETTES = {
  violet: "iris",
  plum: "iris",
  teal: "lagoon",
  sandstone: "linen",
  /* cobalt kept its id and its 258-degree base. */
};

/** @param {string} id */
export function paletteById(id) {
  return SCHEMES.find((x) => x.id === id) || SCHEMES.find((x) => x.id === DEFAULT_PALETTE);
}

/* ---- THE DESIGNER'S STOPS -------------------------------------------------
   Verbatim port of `palette()` from the handoff prototype. Twenty-four tones
   per scheme per mode. GENERATED OUTPUT DEPENDS ON EVERY NUMBER HERE — the
   handoff's tokens.css is exactly this function's output, and palette.test.ts
   re-checks the contrast pairs it promises. */

/** Hues near 170 are intrinsically lighter at a given L; darken by proximity. */
function lightDrop(h) {
  const d = Math.min(Math.abs((((h - 170) % 360) + 540) % 360 - 180), 180);
  return 0.05 * Math.max(0, 1 - d / 90);
}

/**
 * The 24 design tones for one scheme in one mode, as [L, C, H] triples.
 * @param {typeof SCHEMES[number]} s @param {"light"|"dark"} mode
 */
function palette(s, mode) {
  const H = s.base, S = s.sys, A = s.auto;
  const yc = s.yc || 1, sc = s.sc || 1, ac = s.ac || 1;
  const yl = s.yl || 0, sl = s.sl || 0, al = s.al || 0;
  const bc = s.bgC || 1;
  const o = (l, c, h) => [l, c, h];
  if (mode === "dark") {
    return {
      bg: o(0.165, 0.011 * bc, H), inset: o(0.205, 0.013 * bc, H), card: o(0.235, 0.014 * bc, H),
      border: o(0.315, 0.016 * bc, H), faint: o(0.635, 0.018 * bc, H), muted: o(0.755, 0.018 * bc, H),
      fg: o(0.945, 0.01 * bc, H),
      you: o(0.795 + yl, 0.115 * yc, H), youFg: o(0.2, 0.035 * yc, H), youSoft: o(0.3, 0.042 * yc, H),
      sys: o(0.775 + sl, 0.105 * sc, S), sysSoft: o(0.295, 0.04 * sc, S), sysUp: o(0.855 + sl, 0.13 * sc, S),
      auto: o(0.76 + al, 0.145 * ac, A), autoSoft: o(0.315, 0.055 * ac, A), autoUp: o(0.83 + al, 0.17 * ac, A),
      ok: o(0.775, 0.095, OK_H), okSoft: o(0.295, 0.034, OK_H),
      warn: o(0.845, 0.135, WARN_H), warnSoft: o(0.3, 0.036, WARN_H),
      danger: o(0.755, 0.12, DANGER_H), dangerSoft: o(0.3, 0.042, DANGER_H),
      stopped: o(0.7, 0.012 * bc, H), stoppedSoft: o(0.29, 0.01 * bc, H),
    };
  }
  /* The light branch ignores the *l trims (they tune the dark swatches, and
     applying them here lightens foregrounds against already-pale tints) and
     holds fixed stops that clear 4.5:1 on the soft fills at 10px. */
  return {
    bg: o(0.975, 0.005, H), inset: o(0.945, 0.007, H), card: o(0.995, 0.003, H),
    border: o(0.9, 0.009, H), faint: o(0.52, 0.016, H), muted: o(0.455, 0.016, H), fg: o(0.235, 0.014, H),
    you: o(0.545 - lightDrop(H), 0.145 * yc, H), youFg: o(0.995, 0.004, H), youSoft: o(0.945, 0.038 * yc, H),
    sys: o(0.505 - lightDrop(S), 0.135 * sc, S), sysSoft: o(0.955, 0.03 * sc, S), sysUp: o(0.43 - lightDrop(S), 0.15 * sc, S),
    auto: o(0.495 - lightDrop(A), 0.17 * ac, A), autoSoft: o(0.955, 0.04 * ac, A), autoUp: o(0.425 - lightDrop(A), 0.185 * ac, A),
    ok: o(0.51, 0.115, OK_H), okSoft: o(0.95, 0.032, OK_H),
    warn: o(0.56, 0.14, WARN_H), warnSoft: o(0.955, 0.034, WARN_H),
    danger: o(0.525, 0.155, DANGER_H), dangerSoft: o(0.95, 0.038, DANGER_H),
    stopped: o(0.52, 0.01, H), stoppedSoft: o(0.935, 0.006, H),
  };
}

/* ---- DERIVED SURFACES AND LADDERS -----------------------------------------
   The design defines four elevation steps; the UI has controls, hovers, rings
   and scrims between them. These are offsets FROM the designer's stops rather
   than a second table, so retuning `palette()` carries them along. */

const DERIVED = {
  dark: {
    /* [L, chroma x the scheme's surface chroma] at the base hue. */
    fieldBg: [0.193, 0.0125],
    cardHover: [0.268, 0.015],
    cardRing: [0.29, 0.015],       // dark cards float on a faint ring, not a shadow
    /* A BLOCK OF GROUPED CONTENT INSIDE A CARD. A WHISPER: just enough to say
       "these rows belong together", and no more. 0.27 against a 0.235 card.
       See the light entry for why this stopped chasing `inset`. */
    bgBlock: [0.27, 0.015],
    /* A RECESSED PANEL holding a separate context (a delegation). The deep step,
       and the one that carries the distinction the block used to be asked to
       carry on its own. In dark it is essentially where `inset` already sat, so
       nothing visibly moves here; the work is all in light mode. */
    bgSunken: [0.195, 0.014],
    borderStrong: [0.4, 0.017],
    scrim: [0.12, 0.03],
    scrimAlpha: 0.62,
    /* Accent ladder: dark interaction moves TOWARD the light, shedding chroma. */
    hoverL: 0.05, activeL: 0.09, hoverC: 0.9, activeC: 0.8,
    dangerHoverL: 0.045,
    focusAlpha: 0.55,
    shadow: null,                  // true black under a dark scheme
    shadows: {
      sm: () => "0 1px 2px rgba(0, 0, 0, 0.4)",
      card: () => "none",
      md: () => "0 8px 28px rgba(0, 0, 0, 0.5)",
      lg: () => "0 24px 60px rgba(0, 0, 0, 0.62)",
    },
  },
  light: {
    fieldBg: [0.96, 0.006],
    cardHover: [0.985, 0.004],
    cardRing: null,                // light cards float on shadow
    /* BELOW `inset`, not between it and `card`, and that asymmetry is the whole
       point of the token.

       Elevation is not symmetric across the modes: `inset` is the LOWEST surface
       in both (dark 0.205 < card 0.235; light 0.945 < card 0.995), so a block
       drawn as "card lifted toward --fg" moves AWAY from inset in dark and
       straight ONTO it in light. That is exactly what shipped: measured across
       all sixteen scheme x modes, the grouped-tool-call fill and the delegation
       fill landed within 3/255 of each other in every light scheme while sitting
       26 apart in every dark one. Two different things, one color, in half the
       app. Operator, 2026-08-14: "it's the same background color as the agent
       background color and that's wrong."

       THE BLOCK STOPPED CHASING `inset`, because chasing it was the wrong move.
       Three passes drove this further and further from the card (0.90, then
       0.855) to get clear of the delegation's fill, and the operator's verdict on
       the result was that the block now stands out too much from the message it
       sits in: "it should be subtler of a difference compared to the regular
       chat message background."

       Both of his asks are right and they cannot both be satisfied by this one
       token, which is the thing the earlier passes missed. In light mode the
       card is 0.995 and `inset` is 0.945, so a block that stays subtle against
       the card lands ON the delegation, and a block clear of the delegation is
       loud against the card. The delegation was sitting between the two.

       So the block goes back to a whisper (0.965, one soft step under the card)
       and the DELEGATION moves out of the way instead: see `bgSunken`. That also
       matches what the two things are. A group is this turn's own steps gathered
       under a count, so it earns the softest mark there is; a delegation holds a
       whole separate context, so it earns the deep one, and it has a border on
       top of that. Fill against frame, loud against soft, both saying the same
       thing twice rather than one token doing two jobs. */
    bgBlock: [0.965, 0.006],
    /* THE DEEP STEP, and the one doing the work light mode needs. 0.90 against a
       0.995 card and a 0.965 block: unmistakably a different surface from either,
       with room left for `--muted` to clear AA on it. */
    bgSunken: [0.9, 0.009],
    borderStrong: [0.855, 0.011],
    scrim: [0.18, 0.03],
    scrimAlpha: 0.55,
    hoverL: -0.05, activeL: -0.09, hoverC: 1, activeC: 0.92,
    dangerHoverL: 0.045,
    focusAlpha: 0.5,
    shadow: [0.22, 0.03],
    shadows: {
      sm: (s) => `0 1px 2px ${alpha(s, 0.05)}`,
      card: (s) => `0 1px 2px ${alpha(s, 0.04)}, 0 4px 16px ${alpha(s, 0.05)}`,
      md: (s) => `0 8px 28px ${alpha(s, 0.1)}`,
      lg: (s) => `0 24px 60px ${alpha(s, 0.16)}`,
    },
  },
};

/** The surfaces any mark has to stay legible on, for one scheme. */
function surfacesOf(s, mode) {
  const P = palette(s, mode);
  const T = ([L, C, H]) => oklch(L, C, H);
  return [T(P.bg), T(P.card), T(P.inset)];
}

/** Every surface in the system for a mode — status marks solve against all of
    them at once, so ok/warn/danger come out identical in all eight schemes. */
const allSurfaces = (() => {
  const cache = {};
  return (mode) => (cache[mode] ||= SCHEMES.flatMap((s) => surfacesOf(s, mode)));
})();

/* ---- SCHEME CONSTRUCTION -------------------------------------------------- */

/**
 * Build every semantic color for one scheme in one mode.
 *
 * Token names keep the shapes the app already uses (bg / card / fg / accent /
 * status), so nothing had to be renamed to adopt the agency model, and add the
 * agency family on top. `accent*` IS the `you` tier: the brand accent and "your
 * hand on it" were always the same idea, and now they are the same token.
 *
 * @param {string} schemeId
 * @param {"light"|"dark"} mode
 * @returns {Record<string, string>} camelCase token -> css color string
 */
export function buildScheme(schemeId, mode) {
  const s = paletteById(schemeId);
  const P = palette(s, mode);
  const d = DERIVED[mode];
  const dir = mode === "light" ? -1 : +1;
  const bc = s.bgC || 1;

  const T = ([L, C, H]) => oklch(L, C, H);
  /** a derived neutral: [L, C] at the base hue, at the scheme's surface chroma */
  const N = ([L, C]) => oklch(L, C * bc, s.base);
  /** a step along the accent ladder, from a tone triple */
  const step = ([L, C, H], dL, cx) => oklch(Math.min(0.97, Math.max(0.06, L + dL)), C * cx, H);

  const surfaces = surfacesOf(s, mode);

  /* THE ONE PLACE THE DESIGNER'S STOP IS NOT THE FINAL ANSWER.
     -----------------------------------------------------------------------
     Workshop renders several of these tones as SMALL TEXT on a surface, which
     the prototype did not: a warn line's label, a status word in a dense table
     row, a link in the accent. The handoff's stops were checked on the
     backgrounds its own layout paints; on ours, a handful land in the 4.0-4.5
     band, which is under AA. So every colored TEXT tone is walked toward its
     mode's extreme until it clears 4.5:1 on all three surfaces AND on its own
     soft fill, and then stopped.

     This is a no-op for most tones (the stop already clears), so the designer's
     numbers are what ships almost everywhere. It moves `warn` in light mode
     (0.560 -> ~0.516, which is where the pre-agency theme's hand-tuned gold
     also landed) and darkens `you` a step in a few light schemes. Hue and
     chroma are never touched, so the scheme still reads as the designer drew
     it. If you retune a stop, this keeps it honest rather than fighting it. */
  const text = ([L, C, H], against) => [solveTone(L, C, H, against, 4.5, dir), C, H];
  /* A `solid` is a MARK, not text: dots, fills, the pip on a pill. WCAG 1.4.11
     wants 3:1 for a meaningful non-text object. Status solves against every
     scheme's surfaces so the answer is one color in all eight; the neutral
     `stopped` solves against its own, since it is scheme-tinted. */
  const solidTone = (against) => ([L, C, H]) => oklch(solveTone(L, C, H, against, 3.0, dir), C, H);
  const statusSolid = solidTone(allSurfaces(mode));
  const neutralSolid = solidTone(surfaces);

  /* Each tier's tone has to be readable on the surfaces AND on its own chip
     fill, which is what the tightest cases in the handoff are (10px rail
     labels, 10.5px chips). Status solves against every scheme's surfaces at
     once so ok/warn/danger stay byte-identical in all eight. */
  const onSurfaces = (soft) => [...surfaces, T(soft)];
  const onAllSurfaces = (soft) => [...allSurfaces(mode), T(soft)];

  const youT = text(P.you, onSurfaces(P.youSoft));
  const sysT = text(P.sys, onSurfaces(P.sysSoft));
  const sysUpT = text(P.sysUp, onSurfaces(P.sysSoft));
  const autoT = text(P.auto, onSurfaces(P.autoSoft));
  const autoUpT = text(P.autoUp, onSurfaces(P.autoSoft));
  const okT = text(P.ok, onAllSurfaces(P.okSoft));
  const warnT = text(P.warn, onAllSurfaces(P.warnSoft));
  const dangerT = text(P.danger, onAllSurfaces(P.dangerSoft));
  const stoppedT = text(P.stopped, onSurfaces(P.stoppedSoft));

  const you = T(youT);

  /* ---- the code layer, see SYN_HUES ------------------------------------
     Every syntax tone is solved against the three surfaces, like every other
     text tone: an editor paints on --bg-inset, and a token nobody can read is
     not highlighting. Hue and chroma are never touched by the solver, so the
     ring still reads as the scheme drew it. */
  const synC = Math.max(SYN_C_FLOOR, s.yc || 1);
  const synHue = (offset) => (((s.base + offset) % 360) + 360) % 360;
  const synTone = (offset) => {
    const h = synHue(offset);
    return T(text(mode === "dark" ? [0.795, 0.115 * synC, h] : [0.545 - lightDrop(h), 0.145 * synC, h], surfaces));
  };
  const synMuted = ([L, C]) => T(text([L, C * bc, s.base], surfaces));

  const scrim = alpha(N(d.scrim), d.scrimAlpha);
  const shadowSeed = d.shadow ? N(d.shadow) : "#000000";

  return {
    /* ---- surfaces + text: the designer's four elevation and three emphasis
       steps, plus the in-between surfaces the UI needs. */
    bg: T(P.bg),
    bgInset: T(P.inset),
    fg: T(P.fg),
    muted: T(P.muted),
    faint: T(P.faint),
    card: T(P.card),
    /* A grouped block sitting ON a card (or inside a delegation), distinct from
       BOTH by construction rather than by luck. It is not derived from `card`
       the way a hover is, because it must stay clear of `bgInset` too, and in
       light mode those two pull the same direction. */
    bgBlock: N(d.bgBlock),
    /* The recessed panel. Deliberately NOT `bgInset`: that is a page-level
       surface, and inside a card it happened to land right where a grouped
       block wanted to be. Giving the panel its own token is what lets the block
       be a whisper and the panel be unmistakable at the same time. */
    bgSunken: N(d.bgSunken),
    cardHover: N(d.cardHover),
    cardRing: d.cardRing ? N(d.cardRing) : "transparent",
    border: T(P.border),
    borderStrong: N(d.borderStrong),
    fieldBg: N(d.fieldBg),

    /* ---- AGENCY: you / system / autonomous, resting + active ------------- */
    you,
    youFg: T(P.youFg),
    youSoft: T(P.youSoft),
    /* `you` has no separate `-up`: your own action is not a thing that keeps
       happening while you watch it. Where the tier needs an active tone (a
       run you started), it uses the resting one, which is already the loudest
       thing on the surface. */
    sys: T(sysT),
    sysSoft: T(P.sysSoft),
    sysUp: T(sysUpT),
    auto: T(autoT),
    autoSoft: T(P.autoSoft),
    autoUp: T(autoUpT),

    /* ---- the accent family IS the `you` tier ----------------------------- */
    accent: you,
    accentHover: step(youT, d.hoverL, d.hoverC),
    accentActive: step(youT, d.activeL, d.activeC),
    accentFg: T(P.youFg),
    accentSoft: T(P.youSoft),

    /* ---- status: outline vocabulary, identical in all eight schemes ------- */
    ok: T(okT),
    okSolid: statusSolid(P.ok),
    okSoft: T(P.okSoft),

    /* `running` is the one status that describes an ACTOR rather than an
       outcome, so it does not get a fixed color: it resolves to the initiating
       tier's active tone. The default binding is `system` (work inside a
       conversation you are present for); `[data-agency]` in components.css
       re-points it for a row that knows better. Terminal states never split by
       tier — an outcome has no actor. */
    running: T(sysUpT),
    runningSolid: T(sysUpT),
    runningSoft: T(P.sysSoft),

    stopped: T(stoppedT),
    stoppedSolid: neutralSolid(P.stopped),
    stoppedSoft: T(P.stoppedSoft),

    warn: T(warnT),
    warnSolid: statusSolid(P.warn),
    warnSoft: T(P.warnSoft),

    danger: T(dangerT),
    dangerSolid: statusSolid(P.danger),
    dangerSoft: T(P.dangerSoft),
    dangerHover: step(dangerT, d.dangerHoverL, 1),

    /* ---- the code layer: nine roles, six hues, one danger alias ---------- */
    synComment: synMuted(SYN_NEUTRAL[mode].comment),
    synPunctuation: synMuted(SYN_NEUTRAL[mode].punctuation),
    synKeyword: synTone(SYN_HUES.keyword),
    synType: synTone(SYN_HUES.type),
    synFunction: synTone(SYN_HUES.function),
    synString: synTone(SYN_HUES.string),
    synNumber: synTone(SYN_HUES.number),
    synProperty: synTone(SYN_HUES.property),
    /* Not a tenth color. Emitted as `var(--danger)` in CSS (see ALIASES in
       gen-tokens.mjs); the RN object carries the value, since it cannot point. */
    synInvalid: T(dangerT),

    scrim,
    focusRing: alpha(you, d.focusAlpha),
    shadowSm: d.shadows.sm(shadowSeed),
    shadowCard: d.shadows.card(shadowSeed),
    shadowMd: d.shadows.md(shadowSeed),
    shadowLg: d.shadows.lg(shadowSeed),
  };
}

/**
 * The three agency tones for a scheme, mode-independent and at moderate chroma,
 * for the Settings picker's identity strip and nothing else.
 *
 * The picker's live preview renders the scheme's real surfaces, which is
 * correct but nearly hue-less at chip size: near-black and near-white do not
 * show hue. The strip shows what the scheme actually IS now — the relationship
 * between the three tiers — which is the thing being chosen.
 * @param {string} schemeId
 */
export function identitySwatch(schemeId) {
  const s = paletteById(schemeId);
  const yc = s.yc || 1, sc = s.sc || 1, ac = s.ac || 1;
  return {
    you: oklch(0.66, 0.14 * yc, s.base),
    sys: oklch(0.66, 0.13 * sc, s.sys),
    auto: oklch(0.66, 0.15 * ac, s.auto),
  };
}

/** Every scheme, keyed `schemeId.mode`. */
export function allSchemes() {
  /** @type {Record<string, Record<string,string>>} */
  const out = {};
  for (const s of SCHEMES) for (const m of MODES) out[`${s.id}.${m}`] = buildScheme(s.id, m);
  return out;
}

/* Categorical suite — DIFFERENTIATION, not status and not agency. Deliberately
   the same in every scheme: a tag color that moved when you changed scheme
   would stop being a stable identity for an agent or a source.

   These CAN land near an agency hue in some scheme (eight schemes cover the
   wheel, so any fixed set will), and that is handled the same way status is:
   by treatment, not by hue. A categorical tag is a rounded rect with a dot and
   a name in sans; an agency chip is a pill with no dot and no border, in
   uppercase mono. Nothing reads them as the same vocabulary. */
export const CATEGORICAL = {
  violet: "#7a57e0",
  turquoise: "#10b4bf",
  blue: "#3f7df0",
  indigo: "#5160ee",
  orchid: "#b35fd6",
  slate: "#7c8aa0",
};

/* Back-compat alias: the seed list was called PALETTES before the agency model
   renamed the concept to a scheme. Kept so nothing importing it breaks. */
export const PALETTES = SCHEMES;
