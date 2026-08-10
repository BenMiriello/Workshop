import { describe, expect, it } from "vitest";
import { staleNoticeText } from "./staleNotice";

const NOW = 1_700_000_000_000;

describe("staleNoticeText", () => {
  it("says nothing while the chat is current", () => {
    expect(staleNoticeText(null, NOW)).toBeNull();
  });

  it("speaks up once the polls have been failing", () => {
    // The case that used to be silent: a full transcript on screen, minutes
    // behind, indistinguishable from a turn still thinking.
    const s = staleNoticeText(NOW - 90_000, NOW);
    expect(s).toContain("Not hearing from the server");
    expect(s).toContain("2 minutes");
  });

  it("counts seconds while it is still seconds", () => {
    expect(staleNoticeText(NOW - 20_000, NOW)).toContain("20s");
  });

  it("says one minute rather than 1 minutes", () => {
    expect(staleNoticeText(NOW - 62_000, NOW)).toContain("1 minute ago");
  });

  it("never reports a time in the future off a clock step", () => {
    // A laptop waking up, or an NTP correction. "stopped updating -4s ago" is
    // worse than useless: it makes the whole notice look broken.
    expect(staleNoticeText(NOW + 5_000, NOW)).toContain("0s");
  });

  it("uses no em-dashes", () => {
    expect(staleNoticeText(NOW - 90_000, NOW)).not.toContain("—");
  });
});
