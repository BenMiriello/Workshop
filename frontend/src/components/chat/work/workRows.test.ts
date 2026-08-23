import { describe, it, expect } from "vitest";
import {
  MAX_ROWS, NOTHING_FILED_HERE, NOTHING_RUNNING, NOTHING_UNDER_WAY, NOTHING_WAITING,
  WORK_CHIPS,
  chatBadge, chipCount, chipLabel, defaultChip, emptyText, ticketsForChip,
  workList, workTitle, needsYouMeta } from "./workRows";
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
    // Both are required on the wire (`_normalize` stamps them), so they are
    // strings here rather than absent. Empty when this case does not set one:
    // `sortKey` reads `t.updated || t.created`, so "" falls through exactly as
    // an absent field did, which is the fallback these tests exercise.
    updated: opts.updated ?? "",
    created: opts.created ?? "",
    owner: "operator",
    scoped_by: "operator",
    source: { kind: "operator", ref: null },
    priority: 0,
    attempts: 0,
    labels: [],
    depends_on: [],
    relates_to: [],
    job_ids: [],
    touches: [],
    evidence: [],
    comments: [],
    external_refs: [],
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
          halted: null,
          refusal: null,
        }
      : null,
  };
}

function item(id: string, ticketId: string | null = null): InboxItem {
  return {
    id,
    kind: "question",
    agent: "maker",
    scope: "development",
    title: `Question ${id}`,
    body: "Which one?",
    options: [],
    chat_id: "t1",
    ticket_id: ticketId,
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

describe("chipLabel", () => {
  it("names the scope on the chip that lists the scope's work", () => {
    expect(chipLabel("all", "Development")).toBe("Development");
  });

  it("leaves the other three alone: they are already about this chat", () => {
    expect(chipLabel("needs_you", "Development")).toBe("Needs you");
    expect(chipLabel("running", "Development")).toBe("Running");
    expect(chipLabel("this_chat", "Development")).toBe("This chat");
  });

  it("falls back to All on a chat with no scope to name", () => {
    expect(chipLabel("all", null)).toBe("All");
    expect(chipLabel("all", "   ")).toBe("All");
  });
});

describe("chatBadge", () => {
  const THREAD = "t1";

  // The reported defect, executable, and measured against the live store on
  // 2026-08-23: the constant 13 was `counts.needs_you`, not `counts.all`. The
  // server derives a needs-you row for every waiting ticket in the SCOPE and
  // admits it into a chat whose scope matches, with `chat_id: null`, so every
  // development chat showed the identical thirteen.
  it("does not print the scope's needs-you count, which is the same in every chat", () => {
    // chat_id null is what the server actually sends for a derived row: it
    // reached this chat by SCOPE, not by naming it.
    const inbox = Array.from({ length: 13 },
      (_, i) => ({ ...item(`i${i}`, `s${i}`), chat_id: null }));
    const here = payload({ inbox, filed: [ticket("mine", "todo")] });
    const elsewhere = payload({ inbox, filed: [] });
    expect(here.counts.needs_you).toBe(13);
    expect(elsewhere.counts.needs_you).toBe(13);
    expect(chatBadge(here, THREAD).count).toBe(1);
    expect(chatBadge(elsewhere, THREAD).count).toBe(0);
  });

  it("is never the scope-wide ticket total either", () => {
    const scope = Array.from({ length: 12 }, (_, i) => ticket(`s${i}`, "todo"));
    const data = payload({ filed: [ticket("mine", "todo")], scope });
    expect(data.counts.all).toBe(13);
    expect(chatBadge(data, THREAD).count).toBe(1);
  });

  it("differs between two chats that share one scope", () => {
    const scope = Array.from({ length: 12 }, (_, i) => ticket(`s${i}`, "todo"));
    const busy = payload({ filed: [ticket("a", "todo"), ticket("b", "doing")], scope });
    const quiet = payload({ filed: [], scope });
    expect(chatBadge(busy, THREAD).count).toBe(2);
    expect(chatBadge(quiet, THREAD).count).toBe(0);
  });

  it("drops the work this chat already finished", () => {
    const data = payload({
      filed: [ticket("a", "todo"), ticket("b", "done"), ticket("c", "cancelled")],
    });
    expect(chatBadge(data, THREAD).count).toBe(1);
  });

  it("counts a filed ticket that is now blocked on a question once, not twice", () => {
    const data = payload({ filed: [ticket("a", "blocked")], inbox: [item("i1", "a")] });
    expect(data.counts.this_chat + data.counts.needs_you).toBe(2);
    const badge = chatBadge(data, THREAD);
    expect(badge.count).toBe(1);
    expect(badge.needsYou).toBe(1);
  });

  it("counts a raised item that names this chat even with no ticket behind it", () => {
    const raised = { ...item("i1"), chat_id: THREAD };
    const data = payload({ filed: [], inbox: [raised] });
    expect(chatBadge(data, THREAD).count).toBe(1);
  });

  it("ignores a raised item that names another chat", () => {
    const raised = { ...item("i1"), chat_id: "somewhere-else" };
    const data = payload({ filed: [], inbox: [raised] });
    expect(chatBadge(data, THREAD).count).toBe(0);
  });

  it("resolves on a blank chat that has no thread yet", () => {
    expect(chatBadge(EMPTY_WORK, null).count).toBe(0);
  });
});

describe("workTitle", () => {
  it("names an interruption first, because it is the only alarm", () => {
    expect(workTitle(4, 2)).toBe("Tickets. 2 waiting on you");
  });

  it("otherwise says what the number is", () => {
    expect(workTitle(3, 0)).toBe("Tickets. 3 from this chat");
    expect(workTitle(0, 0)).toBe("Tickets");
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

describe("needsYouMeta", () => {
  it("is empty when there is nothing to add, so the line disappears", () => {
    // The dominant case: a finished ticket the queue picked up. No ask beyond
    // its status, no agent identity. The card is its title and nothing else.
    expect(needsYouMeta("", "question", "")).toBe("");
    expect(needsYouMeta(null, "question", "")).toBe("");
  });

  it("names the raiser alone when there is no ask, without a dangling verb", () => {
    expect(needsYouMeta("Autonomous run", "question", "")).toBe("Autonomous run");
  });

  it("makes a sentence when there IS an ask", () => {
    expect(needsYouMeta("reviewer", "question", "Which store wins?"))
      .toBe("reviewer asks · Which store wins?");
  });

  it("uses the verb the kind implies", () => {
    expect(needsYouMeta("keeper", "review", "Promote this rule?"))
      .toBe("keeper proposes · Promote this rule?");
    expect(needsYouMeta("keeper", "notify", "Index rebuilt."))
      .toBe("keeper says · Index rebuilt.");
  });

  it("takes the first non-blank line of a multi-line ask", () => {
    expect(needsYouMeta("a", "question", "\n\n  first  \nsecond"))
      .toBe("a asks · first");
  });

  it("shows the ask alone when nobody is named", () => {
    expect(needsYouMeta("", "question", "Which store wins?")).toBe("Which store wins?");
  });
});
