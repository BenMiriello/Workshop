import { describe, expect, it } from "vitest";
import { threadAgent, type ThreadSummary } from "./chat";

// The rule behind the Chats page's "Agents" filter, its agent tag and its
// search, extracted so all three provably agree.
//
// It used to be "the thread carries a non-empty `participants` list", a field
// written only by the P5 promotion migration and read only by that filter. The
// question the operator is actually asking is WHO ANSWERS, and there are two
// ways a thread answers that, so there is one function.

const base: Pick<ThreadSummary, "context_key" | "agent_id"> = {
  context_key: "general",
  agent_id: null,
};

describe("threadAgent", () => {
  it("names the agent that answers an agent-bound chat", () => {
    expect(threadAgent({ ...base, context_key: "agent:scribe" })).toBe("scribe");
  });

  it("names the agent a re-homed chat was with", () => {
    // The 15 P5 casualties: their transcript stays in the project where their
    // work is, and this is what still says who it was with.
    expect(threadAgent({ context_key: "project:ledger", agent_id: "ledger-builder-1" }))
      .toBe("ledger-builder-1");
  });

  it("prefers the responder over the historical record when both are set", () => {
    expect(threadAgent({ context_key: "agent:scribe", agent_id: "someone-else" }))
      .toBe("scribe");
  });

  it("is null for a chat no agent answers", () => {
    expect(threadAgent(base)).toBeNull();
    expect(threadAgent({ ...base, context_key: "project:atlas" })).toBeNull();
    expect(threadAgent({ ...base, context_key: "development" })).toBeNull();
    expect(threadAgent({ ...base, context_key: "app:ledger" })).toBeNull();
  });

  it("treats a bare `agent:` as no agent rather than an empty-named one", () => {
    expect(threadAgent({ ...base, context_key: "agent:" })).toBeNull();
  });

  it("treats an empty agent_id as absent", () => {
    expect(threadAgent({ context_key: "general", agent_id: "" })).toBeNull();
    expect(threadAgent({ context_key: "general", agent_id: undefined })).toBeNull();
  });

  it("does not answer for the deleted `build:` spelling", () => {
    // `build:<slug>` is gone from the code and from the stored threads, so no
    // thread can hold one. Answering here would be this module keeping a
    // vocabulary the rest of the system no longer has.
    expect(threadAgent({ ...base, context_key: "build:smith" })).toBeNull();
  });
});
