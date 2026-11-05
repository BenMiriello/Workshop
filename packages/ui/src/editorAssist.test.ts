import { describe, expect, it } from "vitest";
import { matchTrigger } from "./editorAssist";

describe("matchTrigger", () => {
  it("finds a mention query at the cursor", () => {
    expect(matchTrigger("met @Ma", "@")).toEqual({ start: 4, query: "Ma" });
  });

  it("opens on the bare trigger with an empty query", () => {
    expect(matchTrigger("see [[", "[[", "]]")).toEqual({ start: 4, query: "" });
  });

  it("lets a closed source's query carry spaces", () => {
    expect(matchTrigger("[[A Lust for", "[[", "]]")).toEqual({ start: 0, query: "A Lust for" });
  });

  it("stops an unclosed source at a space", () => {
    expect(matchTrigger("@Mara said", "@")).toBeNull();
  });

  it("ends a closed source once its close is typed", () => {
    expect(matchTrigger("[[Note]] and", "[[", "]]")).toBeNull();
  });

  it("ignores an @ inside a word or an address", () => {
    expect(matchTrigger("me@site", "@")).toBeNull();
    expect(matchTrigger("a.b@c", "@")).toBeNull();
  });

  it("uses the last trigger on the line", () => {
    expect(matchTrigger("@Ann met @Jo", "@")).toEqual({ start: 9, query: "Jo" });
  });

  it("answers null with no trigger present", () => {
    expect(matchTrigger("plain text", "@")).toBeNull();
  });
});
