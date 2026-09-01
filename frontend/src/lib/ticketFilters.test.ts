import { describe, it, expect } from "vitest";
import {
  ACTIVE_STATUSES, BOARD_STATUSES, DEFAULT_SORT, DEFAULT_VIEW, PAGE_SIZE,
  apiQuery, boardLanes, chipsFor, clampPage, clearField, defaultFilters,
  defaultStatuses, isDefaultStatus, isNarrowed, laneQuery, listGroups, pageCount,
  pageItems, parseFilters, parsePage, parseSort, parseView, toggleValue, writeParams,
} from "./ticketFilters";
import type { TaskStatus } from "./tasks";

const fmt = {
  scope: (k: string) => k.replace("project:", "Project: "),
  status: (s: string) => s,
  source: (s: string) => s,
};
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
      labels: ["design", "chat-reported"], effort: ["quick"], assignee: ["agent:builder"],
      source: ["operator", "chat"],
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
      labels: ["design"], effort: ["quick", "deep"], assignee: ["operator"],
      source: ["operator", "chat"],
    }, { key: "priority", dir: "asc" }, 50);
    expect(p.get("status")).toBe("todo,blocked");
    expect(p.get("scope")).toBe("workshop");
    expect(p.get("labels")).toBe("design");
    expect(p.get("effort")).toBe("quick,deep");
    expect(p.get("assignee")).toBe("operator");
    expect(p.get("source")).toBe("operator,chat");
    expect(p.get("q")).toBe("oauth");
    expect(p.get("sort")).toBe("priority");
    expect(p.get("desc")).toBe("false");
    expect(p.get("limit")).toBe("50");
  });

  it("omits a filter that is not set, rather than sending an empty one", () => {
    const p = apiQuery(
      { q: "", status: [], scope: null, labels: [], effort: [], assignee: [], source: [] },
      DEFAULT_SORT, 50);
    expect(p.has("status")).toBe(false);
    expect(p.has("scope")).toBe(false);
    expect(p.has("labels")).toBe(false);
    expect(p.has("q")).toBe(false);
    expect(p.has("source")).toBe(false);
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
      q: "", status: ["todo"], scope: "workshop", labels: ["design"],
      effort: ["quick"], assignee: ["operator"], source: ["agent"],
    }, "table", fmt);
    expect(chips.map((c) => c.field))
      .toEqual(["status", "source", "scope", "tag", "effort", "assignee"]);
  });

  it("shows no scope chip on a surface that IS one scope", () => {
    // The app Tickets tab mounts this list pinned to the app. A chip there
    // carries a clear button, and clearing it would ask for a list the surface
    // cannot show.
    const f = { ...defaultFilters("table"), scope: "project:birds" };
    expect(chipsFor(f, "table", fmt, { lockedScope: true })
      .some((c) => c.field === "scope")).toBe(false);
    // Every other chip is unaffected.
    expect(chipsFor({ ...f, source: ["agent"] }, "table", fmt, { lockedScope: true })
      .map((c) => c.field)).toEqual(["status", "source"]);
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
    expect(isNarrowed({ ...base, assignee: ["operator"] }, "table")).toBe(true);
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

// The list must not answer "how many tickets are there" by loading all of them.
describe("paging asks for one page", () => {
  it("reads page 1 from a URL that does not say", () => {
    expect(parsePage(q(""))).toBe(1);
    expect(parsePage(q("page=0"))).toBe(1);
    expect(parsePage(q("page=nonsense"))).toBe(1);
    expect(parsePage(q("page=4"))).toBe(4);
  });

  it("leaves page 1 out of the URL and writes any other", () => {
    const f = defaultFilters("table");
    expect(writeParams(f, DEFAULT_SORT, "table", undefined, 1).toString()).toBe("");
    expect(writeParams(f, DEFAULT_SORT, "table", undefined, 3).get("page")).toBe("3");
  });

  it("sends an offset for later pages and none for the first", () => {
    const f = defaultFilters("table");
    expect(apiQuery(f, DEFAULT_SORT, PAGE_SIZE, 0).has("offset")).toBe(false);
    expect(apiQuery(f, DEFAULT_SORT, PAGE_SIZE, 100).get("offset")).toBe("100");
    expect(apiQuery(f, DEFAULT_SORT, PAGE_SIZE).get("limit")).toBe(String(PAGE_SIZE));
  });

  it("counts pages from the total, never fewer than one", () => {
    expect(pageCount(0)).toBe(1);
    expect(pageCount(50)).toBe(1);
    expect(pageCount(51)).toBe(2);
    expect(pageCount(5000)).toBe(100);
  });

  it("clamps a page that a narrowed filter left stranded", () => {
    // Sitting on page 9 and then filtering down to 12 rows must not show an
    // empty page with no way back.
    expect(clampPage(9, 12)).toBe(1);
    expect(clampPage(3, 5000)).toBe(3);
    expect(clampPage(0, 5000)).toBe(1);
  });
});

// A board is N paged lists, not one paged list drawn in columns.
describe("the board asks per lane", () => {
  it("pins the query to exactly one status", () => {
    const p = laneQuery(defaultFilters("board"), DEFAULT_SORT, "blocked", 25);
    expect(p.get("status")).toBe("blocked");
    expect(p.get("limit")).toBe("25");
  });

  it("keeps every other filter on the lane", () => {
    const f = { ...defaultFilters("board"), scope: "workshop", labels: ["design"], q: "oauth" };
    const p = laneQuery(f, DEFAULT_SORT, "todo", 25);
    expect(p.get("scope")).toBe("workshop");
    expect(p.get("labels")).toBe("design");
    expect(p.get("q")).toBe("oauth");
  });

  it("never asks one query to fill every lane", () => {
    // The failure this replaces: one `limit: 300` query on a large queue comes
    // back as 300 done tickets, so the lanes that matter render empty while the
    // data says they are full.
    for (const status of BOARD_STATUSES) {
      expect(laneQuery(defaultFilters("board"), DEFAULT_SORT, status, 25).get("status"))
        .toBe(status);
    }
  });
});

// One control at every size, so the windowing is a rule and not a set of cases.
describe("the numbered pager", () => {
  const nums = (items: ReturnType<typeof pageItems>) => items.filter((i) => i !== "gap");

  it("draws every page while they fit", () => {
    expect(pageItems(1, 1)).toEqual([1]);
    expect(pageItems(2, 3)).toEqual([1, 2, 3]);
    expect(pageItems(3, 5)).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps both ends and a window around here", () => {
    const items = pageItems(8, 42);
    expect(items[0]).toBe(1);
    expect(items[items.length - 1]).toBe(42);
    for (const p of [6, 7, 8, 9, 10]) expect(items).toContain(p);
  });

  it("elides a RANGE, never a single page", () => {
    // 8 pages at page 4: the window reaches 6, and the only thing between 6 and
    // the last page is 7. A gap hiding one page costs the same width as the
    // page and removes a destination, so 7 is drawn instead.
    expect(pageItems(4, 8)).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    // 20 pages at page 3: the hole is 6..19, which is a real range.
    expect(pageItems(3, 20)).toEqual([1, 2, 3, 4, 5, "gap", 20]);
    expect(pageItems(1, 5)).toEqual([1, 2, 3, 4, 5]);
  });

  it("never repeats a page and stays in order", () => {
    for (const [here, pages] of [[1, 40], [20, 40], [40, 40], [2, 9], [7, 7]]) {
      const ns = nums(pageItems(here, pages)) as number[];
      expect(new Set(ns).size).toBe(ns.length);
      expect([...ns].sort((a, b) => a - b)).toEqual(ns);
      expect(ns[0]).toBe(1);
      expect(ns[ns.length - 1]).toBe(pages);
    }
  });

  it("clamps a current page outside the range rather than drawing it", () => {
    expect(pageItems(99, 3)).toEqual([1, 2, 3]);
    expect(pageItems(0, 3)).toEqual([1, 2, 3]);
  });
});
