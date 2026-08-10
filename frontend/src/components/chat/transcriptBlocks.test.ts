import { describe, expect, it } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import { initialThreadState, threadReducer, type LogEvent, type ThreadState } from "./threadStore";
import { selectTranscript, type TranscriptBlock } from "./transcriptBlocks";
import { splitAtUnits } from "./transcriptUnits";

const text = (s: string): ChatSegment => ({ t: "text", text: s });
const tool = (n: string): ChatSegment => ({ t: "tool", name: n, input: "" });

// Module-global so every event in the file gets a distinct id.
let seq = 0;
const user = (content: string, meta?: Record<string, unknown>): LogEvent =>
  ({ seq: ++seq, id: `e${seq}`, ts: "2026-08-09T12:00:00Z", kind: "message", role: "user", content, ...(meta ? { meta } : {}) });
const reply = (content: string, meta: Record<string, unknown>): LogEvent =>
  ({ seq: ++seq, id: `e${seq}`, ts: "2026-08-09T12:00:00Z", kind: "message", role: "assistant", content, meta });
const boundary = (mode: "compact" | "clear"): LogEvent =>
  ({ seq: ++seq, id: `e${seq}`, ts: "2026-08-09T12:00:00Z", kind: "boundary", mode });

function stateOf(events: LogEvent[]): ThreadState {
  return threadReducer(initialThreadState("t"),
    { type: "eventsReceived", events, lastSeq: events.length, activeRun: null });
}

/** A readable shape for an expected transcript. */
const shape = (blocks: TranscriptBlock[]) => blocks.map((b) => {
  if (b.kind === "message") return `${b.msg.role}:${(b.msg.meta?.segments || []).map((s) => (s.t === "text" ? s.text : `[${s.t}]`)).join("") || b.msg.content}`;
  if (b.kind === "live") return `live:${b.segments.map((s) => (s.t === "text" ? s.text : `[${s.t}]`)).join("")}${b.working ? "*" : ""}`;
  if (b.kind === "pending") return `pending:${b.text}`;
  return `cut:${b.count}${b.cleared ? ":cleared" : ""}`;
});

describe("splitAtUnits", () => {
  // With no subagents a unit IS a segment, so these read exactly as they did
  // when the splitter counted segments — which is the point: the ordinary reply
  // must not move.
  it("cuts where it is told", () => {
    expect(splitAtUnits([text("a"), text("b"), text("c")], [1]))
      .toEqual([[text("a")], [text("b"), text("c")]]);
  });

  it("clamps a cut past the end rather than inventing segments", () => {
    expect(splitAtUnits([text("a")], [99])).toEqual([[text("a")], []]);
  });

  it("forces cuts non-decreasing, so a stale index can only empty a block", () => {
    expect(splitAtUnits([text("a"), text("b")], [2, 1]))
      .toEqual([[text("a"), text("b")], [], []]);
  });
});

describe("a reply nobody interrupted", () => {
  it("renders as one block, exactly as before this rule existed", () => {
    const s = stateOf([user("hi"), reply("hello", { turn_id: "r1", segments: [text("hello")] })]);
    const blocks = selectTranscript(s);
    expect(blocks).toHaveLength(2);
    expect(blocks[1].kind).toBe("message");
    // Untouched: same object identity of meta.segments, same content.
    expect((blocks[1] as { msg: { content: string } }).msg.content).toBe("hello");
  });
});

describe("a message sent mid-turn closes the assistant's block", () => {

  it("puts the message after work already emitted and before work not yet emitted", () => {
    const segments = [text("one"), tool("Read"), text("two"), tool("Edit"), text("three")];
    const s = stateOf([
      user("go"),
      user("wait", { during_run: "r1", at_segment: 2 }),
      reply("", { turn_id: "r1", segments }),
    ]);
    expect(shape(selectTranscript(s))).toEqual([
      "user:go",
      "assistant:one[tool]",
      "user:wait",
      "assistant:two[tool]three",
    ]);
  });

  it("cuts once per message, so five interjections make six blocks", () => {
    const segments = [text("a"), text("b"), text("c"), text("d"), text("e")];
    const s = stateOf([
      user("go"),
      user("1", { during_run: "r1", at_segment: 1 }),
      user("2", { during_run: "r1", at_segment: 2 }),
      user("3", { during_run: "r1", at_segment: 3 }),
      user("4", { during_run: "r1", at_segment: 4 }),
      reply("", { turn_id: "r1", segments }),
    ]);
    expect(shape(selectTranscript(s))).toEqual([
      "user:go",
      "assistant:a", "user:1",
      "assistant:b", "user:2",
      "assistant:c", "user:3",
      "assistant:d", "user:4",
      "assistant:e",
    ]);
  });

  it("puts the whole reply BELOW a message that arrived before anything streamed", () => {
    // at_segment 0: the turn had produced nothing, so none of the reply can
    // precede the message. This is the case the old splicing scheme got
    // backwards, rendering the operator's words above the working indicator.
    const s = stateOf([
      user("go"),
      user("wait", { during_run: "r1", at_segment: 0 }),
      reply("ok", { turn_id: "r1", segments: [text("ok")] }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["user:go", "user:wait", "assistant:ok"]);
  });

  it("keeps a reply with no segments at all below the messages it answered", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_run: "r1", at_segment: 0 }),
      reply("plain text reply", { turn_id: "r1" }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["user:go", "user:wait", "assistant:plain text reply"]);
  });

  it("puts a message with no at_segment below the whole reply", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_run: "r1" }),
      reply("", { turn_id: "r1", segments: [text("a"), text("b")] }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["user:go", "assistant:ab", "user:wait"]);
  });

  it("carries how the turn ENDED only on the final block", () => {
    const s = stateOf([
      user("wait", { during_run: "r1", at_segment: 1 }),
      reply("", { turn_id: "r1", segments: [text("a"), text("b")], timeout: true, notes: ["did a thing"] }),
    ]);
    const blocks = selectTranscript(s).filter((b) => b.kind === "message" && b.msg.role === "assistant");
    expect((blocks[0] as { msg: { meta?: { timeout?: boolean } } }).msg.meta?.timeout).toBeUndefined();
    expect((blocks[1] as { msg: { meta?: { timeout?: boolean } } }).msg.meta?.timeout).toBe(true);
    expect((blocks[1] as { msg: { meta?: { notes?: string[] } } }).msg.meta?.notes).toEqual(["did a thing"]);
  });

  it("leaves a message filed against an ALREADY finished run where the log put it", () => {
    // The take-over case: the run refused delivery, so the message is below the
    // reply in the log and that is where it belongs.
    const s = stateOf([
      reply("done", { turn_id: "r1", segments: [text("done")] }),
      user("late", { during_run: "r1", at_segment: 1 }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["assistant:done", "user:late"]);
  });
});

describe("the live turn", () => {

  it("splits the same way while it is still running", () => {
    let s = stateOf([user("go"), user("wait", { during_run: "r1", at_segment: 1 })]);
    s = threadReducer(s, { type: "runStarted", runId: "r1" });
    s = threadReducer(s, { type: "runDelta", runId: "r1", event: { kind: "text", content: "first" } });
    s = threadReducer(s, { type: "runDelta", runId: "r1", event: { kind: "tool", name: "Read" } });
    // The store folded "first" and a tool call into two segments; the message
    // landed after the first, so it closes that block.
    expect(shape(selectTranscript(s))).toEqual([
      "user:go", "live:first", "user:wait", "live:[tool]*",
    ]);
  });

  it("shows a running turn that has produced nothing, so the log is never silent", () => {
    let s = stateOf([user("go")]);
    s = threadReducer(s, { type: "runStarted", runId: "r1" });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:*"]);
  });

  it("marks only the last block as the one still working", () => {
    let s = stateOf([user("wait", { during_run: "r1", at_segment: 1 })]);
    s = threadReducer(s, { type: "runStarted", runId: "r1" });
    s = threadReducer(s, { type: "runDelta", runId: "r1", event: { kind: "text", content: "a" } });
    const live = selectTranscript(s).filter((b) => b.kind === "live");
    expect(live.map((b) => (b as { working: boolean }).working)).toEqual([false, true]);
  });
});

describe("pending sends and the context cut", () => {

  it("renders pending sends last, below even the live turn", () => {
    let s = stateOf([user("go")]);
    s = threadReducer(s, { type: "runStarted", runId: "r1" });
    s = threadReducer(s, { type: "sendStarted", clientId: "c1", text: "typed ahead", ts: "t" });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:*", "pending:typed ahead"]);
  });

  it("draws the divider where the boundary sits in the log", () => {
    const s = stateOf([user("a"), user("b"), boundary("compact"), user("c")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2", "user:c"]);
  });

  it("says a clear was a clear, so the wording never claims a summary", () => {
    const s = stateOf([user("a"), boundary("clear")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "cut:1:cleared"]);
  });

  it("shows only the latest boundary, as one divider", () => {
    const s = stateOf([user("a"), boundary("compact"), user("b"), boundary("compact")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2"]);
  });
});

describe("nothing is lost or duplicated by a cut", () => {
  // THE SAFETY PROPERTY. Splitting a reply is only acceptable if the pieces
  // still add up to the reply: concatenating every assistant block's segments,
  // in render order, must reproduce the original list exactly. Verified against
  // a real 157-segment turn from the operator's own log before being pinned
  // here as a shape (text/tool/agent interleaved, cuts at the ends, in the
  // middle, and past the end).
  const many: ChatSegment[] = Array.from({ length: 157 }, (_, i) =>
    i % 7 === 3
      ? { t: "agent", name: "Explore", input: `job ${i}`, id: `a${i}`, steps: [], done: true, result: "ok" }
      : i % 3 === 0
        ? text(`t${i}`)
        : tool(`Tool${i}`));

  it("holds for cuts at the start, the middle, the end and past the end", () => {
    const cuts = [0, 1, 7, 40, 100, 156, 157, 900];
    const s = stateOf([
      user("go"),
      ...cuts.map((at, i) => user(`interject ${i}`, { during_run: "r1", at_segment: at })),
      reply("", { turn_id: "r1", segments: many }),
    ]);
    const blocks = selectTranscript(s);
    const rebuilt: ChatSegment[] = [];
    const said: string[] = [];
    for (const b of blocks) {
      if (b.kind !== "message") continue;
      if (b.msg.role === "assistant") rebuilt.push(...(b.msg.meta?.segments || []));
      else said.push(b.msg.content);
    }
    expect(rebuilt).toEqual(many);
    // And every message the operator sent is still there, once, in order.
    expect(said).toEqual(["go", ...cuts.map((_, i) => `interject ${i}`)]);
  });

  it("holds for the live turn too", () => {
    let s = stateOf([
      user("1", { during_run: "r1", at_segment: 2 }),
      user("2", { during_run: "r1", at_segment: 9 }),
    ]);
    s = threadReducer(s, { type: "runStarted", runId: "r1" });
    s = threadReducer(s, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeRun: { run_id: "r1", live: { meta: { segments: many } } },
    });
    const rebuilt: ChatSegment[] = [];
    for (const b of selectTranscript(s)) if (b.kind === "live") rebuilt.push(...b.segments);
    expect(rebuilt).toEqual(many);
  });
});
