import { describe, expect, it } from "vitest";
import { parseHistory } from "./history";

describe("parseHistory", () => {
  it("drops what is not a record and survives junk", () => {
    expect(parseHistory("{bad")).toEqual([]);
    expect(parseHistory(null)).toEqual([]);
    const ok = { id: "a", startedAt: 1, endedAt: 2, text: "Hi.", places: ["Message"], status: "done" };
    expect(parseHistory(JSON.stringify([ok, { id: 3 }, "x"]))).toEqual([ok]);
  });
});
