import { describe, it, expect } from "vitest";
import {
  ACTIVE_STATUSES, BOARD_STATUSES, DEFAULT_SORT, DEFAULT_VIEW,
  apiQuery, boardLanes, chipsFor, clearField, defaultFilters, defaultStatuses,
  isDefaultStatus, isNarrowed, listGroups, parseFilters, parseSort, parseView,
  toggleValue, writeParams,
} from "./ticketFilters";
import type { TaskStatus } from "./tasks";

const fmt = { scope: (k: string) => k.replace("project:", "Project: "), status: (s: string) => s };
const q = (s: string) => new URLSearchParams(s);

describe("the default status set follows the view", () => {
  // Not a shortcut: a table opening on 108 done tickets and a board with two
  // permanently empty lanes are the same mistake from opposite ends.
  it("opens a table on active work only", () => {
    expect(defaultStatuses("table")).toEqual(ACTIVE_STATUSES);
    expect(defaultStatuses("table")).not.toContain("done");
  });

  it("opens a board on every lane it draws", () => {
    expect(defaultStatuses("board")).toEqual(BOARD_STATUSES);
    expect(defaultStatuses("board")).toContain("done");
    expect(defaultStatuses("board")).toContain("draft");
  });

  it("keeps work that has stopped out of both defaults", () => {
    for (const view of ["table", "board"] as const) {
      expect(defaultStatuses(view)).not.toContain("parked");
      expect(defaultStatuses(view)).not.toContain("cancelled");
    }
  });

  it("honours an explicit filter identically in both views", () => {
    const a = parseFilters(q("status=done"), "table");
    const b = parseFilters(q("status=done"), "board");
    expect(a.status).toEqual(["done"]);
    expect(b.status).toEqual(["done"]);
  });
});

describe("the URL is the filter state", () => {
  it("reads a plain URL as the view default", () => {
    expect(parseView(q(""))).toBe(DEFAULT_VIEW);
    expect(parseSort(q(""))).toEqual(DEFAULT_SORT);
    expect(parseFilters(q(""), "table")).toEqual(defaultFilters("table"));
  });

  it("spells every default by leaving the param off", () => {
    const p = writeParams(defaultFilters("table"), DEFAULT_SORT, "table");
    expect(p.toString()).toBe("");
  });

  it("round-trips a fully specified state", () => {
    const filters = {
      q: "oauth", status: ["todo", "blocked"] as TaskStatus[], scope: "project:birds",
      labels: ["design", "chat-reported"], effort: ["quick"],
    };
    const sort = { key: "priority", dir: "asc" } as const;
    const p = writeParams(filters, sort, "board");
    expect(parseFilters(p, "board")).toEqual(filters);
    expect(parseSort(p)).toEqual(sort);
    expect(parseView(p)).toBe("board");
  });

  it("writes 'all' for every status, so it is not read back as the default", () => {
    const cleared = clearField(defaultFilters("table"), "status");
    const p = writeParams(cleared, DEFAULT_SORT, "table");
    expect(p.get("status")).toBe("all");
    expect(parseFilters(p, "table").status).toEqual([]);
  });

  it("keeps params it does not own", () => {
    const p = writeParams(defaultFilters("table"), DEFAULT_SORT, "table", q("from=chat"));
    expect(p.get("from")).toBe("chat");
  });

  it("falls back rather than showing a list nothing can match", () => {
    // A typo and an empty backlog must not look the same.
    expect(parseFilters(q("status=nonsense"), "table").status).toEqual(ACTIVE_STATUSES);
    expect(parseView(q("view=timeline"))).toBe(DEFAULT_VIEW);
    expect(parseSort(q("sort=nonsense")).key).toBe(DEFAULT_SORT.key);
  });

  it("drops only the unknown members of a mixed status list", () => {
    expect(parseFilters(q("status=todo,nonsense"), "table").status).toEqual(["todo"]);
  });
});

describe("every filter reaches the server", () => {
  // A chip narrowed in the browser reports the count of the UNnarrowed set and
  // pages against it, so the number beside the title contradicts the rows.
  it("sends each active filter as a query param", () => {
    const p = apiQuery({
      q: "oauth", status: ["todo", "blocked"], scope: "workshop",
      labels: ["design"], effort: ["quick", "deep"],
    }, { key: "priority", dir: "asc" }, 50);
    expect(p.get("status")).toBe("todo,blocked");
    expect(p.get("scope")).toBe("workshop");
    expect(p.get("labels")).toBe("design");
    expect(p.get("effort")).toBe("quick,deep");
    expect(p.get("q")).toBe("oauth");
    expect(p.get("sort")).toBe("priority");
    expect(p.get("desc")).toBe("false");
    expect(p.get("limit")).toBe("50");
  });

  it("omits a filter that is not set, rather than sending an empty one", () => {
    const p = apiQuery({ q: "", status: [], scope: null, labels: [], effort: [] },
      DEFAULT_SORT, 50);
    expect(p.has("status")).toBe(false);
    expect(p.has("scope")).toBe(false);
    expect(p.has("labels")).toBe(false);
    expect(p.has("q")).toBe(false);
  });
});

describe("chips say what is narrowing the list", () => {
  it("shows the default as a chip, because that is the question it answers", () => {
    // "Where are the done tickets" is what a filtered list gets asked, and a
    // control that appears only after you change it cannot answer that.
    const chips = chipsFor(defaultFilters("table"), "table", fmt);
    expect(chips).toEqual([{ field: "status", label: "Status", value: "Active" }]);
    expect(chipsFor(defaultFilters("board"), "board", fmt)[0].value).toBe("On the board");
  });

  it("names a single value and counts several", () => {
    const one = chipsFor({ ...defaultFilters("table"), status: ["blocked"] }, "table", fmt);
    expect(one[0].value).toBe("blocked");
    const two = chipsFor({ ...defaultFilters("table"), status: ["blocked", "done"] }, "table", fmt);
    expect(two[0].value).toBe("· 2");
  });

  it("renders a scope through the caller's vocabulary", () => {
    const chips = chipsFor({ ...defaultFilters("table"), scope: "project:birds" }, "table", fmt);
    expect(chips.find((c) => c.field === "scope")?.value).toBe("Project: birds");
  });

  it("keeps a fixed chip order as filters come and go", () => {
    const chips = chipsFor({
      q: "", status: ["todo"], scope: "workshop", labels: ["design"], effort: ["quick"],
    }, "table", fmt);
    expect(chips.map((c) => c.field)).toEqual(["status", "scope", "tag", "effort"]);
  });

  it("drops the chip whose filter is cleared", () => {
    const f = clearField({ ...defaultFilters("table"), scope: "workshop" }, "scope");
    expect(chipsFor(f, "table", fmt).some((c) => c.field === "scope")).toBe(false);
  });

  it("clears status to EVERY status, not back to the default", () => {
    // Clearing to the default would look like the control did nothing.
    const f = clearField(defaultFilters("table"), "status");
    expect(f.status).toEqual([]);
    expect(isDefaultStatus(f, "table")).toBe(false);
  });
});

describe("narrowing is what the empty state has to explain", () => {
  it("is false for a view sitting at its own default", () => {
    expect(isNarrowed(defaultFilters("table"), "table")).toBe(false);
    expect(isNarrowed(defaultFilters("board"), "board")).toBe(false);
  });

  it("is true for any filter the operator chose", () => {
    const base = defaultFilters("table");
    expect(isNarrowed({ ...base, q: "x" }, "table")).toBe(true);
    expect(isNarrowed({ ...base, scope: "workshop" }, "table")).toBe(true);
    expect(isNarrowed({ ...base, labels: ["design"] }, "table")).toBe(true);
    expect(isNarrowed({ ...base, effort: ["quick"] }, "table")).toBe(true);
    expect(isNarrowed({ ...base, status: ["done"] }, "table")).toBe(true);
  });

  it("reads a table default as narrowed when the view is a board", () => {
    // The same set is the default for one view and a real choice in the other.
    expect(isNarrowed(defaultFilters("table"), "board")).toBe(true);
  });
});

describe("toggling a multi-value filter", () => {
  it("adds then removes", () => {
    let f = defaultFilters("table");
    f = toggleValue(f, "tag", "design");
    expect(f.labels).toEqual(["design"]);
    f = toggleValue(f, "tag", "infra");
    expect(f.labels).toEqual(["design", "infra"]);
    f = toggleValue(f, "tag", "design");
    expect(f.labels).toEqual(["infra"]);
  });

  it("touches only the field it names", () => {
    const f = toggleValue(defaultFilters("table"), "effort", "quick");
    expect(f.effort).toEqual(["quick"]);
    expect(f.status).toEqual(ACTIVE_STATUSES);
    expect(f.labels).toEqual([]);
  });
});

describe("the board never loses a ticket", () => {
  const rows = [
    { id: "a", status: "todo" }, { id: "b", status: "done" },
    { id: "c", status: "todo" }, { id: "d", status: "parked" },
    { id: "e", status: "invented-by-a-future-build" },
  ];

  it("puts every lane in pipeline order, empty ones included", () => {
    const lanes = boardLanes(rows);
    expect(lanes.slice(0, 6).map((l) => l.status)).toEqual(BOARD_STATUSES);
    expect(lanes.find((l) => l.status === "todo")?.items.map((t) => t.id)).toEqual(["a", "c"]);
    expect(lanes.find((l) => l.status === "doing")?.items).toEqual([]);
  });

  it("gives a row with no lane its own column rather than dropping it", () => {
    const extra = boardLanes(rows).slice(6).map((l) => l.status);
    expect(extra).toContain("parked");
    expect(extra).toContain("invented-by-a-future-build");
  });

  it("conserves every row", () => {
    const total = boardLanes(rows).reduce((n, l) => n + l.items.length, 0);
    expect(total).toBe(rows.length);
  });

  it("drops the empty groups on the phone list, and only those", () => {
    const groups = listGroups(rows);
    expect(groups.every((g) => g.items.length > 0)).toBe(true);
    expect(groups.reduce((n, g) => n + g.items.length, 0)).toBe(rows.length);
  });
});
