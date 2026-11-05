import { describe, expect, it } from "vitest";
import { filterGroups, numberGroups } from "./numbered";

const run = () => {};
const groups = numberGroups([
  { label: "This page", items: [{ label: "Scroll down", hint: "scroll down", run }, { label: "Close", phrases: ["close that", "close menu"], run }] },
  { label: "Chat", items: [{ label: "Send message", hint: "ok workshop send message", run }] },
]);

describe("filterGroups", () => {
  it("keeps the numbers the full list gave", () => {
    const got = filterGroups(groups, "send");
    expect(got.map((g) => g.label)).toEqual(["Chat"]);
    expect(got[0].items[0].n).toBe(3);
  });

  it("matches every word against the name and every phrase", () => {
    expect(filterGroups(groups, "close menu")[0].items.map((i) => i.label)).toEqual(["Close"]);
    expect(filterGroups(groups, "close send")).toEqual([]);
  });

  it("keeps a whole group named by the filter", () => {
    expect(filterGroups(groups, "this page")[0].items).toHaveLength(2);
  });
});
