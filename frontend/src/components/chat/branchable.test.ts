import { describe, it, expect } from "vitest";
import { branchTargets, type BranchBlock } from "./branchable";

const b = (key: string, id: string, role: string): BranchBlock => ({ key, id, role });

describe("branchTargets", () => {
  it("offers 'before' on your message and 'after' on a reply", () => {
    const t = branchTargets([b("u1", "u1", "user"), b("a1", "a1", "assistant")], "a1");
    expect(t.get("u1")).toBe("before");
  });

  it("does NOT offer anything on the trailing reply: it is the identity operation", () => {
    // The operator's question. Going back to the message the chat already ends
    // at produces the transcript already on screen, so there is nothing to show.
    const t = branchTargets([b("u1", "u1", "user"), b("a1", "a1", "assistant")], "a1");
    expect(t.has("a1")).toBe(false);
  });

  it("DOES offer on a trailing message of your own", () => {
    // Not "the last bubble never has one": take back what you just sent is the
    // most useful case in the feature, and it is always a real change.
    const t = branchTargets([b("u1", "u1", "user"), b("a1", "a1", "assistant"), b("u2", "u2", "user")], "u2");
    expect(t.get("u2")).toBe("before");
  });

  it("offers on a reply that is not the last message", () => {
    const t = branchTargets(
      [b("u1", "u1", "user"), b("a1", "a1", "assistant"), b("u2", "u2", "user"), b("a2", "a2", "assistant")],
      "a2",
    );
    expect(t.get("a1")).toBe("after");
    expect(t.has("a2")).toBe(false);
  });

  it("puts ONE control on the last block of an interrupted reply", () => {
    // One event, three blocks, one anchor. Three controls would read as three
    // branch points.
    const t = branchTargets(
      [
        b("a1:0", "a1", "assistant"),
        b("u1", "u1", "user"),
        b("a1:1", "a1", "assistant"),
        b("u2", "u2", "user"),
        b("a1:2", "a1", "assistant"),
      ],
      "a1",
    );
    expect(t.has("a1:0")).toBe(false);
    expect(t.has("a1:1")).toBe(false);
    // and the last block of that event is also the head, so it is dropped too
    expect(t.has("a1:2")).toBe(false);
    expect(t.get("u1")).toBe("before");
    expect(t.get("u2")).toBe("before");
  });

  it("keeps the last block of an interrupted reply when it is NOT the head", () => {
    const t = branchTargets(
      [b("a1:0", "a1", "assistant"), b("u1", "u1", "user"), b("a1:1", "a1", "assistant"), b("u2", "u2", "user")],
      "u2",
    );
    expect(t.has("a1:0")).toBe(false);
    expect(t.get("a1:1")).toBe("after");
  });

  it("offers everywhere when the head is unknown", () => {
    // A thread whose tree row has not been resolved yet: nothing is known to be
    // the identity operation, so nothing is withheld.
    const t = branchTargets([b("u1", "u1", "user"), b("a1", "a1", "assistant")], null);
    expect(t.get("a1")).toBe("after");
  });

  it("is empty for an empty transcript", () => {
    expect(branchTargets([], null).size).toBe(0);
  });

  it("offers no control on an error message, and keeps the prompt before it", () => {
    // A failed turn is not a point to return to (operator, 2026-08-23). The
    // errored assistant bubble carries no control; the user prompt that led to it
    // still does, so retrying from before the failure is one click away.
    const err = (key: string, id: string, role: string): BranchBlock =>
      ({ key, id, role, error: true });
    const t = branchTargets(
      [b("u1", "u1", "user"), err("a1", "a1", "assistant")],
      "a1",
    );
    expect(t.has("a1")).toBe(false);
    expect(t.get("u1")).toBe("before");
  });
});
