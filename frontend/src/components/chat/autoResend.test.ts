import { describe, expect, it } from "vitest";
import type { PendingSend } from "./threadStore";
import {
  RESEND_IDLE, resendBackoff, resendInOrder, resendOrder, resendPassEnded, resendTriggered,
  type ResendGate,
} from "./autoResend";

const send = (over: Partial<PendingSend> = {}): PendingSend => ({
  clientId: "c1", text: "hello", ts: "2026-09-17T06:00:00Z",
  status: "failed", at: 1, ...over,
} as PendingSend);

describe("which messages a resend pass sends", () => {
  it("takes the failed ones, oldest first, and leaves in-flight sends alone", () => {
    const pending = [
      send({ clientId: "a", at: 1 }),
      send({ clientId: "b", at: 2, status: "sending" }),
      send({ clientId: "c", at: 3 }),
    ];
    expect(resendOrder(pending)).toEqual(["a", "c"]);
  });

  it("has nothing to do when nothing failed", () => {
    expect(resendOrder([send({ status: "sending" })])).toEqual([]);
  });

  it("leaves a refused message alone on a pass the page started itself", () => {
    const pending = [
      send({ clientId: "blip", at: 1, transient: true }),
      send({ clientId: "refused", at: 2, transient: false }),
      send({ clientId: "restored", at: 3 }),
    ];
    expect(resendOrder(pending, true)).toEqual(["blip"]);
    expect(resendOrder(pending)).toEqual(["blip", "refused", "restored"]);
  });
});

describe("how often the page tries on its own", () => {
  it("starts at two seconds, doubles, and caps at thirty", () => {
    expect([0, 1, 2, 3, 4, 10].map(resendBackoff)).toEqual([2000, 4000, 8000, 16000, 30000, 30000]);
  });
});

describe("sending them", () => {
  it("sends one at a time, in order", async () => {
    const log: string[] = [];
    let inFlight = 0;
    const n = await resendInOrder(["a", "b", "c"], async (id) => {
      inFlight += 1;
      expect(inFlight).toBe(1);
      await Promise.resolve();
      log.push(id);
      inFlight -= 1;
      return true;
    });
    expect(log).toEqual(["a", "b", "c"]);
    expect(n).toBe(3);
  });

  it("stops at the first failure so a later message never overtakes an earlier one", async () => {
    const tried: string[] = [];
    const n = await resendInOrder(["a", "b", "c"], async (id) => {
      tried.push(id);
      return id !== "b";
    });
    expect(tried).toEqual(["a", "b"]);
    expect(n).toBe(1);
  });
});

describe("two triggers at once", () => {
  it("starts one pass, not two", () => {
    const first = resendTriggered(RESEND_IDLE);
    expect(first.start).toBe(true);
    const second = resendTriggered(first.next);
    expect(second.start).toBe(false);
  });

  it("owes exactly one more pass for any number of triggers during a pass", () => {
    let g: ResendGate = resendTriggered(RESEND_IDLE).next;
    for (let i = 0; i < 4; i++) {
      const r = resendTriggered(g);
      expect(r.start).toBe(false);
      g = r.next;
    }
    const end1 = resendPassEnded(g);
    expect(end1.start).toBe(true);
    const end2 = resendPassEnded(end1.next);
    expect(end2.start).toBe(false);
    expect(end2.next).toEqual(RESEND_IDLE);
  });

  it("never sends one message twice across the two passes", async () => {
    // The follow-up pass reads what is STILL unsent; whatever the first pass
    // delivered has left `pending` by then.
    const pending = new Map([
      ["a", send({ clientId: "a", at: 1 })],
      ["b", send({ clientId: "b", at: 2 })],
    ]);
    const sent: string[] = [];
    const deliver = async (id: string) => {
      sent.push(id);
      pending.delete(id);
      return true;
    };
    let g = resendTriggered(RESEND_IDLE).next;
    g = resendTriggered(g).next;
    await resendInOrder(resendOrder([...pending.values()]), deliver);
    const again = resendPassEnded(g);
    expect(again.start).toBe(true);
    await resendInOrder(resendOrder([...pending.values()]), deliver);
    expect(sent).toEqual(["a", "b"]);
  });

  it("starts afresh for a trigger after the pass is over", () => {
    const g = resendPassEnded(resendTriggered(RESEND_IDLE).next).next;
    expect(resendTriggered(g).start).toBe(true);
  });
});
