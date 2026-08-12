import { describe, expect, it } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import { initialThreadState, threadReducer, type LogEvent, type ThreadState } from "./threadStore";
import {
  selectTranscript, selectCommitted, composeTranscript, withStaleFailures,
  withScopeNotes, committedClientIds, withoutCommitted,
  type TranscriptBlock,
} from "./transcriptBlocks";
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
  if (b.kind === "scope") return `scope:${b.to}`;
  return `cut:${b.count}`;
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

  it("carries the turn's RECEIPTS only on the final block", () => {
    // A receipt is what the whole turn changed outside the chat. Repeated on
    // every stretch of work, "Wrote 1 note" reads as several notes.
    type R = { kind: string; label: string }[];
    const receipts: R = [{ kind: "note", label: "Wrote 1 note" }];
    const s = stateOf([
      user("wait", { during_run: "r1", at_unit: 1 }),
      reply("", { turn_id: "r1", segments: [text("a"), text("b")], receipts }),
    ]);
    const blocks = selectTranscript(s).filter((b) => b.kind === "message" && b.msg.role === "assistant");
    expect(blocks).toHaveLength(2);
    expect((blocks[0] as { msg: { meta?: { receipts?: R } } }).msg.meta?.receipts).toBeUndefined();
    expect((blocks[1] as { msg: { meta?: { receipts?: R } } }).msg.meta?.receipts).toEqual(receipts);
  });

  it("keeps how the turn ended when the LAST slice is empty", () => {
    // A message sent and then Stop pressed: the cut is at the very end, so the
    // final slice holds nothing and emits no block. Keying the ending to the
    // last SLICE dropped it, so "Stopped by operator" never rendered and an
    // errored reply rendered as an ordinary one.
    const s = stateOf([
      user("stop", { during_run: "r1", at_unit: 2 }),
      reply("", { turn_id: "r1", segments: [text("a"), text("b")], stopped: true, error: "boom" }),
    ]);
    const blocks = selectTranscript(s).filter((b) => b.kind === "message" && b.msg.role === "assistant");
    expect(blocks).toHaveLength(1);
    const meta = (blocks[0] as { msg: { meta?: { stopped?: boolean; error?: string } } }).msg.meta;
    expect(meta?.stopped).toBe(true);
    expect(meta?.error).toBe("boom");
  });

  it("shows no turn total on a split reply", () => {
    // Every block carries the same started_at while only the last carries
    // ended_at, so a total paired with a per-block step count read "2 steps ·
    // 3h 41m". Both ends are needed to compute one, so the start is dropped.
    const s = stateOf([
      user("wait", { during_run: "r1", at_unit: 1 }),
      reply("", { turn_id: "r1", segments: [text("a"), text("b")], started_at: 1000, ended_at: 2000 }),
    ]);
    const blocks = selectTranscript(s).filter((b) => b.kind === "message" && b.msg.role === "assistant");
    expect(blocks).toHaveLength(2);
    for (const b of blocks) {
      expect((b as { msg: { meta?: { started_at?: number } } }).msg.meta?.started_at).toBeUndefined();
    }
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

  it("ignores an active_run whose reply the log already carries", () => {
    // The server does not write these two facts atomically: commit_run appends
    // the reply and clears active_run_id afterwards, while read_thread reads the
    // meta BEFORE the events. A page landing in that gap carries the committed
    // reply AND active_run still naming the run that produced it, with its stale
    // draft attached.
    //
    // Believing it printed that reply twice — once committed, and again below
    // the operator's new message as a block marked `working`, which is why it
    // read as the answer being written and then swapped out. Reported
    // 2026-08-12: "it shows your previous response underneath my message, and
    // then it finally removes that and replaces it with your actual response".
    const events = [
      user("first"),
      reply("done", { turn_id: "r1", segments: [text("done")] }),
      user("next", { during_run: "r1", at_unit: 0 }),
    ];
    const s = threadReducer(initialThreadState("t"), {
      type: "eventsReceived",
      events,
      lastSeq: events.length,
      activeRun: { run_id: "r1", live: { meta: { segments: [text("done")] } } },
    });
    expect(s.activeRun?.runId).toBe("r1");   // the stale snapshot really is adopted
    expect(shape(selectTranscript(s))).toEqual(["user:first", "assistant:done", "user:next"]);
  });

  it("still renders the live block for a run that has NOT committed a reply", () => {
    // The guard must not cost the ordinary case: a genuinely running turn has no
    // committed reply yet, so its draft is the only copy there is.
    let s = stateOf([user("go"), user("wait", { during_run: "r2", at_unit: 1 })]);
    s = threadReducer(s, { type: "runStarted", runId: "r2" });
    s = threadReducer(s, { type: "runDelta", runId: "r2", event: { kind: "text", content: "a" } });
    s = threadReducer(s, { type: "runDelta", runId: "r2", event: { kind: "tool", name: "Read" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:a", "user:wait", "live:[tool]*"]);
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
    const s = stateOf([user("a"), user("b"), boundary("clear"), user("c")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2", "user:c"]);
  });

  it("shows only the latest boundary, as one divider", () => {
    const s = stateOf([user("a"), boundary("clear"), user("b"), boundary("clear")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2"]);
  });

  it("draws nothing for a boundary left by the removed folding path", () => {
    // A `compact` boundary hid messages behind a running summary that is no
    // longer assembled into any prompt. The server stopped honouring it, so the
    // divider must not claim a cut that is not happening.
    const s = stateOf([user("a"), user("b"), boundary("compact"), user("c")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "user:c"]);
  });

  it("ignores a stale fold boundary even after a real clear", () => {
    const s = stateOf([user("a"), boundary("compact"), user("b"), boundary("clear"), user("c")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2", "user:c"]);
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

describe("withStaleFailures", () => {
  // Reported 2026-08-10: a send refused earlier (durable in the queue, no
  // longer in `pending` after a reload) rendered below a LATER message and
  // below that later message's live run — reading as the chat ignoring
  // everything sent since. A stale failure must land before whatever is
  // live, not after it.
  it("inserts before the live run, not after it", () => {
    const s = stateOf([user("newer")]);
    const withLive = threadReducer(s, { type: "runStarted", runId: "r1" });
    const blocks = selectTranscript(withLive);
    const out = withStaleFailures(blocks, [{ clientId: "stale-1", text: "Hello" }]);
    expect(shape(out)).toEqual(["user:newer", "pending:Hello", "live:*"]);
  });

  it("appends at the end when nothing is live", () => {
    const s = stateOf([user("newer")]);
    const blocks = selectTranscript(s);
    const out = withStaleFailures(blocks, [{ clientId: "stale-1", text: "Hello" }]);
    expect(shape(out)).toEqual(["user:newer", "pending:Hello"]);
  });

  it("is a no-op with nothing stale to insert", () => {
    const s = stateOf([user("a")]);
    const blocks = selectTranscript(s);
    expect(withStaleFailures(blocks, [])).toBe(blocks);
  });

  it("keeps several stale failures together, in order", () => {
    const s = stateOf([user("newer")]);
    const withLive = threadReducer(s, { type: "runStarted", runId: "r1" });
    const blocks = selectTranscript(withLive);
    const out = withStaleFailures(blocks, [
      { clientId: "s1", text: "first" }, { clientId: "s2", text: "second" },
    ]);
    expect(shape(out)).toEqual(["user:newer", "pending:first", "pending:second", "live:*"]);
  });

  // The diagnosis is durable now, so the bubble that comes back after a reload
  // says the same thing it said before it. Dropping it here is what made every
  // reloaded failure read "this message never reached the server" (UnsentNote's
  // default) regardless of what the server had actually answered.
  it("carries the reason across the reload", () => {
    const blocks = selectTranscript(stateOf([user("newer")]));
    const out = withStaleFailures(blocks, [
      { clientId: "s1", text: "first", error: "Not sent: the server refused this message (403)." },
    ]);
    const p = out.find((b) => b.kind === "pending");
    expect(p && p.kind === "pending" && p.error)
      .toBe("Not sent: the server refused this message (403).");
  });
});

describe("withScopeNotes", () => {
  // `seq` is a module-global counter shared across every test in this file
  // (see `user`/`reply` above), so a note's `afterSeq` has to be read off the
  // blocks it is being spliced into rather than guessed as 1, 2, 3 — Chat.tsx
  // itself always reads it the same way, off `lastSeq(stateRef.current)`.
  const seqOf = (blocks: TranscriptBlock[], i: number): number => {
    const b = blocks[i];
    if (b.kind !== "message") throw new Error("expected a message block");
    return b.seq;
  };

  // Chat.tsx never appends more than one note per settle (a flip the operator
  // hasn't sent past yet just overwrites its own pending state, not this
  // list), but the merge itself just has to place whatever it is given at the
  // seq it happened.
  it("splices a note in at the seq it happened", () => {
    const blocks = selectTranscript(stateOf([user("a"), user("b")]));
    const out = withScopeNotes(blocks, [{ afterSeq: seqOf(blocks, 0), to: "development" }]);
    expect(shape(out)).toEqual(["user:a", "scope:development", "user:b"]);
  });

  it("keeps several notes in order", () => {
    const blocks = selectTranscript(stateOf([user("a"), user("b"), user("c")]));
    const out = withScopeNotes(blocks, [
      { afterSeq: seqOf(blocks, 0), to: "development" },
      { afterSeq: seqOf(blocks, 1), to: "general" },
    ]);
    expect(shape(out)).toEqual(["user:a", "scope:development", "user:b", "scope:general", "user:c"]);
  });

  it("is a no-op with nothing to splice in", () => {
    const blocks = selectTranscript(stateOf([user("a")]));
    expect(withScopeNotes(blocks, [])).toBe(blocks);
  });

  it("lands at the very top for a scope changed before the first message", () => {
    const blocks = selectTranscript(stateOf([user("a")]));
    const out = withScopeNotes(blocks, [{ afterSeq: 0, to: "development" }]);
    expect(shape(out)).toEqual(["scope:development", "user:a"]);
  });
});

// ---------------------------------------------------------------------------
// WHERE A FAILED SEND SITS.
// ---------------------------------------------------------------------------
// Reported 2026-08-11: "it just sort of sits there, and then I send new
// messages, and my new messages show above this old message, and then
// subsequent chat messages show above these messages."
//
// A failed send never leaves `pending` (nothing commits it, nothing retracts
// it), and every pending send used to be appended after the whole committed
// log, so it stayed nailed to the bottom while the conversation grew above it.
describe("a pending send's place in the log", () => {
  const failed = (s: ThreadState, clientId: string, text: string): ThreadState =>
    threadReducer(
      threadReducer(s, { type: "sendStarted", clientId, text, ts: "2026-08-11T00:00:00Z" }),
      { type: "sendFailed", clientId, error: "Not sent." },
    );

  it("renders last while it is still in flight, which is where it belongs", () => {
    const s = threadReducer(stateOf([user("a")]),
      { type: "sendStarted", clientId: "c1", text: "b", ts: "2026-08-11T00:00:00Z" });
    expect(shape(selectTranscript(s))).toEqual(["user:a", "pending:b"]);
  });

  it("stays where it was sent once the log has moved past it", () => {
    const s0 = stateOf([user("a")]);
    const withFailure = failed(s0, "c1", "lost");
    // Two more messages land, committed, after the failure.
    const later = threadReducer(withFailure, {
      type: "eventsReceived",
      events: [user("b"), reply("c", { turn_id: "r9", segments: [text("c")] })],
      lastSeq: 99, activeRun: null,
    });
    expect(shape(selectTranscript(later)))
      .toEqual(["user:a", "pending:lost", "user:b", "assistant:c"]);
  });

  it("sinks below nothing, including a run that started after it", () => {
    const s0 = stateOf([user("a")]);
    const withFailure = failed(s0, "c1", "lost");
    const later = threadReducer(withFailure, {
      type: "eventsReceived", events: [user("b")], lastSeq: 99, activeRun: null,
    });
    const live = threadReducer(later, { type: "runStarted", runId: "r1" });
    expect(shape(selectTranscript(live)))
      .toEqual(["user:a", "pending:lost", "user:b", "live:*"]);
  });

  it("keeps two failures in the order they were made", () => {
    let s = failed(stateOf([user("a")]), "c1", "first");
    s = threadReducer(s, {
      type: "eventsReceived", events: [user("b")], lastSeq: 98, activeRun: null,
    });
    s = failed(s, "c2", "second");
    s = threadReducer(s, {
      type: "eventsReceived", events: [user("c")], lastSeq: 99, activeRun: null,
    });
    expect(shape(selectTranscript(s)))
      .toEqual(["user:a", "pending:first", "user:b", "pending:second", "user:c"]);
  });

  it("goes to the very top when it failed on an empty thread", () => {
    const empty = initialThreadState("t");
    const withFailure = failed(empty, "c1", "lost");
    const later = threadReducer(withFailure, {
      type: "eventsReceived", events: [user("b")], lastSeq: 99, activeRun: null,
    });
    expect(shape(selectTranscript(later))).toEqual(["pending:lost", "user:b"]);
  });
});

// The split that keeps a streamed frame cheap. These are not style assertions:
// if the committed half is re-derived per frame, the chat re-renders every
// message in the thread dozens of times a second, which is what made typing on
// a phone unusable. See selectCommitted's comment.
describe("selectCommitted / composeTranscript", () => {
  const withRun = (events: LogEvent[]): ThreadState =>
    threadReducer(initialThreadState("t"), {
      type: "eventsReceived",
      events,
      lastSeq: events.length,
      activeRun: { run_id: "r-live" },
    });

  it("composes to exactly what selectTranscript produces", () => {
    const s = withRun([user("hello"), reply("hi", { turn_id: "r-old" })]);
    expect(shape(composeTranscript(selectCommitted(s), s))).toEqual(shape(selectTranscript(s)));
  });

  it("keeps the committed half IDENTICAL across a streamed frame", () => {
    const s0 = withRun([user("hello"), reply("hi", { turn_id: "r-old" })]);
    const before = selectCommitted(s0);

    // One token arrives on the live run, the way runDelta delivers it.
    const s1 = threadReducer(s0, {
      type: "runDelta", runId: "r-live", event: { kind: "text", content: "wor" },
    });
    const s2 = threadReducer(s1, {
      type: "runDelta", runId: "r-live", event: { kind: "text", content: "king" },
    });

    // The store must not have touched the committed log at all: these identity
    // checks are what the memo in useThreadLog keys on, so if they ever stop
    // holding, the memo silently stops working and nothing else fails.
    expect(s2.byId).toBe(s0.byId);
    expect(s2.order).toBe(s0.order);
    expect(s2.idBySeq).toBe(s0.idBySeq);
    expect(s2.activeRun?.runId).toBe(s0.activeRun?.runId);

    // And the derivation over them is stable, so re-running it is pure waste.
    expect(shape(selectCommitted(s2).blocks)).toEqual(shape(before.blocks));

    // Meanwhile the live half did move, which is the whole point.
    expect(shape(composeTranscript(before, s2)).join("|")).toContain("live:working");
  });

  it("still hands the live run the messages held back for it", () => {
    const s = withRun([user("first"), user("mid-run", { during_run: "r-live" })]);
    const committed = selectCommitted(s);
    // The mid-run message is NOT in the committed blocks; it belongs inside the
    // live block, which is where composing puts it.
    expect(shape(committed.blocks)).toEqual(["user:first"]);
    expect(committed.heldForActiveRun).toHaveLength(1);
    expect(shape(composeTranscript(committed, s))).toContain("user:mid-run");
  });
});

// THE GHOST BUBBLE. A failed send is kept in the durable queue so its text
// survives a reload. A retry reuses THE SAME client id on purpose, so when it
// lands the server dedupes and commits the message under the id the durable
// entry still holds. Nothing dropped that entry, and it was only ever filtered
// against the in-memory `pending` map — so the instant reconciliation removed
// the pending copy, the durable one surfaced and the message drew TWICE: once
// committed, once as a failed bubble with a Retry under it.
//
// Reported 2026-08-11: "it printed it once and then it printed it again with
// the error message on the second one." The second bubble appeared BECAUSE the
// send had succeeded, and no reload could clear it.
describe("a durable failure whose message actually landed", () => {
  const committedWith = (cid: string) => stateOf([
    { seq: ++seq, id: `e${seq}`, ts: "2026-08-09T12:00:00Z", kind: "message",
      role: "user", content: "do it", client_id: cid } as LogEvent,
  ]);

  it("collects the client ids the log carries", () => {
    expect(committedClientIds(committedWith("cid-1")).has("cid-1")).toBe(true);
  });

  it("ignores committed messages that carry no client id", () => {
    expect(committedClientIds(stateOf([user("typed elsewhere")])).size).toBe(0);
  });

  it("drops the queue entry the log already carries", () => {
    const items = [{ clientId: "cid-1", text: "do it", failed: true }];
    expect(withoutCommitted(items, committedClientIds(committedWith("cid-1")))).toEqual([]);
  });

  it("keeps a queued message the log does NOT carry", () => {
    const items = [{ clientId: "other", text: "still waiting" }];
    expect(withoutCommitted(items, committedClientIds(committedWith("cid-1")))).toEqual(items);
  });

  it("does not rebuild the array when nothing is committed", () => {
    const items = [{ clientId: "a", text: "x" }];
    // Identity matters: this feeds a memo whose churn turns off the transcript's
    // memoization, which is the thing that keeps typing from re-rendering it.
    expect(withoutCommitted(items, new Set())).toBe(items);
    expect(withoutCommitted(items, new Set(["nope"]))).toBe(items);
  });

  it("removes only the landed one from a mixed queue", () => {
    const items = [
      { clientId: "cid-1", text: "landed", failed: true },
      { clientId: "cid-2", text: "genuinely waiting" },
    ];
    const out = withoutCommitted(items, committedClientIds(committedWith("cid-1")));
    expect(out.map((i) => i.clientId)).toEqual(["cid-2"]);
  });
});
