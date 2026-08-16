import { describe, it, expect } from "vitest";
import { composeTaskText } from "./uiCardActions";

// composeTaskText is the whole seam between a proposal card and the ticket
// store: the store reads the first line as the title and the rest as the body,
// so what this returns IS the filed ticket. Three input shapes reach it,
// because a transcript is permanent and old cards re-render from their stored
// block.
describe("composeTaskText", () => {
  it("files the title as the first line and the description under it", () => {
    expect(composeTaskText({
      title: "Fix the flaky send",
      description: "Objective: sends stop dropping.\nOutput: the script exits 0.",
    })).toBe("Fix the flaky send\n\nObjective: sends stop dropping.\nOutput: the script exits 0.");
  });

  it("does not repeat a title the description already opens with", () => {
    expect(composeTaskText({
      title: "Fix the flaky send",
      description: "Fix the flaky send\n\nObjective: sends stop dropping.",
    })).toBe("Fix the flaky send\n\nObjective: sends stop dropping.");
  });

  it("files a title with no description", () => {
    expect(composeTaskText({ title: "Fix the flaky send" })).toBe("Fix the flaky send");
  });

  it("files a description with no title", () => {
    expect(composeTaskText({ description: "Objective: sends stop dropping." }))
      .toBe("Objective: sends stop dropping.");
  });

  it("still composes a legacy card's one-field-per-section spec", () => {
    expect(composeTaskText({
      title: "Fix the flaky send",
      objective: "Sends stop dropping.",
      output: "The script exits 0.",
      boundaries: "Nothing under backend/.",
    })).toBe([
      "Fix the flaky send",
      "",
      "Objective: Sends stop dropping.",
      "Output: The script exits 0.",
      "Boundaries: Nothing under backend/.",
    ].join("\n"));
  });

  it("still files a legacy card's plain text body, whose first line is its title", () => {
    expect(composeTaskText({ text: "Fix the flaky send\nIt drops under load." }))
      .toBe("Fix the flaky send\nIt drops under load.");
  });
});
