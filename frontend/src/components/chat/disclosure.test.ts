import { describe, it, expect } from "vitest";
import { openInitial, onLive, onToggle, MIN_VISIBLE_MS, type Disclosure } from "./disclosure";

/** Replay a sequence of events, the way the component will. Every step is a
 *  long way apart in time, so these exercise the placement rule and not the
 *  animation one; the animation tests below drive `now` themselves. */
const run = (start: Disclosure, evs: (boolean | "click")[], step = 10_000) => {
  let t = 0;
  return evs.reduce<Disclosure>(
    (d, e) => { t += step; return e === "click" ? onToggle(d, t) : onLive(d, e, t); }, start);
};

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

  it("a whole turn replaying produces no operator-driven transitions", () => {
    const replay = Array.from({ length: 40 }, (_, i) => i % 2 === 0);
    expect(run(openInitial(false), replay).byUser).toBe(false);
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


// ---------------------------------------------------------------------------
// WHAT ANIMATES. The rule is not who caused the change, it is whether the state
// being left was ever on screen. See the note in disclosure.ts.
// ---------------------------------------------------------------------------
describe("what animates", () => {
  const slow = MIN_VISIBLE_MS + 50;

  it("animates a close the reader could actually see", () => {
    // Work ran for a while with the group open, then the turn moved past it.
    const opened = onLive(openInitial(false, false, 0), true, 100);
    expect(onLive(opened, false, 100 + slow).animate).toBe(true);
  });

  it("does not animate a state that came and went faster than the animation", () => {
    const opened = onLive(openInitial(false, false, 0), true, 100);
    expect(onLive(opened, false, 100 + 20).animate).toBe(false);
  });

  it("never animates on mount", () => {
    expect(openInitial(true, false, 0).animate).toBe(false);
    expect(openInitial(false, true, 0).animate).toBe(false);
  });

  it("always animates the operator's own click", () => {
    // Even a double-click, which is the one case the visibility rule would
    // otherwise suppress. A click is definitely being watched.
    const d = onToggle(openInitial(false, false, 0), 10);
    expect(d.animate).toBe(true);
    expect(onToggle(d, 20).animate).toBe(true);
  });

  // THE REGRESSION THIS FILE EXISTS FOR. Re-attaching to a turn replays it from
  // offset 0, so every group opens and closes inside a few hundred ms. Reported
  // 2026-08-12 as "it replays a lot of animations real quick at once".
  it("animates nothing during a replay", () => {
    let d = openInitial(false, false, 0);
    let t = 0;
    const animated: boolean[] = [];
    // 40 liveness flips, 8ms apart: a replay walking a long turn.
    for (let i = 0; i < 40; i++) {
      t += 8;
      d = onLive(d, i % 2 === 0, t);
      animated.push(d.animate);
    }
    expect(animated.some(Boolean)).toBe(false);
  });

  it("still animates the real turn that follows the replay", () => {
    let d = openInitial(false, false, 0);
    let t = 0;
    for (let i = 0; i < 40; i++) { t += 8; d = onLive(d, i % 2 === 0, t); }
    // The replay catches up and real work starts: seconds, not milliseconds.
    d = onLive(d, true, t + 2000);
    d = onLive(d, false, t + 9000);
    expect(d.open).toBe(false);
    expect(d.animate).toBe(true);
  });
});
