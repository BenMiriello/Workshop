import { describe, expect, it } from "vitest";
import { ERROR_BODY_CAP, shortHttpError } from "./httpError";

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
      .toBe("500 The server errored.");
  });

  it("drops a body over the cap, even when it is plain text with no markup", () => {
    const long = "n".repeat(ERROR_BODY_CAP + 1);
    expect(shortHttpError(400, "Bad Request", long)).toBe("400 Bad Request");
  });

  it("keeps a body right at the cap", () => {
    const atCap = "n".repeat(ERROR_BODY_CAP);
    expect(shortHttpError(400, "Bad Request", atCap)).toBe(`400 ${atCap}`);
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

  it("also collapses 503 and 504 to the same retry sentence", () => {
    expect(shortHttpError(503, "Service Unavailable", "")).toBe("Server unavailable (503). Retrying.");
    expect(shortHttpError(504, "Gateway Timeout", "<html>timed out</html>")).toBe("Server unavailable (504). Retrying.");
  });

  it("maps the common statuses to a short plain sentence when there is no usable body", () => {
    expect(shortHttpError(401, "Unauthorized", "")).toBe("401 Not signed in.");
    expect(shortHttpError(403, "Forbidden", "")).toBe("403 Not allowed.");
    expect(shortHttpError(404, "Not Found", "")).toBe("404 Not found.");
    expect(shortHttpError(500, "", "")).toBe("500 The server errored.");
  });

  it("falls all the way back to statusText, then a generic sentence, for an unmapped status", () => {
    expect(shortHttpError(418, "I'm a teapot", "")).toBe("418 I'm a teapot");
    expect(shortHttpError(418, "", "")).toBe("418 request failed");
  });
});
