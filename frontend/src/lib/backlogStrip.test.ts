import { describe, expect, it } from "vitest";
import {
  backlogCountQuery,
  backlogStripCopy,
  backlogTotal,
  showBacklogStrip,
  NO_BACKLOG,
} from "./backlogStrip";

describe("backlogCountQuery", () => {
  it("asks for the total without the rows", () => {
    expect(backlogCountQuery("draft", "all")).toBe("status=draft&limit=1");
  });

  it("counts within the scope the operator is filtered to", () => {
    expect(backlogCountQuery("parked", "development"))
      .toBe("status=parked&limit=1&scope=development");
  });

  it("treats an empty scope as no scope filter", () => {
    expect(backlogCountQuery("draft", "")).toBe("status=draft&limit=1");
  });
});

describe("showBacklogStrip", () => {
  it("is hidden when nothing is waiting", () => {
    expect(showBacklogStrip(NO_BACKLOG, "active")).toBe(false);
  });

  it("shows under the default Active view, which hides both statuses", () => {
    expect(showBacklogStrip({ draft: 2, parked: 0 }, "active")).toBe(true);
  });

  // The regression this module exists for: a narrowed view used to zero the
  // counts, so the strip vanished exactly when the work was hardest to find.
  it("still shows under an unrelated single-status filter", () => {
    expect(showBacklogStrip({ draft: 1, parked: 3 }, "doing")).toBe(true);
    expect(showBacklogStrip({ draft: 1, parked: 3 }, "done")).toBe(true);
  });

  it("hides once the operator is looking at backlog work already", () => {
    expect(showBacklogStrip({ draft: 1, parked: 3 }, "backlog")).toBe(false);
    expect(showBacklogStrip({ draft: 1, parked: 0 }, "draft")).toBe(false);
    expect(showBacklogStrip({ draft: 0, parked: 3 }, "parked")).toBe(false);
  });
});

describe("backlogStripCopy", () => {
  it("names only the statuses that are present", () => {
    expect(backlogStripCopy({ draft: 2, parked: 0 }).what)
      .toBe("2 proposed tickets are waiting for you");
    expect(backlogStripCopy({ draft: 0, parked: 1 }).what)
      .toBe("1 parked ticket is waiting for you");
    expect(backlogStripCopy({ draft: 1, parked: 2 }).what)
      .toBe("1 proposed and 2 parked tickets are waiting for you");
  });

  it("explains the reason that applies to what is there", () => {
    expect(backlogStripCopy({ draft: 2, parked: 0 }).why)
      .toBe("Nothing runs them until you approve.");
    expect(backlogStripCopy({ draft: 0, parked: 2 }).why)
      .toBe("Parked tickets used up their attempts. Nothing retries them on its own.");
    expect(backlogStripCopy({ draft: 1, parked: 2 }).why)
      .toBe("Nothing runs them until you approve or requeue them.");
  });

  it("agrees in number with the verb", () => {
    expect(backlogStripCopy({ draft: 1, parked: 0 }).verb).toBe("Review it");
    expect(backlogStripCopy({ draft: 1, parked: 1 }).verb).toBe("Review them");
  });

  it("has no em-dash in any string the operator reads", () => {
    const copy = backlogStripCopy({ draft: 1, parked: 2 });
    expect(`${copy.what} ${copy.why} ${copy.verb}`).not.toContain("—");
  });
});

describe("backlogTotal", () => {
  it("adds the two statuses", () => {
    expect(backlogTotal({ draft: 3, parked: 4 })).toBe(7);
    expect(backlogTotal(NO_BACKLOG)).toBe(0);
  });
});
