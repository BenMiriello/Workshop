/* =============================================================================
   build-css.mjs — emit the two shipped stylesheets into dist/.

     dist/tokens.css   the whole token layer, @imports inlined, ZERO JavaScript.
                       This is the portable artifact: any consumer that can load
                       a stylesheet gets the Workshop visual language, whether or
                       not it uses React.
     dist/styles.css   the component rules the primitives' classNames refer to.

   tokens.css @imports palettes.css, and a published package must not ship a
   stylesheet whose @import resolves against the source tree. So the imports are
   inlined here rather than copied. Recursive and cycle-guarded; the graph is
   one level deep today but a silent infinite loop is not worth the saved lines.
   ========================================================================== */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const STYLES = resolve(HERE, "../src/styles");
const DIST = resolve(HERE, "../dist");

// Matches `@import "./x.css";` and `@import url("./x.css");` with either quote.
const IMPORT_RE = /@import\s+(?:url\(\s*)?["']([^"']+)["']\s*\)?\s*;/g;

function inline(file, seen = new Set()) {
  const path = resolve(file);
  if (seen.has(path)) {
    throw new Error(`build-css: circular @import at ${path}`);
  }
  seen.add(path);

  return readFileSync(path, "utf8").replace(IMPORT_RE, (whole, spec) => {
    // Leave remote imports (fonts, CDNs) alone — only local files are inlined.
    if (/^(https?:)?\/\//.test(spec)) return whole;
    const child = resolve(dirname(path), spec);
    return `/* --- inlined from ${spec} --- */\n${inline(child, seen)}`;
  });
}

const banner = (name) =>
  `/* @workshop/ui — ${name}\n` +
  `   Built by packages/ui/scripts/build-css.mjs. Do not edit; edit the sources\n` +
  `   under packages/ui/src/styles/ and rebuild. */\n\n`;

mkdirSync(DIST, { recursive: true });

const artifacts = [
  ["tokens.css", banner("tokens.css") + inline(resolve(STYLES, "tokens.css"))],
  ["styles.css", banner("styles.css") + inline(resolve(STYLES, "components.css"))],
];

for (const [name, css] of artifacts) {
  const out = resolve(DIST, name);
  writeFileSync(out, css);
  console.log(`wrote ${out} (${css.length} bytes)`);
}

// A token file that lost its color layer would fail silently and ship a
// colorless UI, so assert the inline actually pulled the palettes in.
const tokens = readFileSync(resolve(DIST, "tokens.css"), "utf8");
if (!tokens.includes("data-palette")) {
  throw new Error("build-css: dist/tokens.css has no [data-palette] block — the palettes @import did not inline");
}
