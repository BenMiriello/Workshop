// The pure rules in lib/tasks: the readiness line, and the pre-filing gap
// check the chat's file-a-ticket card runs against its own fields.
//
// The `why` strings below are the checker's, verbatim from
// backend/services/ticket_readiness.py. That is the point of these tests: the
// line is DERIVED from those strings, so a test that made up its own wording
// would prove nothing.
import { describe, expect, it } from "vitest";
import { executionLabel, proposalGaps, readinessLine } from "./tasks";

const WHY = {
  title: "the ticket has no title",
  objective: "the body does not say what this is trying to achieve",
  acceptance: "the ticket does not say what done looks like",
  entry_points: "the body names no file, command or symbol to start from",
  boundaries: "the ticket does not say which files it may change",
};

const gap = (check: keyof typeof WHY) => ({ check, why: WHY[check] });

describe("readinessLine", () => {
  it("is empty for a ready ticket", () => {
    expect(readinessLine([])).toBe("");
    expect(readinessLine(undefined)).toBe("");
    expect(readinessLine(null)).toBe("");
  });

  it("says one gap as one sentence", () => {
    expect(readinessLine([gap("acceptance")]))
      .toBe("Does not say what done looks like.");
  });

  it("keeps the subject off a gap that does not open with one", () => {
    expect(readinessLine([gap("title")])).toBe("Has no title.");
  });

  it("joins two gaps without repeating the opener", () => {
    expect(readinessLine([gap("acceptance"), gap("boundaries")]))
      .toBe("Does not say what done looks like, or which files it may change.");
  });

  it("keeps the second clause whole when the two do not share an opener", () => {
    expect(readinessLine([gap("title"), gap("acceptance")]))
      .toBe("Has no title, or does not say what done looks like.");
  });

  it("lists three gaps as things rather than as sentences", () => {
    expect(readinessLine([gap("acceptance"), gap("boundaries"), gap("entry_points")]))
      .toBe("Three things are unspecified: what done looks like, "
        + "which files it may change, and no file, command or symbol to start from.");
  });

  it("counts the tail past two gaps", () => {
    expect(readinessLine([
      gap("acceptance"), gap("boundaries"), gap("entry_points"), gap("objective"),
    ])).toBe("Does not say what done looks like, or which files it may change, and 2 more.");
  });

  it("counts every gap past the first two, not just the fourth", () => {
    expect(readinessLine([
      gap("acceptance"), gap("boundaries"), gap("entry_points"),
      gap("objective"), gap("title"),
    ])).toBe("Does not say what done looks like, or which files it may change, and 3 more.");
  });

  it("ignores a gap with no reason rather than emitting a bare period", () => {
    expect(readinessLine([{ check: "mystery", why: "  " }])).toBe("");
    expect(readinessLine([{ check: "mystery", why: "" }, gap("acceptance")]))
      .toBe("Does not say what done looks like.");
  });

  it("passes through a why it has no subject or verb rule for", () => {
    expect(readinessLine([{ check: "x", why: "nobody owns this ticket" }]))
      .toBe("Nobody owns this ticket.");
  });
});

describe("proposalGaps", () => {
  it("finds nothing on a proposal that fills every section", () => {
    expect(proposalGaps({
      title: "Fix the thing",
      objective: "The thing stops dropping messages under load.",
      output: "The reproduction script exits 0.",
      entry_points: "backend/services/chat.py",
      boundaries: "Nothing under frontend/.",
    })).toEqual([]);
  });

  it("names each empty section with the checker's own wording", () => {
    const gaps = proposalGaps({ title: "Fix the thing" });
    expect(gaps.map((g) => g.check))
      .toEqual(["objective", "acceptance", "entry_points", "boundaries"]);
    expect(gaps[1].why).toBe(WHY.acceptance);
  });

  it("reports a missing title", () => {
    expect(proposalGaps({}).map((g) => g.check)).toContain("title");
  });

  it("accepts a long body in place of a stated objective", () => {
    const long = "The queue keeps handing the same ticket back out again and again.";
    expect(proposalGaps({ title: "t", objective: "", output: long })
      .map((g) => g.check)).not.toContain("objective");
  });

  it("does not ask a quick ticket where to start", () => {
    expect(proposalGaps({ title: "t", effort: "quick" }).map((g) => g.check))
      .not.toContain("entry_points");
  });

  it("takes a path named anywhere in the proposal as somewhere to start", () => {
    expect(proposalGaps({
      title: "t",
      objective: "Rewrite backend/services/tasks.py so the sweep stops guessing.",
    }).map((g) => g.check)).not.toContain("entry_points");
  });

  it("reads the unstructured fallback body", () => {
    const gaps = proposalGaps({ text: "Fix the thing\nIt is broken in `tasks.py`." });
    expect(gaps.map((g) => g.check)).not.toContain("title");
    expect(gaps.map((g) => g.check)).not.toContain("entry_points");
  });

  it("produces gaps the line can render", () => {
    expect(readinessLine(proposalGaps({
      title: "Fix the thing",
      objective: "The thing stops dropping messages.",
      output: "The reproduction script exits 0.",
      entry_points: "backend/services/chat.py",
    }))).toBe("Does not say which files it may change.");
  });
});

describe("executionLabel", () => {
  it("labels the three classified values", () => {
    expect(executionLabel("code")).toBe("code");
    expect(executionLabel("judgement")).toBe("judgement");
    expect(executionLabel("external")).toBe("external");
  });

  it("reads an unset or unknown value as unclassified, never as code", () => {
    expect(executionLabel(null)).toBe("not classified");
    expect(executionLabel(undefined)).toBe("not classified");
    expect(executionLabel("")).toBe("not classified");
    expect(executionLabel("codeish")).toBe("not classified");
  });
});
