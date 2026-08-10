import { describe, it, expect } from "vitest";
import {
  collapse, decode, encode, enabledCount, isEnabled, selectionSummary, toggle,
  type McpSelection,
} from "./mcpSelection";

const ALL_SERVERS = ["plaid", "playwright", "google-docs", "plugin:discord:discord"];

describe("the default is every server", () => {
  it("treats an absent selection as everything on", () => {
    for (const name of ALL_SERVERS) expect(isEnabled(null, name)).toBe(true);
    expect(enabledCount(null, ALL_SERVERS)).toBe(4);
  });

  it("does NOT treat an absent selection as nothing on", () => {
    // The regression that would strip the tools off every existing chat.
    expect(isEnabled(null, "plaid")).not.toBe(false);
  });

  it("keeps an empty selection meaning none", () => {
    expect(isEnabled([], "plaid")).toBe(false);
    expect(enabledCount([], ALL_SERVERS)).toBe(0);
  });
});

describe("toggling", () => {
  it("turns one off out of the default, listing the rest", () => {
    const next = toggle(null, "plaid", ALL_SERVERS);
    expect(next).not.toBeNull();
    expect(new Set(next!)).toEqual(new Set(["playwright", "google-docs", "plugin:discord:discord"]));
  });

  it("collapses back to all when the last one is turned on again", () => {
    const off = toggle(null, "plaid", ALL_SERVERS);
    expect(toggle(off, "plaid", ALL_SERVERS)).toBeNull();
  });

  it("reaches none by turning every one off", () => {
    let sel: McpSelection = null;
    for (const n of ALL_SERVERS) sel = toggle(sel, n, ALL_SERVERS);
    expect(sel).toEqual([]);
  });

  it("does not duplicate a name toggled twice through the list", () => {
    const sel = toggle(toggle([], "plaid", ALL_SERVERS), "playwright", ALL_SERVERS);
    expect(sel).toEqual(["plaid", "playwright"]);
  });
});

describe("collapse", () => {
  it("reads a full list as all", () => {
    expect(collapse([...ALL_SERVERS], ALL_SERVERS)).toBeNull();
  });

  it("keeps a real subset", () => {
    expect(collapse(["plaid"], ALL_SERVERS)).toEqual(["plaid"]);
  });

  it("does not collapse when a known server is missing", () => {
    expect(collapse(ALL_SERVERS.slice(1), ALL_SERVERS)).not.toBeNull();
  });

  it("ignores a name the machine no longer has", () => {
    // A stale selection must not be read as covering everything just because
    // it is long.
    expect(collapse(["plaid", "ghost"], ALL_SERVERS)).toEqual(["plaid", "ghost"]);
  });
});

describe("summary", () => {
  it("says the total either way", () => {
    expect(selectionSummary(null, ALL_SERVERS)).toBe("all 4 enabled");
    expect(selectionSummary(["plaid"], ALL_SERVERS)).toBe("1 of 4 enabled");
    expect(selectionSummary([], ALL_SERVERS)).toBe("0 of 4 enabled");
  });

  it("says nothing when there are no servers to talk about", () => {
    expect(selectionSummary(null, [])).toBe("");
  });
});

describe("encode / decode", () => {
  // useEditMode compares drafts shallowly, so the draft field has to be a
  // value, not an array. These are the round trips that keeps honest.
  it("round-trips all", () => {
    expect(decode(encode(null))).toBeNull();
  });

  it("round-trips none", () => {
    expect(decode(encode([]))).toEqual([]);
  });

  it("round-trips a subset", () => {
    expect(decode(encode(["plaid", "playwright"]))).toEqual(["plaid", "playwright"]);
  });

  it("encodes the same members identically whatever their order", () => {
    expect(encode(["b", "a"])).toBe(encode(["a", "b"]));
  });

  it("never confuses none with all", () => {
    expect(encode([])).not.toBe(encode(null));
  });

  it("survives a plugin name full of colons", () => {
    expect(decode(encode(["plugin:discord:discord"]))).toEqual(["plugin:discord:discord"]);
  });
});
