import { describe, it, expect } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import {
  buildTurnBlocks, groupSummary, groupTools, groupIsRunning, turnWorkSummary,
  isRunning, isFailed, stepDuration, runningSeconds, turnDuration,
  type TurnItem, type TurnBlock,
} from "./grouping";

const read = (path: string, extra: Partial<Extract<ChatSegment, { t: "tool" }>> = {}): ChatSegment =>
  ({ t: "tool", name: "Read", input: path, ok: true, ...extra });
const grep = (q: string): ChatSegment => ({ t: "tool", name: "Grep", input: q, ok: true });
const bash = (cmd: string, extra = {}): ChatSegment =>
  ({ t: "tool", name: "Bash", input: cmd, ok: true, ...extra });
const edit = (path: string): ChatSegment => ({ t: "tool", name: "Edit", input: path, ok: true });
const text = (s: string): ChatSegment => ({ t: "text", text: s });
const agent = (name: string): ChatSegment =>
  ({ t: "agent", name, input: "go", id: `a-${name}`, steps: [], done: true, result: "ok" });
const approval = (state: "pending" | "denied"): ChatSegment =>
  ({ t: "approval", id: "r1", tool: "Bash", label: "Bash", detail: "", input: {}, suggestions: [], state });

/** The items of the nth run block. Every test here builds one on purpose, so a
 *  missing or wrong block type throws rather than silently passing. */
const items = (blocks: TurnBlock[], i = 0): TurnItem[] => {
  const b = blocks[i];
  if (!b || b.t !== "run") throw new Error(`expected a run block at ${i}, got ${b?.t}`);
  return b.items;
};

describe("buildTurnBlocks: adjacent coalescing by part type", () => {
  it("coalesces adjacent tool calls WHATEVER the tool is", () => {
    // The rule is positional, not semantic. Nothing here asks what a Bash
    // command did; that classifier existed, compressed less, and reordered.
    const blocks = buildTurnBlocks([read("a.ts"), bash("ls"), grep("x"), edit("b.ts")]);
    expect(blocks).toHaveLength(1);
    expect(items(blocks)).toHaveLength(1);
    expect(groupSummary(items(blocks)[0] as never)).toBe("4 steps");
  });

  it("PRESERVES ORDER EXACTLY, which is the property the old window broke", () => {
    // Measured on the real logs, the class-window rule displayed 282 of 4,296
    // runs out of chronological order (397 inversions). A transcript that lies
    // about what happened first is worse than a long one. This must never
    // regress, so it is asserted on the steps themselves.
    const work = [read("1"), grep("2"), bash("3"), read("4"), grep("5")];
    const g = items(buildTurnBlocks(work))[0] as Extract<TurnItem, { t: "group" }>;
    expect(g.steps.map((s) => s.input)).toEqual(["1", "2", "3", "4", "5"]);
  });

  it("a group of one collapses back to a plain step", () => {
    const got = items(buildTurnBlocks([read("only.ts")]));
    expect(got).toHaveLength(1);
    expect(got[0].t).toBe("step");
  });

  it("is total on empty input", () => {
    expect(buildTurnBlocks([])).toEqual([]);
  });
});

describe("what breaks a group", () => {
  it("prose closes the run and becomes its own block", () => {
    const blocks = buildTurnBlocks([read("a"), read("b"), text("Found it."), read("c"), read("d")]);
    expect(blocks.map((b) => b.t)).toEqual(["run", "prose", "run"]);
    expect((blocks[1] as { text: string }).text).toBe("Found it.");
  });

  it("drops whitespace-only prose without drawing a block for it", () => {
    const blocks = buildTurnBlocks([read("a"), text("   "), read("b")]);
    // It still ends the run: it is a real segment boundary either way.
    expect(blocks.map((b) => b.t)).toEqual(["run", "run"]);
  });

  it("a subagent breaks the group and stays its own row", () => {
    // It is the one step that CONTAINS steps, so folding it into a count of
    // sibling tool calls would hide a whole delegation inside a number.
    const got = items(buildTurnBlocks([read("a"), read("b"), agent("Explore"), read("c"), read("d")]));
    expect(got.map((i) => i.t)).toEqual(["group", "step", "group"]);
  });

  it("an approval breaks the group and stays its own row", () => {
    // It is a control. A pending one is the only thing that can restart the
    // turn, so it can never be behind a count.
    const got = items(buildTurnBlocks([read("a"), read("b"), approval("pending"), read("c"), read("d")]));
    expect(got.map((i) => i.t)).toEqual(["group", "step", "group"]);
  });

  it("nothing else breaks a group, including a failure or a running step", () => {
    // Both used to stand alone under the allowlist rule. They no longer need
    // to: the group names its failures in the header and opens onto every step
    // in order, so neither is hidden by being inside one.
    const got = items(buildTurnBlocks([
      read("a"),
      { t: "tool", name: "Read", input: "missing.ts", ok: false, error: "ENOENT" },
      { t: "tool", name: "Bash", input: "slow", at: 1000 },
    ]));
    expect(got).toHaveLength(1);
    expect(got[0].t).toBe("group");
  });
});

describe("the group header is a count, never a description", () => {
  it("counts, with the right plural", () => {
    const g = items(buildTurnBlocks([read("a"), read("b")]))[0];
    expect(groupSummary(g as never)).toBe("2 steps");
  });

  it("names failures, because that is the reason to open it", () => {
    const g = items(buildTurnBlocks([
      read("a"),
      { t: "tool", name: "Bash", input: "make", ok: false },
      { t: "tool", name: "Bash", input: "make", ok: false },
    ]))[0];
    expect(groupSummary(g as never)).toBe("3 steps, 2 failed");
  });

  it("NEVER paraphrases what a step did, not even a destructive one", () => {
    // microsoft/vscode#292985: `rm -rf important_dir` collapsed to "Executed
    // command in terminal". A count cannot make that mistake, which is why no
    // merge allowlist is needed to keep a destructive command out of a group.
    const g = items(buildTurnBlocks([read("a"), bash("rm -rf important_dir")]))[0] as
      Extract<TurnItem, { t: "group" }>;
    const line = `${groupSummary(g)} ${groupTools(g)}`;
    expect(line).not.toMatch(/execut|ran|command|deleted|removed/i);
    // And the command itself is still there, in full, on its own row inside.
    expect(g.steps.map((s) => s.input)).toContain("rm -rf important_dir");
  });

  it("lists distinct tool names in first-appearance order", () => {
    const g = items(buildTurnBlocks([bash("ls"), read("a"), bash("pwd"), grep("x")]))[0];
    expect(groupTools(g as never)).toBe("Bash, Read, Grep");
  });

  it("opens itself while a step inside it is still running", () => {
    // assistant-ui's "auto-expand during streaming". It is what makes
    // collapsed-by-default cost nothing: work in flight is always visible.
    const live = items(buildTurnBlocks([read("a"), { t: "tool", name: "Bash", input: "x", at: 1 }]))[0];
    const done = items(buildTurnBlocks([read("a"), read("b")]))[0];
    expect(groupIsRunning(live as never)).toBe(true);
    expect(groupIsRunning(done as never)).toBe(false);
  });
});

describe("legacy segments", () => {
  it("groups turns stored before the outcome fields existed", () => {
    // Every stored turn predates them. If an outcome were required, the whole
    // rule would be invisible on existing threads.
    const got = items(buildTurnBlocks([
      { t: "tool", name: "Read", input: "a" }, { t: "tool", name: "Read", input: "b" },
    ]));
    expect(got).toHaveLength(1);
    expect(groupSummary(got[0] as never)).toBe("2 steps");
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
  // The turn line and the group line now speak the same vocabulary: counts of
  // steps. What a turn CHANGED is answered by the receipts row, which is
  // already outside every fold, rather than by a second summary dialect here.
  it("counts steps", () => {
    expect(turnWorkSummary([read("a"), read("b"), edit("x.ts"), bash("make")])).toBe("4 steps");
  });

  it("names failures, because that is why a settled turn gets opened", () => {
    expect(turnWorkSummary([read("a"), { t: "tool", name: "Bash", input: "make", ok: false }]))
      .toBe("2 steps · 1 failed");
  });

  it("counts a subagent as a step and names it", () => {
    expect(turnWorkSummary([read("a"), agent("Explore")])).toBe("2 steps · 1 agent");
  });

  it("a denied approval is still a step", () => {
    // A settled turn shows ONLY this line, so a shape that summarises to
    // nothing is a shape that disappears. A turn whose whole body was a note
    // and a refused permission has no tool call in it at all.
    expect(turnWorkSummary([text("I will run the build."), approval("denied")]))
      .toBe("1 step · 1 approval");
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
