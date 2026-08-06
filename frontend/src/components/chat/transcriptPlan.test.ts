import { describe, expect, it } from "vitest";
import type { ChatMsg, ChatSegment } from "../../lib/chat";
import { cutPoints, latestAssistantRow, planTranscript, type TranscriptRow } from "./transcriptPlan";

// Where a message sent DURING a reply is shown, once that reply is finished.
//
// This is the third regression in this area and the first with a test, because
// the frontend had no runner. Every one of them was invisible to the backend
// suite: the store was right and the renderer moved the words afterwards.

const seg = (...t: string[]): ChatSegment[] => t.map((text) => ({ t: "text", text }));
const A = (turn: string, segments: ChatSegment[]): ChatMsg =>
  ({ role: "assistant", content: "", ts: "t", meta: { turn_id: turn, segments } });
const U = (content: string, meta?: ChatMsg["meta"]): ChatMsg =>
  ({ role: "user", content, ts: "t", ...(meta ? { meta } : {}) });

/** Compact readable shape: `A1[a+b]^$` is assistant msg 1, that slice, head/tail. */
const shape = (rows: TranscriptRow[]) => rows.map((r) =>
  r.kind === "slice"
    ? `A${r.index}[${r.segments.map((s) => (s.t === "text" ? s.text : s.name)).join("+")}]`
      + `${r.head ? "^" : ""}${r.tail ? "$" : ""}`
    : `${r.msg.role[0].toUpperCase()}${r.index}`);

describe("a message that arrived mid-reply stays where it landed", () => {
  // The actual 2026-08-06 transcript: the operator's message was filed after the
  // reply with at_segment 4, the list rendered flat, and it appeared BELOW the
  // whole reply — including below the paragraph answering it. They re-sent it
  // and asked why it had been ignored.
  const real = [
    U("the chat mode selector dropdown is bad"),
    A("9e169b9d", seg("a", "b", "c", "d", "e", "f", "g", "h")),
    U("and remove that description text too", { during_turn: "9e169b9d", at_segment: 4, injected: true }),
  ];

  it("splits the reply at the point the message arrived", () => {
    expect(shape(planTranscript(real))).toEqual(["U0", "A1[a+b+c+d]^", "U2", "A1[e+f+g+h]$"]);
  });

  it("loses no output when it splits", () => {
    const text = planTranscript(real)
      .filter((r): r is Extract<TranscriptRow, { kind: "slice" }> => r.kind === "slice")
      .flatMap((r) => r.segments.map((s) => (s.t === "text" ? s.text : "")))
      .join("");
    expect(text).toBe("abcdefgh");
  });

  it("shows every message exactly once", () => {
    const seen = planTranscript(real)
      .filter((r) => r.kind === "message")
      .map((r) => r.index);
    expect(seen).toEqual([0, 2]);
  });

  it("cuts twice for two arrivals", () => {
    expect(shape(planTranscript([
      A("t1", seg("a", "b", "c", "d")),
      U("one", { during_turn: "t1", at_segment: 1 }),
      U("two", { during_turn: "t1", at_segment: 3 }),
    ]))).toEqual(["A0[a]^", "U1", "A0[b+c]", "U2", "A0[d]$"]);
  });
});

describe("an ordinary transcript is untouched", () => {
  it("emits one row per message when nothing arrived mid-turn", () => {
    expect(shape(planTranscript([U("hi"), A("t1", seg("x", "y"))]))).toEqual(["U0", "A1"]);
  });

  it("does not split a reply with no segments", () => {
    expect(shape(planTranscript([
      { role: "assistant", content: "plain", ts: "t", meta: { turn_id: "t1" } },
      U("during", { during_turn: "t1", at_segment: 0 }),
    ]))).toEqual(["A0", "U1"]);
  });

  it("does not pull a plain follow-up into the reply", () => {
    expect(shape(planTranscript([A("t1", seg("a", "b")), U("next question")])))
      .toEqual(["A0", "U1"]);
  });

  it("leaves a different turn's arrival alone", () => {
    expect(shape(planTranscript([A("t1", seg("a", "b")), U("other", { during_turn: "t2", at_segment: 1 })])))
      .toEqual(["A0", "U1"]);
  });

  it("consumes only the contiguous run", () => {
    expect(shape(planTranscript([
      A("t1", seg("a", "b", "c")),
      U("during", { during_turn: "t1", at_segment: 1 }),
      U("plain after"),
      U("stray", { during_turn: "t1", at_segment: 2 }),
    ]))).toEqual(["A0[a]^", "U1", "A0[b+c]$", "U2", "U3"]);
  });
});

describe("edges", () => {
  it("puts an arrival at position 0 above the reply, and keeps the byline on a slice", () => {
    expect(shape(planTranscript([A("t1", seg("a", "b")), U("first", { during_turn: "t1", at_segment: 0 })])))
      .toEqual(["U1", "A0[a+b]^$"]);
  });

  it("clamps a position past the end", () => {
    expect(shape(planTranscript([A("t1", seg("a", "b")), U("late", { during_turn: "t1", at_segment: 99 })])))
      .toEqual(["A0[a+b]^$", "U1"]);
  });

  it("treats a missing position as the end, not the start", () => {
    expect(shape(planTranscript([A("t1", seg("a", "b")), U("x", { during_turn: "t1" })])))
      .toEqual(["A0[a+b]^$", "U1"]);
  });

  it("never produces a negative slice from a backwards position", () => {
    expect(shape(planTranscript([
      A("t1", seg("a", "b", "c")),
      U("one", { during_turn: "t1", at_segment: 2 }),
      U("two", { during_turn: "t1", at_segment: 1 }),
    ]))).toEqual(["A0[a+b]^", "U1", "U2", "A0[c]$"]);
  });

  it("handles an empty transcript", () => {
    expect(planTranscript([])).toEqual([]);
  });
});

describe("the cut rule is shared with the live overlay", () => {
  // LiveTurn uses this too. If the two ever disagree, a message jumps position
  // the instant its turn finishes — which is the whole bug.
  it("clamps into the reply", () => expect(cutPoints([-3, 99], 4)).toEqual([0, 4]));
  it("never goes backwards", () => expect(cutPoints([3, 1, 2], 5)).toEqual([3, 3, 3]));
  it("treats a missing position as the end", () => expect(cutPoints([undefined], 6)).toEqual([6]));
  it("passes ordinary cuts through", () => expect(cutPoints([1, 3], 5)).toEqual([1, 3]));
});

describe("recency", () => {
  it("expands the final slice of a split reply", () => {
    const rows = planTranscript([
      U("q"),
      A("t1", seg("a", "b")),
      U("during", { during_turn: "t1", at_segment: 1 }),
    ]);
    expect(latestAssistantRow(rows)).toBe(rows.length - 1);
    expect(shape(rows)[rows.length - 1]).toBe("A1[b]$");
  });

  it("returns -1 when there is no assistant content", () => {
    expect(latestAssistantRow(planTranscript([U("hi")]))).toBe(-1);
  });
});
