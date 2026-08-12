import { describe, expect, it } from "vitest";
import {
  DEFAULT_STATUS_FILTER,
  STATUS_FILTERS,
  matchesStatusFilter,
  statusFilterQuery,
  statusFilterStatuses,
} from "./ticketGroups";
import { STATUSES_BY_STATE_TYPE, TASK_STATUSES, type TaskStatus } from "./tasks";

describe("statusFilterStatuses", () => {
  it("admits every status for 'all'", () => {
    expect(statusFilterStatuses("all")).toBeNull();
  });

  it("expands 'active' to unstarted plus started", () => {
    const wanted = statusFilterStatuses("active");
    expect(wanted).toEqual([
      ...STATUSES_BY_STATE_TYPE.unstarted,
      ...STATUSES_BY_STATE_TYPE.started,
    ]);
  });

  it("expands a state type to its statuses", () => {
    expect(statusFilterStatuses("backlog")).toEqual(STATUSES_BY_STATE_TYPE.backlog);
    expect(statusFilterStatuses("completed")).toEqual(STATUSES_BY_STATE_TYPE.completed);
  });

  it("passes a bare status through as itself", () => {
    expect(statusFilterStatuses("blocked")).toEqual(["blocked"]);
  });
});

describe("statusFilterQuery", () => {
  it("sends no status param for 'all'", () => {
    expect(statusFilterQuery("all")).toEqual({ status: null });
  });

  it("sends a comma-separated list for a multi-status filter", () => {
    const q = statusFilterQuery("active");
    expect(q.status).toBe(statusFilterStatuses("active")!.join(","));
    expect(q.status).toContain(",");
  });

  it("sends one value for a bare status", () => {
    expect(statusFilterQuery("blocked")).toEqual({ status: "blocked" });
  });
});

describe("matchesStatusFilter", () => {
  it("matches everything under 'all'", () => {
    for (const s of TASK_STATUSES) {
      expect(matchesStatusFilter({ status: s }, "all")).toBe(true);
    }
  });

  it("admits a started ticket but not a finished one under 'active'", () => {
    expect(matchesStatusFilter({ status: "todo" }, "active")).toBe(true);
    expect(matchesStatusFilter({ status: "done" }, "active")).toBe(false);
    expect(matchesStatusFilter({ status: "cancelled" }, "active")).toBe(false);
  });
});

describe("STATUS_FILTERS", () => {
  // The control is five options because ten of the old fifteen could only ever
  // return nothing. Guard the count so the menu cannot quietly regrow.
  it("offers exactly five views", () => {
    expect(STATUS_FILTERS).toHaveLength(5);
  });

  it("leads with the default", () => {
    expect(STATUS_FILTERS[0].value).toBe(DEFAULT_STATUS_FILTER);
  });

  it("only offers filters that resolve to real statuses", () => {
    for (const o of STATUS_FILTERS) {
      const wanted = statusFilterStatuses(o.value);
      if (wanted === null) continue;
      expect(wanted.length).toBeGreaterThan(0);
      for (const s of wanted) {
        expect(TASK_STATUSES).toContain(s as TaskStatus);
      }
    }
  });
});
