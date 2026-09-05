// The catalog read, and the two ways it is allowed to end.
//
// A shell that throws because the app roster could not be read is a shell that
// is down for a reason that has nothing to do with it. `manifests._normalize_
// entry` is tolerant on the server for the same reason and this mirrors it: a
// malformed payload, a 500 and an unreachable endpoint all yield FEWER APPS and
// a `ready` state, never an exception into render.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppDeclaration } from "./contract";

const ENDPOINT = "/api/base-apps/contributions";

function healthy(over: Partial<AppDeclaration> = {}): AppDeclaration {
  return {
    id: "sample-widget",
    contract: 1,
    kit: "*",
    headless: false,
    icon: "sliders",
    link: { label: "Sample", to: "/apps/sample-widget" },
    pages: [{
      path: "/apps/sample-widget",
      mount: "app.route",
      export: "Home",
      crumb: { label: "Sample", parent: "/apps" },
    }],
    urls: { entry: "/base-apps/ui/sample-widget/app.js?v=abc123", styles: [], provider: null },
    error: null,
    ...over,
  } as AppDeclaration;
}

/** A fresh module per case: the store reads once per session on purpose. */
async function readWith(response: unknown, ok = true, status = 200) {
  vi.resetModules();
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok,
    status,
    json: async () => response,
  })));
  const mod = await import("./declarations");
  await mod.ensureDeclarations();
  return mod.declarationState();
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reading the app catalog", () => {
  it("turns a declaration into the catalog shape the shell already consumes", async () => {
    const state = await readWith({ contract: 1, kit: "0.7.9", apps: [healthy()] });
    expect(state.ready).toBe(true);
    expect(state.error).toBeNull();
    expect(state.apps).toHaveLength(1);
    expect(state.apps[0].id).toBe("sample-widget");
    expect(state.apps[0].icon).toBe("sliders");
    expect(state.apps[0].crumbs["/apps/sample-widget"]).toEqual({
      label: "Sample",
      parent: "/apps",
    });
  });

  it("yields an empty catalog rather than throwing on a malformed payload", async () => {
    const state = await readWith({ apps: "not a list" });
    expect(state.apps).toEqual([]);
    expect(state.ready).toBe(true);
  });

  it("names the endpoint when the read fails, so the surface can say which", async () => {
    const state = await readWith(null, false, 503);
    expect(state.apps).toEqual([]);
    expect(state.error).toContain(ENDPOINT);
    expect(state.error).toContain("503");
  });

  it("drops an app that cannot activate, so its build-time copy still renders", async () => {
    // The migration is a sequence, not a flag day: an app whose bundle is not
    // built is not published here, and the shell's own build keeps serving it.
    const state = await readWith({
      contract: 1,
      kit: "0.7.9",
      apps: [healthy({ error: "dist/ui does not exist" }), healthy({ id: "other-widget" })],
    });
    expect(state.apps.map((a) => a.id)).toEqual(["other-widget"]);
  });

  it("keeps a headless app, which contributes an identity and no screens", async () => {
    const state = await readWith({
      contract: 1,
      kit: "0.7.9",
      apps: [healthy({ headless: true, pages: [], urls: { entry: null, styles: [], provider: null } })],
    });
    expect(state.apps).toHaveLength(1);
    expect(Object.keys(state.apps[0].crumbs)).toEqual([]);
  });
});

describe("canActivate", () => {
  it("refuses an app the platform already reported an error for", async () => {
    const { canActivate } = await import("./declarations");
    expect(canActivate(healthy({ error: "kit mismatch" }))).toBe(false);
  });

  it("refuses an app with screens and no built entry", async () => {
    const { canActivate } = await import("./declarations");
    expect(canActivate(healthy({ urls: { entry: null, styles: [], provider: null } }))).toBe(false);
  });

  it("accepts a headless app with no bundle at all", async () => {
    const { canActivate } = await import("./declarations");
    expect(canActivate(healthy({
      headless: true,
      urls: { entry: null, styles: [], provider: null },
    }))).toBe(true);
  });
});
