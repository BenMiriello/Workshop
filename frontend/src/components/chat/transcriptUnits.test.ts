import { describe, expect, it } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import { cutPointOf, splitAtUnits, unitCount, unitOfSegmentIndex } from "./transcriptUnits";

const text = (s: string): ChatSegment => ({ t: "text", text: s });
const tool = (n: string): ChatSegment => ({ t: "tool", name: n, input: "" });
const agent = (steps: ChatSegment[], done = false, result = ""): ChatSegment =>
  ({ t: "agent", name: "Explore", input: "look", id: "a1", steps, done, result });

describe("unitCount", () => {
  it("counts a flat reply as its segments", () => {
    expect(unitCount([text("a"), tool("Read")])).toBe(2);
  });

  it("counts every step a delegate took", () => {
    // THE BUG, stated as a number. This chip was one segment and is six units;
    // on the operator's thread one chip held 175 steps and the cut coordinate
    // did not move once for any of them.
    expect(unitCount([agent([tool("a"), tool("b"), tool("c"), tool("d"), tool("e")])])).toBe(6);
  });

  it("is zero for a reply that produced nothing", () => {
    expect(unitCount([])).toBe(0);
  });
});

describe("unitOfSegmentIndex", () => {
  it("agrees with the segment index when nothing is nested", () => {
    expect(unitOfSegmentIndex([text("a"), text("b"), text("c")], 2)).toBe(2);
  });

  it("carries a legacy cut past the steps that were inside it", () => {
    // A message recorded before at_unit existed rendered BELOW the whole chip,
    // because the chip was one indivisible segment. It keeps rendering there:
    // this fix does not reach back and move messages already on disk.
    expect(unitOfSegmentIndex([text("a"), agent([tool("x"), tool("y")]), text("b")], 2)).toBe(4);
  });
});

describe("splitAtUnits inside a delegation", () => {
  const steps = [tool("one"), tool("two"), tool("three"), tool("four")];

  it("divides a chip at the step the message landed on", () => {
    const [before, after] = splitAtUnits([agent(steps, true, "found it")], [3]);
    // head + two steps above, the remaining two below.
    expect(before).toHaveLength(1);
    expect(after).toHaveLength(1);
    const b = before[0] as Extract<ChatSegment, { t: "agent" }>;
    const a = after[0] as Extract<ChatSegment, { t: "agent" }>;
    expect(b.steps.map((s) => (s.t === "tool" ? s.name : "?"))).toEqual(["one", "two"]);
    expect(a.steps.map((s) => (s.t === "tool" ? s.name : "?"))).toEqual(["three", "four"]);
  });

  it("does not show a conclusion above the message that preceded it", () => {
    // The piece above the operator's message is a delegation that had NOT
    // reported back when they typed. Carrying `done`/`result` up there would be
    // this whole file's bug in miniature.
    const [before, after] = splitAtUnits([agent(steps, true, "found it")], [3]);
    const b = before[0] as Extract<ChatSegment, { t: "agent" }>;
    const a = after[0] as Extract<ChatSegment, { t: "agent" }>;
    expect(b.done).toBe(false);
    expect(b.result).toBe("");
    expect(a.done).toBe(true);
    expect(a.result).toBe("found it");
  });

  it("keeps the whole chip below a message that arrived before it started", () => {
    const [before, after] = splitAtUnits([text("hi"), agent(steps)], [1]);
    expect(before).toEqual([text("hi")]);
    expect(after).toHaveLength(1);
    expect((after[0] as Extract<ChatSegment, { t: "agent" }>).steps).toHaveLength(4);
  });

  it("keeps the whole chip above a message that arrived after it ended", () => {
    const [before, after] = splitAtUnits([agent(steps, true, "done"), text("bye")], [5]);
    expect(before).toHaveLength(1);
    expect((before[0] as Extract<ChatSegment, { t: "agent" }>).done).toBe(true);
    expect(after).toEqual([text("bye")]);
  });

  it("places two messages sent during one delegation in different places", () => {
    // seq37 and seq38 both recorded 42 and could only render together. With unit
    // coordinates they land where they were actually typed.
    const parts = splitAtUnits([agent(steps)], [2, 4]);
    expect(parts).toHaveLength(3);
    const stepsOf = (p: ChatSegment[]) =>
      p.length ? (p[0] as Extract<ChatSegment, { t: "agent" }>).steps.length : 0;
    expect(stepsOf(parts[0])).toBe(1);
    expect(stepsOf(parts[1])).toBe(2);
    expect(stepsOf(parts[2])).toBe(1);
  });

  it("drops nothing: every step appears exactly once across the slices", () => {
    const parts = splitAtUnits([text("a"), agent(steps), text("z")], [2, 4]);
    const names = parts.flatMap((p) => p.flatMap((s) =>
      s.t === "agent" ? s.steps.map((x) => (x.t === "tool" ? x.name : "?"))
        : s.t === "tool" ? [s.name] : s.t === "text" ? [s.text] : []));
    expect(names).toEqual(["a", "one", "two", "three", "four", "z"]);
  });
});

describe("cutPointOf", () => {
  const segs = [text("a"), agent([tool("x"), tool("y")]), text("b")];

  it("uses at_unit when the server recorded one", () => {
    expect(cutPointOf({ at_unit: 3, at_segment: 1 }, segs)).toBe(3);
  });

  it("converts a legacy at_segment rather than ignoring it", () => {
    expect(cutPointOf({ at_segment: 2 }, segs)).toBe(4);
  });

  it("puts a message with no cut point below the whole reply", () => {
    expect(cutPointOf(undefined, segs)).toBe(unitCount(segs));
  });

  it("reads at_unit 0 as the top, not as missing", () => {
    // `0 || fallback` is the bug this guards: a message that arrived before the
    // turn produced anything belongs ABOVE all of it.
    expect(cutPointOf({ at_unit: 0 }, segs)).toBe(0);
  });
});
