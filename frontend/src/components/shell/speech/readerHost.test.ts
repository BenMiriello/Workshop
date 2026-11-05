import { describe, expect, it, vi } from "vitest";
import type { ReadAloudPhase } from "@workshop/ui";
import { readerHost } from "./readerHost";

function fakeReader() {
  let state = { phase: "idle" as ReadAloudPhase, source: null as string | null, index: 0, count: 0 };
  const listeners = new Set<() => void>();
  return {
    read: vi.fn(),
    stop: vi.fn(),
    getState: () => state,
    subscribe: (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    put(patch: Partial<typeof state>) {
      state = { ...state, ...patch };
      listeners.forEach((fn) => fn());
    },
  };
}

describe("readerHost", () => {
  it("keeps the same state object while phase and source hold", () => {
    const r = fakeReader();
    const host = readerHost(r);
    r.put({ phase: "playing", source: "a", count: 4 });
    const first = host.getState();
    r.put({ index: 2 });
    expect(host.getState()).toBe(first);
    expect(first).toEqual({ phase: "playing", source: "a" });
  });

  it("gives a new object when the phase or source changes", () => {
    const r = fakeReader();
    const host = readerHost(r);
    r.put({ phase: "playing", source: "a" });
    const playing = host.getState();
    r.put({ phase: "paused" });
    expect(host.getState()).not.toBe(playing);
    expect(host.getState()).toEqual({ phase: "paused", source: "a" });
    r.put({ source: "b" });
    expect(host.getState().source).toBe("b");
  });

  it("passes read with its place, stop and subscribe through", () => {
    const r = fakeReader();
    const host = readerHost(r);
    const fn = vi.fn();
    const off = host.subscribe(fn);
    const place = {} as Element;
    host.read("Hello.", "msg", place);
    host.stop();
    r.put({ phase: "loading" });
    off();
    r.put({ phase: "idle" });
    expect(r.read).toHaveBeenCalledWith("Hello.", "msg", place);
    expect(r.stop).toHaveBeenCalled();
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
