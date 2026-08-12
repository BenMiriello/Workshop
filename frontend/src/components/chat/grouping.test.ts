import { describe, it, expect } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import {
  buildTurnBlocks, groupSummary, groupDetail, splitRun, turnWorkSummary,
  isRunning, isFailed, mergeClass, stepDuration, runningSeconds, turnDuration, RUN_ROW_CAP,
} from "./grouping";

const read = (path: string, extra: Partial<Extract<ChatSegment, { t: "tool" }>> = {}): ChatSegment =>
  ({ t: "tool", name: "Read", input: path, ok: true, ...extra });
const grep = (q: string): ChatSegment => ({ t: "tool", name: "Grep", input: q, ok: true });
const bash = (cmd: string, extra = {}): ChatSegment =>
  ({ t: "tool", name: "Bash", input: cmd, ok: true, ...extra });
const text = (s: string): ChatSegment => ({ t: "text", text: s });

describe("buildTurnBlocks", () => {
  it("merges a consecutive run of one class into one group", () => {
    const blocks = buildTurnBlocks([read("a.ts"), read("b.ts"), read("c.ts")]);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].t).toBe("run");
    const items = (blocks[0] as { t: "run"; items: unknown[] }).items;
    expect(items).toHaveLength(1);
    expect(groupSummary(items[0] as never)).toBe("Read 3 files");
  });

  it("merges INTERLEAVED classes, which is the shape a strict rule misses", () => {
    // Forty reads alternating with forty greps has no two adjacent steps of one
    // class. A "consecutive only" rule produces eighty rows and buys nothing.
    const work: ChatSegment[] = [];
    for (let i = 0; i < 40; i++) { work.push(read(`f${i}.ts`)); work.push(grep(`q${i}`)); }
    const items = (buildTurnBlocks(work)[0] as { items: unknown[] }).items;
    expect(items).toHaveLength(2);
    expect(items.map((i) => groupSummary(i as never)))
      .toEqual(["Read 40 files", "Searched 40 times"]);
  });

  it("keeps first-appearance order of classes within a window", () => {
    const items = (buildTurnBlocks([grep("x"), read("a"), grep("y"), read("b")])[0] as { items: unknown[] }).items;
    expect(items.map((i) => groupSummary(i as never)))
      .toEqual(["Searched 2 times", "Read 2 files"]);
  });

  it("a command breaks the window and nothing groupable hops over it", () => {
    const items = (buildTurnBlocks([read("a"), read("b"), bash("rm -rf build/"), read("c"), read("d")])[0] as { items: { t: string }[] }).items;
    expect(items.map((i) => i.t)).toEqual(["group", "step", "group"]);
    // The command keeps its object. A collapsed row that said "Ran a command"
    // is the documented failure this rule exists to avoid.
    expect((items[1] as unknown as { seg: ChatSegment }).seg).toMatchObject({ input: "rm -rf build/" });
  });

  it("never merges a write or a command", () => {
    const items = (buildTurnBlocks([bash("ls"), bash("pwd"), bash("id")])[0] as { items: { t: string }[] }).items;
    expect(items.map((i) => i.t)).toEqual(["step", "step", "step"]);
  });

  it("a group of one collapses back to a plain step", () => {
    const items = (buildTurnBlocks([read("only.ts")])[0] as { items: { t: string }[] }).items;
    expect(items).toHaveLength(1);
    expect(items[0].t).toBe("step");
  });

  it("prose closes the run and becomes its own block", () => {
    const blocks = buildTurnBlocks([read("a"), text("Found it."), read("b"), read("c")]);
    expect(blocks.map((b) => b.t)).toEqual(["run", "prose", "run"]);
    expect((blocks[1] as { text: string }).text).toBe("Found it.");
  });

  it("drops whitespace-only prose without closing anything twice", () => {
    const blocks = buildTurnBlocks([read("a"), text("   "), read("b")]);
    // The empty prose still ends the run (it is a real segment boundary), but
    // it does not draw a block of its own.
    expect(blocks.map((b) => b.t)).toEqual(["run", "run"]);
  });

  it("handles a turn with no prose at all", () => {
    const blocks = buildTurnBlocks([read("a"), read("b"), bash("make")]);
    expect(blocks.map((b) => b.t)).toEqual(["run"]);
  });

  it("is total on empty input", () => {
    expect(buildTurnBlocks([])).toEqual([]);
  });

  it("a failed step stands alone and is never merged away", () => {
    const items = (buildTurnBlocks([
      read("a"), { t: "tool", name: "Read", input: "missing.ts", ok: false, error: "ENOENT" }, read("c"),
    ])[0] as { items: { t: string }[] }).items;
    expect(items.map((i) => i.t)).toEqual(["step", "step", "step"]);
  });

  it("the running step stands alone", () => {
    const items = (buildTurnBlocks([read("a"), { t: "tool", name: "Read", input: "b", at: 1000 }])[0] as { items: { t: string }[] }).items;
    expect(items.map((i) => i.t)).toEqual(["step", "step"]);
  });

  it("LEGACY segments with no outcome still group", () => {
    // Every stored turn predates the outcome fields. If "succeeded" were
    // required, the whole redesign would be invisible on existing threads.
    const items = (buildTurnBlocks([
      { t: "tool", name: "Read", input: "a" }, { t: "tool", name: "Read", input: "b" },
    ])[0] as { items: unknown[] }).items;
    expect(items).toHaveLength(1);
    expect(groupSummary(items[0] as never)).toBe("Read 2 files");
  });

  it("a subagent is never merged into a group", () => {
    const agent: ChatSegment = {
      t: "agent", name: "Explore", input: "look", id: "a1", steps: [], done: true, result: "ok",
    };
    const items = (buildTurnBlocks([read("a"), agent, read("b")])[0] as { items: { t: string }[] }).items;
    expect(items.map((i) => i.t)).toEqual(["step", "step", "step"]);
  });
});

describe("group summary lines", () => {
  it("counts with the right plural", () => {
    const one = (buildTurnBlocks([read("a"), read("b")])[0] as { items: unknown[] }).items[0];
    expect(groupSummary(one as never)).toBe("Read 2 files");
  });

  it("keeps the objects on the detail line", () => {
    const g = (buildTurnBlocks([read("a.ts"), read("b.ts")])[0] as { items: unknown[] }).items[0];
    expect(groupDetail(g as never)).toBe("a.ts, b.ts");
  });

  it("folds newlines out of the detail line so it cannot wrap", () => {
    const g = (buildTurnBlocks([read("a\nb"), read("c")])[0] as { items: unknown[] }).items[0];
    expect(groupDetail(g as never)).toBe("a b, c");
  });
});

describe("splitRun", () => {
  const items = Array.from({ length: 20 }, (_, i) => ({ t: "step" as const, seg: read(`f${i}`) }));

  it("keeps everything under the cap", () => {
    const { shown, hidden } = splitRun(items.slice(0, RUN_ROW_CAP));
    expect(shown).toHaveLength(RUN_ROW_CAP);
    expect(hidden).toHaveLength(0);
  });

  it("keeps the TAIL, because that is the work being followed", () => {
    const { shown, hidden } = splitRun(items);
    expect(shown).toHaveLength(RUN_ROW_CAP);
    expect(hidden).toHaveLength(20 - RUN_ROW_CAP);
    expect((shown[shown.length - 1] as { seg: { input: string } }).seg.input).toBe("f19");
  });

  it("never hides a pending approval, wherever it sits", () => {
    const approval: ChatSegment = {
      t: "approval", id: "r1", tool: "Bash", label: "Bash", detail: "", input: {},
      suggestions: [], state: "pending",
    };
    const withApproval = [{ t: "step" as const, seg: approval }, ...items];
    const { shown } = splitRun(withApproval);
    expect(shown.some((i) => i.t === "step" && i.seg.t === "approval")).toBe(true);
  });

  it("never hides a failure", () => {
    const failed: ChatSegment = { t: "tool", name: "Bash", input: "make", ok: false };
    const { shown } = splitRun([{ t: "step" as const, seg: failed }, ...items]);
    expect(shown.some((i) => i.t === "step" && isFailed(i.seg))).toBe(true);
  });

  it("preserves order in both halves", () => {
    const { shown, hidden } = splitRun(items);
    const idx = (i: unknown) => Number((i as { seg: { input: string } }).seg.input.slice(1));
    expect(hidden.map(idx)).toEqual([...hidden.map(idx)].sort((a, b) => a - b));
    expect(shown.map(idx)).toEqual([...shown.map(idx)].sort((a, b) => a - b));
  });
});

describe("state predicates", () => {
  it("running needs a start and no outcome", () => {
    expect(isRunning({ t: "tool", name: "Bash", input: "x", at: 1 })).toBe(true);
    expect(isRunning({ t: "tool", name: "Bash", input: "x", at: 1, ok: true })).toBe(false);
    expect(isRunning({ t: "tool", name: "Bash", input: "x" })).toBe(false);
  });

  it("an unknown outcome is not a failure", () => {
    // A turn that ended before its result arrived leaves ok unset. Reading that
    // as failure would paint every step of an interrupted turn red.
    expect(isFailed({ t: "tool", name: "Bash", input: "x" })).toBe(false);
    expect(isFailed({ t: "tool", name: "Bash", input: "x", ok: false })).toBe(true);
  });

  it("only allowlisted read-only tools have a merge class", () => {
    expect(mergeClass(read("a"))).not.toBeNull();
    expect(mergeClass(bash("ls"))).toBeNull();
    expect(mergeClass({ t: "tool", name: "Write", input: "a", ok: true })).toBeNull();
    expect(mergeClass({ t: "tool", name: "mcp__thing__do", input: "a", ok: true })).toBeNull();
  });
});

describe("durations", () => {
  it("says nothing below a second", () => {
    expect(stepDuration({ t: "tool", name: "Read", input: "a", ms: 40 })).toBe("");
  });

  it("formats seconds and minutes", () => {
    expect(stepDuration({ t: "tool", name: "Bash", input: "a", ms: 12400 })).toBe("12s");
    expect(stepDuration({ t: "tool", name: "Bash", input: "a", ms: 134000 })).toBe("2m 14s");
  });

  it("a running step stays silent under the attention limit, then ticks", () => {
    const seg: ChatSegment = { t: "tool", name: "Bash", input: "x", at: 1000 };
    expect(runningSeconds(seg, 1_004_000)).toBe(0);
    expect(runningSeconds(seg, 1_012_000)).toBe(12);
  });

  it("a finished step never ticks", () => {
    expect(runningSeconds({ t: "tool", name: "Bash", input: "x", at: 1000, ok: true }, 9_999_000)).toBe(0);
  });
});

describe("turnWorkSummary", () => {
  it("counts steps and names what changed", () => {
    expect(turnWorkSummary([
      read("a"), read("b"), { t: "tool", name: "Edit", input: "x.ts", ok: true }, bash("make"),
    ])).toBe("4 steps · 1 edit · 1 command");
  });

  it("names failures, because that is why a settled turn gets opened", () => {
    // The command is still counted: it was attempted, and "1 command · 1 failed"
    // is the honest pair. Counting only the failure would hide what failed.
    expect(turnWorkSummary([read("a"), { t: "tool", name: "Bash", input: "make", ok: false }]))
      .toBe("2 steps · 1 command · 1 failed");
  });

  it("is empty when the turn did no work", () => {
    expect(turnWorkSummary([text("hello")])).toBe("");
  });
});

describe("turnDuration", () => {
  it("says nothing about a turn nobody waited on", () => {
    expect(turnDuration(4_000)).toBe("");
    expect(turnDuration(29_999)).toBe("");
  });

  it("formats a wait worth naming", () => {
    expect(turnDuration(45_000)).toBe("45s");
    expect(turnDuration(413_000)).toBe("6m 53s");
    expect(turnDuration(7_500_000)).toBe("2h 5m");
  });

  it("says nothing rather than guessing on a missing or impossible span", () => {
    // A reply the operator interrupted renders as several blocks, and only the
    // last carries both ends. The others must print no number, not a wrong one.
    expect(turnDuration(undefined)).toBe("");
    expect(turnDuration(NaN)).toBe("");
    expect(turnDuration(-5000)).toBe("");
  });
});

describe("turnWorkSummary counts approvals", () => {
  // A settled turn shows ONLY this line, so a shape that summarises to nothing
  // is a shape that disappears. A turn whose whole body is a note to the
  // operator and a permission it was refused has no tool call in it.
  it("a denied approval is still a step", () => {
    const denied: ChatSegment = {
      t: "approval", id: "r1", tool: "Bash", label: "Bash", detail: "", input: {},
      suggestions: [], state: "denied",
    };
    expect(turnWorkSummary([text("I will run the build."), denied]))
      .toBe("1 step · 1 approval");
  });
});
