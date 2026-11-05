import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isTransientNetworkError } from "@workshop/ui";
import {
  READ_TIMEOUT_MS, isRequestTimeout, readDeadlineFor, requestTimeout, withDeadline,
} from "./requestTimeout";

/** A fetch that never answers until its signal aborts, the way a stalled
 *  backend behaves behind an edge that keeps the connection open. */
function hanging(signal: AbortSignal): Promise<Response> {
  return new Promise((_, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("which requests get a read deadline", () => {
  it("gives one to a plain read", () => {
    expect(readDeadlineFor(undefined, false)).toBe(READ_TIMEOUT_MS);
    expect(readDeadlineFor("get", false)).toBe(READ_TIMEOUT_MS);
    expect(readDeadlineFor("HEAD", false)).toBe(READ_TIMEOUT_MS);
  });

  it("leaves a write alone, since it may run as long as the work it starts", () => {
    for (const m of ["POST", "PUT", "PATCH", "DELETE"]) {
      expect(readDeadlineFor(m, false)).toBeNull();
    }
  });

  it("leaves a request alone whose caller passed its own signal", () => {
    expect(readDeadlineFor("GET", true)).toBeNull();
  });
});

describe("a request that misses its deadline", () => {
  it("rejects as a timeout that every retry path counts as transient", async () => {
    const p = withDeadline(hanging, 1000);
    const caught = p.catch((e) => e);
    await vi.advanceTimersByTimeAsync(1000);
    const e = await caught;
    expect(isRequestTimeout(e)).toBe(true);
    expect(isTransientNetworkError(e)).toBe(true);
  });

  it("is not cut off once the response has arrived", async () => {
    let seen: AbortSignal | null = null;
    const ok = new Response("{}");
    const r = await withDeadline(async (signal) => {
      seen = signal;
      return ok;
    }, 1000);
    await vi.advanceTimersByTimeAsync(5000);
    expect(r).toBe(ok);
    expect(seen!.aborted).toBe(false);
  });

  it("passes a failure that was not the deadline through unchanged", async () => {
    const boom = new TypeError("Failed to fetch");
    await expect(withDeadline(async () => { throw boom; }, 1000)).rejects.toBe(boom);
  });

  it("names how long it waited", () => {
    expect(requestTimeout(15_000).message).toContain("15s");
  });
});
