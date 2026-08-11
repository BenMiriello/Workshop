import { describe, expect, it } from "vitest";
import { shortHttpError } from "./chat";

// What a failed request is allowed to put on screen.
//
// Two things go wrong here and both reached the operator. A gateway 5xx answers
// with a full HTML page, which rendered as a red wall over the UI. And FastAPI
// raises as `{"detail": "..."}`, so the sentence the backend wrote FOR the
// operator arrived wrapped in its own JSON and was shown that way.

describe("the line shown for a failed request", () => {
  it("unwraps FastAPI's detail, so the backend's sentence is the message", () => {
    const s = shortHttpError(403, "Forbidden", '{"detail":"developer mode is off here"}');
    expect(s).toBe("403 developer mode is off here");
  });

  it("leaves a body that is not FastAPI's shape alone", () => {
    expect(shortHttpError(400, "Bad Request", "empty message")).toBe("400 empty message");
    expect(shortHttpError(400, "Bad Request", "{not json")).toBe("400 {not json");
  });

  it("falls back to the status text when the detail is not a sentence", () => {
    expect(shortHttpError(422, "Unprocessable", '{"detail":[{"loc":["body"]}]}'))
      .toMatch(/^422 /);
  });

  it("never lets an error page through", () => {
    const html = "<!DOCTYPE html><html><body>" + "x".repeat(5000) + "</body></html>";
    expect(shortHttpError(500, "Internal Server Error", html))
      .toBe("500 Internal Server Error");
  });

  it("says a gateway failure is worth retrying, without quoting the gateway", () => {
    expect(shortHttpError(502, "Bad Gateway", "<html>cloudflare</html>"))
      .toBe("Server unavailable (502). Retrying.");
  });

  // The public edge answers 502 with this exact body when it cannot reach the
  // backend (workshop_public_app._unreachable). The status carries the meaning;
  // the body must not leak into the bubble.
  it("prefers the status for the edge's own unreachable answer", () => {
    expect(shortHttpError(502, "Bad Gateway", '{"detail":"the workshop backend is unreachable"}'))
      .toBe("Server unavailable (502). Retrying.");
  });
});
