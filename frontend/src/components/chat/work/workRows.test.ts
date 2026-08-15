import { describe, it, expect } from "vitest";
import {
  MAX_ROWS, NOTHING_FILED_HERE, NOTHING_RUNNING, NOTHING_UNDER_WAY, NOTHING_WAITING,
  WORK_CHIPS,
  chipCount, defaultChip, emptyText, ticketsForChip, workList,
} from "./workRows";
import { EMPTY_WORK, type InboxItem, type WorkPayload, type WorkTicket } from "./workTypes";
import type { TaskStatus } from "../../../lib/tasks";

function ticket(
  id: string,
  status: TaskStatus,
  opts: { updated?: string; created?: string; run?: string | null; scope?: string } = {},
): WorkTicket {
  return {
    id,
    title: `Ticket ${id}`,
    body: "",
    text: `Ticket ${id}`,
    status,
    scope: opts.scope ?? "development",
    updated: opts.updated,
    created: opts.created,
    run: opts.run
      ? {
          id: `run-${id}`,
          status: opts.run,
          started_at: null,
          ended_at: null,
          agent_name: "maker",
          trigger: "manual",
          exit_code: null,
          files: null,
          commits: null,
        }
      : null,
  };
}

function item(id: string): InboxItem {
  return {
    id,
    kind: "question",
    agent: "maker",
    scope: "development",
    title: `Question ${id}`,
    body: "Which one?",
    options: [],
    chat_id: "t1",
    ticket_id: null,
    run_id: null,
    snoozed_until: null,
  };
}

function payload(over: Partial<WorkPayload> = {}): WorkPayload {
  const filed = over.filed ?? [];
  const scope = over.scope ?? [];
  const inbox = over.inbox ?? [];
  return {
    inbox,
    filed,
    scope,
    scope_key: "development",
    counts: over.counts ?? {
      needs_you: inbox.length,
      running: 0,
      this_chat: filed.length,
      all: filed.length + scope.length,
    },
  };
}

describe("defaultChip", () => {
  it("opens on Needs you whenever something is waiting", () => {
    expect(defaultChip({ needs_you: 1, running: 4, this_chat: 9, all: 30 }))
      .toBe("needs_you");
  });

  it("falls back to This chat when nothing is waiting but this chat filed work", () => {
    expect(defaultChip({ needs_you: 0, running: 2, this_chat: 3, all: 30 }))
      .toBe("this_chat");
  });

  it("falls back to All when the chat has filed nothing", () => {
    expect(defaultChip({ needs_you: 0, running: 2, this_chat: 0, all: 30 }))
      .toBe("all");
  });

  it("still resolves on a chat with no work at all", () => {
    expect(defaultChip(EMPTY_WORK.counts)).toBe("all");
  });
});

describe("chipCount", () => {
  const counts = { needs_you: 1, running: 2, this_chat: 3, all: 4 };
  it("reads each chip from the server's counts", () => {
    expect(chipCount(counts, "needs_you")).toBe(1);
    expect(chipCount(counts, "running")).toBe(2);
    expect(chipCount(counts, "this_chat")).toBe(3);
    expect(chipCount(counts, "all")).toBe(4);
  });
});

describe("ticketsForChip", () => {
  const filed = ticket("a", "todo");
  const mine = ticket("b", "doing", { run: "running" });
  const theirs = ticket("c", "todo");
  const data = payload({ filed: [filed, mine], scope: [theirs] });

  it("gives Needs you no tickets: it lists inbox items", () => {
    expect(ticketsForChip(data, "needs_you")).toEqual([]);
  });

  it("gives This chat only what this chat filed", () => {
    expect(ticketsForChip(data, "this_chat").map((t) => t.id)).toEqual(["a", "b"]);
  });

  it("gives Running only tickets whose run is live", () => {
    expect(ticketsForChip(data, "running").map((t) => t.id)).toEqual(["b"]);
  });

  it("gives All both lists", () => {
    expect(ticketsForChip(data, "all").map((t) => t.id)).toEqual(["a", "b", "c"]);
  });

  it("never lists one ticket twice when it is in both", () => {
    const both = payload({ filed: [filed], scope: [filed] });
    expect(ticketsForChip(both, "all").map((t) => t.id)).toEqual(["a"]);
  });
});

describe("workList grouping", () => {
  it("puts a live run first, whatever the ticket's own status says", () => {
    const data = payload({
      filed: [
        ticket("done", "done"),
        ticket("queued", "todo"),
        // `todo` on the ticket, a run in flight on the job. The run wins.
        ticket("live", "todo", { run: "running" }),
        ticket("stuck", "blocked"),
      ],
    });
    expect(workList(data, "this_chat").groups.map((g) => g.key))
      .toEqual(["live", "started", "unstarted", "completed"]);
  });

  it("labels each group by what it holds", () => {
    const data = payload({ filed: [ticket("a", "doing"), ticket("b", "parked")] });
    expect(workList(data, "this_chat").groups.map((g) => g.label))
      .toEqual(["Under way", "Backlog"]);
  });

  it("keeps a cancelled ticket rather than dropping it off the end", () => {
    const data = payload({ filed: [ticket("a", "cancelled")] });
    const list = workList(data, "this_chat");
    expect(list.groups.map((g) => g.key)).toEqual(["canceled"]);
    expect(list.shown).toBe(1);
  });

  it("sorts the newest update first inside a group", () => {
    const data = payload({
      filed: [
        ticket("old", "todo", { updated: "2026-08-01T00:00:00Z" }),
        ticket("new", "todo", { updated: "2026-08-14T00:00:00Z" }),
        ticket("mid", "todo", { updated: "2026-08-09T00:00:00Z" }),
      ],
    });
    expect(workList(data, "this_chat").groups[0].rows.map((r) => r.id))
      .toEqual(["new", "mid", "old"]);
  });

  it("falls back to created, then to the id, so the order never shuffles", () => {
    const data = payload({
      filed: [
        ticket("b", "todo", { created: "2026-08-02T00:00:00Z" }),
        ticket("a", "todo", { created: "2026-08-02T00:00:00Z" }),
        ticket("c", "todo", { created: "2026-08-03T00:00:00Z" }),
      ],
    });
    expect(workList(data, "this_chat").groups[0].rows.map((r) => r.id))
      .toEqual(["c", "a", "b"]);
  });

  it("carries the ticket on a ticket row and the item on a needs-you row", () => {
    const data = payload({ inbox: [item("i1")], filed: [ticket("a", "todo")] });
    const [itemRow] = workList(data, "needs_you").groups[0].rows;
    const [ticketRow] = workList(data, "this_chat").groups[0].rows;
    expect(itemRow.kind === "item" && itemRow.item.agent).toBe("maker");
    expect(ticketRow.kind === "ticket" && ticketRow.ticket.status).toBe("todo");
  });
});

describe("workList overflow", () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) =>
      ticket(`t${String(i).padStart(2, "0")}`, "todo",
        { updated: `2026-08-${String(28 - i).padStart(2, "0")}T00:00:00Z` }));

  it("stops at the cap and reports what is past it", () => {
    const rows = many(14);
    const data = payload({
      filed: rows,
      counts: { needs_you: 0, running: 0, this_chat: 14, all: 14 },
    });
    const list = workList(data, "this_chat");
    expect(list.shown).toBe(MAX_ROWS);
    expect(list.total).toBe(14);
    expect(list.overflow).toBe(6);
  });

  it("spends the cap across groups in order, and drops the last group entirely", () => {
    const data = payload({
      filed: [
        ...Array.from({ length: 8 }, (_, i) => ticket(`u${i}`, "todo")),
        ticket("z", "done"),
      ],
    });
    const list = workList(data, "this_chat");
    expect(list.groups.map((g) => g.key)).toEqual(["unstarted"]);
    expect(list.overflow).toBe(1);
  });

  it("trusts the server's count when it is larger than what it sent", () => {
    // `scope` arrives capped at eight from the server; `all` counts the whole
    // active queue. The chip must say there is more, not that this is all of it.
    const data = payload({
      scope: many(8),
      counts: { needs_you: 0, running: 0, this_chat: 0, all: 31 },
    });
    const list = workList(data, "all");
    expect(list.shown).toBe(8);
    expect(list.overflow).toBe(23);
  });

  it("trusts the rows when they outnumber the count", () => {
    // `all` counts ACTIVE tickets in the chat's scope, so a done ticket this
    // chat filed is a real row the count has never heard of.
    const data = payload({
      filed: [ticket("a", "done"), ticket("b", "done")],
      counts: { needs_you: 0, running: 0, this_chat: 2, all: 0 },
    });
    const list = workList(data, "all");
    expect(list.shown).toBe(2);
    expect(list.total).toBe(2);
    expect(list.overflow).toBe(0);
  });

  it("caps the needs-you list too, and counts the rest", () => {
    const data = payload({
      inbox: Array.from({ length: 11 }, (_, i) => item(`i${i}`)),
      counts: { needs_you: 11, running: 0, this_chat: 0, all: 0 },
    });
    const list = workList(data, "needs_you");
    expect(list.shown).toBe(MAX_ROWS);
    expect(list.overflow).toBe(3);
  });
});

describe("workList when there is nothing", () => {
  it("returns no groups and no overflow for every chip on an empty payload", () => {
    for (const chip of ["needs_you", "running", "this_chat", "all"] as const) {
      const list = workList(EMPTY_WORK, chip);
      expect(list.groups).toEqual([]);
      expect(list.shown).toBe(0);
      expect(list.total).toBe(0);
      expect(list.overflow).toBe(0);
    }
  });

  it("shows no rows for a chip whose set is empty while others have work", () => {
    const data = payload({ filed: [ticket("a", "todo")] });
    expect(workList(data, "needs_you").groups).toEqual([]);
    expect(workList(data, "running").groups).toEqual([]);
  });
});

describe("emptyText", () => {
  it("says what raises a needs-you row", () => {
    expect(emptyText("needs_you")).toBe(NOTHING_WAITING);
  });

  // Each chip selects a DIFFERENT set, so each empty state has to describe the
  // set that is empty. "Nothing under way in this scope" under `This chat` is
  // wrong twice over: the set is this conversation's tickets rather than the
  // scope's, and a filed ticket sitting in `to do` is not under way.
  it("describes the set the chip selects, not a generic absence", () => {
    expect(emptyText("running")).toBe(NOTHING_RUNNING);
    expect(emptyText("this_chat")).toBe(NOTHING_FILED_HERE);
    expect(emptyText("all")).toBe(NOTHING_UNDER_WAY);
  });

  it("gives every chip its own sentence", () => {
    const said = WORK_CHIPS.map((c) => emptyText(c.id));
    expect(new Set(said).size).toBe(said.length);
  });

  it("never uses an em-dash", () => {
    for (const c of WORK_CHIPS) expect(emptyText(c.id)).not.toMatch(/—/);
  });
});

// A heading SEPARATES, so one group has nothing to separate it from and the
// heading only repeats the chip already selected above it.
describe("headings", () => {
  it("prints none for a single group", () => {
    const one = workList(payload({ filed: [ticket("a", "todo")] }), "this_chat");
    expect(one.groups).toHaveLength(1);
    expect(one.headed).toBe(false);
  });

  it("prints them once there is more than one", () => {
    const two = workList(
      payload({ filed: [ticket("a", "todo"), ticket("b", "done")] }),
      "this_chat",
    );
    expect(two.groups.length).toBeGreaterThan(1);
    expect(two.headed).toBe(true);
  });

  it("never heads the needs-you list, which is always one group", () => {
    expect(workList(payload({ inbox: [item("i")] }), "needs_you").headed).toBe(false);
  });
});
