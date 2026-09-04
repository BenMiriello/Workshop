import { describe, expect, it } from "vitest";
import { HttpError } from "@workshop/ui";
import { THREAD_GONE_STATUSES, threadIsGone } from "./threadGone";

describe("telling a thread that is gone from one we could not ask about", () => {
  it("is gone when the server looked and answered", () => {
    expect(threadIsGone(new HttpError(404, "404 Not found."))).toBe(true);
    expect(threadIsGone(new HttpError(403, "403 Not allowed."))).toBe(true);
  });

  it("is NOT gone for a restart window", () => {
    // THE REPORTED BUG, 2026-09-04. `catch(() => null)` here meant
    // `setActiveId(null)`: the chat dropped to blank over a 502 the edge waits
    // out by itself, and the transcript re-rendered from nothing when the
    // conversation came back.
    for (const status of [502, 503, 504]) {
      expect(threadIsGone(new HttpError(status, `Server unavailable (${status}). Retrying.`))).toBe(false);
    }
  });

  it("is NOT gone for a backend bug", () => {
    // A 500 is not transient and must not be retried away, but it is still the
    // server failing rather than the server saying the thread is missing.
    // Dropping the operator's open conversation on one would be a bug reported
    // as data loss.
    expect(threadIsGone(new HttpError(500, "500 The server errored."))).toBe(false);
  });

  it("is NOT gone when the request never landed at all", () => {
    // A fetch that never got a response rejects as a TypeError. Nothing was
    // learned, so nothing changes.
    expect(threadIsGone(new TypeError("Failed to fetch"))).toBe(false);
    expect(threadIsGone(new Error("boom"))).toBe(false);
    expect(threadIsGone(null)).toBe(false);
    expect(threadIsGone(undefined)).toBe(false);
  });

  it("names the set, because widening it is how this regresses", () => {
    expect([...THREAD_GONE_STATUSES].sort()).toEqual([403, 404]);
  });
});
