import { describe, it, expect } from "vitest";
import {
  agentBrief, agentChipLine, agentSummary, applyStreamEvent, recentWorkStepCount,
  sealTrailingText, type StreamEvent,
} from "./segments";
import type { ChatSegment } from "../../lib/chat";

// The live reducer must produce what the server persists, because the same turn
// is rendered from this while it streams and from the server's segments after a
// reload. These pin the rules that differ from "append everything in order".

const feed = (evs: StreamEvent[]): ChatSegment[] =>
  evs.reduce<ChatSegment[]>((segs, ev) => applyStreamEvent(segs, ev), []);

const launch = (id = "tu_agent", agent = "general-purpose"): StreamEvent =>
  ({ kind: "tool", name: "Agent", input: "Read the tree and report", id, agent });

describe("subagent segments", () => {
  it("renders a launch as one agent segment, not a tool chip", () => {
    const segs = feed([launch("tu_1", "work-reviewer")]);
    expect(segs).toHaveLength(1);
    expect(segs[0]).toMatchObject({ t: "agent", name: "work-reviewer", id: "tu_1", done: false });
  });

  it("nests the subagent's steps inside it", () => {
    const segs = feed([
      launch("tu_1"),
      { kind: "tool", name: "Bash", input: "ls", parent: "tu_1" },
      { kind: "tool", name: "Read", input: "/tmp/x", parent: "tu_1" },
    ]);
    expect(segs).toHaveLength(1);
    const agent = segs[0] as Extract<ChatSegment, { t: "agent" }>;
    expect(agent.steps.map((s) => (s.t === "tool" ? s.name : s.t))).toEqual(["Bash", "Read"]);
  });

  it("keeps a subagent's text out of the answer", () => {
    const segs = feed([
      launch("tu_1"),
      { kind: "text", content: "checking the second file", parent: "tu_1" },
      { kind: "tool_result", tool_use_id: "tu_1", text: "three files" },
      { kind: "text", content: "It found three files." },
    ]);
    const answer = segs.filter((s) => s.t === "text").map((s) => (s as { text: string }).text).join("");
    expect(answer).toBe("It found three files.");
  });

  it("closes the segment on the agent's own result", () => {
    const segs = feed([launch("tu_1"), { kind: "tool_result", tool_use_id: "tu_1", text: "three files" }]);
    expect(segs[0]).toMatchObject({ done: true, result: "three files" });
  });

  it("leaves an unfinished subagent open", () => {
    const segs = feed([launch("tu_1"), { kind: "tool", name: "Bash", input: "ls", parent: "tu_1" }]);
    expect((segs[0] as Extract<ChatSegment, { t: "agent" }>).done).toBe(false);
  });

  it("keeps two subagents' work apart", () => {
    const segs = feed([
      launch("tu_1"),
      launch("tu_2", "work-reviewer"),
      { kind: "tool", name: "Bash", input: "one", parent: "tu_1" },
      { kind: "tool", name: "Read", input: "two", parent: "tu_2" },
    ]);
    const steps = segs.map((s) => (s as Extract<ChatSegment, { t: "agent" }>).steps.length);
    expect(steps).toEqual([1, 1]);
  });

  it("keeps the turn's own tools at the top level", () => {
    const segs = feed([
      { kind: "tool", name: "Grep", input: "foo" },
      launch("tu_1"),
      { kind: "tool", name: "Bash", input: "ls", parent: "tu_1" },
    ]);
    expect(segs.map((s) => s.t)).toEqual(["tool", "agent"]);
  });

  it("does not drop a step whose parent it never saw", () => {
    // A reconnect mid-turn can start the stream after the launch event.
    const segs = feed([{ kind: "tool", name: "Bash", input: "ls", parent: "tu_gone" }]);
    expect(segs).toEqual([{ t: "tool", name: "Bash", input: "ls" }]);
  });

  it("ignores an ordinary tool result", () => {
    const segs = feed([
      { kind: "tool", name: "Bash", input: "ls" },
      { kind: "tool_result", tool_use_id: "tu_bash", text: "a b c" },
    ]);
    expect(segs).toHaveLength(1);
  });

  it("merges consecutive text the same way the server does", () => {
    const segs = feed([{ kind: "text", content: "one " }, { kind: "text", content: "two" }]);
    expect(segs).toEqual([{ t: "text", text: "one two" }]);
  });

  it("does not mutate the segments it was given", () => {
    const before = feed([launch("tu_1")]);
    const snapshot = JSON.stringify(before);
    applyStreamEvent(before, { kind: "tool", name: "Bash", input: "ls", parent: "tu_1" });
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});

describe("recentWorkStepCount", () => {
  const tool = (name = "Bash"): ChatSegment => ({ t: "tool", name, input: "x" });
  const text = (t = "noted"): ChatSegment => ({ t: "text", text: t });

  it("folds everything on an older turn", () => {
    const work = [tool(), tool(), text()];
    expect(recentWorkStepCount(work, false, 2, 8)).toBe(0);
  });

  it("keeps the plain trailing window when nothing needs rescuing", () => {
    const work = [tool(), tool(), tool(), tool()];
    expect(recentWorkStepCount(work, true, 2, 8)).toBe(2);
  });

  it("shows everything when the turn is shorter than the window", () => {
    const work = [tool(), text()];
    expect(recentWorkStepCount(work, true, 2, 8)).toBe(2);
  });

  it("widens the window to sweep in a nearby text checkpoint", () => {
    // An acknowledgment of a mid-turn message, then two more tool calls before
    // the turn ends. The plain trailing-2 window would fold the ack the moment
    // those two calls landed — the exact bug this exists to prevent.
    const work = [tool(), tool(), text("Noted, will fold it in after this."), tool(), tool()];
    const n = recentWorkStepCount(work, true, 2, 8);
    expect(n).toBe(3);
    expect(work.slice(work.length - n).some((s) => s.t === "text")).toBe(true);
  });

  it("falls back to the plain window for a checkpoint too far back to reach", () => {
    // A chatty turn must still fold SOMETHING, or a long-enough turn stops
    // folding at all and the accordion loses its point — growing all the way
    // to the cap in search of a text segment that isn't there defeats that
    // just as much as never widening at all.
    const work = [text("early aside"), ...Array.from({ length: 20 }, () => tool())];
    expect(recentWorkStepCount(work, true, 2, 8)).toBe(2);
  });

  it("an empty turn stays empty", () => {
    expect(recentWorkStepCount([], true, 2, 8)).toBe(0);
  });
});

describe("agentSummary", () => {
  const agent = (over: Partial<Extract<ChatSegment, { t: "agent" }>>) =>
    ({ t: "agent", name: "general-purpose", input: "", id: "x", steps: [], done: false, result: "", ...over }) as Extract<ChatSegment, { t: "agent" }>;

  it("says it is working while it runs", () => {
    expect(agentSummary(agent({}))).toBe("working");
  });

  it("counts its steps", () => {
    const steps: ChatSegment[] = [{ t: "tool", name: "Bash", input: "ls" }, { t: "tool", name: "Read", input: "x" }];
    expect(agentSummary(agent({ steps, done: true }))).toBe("2 steps");
  });

  it("says done when it reported without calling a tool", () => {
    expect(agentSummary(agent({ done: true }))).toBe("done");
  });
});

describe("the collapsed chip is one line, and the brief is behind it", () => {
  const agent = (over: Partial<Extract<ChatSegment, { t: "agent" }>>) =>
    ({ t: "agent", name: "general-purpose", input: "", id: "x", steps: [], done: false, result: "", ...over }) as Extract<ChatSegment, { t: "agent" }>;

  it("carries the brief through the live reducer", () => {
    // Without this the brief never reached the client at all and opening a chip
    // showed the steps and the report but not what was actually asked for.
    const segs = feed([{
      kind: "tool", name: "Agent", id: "tu_1", agent: "Explore",
      input: "Find the chat store", detail: "Search the backend for...",
    }]);
    expect(segs[0]).toMatchObject({ t: "agent", input: "Find the chat store", detail: "Search the backend for..." });
  });

  it("folds a multi-line task into one line, so the row cannot wrap", () => {
    expect(agentChipLine(agent({ input: "Do the thing\n\nthen report back" })))
      .toBe("Do the thing then report back");
  });

  it("falls back to the agent's name when it was given no task line", () => {
    expect(agentChipLine(agent({ input: "  " }))).toBe("general-purpose");
  });

  it("reveals the full brief on open", () => {
    const brief = "Read every file under backend/services and report the ones that write threads.";
    expect(agentBrief(agent({ input: "Audit the writers", detail: brief }))).toBe(brief);
  });

  it("reveals nothing when there is no brief to reveal", () => {
    expect(agentBrief(agent({ input: "Audit the writers" }))).toBe("");
  });

  it("reveals nothing when the brief is only the chip line again", () => {
    // A disclosure that opens onto a repeat of its own label is worse than none.
    expect(agentBrief(agent({ input: "Audit the writers", detail: "Audit the writers" }))).toBe("");
  });
});

describe("sealTrailingText", () => {
  // The half of the placement fix that is not a coordinate. Text streams into
  // the trailing segment, so without a seal the reply is cut where the message
  // landed and then text written afterwards merges back ABOVE the cut — the
  // operator's message renders below words that were written after it.
  const stream = (segs: ChatSegment[], ev: StreamEvent) => applyStreamEvent(segs, ev);

  it("stops later text merging into text that preceded the message", () => {
    let segs: ChatSegment[] = stream([], { kind: "text", content: "A" });
    segs = sealTrailingText(segs);
    segs = stream(segs, { kind: "text", content: "B" });
    expect(segs.map((s) => (s.t === "text" ? s.text : "?"))).toEqual(["A", "B"]);
  });

  it("leaves an uninterrupted reply as one text segment", () => {
    let segs: ChatSegment[] = [];
    for (const c of "abc") segs = stream(segs, { kind: "text", content: c });
    expect(segs).toHaveLength(1);
    expect(segs[0]).toEqual({ t: "text", text: "abc" });
  });

  it("seals a delegate's interim text too", () => {
    let segs = stream([], { kind: "tool", name: "Agent", agent: "Explore", id: "a1", input: "look" });
    segs = stream(segs, { kind: "text", content: "A", parent: "a1" });
    segs = sealTrailingText(segs);
    segs = stream(segs, { kind: "text", content: "B", parent: "a1" });
    const steps = (segs[0] as Extract<ChatSegment, { t: "agent" }>).steps;
    expect(steps.map((s) => (s.t === "text" ? s.text : "?"))).toEqual(["A", "B"]);
  });

  it("is idempotent, so a replayed event cannot cut twice", () => {
    const once = sealTrailingText([{ t: "text", text: "A" }]);
    expect(sealTrailingText(once)).toBe(once);
  });

  it("does nothing to a reply that has produced no text yet", () => {
    const segs: ChatSegment[] = [];
    expect(sealTrailingText(segs)).toBe(segs);
  });

  it("does not disturb a trailing tool call", () => {
    const segs: ChatSegment[] = [{ t: "tool", name: "Read", input: "a.py" }];
    expect(sealTrailingText(segs)).toBe(segs);
  });
});

describe("tool outcomes close their own step", () => {
  // The backend records the same four fields (build_streaming._close_tool), so a
  // turn rendered from this reducer and the same turn rendered from the
  // server's persisted segments after a reload have to agree. If they do not,
  // a step changes state the moment you refresh.
  const open = (id: string) =>
    applyStreamEvent([], { kind: "tool", name: "Bash", input: "make", id });

  it("marks a step running while it is open", () => {
    const segs = open("t1");
    expect(segs[0]).toMatchObject({ t: "tool", id: "t1" });
    expect(typeof (segs[0] as { at?: number }).at).toBe("number");
    expect((segs[0] as { ok?: boolean }).ok).toBeUndefined();
  });

  it("closes the matching step on success and stops it running", () => {
    const segs = applyStreamEvent(open("t1"), { kind: "tool_result", tool_use_id: "t1", text: "done" });
    expect(segs[0]).toMatchObject({ t: "tool", ok: true });
    expect((segs[0] as { at?: number }).at).toBeUndefined();
    expect(typeof (segs[0] as { ms?: number }).ms).toBe("number");
  });

  it("keeps the failure text, and only on failure", () => {
    const bad = applyStreamEvent(open("t1"),
      { kind: "tool_result", tool_use_id: "t1", text: "boom", is_error: true });
    expect(bad[0]).toMatchObject({ ok: false, error: "boom" });
    const good = applyStreamEvent(open("t2"),
      { kind: "tool_result", tool_use_id: "t2", text: "lots of output" });
    expect((good[0] as { error?: string }).error).toBeUndefined();
  });

  it("ignores a result for a step it never saw", () => {
    const segs = open("t1");
    expect(applyStreamEvent(segs, { kind: "tool_result", tool_use_id: "nope" })).toEqual(segs);
  });

  it("a subagent result still closes the subagent, not a tool", () => {
    let segs = applyStreamEvent([], { kind: "tool", name: "Agent", input: "go", id: "a1", agent: "Explore" });
    segs = applyStreamEvent(segs, { kind: "tool_result", tool_use_id: "a1", text: "report" });
    expect(segs[0]).toMatchObject({ t: "agent", done: true, result: "report" });
  });

  it("a nested step gets no id, so a subagent's plumbing cannot close a parent row", () => {
    let segs = applyStreamEvent([], { kind: "tool", name: "Agent", input: "go", id: "a1", agent: "Explore" });
    segs = applyStreamEvent(segs, { kind: "tool", name: "Read", input: "x.ts", id: "n1", parent: "a1" });
    const step = (segs[0] as { steps: { id?: string }[] }).steps[0];
    expect(step.id).toBeUndefined();
  });
});
