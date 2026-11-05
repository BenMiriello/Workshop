import { describe, expect, it } from "vitest";
import { fieldName, placeName } from "./placeName";

describe("fieldName", () => {
  it("takes the first thing that names the field", () => {
    expect(fieldName({ ariaLabel: "Search", placeholder: "Type here" })).toBe("Search");
    expect(fieldName({ ariaLabel: " ", label: "Title" })).toBe("Title");
    expect(fieldName({ placeholder: "Message…" })).toBe("Message");
    expect(fieldName({})).toBe("");
  });

  it("keeps a long name short", () => {
    const name = fieldName({ placeholder: "Describe what you want the picture to show in detail" });
    expect(name.length).toBeLessThanOrEqual(32);
    expect(name.endsWith("…")).toBe(true);
  });
});

describe("placeName", () => {
  it("names the field and the page", () => {
    expect(placeName("Message", "Chats")).toBe("Message in Chats");
    expect(placeName("", "Settings")).toBe("Text field in Settings");
    expect(placeName("Prompt", "")).toBe("Prompt");
  });
});
