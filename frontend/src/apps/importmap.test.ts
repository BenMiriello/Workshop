// TRAP A, EXPRESSED AS STRING COMPARISONS.
//
// An app bundle resolves `react` through the import map in index.html. If a map
// value points into `/node_modules/`, the browser fetches a URL the shell does
// not use -- in dev that path also carries a `?v=` re-optimization token that
// changes -- and evaluates a SECOND React with identical bytes. Every hook in
// that app then throws "Invalid hook call", in dev only, which is the hardest
// kind of difference to notice: it is invisible in a build, in typecheck and in
// every other test here.
//
// So the assertions below are cheap on purpose. They run in the ordinary
// `npm test`, need no browser, and the one that matters most is a substring
// check.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { SHARED } from "@workshop/app-build";
import {
  builtImportMap,
  devImportMap,
  sharedEntryFile,
  sharedEntryName,
  sharedInputs,
} from "../../plugins/importmap";

const FRONTEND = path.resolve(__dirname, "../..");

/** Both modes, so an assertion cannot pass by only being true of one. */
const MAPS = {
  serve: devImportMap().imports,
  build: builtImportMap((name) => `assets/${name}-abc12345.js`).imports,
};

describe("the import map publishes the shell's runtime", () => {
  for (const [mode, imports] of Object.entries(MAPS)) {
    it(`covers every shared specifier in ${mode} mode`, () => {
      expect(Object.keys(imports).sort()).toEqual([...SHARED].sort());
    });

    it(`maps the jsx runtimes explicitly in ${mode} mode`, () => {
      // An import map matches a WHOLE specifier, never a prefix, so the `react`
      // entry does not cover these -- and a compiled .tsx imports them
      // directly. Missing them is the likeliest way to ship a second React.
      expect(imports["react/jsx-runtime"]).toBeTruthy();
      expect(imports["react/jsx-dev-runtime"]).toBeTruthy();
    });

    it(`never points at node_modules in ${mode} mode`, () => {
      for (const [spec, url] of Object.entries(imports)) {
        expect(url, spec).not.toContain("/node_modules/");
      }
    });

    it(`carries no re-optimization token in ${mode} mode`, () => {
      for (const [spec, url] of Object.entries(imports)) {
        expect(url, spec).not.toContain("?v=");
      }
    });
  }

  it("points at shell source files that exist, in serve mode", () => {
    for (const [spec, url] of Object.entries(MAPS.serve)) {
      expect(fs.existsSync(path.join(FRONTEND, url.replace(/^\//, ""))), spec).toBe(true);
    }
  });

  it("refuses to emit a map with a specifier the build did not emit", () => {
    // A missing entry chunk would otherwise become a bare specifier in an app
    // bundle, which fails in the browser and reads like a loader bug.
    expect(() => builtImportMap(() => undefined)).toThrow(/emitted no entry chunk/);
  });
});

describe("the shared entries the build has to emit", () => {
  it("declares one rollup input per shared specifier", () => {
    const inputs = sharedInputs();
    expect(Object.keys(inputs).sort()).toEqual(SHARED.map(sharedEntryName).sort());
    for (const [name, file] of Object.entries(inputs)) {
      expect(fs.existsSync(file), name).toBe(true);
    }
  });

  it("turns a scoped or subpath specifier into one filename", () => {
    expect(sharedEntryFile("react/jsx-runtime")).toBe("src/runtime/shared/react-jsx-runtime.js");
    expect(sharedEntryFile("@workshop/ui/map")).toBe("src/runtime/shared/workshop-ui-map.js");
    expect(sharedEntryFile("@workshop/app-host")).toBe("src/runtime/shared/workshop-app-host.tsx");
  });

  it("says which module is missing when SHARED grows without one", () => {
    expect(() => sharedEntryFile("not-a-shared-package")).toThrow(/no module publishes it/);
  });

  it("guards the shared runtime from every published module", () => {
    // The guard is what turns "Invalid hook call" into a sentence naming the
    // cause. A published module that forgets to import it is a hole in that.
    for (const spec of SHARED) {
      const source = fs.readFileSync(path.join(FRONTEND, sharedEntryFile(spec)), "utf8");
      expect(source, spec).toContain('import "./guard"');
    }
  });
});
