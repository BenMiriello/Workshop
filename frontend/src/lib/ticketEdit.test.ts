import { describe, expect, it } from "vitest";
import type { Task } from "./tasks";
import {
  draftFromTask,
  parseCount,
  parseList,
  ticketDirty,
  ticketPatch,
  ticketTitleMissing,
  unresolvedIds,
  type TicketDraft,
} from "./ticketEdit";

function task(over: Partial<Task> = {}): Task {
  return {
    id: "t1",
    title: "Fix the thing",
    body: "Because it is broken.",
    text: "Fix the thing\nBecause it is broken.",
    status: "todo",
    ...over,
  } as Task;
}

describe("draftFromTask", () => {
  it("reads every editable field off the record", () => {
    const d = draftFromTask(task({
      priority: 3,
      effort: "deep",
      execution: "code",
      assignee: "agent:mole",
      acceptance: { kind: "command", spec: "npm test" },
      max_attempts: 4,
      labels: ["ui", "chat"],
      depends_on: ["a1", "b2"],
      relates_to: ["c3"],
    }));
    expect(d).toEqual({
      title: "Fix the thing",
      body: "Because it is broken.",
      status: "todo",
      priority: "3",
      effort: "deep",
      execution: "code",
      assignee: "agent:mole",
      acceptanceKind: "command",
      acceptanceSpec: "npm test",
      maxAttempts: "4",
      labels: "ui, chat",
      dependsOn: "a1, b2",
      relatesTo: "c3",
    });
  });

  it("spells an absent cap as blank", () => {
    const d = draftFromTask(task());
    expect(d.maxAttempts).toBe("");
    expect(d.priority).toBe("0");
    expect(d.effort).toBe("");
    expect(d.execution).toBe("");
    expect(d.acceptanceKind).toBe("");
  });

  it("falls back to splitting text when the pair is absent", () => {
    const d = draftFromTask({
      id: "t2", status: "todo", text: "One line\nand a body.",
    } as unknown as Task);
    expect(d.title).toBe("One line");
    expect(d.body).toBe("and a body.");
  });

  it("round-trips to an empty patch", () => {
    const t = task({ priority: 2, labels: ["x"], depends_on: ["a"] });
    const d = draftFromTask(t);
    expect(ticketPatch(d, d)).toEqual({});
    expect(ticketDirty(d, d)).toBe(false);
  });
});

describe("parseList", () => {
  it("splits, trims, drops empties and duplicates, keeps order", () => {
    expect(parseList(" b2 , a1,, a1 \n c3 ")).toEqual(["b2", "a1", "c3"]);
  });

  it("reads a blank input as an empty list", () => {
    expect(parseList("")).toEqual([]);
    expect(parseList("  ,  ")).toEqual([]);
  });
});

describe("parseCount", () => {
  it("reads blank and junk as absent, not as zero", () => {
    expect(parseCount("")).toBeNull();
    expect(parseCount("   ")).toBeNull();
    expect(parseCount("many")).toBeNull();
    expect(parseCount("0")).toBe(0);
    expect(parseCount("3.7")).toBe(3);
    expect(parseCount("-2")).toBe(-2);
  });
});

describe("ticketPatch", () => {
  const base = draftFromTask(task());

  function change(over: Partial<TicketDraft>): TicketDraft {
    return { ...base, ...over };
  }

  it("sends only the fields that changed", () => {
    expect(ticketPatch(change({ priority: "5" }), base)).toEqual({ priority: 5 });
  });

  it("round-trips execution through the draft and clears it with an empty string", () => {
    expect(ticketPatch(change({ execution: "judgement" }), base))
      .toEqual({ execution: "judgement" });
    const classified = draftFromTask(task({ execution: "external" }));
    expect(classified.execution).toBe("external");
    expect(ticketPatch({ ...classified, execution: "" }, classified))
      .toEqual({ execution: "" });
    expect(ticketDirty(classified, classified)).toBe(false);
  });

  it("collapses a pasted multi-line title to one line", () => {
    expect(ticketPatch(change({ title: " Two\nlines " }), base))
      .toEqual({ title: "Two lines" });
  });

  it("clears effort and assignee with an empty string", () => {
    const filled = draftFromTask(task({ effort: "quick", assignee: "agent:mole" }));
    expect(ticketPatch(base, filled)).toEqual({ effort: "", assignee: "" });
  });

  it("carries a spec only for the command kind", () => {
    expect(ticketPatch(change({ acceptanceKind: "command", acceptanceSpec: " npm test " }), base))
      .toEqual({ acceptance: { kind: "command", spec: "npm test" } });
    expect(ticketPatch(change({ acceptanceKind: "reviewer", acceptanceSpec: "npm test" }), base))
      .toEqual({ acceptance: { kind: "reviewer", spec: null } });
  });

  it("ignores spec text typed against a kind that has no spec", () => {
    const reviewer = draftFromTask(task({ acceptance: { kind: "reviewer" } }));
    const typed = { ...reviewer, acceptanceSpec: "npm test" };
    expect(ticketPatch(typed, reviewer)).toEqual({});
  });

  it("clears the acceptance gate with an empty kind", () => {
    const gated = draftFromTask(task({ acceptance: { kind: "command", spec: "npm test" } }));
    expect(ticketPatch(change({ acceptanceKind: "", acceptanceSpec: "" }), gated))
      .toEqual({ acceptance: { kind: "", spec: null } });
  });

  it("leaves a stored attempt cap alone when the input is blank", () => {
    const capped = draftFromTask(task({ max_attempts: 4 }));
    expect(ticketPatch(change({ maxAttempts: "" }), capped)).toEqual({});
  });

  it("sends a changed attempt cap, floored at zero", () => {
    expect(ticketPatch(change({ maxAttempts: "3" }), base)).toEqual({ max_attempts: 3 });
    expect(ticketPatch(change({ maxAttempts: "-1" }), base)).toEqual({ max_attempts: 0 });
  });

  it("sends whole lists, and an empty list to clear one", () => {
    const linked = draftFromTask(task({ labels: ["ui"], depends_on: ["a1"], relates_to: ["c3"] }));
    expect(ticketPatch(base, linked)).toEqual({ labels: [], depends_on: [], relates_to: [] });
    expect(ticketPatch(change({ dependsOn: "a1, b2" }), base)).toEqual({ depends_on: ["a1", "b2"] });
  });

  it("does not call a reordered-looking list unchanged", () => {
    const linked = draftFromTask(task({ depends_on: ["a1", "b2"] }));
    expect(ticketPatch({ ...linked, dependsOn: "b2, a1" }, linked))
      .toEqual({ depends_on: ["b2", "a1"] });
  });

  it("treats cosmetic list whitespace as no change", () => {
    const linked = draftFromTask(task({ labels: ["ui", "chat"] }));
    expect(ticketPatch({ ...linked, labels: "ui,chat" }, linked)).toEqual({});
  });
});

describe("unresolvedIds", () => {
  it("names the ids the server could not resolve for this list", () => {
    expect(unresolvedIds(["a1", "b2"], [{ id: "a1" }])).toEqual(["b2"]);
    expect(unresolvedIds(["a1"], [{ id: "a1" }])).toEqual([]);
    expect(unresolvedIds(undefined, undefined)).toEqual([]);
    expect(unresolvedIds(["a1"], undefined)).toEqual(["a1"]);
  });
});

describe("ticketTitleMissing", () => {
  it("catches a blanked title before the route 400s on it", () => {
    const base = draftFromTask(task());
    expect(ticketTitleMissing(base)).toBe(false);
    expect(ticketTitleMissing({ ...base, title: "   " })).toBe(true);
  });
});

// `isReassignRefusal` lived here. It detected the store refusing a reassign,
// which happened because `tasks.update()` used `assignee` both as the field to
// write and as the owner filter that found the record, so naming a new
// assignee matched nothing and dropped the whole save with a 404. The store now
// has a write-only `set_assignee` and a reassign returns 200, so the workaround
// and the message it printed are both gone.
