import { describe, it, expect } from "vitest";
import { authorLabel, chatRefsIn, freshNoteId } from "./notes";

describe("authorLabel", () => {
  it("reads the origin vocabulary", () => {
    expect(authorLabel("operator")).toBe("You");
    expect(authorLabel("self")).toBe("The assistant");
    expect(authorLabel("agent:scout")).toBe("Agent: scout");
    expect(authorLabel("derived:haiku")).toBe("Distilled by haiku");
    expect(authorLabel("tool:grep")).toBe("Tool: grep");
  });

  it("says so rather than inventing an author", () => {
    expect(authorLabel("")).toBe("Unknown author");
    expect(authorLabel(null)).toBe("Unknown author");
  });

  it("passes an origin it does not recognise straight through", () => {
    expect(authorLabel("someone")).toBe("someone");
  });
});

describe("chatRefsIn", () => {
  it("finds each chat ref once", () => {
    const body = "see workshop:chat/abc and workshop:chat/abc and workshop:chat/def ok";
    expect(chatRefsIn(body)).toEqual(["workshop:chat/abc", "workshop:chat/def"]);
  });

  it("stops at a bracket so a markdown link does not swallow the paren", () => {
    expect(chatRefsIn("[a](workshop:chat/abc)")).toEqual(["workshop:chat/abc"]);
  });

  it("finds nothing in a body with no refs", () => {
    expect(chatRefsIn("plain prose")).toEqual([]);
  });
});

describe("freshNoteId", () => {
  it("slugs the title and appends a timestamp", () => {
    expect(freshNoteId("Ports are squatted!", 0)).toBe("ports-are-squatted-0");
  });

  it("falls back to note when the title slugs to nothing", () => {
    expect(freshNoteId("!!!", 0)).toBe("note-0");
    expect(freshNoteId("", 0)).toBe("note-0");
  });

  it("caps the slug so a paragraph title cannot become a filename", () => {
    const long = "a".repeat(100);
    const id = freshNoteId(long, 0);
    expect(id.startsWith("a".repeat(48) + "-")).toBe(true);
  });

  it("never collides with itself across two calls at different times", () => {
    expect(freshNoteId("x", 1)).not.toBe(freshNoteId("x", 2));
  });
});
