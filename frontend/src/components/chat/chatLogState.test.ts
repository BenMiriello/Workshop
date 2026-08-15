import { describe, expect, it } from "vitest";
import { logPlaceholder } from "./chatLogState";

const base = { empty: true, loading: false, error: false, inFlight: false };

describe("logPlaceholder", () => {
  it("shows nothing once there are blocks to render and loading has settled", () => {
    expect(logPlaceholder({ ...base, empty: false })).toBe(null);
    expect(logPlaceholder({ ...base, empty: false, error: true })).toBe(null);
  });

  it("NEVER claims empty while the log is still loading", () => {
    expect(logPlaceholder({ ...base, loading: true })).toBe("loading");
    // The regression this exists for: a thread whose first page is in flight.
    // Blocks are empty and nothing is streaming yet, and the answer is still
    // "loading", not "this conversation is empty".
    expect(logPlaceholder({ ...base, loading: true, inFlight: false })).toBe("loading");
  });

  it("shows loading over a restored pending send, not the send alone", () => {
    // The bug: the outbox restores a failed send SYNCHRONOUSLY on thread
    // select, while the real history is still loading asynchronously. That
    // restored bubble makes `empty` false before the log has hydrated, and the
    // old precedence ("blocks win, whatever loading says") let it stand in for
    // a settled conversation — one old failed message, alone, no loading
    // indicator. `loading` must win here: there IS a block (`empty: false`,
    // the restored pending send), but the thread has not hydrated, so the
    // honest answer is still "loading".
    expect(logPlaceholder({ ...base, empty: false, loading: true })).toBe("loading");
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

