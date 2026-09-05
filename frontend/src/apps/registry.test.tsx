// The shell's route table is assembled from the app registry at runtime, so a
// registry that produces something React Router cannot mount is a blank screen
// that typechecks. Render it for real and assert each app path resolves.
//
// Server rendering, no DOM: this stays inside the "pure logic, no DOM harness"
// rule while still exercising the actual router.

import { beforeAll, describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { APPS, AppSuspense, appCrumbs, appGroups, appRoutes } from "./registry";
import { declareTestApp } from "./declareForTest";

const NO_MATCH = "data-nomatch";

function renderAt(path: string): string {
  return renderToString(
    <MemoryRouter initialEntries={[path]}>
      {/* The shell wraps the route table in exactly this boundary, because app
          pages load lazily. Without it a matched route throws instead of
          rendering, which is what the shell would do too. */}
      <AppSuspense>
        <Routes>
          {appRoutes()}
          <Route path="*" element={<i data-nomatch="1" />} />
        </Routes>
      </AppSuspense>
    </MemoryRouter>,
  );
}

// THE CATALOG IS A RUNTIME READ NOW, so under vitest it starts empty and this
// suite would assert nothing at all. One fictional app is published through the
// real declaration path first; see `declareForTest`.
beforeAll(async () => {
  await declareTestApp();
});

describe("the app registry mounts", () => {
  it("resolves every route each app declares", () => {
    // Every crumb key is a path the app says it owns, so the crumbs double as
    // the list of paths that must not fall through to the catch-all.
    const paths = APPS.flatMap((a) => Object.keys(a.crumbs));
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(renderAt(path), path).not.toContain(NO_MATCH);
    }
  });

  it("falls through for a path no app claims", () => {
    expect(renderAt("/apps/nothing-here")).toContain(NO_MATCH);
  });

  it("gives every app a distinct id", () => {
    const ids = APPS.map((a) => a.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("merges crumbs without collisions", () => {
    const declared = APPS.reduce((n, a) => n + Object.keys(a.crumbs).length, 0);
    expect(Object.keys(appCrumbs()).length).toBe(declared);
  });

  it("only groups apps that have a sibling to switch to", () => {
    for (const group of appGroups()) expect(group.length).toBeGreaterThan(1);
  });
});
