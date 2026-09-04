import { describe, expect, it } from "vitest";
import {
  NEW_STREAM, RECONNECT_BASE_MS, RECONNECT_MAX_MS,
  reconnectDelayMs, streamErrored, streamOpened,
  type StreamHealth,
} from "./streamHealth";

describe("the change stream's reconnect policy", () => {
  it("leaves the browser's own retry alone", () => {
    // `EventSource` reconnects by itself and fires `error` while it does
    // (readyState CONNECTING). Opening a socket here too is two reconnect
    // machines racing over one stream, which is what this used to do.
    const r = streamErrored(NEW_STREAM, true, 0);
    expect(r.reopenAfterMs).toBeNull();
    expect(r.next.phase).toBe("connecting");
    expect(r.next.attempt).toBe(0);
  });

  it("does not climb the backoff on errors it is not handling", () => {
    let h: StreamHealth = NEW_STREAM;
    for (let i = 0; i < 5; i++) h = streamErrored(h, true, 0).next;
    // The first drop the browser gives up on is still the FIRST one.
    expect(streamErrored(h, false, 0).reopenAfterMs).toBe(RECONNECT_BASE_MS);
  });

  it("reopens on a schedule when the browser has given up", () => {
    const first = streamErrored(NEW_STREAM, false, 0);
    expect(first.reopenAfterMs).toBe(RECONNECT_BASE_MS);
    expect(first.next.phase).toBe("waiting");
    const second = streamErrored(first.next, false, 0);
    expect(second.reopenAfterMs).toBe(RECONNECT_BASE_MS);
    expect(second.next.attempt).toBe(2);
  });

  it("never retries with no gap at all", () => {
    // A retry fired inside the same outage fails identically. That is the
    // mistake the edge proxy was fixed for on 2026-09-04, and it is the reason
    // the base is a floor rather than a starting point.
    for (let attempt = 1; attempt <= 12; attempt++) {
      for (const rand of [0, 0.01, 0.5, 0.999]) {
        expect(reconnectDelayMs(attempt, rand)).toBeGreaterThanOrEqual(RECONNECT_BASE_MS);
      }
    }
  });

  it("backs off, and stops backing off at the ceiling", () => {
    const full = (attempt: number) => reconnectDelayMs(attempt, 0.999999);
    expect(full(1)).toBe(RECONNECT_BASE_MS);
    expect(full(2)).toBeGreaterThan(full(1));
    expect(full(3)).toBeGreaterThan(full(2));
    expect(full(20)).toBe(RECONNECT_MAX_MS);
    expect(full(200)).toBe(RECONNECT_MAX_MS);
  });

  it("spreads the reconnects, because one restart drops every tab at once", () => {
    const waits = new Set([0, 0.25, 0.5, 0.75].map((r) => reconnectDelayMs(6, r)));
    expect(waits.size).toBe(4);
  });

  it("comes back to zero when the socket OPENS, not when traffic arrives", () => {
    // A stream that reconnects fine to a quiet thread sees no events for
    // minutes. Resetting on traffic left it at the last outage's attempt
    // number, so the next drop waited up to thirty seconds.
    let h: StreamHealth = NEW_STREAM;
    for (let i = 0; i < 6; i++) h = streamErrored(h, false, 0).next;
    expect(h.attempt).toBe(6);
    h = streamOpened();
    expect(h).toEqual({ phase: "open", attempt: 0 });
    expect(streamErrored(h, false, 0.999999).reopenAfterMs).toBe(RECONNECT_BASE_MS);
  });

  it("survives the whole sequence: connect, blip, recover, drop, recover", () => {
    let h: StreamHealth = NEW_STREAM;
    h = streamOpened();
    expect(h.phase).toBe("open");

    // A blip the browser handles: nothing scheduled, nothing counted.
    const blip = streamErrored(h, true, 0.5);
    expect(blip.reopenAfterMs).toBeNull();
    h = blip.next;

    h = streamOpened();
    expect(h.attempt).toBe(0);

    // A drop the browser gives up on: ours to reopen, on a real gap.
    const drop = streamErrored(h, false, 0.5);
    expect(drop.reopenAfterMs).toBeGreaterThanOrEqual(RECONNECT_BASE_MS);
    h = drop.next;
    expect(h.phase).toBe("waiting");

    h = streamOpened();
    expect(h).toEqual({ phase: "open", attempt: 0 });
  });
});
