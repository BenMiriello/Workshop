import { describe, expect, it } from "vitest";
import { sameIdentity, stableKey } from "./fieldIdentity";

describe("stableKey", () => {
  it("takes the first stable name, in order", () => {
    expect(stableKey({ id: "title", name: "t", placeholder: "Title" })).toBe("id:title");
    expect(stableKey({ name: "q", ariaLabel: "Search" })).toBe("name:q");
    expect(stableKey({ ariaLabel: " Message  text ", placeholder: "Ask" })).toBe("label:Message text");
    expect(stableKey({ placeholder: "Ask anything" })).toBe("hint:Ask anything");
  });

  it("skips ids a framework mints per render", () => {
    expect(stableKey({ id: ":r3:", placeholder: "Ask" })).toBe("hint:Ask");
    expect(stableKey({ id: "radix-12", name: "n" })).toBe("name:n");
  });

  it("is empty when nothing names the field", () => {
    expect(stableKey({})).toBe("");
    expect(stableKey({ id: " ", placeholder: "  " })).toBe("");
  });
});

describe("sameIdentity", () => {
  const a = { path: "/notes/1", key: "label:Body", index: 0 };
  it("matches path, key and index", () => {
    expect(sameIdentity(a, { ...a })).toBe(true);
    expect(sameIdentity(a, { ...a, index: 1 })).toBe(false);
    expect(sameIdentity(a, { ...a, path: "/notes/2" })).toBe(false);
    expect(sameIdentity(a, null)).toBe(false);
  });
});
