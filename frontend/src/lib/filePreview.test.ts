import { describe, expect, it } from "vitest";
import { previewKindFor, rawFileUrl } from "./filePreview";

describe("previewKindFor", () => {
  it("shows the image formats a browser draws natively", () => {
    for (const p of ["a.png", "a.jpg", "a.jpeg", "a.gif", "a.webp", "a.svg"]) {
      expect(previewKindFor(p)).toBe("image");
    }
  });

  it("ignores case and reads the last segment of a path", () => {
    expect(previewKindFor("docs/diagrams/Flow.PNG")).toBe("image");
    expect(previewKindFor("docs/spec.PDF")).toBe("pdf");
  });

  it("has no preview for a format that would only draw a broken icon", () => {
    expect(previewKindFor("photo.heic")).toBe("none");
    expect(previewKindFor("scan.tiff")).toBe("none");
  });

  it("has no preview for text, which the editor already shows", () => {
    expect(previewKindFor("main.py")).toBe("none");
    expect(previewKindFor("README.md")).toBe("none");
  });

  it("treats a dotfile's leading dot as a name, not an extension", () => {
    expect(previewKindFor(".gitignore")).toBe("none");
    expect(previewKindFor("src/.png")).toBe("none");
  });

  it("has no preview for a file with no extension at all", () => {
    expect(previewKindFor("Makefile")).toBe("none");
    expect(previewKindFor("")).toBe("none");
  });
});

describe("rawFileUrl", () => {
  it("encodes both parameters, so a space or a slash cannot break out", () => {
    expect(rawFileUrl("app:my-app", "docs/a b.png"))
      .toBe("/api/files/raw?workspace=app%3Amy-app&path=docs%2Fa%20b.png");
  });
});
