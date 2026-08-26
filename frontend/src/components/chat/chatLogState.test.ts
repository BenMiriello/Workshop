import { describe, expect, it } from "vitest";
import { logPlaceholder, noRealContent } from "./chatLogState";
import type { TranscriptBlock } from "./transcriptBlocks";

const base = { empty: true, loading: false, error: false, inFlight: false };

const committed: TranscriptBlock = {
  kind: "message", key: "k", id: "k", seq: 1,
  msg: { role: "user", content: "hi", ts: "" },
};
const failedPending: TranscriptBlock = {
  kind: "pending", key: "p:1", clientId: "1", text: "x", status: "failed",
};
const sendingPending: TranscriptBlock = {
  kind: "pending", key: "p:2", clientId: "2", text: "y", status: "sending",
};

describe("logPlaceholder", () => {
  it("shows nothing once there are blocks to render and loading has settled", () => {
    expect(logPlaceholder({ ...base, empty: false })).toBe(null);
    expect(logPlaceholder({ ...base, empty: false, error: true })).toBe(null);
  });

  it("NEVER draws a loading region OVER real content", () => {
    // The bug the operator reported at least seven times: on a NEW chat the
    // thread is created the instant the first message is sent, `hydrated` flips
    // back to false while the new (empty) thread hydrates, and a "Loading..."
    // region drew above the message already on screen. The message shoved down
    // the page, then snapped back when hydration landed. A region stands IN
    // PLACE of the transcript; it may not stack over it.
    expect(logPlaceholder({ ...base, empty: false, loading: true })).toBe(null);
  });

  it("NEVER claims empty while the log is still loading", () => {
    expect(logPlaceholder({ ...base, loading: true })).toBe("loading");
    // The regression this exists for: a thread whose first page is in flight.
    // Blocks are empty and nothing is streaming yet, and the answer is still
    // "loading", not "this conversation is empty".
    expect(logPlaceholder({ ...base, loading: true, inFlight: false })).toBe("loading");
  });

  it("shows loading over a thread that holds only a restored stub", () => {
    // The 2026-08-14 case: the outbox restores a failed send SYNCHRONOUSLY on
    // thread select, while the real history is still loading asynchronously. A
    // restored stub is NOT real content (see noRealContent), so the transcript
    // still counts as empty here and loading wins: the honest answer while the
    // log has not hydrated is "loading", not one old failed message standing in
    // for the whole conversation.
    expect(noRealContent([failedPending])).toBe(true);
    expect(logPlaceholder({ ...base, empty: noRealContent([failedPending]), loading: true }))
      .toBe("loading");
  });

  it("prefers loading over an error left from a previous load", () => {
    expect(logPlaceholder({ ...base, loading: true, error: true })).toBe("loading");
  });

  it("shows the error when a finished load failed", () => {
    expect(logPlaceholder({ ...base, error: true })).toBe("error");
  });

  it("stays quiet when a run is in flight but has produced nothing", () => {
    expect(logPlaceholder({ ...base, inFlight: true })).toBe(null);
  });

  it("claims empty only for a settled, idle, error-free empty log", () => {
    expect(logPlaceholder(base)).toBe("empty");
  });
});

describe("noRealContent", () => {
  it("is true for nothing at all", () => {
    expect(noRealContent([])).toBe(true);
  });
  it("is true for a transcript of only restored failed stubs", () => {
    expect(noRealContent([failedPending, failedPending])).toBe(true);
  });
  it("is false the moment a committed message is present", () => {
    // The new-chat message: real content, so no region draws over it.
    expect(noRealContent([committed])).toBe(false);
    expect(noRealContent([failedPending, committed])).toBe(false);
  });
  it("is false for an in-flight send, which is the operator's live action", () => {
    expect(noRealContent([sendingPending])).toBe(false);
  });
});
