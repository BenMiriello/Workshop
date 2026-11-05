import { describe, expect, it } from "vitest";
import { importBody } from "./historyImport";

describe("importBody", () => {
  it("keeps the record's own times and places, marked imported", () => {
    const body = importBody({ id: "a", startedAt: 0, endedAt: 1000, text: "Hi.", places: ["Message in Chats"], status: "live" });
    expect(body).toEqual({
      id: "a", text: "Hi.", places: [{ kind: "field", name: "Message in Chats", path: "" }],
      started_at: "1970-01-01T00:00:00.000Z", ended_at: "1970-01-01T00:00:01.000Z", status: "done", imported: true,
    });
  });
});
