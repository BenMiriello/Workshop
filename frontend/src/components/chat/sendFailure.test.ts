import { describe, expect, it } from "vitest";
import { isTransientSendFailure, sendFailureText } from "./sendFailure";
import { shortHttpError } from "@workshop/ui";

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

  it("says the server, not the message, on a gateway failure, without infra jargon", () => {
    const s = sendFailureText(new Error("Server unavailable (502). Retrying."));
    expect(s).toMatch(/server is briefly unavailable/);
    // "restarting" describes our process mechanics, not what the operator sees:
    // a user-facing app should not narrate its own backend's lifecycle.
    expect(s).not.toMatch(/restart/);
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

  it("still blames the session for a bare 401, which is the one that means it", () => {
    expect(sendFailureText(new Error("401 Unauthorized"))).toMatch(/not signed in/);
    expect(sendFailureText(new Error("401"))).toMatch(/not signed in/);
  });

  // A bare 403 used to say the same thing as a bare 401, and for 403 it is
  // false: the request WAS authenticated and was refused anyway. The operator
  // hit one on 2026-08-11, reloaded because the sentence told him to, came
  // straight back into a working session, and concluded the app was lying.
  // A Cloudflare-style HTML refusal in front of the origin arrives exactly this
  // way, because shortHttpError drops HTML bodies and leaves no detail.
  it("does not claim a bare 403 signed you out", () => {
    for (const raw of ["403", "403 Forbidden", "403 Access denied"]) {
      const s = sendFailureText(new Error(raw));
      expect(s).not.toMatch(/not signed in/);
      expect(s).toMatch(/still signed in/);
      expect(s).toMatch(/refused/);
    }
  });

  it("does not send you to reload for a 403, since that cannot help", () => {
    expect(sendFailureText(new Error("403 Forbidden"))).not.toMatch(/Reload the page/);
  });

  // `shortHttpError` now maps a bare 401/403 (no body) to its own short
  // sentence ("Not signed in.", "Not allowed.") instead of echoing the
  // browser's statusText. BARE_AUTH_REASON has to recognise that sentence too,
  // or a bare refusal starts reading as a real, backend-written reason.
  it("still reads shortHttpError's own bare-401/403 sentence as no real reason", () => {
    expect(sendFailureText(new Error(shortHttpError(401, "Unauthorized", "")))).toMatch(/not signed in/);
    const s403 = sendFailureText(new Error(shortHttpError(403, "Forbidden", "")));
    expect(s403).not.toMatch(/not signed in/);
    expect(s403).toMatch(/still signed in/);
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
