// THE SHELL PUBLISHES ITS RUNTIME, as one import map in one document.
//
// An app's bundle is built somewhere else, against `external: SHARED`
// (@workshop/app-build), so it reaches the browser with bare specifiers still
// in it: `import { useState } from "react"`. A browser resolves those only
// through an import map, and this plugin is what puts one in index.html --
// pointing every shared specifier at a URL the SHELL owns and already loads.
//
// Two properties are load-bearing and both are asserted in
// `src/apps/importmap.test.ts`:
//
//   ONE MAP. Multiple import maps in one document are not supported in Firefox
//   and external maps (`src=`) are not standardised anywhere, so this injects a
//   single inline map, head-first, before any module script.
//
//   NO `/node_modules/` VALUE, EVER. This is the whole of trap A. The browser
//   keys modules by URL, and in dev the shell imports React as
//   `/node_modules/.vite/deps/react.js?v=<token>` where the token changes on
//   re-optimization. A map pointing at that path without the token is a SECOND
//   URL, a second module instance and a second React, and every hook in the app
//   throws -- in dev only, which is the hardest kind of difference to notice.
//   Pointing at a shell-owned module (`/src/runtime/shared/react.ts` in dev,
//   `/assets/shared-react-<hash>.js` in a build) means there is exactly one URL
//   for `react` in the browser's module map, in both modes, and the token
//   problem cannot arise because no map value contains one.
import fs from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";
import { SHARED } from "@workshop/app-build";

// WHERE THE SHELL IS, found rather than assumed. `__dirname` is not usable
// here: vite bundles a config and its local imports into ONE temporary module
// beside the config file, so a path relative to this file resolves relative to
// `frontend/` in a build and to nothing at all under vitest's ESM loader. Both
// callers run with the cwd at the repo root or at `frontend/`, so look for the
// directory that actually holds the shared modules.
function frontendRoot(): string {
  let dir = process.cwd();
  for (let i = 0; i < 5; i++) {
    if (fs.existsSync(path.join(dir, "src", "runtime", "shared"))) return dir;
    if (fs.existsSync(path.join(dir, "frontend", "src", "runtime", "shared"))) {
      return path.join(dir, "frontend");
    }
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  throw new Error(
    "importmap: could not find frontend/src/runtime/shared from " + process.cwd(),
  );
}

const SHARED_DIR = () => path.join(frontendRoot(), "src", "runtime", "shared");

/** The file base name that publishes `spec`. A specifier is not a filename:
 *  `react/jsx-runtime` and `@workshop/ui/map` both have to become one. */
export function sharedFileBase(spec: string): string {
  return spec.replace(/^@/, "").replace(/\//g, "-");
}

/** The rollup entry name for `spec`. Prefixed so it cannot collide with the
 *  html entry or with a chunk vite names after a source file. */
export function sharedEntryName(spec: string): string {
  return `shared-${sharedFileBase(spec)}`;
}

/** The shell source file that publishes `spec`, relative to `frontend/`. */
export function sharedEntryFile(spec: string): string {
  const base = sharedFileBase(spec);
  // `.js` is first and is what most of these are: a re-export shim has no
  // types of its own, and `export * from "react"` is a TYPE error against
  // @types/react (which uses `export =`) while being exactly right at runtime.
  // Writing them as .js keeps tsc out of a file that only exists for rollup.
  for (const ext of [".js", ".ts", ".tsx"]) {
    if (fs.existsSync(path.join(SHARED_DIR(), base + ext))) {
      return `src/runtime/shared/${base}${ext}`;
    }
  }
  throw new Error(
    `importmap: ${spec} is in SHARED and no module publishes it. Add ` +
      `frontend/src/runtime/shared/${base}.js (see the files beside it) or drop ` +
      "it from SHARED in @workshop/app-build -- the two lists are one list.",
  );
}

/** `rollupOptions.input` additions: one entry per shared specifier.
 *
 *  ONE ROLLUP BUILD, SEVERAL ENTRIES, deliberately. A module imported by two
 *  entries is hoisted into a chunk both import, so `src/main.tsx` and
 *  `src/runtime/shared/react.ts` end up importing the same react chunk: one
 *  instance, and the re-export entry is a stable public URL for it. Note what
 *  is absent: `manualChunks`. Splitting React out by hand is how the shared
 *  chunk stops being shared. */
export function sharedInputs(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const spec of SHARED) {
    out[sharedEntryName(spec)] = path.join(frontendRoot(), sharedEntryFile(spec));
  }
  return out;
}

/** The map itself, given a way to turn a specifier into a URL. Pure, so the
 *  test can generate both modes without running a build. */
export function importMap(urlFor: (spec: string) => string): { imports: Record<string, string> } {
  const imports: Record<string, string> = {};
  for (const spec of SHARED) imports[spec] = urlFor(spec);
  return { imports };
}

/** Dev URLs: the shell's own source modules, served by vite at their real path. */
export function devImportMap(base = "/"): { imports: Record<string, string> } {
  return importMap((spec) => base + sharedEntryFile(spec));
}

/** Build URLs: the content-hashed entry chunk rollup emitted for each. */
export function builtImportMap(
  fileNameFor: (entryName: string) => string | undefined,
  base = "/",
): { imports: Record<string, string> } {
  return importMap((spec) => {
    const name = sharedEntryName(spec);
    const file = fileNameFor(name);
    if (!file) {
      throw new Error(
        `importmap: the build emitted no entry chunk named ${name}, so ${spec} would ` +
          "have no URL and every app bundle importing it would fail on a bare specifier. " +
          "Check build.rollupOptions.input in vite.config.ts.",
      );
    }
    return base + file;
  });
}

export function importmapPlugin(): Plugin {
  let base = "/";
  return {
    name: "workshop-importmap",
    configResolved(config) {
      base = config.base || "/";
    },
    transformIndexHtml: {
      // `post` so a build's hook receives the finished bundle and can therefore
      // name content-hashed files. In serve there is no bundle and the dev URLs
      // are the source paths.
      order: "post",
      handler(html, ctx) {
        const map = ctx.bundle
          ? builtImportMap((name) => {
              for (const out of Object.values(ctx.bundle!)) {
                if (out.type === "chunk" && out.isEntry && out.name === name) {
                  return out.fileName;
                }
              }
              return undefined;
            }, base)
          : devImportMap(base);
        return {
          html,
          tags: [
            {
              tag: "script",
              attrs: { type: "importmap" },
              children: JSON.stringify(map, null, 2),
              // Before every module script in the document. A map is only
              // consulted for imports that start resolving after it is parsed.
              injectTo: "head-prepend",
            },
          ],
        };
      },
    },
  };
}
