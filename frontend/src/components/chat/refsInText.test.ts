import { describe, it, expect } from "vitest";
import { extractRefs } from "./refsInText";

describe("extractRefs", () => {
  it("pulls a ref out of a markdown link href", () => {
    expect(extractRefs("captured it in [PROJECT.md](workshop:doc/PROJECT.md) today")).toEqual([
      "workshop:doc/PROJECT.md",
    ]);
  });

  it("does not swallow the closing paren or a trailing period", () => {
    expect(extractRefs("see [t](workshop:doc/docs/a.md).")).toEqual(["workshop:doc/docs/a.md"]);
  });

  it("finds a bare token in prose", () => {
    expect(extractRefs("filed workshop:ticket/abc123 for you")).toEqual(["workshop:ticket/abc123"]);
  });

  it("finds several kinds and de-duplicates", () => {
    const out = extractRefs(
      "workshop:note/workshop/n1 and workshop:doc/PROJECT.md and workshop:note/workshop/n1",
    );
    expect(out).toEqual(["workshop:note/workshop/n1", "workshop:doc/PROJECT.md"]);
  });

  it("returns nothing for text with no refs", () => {
    expect(extractRefs("just a plain sentence about /tmp/x/y.py")).toEqual([]);
  });
});
