import { describe, it, expect } from "vitest";
import {
  composeTaskText, resolveCardActions, DEFAULT_CARD_ACTIONS, UI_CARD_ACTIONS, hasCardHandler,
} from "./uiCardActions";

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

// The card renders model-authored actions, and a model can name an id this
// build has no handler for. resolveCardActions is what keeps that from ever
// producing a dead button or a known card with no way to act.
describe("resolveCardActions", () => {
  const ids = (as: { id: string }[]) => as.map((a) => a.id);

  it("drops an action whose id has no handler", () => {
    const out = resolveCardActions("task-proposal", [
      { id: "addTasks", label: "File ticket" },
      { id: "fileItRightNow", label: "File (typo)" },
    ]);
    expect(ids(out)).toEqual(["addTasks"]);
  });

  it("falls back to the kind's canonical actions when the model named none this build can run", () => {
    expect(ids(resolveCardActions("task-proposal", []))).toEqual(ids(DEFAULT_CARD_ACTIONS["task-proposal"]));
    expect(ids(resolveCardActions("task-proposal", undefined))).toEqual(ids(DEFAULT_CARD_ACTIONS["task-proposal"]));
    expect(ids(resolveCardActions("project-proposal", [{ id: "nope", label: "x" }])))
      .toEqual(ids(DEFAULT_CARD_ACTIONS["project-proposal"]));
  });

  it("keeps the model's valid actions rather than the canonical set when it gave usable ones", () => {
    const out = resolveCardActions("task-proposal", [{ id: "dismiss", label: "No thanks" }]);
    expect(ids(out)).toEqual(["dismiss"]);
    expect(out[0].label).toBe("No thanks"); // the model's own label, not the canonical one
  });

  it("hides the run action where the scope cannot run tickets", () => {
    const runnable = resolveCardActions("task-proposal", undefined, { runnable: true });
    const notRunnable = resolveCardActions("task-proposal", undefined, { runnable: false });
    expect(ids(runnable)).toContain("addTasksAndRun");
    expect(ids(notRunnable)).not.toContain("addTasksAndRun");
    expect(ids(notRunnable)).toContain("addTasks"); // filing still offered
  });

  it("offers nothing for a kind it has no canonical actions for", () => {
    expect(resolveCardActions("mystery-kind", [{ id: "nope", label: "x" }])).toEqual([]);
  });

  it("every canonical action id has a registered handler", () => {
    for (const actions of Object.values(DEFAULT_CARD_ACTIONS)) {
      for (const a of actions) {
        expect(hasCardHandler(a.id), `${a.id} must be in UI_CARD_ACTIONS`).toBe(true);
        expect(UI_CARD_ACTIONS[a.id]).toBeTypeOf("function");
      }
    }
  });
});
