import { describe, expect, it } from "vitest";
import { logPlaceholder } from "./chatLogState";

const base = { empty: true, loading: false, error: false, inFlight: false };

describe("logPlaceholder", () => {
  it("shows nothing once there are blocks to render", () => {
    expect(logPlaceholder({ ...base, empty: false })).toBe(null);
    expect(logPlaceholder({ ...base, empty: false, loading: true })).toBe(null);
    expect(logPlaceholder({ ...base, empty: false, error: true })).toBe(null);
  });

  it("NEVER claims empty while the log is still loading", () => {
    expect(logPlaceholder({ ...base, loading: true })).toBe("loading");
    // The regression this exists for: a thread whose first page is in flight.
    // Blocks are empty and nothing is streaming yet, and the answer is still
    // "loading", not "this conversation is empty".
    expect(logPlaceholder({ ...base, loading: true, inFlight: false })).toBe("loading");
  });

  it("prefers loading over an error left from a previous load", () => {
    expect(logPlaceholder({ ...base, loading: true, error: true })).toBe("loading");
  });

  it("shows the error when a finished load failed", () => {
    expect(logPlaceholder({ ...base, error: true })).toBe("error");
  });

  it("stays quiet when a run is in flight but has produced nothing", () => {
    expect(logPlaceholder({ ...base, inFlight: true })).toBe(null);
  });

  it("claims empty only for a settled, idle, error-free empty log", () => {
    expect(logPlaceholder(base)).toBe("empty");
  });
});
