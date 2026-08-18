import { describe, expect, it } from "vitest";
import type { ChatSegment } from "../../lib/chat";
import { initialThreadState, threadReducer, type LogEvent, type ThreadState } from "./threadStore";
import {
  selectCommitted, composeTranscript, committedClientIds, withoutCommitted,
  type TranscriptBlock,
} from "./transcriptBlocks";

import { splitAtUnits } from "./transcriptUnits";

/** The whole transcript, composed the way the app composes it.
 *
 *  A TEST HELPER, and it lives here because that is what it is. It used to be
 *  exported from the module as `selectTranscript`, where nothing in the app
 *  called it: production splits the two halves apart for the memo boundaries in
 *  useThreadLog and composes them there. An export only the tests use is dead
 *  code with a reason attached, which is still dead code. Operator, 2026-08-12. */
const selectTranscript = (state: ThreadState): TranscriptBlock[] =>
  composeTranscript(selectCommitted(state), state);

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

/** `cut` is what the SERVER says it is applying. Without it there is no
 *  divider, whatever boundary events the log carries: the transcript describes
 *  the prompt, and only the server knows what the prompt contains. */
function stateOf(
  events: LogEvent[],
  cut?: { clearedThrough: number; cutMode?: "clear" | "compact" },
): ThreadState {
  return threadReducer(initialThreadState("t"), {
    type: "eventsReceived", events, lastSeq: events.length, activeTurn: null,
    ...(cut ? { clearedThrough: cut.clearedThrough, cutMode: cut.cutMode ?? "clear" } : {}),
  });
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
      user("wait", { during_turn: "r1", at_segment: 2 }),
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
      user("1", { during_turn: "r1", at_segment: 1 }),
      user("2", { during_turn: "r1", at_segment: 2 }),
      user("3", { during_turn: "r1", at_segment: 3 }),
      user("4", { during_turn: "r1", at_segment: 4 }),
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
      user("wait", { during_turn: "r1", at_segment: 0 }),
      reply("ok", { turn_id: "r1", segments: [text("ok")] }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["user:go", "user:wait", "assistant:ok"]);
  });

  it("keeps a reply with no segments at all below the messages it answered", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_turn: "r1", at_segment: 0 }),
      reply("plain text reply", { turn_id: "r1" }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["user:go", "user:wait", "assistant:plain text reply"]);
  });

  it("puts a message with no at_segment below the whole reply", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_turn: "r1" }),
      reply("", { turn_id: "r1", segments: [text("a"), text("b")] }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["user:go", "assistant:ab", "user:wait"]);
  });

  it("carries how the turn ENDED only on the final block", () => {
    const s = stateOf([
      user("wait", { during_turn: "r1", at_segment: 1 }),
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
      user("wait", { during_turn: "r1", at_unit: 1 }),
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
      user("stop", { during_turn: "r1", at_unit: 2 }),
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
      user("wait", { during_turn: "r1", at_unit: 1 }),
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
      user("late", { during_turn: "r1", at_segment: 1 }),
    ]);
    expect(shape(selectTranscript(s))).toEqual(["assistant:done", "user:late"]);
  });

  it("ignores an active_turn whose reply the log already carries", () => {
    // The server does not write these two facts atomically: commit_run appends
    // the reply and clears active_turn_id afterwards, while read_thread reads the
    // meta BEFORE the events. A page landing in that gap carries the committed
    // reply AND active_turn still naming the run that produced it, with its stale
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
      user("next", { during_turn: "r1", at_unit: 0 }),
    ];
    const s = threadReducer(initialThreadState("t"), {
      type: "eventsReceived",
      events,
      lastSeq: events.length,
      activeTurn: { turn_id: "r1", live: { meta: { segments: [text("done")] } } },
    });
    expect(s.activeTurn?.turnId).toBe("r1");   // the stale snapshot really is adopted
    expect(shape(selectTranscript(s))).toEqual(["user:first", "assistant:done", "user:next"]);
  });

  it("still renders the live block for a run that has NOT committed a reply", () => {
    // The guard must not cost the ordinary case: a genuinely running turn has no
    // committed reply yet, so its draft is the only copy there is.
    let s = stateOf([user("go"), user("wait", { during_turn: "r2", at_unit: 1 })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r2" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r2", event: { kind: "text", content: "a" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r2", event: { kind: "tool", name: "Read" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:a", "user:wait", "live:[tool]*"]);
  });
});

describe("the live turn", () => {

  // A LIVE SPLIT COMES FROM THE `cut` FRAME, exactly as the committed one comes
  // from the recorded token. This test used to drive `at_segment` because the
  // live path had no other source: the server pushed a `cut` frame and no
  // listener existed, so the streaming tab could only ever resolve an index.
  // See streamWire.ts and useTurnStream's handler table.
  it("splits the same way while it is still running", () => {
    let s = stateOf([user("go"), user("wait", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "first" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "tool", name: "Read" } });
    // The message landed after "first" and before the tool call, so it closes
    // that block and the work that followed starts a new one.
    expect(shape(selectTranscript(s))).toEqual([
      "user:go", "live:first", "user:wait", "live:[tool]*",
    ]);
  });

  // THE STREAMING TAB'S OWN FAILURE, pinned. Reported 2026-08-14: "I AGAIN see
  // user messages showing up ABOVE system messages that preceded my message."
  //
  // The marker could not arrive: nothing subscribed to the frame carrying it,
  // so a live reply had no recorded position and the index was resolved against
  // a segment list still growing under it. The message drifted upward through
  // work that had already happened when it was sent. The frame is subscribed
  // now, and this pins the placement it produces.
  it("puts the message where the marker says once the frame arrives", () => {
    let s = stateOf([user("go"), user("and also", { during_turn: "r1", cut_id: "c1", at_unit: 0 })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "first" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "tool", name: "Read" } });
    expect(shape(selectTranscript(s))).toEqual([
      "user:go", "live:first", "user:and also", "live:[tool]*",
    ]);
  });

  it("shows a running turn that has produced nothing, so the log is never silent", () => {
    let s = stateOf([user("go")]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:*"]);
  });

  it("marks only the last block as the one still working", () => {
    let s = stateOf([user("wait", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "a" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    const live = selectTranscript(s).filter((b) => b.kind === "live");
    expect(live.map((b) => (b as { working: boolean }).working)).toEqual([false, true]);
  });

  // THE REGRESSION THIS FILE EXISTS FOR, in its last remaining form. A turn that
  // has announced itself and produced nothing is still a turn that started
  // BEFORE the message, so the message can never be the first thing in the run.
  it("keeps the working indicator above a message sent before anything streamed", () => {
    let s = stateOf([user("go"), user("and also", { during_turn: "r1", at_unit: 0 })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:*", "user:and also"]);
  });

  it("keeps it above every message when several land before anything streams", () => {
    let s = stateOf([
      user("go"),
      user("one more", { during_turn: "r1", at_unit: 0 }),
      user("and another", { during_turn: "r1", at_unit: 0 }),
    ]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    expect(shape(selectTranscript(s)))
      .toEqual(["user:go", "live:*", "user:one more", "user:and another"]);
  });

  // ...and it hands the indicator back to the bottom as soon as there is work
  // to sit under, because that is where the turn is writing now.
  it("moves the indicator below the message once work arrives after it", () => {
    let s = stateOf([user("go"), user("and also", { during_turn: "r1", at_unit: 0 })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "ok" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "user:and also", "live:ok*"]);
  });

  // The common case must not move: work emitted before the message stays above
  // it, and the still-running edge stays below it.
  it("leaves work emitted before the message above it", () => {
    let s = stateOf([user("go"), user("and also", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "ok" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:ok", "user:and also", "live:*"]);
  });
});

// ---------------------------------------------------------------------------
// THE RECORDED POSITION. Everything above this point drives the LEGACY path
// (`at_unit`), and it still passes, which is the point: transcripts on disk must
// not shift. These drive what replaced it. See docs/decisions/0018.
// ---------------------------------------------------------------------------
describe("a cut the server recorded", () => {
  const agentSeg = (id: string, steps: number, cut?: string): ChatSegment => ({
    t: "agent", name: id, input: id, id, done: false, result: "",
    steps: Array.from({ length: steps }, (_, i) => text(`${id}${i}`)),
    ...(cut ? { cut_after: [cut] } : {}),
  });

  it("splits the reply where the token says, not where an index says", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_turn: "r1", cut_id: "c1", at_unit: 999 }),
      reply("done", {
        turn_id: "r1",
        segments: [{ ...text("before"), cut_after: ["c1"] }, text("after")],
      }),
    ]);
    // at_unit says 999 (below everything). The recorded token outranks it.
    expect(shape(selectTranscript(s)))
      .toEqual(["user:go", "assistant:before", "user:wait", "assistant:after"]);
  });

  it("puts a message that beat the first output above the whole reply", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_turn: "r1", cut_id: "c1" }),
      reply("done", { turn_id: "r1", cut_head: ["c1"], segments: [text("all of it")] }),
    ]);
    expect(shape(selectTranscript(s)))
      .toEqual(["user:go", "user:wait", "assistant:all of it"]);
  });

  // THE MEASURED REGRESSION, from the client side. Two delegations open at
  // once; the message was sent after both were launched, so both stay above it.
  // Under the unit index, A growing slid the cut into A and dropped B below the
  // message. A recorded position cannot move.
  it("holds its place while an earlier delegation keeps growing", () => {
    const withA = (steps: number) => stateOf([
      user("go"),
      user("wait", { during_turn: "r1", cut_id: "c1", at_unit: 5 }),
      reply("done", {
        turn_id: "r1",
        segments: [agentSeg("A", steps), agentSeg("B", 1, "c1"), text("after")],
      }),
    ]);
    const atSend = ["user:go", "assistant:[agent][agent]", "user:wait", "assistant:after"];
    expect(shape(selectTranscript(withA(1)))).toEqual(atSend);
    // A gains twenty steps. Nothing about B changed, so nothing may move.
    expect(shape(selectTranscript(withA(21)))).toEqual(atSend);
  });

  it("puts a message whose token is nowhere at the end, rather than guessing", () => {
    const s = stateOf([
      user("go"),
      user("wait", { during_turn: "r1", cut_id: "gone" }),
      reply("done", {
        turn_id: "r1",
        segments: [{ ...text("work"), cut_after: ["other"] }],
      }),
    ]);
    expect(shape(selectTranscript(s)))
      .toEqual(["user:go", "assistant:work", "user:wait"]);
  });

  it("keeps the working indicator above a message that beat the first output", () => {
    let s = stateOf([user("go"), user("wait", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:*", "user:wait"]);
  });

  it("hands the indicator back to the bottom once work arrives after it", () => {
    let s = stateOf([user("go"), user("wait", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "ok" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "user:wait", "live:ok*"]);
  });

  it("records a live cut against the segment it arrived after", () => {
    let s = stateOf([user("go"), user("wait", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "A" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "B" } });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:A", "user:wait", "live:B*"]);
  });

  // The stream is replayed from offset 0 on every re-attach, so every cut
  // arrives again. Recording it twice would put the message in two places.
  it("is idempotent across the replay a re-attach causes", () => {
    let s = stateOf([user("go"), user("wait", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "text", content: "A" } });
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    const once = shape(selectTranscript(s));
    s = threadReducer(s, { type: "turnDelta", turnId: "r1", event: { kind: "cut", ref: "c1" } });
    expect(shape(selectTranscript(s))).toEqual(once);
  });
});

// PHASE 3b: opening a thread now fetches only its newest slice
// (useThreadLog's `INITIAL_TAIL`), so a reply whose interrupting message
// landed further back than the window's floor can be in view while the
// message that interrupted it is not (yet) loaded -- the reply is always the
// more recent of the two, so a tail window either has both or the message
// alone, never the reply without it. This does not need a special case: a
// `cut_after` token with no matching event among the CURRENTLY HELD interrupts
// simply does not split there (see `cutPieces`'s `byToken`), which degrades to
// the reply rendering as one contiguous block -- the honest "we do not have
// that part" -- rather than a fragment that misrepresents a partial turn as a
// whole one.
describe("a tail window straddling a turn", () => {
  const segments = [{ ...text("before"), cut_after: ["c1"] }, text("after")];

  it("splits normally once the interrupting message is loaded", () => {
    const full = stateOf([
      user("go"),
      user("wait", { during_turn: "r1", cut_id: "c1" }),
      reply("done", { turn_id: "r1", segments }),
    ]);
    expect(shape(selectTranscript(full)))
      .toEqual(["user:go", "assistant:before", "user:wait", "assistant:after"]);
  });

  it("renders one contiguous block when the interrupting message fell below the window", () => {
    // Same reply, same recorded cut token -- but the tail window's floor sat
    // between the interrupting message and the reply, so this tab was never
    // sent the message at all.
    const windowed = stateOf([reply("done", { turn_id: "r1", segments })]);
    expect(shape(selectTranscript(windowed))).toEqual(["assistant:beforeafter"]);
  });
});

describe("pending sends and the context cut", () => {

  it("renders pending sends last, below even the live turn", () => {
    let s = stateOf([user("go")]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, { type: "sendStarted", clientId: "c1", text: "typed ahead", ts: "t" });
    expect(shape(selectTranscript(s))).toEqual(["user:go", "live:*", "pending:typed ahead"]);
  });

  it("draws the divider where the boundary sits in the log", () => {
    const s = stateOf([user("a"), user("b"), boundary("clear"), user("c")],
                      { clearedThrough: 2 });
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2", "user:c"]);
  });

  it("shows only the latest boundary, as one divider", () => {
    const s = stateOf([user("a"), boundary("clear"), user("b"), boundary("clear")],
                      { clearedThrough: 2 });
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2"]);
  });

  it("draws NOTHING for a boundary the server is not applying", () => {
    // Four of these exist in real threads: `compact` boundaries written during
    // the hours when folding was broken, with no summary behind them. The
    // server ignores them, so the divider must too — a transcript announcing a
    // fold while every message is still in the prompt is the app describing
    // something that is not happening.
    // A `compact` boundary hid messages behind a running summary that is no
    // longer assembled into any prompt. The server stopped honouring it, so the
    // divider must not claim a cut that is not happening.
    const s = stateOf([user("a"), user("b"), boundary("compact"), user("c")]);
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "user:c"]);
  });

  it("draws the LATEST boundary the server honours, not an older one", () => {
    const s = stateOf(
      [user("a"), boundary("compact"), user("b"), boundary("clear"), user("c")],
      { clearedThrough: 2 });
    expect(shape(selectTranscript(s))).toEqual(["user:a", "user:b", "cut:2", "user:c"]);
  });

  it("says which command made the cut", () => {
    // Two different things happened to those messages and the sentences differ:
    // /clear dropped them, /compact folded them into a summary the model still
    // reads. One wording for both would be wrong for one of them.
    const cleared = stateOf([user("a"), user("b"), boundary("clear"), user("c")],
                            { clearedThrough: 2, cutMode: "clear" });
    const folded = stateOf([user("a"), user("b"), boundary("compact"), user("c")],
                           { clearedThrough: 2, cutMode: "compact" });
    const modeOf = (st: ThreadState) =>
      selectTranscript(st).find((b) => b.kind === "cut") as { mode: string } | undefined;
    expect(modeOf(cleared)?.mode).toBe("clear");
    expect(modeOf(folded)?.mode).toBe("compact");
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
      ...cuts.map((at, i) => user(`interject ${i}`, { during_turn: "r1", at_segment: at })),
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
      user("1", { during_turn: "r1", at_segment: 2 }),
      user("2", { during_turn: "r1", at_segment: 9 }),
    ]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    s = threadReducer(s, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeTurn: { turn_id: "r1", live: { meta: { segments: many } } },
    });
    const rebuilt: ChatSegment[] = [];
    for (const b of selectTranscript(s)) if (b.kind === "live") rebuilt.push(...b.segments);
    expect(rebuilt).toEqual(many);
  });
});

describe("a message handed to a running turn", () => {
  const cut = (s: ChatSegment, ...refs: string[]): ChatSegment =>
    ({ ...s, cut_after: refs });

  /** A live run holding one mid-turn message, with `segments` emitted so far. */
  const midRun = (segments: ChatSegment[], live?: Record<string, unknown>): ThreadState => {
    let s = stateOf([user("are you reading this", { during_turn: "r1", cut_id: "c1" })]);
    s = threadReducer(s, { type: "turnStarted", turnId: "r1" });
    return threadReducer(s, {
      type: "eventsReceived", events: [], lastSeq: 0,
      activeTurn: { turn_id: "r1", live: { meta: { segments, ...(live || {}) } } },
    });
  };

  // The read state and the working indicator's position are the SAME fact: a
  // message dims only while it is BELOW the indicator (not reached yet) and
  // turns solid the moment work appears below it. Ordering still holds too: the
  // message renders after the work that preceded it and before the work that
  // follows, and the run's own start stays above a message typed while it was
  // already going. See liveBlocks.
  const dimOf = (state: ThreadState): (boolean | undefined)[] =>
    selectTranscript(state).map((b) => (b.kind === "message" ? !!b.inflight : undefined));

  it("reads as solid once the working indicator sits below it", () => {
    // The turn emitted "a", then the message landed, then the trailing working
    // stretch renders BELOW the message. Indicator below = reached = solid, the
    // operator's own equivalence ("whether it's read and whether the indicator
    // is below it are the same").
    const s = midRun([cut(text("a"), "c1")]);
    expect(shape(selectTranscript(s)))
      .toEqual(["live:a", "user:are you reading this", "live:*"]);
    expect(dimOf(s)).toEqual([undefined, false, undefined]);
  });

  it("stays solid when the turn's next work renders below it", () => {
    const s = midRun([cut(text("a"), "c1"), text("understood")]);
    expect(shape(selectTranscript(s)))
      .toEqual(["live:a", "user:are you reading this", "live:understood*"]);
    expect(dimOf(s)).toEqual([undefined, false, undefined]);
  });

  it("dims a message typed before the turn wrote a word, under the indicator", () => {
    // `cut_head`: it arrived before the run had written anything. The run was
    // already in flight, so its indicator stays ABOVE the message (the floor
    // rule in workingPieceIndex), which means the message is below it: dim.
    const s = midRun([], { cut_head: ["c1"] });
    expect(shape(selectTranscript(s))).toEqual(["live:*", "user:are you reading this"]);
    expect(dimOf(s)).toEqual([undefined, true]);
  });
});

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
      lastSeq: 99, activeTurn: null,
    });
    expect(shape(selectTranscript(later)))
      .toEqual(["user:a", "pending:lost", "user:b", "assistant:c"]);
  });

  it("sinks below nothing, including a run that started after it", () => {
    const s0 = stateOf([user("a")]);
    const withFailure = failed(s0, "c1", "lost");
    const later = threadReducer(withFailure, {
      type: "eventsReceived", events: [user("b")], lastSeq: 99, activeTurn: null,
    });
    const live = threadReducer(later, { type: "turnStarted", turnId: "r1" });
    expect(shape(selectTranscript(live)))
      .toEqual(["user:a", "pending:lost", "user:b", "live:*"]);
  });

  it("keeps two failures in the order they were made", () => {
    let s = failed(stateOf([user("a")]), "c1", "first");
    s = threadReducer(s, {
      type: "eventsReceived", events: [user("b")], lastSeq: 98, activeTurn: null,
    });
    s = failed(s, "c2", "second");
    s = threadReducer(s, {
      type: "eventsReceived", events: [user("c")], lastSeq: 99, activeTurn: null,
    });
    expect(shape(selectTranscript(s)))
      .toEqual(["user:a", "pending:first", "user:b", "pending:second", "user:c"]);
  });

  it("goes to the very top when it failed on an empty thread", () => {
    const empty = initialThreadState("t");
    const withFailure = failed(empty, "c1", "lost");
    const later = threadReducer(withFailure, {
      type: "eventsReceived", events: [user("b")], lastSeq: 99, activeTurn: null,
    });
    expect(shape(selectTranscript(later))).toEqual(["pending:lost", "user:b"]);
  });
});

// The split that keeps a streamed frame cheap. These are not style assertions:
// if the committed half is re-derived per frame, the chat re-renders every
// message in the thread dozens of times a second, which is what made typing on
// a phone unusable. See selectCommitted's comment.
describe("selectCommitted / composeTranscript", () => {
  const withTurn = (events: LogEvent[]): ThreadState =>
    threadReducer(initialThreadState("t"), {
      type: "eventsReceived",
      events,
      lastSeq: events.length,
      activeTurn: { turn_id: "r-live" },
    });

  it("composes to exactly what selectTranscript produces", () => {
    const s = withTurn([user("hello"), reply("hi", { turn_id: "r-old" })]);
    expect(shape(composeTranscript(selectCommitted(s), s))).toEqual(shape(selectTranscript(s)));
  });

  it("keeps the committed half IDENTICAL across a streamed frame", () => {
    const s0 = withTurn([user("hello"), reply("hi", { turn_id: "r-old" })]);
    const before = selectCommitted(s0);

    // One token arrives on the live run, the way turnDelta delivers it.
    const s1 = threadReducer(s0, {
      type: "turnDelta", turnId: "r-live", event: { kind: "text", content: "wor" },
    });
    const s2 = threadReducer(s1, {
      type: "turnDelta", turnId: "r-live", event: { kind: "text", content: "king" },
    });

    // The store must not have touched the committed log at all: these identity
    // checks are what the memo in useThreadLog keys on, so if they ever stop
    // holding, the memo silently stops working and nothing else fails.
    expect(s2.byId).toBe(s0.byId);
    expect(s2.order).toBe(s0.order);
    expect(s2.idBySeq).toBe(s0.idBySeq);
    expect(s2.activeTurn?.turnId).toBe(s0.activeTurn?.turnId);

    // And the derivation over them is stable, so re-running it is pure waste.
    expect(shape(selectCommitted(s2).blocks)).toEqual(shape(before.blocks));

    // Meanwhile the live half did move, which is the whole point.
    expect(shape(composeTranscript(before, s2)).join("|")).toContain("live:working");
  });

  it("still hands the live run the messages held back for it", () => {
    const s = withTurn([user("first"), user("mid-run", { during_turn: "r-live" })]);
    const committed = selectCommitted(s);
    // The mid-run message is NOT in the committed blocks; it belongs inside the
    // live block, which is where composing puts it.
    expect(shape(committed.blocks)).toEqual(["user:first"]);
    expect(committed.heldForActiveTurn).toHaveLength(1);
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

describe("everything in the transcript comes from the transcript", () => {
  // THE RULE, and the one test that states it directly.
  //
  // A transcript block may be derived from: a log event, the server's active
  // run, or this browser's own un-acknowledged sends. There is no fourth
  // source. Anything else — an annotation held in React state, a queue held in
  // localStorage — is a line the reader cannot verify and that a second device
  // does not show, which is indistinguishable from the app making things up.
  //
  // A scope change used to be exactly that: `withScopeNotes` spliced it in from
  // component state, so it appeared for the browser that flipped the picker,
  // vanished on reload, and never existed on any other device.
  it("renders a scope change from the log's own event", () => {
    let state = initialThreadState("t1");
    state = threadReducer(state, {
      type: "eventsReceived",
      events: [
        { seq: 1, id: "e1", ts: "", kind: "message", role: "user", content: "hello" },
        { seq: 2, id: "e2", ts: "", kind: "scope", from: "general", to: "development" },
      ] as LogEvent[],
      lastSeq: 2,
      activeTurn: null,
    });
    const blocks = selectTranscript(state);
    expect(blocks.map((b) => b.kind)).toEqual(["message", "scope"]);
    expect(blocks[1]).toMatchObject({ kind: "scope", to: "development" });
  });

  it("shows the queue the SERVER reports, not one this browser is keeping", () => {
    let state = initialThreadState("t1");
    state = threadReducer(state, {
      type: "eventsReceived",
      events: [],
      lastSeq: 0,
      activeTurn: null,
      queued: [{ client_id: "c1", text: "typed during the command", ts: "2026-08-12T17:44:00Z" }],
    });
    expect(state.queued.map((q) => q.text)).toEqual(["typed during the command"]);
  });

  it("drops a pending send the server's queue has taken over", () => {
    // The response was lost in flight: the request landed, the answer did not.
    // This tab still thinks the message is its problem while the server has
    // already accepted it, and both would draw the same text.
    let state = initialThreadState("t1");
    state = threadReducer(state, {
      type: "sendStarted", clientId: "c1", text: "hello", ts: "2026-08-12T17:44:00Z",
    });
    expect(Object.keys(state.pending)).toEqual(["c1"]);
    state = threadReducer(state, {
      type: "eventsReceived",
      events: [],
      lastSeq: 0,
      activeTurn: null,
      queued: [{ client_id: "c1", text: "hello", ts: "2026-08-12T17:44:00Z" }],
    });
    expect(state.pending).toEqual({});
    expect(selectTranscript(state).filter((b) => b.kind === "pending")).toEqual([]);
  });

  it("brings back an un-acknowledged send after a reload", () => {
    // The composer is cleared the instant the operator hits enter, so between
    // that moment and the server's answer this browser holds the only copy.
    let state = initialThreadState("t1");
    state = threadReducer(state, {
      type: "pendingRestored",
      sends: [{
        clientId: "c1", text: "the one that never landed", ts: "2026-08-12T17:44:00Z",
        status: "failed", error: "the backend was restarting", at: 0, afterSeq: 0,
      }],
    });
    const blocks = selectTranscript(state);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ kind: "pending", status: "failed", text: "the one that never landed" });
  });
});
