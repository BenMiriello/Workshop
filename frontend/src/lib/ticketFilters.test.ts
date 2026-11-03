import { describe, it, expect } from "vitest";
import {
  ACTIVE_STATUSES, BOARD_STATUSES, DEFAULT_SORT, DEFAULT_SOURCES, DEFAULT_VIEW,
  PAGE_SIZE,
  SOURCE_PRESETS, STATUS_PRESETS, apiQuery, chipsFor, clampPage, clearField,
  defaultFilters,
  defaultStatuses, isDefaultSource, isDefaultStatus, isNarrowed, laneQuery,
  pageCount,
  pageItems, parseFilters, parsePage, parseSort, parseView, sourcePresetOf,
  sourcesForPreset, statusPresetOf,
  statusesForPreset, toggleValue, writeParams,
} from "./ticketFilters";
import type { TaskStatus } from "./tasks";

const fmt = {
  scope: (k: string) => k.replace("project:", "Project: "),
  status: (s: string) => s,
  source: (s: string) => s,
};
const q = (s: string) => new URLSearchParams(s);

describe("the default status set follows the view", () => {
  // Not a shortcut: a list opening on 108 done tickets and a board with two
  // permanently empty lanes are the same mistake from opposite ends.
  it("opens a list on active work only", () => {
    expect(defaultStatuses("list")).toEqual(ACTIVE_STATUSES);
    expect(defaultStatuses("list")).not.toContain("done");
  });

  it("leaves the board's lanes to the board", () => {
    // The empty set is "every status". The board narrows to its own six lanes
    // itself, so restating them here would be a second place they are decided,
    // and it made the board's default match no preset on the status bar.
    expect(defaultStatuses("board")).toEqual([]);
    expect(statusPresetOf(defaultStatuses("board"))).toBe("all");
  });

  it("keeps work that has stopped out of the list default", () => {
    expect(defaultStatuses("list")).not.toContain("parked");
    expect(defaultStatuses("list")).not.toContain("cancelled");
  });

  it("honours an explicit filter identically in both views", () => {
    const a = parseFilters(q("status=done"), "list");
    const b = parseFilters(q("status=done"), "board");
    expect(a.status).toEqual(["done"]);
    expect(b.status).toEqual(["done"]);
  });

  it("resolves the retired table view to the list rather than blanking", () => {
    // Same fallback that retired `?view=timeline`. Old links and bookmarks
    // must not land on a page with no body.
    expect(parseView(q("view=table"))).toBe("list");
    expect(parseView(q("view=timeline"))).toBe("list");
    expect(DEFAULT_VIEW).toBe("list");
  });
});

describe("the status bar is the status control", () => {
  it("offers the five presets and no sixth vocabulary", () => {
    expect(STATUS_PRESETS.map((p) => p.value))
      .toEqual(["active", "blocked", "backlog", "completed", "all"]);
  });

  it("recognises each preset from the status set it produces", () => {
    for (const p of STATUS_PRESETS) {
      expect(statusPresetOf(statusesForPreset(p.value))).toBe(p.value);
    }
  });

  it("spells All as every status, which is the empty set", () => {
    expect(statusesForPreset("all")).toEqual([]);
    expect(statusPresetOf([])).toBe("all");
  });

  it("matches a preset whatever order the statuses arrive in", () => {
    // A `?status=` param is whatever someone typed or a chip left behind.
    const shuffled = [...ACTIVE_STATUSES].reverse();
    expect(statusPresetOf(shuffled)).toBe("active");
  });

  it("says a hand-picked set is no preset, so nothing on the bar lights", () => {
    expect(statusPresetOf(["doing", "in_review"])).toBe(null);
  });
});

describe("the URL is the filter state", () => {
  it("reads a plain URL as the view default", () => {
    expect(parseView(q(""))).toBe(DEFAULT_VIEW);
    expect(parseSort(q(""))).toEqual(DEFAULT_SORT);
    expect(parseFilters(q(""), "list")).toEqual(defaultFilters("list"));
  });

  it("spells every default by leaving the param off", () => {
    const p = writeParams(defaultFilters("list"), DEFAULT_SORT, "list");
    expect(p.toString()).toBe("");
  });

  it("round-trips a fully specified state", () => {
    const filters = {
      q: "oauth", status: ["todo", "blocked"] as TaskStatus[], scope: "project:widgets",
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
    const cleared = clearField(defaultFilters("list"), "status");
    const p = writeParams(cleared, DEFAULT_SORT, "list");
    expect(p.get("status")).toBe("all");
    expect(parseFilters(p, "list").status).toEqual([]);
  });

  it("keeps params it does not own", () => {
    const p = writeParams(defaultFilters("list"), DEFAULT_SORT, "list", q("from=chat"));
    expect(p.get("from")).toBe("chat");
  });

  it("falls back rather than showing a list nothing can match", () => {
    // A typo and an empty backlog must not look the same.
    expect(parseFilters(q("status=nonsense"), "list").status).toEqual(ACTIVE_STATUSES);
    expect(parseView(q("view=timeline"))).toBe(DEFAULT_VIEW);
    expect(parseSort(q("sort=nonsense")).key).toBe(DEFAULT_SORT.key);
  });

  it("drops only the unknown members of a mixed status list", () => {
    expect(parseFilters(q("status=todo,nonsense"), "list").status).toEqual(["todo"]);
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
  it("spends no chip on a status the bar is already showing", () => {
    // The bar is on screen at both widths with the preset pressed. A chip
    // repeating it is the same fact twice, which is what stops either being
    // scannable.
    expect(chipsFor(defaultFilters("list"), fmt)).toEqual([]);
    expect(chipsFor(defaultFilters("board"), fmt)).toEqual([]);
    for (const p of STATUS_PRESETS) {
      const f = { ...defaultFilters("list"), status: statusesForPreset(p.value) };
      expect(chipsFor(f, fmt).some((c) => c.field === "status")).toBe(false);
    }
  });

  it("gives a hand-picked set a chip, because nothing else can clear it", () => {
    // `?status=doing,in_review` matches no preset, so the bar shows nothing
    // pressed and the chip is the only way back.
    const one = chipsFor({ ...defaultFilters("list"), status: ["blocked", "done"] }, fmt);
    expect(one[0]).toEqual({ field: "status", label: "Status", value: "· 2" });
    const solo = chipsFor({ ...defaultFilters("list"), status: ["doing"] }, fmt);
    expect(solo[0]).toEqual({ field: "status", label: "Status", value: "doing" });
  });

  it("renders a scope through the caller's vocabulary", () => {
    const chips = chipsFor({ ...defaultFilters("list"), scope: "project:widgets" }, fmt);
    expect(chips.find((c) => c.field === "scope")?.value).toBe("Project: widgets");
  });

  it("keeps a fixed chip order as filters come and go", () => {
    const chips = chipsFor({
      q: "", status: ["todo"], scope: "workshop", labels: ["design"],
      effort: ["quick"], assignee: ["operator"], source: ["agent"],
    }, fmt);
    expect(chips.map((c) => c.field))
      .toEqual(["status", "source", "scope", "tag", "effort", "assignee"]);
  });

  it("shows no scope chip on a surface that IS one scope", () => {
    // The app Tickets tab mounts this list pinned to the app. A chip there
    // carries a clear button, and clearing it would ask for a list the surface
    // cannot show.
    const f = { ...defaultFilters("list"), scope: "project:widgets" };
    expect(chipsFor(f, fmt, { lockedScope: true })
      .some((c) => c.field === "scope")).toBe(false);
    // Every other chip is unaffected.
    expect(chipsFor({ ...f, source: ["agent"] }, fmt, { lockedScope: true })
      .map((c) => c.field)).toEqual(["source"]);
  });

  it("drops the chip whose filter is cleared", () => {
    const f = clearField({ ...defaultFilters("list"), scope: "workshop" }, "scope");
    expect(chipsFor(f, fmt).some((c) => c.field === "scope")).toBe(false);
  });

  it("clears status to EVERY status, not back to the default", () => {
    // Clearing to the default would look like the control did nothing, and the
    // set it lands on is the All preset, so the bar lights up on it.
    const f = clearField(defaultFilters("list"), "status");
    expect(f.status).toEqual([]);
    expect(isDefaultStatus(f, "list")).toBe(false);
    expect(statusPresetOf(f.status)).toBe("all");
  });
});

describe("narrowing is what the empty state has to explain", () => {
  it("is false for a view sitting at its own default", () => {
    expect(isNarrowed(defaultFilters("list"), "list")).toBe(false);
    expect(isNarrowed(defaultFilters("board"), "board")).toBe(false);
  });

  it("is true for any filter the operator chose", () => {
    const base = defaultFilters("list");
    expect(isNarrowed({ ...base, q: "x" }, "list")).toBe(true);
    expect(isNarrowed({ ...base, scope: "workshop" }, "list")).toBe(true);
    expect(isNarrowed({ ...base, labels: ["design"] }, "list")).toBe(true);
    expect(isNarrowed({ ...base, effort: ["quick"] }, "list")).toBe(true);
    expect(isNarrowed({ ...base, assignee: ["operator"] }, "list")).toBe(true);
    expect(isNarrowed({ ...base, status: ["done"] }, "list")).toBe(true);
    expect(isNarrowed({ ...base, source: ["agent"] }, "list")).toBe(true);
    expect(isNarrowed({ ...base, source: [] }, "list")).toBe(true);
  });

  it("does not count the DEFAULT source set as narrowing", () => {
    // The page opens on it. Counting it would make every fresh load read as
    // filtered and hand the empty state a Clear button that clears to the state
    // it is already in. `TicketsView` draws that case separately.
    expect(isNarrowed({ ...defaultFilters("list"), source: [...DEFAULT_SOURCES] },
                      "list")).toBe(false);
  });

  it("reads a list default as narrowed when the view is a board", () => {
    // The same set is the default for one view and a real choice in the other.
    expect(isNarrowed(defaultFilters("list"), "board")).toBe(true);
  });
});

describe("the list opens on what the operator asked for", () => {
  // 215 live tickets on 2026-09-04, 140 of them filed by an agent. An
  // unfiltered list is two thirds work nobody asked for, which is the whole
  // finding in WHY-THE-WORK-DOES-NOT-LAND-2026-09-01 section 1.
  it("defaults to the operator's own two doors", () => {
    expect(DEFAULT_SOURCES).toEqual(["operator", "chat"]);
    expect(defaultFilters("list").source).toEqual(["operator", "chat"]);
    expect(defaultFilters("board").source).toEqual(["operator", "chat"]);
    expect(sourcePresetOf(defaultFilters("list").source)).toBe("you");
  });

  it("does not hand the default array out to be mutated", () => {
    const f = defaultFilters("list");
    f.source.push("agent");
    expect(DEFAULT_SOURCES).toEqual(["operator", "chat"]);
    expect(defaultFilters("list").source).toEqual(["operator", "chat"]);
  });

  it("covers both machine doors with one pill and neither with anywhere", () => {
    // `unknown` is in no group but the widest one: a record nobody wrote a
    // source for must not be counted as anyone's, which is why that value
    // exists at all.
    expect(sourcesForPreset("agents")).toEqual(["agent", "autogen"]);
    expect(sourcesForPreset("anywhere")).toEqual([]);
    expect(SOURCE_PRESETS.map((p) => p.value)).toEqual(["you", "agents", "anywhere"]);
    expect(SOURCE_PRESETS.flatMap((p) => p.sources)).not.toContain("unknown");
  });

  it("spells its default by leaving the param off, and 'every' by a word", () => {
    // Exactly how status is spelled, and for the same reason: an empty param
    // would be indistinguishable from an absent one, and absent means default.
    expect(writeParams(defaultFilters("list"), DEFAULT_SORT, "list").has("source"))
      .toBe(false);
    const every = writeParams(
      { ...defaultFilters("list"), source: [] }, DEFAULT_SORT, "list");
    expect(every.get("source")).toBe("all");
    expect(parseFilters(every, "list").source).toEqual([]);
  });

  it("round-trips a hand-picked source and reads it back as itself", () => {
    const p = writeParams(
      { ...defaultFilters("list"), source: ["chat"] }, DEFAULT_SORT, "list");
    expect(p.get("source")).toBe("chat");
    expect(parseFilters(p, "list").source).toEqual(["chat"]);
    expect(sourcePresetOf(["chat"])).toBe(null);
  });

  it("falls back to the default rather than showing a list nothing can match", () => {
    expect(parseFilters(q("source=nonsense"), "list").source).toEqual([...DEFAULT_SOURCES]);
    expect(parseFilters(q("source=agent,nonsense"), "list").source).toEqual(["agent"]);
  });

  it("is order-independent about which set is the default", () => {
    expect(isDefaultSource({ ...defaultFilters("list"), source: ["chat", "operator"] }))
      .toBe(true);
    expect(isDefaultSource({ ...defaultFilters("list"), source: ["operator"] }))
      .toBe(false);
  });

  it("pushes the source down to the server", () => {
    // A source narrowed in the browser would report the count of the unnarrowed
    // set and page against it, so the number on the pill would contradict the
    // rows under it.
    expect(apiQuery(defaultFilters("list"), DEFAULT_SORT, 50).get("source"))
      .toBe("operator,chat");
    expect(apiQuery({ ...defaultFilters("list"), source: [] }, DEFAULT_SORT, 50)
      .has("source")).toBe(false);
  });

  it("spends no chip on a source the bar is already showing", () => {
    for (const p of SOURCE_PRESETS) {
      const f = { ...defaultFilters("list"), source: p.sources };
      expect(chipsFor(f, fmt).filter((c) => c.field === "source")).toEqual([]);
    }
    const picked = chipsFor({ ...defaultFilters("list"), source: ["chat"] }, fmt);
    expect(picked.map((c) => c.field)).toEqual(["source"]);
    expect(picked[0].value).toBe("chat");
  });

  it("clears a source chip to every source, not back to the default", () => {
    // Clearing to a default that has its own pressed pill would read as the
    // control doing nothing.
    const f = clearField({ ...defaultFilters("list"), source: ["chat"] }, "source");
    expect(f.source).toEqual([]);
    expect(sourcePresetOf(f.source)).toBe("anywhere");
  });
});

describe("toggling a multi-value filter", () => {
  it("adds then removes", () => {
    let f = defaultFilters("list");
    f = toggleValue(f, "tag", "design");
    expect(f.labels).toEqual(["design"]);
    f = toggleValue(f, "tag", "infra");
    expect(f.labels).toEqual(["design", "infra"]);
    f = toggleValue(f, "tag", "design");
    expect(f.labels).toEqual(["infra"]);
  });

  it("touches only the field it names", () => {
    const f = toggleValue(defaultFilters("list"), "effort", "quick");
    expect(f.effort).toEqual(["quick"]);
    expect(f.status).toEqual(ACTIVE_STATUSES);
    expect(f.labels).toEqual([]);
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
    const f = defaultFilters("list");
    expect(writeParams(f, DEFAULT_SORT, "list", undefined, 1).toString()).toBe("");
    expect(writeParams(f, DEFAULT_SORT, "list", undefined, 3).get("page")).toBe("3");
  });

  it("sends an offset for later pages and none for the first", () => {
    const f = defaultFilters("list");
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
