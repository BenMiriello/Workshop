import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** A page the test can hide, show and bring back online. */
function fakePage() {
  const win = new EventTarget();
  const doc = Object.assign(new EventTarget(), { visibilityState: "visible" as "visible" | "hidden" });
  vi.stubGlobal("window", win);
  vi.stubGlobal("document", doc);
  return {
    hide() { doc.visibilityState = "hidden"; doc.dispatchEvent(new Event("visibilitychange")); },
    show() { doc.visibilityState = "visible"; doc.dispatchEvent(new Event("visibilitychange")); },
    online() { win.dispatchEvent(new Event("online")); },
  };
}

async function freshModule() {
  vi.resetModules();
  return import("./retryPause");
}

/** Whether `p` has settled after the timers have run as far as `ms`. */
async function settledAfter(p: Promise<void>, ms: number): Promise<boolean> {
  let done = false;
  void p.then(() => { done = true; });
  await vi.advanceTimersByTimeAsync(ms);
  return done;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the wait between asks", () => {
  it("starts at half a second, doubles, and caps at five", async () => {
    const { cappedBackoff } = await freshModule();
    expect([0, 1, 2, 3, 4, 5, 20].map(cappedBackoff)).toEqual([500, 1000, 2000, 4000, 5000, 5000, 5000]);
  });

  it("ends after its time on a visible page", async () => {
    fakePage();
    const { pauseForRetry } = await freshModule();
    const p = pauseForRetry(1000);
    expect(await settledAfter(p, 999)).toBe(false);
    expect(await settledAfter(p, 1)).toBe(true);
  });

  it("ends at once when the network comes back", async () => {
    const page = fakePage();
    const { pauseForRetry } = await freshModule();
    const p = pauseForRetry(5000);
    page.online();
    expect(await settledAfter(p, 0)).toBe(true);
  });

  it("holds on a hidden page until it is shown", async () => {
    const page = fakePage();
    const { pauseForRetry } = await freshModule();
    const p = pauseForRetry(500);
    page.hide();
    expect(await settledAfter(p, 60_000)).toBe(false);
    page.show();
    expect(await settledAfter(p, 0)).toBe(true);
  });
});
