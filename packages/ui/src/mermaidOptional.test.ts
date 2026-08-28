import { describe, expect, it } from "vitest";
import mermaidNotInstalled, { MermaidUnavailableError, diagramNotice } from "./mermaidOptional";

describe("the stand-in for a mermaid that was never installed", () => {
  it("refuses to initialise", () => {
    expect(() => mermaidNotInstalled.initialize()).toThrow(MermaidUnavailableError);
  });

  it("rejects a render rather than resolving to an empty diagram", async () => {
    await expect(mermaidNotInstalled.render()).rejects.toBeInstanceOf(MermaidUnavailableError);
  });

  it("fails the way MermaidDiagram loads it, so the component's catch sees it", async () => {
    // Mirrors loadMermaid().then(m => { m.initialize(...); return m.render(...) }),
    // which is the only call sequence the substitution has to survive.
    const notice = await Promise.resolve(mermaidNotInstalled)
      .then((mermaid) => {
        mermaid.initialize();
        return mermaid.render();
      })
      .then(() => "drawn a diagram it does not have")
      .catch(diagramNotice);
    expect(notice).toBeNull();
  });
});

describe("diagramNotice", () => {
  it("says nothing when the library is simply not installed", () => {
    // Null is the whole point: the fence renders as the plain code block it was
    // before diagrams existed, with no warning about someone else's install.
    expect(diagramNotice(new MermaidUnavailableError())).toBeNull();
  });

  it("reports a diagram that would not parse", () => {
    expect(diagramNotice(new Error("Parse error on line 2"))).toBe("Parse error on line 2");
  });

  it("reports a non-Error rejection rather than dropping it", () => {
    expect(diagramNotice("mermaid threw a string")).toBe("mermaid threw a string");
  });
});
