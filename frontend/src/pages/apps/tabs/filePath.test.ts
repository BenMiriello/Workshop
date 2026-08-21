import { describe, expect, it } from "vitest";
import { breadcrumbsFor, parentPath, viewModeFor } from "./filePath";

describe("breadcrumbsFor", () => {
  it("is empty at the root", () => {
    expect(breadcrumbsFor("")).toEqual([]);
  });

  it("accumulates each segment's full path", () => {
    expect(breadcrumbsFor("src/pages/apps")).toEqual([
      { label: "src", path: "src" },
      { label: "pages", path: "src/pages" },
      { label: "apps", path: "src/pages/apps" },
    ]);
  });

  it("tolerates a leading, trailing or doubled slash", () => {
    expect(breadcrumbsFor("/src//pages/")).toEqual([
      { label: "src", path: "src" },
      { label: "pages", path: "src/pages" },
    ]);
  });
});

describe("parentPath", () => {
  it("stays at the root from the root", () => {
    expect(parentPath("")).toBe("");
  });

  it("goes up one segment", () => {
    expect(parentPath("src/pages/apps")).toBe("src/pages");
    expect(parentPath("src")).toBe("");
  });
});

describe("viewModeFor", () => {
  it("is editable when nothing stops it", () => {
    expect(viewModeFor({ editable: true, truncated: false, encoding: "utf-8" })).toBe("editable");
  });

  it("binary wins even if also reported truncated", () => {
    expect(viewModeFor({ editable: true, truncated: true, encoding: "binary" })).toBe("binary");
  });

  it("truncated blocks editing on an otherwise-editable file", () => {
    expect(viewModeFor({ editable: true, truncated: true, encoding: "utf-8" })).toBe("truncated");
  });

  it("readonly when the backend says so", () => {
    expect(viewModeFor({ editable: false, truncated: false, encoding: "utf-8" })).toBe("readonly");
  });
});
