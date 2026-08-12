import { describe, it, expect } from "vitest";
import { activeSlice, isActiveTicket, scopeTicketsUrl } from "./activeTickets";
import type { Task } from "./tasks";

const row = (over: Partial<Task>): Task =>
  ({ id: "t1", title: "T", body: "", text: "T", status: "todo", ...over }) as Task;

describe("isActiveTicket", () => {
  it("admits unstarted and started, and nothing else", () => {
    expect(isActiveTicket({ status: "todo" })).toBe(true);
    expect(isActiveTicket({ status: "doing" })).toBe(true);
    expect(isActiveTicket({ status: "in_review" })).toBe(true);
    expect(isActiveTicket({ status: "blocked" })).toBe(true);
    expect(isActiveTicket({ status: "draft" })).toBe(false);
    expect(isActiveTicket({ status: "parked" })).toBe(false);
    expect(isActiveTicket({ status: "done" })).toBe(false);
    expect(isActiveTicket({ status: "cancelled" })).toBe(false);
  });

  it("prefers the state_type the server derived", () => {
    // The server said started; the status alone would read as backlog. A client
    // that recomputed instead of reading would be a second source of truth for
    // the one field the backend deliberately refuses to store.
    expect(isActiveTicket({ status: "parked", state_type: "started" })).toBe(true);
    expect(isActiveTicket({ status: "doing", state_type: "completed" })).toBe(false);
  });

  it("reads an unclassifiable row as backlog rather than dropping it", () => {
    expect(isActiveTicket({ status: "invented" })).toBe(false);
    expect(isActiveTicket({})).toBe(false);
  });
});

describe("activeSlice", () => {
  it("keeps server order and caps", () => {
    const rows = [
      row({ id: "a", status: "doing" }),
      row({ id: "b", status: "done" }),
      row({ id: "c", status: "todo" }),
      row({ id: "d", status: "blocked" }),
      row({ id: "e", status: "parked" }),
    ];
    const s = activeSlice(rows, 2);
    expect(s.rows.map((t) => t.id)).toEqual(["a", "c"]);
    expect(s.total).toBe(3);
    expect(s.overflow).toBe(1);
  });

  it("reports no overflow when everything fits", () => {
    const s = activeSlice([row({ id: "a" }), row({ id: "b" })], 5);
    expect(s.rows).toHaveLength(2);
    expect(s.overflow).toBe(0);
  });

  it("handles the pre-load state", () => {
    expect(activeSlice(null, 5)).toEqual({ rows: [], total: 0, overflow: 0 });
    expect(activeSlice(undefined, 5)).toEqual({ rows: [], total: 0, overflow: 0 });
  });
});

describe("scopeTicketsUrl", () => {
  it("asks for the scheduler's order and escapes the scope key", () => {
    expect(scopeTicketsUrl("project:birds")).toBe("/api/tasks?scope=project%3Abirds&sort=queue");
  });
});
