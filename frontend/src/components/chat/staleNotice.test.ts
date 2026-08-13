import { describe, expect, it } from "vitest";
import { isStale, STALE_AFTER_MS, STALE_TEXT } from "./staleNotice";

const NOW = 1_700_000_000_000;

describe("when the chat says it stopped updating", () => {
  it("says nothing on a single failure, however long ago the last one landed", () => {
    // One dropped request is not an outage, and the chat may legitimately have
    // been idle for an hour before it.
    expect(isStale(1, NOW - 3_600_000, NOW)).toBe(false);
  });

  it("says nothing about a burst of failures inside a few seconds", () => {
    // THE REPORTED BUG. Three retries a second apart used to put "this chat
    // stopped updating 3s ago" over the composer of a working chat. Nothing has
    // been out of contact for three seconds.
    expect(isStale(3, NOW - 3_000, NOW)).toBe(false);
    expect(isStale(9, NOW - 3_000, NOW)).toBe(false);
  });

  it("speaks up once the failures have gone on for real time", () => {
    // The case that used to be silent: a full transcript on screen, minutes
    // behind, indistinguishable from a turn still thinking.
    expect(isStale(2, NOW - 120_000, NOW)).toBe(true);
  });

  it("needs both halves, so neither alone can trip it", () => {
    expect(isStale(1, NOW - 120_000, NOW)).toBe(false);
    expect(isStale(5, NOW - (STALE_AFTER_MS - 1), NOW)).toBe(false);
    expect(isStale(2, NOW - STALE_AFTER_MS, NOW)).toBe(true);
  });

  it("does not manufacture silence off a clock that ran backwards", () => {
    // A laptop waking up, or an NTP correction. The last success appearing to
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
