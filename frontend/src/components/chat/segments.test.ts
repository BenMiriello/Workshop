import { describe, it, expect } from "vitest";
import { applyStreamEvent, agentSummary, type StreamEvent } from "./segments";
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
