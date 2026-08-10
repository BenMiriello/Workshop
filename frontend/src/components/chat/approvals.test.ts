import { describe, it, expect } from "vitest";
import { applyStreamEvent } from "./segments";
import type { ChatSegment } from "../../lib/chat";

// The reducer half of the approval path. The backend builds the same shape
// (build_streaming._segment_ingest), so a live turn and a reloaded one have to
// render identically — if they disagree, an approval answered in one tab shows
// as pending in the other.

const ask = (id: string, tool = "Write") => ({
  kind: "permission" as const,
  id, tool, label: tool, detail: "hello.txt",
  input: { file_path: "/tmp/hello.txt" },
  suggestions: [{ type: "setMode", mode: "acceptEdits" }],
});

const approvals = (segs: ChatSegment[]) => segs.filter((s) => s.t === "approval");

describe("approval segments", () => {
  it("a request becomes a pending approval", () => {
    const segs = applyStreamEvent([], ask("r1"));
    expect(approvals(segs)).toHaveLength(1);
    const a = segs[0] as Extract<ChatSegment, { t: "approval" }>;
    expect(a.state).toBe("pending");
    expect(a.tool).toBe("Write");
    expect(a.suggestions[0].mode).toBe("acceptEdits");
  });

  it("does not ask twice for one decision", () => {
    // A reconnect replays the tail of the stream.
    let segs = applyStreamEvent([], ask("r1"));
    segs = applyStreamEvent(segs, ask("r1"));
    expect(approvals(segs)).toHaveLength(1);
  });

  it("resolves the request that was answered, by id", () => {
    let segs = applyStreamEvent([], ask("r1"));
    segs = applyStreamEvent(segs, ask("r2", "Bash"));
    segs = applyStreamEvent(segs, { kind: "permission_resolved", id: "r1", state: "allowed" });
    const [a, b] = approvals(segs) as Extract<ChatSegment, { t: "approval" }>[];
    expect(a.state).toBe("allowed");
    expect(b.state).toBe("pending");
  });

  it("a denial is recorded as a denial, not as an allow", () => {
    let segs = applyStreamEvent([], ask("r1"));
    segs = applyStreamEvent(segs, { kind: "permission_resolved", id: "r1", state: "denied" });
    expect((segs[0] as Extract<ChatSegment, { t: "approval" }>).state).toBe("denied");
  });

  it("the turn ending stales every request still open", () => {
    // The process they blocked is gone; live buttons would offer a decision that
    // silently does nothing.
    let segs = applyStreamEvent([], ask("r1"));
    segs = applyStreamEvent(segs, ask("r2", "Bash"));
    segs = applyStreamEvent(segs, { kind: "permission_resolved", stale: true });
    expect(approvals(segs).every((s) => (s as { state: string }).state === "stale")).toBe(true);
  });

  it("the turn ending does not rewrite a decision already made", () => {
    let segs = applyStreamEvent([], ask("r1"));
    segs = applyStreamEvent(segs, { kind: "permission_resolved", id: "r1", state: "allowed" });
    segs = applyStreamEvent(segs, { kind: "permission_resolved", stale: true });
    expect((segs[0] as Extract<ChatSegment, { t: "approval" }>).state).toBe("allowed");
  });

  it("an approval does not swallow the text around it", () => {
    // The approval interrupts the turn; the answer written after it must still
    // read as the answer rather than merging into the step before.
    let segs = applyStreamEvent([], { kind: "text", content: "Writing the file." });
    segs = applyStreamEvent(segs, ask("r1"));
    segs = applyStreamEvent(segs, { kind: "text", content: "Done." });
    expect(segs.map((s) => s.t)).toEqual(["text", "approval", "text"]);
  });
});
