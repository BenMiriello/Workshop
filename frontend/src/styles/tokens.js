/* =============================================================================
   Workshop Design System — MOBILE TOKENS
   The scales as a plain JS object for the Expo app's StyleSheet, since CSS
   custom properties don't reach React Native.

   COLOR is not defined here. It comes from tokens.generated.js, which
   scripts/gen-tokens.mjs emits from palette.mjs — the same model, and the same
   numbers, as the web's palettes.css. This file owns the theme-independent
   scales only (space, type, radii, controls, motion), mirroring tokens.css.

   Usage (Expo / RN):
     import { tokens, makeTheme } from "./tokens";
     const t = makeTheme(scheme, paletteId);   // "light"|"dark", "violet"|...
     const styles = StyleSheet.create({
       card:  { backgroundColor: t.card, borderRadius: tokens.radius.lg, padding: tokens.space[5] },
       title: { color: t.fg, fontFamily: tokens.font.sans, fontSize: tokens.fs.lg, fontWeight: "600" },
       btn:   { height: tokens.control.h, paddingHorizontal: tokens.control.padX, borderRadius: tokens.radius.md },
     });
   ========================================================================== */

import { palettes, paletteMeta, defaultPalette, categorical } from "./tokens.generated.js";

/* Default chart series order. Categorical, never status hues — a trending line
   must not read as good/bad. Same six in every palette (see tokens.css). */
const chartSeries = [
  categorical.violet,
  categorical.turquoise,
  categorical.orchid,
  categorical.blue,
  categorical.indigo,
];

/* Back-compat: `palette.light` / `palette.dark` used to be the only scheme.
   That is now the default palette's two modes. */
const palette = palettes[defaultPalette];

const tokens = {
  space: { 0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32, 7: 48, 8: 64 },
  fs: { xs: 12, sm: 13, base: 14, md: 15, lg: 17, xl: 24, "2xl": 32 },
  lh: { tight: 1.2, snug: 1.4, base: 1.55 },
  weight: { normal: "400", medium: "500", semi: "600", bold: "700" },
  radius: { sm: 6, md: 9, lg: 14, xl: 20, pill: 999 },
  font: { sans: "HankenGrotesk", mono: "JetBrainsMono" },
  label: { spacing: 1.1, transform: "uppercase" },
  // Control sizing — comfortable, never chunky. Mirrors --control-h / --pad-x.
  control:  { h: 32, hSm: 28, hLg: 38, padX: 14, padXSm: 10, padXLg: 18, fieldPadX: 12 },
  density:  { rowPadY: 6, controlH: 32 },
  motion:   { fast: 120, base: 200, slow: 320, easeOut: [0.16, 1, 0.3, 1], easeInOut: [0.4, 0, 0.2, 1] },
  palettes,
  paletteMeta,
  defaultPalette,
  palette,
  categorical,
  chartSeries,
};

// Flat theme object for a given mode + color scheme. Unknown ids fall back to
// the default palette in light, so a stale stored preference can't blank the UI.
function makeTheme(scheme = "light", paletteId = defaultPalette) {
  const p = palettes[paletteId] || palettes[defaultPalette];
  return Object.assign({}, p[scheme] || p.light);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { tokens, palettes, palette, paletteMeta, defaultPalette, categorical, chartSeries, makeTheme };
}
if (typeof window !== "undefined") {
  window.WorkshopTokens = { tokens, palettes, palette, paletteMeta, defaultPalette, categorical, chartSeries, makeTheme };
}
export { tokens, palettes, palette, paletteMeta, defaultPalette, categorical, chartSeries, makeTheme };
