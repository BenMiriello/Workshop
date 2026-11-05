import { describe, expect, it } from "vitest";
import { findTarget, nameFit, nameFrom, NAME_MAX, spokenNumber } from "./targetMatch";

describe("spokenNumber", () => {
  it("reads digits and number words", () => {
    expect(spokenNumber("4")).toBe(4);
    expect(spokenNumber("four")).toBe(4);
    expect(spokenNumber("twelve")).toBe(12);
    expect(spokenNumber("twenty")).toBe(20);
    expect(spokenNumber("twenty one")).toBe(21);
    expect(spokenNumber("twenty-one")).toBe(21);
    expect(spokenNumber("ninety nine")).toBe(99);
    expect(spokenNumber(" Four. ")).toBe(4);
  });

  it("reads numbers past a hundred, as written and as said", () => {
    expect(spokenNumber("123")).toBe(123);
    expect(spokenNumber("a hundred")).toBe(100);
    expect(spokenNumber("one hundred and five")).toBe(105);
    expect(spokenNumber("two hundred fifty")).toBe(250);
    expect(spokenNumber("one twenty three")).toBe(123);
    expect(spokenNumber("one fifty")).toBe(150);
    expect(spokenNumber("one hundred two hundred")).toBeNull();
  });

  it("tolerates a number prefix", () => {
    expect(spokenNumber("number twelve")).toBe(12);
    expect(spokenNumber("no. 21")).toBe(21);
    expect(spokenNumber("#7")).toBe(7);
  });

  it("does not read homophones or words as numbers", () => {
    for (const w of ["for", "to", "too", "number", "nothing", "send", "twenty twelve", "one two", ""]) {
      expect(spokenNumber(w)).toBeNull();
    }
  });
});

const targets = [
  { n: 1, name: "Open menu" },
  { n: 2, name: "Sent" },
  { n: 3, name: "Send" },
  { n: 4, name: "Send later" },
  { n: 5, name: "Search messages" },
  { n: 6, name: "Settings" },
  { n: 7, name: "" },
];

describe("findTarget", () => {
  it("finds by number", () => {
    expect(findTarget(targets, "4")?.n).toBe(4);
    expect(findTarget(targets, "number seven")?.n).toBe(7);
    expect(findTarget(targets, "twenty one")).toBeNull();
  });

  it("prefers the exact name over one letter off", () => {
    expect(findTarget(targets, "send")?.n).toBe(3);
  });

  it("prefers fewest extra name words", () => {
    expect(findTarget(targets, "send later")?.n).toBe(4);
    expect(findTarget(targets, "menu")?.n).toBe(1);
  });

  it("matches a prefix of three letters or more", () => {
    expect(findTarget(targets, "sett")?.n).toBe(6);
    expect(findTarget(targets, "mess")?.n).toBe(5);
    expect(findTarget(targets, "se")).toBeNull();
  });

  it("matches one letter off for words of four letters or more", () => {
    expect(findTarget(targets, "serch messages")?.n).toBe(5);
    expect(findTarget(targets, "settigns")).toBeNull();
  });

  it("ignores filler words", () => {
    expect(findTarget(targets, "the send button please")?.n).toBe(3);
    expect(findTarget(targets, "button")).toBeNull();
  });

  it("ties go to the earlier target", () => {
    const twins = [{ n: 1, name: "Reply" }, { n: 2, name: "Reply" }];
    expect(findTarget(twins, "reply")?.n).toBe(1);
  });

  it("needs every query word to match", () => {
    expect(findTarget(targets, "send now")).toBeNull();
    expect(findTarget(targets, "delete")).toBeNull();
  });
});

describe("nameFit", () => {
  it("scores a fit and counts the name words left over", () => {
    expect(nameFit("Send later", "send")).toEqual({ score: 3, extra: 1, exact: true });
    expect(nameFit("Settings", "sett")).toEqual({ score: 2, extra: 0, exact: false });
    expect(nameFit("Settings", "the settings icon")).toEqual({ score: 3, extra: 0, exact: true });
    expect(nameFit("Settings", "gear")).toBeNull();
    expect(nameFit("Settings", "the")).toBeNull();
  });
});

describe("nameFrom", () => {
  it("picks parts in order", () => {
    expect(nameFrom({ title: "T", text: "Text", ariaLabel: "Aria", labelledBy: "By" })).toBe("By");
    expect(nameFrom({ title: "T", text: "Text", ariaLabel: "Aria" })).toBe("Aria");
    expect(nameFrom({ title: "T", text: "Text", label: "Label", placeholder: "P" })).toBe("Label");
    expect(nameFrom({ title: "T", text: "Text", placeholder: "Search" })).toBe("Search");
    expect(nameFrom({ title: "T", text: "Text", alt: "Logo" })).toBe("Logo");
    expect(nameFrom({ title: "T", text: "  " })).toBe("T");
    expect(nameFrom({})).toBe("");
  });

  it("collapses whitespace", () => {
    expect(nameFrom({ text: "\n  Save \t  draft\n" })).toBe("Save draft");
  });

  it("clips long names at a word boundary", () => {
    const long = "Generate a new image from the current prompt and settings";
    const name = nameFrom({ text: long });
    expect(name.length).toBeLessThanOrEqual(NAME_MAX);
    expect(long.startsWith(name)).toBe(true);
    expect(long[name.length]).toBe(" ");
    expect(nameFrom({ text: "x".repeat(60) })).toHaveLength(NAME_MAX);
  });
});
