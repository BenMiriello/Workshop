import { describe, expect, it } from "vitest";
import type { PendingSend } from "./threadStore";
import { firstUnsent, unsentLabel, unsentSends } from "./unsentMessages";

const send = (over: Partial<PendingSend> = {}): PendingSend => ({
  clientId: "c1", text: "hello", ts: "2026-09-17T06:00:00Z",
  status: "failed", at: 1, ...over,
} as PendingSend);

describe("unsent messages", () => {
  it("says nothing when nothing failed", () => {
    expect(unsentLabel([])).toBe(null);
    expect(unsentLabel([send({ status: "sending" })])).toBe(null);
    expect(unsentLabel([send({ status: "sent" })])).toBe(null);
  });

  it("counts only the failures, not the sends still in flight", () => {
    const pending = [
      send({ clientId: "a", at: 1, status: "failed" }),
      send({ clientId: "b", at: 2, status: "sending" }),
      send({ clientId: "c", at: 3, status: "failed" }),
    ];
    expect(unsentSends(pending).map((s) => s.clientId)).toEqual(["a", "c"]);
    expect(unsentLabel(pending)).toBe("2 messages were not sent.");
  });

  it("uses the singular for one, because the plural reads as a bigger problem", () => {
    expect(unsentLabel([send()])).toBe("1 message was not sent.");
  });

  it("retries the oldest first, so a run of failures clears in typing order", () => {
    const pending = [
      send({ clientId: "old", at: 1 }),
      send({ clientId: "new", at: 2 }),
    ];
    expect(firstUnsent(pending)?.clientId).toBe("old");
  });

  it("has nothing to retry when nothing failed", () => {
    expect(firstUnsent([send({ status: "sending" })])).toBe(null);
  });
});
