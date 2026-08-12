#!/usr/bin/env node
/* =============================================================================
   gen-tokens.mjs — emit the color layer from packages/ui/src/styles/palette.mjs.
   -----------------------------------------------------------------------------
     node scripts/gen-tokens.mjs            # write the generated files
     node scripts/gen-tokens.mjs --check    # exit 1 if they are stale

   Outputs (all GENERATED — never hand-edit):
     packages/ui/src/styles/palettes.css        every [data-palette][data-theme] block
     packages/ui/src/styles/palettes.gen.ts     the typed menu the picker renders
     packages/ui/src/styles/tokens.generated.js the same values for Expo/React Native

   palette.mjs is canonical; this file only serializes it. It also verifies the
   NON-color scales, which are still hand-written in both tokens.css and
   tokens.js because CSS variables don't reach React Native.

   `--check` runs in scripts/check_design.sh, so neither a palette edit that was
   never regenerated nor a scale that drifted can reach a commit.
   ========================================================================== */

import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { SCHEMES, MODES, DEFAULT_PALETTE, LEGACY_PALETTES, buildScheme, paletteById, CATEGORICAL, identitySwatch } from "../src/styles/palette.mjs";
import { tokens } from "../src/styles/tokens.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const STYLES = resolve(HERE, "../src/styles");

const kebab = (k) => k.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());

/* Tokens that are not emitted as a plain `--name: value` line — they compose
   into a shadow/ring instead. */
const COMPOSED = new Set(["focusRing", "shadowSm", "shadowCard", "shadowMd", "shadowLg"]);

/* The accent family IS the `you` agency tier. Emitted as `var()` aliases rather
   than as a second copy of the hex, so the two can never drift and so the CSS
   says out loud which one is the real name. The RN object copies the values,
   since it has no var() to point with. */
const ALIASES = {
  accent: "you",
  accentFg: "you-fg",
  accentSoft: "you-soft",
};

function cssBlock(selector, paletteId, mode) {
  const s = buildScheme(paletteId, mode);
  const lines = [];
  for (const [k, v] of Object.entries(s)) {
    if (COMPOSED.has(k)) continue;
    if (ALIASES[k]) {
      lines.push(`  --${kebab(k)}: var(--${ALIASES[k]});`);
      continue;
    }
    lines.push(`  --${kebab(k)}: ${v};`);
  }
  lines.push(`  --err: var(--danger);`);
  lines.push(`  --focus-ring: 0 0 0 2px var(--bg), 0 0 0 4px ${s.focusRing};`);
  lines.push(`  --shadow-sm: ${s.shadowSm};`);
  lines.push(`  --shadow-card: ${s.shadowCard};`);
  lines.push(`  --shadow-md: ${s.shadowMd};`);
  lines.push(`  --shadow-lg: ${s.shadowLg};`);
  lines.push(`  color-scheme: ${mode};`);
  return `${selector} {\n${lines.join("\n")}\n}\n`;
}

/* Selector strategy
   ------------------
   `data-palette` and `data-theme` are independent attributes on <html>, so each
   scheme needs a light block, a dark block, and a system block that follows the
   OS.

   THE BASE BLOCK IS UNCONDITIONAL, and that is not a detail. It emits the
   default scheme on a bare `:root`, so EVERY document is fully painted no
   matter what `data-palette` says — including a value this stylesheet has never
   heard of. It used to be `:root:not([data-palette])`, which meant a stale or
   renamed id matched no block at all and every color token in the system
   resolved to nothing: the whole app went white and grey, with only whatever
   the browser defaults to. That is exactly what happened when the five palettes
   became eight schemes and a tab still holding `data-palette="violet"` picked
   up the new CSS over HMR. A color layer must never be able to leave the app
   unpainted; an unknown scheme falls back to the default's colors, and the
   bootstrap in index.html rewrites the attribute on the next load.

   Per-scheme blocks come AFTER the base and carry equal specificity, so they
   win by source order for the document that actually names them.

   Both attributes are matched on the SAME element, which is what lets the
   Settings swatches preview a scheme: give a swatch `data-palette="lagoon"
   data-theme="dark"` and it resolves that scheme locally, because custom
   properties are matched per element and then inherited. */
function baseCss() {
  const p = paletteById(DEFAULT_PALETTE);
  const out = [
    `/* ---- BASE — ${p.label}, on a bare :root, so no document is ever unpainted --- */`,
  ];
  out.push(cssBlock([`:root`, `:root[data-theme="light"]`].join(",\n"), p.id, "light"));
  out.push(cssBlock(`:root[data-theme="dark"]`, p.id, "dark"));
  out.push(
    `@media (prefers-color-scheme: dark) {\n${indent(
      cssBlock([`:root[data-theme="system"]`, `:root:not([data-theme="light"]):not([data-theme="dark"])`].join(",\n"), p.id, "dark"),
    )}}\n`,
  );
  return out.join("\n");
}

function paletteCss(p) {
  const self = `[data-palette="${p.id}"]`;
  const hues = `you ${p.base} · system ${p.sys} · autonomous ${p.auto}`;
  const out = [`/* ---- ${p.label} (${p.quiet ? "quiet" : "expressive"}) — ${hues} ---------------- */`];

  const lightSel = [self, `${self}[data-theme="light"]`];
  const darkSel = [`${self}[data-theme="dark"]`];
  const sysSel = [`${self}[data-theme="system"]`, `${self}:not([data-theme="light"]):not([data-theme="dark"])`];

  out.push(cssBlock(lightSel.join(",\n"), p.id, "light"));
  out.push(cssBlock(darkSel.join(",\n"), p.id, "dark"));
  out.push(`@media (prefers-color-scheme: dark) {\n${indent(cssBlock(sysSel.join(",\n"), p.id, "dark"))}}\n`);
  return out.join("\n");
}

const indent = (s) => s.split("\n").map((l) => (l ? "  " + l : l)).join("\n");

function buildCss() {
  return `/* =============================================================================
   Workshop Design System — PALETTES  (GENERATED — do not edit by hand)
   -----------------------------------------------------------------------------
   Emitted by scripts/gen-tokens.mjs from packages/ui/src/styles/palette.mjs.
   Edit palette.mjs, then run \`npm run gen:tokens\` from packages/ui/.

   ${SCHEMES.length} palettes x { light, dark }. \`data-palette\` and \`data-theme\` both live on
   <html>; the pre-React bootstrap in index.html sets them before first paint.
   Imported by tokens.css, so every entry point picks it up automatically.
   ========================================================================== */

${baseCss()}
${SCHEMES.map(paletteCss).join("\n")}`;
}

function buildJs() {
  const entries = [];
  for (const p of SCHEMES) {
    const modes = MODES.map((m) => {
      const s = buildScheme(p.id, m);
      const body = Object.entries(s).map(([k, v]) => `      ${k}: ${JSON.stringify(v)},`).join("\n");
      return `    ${m}: {\n${body}\n    },`;
    }).join("\n");
    entries.push(`  ${p.id}: {\n${modes}\n  },`);
  }
  const meta = SCHEMES.map(
    (p) => `  { id: ${JSON.stringify(p.id)}, label: ${JSON.stringify(p.label)}, blurb: ${JSON.stringify(p.blurb)}, group: ${JSON.stringify(p.quiet ? "quiet" : "expressive")} },`
  ).join("\n");
  return `/* =============================================================================
   Workshop Design System — MOBILE COLOR TOKENS (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from packages/ui/src/styles/palette.mjs.
   CSS custom properties don't reach React Native, so the Expo app imports these
   hexes instead. Same model, same numbers as palettes.css.
   Run \`npm run gen:tokens\` (from packages/ui/) to refresh.
   ========================================================================== */

export const palettes = {
${entries.join("\n")}
};

export const paletteMeta = [
${meta}
];

export const defaultPalette = ${JSON.stringify(DEFAULT_PALETTE)};

/* Categorical suite — identical in every palette (differentiation, not brand). */
export const categorical = ${JSON.stringify(CATEGORICAL, null, 2).replace(/\n/g, "\n")};
`;
}

/* The typed list the web app imports. Kept separate from tokens.generated.js so
   a Settings picker pulls in eight labels, not every hex in the system. */
function buildTs() {
  const ids = SCHEMES.map((p) => JSON.stringify(p.id)).join(" | ");
  const rows = SCHEMES.map(
    (p) =>
      `  { id: ${JSON.stringify(p.id)}, label: ${JSON.stringify(p.label)}, blurb: ${JSON.stringify(p.blurb)},` +
      ` group: ${JSON.stringify(p.quiet ? "quiet" : "expressive")}, swatch: ${JSON.stringify(identitySwatch(p.id))} },`
  ).join("\n");
  const legacy = Object.entries(LEGACY_PALETTES)
    .map(([from, to]) => `  ${from}: ${JSON.stringify(to)},`)
    .join("\n");
  return `/* =============================================================================
   Workshop Design System — SCHEME LIST (GENERATED — do not edit by hand)
   Emitted by scripts/gen-tokens.mjs from packages/ui/src/styles/palette.mjs.
   The colors themselves are in palettes.css; this is just the menu.
   ========================================================================== */

export type PaletteId = ${ids};

/** Which row of the Settings picker a scheme belongs to. The quiet set is a
    different KIND of choice (one hue, tiers by chroma), not three more colors,
    so the picker shows the two groups separately. */
export type PaletteGroup = "expressive" | "quiet";

export interface PaletteMeta {
  id: PaletteId;
  /** Title-case name shown in Settings. */
  label: string;
  /** One line describing the scheme's character. */
  blurb: string;
  group: PaletteGroup;
  /** The scheme's three agency tones, mode-independent, for the picker's
      identity strip. See palette.mjs identitySwatch(). */
  swatch: { you: string; sys: string; auto: string };
}

export const PALETTE_META: readonly PaletteMeta[] = [
${rows}
] as const;

export const DEFAULT_PALETTE: PaletteId = ${JSON.stringify(DEFAULT_PALETTE)};

export const PALETTE_IDS: readonly PaletteId[] = PALETTE_META.map((p) => p.id);

export function isPaletteId(v: unknown): v is PaletteId {
  return typeof v === "string" && (PALETTE_IDS as readonly string[]).includes(v);
}

/** Scheme ids from before the agency model, mapped to their nearest surviving
    scheme, so a stored preference moves across instead of snapping to the
    default. Unknown ids still fall back to DEFAULT_PALETTE. */
export const LEGACY_PALETTES: Record<string, PaletteId> = {
${legacy}
};

/** The id to actually apply for a stored value: itself, its replacement, or the
    default. One function so the bootstrap, the hook and the tests agree. */
export function resolvePaletteId(v: unknown): PaletteId {
  if (isPaletteId(v)) return v;
  if (typeof v === "string" && LEGACY_PALETTES[v]) return LEGACY_PALETTES[v];
  return DEFAULT_PALETTE;
}
`;
}

/* ---- Non-color scales: tokens.css vs tokens.js ---------------------------
   Colors can't drift (both outputs come from palette.mjs), but the spacing /
   type / radius / control / motion scales are still hand-written in both files
   because CSS custom properties don't reach React Native. The previous
   generator caught that drift by parsing tokens.css; keep the guarantee here.
   A vitest test can't do this job — the app tsconfig is DOM-only, so there is
   no fs, and vitest's CSS handling swallows a `?raw` import. */
function verifyScales() {
  const css = readFileSync(resolve(STYLES, "tokens.css"), "utf8");
  const px = (name) => {
    const m = css.match(new RegExp(`--${name}\\s*:\\s*([^;]+);`));
    if (!m) return { err: `--${name} missing from tokens.css` };
    const v = m[1].trim();
    return { v: v.endsWith("rem") ? parseFloat(v) * 16 : parseFloat(v) };
  };
  const problems = [];
  const cmp = (cssName, jsValue, label) => {
    const { v, err } = px(cssName);
    if (err) return problems.push(err);
    if (v !== jsValue) problems.push(`${label}: tokens.css --${cssName}=${v} vs tokens.js ${jsValue}`);
  };
  for (const s of [1, 2, 3, 4, 5, 6, 7, 8]) cmp(`space-${s}`, tokens.space[s], "spacing");
  for (const [k, n] of Object.entries({ xs: "fs-xs", sm: "fs-sm", base: "fs-base", md: "fs-md", lg: "fs-lg", xl: "fs-xl", "2xl": "fs-2xl" }))
    cmp(n, tokens.fs[k], "type");
  for (const [k, n] of Object.entries({ sm: "radius-sm", md: "radius-md", lg: "radius-lg", xl: "radius-xl", pill: "radius-pill" }))
    cmp(n, tokens.radius[k], "radius");
  cmp("control-h", tokens.control.h, "control");
  cmp("control-h-sm", tokens.control.hSm, "control");
  cmp("control-h-lg", tokens.control.hLg, "control");
  cmp("pad-x", tokens.control.padX, "control");
  cmp("pad-x-sm", tokens.control.padXSm, "control");
  cmp("pad-x-lg", tokens.control.padXLg, "control");
  cmp("field-pad-x", tokens.control.fieldPadX, "control");
  cmp("dur-fast", tokens.motion.fast, "motion");
  cmp("dur-base", tokens.motion.base, "motion");
  cmp("dur-slow", tokens.motion.slow, "motion");

  // A hex back in tokens.css means someone bypassed palette.mjs. The --cat-*
  // suite is the one allowed exception (palette-independent by ADR 0012).
  const hexes = (css.match(/#[0-9a-fA-F]{3,8}\b/g) || []).length;
  const catHexes = (css.match(/--cat-[a-z]+:\s*#[0-9a-fA-F]{3,8}/g) || []).length;
  if (hexes !== catHexes) {
    problems.push(`tokens.css has ${hexes - catHexes} color literal(s) outside the --cat-* suite — colors belong in palette.mjs`);
  }
  return problems;
}

const targets = [
  [resolve(STYLES, "palettes.css"), buildCss()],
  [resolve(STYLES, "palettes.gen.ts"), buildTs()],
  [resolve(STYLES, "tokens.generated.js"), buildJs()],
];

const check = process.argv.includes("--check");
let stale = 0;
for (const [path, content] of targets) {
  const current = existsSync(path) ? readFileSync(path, "utf8") : null;
  if (current === content) continue;
  if (check) {
    console.error(`stale: ${path} — run \`npm run gen:tokens\` (from packages/ui/)`);
    stale++;
  } else {
    writeFileSync(path, content, "utf8");
    console.log(`wrote ${path}`);
  }
}
const scaleProblems = verifyScales();
if (scaleProblems.length) {
  console.error("gen-tokens: tokens.css and tokens.js disagree —");
  for (const p of scaleProblems) console.error(`  ${p}`);
  process.exit(1);
}
if (check) {
  if (stale) process.exit(1);
  console.log(
    `gen-tokens: color files up to date (${SCHEMES.length} palettes x ${MODES.length} modes), scales in sync`
  );
}
