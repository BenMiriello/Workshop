import { describe, expect, it } from "vitest";
import { pickFallbackThread } from "./fallbackThread";

const NEW = "__new__";
const rows = [
  { id: "newest", running: false },
  { id: "spending", running: true },
  { id: "stopped-earlier", running: false },
];
const pick = (over: Partial<Parameters<typeof pickFallbackThread>[0]>) =>
  pickFallbackThread({
    current: null, stored: null, inScope: rows, preferRunning: false, newChat: NEW, ...over,
  });

describe("an app's chat frame opened with no thread", () => {
  // 2026-10-03: a second device kept opening the chat it last saw, a finished
  // one, while the newest chat's turn ran on for an hour.
  it("opens the running turn before the last pick", () => {
    expect(pick({ preferRunning: true, stored: "stopped-earlier" })).toBe("spending");
  });

  it("opens it before a recorded new chat", () => {
    expect(pick({ preferRunning: true, stored: NEW })).toBe("spending");
  });

  it("falls back to the last pick when nothing is running", () => {
    const idle = rows.map((r) => ({ ...r, running: false }));
    expect(pick({ preferRunning: true, inScope: idle, stored: "stopped-earlier" })).toBe("stopped-earlier");
  });

  it("keeps the thread the tab already has open", () => {
    expect(pick({ preferRunning: true, current: "newest" })).toBe("newest");
  });
});

describe("the ordinary chat", () => {
  it("restores the last pick even with a turn running elsewhere", () => {
    expect(pick({ stored: "stopped-earlier" })).toBe("stopped-earlier");
  });

  it("stays blank after a new chat", () => {
    expect(pick({ stored: NEW })).toBeNull();
  });

  it("opens the newest when the pick is gone", () => {
    expect(pick({ stored: "deleted" })).toBe("newest");
  });

  it("opens nothing in an empty scope", () => {
    expect(pick({ inScope: [] })).toBeNull();
  });
});
