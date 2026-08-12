import { describe, expect, it } from "vitest";
import {
  DEFAULT_STATUS_FILTER,
  STATUS_FILTER_GROUPS,
  boardLanes,
  groupTickets,
  isBacklogOnlyFilter,
  matchesStatusFilter,
  statusFilterQuery,
  statusFilterStatuses,
} from "./ticketGroups";
import {
  STATUSES_BY_STATE_TYPE,
  TASK_STATE_TYPES,
  TASK_STATUSES,
  taskStateType,
  type Task,
  type TaskStatus,
} from "./tasks";

const t = (id: string, status: TaskStatus, scope = "workshop"): Task => ({
  id,
  title: id,
  body: "",
  text: id,
  status,
  scope,
});

describe("groupTickets", () => {
  it("returns one unlabelled bucket when grouping is off", () => {
    const tasks = [t("a", "todo"), t("b", "done")];
    const groups = groupTickets(tasks, "none");
    expect(groups).toHaveLength(1);
    expect(groups[0].tasks).toEqual(tasks);
    expect(groups[0].label).toBe("");
  });

  it("orders status buckets by urgency, not alphabetically", () => {
    const groups = groupTickets(
      [t("a", "done"), t("b", "todo"), t("c", "blocked"), t("d", "doing")],
      "status",
    );
    expect(groups.map((g) => g.key)).toEqual(["blocked", "doing", "todo", "done"]);
  });

  // The heading order is derived, and what it derives to is the backend's
  // `_STATUS_RANK` (backend/services/tasks.py). Pinned here because that file
  // cannot import this one and this one cannot import that one: if the two ever
  // diverge, this is the assertion that says so.
  it("reproduces the backend status rank exactly", () => {
    const all = TASK_STATUSES.map((s) => t(s, s));
    expect(groupTickets(all, "status").map((g) => g.key)).toEqual([
      "blocked", "doing", "in_review", "todo", "draft", "parked", "done", "cancelled",
    ]);
  });

  it("omits buckets with nothing in them", () => {
    const groups = groupTickets([t("a", "todo")], "status");
    expect(groups.map((g) => g.key)).toEqual(["todo"]);
  });

  it("groups by scope with human labels, sorted by label", () => {
    const groups = groupTickets(
      [t("a", "todo", "project:zebra"), t("b", "todo", "workshop"), t("c", "todo", "project:apple")],
      "scope",
    );
    expect(groups.map((g) => g.label)).toEqual([
      "Project: apple",
      "Project: zebra",
      "Workshop root",
    ]);
  });

  it("keeps every task exactly once across buckets", () => {
    const tasks = [t("a", "todo"), t("b", "blocked"), t("c", "todo"), t("d", "done")];
    const ids = groupTickets(tasks, "status").flatMap((g) => g.tasks.map((x) => x.id));
    expect(ids.sort()).toEqual(["a", "b", "c", "d"]);
  });

  it("does not drop a status the fixed order has never heard of", () => {
    const odd = { ...t("x", "todo"), status: "archived" as TaskStatus };
    const groups = groupTickets([odd, t("a", "todo")], "status");
    expect(groups.flatMap((g) => g.tasks.map((x) => x.id)).sort()).toEqual(["a", "x"]);
  });
});

describe("the state-type table", () => {
  it("classifies all eight statuses exactly once", () => {
    const listed = TASK_STATE_TYPES.flatMap((type) => STATUSES_BY_STATE_TYPE[type]);
    expect([...listed].sort()).toEqual([...TASK_STATUSES].sort());
  });

  it("puts blocked in started and parked in backlog", () => {
    // Both are the deliberate calls: blocked work HAS begun and is stuck, and a
    // parked ticket is out of the plan but not finished.
    expect(taskStateType("blocked")).toBe("started");
    expect(taskStateType("parked")).toBe("backlog");
  });
});

describe("boardLanes", () => {
  it("always returns the same five lanes, even when empty", () => {
    const lanes = boardLanes([]);
    expect(lanes.map((l) => l.id))
      .toEqual(["backlog", "unstarted", "started", "completed", "canceled"]);
    expect(lanes.every((l) => l.tasks.length === 0)).toBe(true);
    expect(lanes.every((l) => l.hint.length > 0)).toBe(true);
  });

  it("places every task in exactly one lane", () => {
    const tasks: Task[] = [
      t("a", "draft"), t("b", "todo"), t("c", "blocked"), t("d", "parked"),
      t("e", "doing"), t("f", "in_review"), t("g", "done"), t("h", "cancelled"),
    ];
    const lanes = boardLanes(tasks);
    expect(lanes.flatMap((l) => l.tasks.map((x) => x.id)).sort())
      .toEqual(["a", "b", "c", "d", "e", "f", "g", "h"]);
    expect(lanes.map((l) => l.tasks.length)).toEqual([2, 1, 3, 1, 1]);
  });

  it("prefers the state type the server derived", () => {
    const row = { ...t("a", "todo"), state_type: "started" as const };
    const lanes = boardLanes([row]);
    expect(lanes.find((l) => l.id === "started")?.tasks.map((x) => x.id)).toEqual(["a"]);
  });

  it("treats an unknown status as backlog rather than losing it", () => {
    const odd = { ...t("x", "todo"), status: "something-new" as TaskStatus };
    const lanes = boardLanes([odd]);
    expect(lanes.find((l) => l.id === "backlog")?.tasks.map((x) => x.id)).toEqual(["x"]);
  });
});

describe("statusFilterStatuses", () => {
  it("defaults to the two active types", () => {
    expect(DEFAULT_STATUS_FILTER).toBe("active");
    expect(statusFilterStatuses("active")).toEqual(
      ["todo", "blocked", "doing", "in_review"]);
  });

  it("treats all as no filter at all", () => {
    expect(statusFilterStatuses("all")).toBeNull();
  });

  it("expands a state type into its statuses", () => {
    expect(statusFilterStatuses("backlog")).toEqual(["draft", "parked"]);
    expect(statusFilterStatuses("completed")).toEqual(["done"]);
  });

  it("passes a single status through", () => {
    expect(statusFilterStatuses("parked")).toEqual(["parked"]);
  });
});

describe("statusFilterQuery", () => {
  it("pushes a one-status filter down to the endpoint", () => {
    expect(statusFilterQuery("todo")).toEqual({ status: "todo" });
    // A state type covering exactly one status is still one status.
    expect(statusFilterQuery("unstarted")).toEqual({ status: "todo" });
    expect(statusFilterQuery("canceled")).toEqual({ status: "cancelled" });
  });

  it("asks for everything when the filter is all", () => {
    expect(statusFilterQuery("all")).toEqual({ status: null });
  });

  // The endpoint now takes a comma-separated list, so a multi-status filter is
  // ONE query rather than a full unpaged fetch narrowed on the client. These
  // used to assert the opposite (`clientSide: true`), which is what made the
  // default view download every ticket in every scope on each filter change.
  it("sends a multi-status filter as one comma-separated query", () => {
    expect(statusFilterQuery("active")).toEqual({ status: "todo,blocked,doing,in_review" });
    expect(statusFilterQuery("backlog")).toEqual({ status: "draft,parked" });
    expect(statusFilterQuery("started")).toEqual({ status: "blocked,doing,in_review" });
  });

  it("never sends a status the backend would reject", () => {
    // The one-letter trap: the TYPE is `canceled`, the STATUS is `cancelled`,
    // and the endpoint 400s on the wrong one. Only statuses go over the wire.
    for (const filter of ["all", "active", ...TASK_STATE_TYPES, ...TASK_STATUSES] as const) {
      const { status } = statusFilterQuery(filter);
      if (status === null) continue;
      for (const s of status.split(",")) {
        expect(TASK_STATUSES).toContain(s);
      }
    }
  });
});

describe("matchesStatusFilter", () => {
  it("admits everything under all", () => {
    expect(matchesStatusFilter({ status: "draft" }, "all")).toBe(true);
  });

  it("hides backlog work from the active default", () => {
    expect(matchesStatusFilter({ status: "todo" }, "active")).toBe(true);
    expect(matchesStatusFilter({ status: "in_review" }, "active")).toBe(true);
    expect(matchesStatusFilter({ status: "draft" }, "active")).toBe(false);
    expect(matchesStatusFilter({ status: "parked" }, "active")).toBe(false);
    expect(matchesStatusFilter({ status: "done" }, "active")).toBe(false);
  });

  it("drops a row whose status it has never heard of", () => {
    expect(matchesStatusFilter({ status: "archived" }, "active")).toBe(false);
    expect(matchesStatusFilter({}, "active")).toBe(false);
  });
});

describe("isBacklogOnlyFilter", () => {
  it("is true only when nothing but backlog work is on screen", () => {
    expect(isBacklogOnlyFilter("backlog")).toBe(true);
    expect(isBacklogOnlyFilter("draft")).toBe(true);
    expect(isBacklogOnlyFilter("parked")).toBe(true);
    expect(isBacklogOnlyFilter("all")).toBe(false);
    expect(isBacklogOnlyFilter("active")).toBe(false);
    expect(isBacklogOnlyFilter("todo")).toBe(false);
  });
});

describe("STATUS_FILTER_GROUPS", () => {
  it("offers every state type and every status, plus Active and All", () => {
    const values = STATUS_FILTER_GROUPS.flatMap((g) => g.options.map((o) => o.value));
    expect(values).toEqual([
      "active", "all",
      ...TASK_STATE_TYPES,
      ...TASK_STATUSES,
    ]);
  });

  it("names every option", () => {
    for (const g of STATUS_FILTER_GROUPS) {
      expect(g.label.length).toBeGreaterThan(0);
      for (const o of g.options) expect(o.label.length).toBeGreaterThan(0);
    }
  });
});
