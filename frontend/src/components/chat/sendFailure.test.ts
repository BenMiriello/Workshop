import { describe, expect, it } from "vitest";
import { isTransientSendFailure, sendFailureText } from "./sendFailure";

// The bug this pins: `setErr(String(e))` put "TypeError: Failed to fetch" on
// screen as the app's explanation of what happened to a message.

describe("a message that did not send", () => {
  it("says the server could not be reached, not what the browser threw", () => {
    const s = sendFailureText(new TypeError("Failed to fetch"));
    expect(s).not.toMatch(/TypeError/);
    expect(s).not.toMatch(/Failed to fetch/);
    expect(s).toMatch(/could not be reached/);
  });

  it("recognises the other browsers' wording for the same thing", () => {
    for (const m of ["NetworkError when attempting to fetch resource.", "Load failed"]) {
      expect(sendFailureText(new Error(m))).toMatch(/could not be reached/);
    }
  });

  it("names a restart rather than blaming the message, on a gateway failure", () => {
    expect(sendFailureText(new Error("Server unavailable (502). Retrying."))).toMatch(/restarting/);
  });

  it("distinguishes the server failing from the server refusing", () => {
    expect(sendFailureText(new Error("500 boom"))).toMatch(/failed to start this turn/);
    expect(sendFailureText(new Error("400 bad thread"))).toMatch(/refused this message/);
  });

  it("keeps the server's own detail, which is the part that identifies the fault", () => {
    expect(sendFailureText(new Error("400 thread is archived"))).toMatch(/thread is archived/);
  });

  it("tells the operator what to do wherever there is something to do", () => {
    for (const e of [new TypeError("Failed to fetch"), new Error("500 boom"), new Error("nope")]) {
      expect(sendFailureText(e)).toMatch(/Retry/);
    }
  });

  it("does not say Retry for a refusal that retrying cannot fix", () => {
    // A 4xx is the server saying this message is not acceptable. Offering Retry
    // there is the same false promise the whole item is about.
    expect(sendFailureText(new Error("400 bad thread"))).not.toMatch(/Retry/);
  });

  it("bounds an error page instead of letting it take over the transcript", () => {
    const s = sendFailureText(new Error(`500 ${"x".repeat(4000)}`));
    expect(s.length).toBeLessThan(260);
  });

  it("still says something for an error it has never seen", () => {
    expect(sendFailureText(undefined)).toMatch(/never reached the server/);
    expect(sendFailureText("")).toMatch(/never reached the server/);
  });

  it("always opens by saying the message was not sent", () => {
    for (const e of [new TypeError("Failed to fetch"), new Error("500 x"), new Error("400 y"),
      new Error("403 z"), new Error("weird"), null]) {
      expect(sendFailureText(e).startsWith("Not sent")).toBe(true);
    }
  });

  // Five real 403s were reported to the operator as a sign-in problem. They were
  // the chat refusing a developer-mode scope, in a sentence it had written
  // itself, which this then threw away.
  it("keeps a refusal's own reason instead of guessing at the session", () => {
    const s = sendFailureText(new Error("403 developer mode is off for this scope"));
    expect(s).toMatch(/developer mode is off/);
    expect(s).not.toMatch(/signed in/);
  });

  it("still blames the session for a bare 401 or 403", () => {
    expect(sendFailureText(new Error("401 Unauthorized"))).toMatch(/not signed in/);
  });
});

// WHICH FAILURES GO AWAY BY THEMSELVES. This decides whether the send is retried
// once in silence or the operator is handed a dead bubble, so it must not creep:
// anything the server ANSWERED with is about this message and is not transient.
describe("a failure that clears on its own", () => {
  it("covers the connection dropping, however the browser words it", () => {
    for (const m of ["Failed to fetch", "NetworkError when attempting to fetch resource",
      "Load failed", "Network request failed"]) {
      expect(isTransientSendFailure(new TypeError(m))).toBe(true);
    }
  });

  it("covers the edge's answer while the backend restarts", () => {
    for (const s of [502, 503, 504]) {
      expect(isTransientSendFailure(new Error(`Server unavailable (${s}). Retrying.`))).toBe(true);
    }
  });

  it("does not cover an answer that is about this message", () => {
    for (const m of ["400 bad thread", "403 developer mode is off", "404 thread not found",
      "500 the turn could not be started", "409 already running"]) {
      expect(isTransientSendFailure(new Error(m))).toBe(false);
    }
  });

  it("does not cover an error it cannot read", () => {
    expect(isTransientSendFailure(new Error("something went sideways"))).toBe(false);
    expect(isTransientSendFailure(undefined)).toBe(false);
  });
});
