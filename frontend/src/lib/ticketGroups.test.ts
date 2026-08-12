import { describe, expect, it } from "vitest";
import { boardLanes, groupTickets, laneFor } from "./ticketGroups";
import type { Task, TaskStatus } from "./tasks";

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

describe("boardLanes", () => {
  it("always returns the same four lanes, even when empty", () => {
    const lanes = boardLanes([]);
    expect(lanes.map((l) => l.id)).toEqual(["draft", "open", "running", "done"]);
    expect(lanes.every((l) => l.tasks.length === 0)).toBe(true);
  });

  it("folds blocked and parked into Open rather than their own lanes", () => {
    expect(laneFor("blocked")).toBe("open");
    expect(laneFor("parked")).toBe("open");
    expect(laneFor("todo")).toBe("open");
  });

  it("folds in_review into Running and cancelled into Done", () => {
    expect(laneFor("in_review")).toBe("running");
    expect(laneFor("doing")).toBe("running");
    expect(laneFor("cancelled")).toBe("done");
    expect(laneFor("done")).toBe("done");
  });

  it("places every task in exactly one lane", () => {
    const tasks: Task[] = [
      t("a", "draft"), t("b", "todo"), t("c", "blocked"), t("d", "parked"),
      t("e", "doing"), t("f", "in_review"), t("g", "done"), t("h", "cancelled"),
    ];
    const lanes = boardLanes(tasks);
    expect(lanes.flatMap((l) => l.tasks.map((x) => x.id)).sort())
      .toEqual(["a", "b", "c", "d", "e", "f", "g", "h"]);
    expect(lanes.map((l) => l.tasks.length)).toEqual([1, 3, 2, 2]);
  });

  it("treats an unknown status as open rather than losing it", () => {
    expect(laneFor("something-new")).toBe("open");
    expect(laneFor(undefined)).toBe("open");
  });
});
