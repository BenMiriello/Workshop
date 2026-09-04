import { describe, expect, it } from "vitest";
import {
  NO_PROBLEMS, clearProblems, currentProblem, reportProblem,
  type ProblemState,
} from "./problemState";

const lost = { text: "Couldn't refresh the list of conversations: 502.", endpoint: "/api/chat/threads" };
const rename = { text: "Couldn't rename this conversation: 500.", endpoint: null };
const blockedWrite = { text: "The tree is read-only. The write to /a did not happen." };

describe("the chat's problem slot", () => {
  it("shows what was reported", () => {
    const s = reportProblem(NO_PROBLEMS, "threads", lost);
    expect(currentProblem(s)).toEqual({ source: "threads", problem: lost });
  });

  it("lets the source that raised a failure take it back when it recovers", () => {
    // THE REPORTED BUG, 2026-09-04: "warnings will come up about a connection
    // being lost ... and then those warnings will get STUCK." The reader that
    // saw the 502 is the only one that can know the list came back, and now it
    // has a way to say so.
    let s: ProblemState = reportProblem(NO_PROBLEMS, "threads", lost);
    s = reportProblem(s, "threads", null);
    expect(currentProblem(s)).toBeNull();
  });

  it("does not let one source's recovery erase another's failure", () => {
    // This is why a single slot could not be fixed in place. A successful poll
    // must not swallow a refused rename it knows nothing about.
    let s: ProblemState = reportProblem(NO_PROBLEMS, "action", rename);
    s = reportProblem(s, "threads", lost);
    s = reportProblem(s, "threads", null);
    expect(currentProblem(s)).toEqual({ source: "action", problem: rename });
  });

  it("shows the newest failure and keeps the older one behind it", () => {
    let s: ProblemState = reportProblem(NO_PROBLEMS, "action", rename);
    s = reportProblem(s, "log", lost);
    expect(currentProblem(s)?.source).toBe("log");
    // Dismissing the top one reveals the next rather than losing it.
    s = reportProblem(s, "log", null);
    expect(currentProblem(s)?.source).toBe("action");
  });

  it("gives a one-shot notice a way off the screen", () => {
    // A blocked write or a preempted tree has no condition that can recover,
    // so dismissal is the only path there is. Without one it is stuck by
    // construction.
    let s: ProblemState = reportProblem(NO_PROBLEMS, "turn", blockedWrite);
    const top = currentProblem(s);
    expect(top).not.toBeNull();
    s = reportProblem(s, top!.source, null);
    expect(currentProblem(s)).toBeNull();
  });

  it("keeps one entry per source, and re-raising replaces it", () => {
    let s: ProblemState = reportProblem(NO_PROBLEMS, "log", lost);
    s = reportProblem(s, "log", rename);
    expect(s).toHaveLength(1);
    expect(currentProblem(s)?.problem).toEqual(rename);
  });

  it("does not churn state when a failing poll says the same thing again", () => {
    // The backstop sync fails every 60s for as long as an outage lasts, and it
    // builds a fresh object with a fresh retry closure each time. Re-rendering
    // the whole chat on each of those is waste.
    const s = reportProblem(NO_PROBLEMS, "log", { ...lost, retry: () => {} });
    expect(reportProblem(s, "log", { ...lost, retry: () => {} })).toBe(s);
  });

  it("still moves a repeated failure to the front when something newer is on top", () => {
    let s: ProblemState = reportProblem(NO_PROBLEMS, "log", lost);
    s = reportProblem(s, "action", rename);
    s = reportProblem(s, "log", lost);
    expect(currentProblem(s)?.source).toBe("log");
  });

  it("resolving a source that has nothing held changes nothing", () => {
    expect(reportProblem(NO_PROBLEMS, "scope", null)).toBe(NO_PROBLEMS);
  });

  it("drops everything when the conversation changes", () => {
    let s: ProblemState = reportProblem(NO_PROBLEMS, "log", lost);
    s = reportProblem(s, "action", rename);
    expect(clearProblems(s)).toHaveLength(0);
    expect(clearProblems(NO_PROBLEMS)).toBe(NO_PROBLEMS);
  });
});
