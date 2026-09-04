import { describe, expect, it } from "vitest";
import { ARRIVAL_FLASH_MS, flashesOnArrival, flashesOnArrivalAt } from "./arrivalFlash";

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
