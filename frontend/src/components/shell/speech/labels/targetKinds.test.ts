import { describe, expect, it } from "vitest";
import { describeTarget, iconMeaning, iconName, kindOf } from "./targetKinds";

describe("iconName", () => {
  it("reads lucide's class", () => {
    expect(iconName("lucide lucide-settings lucide-settings")).toBe("settings");
    expect(iconName("lucide lucide-trash2 lucide-trash-2 ws-icon")).toBe("trash");
    expect(iconName("lucide lucide-icon lucide-ellipsis-vertical")).toBe("ellipsis vertical");
    expect(iconName("ws-icon")).toBe("");
  });

  it("says what a shape stands for", () => {
    expect(iconMeaning("settings")).toBe("settings gear");
    expect(iconMeaning("x")).toBe("x close");
    expect(iconMeaning("globe")).toBe("globe");
  });
});

describe("kindOf", () => {
  const base = { tag: "DIV", role: "", type: "", editable: false };
  it("goes by role, then by element", () => {
    expect(kindOf({ ...base, role: "tab" })).toBe("tab");
    expect(kindOf({ ...base, tag: "A" })).toBe("link");
    expect(kindOf({ ...base, tag: "A", role: "button" })).toBe("button");
    expect(kindOf({ ...base, tag: "INPUT", type: "text" })).toBe("field");
    expect(kindOf({ ...base, tag: "INPUT", type: "checkbox" })).toBe("checkbox");
    expect(kindOf({ ...base, tag: "IMG" })).toBe("image");
    expect(kindOf({ ...base, editable: true })).toBe("field");
    expect(kindOf(base)).toBe("button");
  });
});

describe("describeTarget", () => {
  it("says name, kind, icon, title, row and section once each", () => {
    expect(describeTarget({
      name: "Delete", kind: "button", image: false, icon: "trash", title: "Delete, hold for options",
      row: "Beta notes", context: "Library",
    })).toBe("Delete, button, trash delete bin icon, Delete, hold for options, Beta notes, in Library");
    expect(describeTarget({ name: "", kind: "link", image: true, icon: "", title: "", row: "", context: "" }))
      .toBe("image link");
    expect(describeTarget({ name: "", kind: "button", image: false, icon: "list", title: "", row: "", context: "Chats" }))
      .toBe("button, list icon, in Chats");
  });
});
