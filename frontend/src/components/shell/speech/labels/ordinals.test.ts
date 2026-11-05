import { describe, expect, it } from "vitest";
import { kindWordIn, kindWordOf, parsePlace, pickPlace, placeName } from "./ordinals";
import type { TargetKind } from "./targetKinds";

describe("parsePlace", () => {
  it("reads ordinals and a kind", () => {
    expect(parsePlace("the second image")).toMatchObject({ ordinal: 2, kind: "image", region: null, rest: "" });
    expect(parsePlace("open the 3rd picture")).toMatchObject({ ordinal: 3, kind: "image", rest: "open" });
    expect(parsePlace("the last link")).toMatchObject({ ordinal: -1, kind: "link" });
    expect(parsePlace("the next to last tab")).toMatchObject({ ordinal: -2, kind: "tab" });
    expect(parsePlace("second to last result")).toMatchObject({ ordinal: -2, kind: "item" });
    expect(parsePlace("the third last button")).toMatchObject({ ordinal: -3, kind: "button" });
  });

  it("reads regions of the screen", () => {
    expect(parsePlace("the button at the top right")).toMatchObject({ ordinal: null, kind: "button", region: { x: 1, y: 0 } });
    expect(parsePlace("bottom left corner")).toMatchObject({ region: { x: 0, y: 1 } });
    expect(parsePlace("the top one")).toMatchObject({ region: { x: null, y: 0 }, kind: "item" });
    expect(parsePlace("the link in the middle")).toMatchObject({ region: { x: 0.5, y: 0.5 } });
  });

  it("keeps the name said with a place", () => {
    expect(parsePlace("delete on the third one")).toMatchObject({ ordinal: 3, kind: "item", rest: "delete" });
    expect(parsePlace("the second sign out button")).toMatchObject({ ordinal: 2, kind: "button", rest: "sign out" });
  });

  it("is null without an ordinal or a region", () => {
    expect(parsePlace("the image")).toBeNull();
    expect(parsePlace("send")).toBeNull();
    expect(parsePlace("")).toBeNull();
  });
});

describe("kindWordIn", () => {
  it("finds the kind a name is said with", () => {
    expect(kindWordIn("the delete button")).toBe("button");
    expect(kindWordIn("usage link")).toBe("link");
    expect(kindWordIn("the blue one")).toBeNull();
  });
});

type T = { n: number; kind: TargetKind; image: boolean; box: { left: number; top: number; width: number; height: number } };
const at = (n: number, kind: TargetKind, left: number, top: number, image = false): T =>
  ({ n, kind, image, box: { left, top, width: 20, height: 20 } });
const view = { width: 1000, height: 800 };
const page = [
  at(1, "button", 10, 10), at(2, "link", 400, 10), at(3, "button", 960, 10),
  at(4, "link", 100, 380, true), at(5, "link", 490, 380, true), at(6, "link", 880, 380, true),
  at(7, "button", 10, 760), at(8, "link", 500, 760), at(9, "button", 960, 760),
];

describe("pickPlace", () => {
  it("counts among the kind in reading order", () => {
    expect(pickPlace(page, parsePlace("the second image")!, view)?.n).toBe(5);
    expect(pickPlace(page, parsePlace("the last link")!, view)?.n).toBe(8);
    expect(pickPlace(page, parsePlace("the next to last button")!, view)?.n).toBe(7);
    expect(pickPlace(page, parsePlace("the first one")!, view)?.n).toBe(1);
    expect(pickPlace(page, parsePlace("the fifth image")!, view)).toBeNull();
  });

  it("takes the one nearest a corner or an edge", () => {
    expect(pickPlace(page, parsePlace("the button at the top right")!, view)?.n).toBe(3);
    expect(pickPlace(page, parsePlace("bottom left")!, view)?.n).toBe(7);
    expect(pickPlace(page, parsePlace("the top link")!, view)?.n).toBe(2);
    expect(pickPlace(page, parsePlace("the picture in the middle")!, view)?.n).toBe(5);
  });

  it("counts inside a region when both are said", () => {
    expect(pickPlace(page, parsePlace("the second button at the bottom")!, view)?.n).toBe(9);
  });

  it("finds nothing when nothing is there", () => {
    expect(pickPlace(page, parsePlace("the tab at the top")!, view)).toBeNull();
  });
});

describe("placeName", () => {
  it("says an unnamed target by its place among its kind, and is read back to it", () => {
    expect(placeName(kindWordOf({ kind: "button", image: true }), 3)).toBe("third image");
    expect(placeName(kindWordOf({ kind: "select", image: false }), 1)).toBe("first field");
    expect(parsePlace(placeName("link", 2))).toMatchObject({ ordinal: 2, kind: "link" });
  });

  it("says nothing past tenth, or for a kind no word picks out", () => {
    expect(placeName("button", 11)).toBe("");
    expect(placeName(kindWordOf({ kind: "menu item", image: false }), 1)).toBe("");
  });
});
