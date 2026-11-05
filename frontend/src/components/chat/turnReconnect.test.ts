import { describe, expect, it } from "vitest";
import { RECONNECT_BASE_MS, RECONNECT_MAX_MS } from "./streamHealth";
import {
  TURN_RECONNECT_WINDOW_MS, TURN_STREAM_HEALTHY, afterSettle, turnStreamDropped,
  type TurnReconnect,
} from "./turnReconnect";

describe("a turn stream's reconnect schedule", () => {
  it("waits at least a second, never retrying inside the same outage", () => {
    const r = turnStreamDropped(TURN_STREAM_HEALTHY, 0, 0);
    expect(r.reattachAfterMs).toBe(RECONNECT_BASE_MS);
    expect(r.next).toEqual({ attempt: 1, since: 0 });
  });

  it("backs off to the ceiling and stays there", () => {
    let h: TurnReconnect = TURN_STREAM_HEALTHY;
    const waits: number[] = [];
    for (let i = 0; i < 12; i++) {
      const r = turnStreamDropped(h, i * 1000, 0.999999);
      h = r.next;
      waits.push(r.reattachAfterMs!);
    }
    for (let i = 1; i < 5; i++) expect(waits[i]).toBeGreaterThan(waits[i - 1]);
    expect(waits[11]).toBe(RECONNECT_MAX_MS);
  });

  it("spreads tabs dropped by the same restart", () => {
    const h: TurnReconnect = { attempt: 5, since: 0 };
    const waits = new Set([0, 0.3, 0.6, 0.9].map((r) => turnStreamDropped(h, 1000, r).reattachAfterMs));
    expect(waits.size).toBe(4);
  });

  it("keeps trying for fifteen minutes of outage, however many tries that is", () => {
    let h: TurnReconnect = TURN_STREAM_HEALTHY;
    let now = 0;
    let tries = 0;
    for (;;) {
      const r = turnStreamDropped(h, now, 0.5);
      h = r.next;
      if (r.reattachAfterMs === null) break;
      tries += 1;
      now += r.reattachAfterMs;
    }
    expect(now).toBeGreaterThanOrEqual(TURN_RECONNECT_WINDOW_MS);
    expect(tries).toBeGreaterThan(8);
  });

  it("times the outage from its first drop, not from the latest", () => {
    const first = turnStreamDropped(TURN_STREAM_HEALTHY, 1_000, 0).next;
    const later = turnStreamDropped(first, 60_000, 0).next;
    expect(later.since).toBe(1_000);
    expect(turnStreamDropped(later, 1_000 + TURN_RECONNECT_WINDOW_MS, 0).reattachAfterMs).toBeNull();
  });

  it("stays spent once the window has passed, so the poll keeps its own pace", () => {
    const spent: TurnReconnect = { attempt: 40, since: 0 };
    const a = turnStreamDropped(spent, TURN_RECONNECT_WINDOW_MS + 5, 0);
    expect(a.reattachAfterMs).toBeNull();
    expect(turnStreamDropped(a.next, TURN_RECONNECT_WINDOW_MS + 10, 0).reattachAfterMs).toBeNull();
  });

  it("starts over once a frame has arrived", () => {
    // markAlive puts the stream back to TURN_STREAM_HEALTHY.
    const r = turnStreamDropped(TURN_STREAM_HEALTHY, TURN_RECONNECT_WINDOW_MS * 3, 0);
    expect(r.reattachAfterMs).toBe(RECONNECT_BASE_MS);
  });
});

describe("what finishes a turn", () => {
  it("retires on `end`, even when the settle syncs could not reach the server", () => {
    expect(afterSettle({ settled: false, ended: true, stopped: false })).toBe("retire");
  });

  it("retires when a sync showed the run gone", () => {
    expect(afterSettle({ settled: true, ended: false, stopped: false })).toBe("retire");
  });

  it("keeps following a run whose stream closed without `end` and whose syncs failed", () => {
    // A backend shutdown closes streams without `end`, and a failed sync leaves
    // the store as it was. Retiring here made the tab look idle mid-turn.
    expect(afterSettle({ settled: false, ended: false, stopped: false })).toBe("keep-following");
  });

  it("leaves a stopped run to Stop", () => {
    expect(afterSettle({ settled: false, ended: false, stopped: true })).toBe("leave-to-stop");
    expect(afterSettle({ settled: true, ended: false, stopped: true })).toBe("retire");
  });
});
