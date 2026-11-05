import { describe, expect, it } from "vitest";
import { chordFor, parseOverrides } from "./prefs";

const mine = { code: "KeyK", ctrl: true, shift: false, alt: false, meta: false };

describe("parseOverrides", () => {
  it("keeps valid chords and drops the rest, entry by entry", () => {
    const raw = JSON.stringify({ a: mine, b: { code: "KeyX" }, c: "Mod+V" });
    expect(parseOverrides(raw)).toEqual({ a: mine });
  });
  it("reads nothing from bad or missing text", () => {
    expect(parseOverrides(null)).toEqual({});
    expect(parseOverrides("{")).toEqual({});
    expect(parseOverrides("[1]")).toEqual({});
  });
});

describe("chordFor", () => {
  it("prefers the operator's chord to the app's", () => {
    expect(chordFor("a", "Mod+Alt+V", { a: mine }, false)).toEqual(mine);
    expect(chordFor("b", "Mod+Alt+V", { a: mine }, false)?.code).toBe("KeyV");
    expect(chordFor("b", null, {}, false)).toBeNull();
  });
});
