// The pure rules in lib/tasks: the readiness line, and the pre-filing gap
// check the chat's file-a-ticket card runs against its own fields.
//
// The `why` strings below are the checker's, verbatim from
// backend/services/ticket_readiness.py. That is the point of these tests: the
// line is DERIVED from those strings, so a test that made up its own wording
// would prove nothing.
import { describe, expect, it } from "vitest";
import {
  executionLabel, proposalGaps, readinessBlockers, readinessLine,
  readinessTone, triageLabel,
} from "./tasks";

const WHY = {
  title: "the ticket has no title",
  objective: "the body does not say what this is trying to achieve",
  acceptance: "the ticket does not say what done looks like",
  entry_points: "the body does not say where to start",
  boundaries: "the ticket does not say which files it may change",
  dependencies: "it depends on a ticket that does not exist: T-4821",
};

// The order `ticket_readiness.check` ACTUALLY appends gaps in. The first version
// of this file passed gaps in a hand-picked order that the checker never emits,
// which is why it never saw the objective+acceptance defect: those two share
// four opening words and were the pair most likely to be produced together.
const EMIT_ORDER: (keyof typeof WHY)[] = [
  "title", "objective", "acceptance", "entry_points", "boundaries", "dependencies",
];

const gap = (check: keyof typeof WHY) => ({ check, why: WHY[check] });

// Build a gap list the way the checker would, whatever order the caller lists.
const gaps = (...checks: (keyof typeof WHY)[]) =>
  EMIT_ORDER.filter((c) => checks.includes(c)).map(gap);

describe("readinessLine", () => {
  it("is empty for a ready ticket", () => {
    expect(readinessLine([])).toBe("");
    expect(readinessLine(undefined)).toBe("");
    expect(readinessLine(null)).toBe("");
  });

  it("says one gap as one sentence", () => {
    expect(readinessLine(gaps("acceptance")))
      .toBe("Does not say what done looks like.");
  });

  it("joins two gaps without repeating the opener", () => {
    expect(readinessLine(gaps("acceptance", "boundaries")))
      .toBe("Does not say what done looks like, or which files it may change.");
  });

  it("drops only the shared VERB, never a shared object clause", () => {
    // THE REGRESSION. objective and acceptance share four opening words
    // ("does not say what"), and the old longest-common-prefix rule cut the
    // second to "done looks like", producing "Does not say what this is trying
    // to achieve, or done looks like." They are the pair the checker emits most
    // often, and the first sentence a task-proposal card renders.
    expect(readinessLine(gaps("objective", "acceptance")))
      .toBe("Does not say what this is trying to achieve, "
        + "or what done looks like.");
  });

  it("lists three gaps as things rather than as sentences", () => {
    expect(readinessLine(gaps("acceptance", "entry_points", "boundaries")))
      .toBe("Three things are unspecified: what done looks like, "
        + "where to start, and which files it may change.");
  });

  it("counts the tail past two gaps", () => {
    expect(readinessLine(gaps("objective", "acceptance", "entry_points", "boundaries")))
      .toBe("Does not say what this is trying to achieve, "
        + "or what done looks like, and 2 more.");
  });
});

describe("readinessLine, blocking gaps", () => {
  // `title` and `dependencies` are the only two checks that REFUSE a run
  // (ticket_readiness.BLOCKING; POST /run returns 400 on them and nothing else).
  // They lead, they say they block, and they keep their own subject: the
  // dependencies why has no strippable verb, so listing it as a noun phrase
  // produced "Three things are unspecified: ... and depends on a ticket that
  // does not exist: T-4821."
  it("leads with a missing title and says it blocks", () => {
    expect(readinessLine(gaps("title")))
      .toBe("Cannot run yet: the ticket has no title.");
  });

  it("keeps the dependency id readable instead of listing it as a thing", () => {
    expect(readinessLine(gaps("dependencies")))
      .toBe("Cannot run yet: it depends on a ticket that does not exist: T-4821.");
  });

  it("counts the advisory gaps after the blocking sentence", () => {
    expect(readinessLine(gaps("acceptance", "boundaries", "dependencies")))
      .toBe("Cannot run yet: it depends on a ticket that does not exist: T-4821."
        + " 2 other things unspecified.");
    expect(readinessLine(gaps("acceptance", "dependencies")))
      .toBe("Cannot run yet: it depends on a ticket that does not exist: T-4821."
        + " 1 other thing unspecified.");
  });

  it("reports the tone the caller should render", () => {
    expect(readinessTone(gaps("acceptance", "boundaries"))).toBe("warn");
    expect(readinessTone(gaps("dependencies"))).toBe("error");
    expect(readinessTone(gaps("title", "acceptance"))).toBe("error");
    expect(readinessTone([])).toBe("warn");
  });

  it("names which gaps actually block", () => {
    expect(readinessBlockers(gaps("acceptance", "boundaries"))).toEqual([]);
    expect(readinessBlockers(gaps("title", "acceptance")).map((g) => g.check))
      .toEqual(["title"]);
  });
});

describe("readinessLine, edges", () => {
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

  it("reads the sections out of one description", () => {
    expect(proposalGaps({
      title: "Fix the thing",
      description: [
        "Objective: the thing stops dropping messages under load.",
        "Output: the reproduction script exits 0.",
        "Start from: backend/services/chat.py",
        "Boundaries: nothing under frontend/.",
      ].join("\n"),
    })).toEqual([]);
  });

  it("takes the labels a model actually writes", () => {
    expect(proposalGaps({
      title: "Fix the thing",
      description: [
        "**Objective:** the thing stops dropping messages under load.",
        "## Done when - the reproduction script exits 0.",
        "Begin with: backend/services/chat.py",
        "Out of scope: anything under frontend/.",
      ].join("\n"),
    })).toEqual([]);
  });

  it("names the sections a description leaves out", () => {
    const gaps = proposalGaps({
      title: "Fix the thing",
      description: "Objective: the thing stops dropping messages under load.",
    });
    expect(gaps.map((g) => g.check))
      .toEqual(["acceptance", "entry_points", "boundaries"]);
  });

  it("does not read a section label out of prose that merely mentions it", () => {
    expect(proposalGaps({
      title: "Fix the thing",
      description: "Objective: keep going until the output is correct.",
    }).map((g) => g.check)).toContain("acceptance");
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

describe("triageLabel", () => {
  // Phrased as an outcome, not as a verdict word: "ok" alone reads as a status
  // the ticket does not have.
  it("says what the pass did, not just what it decided", () => {
    expect(triageLabel("ok")).toBe("reviewed, specified well enough");
    expect(triageLabel("split")).toBe("reviewed, proposed splitting it");
    expect(triageLabel("underspecified")).toBe("reviewed, asked a question");
    expect(triageLabel("none")).toBe("reviewed, nothing to do");
  });

  it("still says it was reviewed for a decision it does not know", () => {
    // The store degrades an unknown decision to `none` rather than dropping the
    // mark, so the UI must not render an empty row for one either.
    expect(triageLabel("something-new")).toBe("reviewed");
    expect(triageLabel(null)).toBe("reviewed");
    expect(triageLabel(undefined)).toBe("reviewed");
  });
});
