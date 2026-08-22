import { describe, expect, it } from "vitest";
import { decisionView, type DecisionInput } from "./ticketDecision";

const input = (over: Partial<DecisionInput> = {}): DecisionInput => ({
  ticketId: "t1",
  cold: false,
  inbox: [],
  status: "in_review",
  ...over,
});

const open = (ticketId: string, body = "The work is finished and waiting on you.") =>
  ({ id: "i1", ticket_id: ticketId, body });

describe("what the inline decision shows", () => {
  it("draws nothing for a message that names no ticket", () => {
    expect(decisionView(input({ ticketId: null })).kind).toBe("hidden");
  });

  it("draws nothing before the work payload has been read once", () => {
    // The flash this prevents: "Accepted" appearing under a live question for
    // the first second of every page load.
    expect(decisionView(input({ cold: true, inbox: [open("t1")] })).kind).toBe("hidden");
  });

  it("asks when a row for this ticket is open", () => {
    const v = decisionView(input({ inbox: [open("t1")] }));
    expect(v).toMatchObject({ kind: "pending", itemId: "i1", finished: true });
  });

  it("ignores a row belonging to a different ticket", () => {
    expect(decisionView(input({ inbox: [open("other")], status: "done" })))
      .toMatchObject({ kind: "resolved", said: "Accepted" });
  });

  it("offers the finished pair only for a finished ticket", () => {
    const stuck = decisionView(input({ inbox: [open("t1")], status: "blocked" }));
    expect(stuck).toMatchObject({ kind: "pending", finished: false });
  });

  it("says accepted once the ticket is done", () => {
    expect(decisionView(input({ status: "done" })))
      .toMatchObject({ kind: "resolved", said: "Accepted" });
  });

  it("says cancelled once the ticket is cancelled", () => {
    expect(decisionView(input({ status: "cancelled" })))
      .toMatchObject({ kind: "resolved", said: "Cancelled" });
  });

  it("says answered once it is back in the queue", () => {
    // todo covers both "sent back" and "answered", and nothing on the record
    // tells them apart afterwards, so the word covers both rather than guessing.
    expect(decisionView(input({ status: "todo" })))
      .toMatchObject({ kind: "resolved", said: "Answered" });
  });

  it("stays quiet while waiting with no row yet", () => {
    // Still in_review but the inbox row has not arrived: claiming a decision
    // here would be a lie that the next poll silently corrects.
    expect(decisionView(input({ status: "in_review" })).kind).toBe("hidden");
    expect(decisionView(input({ status: "blocked" })).kind).toBe("hidden");
  });

  it("stays quiet for a ticket this chat cannot see", () => {
    expect(decisionView(input({ status: undefined })).kind).toBe("hidden");
  });
});
