import { describe, expect, it } from "vitest";
import { anyPresent, tierKind } from "./stores";

describe("tierKind", () => {
  it("draws an unrestricted store as ok", () => {
    expect(tierKind("open")).toBe("ok");
  });

  // The regression this module exists for. `gated` is a real tier
  // (backend/services/policy.py TIERS) that both former copies of this mapper
  // omitted, so it fell through to the unknown branch and a gated store drew
  // as if it had no policy at all.
  it("draws a gated store as restricted, not as unknown", () => {
    expect(tierKind("gated")).toBe("warn");
  });

  it("draws a mediated store as restricted", () => {
    expect(tierKind("mediated")).toBe("warn");
  });

  // `closed` was in both former copies and is not a tier the backend can emit.
  // It must read as unknown rather than as a real state.
  it("treats the phantom closed tier as unknown", () => {
    expect(tierKind("closed")).toBe("stopped");
  });

  it("treats a missing tier as unknown", () => {
    expect(tierKind(undefined)).toBe("stopped");
    expect(tierKind(null)).toBe("stopped");
    expect(tierKind("")).toBe("stopped");
  });

  // No tier is a failure: a mediated store is working as configured.
  it("never reports a tier as danger", () => {
    for (const t of ["open", "gated", "mediated", "closed", undefined]) {
      expect(tierKind(t)).not.toBe("danger");
    }
  });
});

describe("a column that is empty on every row", () => {
  const rows = [{ host: null }, { host: undefined }, { host: "" }];

  it("is not a column", () => {
    expect(anyPresent(rows, (r) => r.host)).toBe(false);
    expect(anyPresent([], (r: { host?: string }) => r.host)).toBe(false);
  });

  it("comes back the moment one row has a value", () => {
    expect(anyPresent([...rows, { host: "somewhere" }], (r) => r.host)).toBe(true);
  });

  it("keeps a column whose value is falsy but real", () => {
    expect(anyPresent([{ n: 0 }], (r) => r.n)).toBe(true);
    expect(anyPresent([{ n: false }], (r) => r.n)).toBe(true);
  });
});
