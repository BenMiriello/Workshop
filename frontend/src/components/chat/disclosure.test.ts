import { describe, it, expect } from "vitest";
import { openInitial, onLive, onToggle, type Disclosure } from "./disclosure";

/** Replay a sequence of events, the way the component will. */
const run = (start: Disclosure, evs: (boolean | "click")[]) =>
  evs.reduce<Disclosure>((d, e) => (e === "click" ? onToggle(d) : onLive(d, e)), start);

describe("automatic opening and closing", () => {
  it("opens while work is in flight and closes when it finishes", () => {
    const d = run(openInitial(false), [true]);
    expect(d.open).toBe(true);
    expect(run(d, [false]).open).toBe(false);
  });

  it("starts open if it mounts mid-flight", () => {
    // A group first renders once it has two steps, by which point the second
    // is usually already running. It must not need a transition to notice.
    expect(openInitial(true).open).toBe(true);
  });

  it("does not slam shut a settled group the reader opened the turn on", () => {
    // defaultOpen with live=false. Without tracking the previous live value,
    // the first onLive(false) is indistinguishable from "just finished" and
    // would close it on the spot.
    const d = openInitial(false, true);
    expect(run(d, [false, false]).open).toBe(true);
  });

  it("ignores a repeated event at the same state", () => {
    const d = run(openInitial(false), [true]);
    // Same object back, so a React setter bails out instead of re-rendering.
    expect(onLive(d, true)).toBe(d);
  });
});

describe("the operator's action is the last word", () => {
  it("never auto-closes a group the operator opened", () => {
    // The failure this rule exists to prevent: reading an earlier group during
    // a running turn, and having the next step to finish shut it again.
    const d = run(openInitial(false), ["click"]);
    expect(d.open).toBe(true);
    expect(run(d, [true, false, true, false]).open).toBe(true);
  });

  it("never auto-reopens a group the operator closed", () => {
    // Cuts the same way round. Closing a noisy run mid-flight has to stick, or
    // the next step reopens it and the click did nothing.
    const d = run(openInitial(true), ["click"]);
    expect(d.open).toBe(false);
    expect(run(d, [false, true]).open).toBe(false);
  });

  it("keeps tracking liveness while hands off, so control could be handed back", () => {
    const d = run(openInitial(false), ["click", true]);
    expect(d.live).toBe(true);
    expect(d.byUser).toBe(true);
  });

  it("a click always flips what is on screen, whatever moved it last", () => {
    const auto = run(openInitial(false), [true]);   // opened automatically
    expect(onToggle(auto).open).toBe(false);
    expect(onToggle(auto).byUser).toBe(true);
  });
});

describe("the whole sequence of a real turn", () => {
  it("opens on start, closes on finish, and holds what the operator held", () => {
    // Group runs and finishes: closed.
    let d = run(openInitial(false), [true, false]);
    expect(d.open).toBe(false);
    // Operator opens it to read it.
    d = onToggle(d);
    expect(d.open).toBe(true);
    // Three more groups' worth of work streams past. It stays open.
    d = run(d, [true, false, true, false, true, false]);
    expect(d.open).toBe(true);
  });
});
