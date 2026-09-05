// THE BLANK-PAGE RULE AS AN EXECUTABLE ASSERTION.
//
// `loadApp` is the shell's only entry point into code the platform did not
// compile, so a rejection escaping it would land in a render as a blank route.
// Every failure below is a resolved value with a sentence in it. None of these
// needs a DOM or a network: they are the refusals that happen BEFORE a fetch,
// which is deliberate, because a version check that arrives after the bundle
// has loaded has checked nothing.
import { describe, expect, it } from "vitest";
import type { AppDeclaration } from "./contract";
import { bundleExport, loadApp } from "./loader";

function declaration(over: Partial<AppDeclaration> = {}): AppDeclaration {
  return {
    id: "sample-widget",
    contract: 1,
    kit: "*",
    headless: false,
    urls: { entry: "/base-apps/ui/sample-widget/app.js?v=abc123", styles: [], provider: null },
    error: null,
    ...over,
  } as AppDeclaration;
}

describe("loadApp never rejects", () => {
  it("resolves a failure when the platform already refused the app", async () => {
    const result = await loadApp(declaration({ error: "its bundle was built for contract 99" }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.detail).toContain("contract 99");
  });

  it("resolves a failure when the app declares screens and ships no bundle", async () => {
    const result = await loadApp(declaration({ urls: { entry: null, styles: [], provider: null } }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("ships no bundle");
  });

  it("says what is wrong in a sentence, not an exception message", async () => {
    const result = await loadApp(declaration({ error: "dist/ui does not exist" }));
    if (result.ok) throw new Error("expected a failure");
    expect(result.reason.endsWith(".")).toBe(true);
    expect(result.reason).not.toContain("Error:");
  });
});

describe("bundleExport", () => {
  const bundle = { contract: 1, exports: { Home: () => null, Setup: () => null } };

  it("finds the export the manifest named", () => {
    const result = bundleExport({ ok: true, bundle }, "Home");
    expect(result.ok).toBe(true);
  });

  it("names what the manifest asked for and what the bundle has", () => {
    const result = bundleExport({ ok: true, bundle }, "Missing");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.detail).toContain("Missing");
      expect(result.detail).toContain("Home, Setup");
    }
  });

  it("passes an earlier failure through rather than reporting a missing export", () => {
    const failure = { ok: false as const, reason: "This app's code could not be loaded." };
    expect(bundleExport(failure, "Home")).toBe(failure);
  });
});
