import { describe, expect, it } from "vitest";
import { panelApp } from "./panel";
import type { OverlayApp } from "./store";

const app = (slug: string, events: string[] = []): OverlayApp =>
  ({ slug, title: slug, url: `/a/${slug}/`, events, shortcut: null, settings: false });

describe("panelApp", () => {
  it("prefers the app that keeps copies", () => {
    expect(panelApp([app("a"), app("b", ["dictated", "copied"])])?.slug).toBe("b");
  });
  it("falls back to the first overlay app", () => {
    expect(panelApp([app("a", ["dictated"]), app("b")])?.slug).toBe("a");
  });
  it("is null with none", () => {
    expect(panelApp([])).toBeNull();
  });
});
