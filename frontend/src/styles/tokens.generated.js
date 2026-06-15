/* =============================================================================
   Workshop Design System — MOBILE TOKENS (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from frontend/src/styles/tokens.css.
   tokens.css is canonical. Run `npm run gen:tokens` (from frontend/) to refresh.
   This file is the diff oracle for the hand-maintained tokens.js; reconcile any
   real value differences there. See frontend/src/ui/CHANGELOG.md.
   ========================================================================== */

const palette = {
  light: {
    bg: "#f5f5fa",
    bgInset: "#ececf3",
    fg: "#1a1923",
    muted: "#6a6879",
    faint: "#9b99ab",
    card: "#ffffff",
    cardHover: "#fbfbff",
    cardRing: "transparent",
    border: "#e7e7f0",
    borderStrong: "#d6d6e3",
    fieldBg: "#efeff6",
    accent: "#7a57e0",
    accentHover: "#6845cd",
    accentActive: "#593bb0",
    accentFg: "#ffffff",
    accentSoft: "#efeafc",
    ok: "#1d7a51",
    okSolid: "#2f9f6d",
    okSoft: "#daf1e4",
    running: "#5d42c6",
    runningSolid: "#7a57e0",
    runningSoft: "#efeafc",
    stopped: "#585868",
    stoppedSolid: "#9594a6",
    stoppedSoft: "#ededf2",
    warn: "#8a5e0f",
    warnSolid: "#d6921f",
    warnSoft: "#f7eccf",
    danger: "#a8404f",
    dangerSolid: "#cb5a6a",
    dangerSoft: "#f6e2e5",
    dangerHover: "#b34a59",
    shadowColor: "#1a1932",
    shadowOpacity: 0.1,
  },
  dark: {
    bg: "#0f0e17",
    bgInset: "#16151f",
    fg: "#ecebf3",
    muted: "#9a98ac",
    faint: "#66647a",
    card: "#191824",
    cardHover: "#201f2d",
    cardRing: "#272636",
    border: "#262534",
    borderStrong: "#36344a",
    fieldBg: "#14131d",
    accent: "#9d86f5",
    accentHover: "#b3a3f8",
    accentActive: "#c8bcfb",
    accentFg: "#100b1c",
    accentSoft: "rgba(157,134,245,0.16)",
    ok: "#62d29d",
    okSolid: "#45c089",
    okSoft: "rgba(69,192,137,0.16)",
    running: "#b4a4f7",
    runningSolid: "#9d86f5",
    runningSoft: "rgba(157,134,245,0.18)",
    stopped: "#9594a6",
    stoppedSolid: "#6f6e80",
    stoppedSoft: "rgba(149,148,166,0.15)",
    warn: "#e6b24a",
    warnSolid: "#d6921f",
    warnSoft: "rgba(214,146,31,0.16)",
    danger: "#e9889a",
    dangerSolid: "#d96576",
    dangerSoft: "rgba(217,101,118,0.16)",
    dangerHover: "#ec97a6",
    shadowColor: "#000000",
    shadowOpacity: 0.5,
  },
};

const categorical = { violet: "#7a57e0", turquoise: "#10b4bf", blue: "#3f7df0", indigo: "#5160ee", orchid: "#b35fd6", slate: "#7c8aa0" };

const chartSeries = ["#7a57e0","#10b4bf","#b35fd6","#3f7df0","#5160ee"];

const tokens = {
  space: { "0": 0, "1": 4, "2": 8, "3": 12, "4": 16, "5": 24, "6": 32, "7": 48, "8": 64 },
  fs: { xs: 12, sm: 13, base: 14, md: 15, lg: 17, xl: 24, "2xl": 32 },
  lh: { tight: 1.2, snug: 1.4, base: 1.55 },
  weight: { normal: "400", medium: "500", semi: "600", bold: "700" },
  radius: { sm: 6, md: 9, lg: 14, xl: 20, pill: 999 },
  font: { sans: "HankenGrotesk", mono: "JetBrainsMono" },
  label: { spacing: 1.1, transform: "uppercase" },
  control: { h: 32, hSm: 28, hLg: 38, padX: 14, padXSm: 10, padXLg: 18, fieldPadX: 12 },
  density: { rowPadY: 6.4, controlH: 32 },
  motion: { fast: 120, base: 200, slow: 320, easeOut: [0.16,1,0.3,1], easeInOut: [0.4,0,0.2,1] },
  palette,
  categorical,
  chartSeries,
};

function makeTheme(scheme = "light") {
  return Object.assign({}, palette[scheme] || palette.light);
}

export { tokens, palette, categorical, chartSeries, makeTheme };
