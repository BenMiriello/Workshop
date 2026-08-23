import { describe, expect, it } from "vitest";
import {
  ancestorIdsFor, breadcrumbsFor, fileIconFor, fileNameOf, folderNodesFor, parentPath, viewModeFor,
} from "./filePath";

describe("fileNameOf", () => {
  it("is the last segment", () => {
    expect(fileNameOf("src/pages/apps/AppFilesTab.tsx")).toBe("AppFilesTab.tsx");
  });

  it("is the whole path when the file is at the root", () => {
    // The header draws parentPath ahead of it, and at the root that is "", so
    // the name has to carry the file on its own.
    expect(fileNameOf("README.md")).toBe("README.md");
  });

  it("is empty for an empty path rather than undefined", () => {
    expect(fileNameOf("")).toBe("");
    expect(fileNameOf("/")).toBe("");
  });
});

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

describe("ancestorIdsFor", () => {
  it("opens nothing for a file at the root", () => {
    expect(ancestorIdsFor("main.py")).toEqual([]);
    expect(ancestorIdsFor("")).toEqual([]);
  });

  it("lists every folder on the way down, outermost first", () => {
    expect(ancestorIdsFor("src/pages/apps/Files.tsx")).toEqual([
      "src",
      "src/pages",
      "src/pages/apps",
    ]);
  });
});

describe("folderNodesFor", () => {
  it("has no folders above a file at the root, so the drill opens at the root", () => {
    expect(folderNodesFor("main.py")).toEqual([]);
    expect(folderNodesFor("")).toEqual([]);
  });

  it("is the file's own folders as tree nodes, outermost first", () => {
    expect(folderNodesFor("src/pages/apps/Files.tsx")).toEqual([
      { id: "src", label: "src", kind: "branch", icon: "folder" },
      { id: "src/pages", label: "pages", kind: "branch", icon: "folder" },
      { id: "src/pages/apps", label: "apps", kind: "branch", icon: "folder" },
    ]);
  });

  it("agrees with the ids the desktop tree opens", () => {
    const path = "a/b/c/d.ts";
    expect(folderNodesFor(path).map((n) => n.id)).toEqual(ancestorIdsFor(path));
  });
});

describe("fileIconFor", () => {
  const code = { language: "Python", profile: "code" as const };
  const prose = { language: "Markdown", profile: "prose" as const };
  const plain = { language: null, profile: "code" as const };

  it("is code when the editor would open it as code", () => {
    expect(fileIconFor("backend/main.py", code)).toBe("code");
  });

  it("is a doc for the prose profile", () => {
    expect(fileIconFor("docs/README.md", prose)).toBe("doc");
  });

  it("is a plain file when nothing claims the extension", () => {
    expect(fileIconFor("build/out.bin", plain)).toBe("file");
    expect(fileIconFor(".gitignore", plain)).toBe("file");
  });

  it("calls out images and data by extension, over the language", () => {
    expect(fileIconFor("assets/logo.svg", { language: "XML", profile: "code" })).toBe("image");
    expect(fileIconFor("a/b/config.yaml", { language: "YAML", profile: "code" })).toBe("data");
    expect(fileIconFor("PHOTO.JPG", plain)).toBe("image");
  });
});

describe("viewModeFor", () => {
  it("is editable when nothing stops it", () => {
    expect(viewModeFor({ editable: true, truncated: false, encoding: "utf-8", writable: true })).toBe("editable");
  });

  it("binary wins even if also reported truncated", () => {
    expect(viewModeFor({ editable: true, truncated: true, encoding: "binary", writable: true })).toBe("binary");
  });

  it("truncated blocks editing on an otherwise-editable file", () => {
    expect(viewModeFor({ editable: true, truncated: true, encoding: "utf-8", writable: true })).toBe("truncated");
  });

  it("readonly when the bytes can't be edited", () => {
    expect(viewModeFor({ editable: false, truncated: false, encoding: "utf-8", writable: true })).toBe("readonly");
  });

  it("readonly when the workspace does not permit a write here", () => {
    expect(viewModeFor({ editable: true, truncated: false, encoding: "utf-8", writable: false })).toBe("readonly");
  });
});
