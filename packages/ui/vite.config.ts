import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

// Library build for @workshop/ui. Emits ESM + a self-contained token module to
// dist/; declarations come from tsconfig.build.json and the two CSS artifacts
// from scripts/build-css.mjs (both run by `npm run build`).
//
// EVERYTHING a consumer already has is external. The kit ships its own code and
// nothing else: bundling react or lucide here would give a consuming app two
// copies of React and break hooks. The heavy optional peers (maplibre, pmtiles,
// protomaps, react-markdown, remark-gfm) are external for the same reason and
// are only reachable through the ./map and ./markdown subpaths.
const EXTERNAL = [
  /^react($|\/)/,
  /^react-dom($|\/)/,
  /^lucide-react($|\/)/,
  /^maplibre-gl($|\/)/,
  /^pmtiles($|\/)/,
  /^protomaps-themes-base($|\/)/,
  /^react-markdown($|\/)/,
  /^remark-gfm($|\/)/,
];

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    // The kit's own primitives carry no CSS imports (styling lives in the
    // shipped stylesheets), so there is no chunk-level CSS to split.
    cssCodeSplit: false,
    lib: {
      entry: {
        index: resolve(HERE, "src/index.ts"),
        Map: resolve(HERE, "src/Map.tsx"),
        Markdown: resolve(HERE, "src/Markdown.tsx"),
        tokens: resolve(HERE, "src/styles/tokens.js"),
      },
      formats: ["es"],
    },
    rollupOptions: {
      external: EXTERNAL,
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "shared/[name]-[hash].js",
        // Keep the shared internals (cx, status, theme) as real chunks rather
        // than duplicating them into every entry.
        preserveModules: false,
      },
    },
  },
});
