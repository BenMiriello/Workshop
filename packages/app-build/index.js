// The build an out-of-tree Workshop app uses, as a config FACTORY rather than a
// copyable template.
//
// A template is a thing every app drifts away from: seven copies of a vite
// config are seven places `cssCodeSplit` can be wrong, and the failure that
// causes (stage-5-app-loader-plan-2026-08-24.md trap B) is invisible in dev,
// in tests and in typecheck, and visible only on the built origin the operator
// actually uses. So an app's whole build config is:
//
//     import { defineWorkshopApp } from "@workshop/app-build";
//     export default defineWorkshopApp();
//
// and everything below is not negotiable per app.
//
// THIS FILE NAMES NO APP. It reads the app's own `workshop.app.json` for what
// it is; nothing here knows or asks which app is being built.

import fs from "node:fs";
import path from "node:path";

/** Every specifier the SHELL publishes at runtime and an app therefore must not
 *  bundle a second copy of.
 *
 *  ONE ARRAY, TWO READERS: this is the `external` list of every app build AND
 *  the key set of the shell's import map (`frontend/plugins/importmap.ts`).
 *  Anything externalized without a map entry fails in the browser on a bare
 *  specifier, which reads like a loader bug and is not one, so the two lists
 *  are the same list.
 *
 *  The membership rule is not "big" or "common" -- it is that a SECOND INSTANCE
 *  IS A BUG rather than a cost. React and the router key their hooks off module
 *  scope state; the query client and the app-host context are React contexts,
 *  and a context object from a second copy reads as undefined; the kit is what
 *  makes "the design system is mandatory" enforceable over code the platform
 *  does not compile. `recharts` and `lucide-react` are here for size, and
 *  because two copies of an icon set in one document is simply waste. */
export const SHARED = [
  "react",
  "react-dom",
  "react-dom/client",
  // The two JSX runtimes are NOT covered by the "react" entry: a compiled .tsx
  // imports `react/jsx-runtime` directly, and an import map matches the whole
  // specifier, never a prefix. Missing these is the single most likely way to
  // ship an app that loads a second React without anything saying so.
  "react/jsx-runtime",
  "react/jsx-dev-runtime",
  "react-router-dom",
  "@tanstack/react-query",
  "lucide-react",
  "recharts",
  "@workshop/ui",
  "@workshop/ui/map",
  "@workshop/ui/markdown",
  "@workshop/ui/editor",
  // Shell-provided services an app page needs and the kit does not own: the
  // app's own id, the sibling-app switcher, and the chat dock renderer. It is
  // a SHELL module published at a runtime URL exactly like react, rather than a
  // kit export, because it is host wiring rather than a design primitive -- and
  // because adding an export to the kit is a version event for every app that
  // declared a `kit` range.
  "@workshop/app-host",
];

/** Stylesheets an app must never import: the shell already loads both, and a
 *  second copy ships the whole design system twice and can shadow the shell's
 *  own token values. The kit is `sideEffects: ["*.css"]`, so this is reachable
 *  by accident from any deep import. */
const FORBIDDEN_CSS = ["@workshop/ui/tokens.css", "@workshop/ui/styles.css"];

/** React's internals export. Its presence in an app's own bundle means React
 *  was bundled rather than externalized, which is two Reacts in one document
 *  and "Invalid hook call" on every page the app renders. */
const REACT_MARKER = "__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED";

const MANIFEST_NAMES = ["workshop.app.json", "workshop.app.jsonc"];

function readManifest(root) {
  for (const name of MANIFEST_NAMES) {
    const p = path.join(root, name);
    if (!fs.existsSync(p)) continue;
    let text = fs.readFileSync(p, "utf8");
    if (name.endsWith("jsonc")) {
      text = text
        .split("\n")
        .filter((line) => !line.trimStart().startsWith("//"))
        .join("\n");
    }
    return { name, data: JSON.parse(text) };
  }
  return null;
}

/** The two build-time refusals, run after the bundle is written.
 *
 *  Both exist because the failure they catch is INVISIBLE LATER: an app whose
 *  stylesheet the manifest does not declare renders unstyled on the built
 *  origin only, and an app that bundled React throws from its first hook. This
 *  is the earliest possible place to say so -- in the app's own build, in the
 *  app's own repo, with the platform not involved. */
function assertions(root, outDir, manifest) {
  return {
    name: "workshop-app-assertions",
    closeBundle() {
      const dir = path.resolve(root, outDir);
      const emitted = fs.readdirSync(dir);

      const css = emitted.filter((f) => f.endsWith(".css"));
      if (css.length > 1) {
        throw new Error(
          `@workshop/app-build: ${css.length} stylesheets in ${outDir} (${css.join(", ")}). ` +
            "The shell links an app's stylesheets by name from its manifest and gets no " +
            "preload helper, so a chunk stylesheet is emitted, served and never requested. " +
            "Build with one.",
        );
      }
      const declared = (manifest?.data?.ui?.styles ?? []).map(String);
      const undeclared = css.filter((f) => !declared.includes(f));
      if (undeclared.length) {
        throw new Error(
          `@workshop/app-build: ${outDir}/${undeclared.join(", ")} is emitted and the ` +
            `manifest's ui.styles does not list it, so the shell will never link it and ` +
            "this app renders unstyled in a build. Add it to ui.styles.",
        );
      }

      const entry = String(manifest?.data?.ui?.entry ?? "app.js");
      const entryPath = path.join(dir, entry);
      if (!fs.existsSync(entryPath)) {
        throw new Error(
          `@workshop/app-build: the manifest declares ui.entry ${entry} and the build ` +
            `emitted ${emitted.join(", ")}. The shell fetches what the manifest names.`,
        );
      }
      if (fs.readFileSync(entryPath, "utf8").includes(REACT_MARKER)) {
        throw new Error(
          "@workshop/app-build: this bundle contains React itself, so the document would " +
            "load a second copy and every hook in this app would throw. React is in " +
            "SHARED and must stay external -- check that the build used this preset.",
        );
      }
    },
  };
}

/** Refuse the two kit stylesheets at resolution time, which names the importing
 *  file rather than leaving a mystery in the emitted CSS. */
function refuseKitCss() {
  return {
    name: "workshop-app-refuse-kit-css",
    enforce: "pre",
    resolveId(source, importer) {
      if (!FORBIDDEN_CSS.includes(source)) return null;
      throw new Error(
        `@workshop/app-build: ${importer ?? "this app"} imports ${source}. The shell ` +
          "already loads the design system's tokens and components; importing them again " +
          "ships a second copy and can shadow the shell's token values.",
      );
    },
  };
}

/**
 * The whole build config for a Workshop app's UI.
 *
 * @param {object} [options]
 * @param {string} [options.root] the app directory (default: the cwd of the build)
 * @param {string} [options.entry] the app's ESM entry (default: `ui/index.ts`)
 * @param {string} [options.outDir] where the built bundle goes, relative to root
 *   (default: `dist/ui`, which is what the manifest's `ui.dir` names)
 * @param {string} [options.providerEntry] a second, tiny entry exporting
 *   `{ Provider }` for an app that needs a session-scoped context. It is
 *   awaited before the routed area first renders, so it must import no CSS and
 *   no page; that is what bounds its cost.
 */
export function defineWorkshopApp(options = {}) {
  const root = path.resolve(options.root ?? process.cwd());
  const outDir = options.outDir ?? "dist/ui";
  // `ui/index.tsx` first: an app's entry usually renders something. Both are
  // tried so an app that declares neither still builds.
  const entry = options.entry ?? (
    fs.existsSync(path.join(root, "ui/index.tsx")) ? "ui/index.tsx" : "ui/index.ts"
  );
  const manifest = readManifest(root);
  const inputs = { app: path.resolve(root, entry) };
  if (options.providerEntry) {
    inputs.provider = path.resolve(root, options.providerEntry);
  }

  return {
    root,
    plugins: [refuseKitCss(), assertions(root, outDir, manifest)],
    // `automatic` rather than a react plugin: the transform an app needs is the
    // JSX one, and doing it in esbuild keeps this preset dependency-free for an
    // app repo that has only vite.
    esbuild: { jsx: "automatic" },
    build: {
      outDir,
      emptyOutDir: true,
      // TRAP B. Vite's default is `true`, and per-chunk CSS is emitted with a
      // preload helper that only exists inside the importing bundle's own
      // rollup graph. The shell fetches this bundle by URL, so it gets no
      // helper: the stylesheet is emitted, is served, and is never requested.
      cssCodeSplit: false,
      // Not `lib`: lib mode is single-entry, and an app may declare a provider
      // entry too. The two are separate ESM entries with fixed names, which is
      // what lets the manifest name stable files and the platform hash them.
      rollupOptions: {
        input: inputs,
        external: SHARED,
        // WITHOUT THIS THE BUNDLE HAS NO EXPORTS. Vite builds an app, not a
        // library, so its default is `preserveEntrySignatures: false`: an entry
        // is an executable start point and its exports are tree-shaken away.
        // Measured 2026-09-05 -- the first fixture build emitted four
        // side-effect imports and nothing else, which loads fine and provides
        // no screens.
        preserveEntrySignatures: "strict",
        output: {
          format: "es",
          entryFileNames: "[name].js",
          chunkFileNames: "[name].js",
          assetFileNames: "app.[ext]",
          // One file per entry. The shell imports one URL; a code-split app
          // would need the preload helper it does not get.
          inlineDynamicImports: !options.providerEntry,
        },
      },
    },
  };
}

export default defineWorkshopApp;
