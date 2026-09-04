import { describe, expect, it } from "vitest";
import { isStale, STALE_AFTER_MS, STALE_TEXT } from "./staleNotice";

const NOW = 1_700_000_000_000;

describe("when the chat says it stopped updating", () => {
  it("says nothing on a single failure, however long ago that failure was", () => {
    // One dropped request is not an outage.
    expect(isStale(1, NOW - 3_600_000, NOW)).toBe(false);
  });

  it("says nothing about a burst of failures inside a few seconds", () => {
    // THE 2026-08-12 BUG. Three retries a second apart used to put "this chat
    // stopped updating 3s ago" over the composer of a working chat. Nothing has
    // been out of contact for three seconds.
    expect(isStale(3, NOW - 3_000, NOW)).toBe(false);
    expect(isStale(9, NOW - 3_000, NOW)).toBe(false);
  });

  it("does not fire two seconds into a blip on a chat that had been idle", () => {
    // THE 2026-09-04 BUG, and the reason the clock moved. The floor used to be
    // measured from the last request that LANDED. A quiet thread polls once a
    // minute, so it is legitimately 59s past its last success with nothing
    // wrong; two failures a second apart then cleared a 30s floor that no
    // outage had come near, and the banner appeared and vanished again inside
    // one restart window.
    //
    // Timed from the outage instead, the same two failures say nothing: this
    // browser has been failing for two seconds.
    const outageStarted = NOW - 2_000;
    expect(isStale(2, outageStarted, NOW)).toBe(false);
    expect(isStale(5, outageStarted, NOW)).toBe(false);
  });

  it("rides out a backend restart in silence", () => {
    // apply.sh arms a deferred restart; the edge waits ~7s of it out by itself
    // (workshop_public_app._RETRY_WAITS). Whatever still fails inside that
    // window is a pause, not a break, and must not be announced.
    for (const elapsed of [1_000, 4_000, 7_000, 12_000]) {
      expect(isStale(4, NOW - elapsed, NOW)).toBe(false);
    }
  });

  it("speaks up once the failing has gone on for real time", () => {
    // The case that used to be silent: a full transcript on screen, minutes
    // behind, indistinguishable from a turn still thinking.
    expect(isStale(2, NOW - 120_000, NOW)).toBe(true);
  });

  it("still surfaces the two-consecutive-failed-polls case the floor was for", () => {
    // The backstop poll is 60s (useThreadLog POLL_MS), so a second consecutive
    // failed poll is a minute into the outage: past the floor, and true.
    expect(isStale(2, NOW - 60_000, NOW)).toBe(true);
  });

  it("needs both halves, so neither alone can trip it", () => {
    expect(isStale(1, NOW - 120_000, NOW)).toBe(false);
    expect(isStale(5, NOW - (STALE_AFTER_MS - 1), NOW)).toBe(false);
    expect(isStale(2, NOW - STALE_AFTER_MS, NOW)).toBe(true);
  });

  it("does not manufacture silence off a clock that ran backwards", () => {
    // A laptop waking up, or an NTP correction. The first failure appearing to
    // be in the future must read as zero elapsed, not as a huge gap.
    expect(isStale(5, NOW + 60_000, NOW)).toBe(false);
  });

  it("states the outage without a duration that would age into a lie", () => {
    // A browser whose backend has gone away has nothing left to re-render it,
    // so any figure rendered here freezes at the moment it appeared.
    expect(STALE_TEXT).not.toMatch(/\d/);
    expect(STALE_TEXT).not.toContain("—");
  });
});
