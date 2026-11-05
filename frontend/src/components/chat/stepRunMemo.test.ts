import { describe, it, expect } from "vitest";
import { applyStreamEvent } from "./segments";
import { buildTurnBlocks, type TurnItem } from "./grouping";
import { runTicks, sameStepRun } from "./stepRunMemo";
import type { ChatSegment } from "../../lib/chat";

// The rule that lets a stream event redraw only the stretch it changed. The
// reducer keeps untouched segments by identity and buildTurnBlocks rebuilds its
// arrays every call, so these run the real pair rather than hand-made items.

const cfg = {};
const runs = (segs: ChatSegment[]): TurnItem[][] =>
  buildTurnBlocks(segs).flatMap((b) => (b.t === "run" ? [b.items] : []));
const props = (items: TurnItem[], nowMs = 1000, current = false) =>
  ({ items, cfg, nowMs, current, onAnswerPermission: undefined });

function longTurn(): ChatSegment[] {
  let segs: ChatSegment[] = [];
  segs = applyStreamEvent(segs, { kind: "tool", name: "Read", input: "a", id: "t1" });
  segs = applyStreamEvent(segs, { kind: "tool_result", tool_use_id: "t1", text: "" });
  segs = applyStreamEvent(segs, { kind: "permission", id: "r1", tool: "Write", label: "Write" });
  segs = applyStreamEvent(segs, { kind: "text", content: "Now the rest." });
  segs = applyStreamEvent(segs, { kind: "tool", name: "Bash", input: "ls", id: "t2" });
  return segs;
}

describe("sameStepRun", () => {
  it("skips a settled stretch when an event lands elsewhere", () => {
    const before = longTurn();
    const after = applyStreamEvent(before, { kind: "tool", name: "Grep", input: "x", id: "t3" });
    const [a] = runs(before);
    const [b] = runs(after);
    expect(a).not.toBe(b);
    expect(sameStepRun(props(a), props(b))).toBe(true);
  });

  it("redraws the stretch an answered approval sits in, and only that one", () => {
    const before = longTurn();
    const after = applyStreamEvent(before, { kind: "permission_resolved", id: "r1", state: "allowed" });
    const [a1, a2] = runs(before);
    const [b1, b2] = runs(after);
    expect(sameStepRun(props(a1), props(b1))).toBe(false);
    expect(sameStepRun(props(a2, 1000, true), props(b2, 1000, true))).toBe(true);
  });

  it("redraws a stretch that gained a step", () => {
    const before = longTurn();
    const after = applyStreamEvent(before, { kind: "tool", name: "Read", input: "b", id: "t4" });
    const a = runs(before)[1];
    const b = runs(after)[1];
    expect(sameStepRun(props(a, 1000, true), props(b, 1000, true))).toBe(false);
  });

  it("follows the clock only while something in the stretch is running", () => {
    const [settled, running] = runs(longTurn());
    expect(runTicks(settled)).toBe(false);
    expect(runTicks(running)).toBe(true);
    expect(sameStepRun(props(settled, 1000), props(settled, 2000))).toBe(true);
    expect(sameStepRun(props(running, 1000), props(running, 2000))).toBe(false);
  });

  it("redraws when the stretch stops being the current one", () => {
    const [, running] = runs(longTurn());
    expect(sameStepRun(props(running, 1000, true), props(running, 1000, false))).toBe(false);
  });
});

describe("permission_resolved keeps identity when nothing changed", () => {
  it("a replayed answer returns the same array", () => {
    const answered = applyStreamEvent(longTurn(), { kind: "permission_resolved", id: "r1", state: "allowed" });
    const again = applyStreamEvent(answered, { kind: "permission_resolved", id: "r1", state: "allowed" });
    expect(again).toBe(answered);
  });
});
