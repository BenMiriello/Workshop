import { describe, it, expect } from "vitest";
import { languageForPath } from "./editorProfile";

// The path -> language + profile table, executed. This is the whole decidable
// part of the editor: everything else needs a browser, and the repo has no DOM
// harness (frontend/CLAUDE.md). Section 4.1 of
// docs/platform-redesign/file-viewer-and-editor-2026-08-22.md is the spec.

describe("a memory document gets the prose profile", () => {
  it("markdown is prose, with the markdown grammar", () => {
    expect(languageForPath("docs/notes.md")).toEqual({ language: "Markdown", profile: "prose" });
    expect(languageForPath("README.markdown").profile).toBe("prose");
  });

  it("plain text is prose with no grammar", () => {
    // Soft wrap and no line numbers are right for a .txt; running the markdown
    // parser over it would invent a heading from any line starting with a hash.
    expect(languageForPath("notes.txt")).toEqual({ language: null, profile: "prose" });
  });
});

describe("source code gets the code profile and its own language", () => {
  it.each([
    ["main.py", "Python"],
    ["src/components/Editor.tsx", "TSX"],
    ["src/lib/api.ts", "TypeScript"],
    ["vite.config.js", "JavaScript"],
    ["package.json", "JSON"],
    ["styles/tokens.css", "CSS"],
    ["infra/compose.yml", "YAML"],
    ["scripts/apply.sh", "Shell"],
    ["src/main.rs", "Rust"],
  ])("%s -> %s", (path, language) => {
    expect(languageForPath(path)).toEqual({ language, profile: "code" });
  });

  it("tells the JSX family apart", () => {
    // .tsx and .ts are different grammars, and getting this wrong shows up as
    // every JSX tag highlighted as a comparison operator.
    expect(languageForPath("a.tsx").language).toBe("TSX");
    expect(languageForPath("a.ts").language).toBe("TypeScript");
    expect(languageForPath("a.jsx").language).toBe("JSX");
  });
});

describe("an unknown file still opens", () => {
  it("an extension nothing claims is plain text, and does not throw", () => {
    expect(languageForPath("data/blob.qzx")).toEqual({ language: null, profile: "code" });
    expect(languageForPath("archive.tar.zst").language).toBeNull();
  });

  it("a file with no extension at all is plain text", () => {
    expect(languageForPath("bin/workshop")).toEqual({ language: null, profile: "code" });
  });

  it("an empty path does not throw", () => {
    expect(() => languageForPath("")).not.toThrow();
    expect(languageForPath("")).toEqual({ language: null, profile: "code" });
    expect(languageForPath("/")).toEqual({ language: null, profile: "code" });
  });

  it("a name that collides with Object's prototype is still a file", () => {
    // `table[key]` would answer these out of the prototype chain and hand back a
    // function where a PathLanguage belongs.
    expect(languageForPath("constructor")).toEqual({ language: null, profile: "code" });
    expect(languageForPath("x.toString")).toEqual({ language: null, profile: "code" });
  });
});

describe("a dotfile is a name, not an extension", () => {
  it(".gitignore is not a file of type gitignore", () => {
    // The leading dot is the only dot, so there is no extension to look up.
    expect(languageForPath(".gitignore")).toEqual({ language: null, profile: "code" });
    expect(languageForPath("frontend/.gitignore").profile).toBe("code");
  });

  it("a dotfile the table names gets its language", () => {
    expect(languageForPath(".env").language).toBe("Properties files");
  });

  it("an unknown dotfile is plain text rather than a lookup of its own name", () => {
    expect(languageForPath(".mysteryrc")).toEqual({ language: null, profile: "code" });
  });

  it("a dotted name with a real extension still resolves by extension", () => {
    expect(languageForPath(".eslintrc.json").language).toBe("JSON");
  });
});

describe("the table is case- and path-insensitive in the right ways", () => {
  it("extensions match regardless of case", () => {
    expect(languageForPath("NOTES.MD")).toEqual({ language: "Markdown", profile: "prose" });
    expect(languageForPath("Main.PY").language).toBe("Python");
  });

  it("only the last path segment decides", () => {
    expect(languageForPath("some.md.dir/main.py").language).toBe("Python");
    expect(languageForPath("/abs/path/to/Dockerfile").language).toBe("Dockerfile");
    expect(languageForPath("a/b/c/").language).toBeNull();
  });
});
