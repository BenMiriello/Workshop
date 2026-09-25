import { describe, it, expect } from "vitest";
import { scopeIsRunnable } from "../../lib/scopePickup";
import {
  composeTaskText, resolveCardActions, DEFAULT_CARD_ACTIONS, UI_CARD_ACTIONS, hasCardHandler,
  composePromptAnswer,
  displayDefault,
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

  it("gives the prompt card its own canonical actions (submit + dismiss)", () => {
    expect(resolveCardActions("prompt", undefined).map((a) => a.id)).toEqual(["submitPrompt", "dismiss"]);
  });
});

// A prompt card (ADR 0027) returns the operator's answer to the conversation.
// composePromptAnswer is the pure seam: card values in, the message text out.
describe("composePromptAnswer", () => {
  it("answers a single-question card with just the chosen option's label", () => {
    const fields = [{ name: "approach", type: "choice" as const, options: [
      { value: "light", label: "Per-format libs" },
      { value: "markitdown", label: "markitdown" },
    ] }];
    expect(composePromptAnswer(fields, { approach: "markitdown" })).toBe("markitdown");
  });

  it("answers a free-text question with the typed value", () => {
    expect(composePromptAnswer([{ name: "name", type: "text" }], { name: "atlas" })).toBe("atlas");
  });

  it("labels each answer when the card asks more than one thing", () => {
    const fields = [
      { name: "size", label: "Size", type: "choice" as const, options: [{ value: "s", label: "Small" }] },
      { name: "note", label: "Note", type: "text" as const },
    ];
    expect(composePromptAnswer(fields, { size: "s", note: "urgent" })).toBe("Size: Small\nNote: urgent");
  });

  it("is empty when nothing was answered, so the handler can refuse to send", () => {
    expect(composePromptAnswer([{ name: "x", type: "text" }], {})).toBe("");
  });
});

// The handler behind "File and run" must reach the SAME answer the button used
// to decide whether to render at all. It did not: it re-derived runnability from
// a local list that omitted `development` and included `workshop`, so the button
// appeared (server said runnable) and then its own handler refused. Measured
// 2026-08-23 on a development chat: "development cannot run tickets".
describe("addTasksAndRun honours the server's pickup answer", () => {
  const ctx = (pickup: unknown) => ({
    navigate: () => {},
    contextKey: "development",
    threadId: "t1",
    rescopeThread: async () => ({}),
    pickup,
  }) as never;

  const state = (scope: string) => ({
    spec: { kind: "task-proposal", title: "x" },
    values: { scope, title: "Do the thing" },
  }) as never;

  // Both cases pick a scope where the local predicate and the server DISAGREE,
  // so the test fails if the handler goes back to deciding for itself.

  it("runs a scope the server calls runnable even where the local list would not", async () => {
    expect(scopeIsRunnable("app:zzfixture")).toBe(false); // local says no...
    const res = await UI_CARD_ACTIONS.addTasksAndRun(state("app:zzfixture"), ctx({
      scope: "app:zzfixture", runnable: true, automatic: false, why: "nothing polls it",
    })); // ...server says yes, and the server wins.
    // It gets past the guard and fails later on the network, which is fine here.
    // What must NOT happen is the pre-flight refusal.
    expect(JSON.stringify(res)).not.toContain("cannot run tickets");
  });

  it("refuses a scope the server calls not runnable even where the local list would allow it", async () => {
    expect(scopeIsRunnable("project:widgets")).toBe(true); // local says yes...
    const res = await UI_CARD_ACTIONS.addTasksAndRun(state("project:widgets"), ctx({
      scope: "project:widgets", runnable: false, automatic: false,
      why: "the autonomy brake is engaged",
    })); // ...server says no, and the server wins.
    expect(res.status).toBe("error");
    expect((res as { message: string }).message).toContain("cannot run tickets");
    expect((res as { message: string }).message).toContain("autonomy brake");
  });
});

// displayDefault is the fix for a defect the operator hit on 2026-08-23: a
// two-option prompt card showed option one as selected while state held "", so
// the action refused with "Choose or type an answer first." under an apparently
// chosen answer. The rule these pin: what a control SHOWS is what state HOLDS.
describe("displayDefault", () => {
  it("gives a choice field its first option, which is what the control draws", () => {
    expect(
      displayDefault({
        name: "ceiling",
        type: "choice",
        options: [
          { value: "keep", label: "Leave it" },
          { value: "3m", label: "Raise to 3M" },
        ],
      }),
    ).toBe("keep");
  });

  it("gives a select field its first option too, because a native select shows it", () => {
    expect(
      displayDefault({
        name: "scope",
        type: "select",
        options: [{ value: "development", label: "Development" }],
      }),
    ).toBe("development");
  });

  it("defaults a sensitivity field to shareable", () => {
    expect(displayDefault({ name: "s", type: "sensitivity" })).toBe("shareable");
  });

  it("leaves a free-text field empty, so an untouched one still reads as unanswered", () => {
    expect(displayDefault({ name: "title", type: "text" })).toBe("");
  });

  it("does not invent a value for an option field that carries no options", () => {
    expect(displayDefault({ name: "x", type: "choice", options: [] })).toBe("");
    expect(displayDefault({ name: "y", type: "select" })).toBe("");
  });

  it("composes an answer from a seeded, untouched choice, which is the whole bug", () => {
    const fields = [
      {
        name: "ceiling",
        type: "choice" as const,
        options: [
          { value: "keep", label: "Leave it, wait for the window" },
          { value: "3m", label: "Raise to 3M per 5h" },
        ],
      },
    ];
    const seeded = { ceiling: displayDefault(fields[0]) };
    expect(composePromptAnswer(fields, seeded)).toBe("Leave it, wait for the window");
  });
});
