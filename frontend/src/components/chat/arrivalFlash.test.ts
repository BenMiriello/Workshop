import { beforeEach, describe, expect, it } from "vitest";
import {
  ARRIVAL_FLASH_MS, _resetSpentFlashes, flashSpent, flashesOnArrival,
  flashesOnArrivalAt, flashesOnArrivalOnce, spendFlash,
} from "./arrivalFlash";

const MOUNT = Date.parse("2026-09-04T12:00:00Z");
const iso = (offsetMs: number) => new Date(MOUNT + offsetMs).toISOString();

describe("the arrival flash", () => {
  it("flashes a message that lands while the reader is looking", () => {
    const ts = iso(5_000);
    expect(flashesOnArrival(ts, MOUNT, MOUNT + 5_050)).toBe(true);
  });

  it("never flashes the history a thread opened with", () => {
    // Every row lighting at once is a strobe, not a signal.
    expect(flashesOnArrival(iso(-60_000), MOUNT, MOUNT + 10)).toBe(false);
    expect(flashesOnArrival(iso(0), MOUNT, MOUNT + 10)).toBe(false);
  });

  it("is spent once it has played, so a rebuilt node cannot replay it", () => {
    // THE REPORTED BUG, 2026-09-04. `ts > mountedAt` alone is permanent: an
    // hour into a session every message in it still carried the arrival class,
    // and anything that recreated those DOM nodes re-ran the animation on all
    // of them at once. The message below is a real arrival at +5s and does
    // flash then; a minute later, and an hour later, it does not.
    const ts = iso(5_000);
    expect(flashesOnArrival(ts, MOUNT, MOUNT + 5_050)).toBe(true);
    expect(flashesOnArrival(ts, MOUNT, MOUNT + 65_000)).toBe(false);
    expect(flashesOnArrival(ts, MOUNT, MOUNT + 3_600_000)).toBe(false);
  });

  it("does not strobe a backlog folded in after a reconnect", () => {
    // A tab that was hidden or dropped comes back and syncs a run of messages
    // at once. They are all newer than the mount and all older than the
    // window, so the transcript lands settled.
    const backlog = [iso(30_000), iso(45_000), iso(60_000), iso(90_000)];
    const now = MOUNT + 600_000;
    expect(backlog.map((t) => flashesOnArrival(t, MOUNT, now))).toEqual(
      [false, false, false, false],
    );
  });

  it("forgives a timestamp that reads as slightly in the future", () => {
    // The server writes `ts` at seconds resolution and its clock need not
    // agree with the browser's. "Not yet" is not "long ago", so this flashes.
    expect(flashesOnArrival(iso(5_000), MOUNT, MOUNT + 3_000)).toBe(true);
  });

  it("holds the boundary at the window", () => {
    const ts = iso(1_000);
    const at = MOUNT + 1_000;
    expect(flashesOnArrival(ts, MOUNT, at + ARRIVAL_FLASH_MS - 1)).toBe(true);
    expect(flashesOnArrival(ts, MOUNT, at + ARRIVAL_FLASH_MS)).toBe(false);
  });

  it("says no to a message with no timestamp, or an unreadable one", () => {
    expect(flashesOnArrival(undefined, MOUNT, MOUNT + 10)).toBe(false);
    expect(flashesOnArrival("", MOUNT, MOUNT + 10)).toBe(false);
    expect(flashesOnArrival("not a date", MOUNT, MOUNT + 10)).toBe(false);
  });
});

describe("the same rule over a tool step's epoch stamp", () => {
  it("flashes a call issued while the reader was looking, once", () => {
    // `seg.at` is seconds since the epoch and the backend REMOVES it when the
    // result lands, so only an open call can flash at all. A call that is
    // still open an hour later must not flash again when its node is rebuilt.
    const at = MOUNT + 2_000;
    expect(flashesOnArrivalAt(at, MOUNT, at + 100)).toBe(true);
    expect(flashesOnArrivalAt(at, MOUNT, at + 3_600_000)).toBe(false);
  });

  it("never flashes a call that was already running when the reader arrived", () => {
    expect(flashesOnArrivalAt(MOUNT - 5_000, MOUNT, MOUNT + 10)).toBe(false);
  });

  it("says no when there is no stamp to assert anything from", () => {
    expect(flashesOnArrivalAt(undefined, MOUNT, MOUNT + 10)).toBe(false);
    expect(flashesOnArrivalAt(Number.NaN, MOUNT, MOUNT + 10)).toBe(false);
  });
});


// THE BURST. This is the case the time window alone cannot answer, and it is
// the one the operator reported three times (2026-09-04, and twice on
// 2026-09-16: "it replays a significant number of animations for every new
// message", "every time I reopen the chat... it sort of replays animating the
// last several messages").
//
// A long agentic turn lands messages a second or two apart, so several are
// inside ARRIVAL_FLASH_MS at any instant. A CSS animation replays whenever its
// node is CREATED, and the list is rebuilt for many ordinary reasons, so every
// message still inside its window re-animates together. Widening or narrowing
// the window cannot fix that; only remembering that a flash already played can.
describe("a flash is spent once it has played", () => {
  beforeEach(() => _resetSpentFlashes());

  it("does not flash the same message twice, however often its node is rebuilt", () => {
    const ts = iso(1_000);
    // First render: inside the window, never flashed, so it flashes.
    expect(flashesOnArrivalOnce("m1", ts, MOUNT, MOUNT + 1_100)).toBe(true);
    spendFlash("m1");
    // The list is rebuilt half a second later and the message is STILL inside
    // its window. Before the spent set this returned true and the animation ran
    // a second time.
    expect(flashesOnArrivalOnce("m1", ts, MOUNT, MOUNT + 1_600)).toBe(false);
  });

  it("settles a whole burst, not just the newest message in it", () => {
    // Five messages one second apart, all inside the window, as a live turn
    // produces. Each flashes exactly once.
    const burst = [0, 1, 2, 3, 4].map((i) => ({ id: `b${i}`, ts: iso(1_000 * (i + 1)) }));
    const now = MOUNT + 5_500;
    for (const m of burst) {
      expect(flashesOnArrivalOnce(m.id, m.ts, MOUNT, now)).toBe(true);
      spendFlash(m.id);
    }
    // The rebuild. Every one of them is still inside ARRIVAL_FLASH_MS of its own
    // timestamp, so the window says yes to all five and the set says no to all
    // five. That disagreement IS the bug, stated as a test.
    for (const m of burst) {
      expect(flashesOnArrival(m.ts, MOUNT, now)).toBe(true);
      expect(flashesOnArrivalOnce(m.id, m.ts, MOUNT, now)).toBe(false);
    }
  });

  it("still refuses a message older than the mount, spent or not", () => {
    expect(flashesOnArrivalOnce("old", iso(-1_000), MOUNT, MOUNT + 10)).toBe(false);
    expect(flashSpent("old")).toBe(false);
  });

  it("spending is idempotent and does not need the message to be eligible", () => {
    spendFlash("x");
    spendFlash("x");
    expect(flashSpent("x")).toBe(true);
  });

  it("keeps each message's flash independent", () => {
    spendFlash("a");
    expect(flashesOnArrivalOnce("a", iso(1_000), MOUNT, MOUNT + 1_100)).toBe(false);
    expect(flashesOnArrivalOnce("b", iso(1_000), MOUNT, MOUNT + 1_100)).toBe(true);
  });
});
