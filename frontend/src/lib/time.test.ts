// The pure rules in lib/time. No fake clock: every relative assertion below
// computes its own "N units ago" timestamp from the real Date.now() at test
// run time, the way the module itself does, and uses an offset generous
// enough (seconds, not milliseconds) to absorb the runtime between the two
// calls.
import { describe, expect, it } from "vitest";
import { formatDuration, timeAbsolute, timeAbsoluteShort, timeAgo } from "./time";

const agoIso = (ms: number) => new Date(Date.now() - ms).toISOString();
const SEC = 1000;
const MIN = 60 * SEC;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("timeAgo", () => {
  it("is empty for a missing or unparseable timestamp", () => {
    expect(timeAgo(undefined)).toBe("");
    expect(timeAgo(null)).toBe("");
    expect(timeAgo("")).toBe("");
    expect(timeAgo("not a date")).toBe("");
  });

  it("clamps a timestamp ahead of the local clock to zero, never negative", () => {
    expect(timeAgo(new Date(Date.now() + 10_000).toISOString())).toBe("0s ago");
  });

  it("steps seconds, minutes, hours, days, months, years, suffixed by default", () => {
    expect(timeAgo(agoIso(5 * SEC))).toBe("5s ago");
    expect(timeAgo(agoIso(5 * MIN))).toBe("5m ago");
    expect(timeAgo(agoIso(5 * HOUR))).toBe("5h ago");
    expect(timeAgo(agoIso(5 * DAY))).toBe("5d ago");
    expect(timeAgo(agoIso(40 * DAY))).toBe("1mo ago");
    expect(timeAgo(agoIso(400 * DAY))).toBe("1y ago");
  });

  it("has a day tier: 48 hours reads as days everywhere, not as hours", () => {
    // CommutePages.tsx had no day tier before this module existed, so the
    // same 48-hour-old timestamp read "48h ago" there and "2d ago" every
    // other place `ago` was implemented.
    expect(timeAgo(agoIso(48 * HOUR))).toBe("2d ago");
  });

  it("drops the suffix on request, for a caller reading inline (a meta line)", () => {
    expect(timeAgo(agoIso(5 * HOUR), { suffix: false })).toBe("5h");
  });
});

describe("timeAbsolute / timeAbsoluteShort", () => {
  it("are empty for a missing or unparseable timestamp, never the raw string", () => {
    // ProjectDetail's own `fmtTs` returned the raw, unparsed string on
    // failure instead of "" — the divergent failure mode this module fixes.
    expect(timeAbsolute(undefined)).toBe("");
    expect(timeAbsolute("not a date")).toBe("");
    expect(timeAbsoluteShort(undefined)).toBe("");
    expect(timeAbsoluteShort("not a date")).toBe("");
  });

  it("format a real timestamp without throwing", () => {
    const iso = new Date("2026-08-14T15:04:00Z").toISOString();
    expect(timeAbsolute(iso)).not.toBe("");
    expect(timeAbsoluteShort(iso)).not.toBe("");
  });
});

describe("formatDuration", () => {
  it("is empty without both a start and an end", () => {
    expect(formatDuration(undefined, undefined)).toBe("");
    expect(formatDuration(agoIso(5 * MIN), undefined)).toBe("");
    expect(formatDuration(undefined, agoIso(0))).toBe("");
  });

  it("renders seconds under a minute, minutes and seconds above it", () => {
    const start = new Date("2026-08-14T00:00:00Z");
    expect(formatDuration(start.toISOString(), new Date(start.getTime() + 42 * SEC).toISOString())).toBe("42s");
    expect(formatDuration(start.toISOString(), new Date(start.getTime() + 3 * MIN).toISOString())).toBe("3m");
    expect(formatDuration(start.toISOString(), new Date(start.getTime() + 3 * MIN + 12 * SEC).toISOString())).toBe("3m 12s");
  });

  it("is empty when either timestamp fails to parse", () => {
    expect(formatDuration("not a date", agoIso(0))).toBe("");
    expect(formatDuration(agoIso(5 * MIN), "not a date")).toBe("");
  });
});
