import { describe, expect, it } from "vitest";
import { Pieces, withoutPiece } from "./pieces";

const at = (name: string) => ({ kind: "field" as const, name, path: "/x" });

describe("Pieces", () => {
  it("joins words said into one place into one piece", () => {
    const p = new Pieces<null>();
    p.add("Hello there.", "a", at("A"), null, "placed");
    p.add("How are you?", "a", at("A"), null, "placed");
    expect(p.list).toHaveLength(1);
    expect(p.list[0].text).toBe("Hello there. How are you?");
  });

  it("starts a new piece when the place changes, after a cut, and after a send", () => {
    const p = new Pieces<null>();
    p.add("one", "a", at("A"), null, "placed");
    p.add("two", "b", at("B"), null, "placed");
    p.cut();
    p.add("three", "b", at("B"), null, "placed");
    p.sent("b");
    p.add("four", "b", at("B"), null, "placed");
    expect(p.list.map((x) => [x.text, x.state])).toEqual([
      ["one", "placed"], ["two", "placed"], ["three", "sent"], ["four", "placed"],
    ]);
  });

  it("starts a new piece when the state changes in one place", () => {
    const p = new Pieces<null>();
    p.add("one", "a", at("A"), null, "placed");
    p.add("two", "a", at("A"), null, "waiting");
    expect(p.list).toHaveLength(2);
    expect(p.mark("a", "waiting", "unplaced")).toBe(true);
    expect(p.loose).toBe("two");
  });

  it("drops a scratched phrase from the newest piece of that place", () => {
    const p = new Pieces<null>();
    p.add("Keep this. Drop this.", "a", at("A"), null, "placed");
    p.add("Elsewhere.", "b", at("B"), null, "placed");
    p.dropTail("a", "Drop this.");
    expect(p.list[0].text).toBe("Keep this.");
    expect(p.words).toBe("Keep this. Elsewhere.");
  });

  it("ignores empty words", () => {
    const p = new Pieces<null>();
    expect(p.add("  ", "a", at("A"), null, "placed")).toBeNull();
    expect(p.list).toHaveLength(0);
  });
});

describe("withoutPiece", () => {
  it("takes the last copy out with its joining space", () => {
    expect(withoutPiece("Hi. Hello there. More", "Hello there.")).toBe("Hi. More");
    expect(withoutPiece("Hello there. More", "Hello there.")).toBe("More");
    expect(withoutPiece("a b a", "a")).toBe("a b");
  });

  it("is null when the words are no longer there as dictated", () => {
    expect(withoutPiece("Hello their.", "Hello there.")).toBeNull();
    expect(withoutPiece("x", "")).toBeNull();
  });
});
