import { describe, it, expect } from "vitest";
import { rowTotal, isCounted } from "./pageTotal";

describe("rowTotal", () => {
  it("takes the server's count when the server counted", () => {
    expect(rowTotal(188, 25)).toBe(188);
    expect(rowTotal(0, 0)).toBe(0);
  });

  it("never claims a non-empty list is empty", () => {
    // The failure this exists to stop: GET /api/jobs answers `total: null` at
    // every limit, and `?? 0` printed 0 over 200 rows.
    expect(rowTotal(null, 200)).toBe(200);
    expect(rowTotal(undefined, 200)).toBe(200);
  });

  it("still says zero when there is genuinely nothing", () => {
    expect(rowTotal(null, 0)).toBe(0);
  });
});

describe("isCounted", () => {
  it("separates 'the server counted' from 'the server declined to'", () => {
    expect(isCounted(0)).toBe(true);
    expect(isCounted(188)).toBe(true);
    expect(isCounted(null)).toBe(false);
    expect(isCounted(undefined)).toBe(false);
  });
});
