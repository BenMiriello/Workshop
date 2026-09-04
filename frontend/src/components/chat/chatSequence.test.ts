import { describe, expect, it } from "vitest";
import {
  hasAnchor, joinedRun, placeSend, placementRank, respectsObservedOrder,
  type LogObservation, type SendObservation,
} from "./chatSequence";

// The sequencing model's own tests. Two halves:
//
//  1. NAMED REGRESSIONS — one test per operator report, quoting it, so a future
//     change that reintroduces one fails on a test that says whose complaint it
//     is rather than on an arrangement assertion nobody can date.
//  2. PROPERTIES — the invariants stated over generated input (shuffled,
//     duplicated, late-arriving), because the recurring failure of this rule has
//     never been a case someone thought of and got wrong. It has been a case
//     nobody enumerated.

const send = (o: Partial<SendObservation> = {}): SendObservation => ({
  status: "sending", afterSeq: 0, observedLog: true, duringTurnId: null, ...o,
});
// `hasCommittedMessage` defaults true because the interesting cases are about a
// conversation that already has something in it; the one test that is about an
// empty log says so explicitly.
const log = (o: Partial<LogObservation> = {}): LogObservation => ({
  tail: 0, hasCommittedMessage: true, liveTurnId: null, ...o,
});

describe("hasAnchor", () => {
  it("a seq read off a log this browser had is an anchor", () => {
    expect(hasAnchor(send({ afterSeq: 12, observedLog: true }))).toBe(true);
  });

  it("a seq read off a log this browser never had is not", () => {
    // The whole of invariant I2. `lastSeq` returns 0 for an empty log and for a
    // log that has not arrived, and only the first is a fact about position.
    expect(hasAnchor(send({ afterSeq: 0, observedLog: false }))).toBe(false);
  });

  it("an empty conversation this browser DID read still anchors at zero", () => {
    // "before everything" is a real position when it was really observed. This
    // is what keeps a failed send on an empty thread above messages that arrive
    // afterwards from another device.
    expect(hasAnchor(send({ afterSeq: 0, observedLog: true }))).toBe(true);
  });
});

describe("joinedRun", () => {
  it("is true only for the run this send was actually handed to", () => {
    expect(joinedRun(send({ duringTurnId: "T1" }), log({ liveTurnId: "T1" }))).toBe(true);
  });

  it("a send that STARTED the run on screen is not part of it", () => {
    // The first message of every TURN, not just of the chat. `duringTurnId` is
    // null because no run had been announced when the operator hit enter.
    expect(joinedRun(send({ duringTurnId: null }), log({ liveTurnId: "T1" }))).toBe(false);
  });

  it("a send typed during an EARLIER run does not join the current one", () => {
    expect(joinedRun(send({ duringTurnId: "T1" }), log({ liveTurnId: "T2" }))).toBe(false);
  });

  it("a failed send joined nothing: the server never received it", () => {
    expect(joinedRun(
      send({ status: "failed", duringTurnId: "T1" }), log({ liveTurnId: "T1" }),
    )).toBe(false);
  });

  it("nothing joins a run that is not on screen", () => {
    expect(joinedRun(send({ duringTurnId: "T1" }), log({ liveTurnId: null }))).toBe(false);
  });
});

describe("placeSend — the reports it closes", () => {
  it("2026-09-04: history that reappears goes BELOW the message typed while it was gone", () => {
    // "why did the chat history just disappear while i was typing, and a bunch
    // of errors and warnings showed on the page, then the chat reappeared but
    // UNDER my last message that failed to send"
    //
    // The send was made against an empty store, so it recorded afterSeq 0. When
    // the log came back with 40 events, `afterSeq < tail` was true and the
    // anchor resolved to index 0 — the top of the transcript, above everything.
    const p = placeSend(
      send({ status: "failed", afterSeq: 0, observedLog: false }), log({ tail: 40 }),
    );
    expect(p).toEqual({ at: "tail" });
  });

  it("2026-08-11: a failed send the log grew past stays where it was typed", () => {
    // "my new messages show above this old message, and then subsequent chat
    // messages show above these messages". The anchoring this rule keeps.
    const p = placeSend(send({ status: "failed", afterSeq: 7 }), log({ tail: 40 }));
    expect(p).toEqual({ at: "anchored", afterSeq: 7 });
  });

  it("2026-08-12: a message sent into a running turn goes INSIDE it, never above", () => {
    // "i see 'working...', i send another message, and my new message shows
    // ABOVE it. if it was already working my new message could not be BEFORE it
    // since it wouldn't have seen it"
    const p = placeSend(
      send({ afterSeq: 40, duringTurnId: "T1" }), log({ tail: 40, liveTurnId: "T1" }),
    );
    expect(p).toEqual({ at: "inRun", turnId: "T1" });
  });

  it("the message that STARTS a turn stays above that turn, on every turn", () => {
    // "when I send a message to a new chat, my message shows at the very bottom
    // of the chat area at first, and then after some change or some event, it
    // moves to the top." The condition this replaces only exempted the first
    // message of the whole CHAT (`noCommittedMessage`), so the first message of
    // turn two onwards dropped below its own working indicator.
    const p = placeSend(
      send({ afterSeq: 40, duringTurnId: null }), log({ tail: 40, liveTurnId: "T9" }),
    );
    expect(p).toEqual({ at: "tail" });
  });

  it("a send whose run finished goes back where it was typed, not into the new run", () => {
    // Typed during T1; T1's reply has since committed (the log grew from 40 to
    // 44) and T2 is now writing. It does not join T2 — T2 was never handed it —
    // and it does not sit at the bottom under T2 either: seq 40 is a position
    // this browser really saw, so it goes there, among the messages it was
    // typed between.
    const p = placeSend(
      send({ afterSeq: 40, duringTurnId: "T1" }), log({ tail: 44, liveTurnId: "T2" }),
    );
    expect(p).toEqual({ at: "anchored", afterSeq: 40 });
  });

  it("a send typed during a run that has not committed anything stays at the tail", () => {
    // Same shape, but the log has NOT grown: nothing came in under it, so there
    // is nothing for it to be above and the tail is where it belongs.
    const p = placeSend(
      send({ afterSeq: 40, duringTurnId: "T1" }), log({ tail: 40, liveTurnId: "T2" }),
    );
    expect(p).toEqual({ at: "tail" });
  });

  it("the ordinary send, at the tail of a settled chat, is at the tail", () => {
    expect(placeSend(send({ afterSeq: 40 }), log({ tail: 40 }))).toEqual({ at: "tail" });
  });

  it("the first message of a genuinely empty chat is at the tail", () => {
    expect(placeSend(send({ afterSeq: 0 }), log({ tail: 0 }))).toEqual({ at: "tail" });
  });

  it("I5: a run on an empty log did not precede the opening message", () => {
    // A blank chat can carry a run this browser has not cleared yet. The
    // opening message goes above it, not under its working indicator.
    const p = placeSend(
      send({ afterSeq: 0, duringTurnId: "stale" }),
      log({ tail: 0, hasCommittedMessage: false, liveTurnId: "stale" }),
    );
    expect(p).toEqual({ at: "tail" });
  });

  it("a run in flight outranks an anchor: a mid-run send is never anchored back", () => {
    // Both conditions hold (afterSeq 7 < tail 40, and it joined T1). The run is
    // younger than every committed message, so anchoring would lift the send
    // above work it was typed under.
    const p = placeSend(
      send({ afterSeq: 7, duringTurnId: "T1" }), log({ tail: 40, liveTurnId: "T1" }),
    );
    expect(p).toEqual({ at: "inRun", turnId: "T1" });
  });
});

// ---------------------------------------------------------------------------
// PROPERTIES
// ---------------------------------------------------------------------------

/** A deterministic generator. Seeded so a failure is reproducible from the
 *  seed printed in the assertion, rather than being a flake nobody can re-run. */
function rng(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >> 17;
    s ^= s << 5; s >>>= 0;
    return s / 0x100000000;
  };
}

function generate(seed: number): { sends: SendObservation[]; observed: LogObservation } {
  const r = rng(seed);
  const tail = Math.floor(r() * 60);
  const turns = [null, "T1", "T2", "T3"];
  const observed: LogObservation = {
    tail,
    hasCommittedMessage: r() < 0.8,
    liveTurnId: turns[Math.floor(r() * turns.length)],
  };
  const n = 1 + Math.floor(r() * 6);
  const sends: SendObservation[] = [];
  for (let i = 0; i < n; i++) {
    sends.push({
      status: (["sending", "sent", "failed"] as const)[Math.floor(r() * 3)],
      afterSeq: Math.floor(r() * (tail + 2)),
      observedLog: r() < 0.8,
      duringTurnId: turns[Math.floor(r() * turns.length)],
    });
  }
  return { sends, observed };
}

const SEEDS = Array.from({ length: 300 }, (_, i) => i + 1);

describe("properties", () => {
  it("I1/I2: no send is ever placed above a position it did not observe", () => {
    for (const seed of SEEDS) {
      const { sends, observed } = generate(seed);
      expect(respectsObservedOrder(sends, observed), `seed ${seed}`).toBe(true);
    }
  });

  it("I2: an anchored placement always names a seq that was really seen", () => {
    for (const seed of SEEDS) {
      const { sends, observed } = generate(seed);
      for (const s of sends) {
        const p = placeSend(s, observed);
        if (p.at !== "anchored") continue;
        expect(s.observedLog, `seed ${seed}`).toBe(true);
        expect(p.afterSeq, `seed ${seed}`).toBe(s.afterSeq);
      }
    }
  });

  it("I3: nothing is placed inside a run that is not on screen", () => {
    for (const seed of SEEDS) {
      const { sends, observed } = generate(seed);
      for (const s of sends) {
        const p = placeSend(s, observed);
        if (p.at === "inRun") expect(p.turnId, `seed ${seed}`).toBe(observed.liveTurnId);
      }
    }
  });

  it("I4: placement does not depend on the ORDER sends are considered in", () => {
    for (const seed of SEEDS) {
      const { sends, observed } = generate(seed);
      const forward = sends.map((s) => placeSend(s, observed));
      const backward = [...sends].reverse().map((s) => placeSend(s, observed)).reverse();
      expect(backward, `seed ${seed}`).toEqual(forward);
    }
  });

  it("I4: a DUPLICATED send places identically to its original", () => {
    // A page re-read, a replayed event, a retry under the same client id: the
    // same observation must not drift because it was considered twice.
    for (const seed of SEEDS) {
      const { sends, observed } = generate(seed);
      const doubled = sends.flatMap((s) => [s, { ...s }]);
      const places = doubled.map((s) => placeSend(s, observed));
      for (let i = 0; i < places.length; i += 2) {
        expect(places[i + 1], `seed ${seed}`).toEqual(places[i]);
      }
    }
  });

  it("I4: a LATE-ARRIVING log page only ever moves a send DOWN the transcript", () => {
    // The log growing under a send may take it from `tail` to `anchored`
    // (it is no longer the newest thing) or leave it where it is. It may never
    // take a send that was at the tail and lift it above the run, which is the
    // shape of every "it moved to the top" report.
    for (const seed of SEEDS) {
      const { sends, observed } = generate(seed);
      const later: LogObservation = { ...observed, tail: observed.tail + 10 };
      for (const s of sends) {
        const before = placeSend(s, observed);
        const after = placeSend(s, later);
        if (before.at === "anchored" || after.at === "anchored") {
          // An anchor is a fixed seq; growth cannot move it.
          if (before.at === "anchored" && after.at === "anchored") {
            expect(after.afterSeq, `seed ${seed}`).toBe(before.afterSeq);
          } else {
            // tail -> anchored is the only permitted transition, and only for a
            // send that had a real observation to be anchored to.
            expect(before.at, `seed ${seed}`).toBe("tail");
            expect(hasAnchor(s), `seed ${seed}`).toBe(true);
            expect(after.at, `seed ${seed}`).toBe("anchored");
          }
          continue;
        }
        expect(after.at, `seed ${seed}`).toBe(before.at);
      }
    }
  });

  it("placementRank orders the three places the way the transcript renders them", () => {
    expect(placementRank({ at: "anchored", afterSeq: 3 }))
      .toBeLessThan(placementRank({ at: "inRun", turnId: "T1" }));
    expect(placementRank({ at: "inRun", turnId: "T1" }))
      .toBeLessThan(placementRank({ at: "tail" }));
  });
});
