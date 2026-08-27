import { describe, expect, it } from "vitest";
import { mermaidSourceOf } from "./mermaidFence";

const text = (value: string) => ({ type: "text", value });
const code = (className: unknown, body: string) => ({
  type: "element",
  tagName: "code",
  properties: { className },
  children: [text(body)],
});
const pre = (...children: unknown[]) => ({
  type: "element",
  tagName: "pre",
  properties: {},
  children,
});

describe("mermaidSourceOf", () => {
  it("reads the source out of a mermaid fence", () => {
    expect(mermaidSourceOf(pre(code(["language-mermaid"], "graph TD\n  a-->b\n"))))
      .toBe("graph TD\n  a-->b");
  });

  it("accepts a string className, which some trees carry instead of an array", () => {
    expect(mermaidSourceOf(pre(code("language-mermaid hljs", "pie\n")))).toBe("pie");
  });

  it("leaves every other language alone", () => {
    expect(mermaidSourceOf(pre(code(["language-ts"], "const a = 1")))).toBeNull();
    expect(mermaidSourceOf(pre(code(undefined, "plain")))).toBeNull();
  });

  it("does not match a language that merely starts the same way", () => {
    expect(mermaidSourceOf(pre(code(["language-mermaidjs"], "graph TD")))).toBeNull();
  });

  it("ignores whitespace text nodes around the code element", () => {
    expect(mermaidSourceOf(pre(text("\n"), code(["language-mermaid"], "graph TD"), text("\n"))))
      .toBe("graph TD");
  });

  it("is null for an empty fence, so it renders as the empty code block it is", () => {
    expect(mermaidSourceOf(pre(code(["language-mermaid"], "   \n ")))).toBeNull();
  });

  it("is null for anything that is not a pre wrapping exactly one code element", () => {
    expect(mermaidSourceOf(null)).toBeNull();
    expect(mermaidSourceOf(undefined)).toBeNull();
    expect(mermaidSourceOf(code(["language-mermaid"], "graph TD"))).toBeNull();
    expect(mermaidSourceOf(pre(text("graph TD")))).toBeNull();
    expect(mermaidSourceOf(
      pre(code(["language-mermaid"], "graph TD"), code(["language-mermaid"], "graph TD")),
    )).toBeNull();
  });
});
