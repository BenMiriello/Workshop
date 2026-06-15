#!/usr/bin/env node
/* =============================================================================
   gen-tokens.mjs — single-source the Workshop design tokens.

   tokens.css (CSS custom properties) is the canonical source of truth for the
   web. Mobile (Expo / React Native) cannot read CSS variables, so it needs the
   same values as a plain JS object. This script PARSES tokens.css and EMITS that
   object, so the two stop drifting (they were hand-synced before).

   It is idempotent and NEVER modifies tokens.css. It writes:
     frontend/src/styles/tokens.generated.js

   That generated file mirrors the shape of the hand-maintained tokens.js (the
   Expo-consumed module that also carries usage docs + makeTheme). Treat the
   generated file as the diff oracle: run this, diff it against tokens.js, and
   reconcile any real value differences. See frontend/src/ui/CHANGELOG.md.

   Run: npm run gen:tokens   (from frontend/)   or   node scripts/gen-tokens.mjs
   ========================================================================== */

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(__dirname, "..");
const CSS_PATH = resolve(REPO, "frontend/src/styles/tokens.css");
const OUT_PATH = resolve(REPO, "frontend/src/styles/tokens.generated.js");

const css = readFileSync(CSS_PATH, "utf8");

/* Strip /* ... *​/ comments so a commented value never wins. */
let noComments = css.replace(/\/\*[\s\S]*?\*\//g, "");

/* Drop the prefers-reduced-motion override block. It re-declares :root with
   --dur-* = 0ms; we want the canonical (non-reduced) motion durations. Match the
   @media wrapper and its full brace-balanced body. */
noComments = noComments.replace(
  /@media\s*\(\s*prefers-reduced-motion[^)]*\)\s*\{[\s\S]*?\}\s*\}/g,
  ""
);

/* Pull each `[data-theme="light"|"dark"]` / `:root` block separately so we can
   read the palette per scheme. We key palette values off the LIGHT and DARK
   selector blocks specifically (the canonical sources), matching tokens.js. */
function blockFor(selectorRe) {
  // find "selector { ... }" — non-greedy to the first closing brace at depth 0.
  const re = new RegExp(selectorRe.source + "\\s*\\{([^}]*)\\}", "g");
  let combined = "";
  let m;
  while ((m = re.exec(noComments)) !== null) combined += m[1] + "\n";
  return combined;
}

/* Parse `--name: value;` declarations out of a block into a Map. */
function vars(block) {
  const out = new Map();
  const re = /--([a-z0-9-]+)\s*:\s*([^;]+);/gi;
  let m;
  while ((m = re.exec(block)) !== null) out.set(m[1].trim(), m[2].trim());
  return out;
}

const lightBlock = blockFor(/\[data-theme="light"\]/);
const darkBlock = blockFor(/\[data-theme="dark"\]/);
// Theme-independent scales live in the bare `:root` block.
const rootBlock = blockFor(/:root(?!,)/) || blockFor(/:root/);

const light = vars(lightBlock);
const dark = vars(darkBlock);
const root = vars(rootBlock);

/* Map CSS var name -> tokens.js palette key (camelCase). Order matches tokens.js
   so the generated diff is line-comparable. */
const PALETTE_KEYS = [
  ["bg", "bg"], ["bg-inset", "bgInset"], ["fg", "fg"], ["muted", "muted"], ["faint", "faint"],
  ["card", "card"], ["card-hover", "cardHover"], ["card-ring", "cardRing"],
  ["border", "border"], ["border-strong", "borderStrong"], ["field-bg", "fieldBg"],
  ["accent", "accent"], ["accent-hover", "accentHover"], ["accent-active", "accentActive"],
  ["accent-fg", "accentFg"], ["accent-soft", "accentSoft"],
  ["ok", "ok"], ["ok-solid", "okSolid"], ["ok-soft", "okSoft"],
  ["running", "running"], ["running-solid", "runningSolid"], ["running-soft", "runningSoft"],
  ["stopped", "stopped"], ["stopped-solid", "stoppedSolid"], ["stopped-soft", "stoppedSoft"],
  ["warn", "warn"], ["warn-solid", "warnSolid"], ["warn-soft", "warnSoft"],
  ["danger", "danger"], ["danger-solid", "dangerSolid"], ["danger-soft", "dangerSoft"],
  ["danger-hover", "dangerHover"],
];

/* RN rgba() can't carry spaces the way CSS does; tokens.js writes them compact
   (e.g. "rgba(157,134,245,0.16)"). Normalize so the diff is apples-to-apples. */
function norm(v) {
  if (v == null) return v;
  return v.replace(/rgba\(\s*([^)]*)\)/gi, (_, inner) =>
    "rgba(" + inner.split(",").map((s) => s.trim()).join(",") + ")"
  );
}

function paletteFor(map) {
  const o = {};
  for (const [cssKey, jsKey] of PALETTE_KEYS) {
    if (map.has(cssKey)) o[jsKey] = norm(map.get(cssKey));
  }
  return o;
}

/* Shadow color/opacity are not their own CSS vars; they are baked into the
   --shadow-* rgba()s. tokens.js exposes a single shadowColor/shadowOpacity per
   scheme for RN's elevation model. Derive them from --shadow-md (the mid shadow
   tokens.js was modeled on): pull its rgba base color + alpha. */
function shadowFrom(map) {
  const md = map.get("shadow-md") || "";
  const m = md.match(/rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)/i);
  if (!m) return null;
  const [, r, g, b, a] = m;
  const hex =
    "#" + [r, g, b].map((n) => Number(n).toString(16).padStart(2, "0")).join("");
  return { shadowColor: hex, shadowOpacity: Number(a) };
}

const lightPalette = paletteFor(light);
const darkPalette = paletteFor(dark);
const lightShadow = shadowFrom(light);
const darkShadow = shadowFrom(dark);
if (lightShadow) Object.assign(lightPalette, lightShadow);
if (darkShadow) Object.assign(darkPalette, darkShadow);

/* Categorical suite — theme-independent, from :root. */
const CAT_KEYS = [
  ["cat-violet", "violet"], ["cat-turquoise", "turquoise"], ["cat-blue", "blue"],
  ["cat-indigo", "indigo"], ["cat-orchid", "orchid"], ["cat-slate", "slate"],
];
const categorical = {};
for (const [cssKey, jsKey] of CAT_KEYS) {
  if (root.has(cssKey)) categorical[jsKey] = root.get(cssKey);
}
const chartSeries = [
  categorical.violet, categorical.turquoise, categorical.orchid,
  categorical.blue, categorical.indigo,
];

/* Scales — convert rem -> px (RN is unitless px). tokens.css uses rem on a 16px
   root for space/type/control; tokens.js stores raw px numbers. */
function rem(v) {
  if (v == null) return undefined;
  const s = String(v).trim();
  if (s.endsWith("rem")) return Math.round(parseFloat(s) * 16 * 1000) / 1000;
  if (s.endsWith("px")) return parseFloat(s);
  if (s === "0") return 0;
  const n = parseFloat(s);
  return Number.isNaN(n) ? s : n;
}
function px(v) {
  if (v == null) return undefined;
  return parseFloat(String(v));
}
function ms(v) {
  return v == null ? undefined : parseInt(String(v), 10);
}

const space = {};
for (let i = 0; i <= 8; i++) {
  const k = `space-${i}`;
  if (root.has(k)) space[i] = rem(root.get(k));
}

const fs = {
  xs: rem(root.get("fs-xs")), sm: rem(root.get("fs-sm")), base: rem(root.get("fs-base")),
  md: rem(root.get("fs-md")), lg: rem(root.get("fs-lg")), xl: rem(root.get("fs-xl")),
  "2xl": rem(root.get("fs-2xl")),
};

const lh = {
  tight: parseFloat(root.get("lh-tight")),
  snug: parseFloat(root.get("lh-snug")),
  base: parseFloat(root.get("lh-base")),
};

const weight = {
  normal: String(root.get("fw-normal")), medium: String(root.get("fw-medium")),
  semi: String(root.get("fw-semi")), bold: String(root.get("fw-bold")),
};

const radius = {
  sm: px(root.get("radius-sm")), md: px(root.get("radius-md")), lg: px(root.get("radius-lg")),
  xl: px(root.get("radius-xl")), pill: px(root.get("radius-pill")),
};

const control = {
  h: rem(root.get("control-h")), hSm: rem(root.get("control-h-sm")), hLg: rem(root.get("control-h-lg")),
  padX: rem(root.get("pad-x")), padXSm: rem(root.get("pad-x-sm")), padXLg: rem(root.get("pad-x-lg")),
  fieldPadX: rem(root.get("field-pad-x")),
};

const density = { rowPadY: rem(root.get("row-pad-y")), controlH: control.h };

const motion = {
  fast: ms(root.get("dur-fast")), base: ms(root.get("dur-base")), slow: ms(root.get("dur-slow")),
  easeOut: [0.16, 1, 0.3, 1], easeInOut: [0.4, 0, 0.2, 1],
};

const palette = { light: lightPalette, dark: darkPalette };

/* ----- Emit. Serialize a stable, readable module mirroring tokens.js. ------- */
const HEADER = `/* =============================================================================
   Workshop Design System — MOBILE TOKENS (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from frontend/src/styles/tokens.css.
   tokens.css is canonical. Run \`npm run gen:tokens\` (from frontend/) to refresh.
   This file is the diff oracle for the hand-maintained tokens.js; reconcile any
   real value differences there. See frontend/src/ui/CHANGELOG.md.
   ========================================================================== */
`;

function lit(v) {
  return JSON.stringify(v);
}
function objInline(o) {
  return "{ " + Object.entries(o).map(([k, v]) => `${/^[a-z_$][\w$]*$/i.test(k) ? k : JSON.stringify(k)}: ${lit(v)}`).join(", ") + " }";
}
function paletteBlock(name, o) {
  const lines = Object.entries(o).map(([k, v]) => `    ${k}: ${lit(v)},`).join("\n");
  return `  ${name}: {\n${lines}\n  },`;
}

const body = `
const palette = {
${paletteBlock("light", palette.light)}
${paletteBlock("dark", palette.dark)}
};

const categorical = ${objInline(categorical)};

const chartSeries = ${lit(chartSeries)};

const tokens = {
  space: ${objInline(space)},
  fs: ${objInline(fs)},
  lh: ${objInline(lh)},
  weight: ${objInline(weight)},
  radius: ${objInline(radius)},
  font: { sans: "HankenGrotesk", mono: "JetBrainsMono" },
  label: { spacing: 1.1, transform: "uppercase" },
  control: ${objInline(control)},
  density: ${objInline(density)},
  motion: ${objInline(motion)},
  palette,
  categorical,
  chartSeries,
};

function makeTheme(scheme = "light") {
  return Object.assign({}, palette[scheme] || palette.light);
}

export { tokens, palette, categorical, chartSeries, makeTheme };
`;

writeFileSync(OUT_PATH, HEADER + body, "utf8");
console.log("gen-tokens: wrote " + OUT_PATH);
console.log(
  "gen-tokens: " +
    Object.keys(palette.light).length + " light palette keys, " +
    Object.keys(palette.dark).length + " dark, " +
    Object.keys(categorical).length + " categorical, " +
    Object.keys(space).length + " space steps"
);
