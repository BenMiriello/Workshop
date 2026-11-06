import { afterEach, describe, expect, it } from "vitest";
import { APPEARANCE_SCRIPT } from "./appearanceScript";
import { applyPalette, applyTheme } from "./theme";
import { workshopAppearance } from "./vite";

type Listener = (e: { key: string | null }) => void;

// A page with storage, one <html> and a window that keeps its storage
// listeners, enough to run the script the way a browser would.
function page(stored: Record<string, string> = {}) {
  const attrs: Record<string, string> = {};
  const listeners: Listener[] = [];
  const store = { ...stored };
  const env = {
    localStorage: {
      getItem: (k: string) => (k in store ? store[k] : null),
      setItem: (k: string, v: string) => { store[k] = v; },
    },
    document: { documentElement: { setAttribute: (k: string, v: string) => { attrs[k] = v; } } },
    window: { addEventListener: (_: string, fn: Listener) => listeners.push(fn) },
  };
  const run = () =>
    new Function("localStorage", "document", "window", APPEARANCE_SCRIPT)(
      env.localStorage, env.document, env.window);
  const write = (k: string, v: string) => {
    store[k] = v;
    listeners.forEach((fn) => fn({ key: k }));
  };
  return { attrs, env, run, write };
}

afterEach(() => {
  const g = globalThis as Record<string, unknown>;
  delete g.localStorage;
  delete g.document;
});

describe("APPEARANCE_SCRIPT", () => {
  it("paints dark in the default scheme when nothing is stored", () => {
    const p = page();
    p.run();
    expect(p.attrs).toEqual({ "data-theme": "dark", "data-palette": "iris" });
  });

  it("applies a stored theme and scheme, and moves an old scheme id to its successor", () => {
    const p = page({ "ws-theme": "light", "ws-palette": "teal" });
    p.run();
    expect(p.attrs).toEqual({ "data-theme": "light", "data-palette": "lagoon" });
  });

  it("treats an unknown theme as dark", () => {
    const p = page({ "ws-theme": "sepia", "ws-palette": "fern" });
    p.run();
    expect(p.attrs).toEqual({ "data-theme": "dark", "data-palette": "fern" });
  });

  it("follows a change another page writes, and ignores other keys", () => {
    const p = page({ "ws-theme": "dark", "ws-palette": "iris" });
    p.run();
    p.write("ws-theme", "system");
    p.write("ws-palette", "clay");
    expect(p.attrs).toEqual({ "data-theme": "system", "data-palette": "clay" });
    p.attrs["data-theme"] = "untouched";
    p.write("ws-app.notes.draft", "x");
    expect(p.attrs["data-theme"]).toBe("untouched");
  });

  it("reads the keys Settings writes", () => {
    const writer = page();
    Object.assign(globalThis, { localStorage: writer.env.localStorage, document: writer.env.document });
    applyTheme("light");
    applyPalette("slate");
    const reader = page({
      "ws-theme": writer.env.localStorage.getItem("ws-theme") ?? "",
      "ws-palette": writer.env.localStorage.getItem("ws-palette") ?? "",
    });
    reader.run();
    expect(reader.attrs).toEqual({ "data-theme": "light", "data-palette": "slate" });
  });
});

describe("workshopAppearance", () => {
  it("puts the script first in <head>", () => {
    expect(workshopAppearance().transformIndexHtml()).toEqual([
      { tag: "script", children: APPEARANCE_SCRIPT, injectTo: "head-prepend" },
    ]);
  });
});
