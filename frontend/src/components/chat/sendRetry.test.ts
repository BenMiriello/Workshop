import { describe, expect, it } from "vitest";
import { HttpError } from "@workshop/ui";
import { requestTimeout } from "../../lib/requestTimeout";
import { LANDED, sendWithRetry, syncUntilRead } from "./sendRetry";

const noWait = async () => {};
const gateway = () => new HttpError(502, "Server unavailable (502). Retrying.");

/** An attempt that fails with each error in turn, then answers `ok`. */
function failing(errors: unknown[]) {
  let calls = 0;
  const attempt = async () => {
    const e = errors[calls++];
    if (e !== undefined) throw e;
    return "ok";
  };
  return { attempt, calls: () => calls };
}

describe("posting a message through a blip", () => {
  it("posts again after a gateway error and returns the answer", async () => {
    const a = failing([gateway(), gateway()]);
    expect(await sendWithRetry(a.attempt, { wait: noWait })).toBe("ok");
    expect(a.calls()).toBe(3);
  });

  it("does not post again after a refusal", async () => {
    const a = failing([new HttpError(400, "400 empty message")]);
    await expect(sendWithRetry(a.attempt, { wait: noWait })).rejects.toThrow("400 empty message");
    expect(a.calls()).toBe(1);
  });

  it("gives up when the schedule runs out, with the last failure", async () => {
    const a = failing([gateway(), gateway()]);
    await expect(sendWithRetry(a.attempt, { wait: noWait, backoffMs: [0] })).rejects.toThrow("502");
    expect(a.calls()).toBe(2);
  });

  it("posts again after a timeout once the log says the message is not there", async () => {
    const a = failing([requestTimeout(20_000)]);
    let asked = 0;
    const out = await sendWithRetry(a.attempt, {
      wait: noWait,
      landed: async () => { asked += 1; return false; },
    });
    expect(out).toBe("ok");
    expect(asked).toBe(1);
    expect(a.calls()).toBe(2);
  });

  it("does not post again after a timeout when the message already landed", async () => {
    const a = failing([requestTimeout(20_000)]);
    const out = await sendWithRetry(a.attempt, { wait: noWait, landed: async () => true });
    expect(out).toBe(LANDED);
    expect(a.calls()).toBe(1);
  });

  it("posts again when the log cannot be read either", async () => {
    const a = failing([requestTimeout(20_000)]);
    const out = await sendWithRetry(a.attempt, {
      wait: noWait,
      landed: async () => { throw new TypeError("Failed to fetch"); },
    });
    expect(out).toBe("ok");
  });

  it("only checks the log after a timeout, never after a gateway error", async () => {
    const a = failing([gateway()]);
    let asked = 0;
    await sendWithRetry(a.attempt, { wait: noWait, landed: async () => { asked += 1; return true; } });
    expect(asked).toBe(0);
  });
});

describe("reading the log back after a send", () => {
  it("asks again until a read lands", async () => {
    const answers = [false, false, true];
    let calls = 0;
    const waits: number[] = [];
    const ok = await syncUntilRead(async () => answers[calls++], [10, 20, 30], async (ms) => { waits.push(ms); });
    expect(ok).toBe(true);
    expect(waits).toEqual([10, 20]);
  });

  it("stops when the schedule runs out, leaving the poll to it", async () => {
    let calls = 0;
    const ok = await syncUntilRead(async () => { calls += 1; return false; }, [0, 0], noWait);
    expect(ok).toBe(false);
    expect(calls).toBe(3);
  });
});
