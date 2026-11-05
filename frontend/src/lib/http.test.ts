import { describe, expect, it } from "vitest";
import { HttpError, isTransientNetworkError } from "@workshop/ui";
import { httpFail } from "./http";

function res(status: number, body = ""): Response {
  return new Response(body, { status });
}

describe("the shared failed-response error", () => {
  it("carries the status, so a gateway blip is retried", async () => {
    const e = await httpFail(res(502, "<html>bad gateway</html>"));
    expect(e).toBeInstanceOf(HttpError);
    expect(e.status).toBe(502);
    expect(isTransientNetworkError(e)).toBe(true);
  });

  it("keeps a real refusal a refusal", async () => {
    const e = await httpFail(res(403, JSON.stringify({ detail: "operator only" })));
    expect(isTransientNetworkError(e)).toBe(false);
    expect(e.message).toBe("403 operator only");
  });
});
