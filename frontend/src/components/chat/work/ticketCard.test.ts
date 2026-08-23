import { describe, it, expect } from "vitest";
import {
  DELETED_TEXT, clockText, receiptText, runStatusLabel, ticketCardPlan,
  type TicketCardInput,
} from "./ticketCard";
import type { InboxItem, WorkRun, WorkTicket } from "./workTypes";
import type { TaskStatus } from "../../../lib/tasks";
import { pickupFor } from "../../../lib/scopePickup";

// The ten states of plan §4 are the point of this module, so they are the shape
// of this file: one describe per state, named the way the plan names it.

const T0 = Date.parse("2026-08-15T12:00:00Z");

function run(over: Partial<WorkRun> = {}): WorkRun {
  return {
    id: "job-1",
    status: "done",
    started_at: "2026-08-15T11:58:00Z",
    ended_at: "2026-08-15T11:59:30Z",
    agent_name: "maker",
    trigger: "manual",
    exit_code: 0,
    files: null,
    commits: null,
    ...over,
  };
}

function ticket(over: Partial<WorkTicket> = {}): WorkTicket {
  return {
    id: "a3f19c0000000000",
    title: "Rebuild the ticket card",
    body: "Every state it can be in.",
    text: "Rebuild the ticket card\nEvery state it can be in.",
    status: "todo" as TaskStatus,
    scope: "workshop",
    updated: "2026-08-15T11:59:00Z",
    created: "2026-08-15T11:00:00Z",
    // The schema marks these required because `_normalize` always emits them
    // and the ticket routes do not use `response_model_exclude_unset`. A
    // fixture without them is not a ticket the server could send.
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
    run: null,
    ...over,
  };
}

function question(over: Partial<InboxItem> = {}): InboxItem {
  return {
    id: "inbox-1",
    kind: "question",
    agent: "maker",
    scope: "workshop",
    title: "Which database?",
    body: "Postgres or the file store?",
    options: [],
    chat_id: "t1",
    ticket_id: "a3f19c0000000000",
    run_id: null,
    snoozed_until: null,
    ...over,
  };
}

function input(over: Partial<TicketCardInput> = {}): TicketCardInput {
  return {
    id: "a3f19c0000000000",
    scope: "development",
    ticket: ticket({ scope: "development" }),
    filedTitle: "Rebuild the ticket card",
    deleted: false,
    error: null,
    // `development`, not `workshop`. A ticket filed to `workshop` is NOT
    // runnable and never was (ADR 0014 4.1: it is the root inbox and has no
    // code tree to run in), so a default fixture claiming it could run made
    // these states test a combination the server cannot produce. Corrected
    // 2026-08-23 when the client predicate was aligned with the backend.
    pickup: pickupFor(null, "development"),
    waiting: null,
    filedNote: null,
    nowMs: T0,
    ...over,
  };
}

describe("state 1: a proposal that has not been filed", () => {
  it("never reaches the rule, and the rule says so by needing a ticket id", () => {
    // The edit form is UiCard's, not this module's: nothing calls the plan
    // until a filing has produced an id. The nearest thing the rule CAN be
    // asked is "an id with nothing behind it", and that is state 3 of the
    // four reads, not a proposal.
    const plan = ticketCardPlan(input({ ticket: null, filedTitle: "" }));
    expect(plan.phase).toBe("cold");
    expect(plan.title).toBe("a3f19c");
  });
});

describe("state 2: filed, never run", () => {
  const plan = ticketCardPlan(input());

  it("has no Run group at all, rather than an empty one", () => {
    expect(plan.groups).not.toContain("run");
    expect(plan.run).toBeNull();
  });

  it("offers Open ticket and Run now, and nothing else", () => {
    expect(plan.actions).toEqual(["open", "runNow"]);
  });

  it("does NOT offer Run now when the ticket records a run the payload did not carry", () => {
    // The card's fallback read carries no run summary, so `run: null` there
    // means "not read", not "never ran". Offering Run now on that reading
    // started a second run of finished work: the only writing action the card
    // has, fired on the strength of a field the reader never populated.
    const ran = ticketCardPlan(input({
      ticket: ticket({ job_ids: ["job-1"], run: null }),
    }));
    expect(ran.actions).toEqual(["open"]);
  });

  it("carries the ticket's own status in the header", () => {
    expect(plan.status).toEqual({ label: "todo", kind: "stopped" });
  });
});

describe("state 3: running", () => {
  const live = ticket({
    status: "doing" as TaskStatus,
    run: run({ status: "running", ended_at: null }),
  });
  const plan = ticketCardPlan(input({ ticket: live }));

  it("shows the ticket as in progress and the run as running, separately", () => {
    expect(plan.status).toEqual({ label: "in progress", kind: "running" });
    expect(plan.run?.label).toBe("running");
    expect(plan.run?.kind).toBe("running");
  });

  it("counts up from the run's real start, in the LIVE tone", () => {
    expect(plan.run?.clock).toEqual({ live: true, text: "2m 00s" });
  });

  it("swaps Run now for View run", () => {
    expect(plan.actions).toEqual(["open", "viewRun"]);
  });
});

describe("state 4: blocked, waiting on an answer", () => {
  const plan = ticketCardPlan(input({
    ticket: ticket({ status: "blocked" as TaskStatus }),
    waiting: question(),
  }));

  it("says who is waiting, and names the item the Answer link opens", () => {
    expect(plan.note).toEqual({
      kind: "waiting",
      itemId: "inbox-1",
      agent: "maker",
      text: "maker is waiting on an answer",
    });
  });

  it("still shows the ticket's status as blocked", () => {
    expect(plan.status).toEqual({ label: "blocked", kind: "danger" });
  });

  it("outranks every other line the card could have shown", () => {
    const withBoth = ticketCardPlan(input({
      ticket: ticket({ status: "blocked" as TaskStatus }),
      waiting: question(),
      filedNote: "Merged into an existing ticket in Workshop.",
    }));
    expect(withBoth.note?.kind).toBe("waiting");
  });
});

describe("state 5: in review", () => {
  it("is the header pill and nothing else", () => {
    const plan = ticketCardPlan(input({ ticket: ticket({ status: "in_review" as TaskStatus }) }));
    expect(plan.status).toEqual({ label: "in review", kind: "warn" });
  });
});

describe("state 6: done", () => {
  const plan = ticketCardPlan(input({
    ticket: ticket({
      status: "done" as TaskStatus,
      run: run({ status: "done", files: 4, commits: 2 }),
    }),
  }));

  it("keeps the receipt on the Run group", () => {
    expect(plan.run?.receipt).toEqual({ files: 4, commits: 2 });
    expect(receiptText(plan.run?.receipt ?? null)).toBe("4 files changed · 2 commits");
  });

  it("shows the final duration, not a clock that is still moving", () => {
    expect(plan.run?.clock).toEqual({ live: false, text: "1m 30s" });
  });
});

describe("state 7: the run failed and the ticket is not done", () => {
  const plan = ticketCardPlan(input({
    ticket: ticket({ status: "todo" as TaskStatus, run: run({ status: "failed", exit_code: 1 }) }),
  }));

  it("shows both, in separate labelled sections (ADR 0009)", () => {
    expect(plan.status).toEqual({ label: "todo", kind: "stopped" });
    expect(plan.run?.label).toBe("failed");
    expect(plan.run?.kind).toBe("danger");
    expect(plan.groups).toEqual(["task", "ticket", "run"]);
  });

  it("does not offer to run it again from here: the run exists", () => {
    expect(plan.actions).toEqual(["open", "viewRun"]);
  });
});

describe("state 8: merged into an existing ticket", () => {
  it("tracks the surviving ticket and carries one line saying it merged", () => {
    const plan = ticketCardPlan(input({
      filedNote: "Merged into an existing ticket in Workshop.",
    }));
    expect(plan.phase).toBe("ready");
    expect(plan.note).toEqual({
      kind: "muted",
      text: "Merged into an existing ticket in Workshop.",
    });
  });

  it("keeps the merge as the single line even where the scope cannot run work", () => {
    // Both facts are true here. The merge wins the line because it is the more
    // surprising one, and because joining the two produced a ~200-character
    // run-on under a card that exists to be read at a glance. Pinned so the
    // "both lines" idea is not re-tried without reading why it was dropped.
    const plan = ticketCardPlan(input({
      scope: "general",
      ticket: ticket({ scope: "general" }),
      pickup: pickupFor(null, "general"),
      filedNote: "Merged into an existing ticket in General.",
    }));
    expect(plan.note?.text).toBe("Merged into an existing ticket in General.");
  });
});

describe("state 9: the ticket was deleted", () => {
  const plan = ticketCardPlan(input({ ticket: null, deleted: true }));

  it("keeps the card's own header and drops everything that would 404", () => {
    expect(plan.phase).toBe("deleted");
    expect(plan.title).toBe("Rebuild the ticket card");
    expect(plan.groups).toEqual([]);
    expect(plan.actions).toEqual([]);
    expect(plan.status).toBeNull();
  });

  it("has a sentence to say, and it is a line rather than a region", () => {
    expect(DELETED_TEXT).toBe("This ticket no longer exists.");
  });
});

describe("state 10: a scope that cannot run work", () => {
  const plan = ticketCardPlan(input({
    scope: "general",
    ticket: ticket({ scope: "general" }),
    pickup: pickupFor(null, "general"),
  }));

  it("offers no Run now", () => {
    expect(plan.actions).toEqual(["open"]);
  });

  it("keeps the pickup warning as the card's one line", () => {
    expect(plan.note?.kind).toBe("muted");
    expect(plan.note?.text).toContain("Filing works here; running does not.");
  });
});

// ---- the four reads a polled card has to survive ---------------------------

describe("cold: nothing has been read yet", () => {
  const plan = ticketCardPlan(input({ ticket: null }));

  it("names the ticket from what the filing recorded", () => {
    expect(plan.phase).toBe("cold");
    expect(plan.title).toBe("Rebuild the ticket card");
  });

  it("offers Open ticket, which is honest without the ticket in hand", () => {
    expect(plan.actions).toEqual(["open"]);
    expect(plan.problem).toBeNull();
  });

  it("does not warn about pickup for a ticket it has not read", () => {
    expect(plan.note).toBeNull();
  });
});

describe("the read failed", () => {
  it("with no copy in hand, the failure is the card's content", () => {
    const plan = ticketCardPlan(input({ ticket: null, error: "503 Service Unavailable" }));
    expect(plan.phase).toBe("unreadable");
    expect(plan.problem).toBe("503 Service Unavailable");
  });

  it("with a copy in hand, the ticket still renders and the failure is a line", () => {
    const plan = ticketCardPlan(input({ error: "503 Service Unavailable" }));
    expect(plan.phase).toBe("ready");
    expect(plan.groups).toEqual(["task", "ticket"]);
    expect(plan.problem).toBe("503 Service Unavailable");
  });
});

describe("a run with no receipt", () => {
  it("prints nothing, because null is not zero", () => {
    const plan = ticketCardPlan(input({ ticket: ticket({ run: run() }) }));
    expect(plan.run?.receipt).toBeNull();
    expect(receiptText(plan.run?.receipt ?? null)).toBe("");
  });

  it("prints zero when the receipt actually counted zero", () => {
    const plan = ticketCardPlan(input({
      ticket: ticket({ run: run({ files: 0, commits: 0 }) }),
    }));
    expect(plan.run?.receipt).toEqual({ files: 0, commits: 0 });
    expect(receiptText(plan.run?.receipt ?? null)).toBe("0 files changed · 0 commits");
  });

  it("prints the half it has when only one count survived", () => {
    const plan = ticketCardPlan(input({
      ticket: ticket({ run: run({ files: 1, commits: null }) }),
    }));
    expect(receiptText(plan.run?.receipt ?? null)).toBe("1 file changed");
  });
});

// ---- the groups, which are the other half of the rule ----------------------

describe("a group with no data is omitted entirely", () => {
  it("drops Task when the ticket has no body", () => {
    const plan = ticketCardPlan(input({ ticket: ticket({ body: "", text: "Rebuild the ticket card" }) }));
    expect(plan.groups).toEqual(["ticket"]);
    expect(plan.body).toBe("");
  });

  it("drops Blocked by, Labels and Effort rather than rendering them empty", () => {
    const plan = ticketCardPlan(input({
      ticket: ticket({ labels: [], blocked_by: [], effort: null }),
    }));
    expect(plan.props.map((p) => p.key)).toEqual(["key"]);
  });

  it("keeps them, in plan order, when they carry something", () => {
    const plan = ticketCardPlan(input({
      ticket: ticket({
        labels: ["infra"],
        effort: "M",
        blocked_by: [{ id: "b1", scope: "workshop", title: "First", status: "todo" as TaskStatus }],
      }),
    }));
    expect(plan.props.map((p) => p.key)).toEqual(["key", "blocked_by", "labels", "effort"]);
  });

  it("names the key as the scope plus the app's real ticket handle", () => {
    const plan = ticketCardPlan(input());
    expect(plan.props[0]).toEqual({
      key: "key", label: "Key", scope: "Development", text: "a3f19c",
    });
  });

  it("drops labels that are only whitespace", () => {
    const plan = ticketCardPlan(input({ ticket: ticket({ labels: ["  "] }) }));
    expect(plan.props.map((p) => p.key)).toEqual(["key"]);
  });
});

// ---- the small formatters, which are copy ----------------------------------

describe("the clock", () => {
  it("agrees with the panel row below an hour, and adds hours above it", () => {
    expect(clockText(9)).toBe("9s");
    expect(clockText(65)).toBe("1m 05s");
    expect(clockText(3600)).toBe("1h 00m");
    expect(clockText(7623)).toBe("2h 07m");
  });

  it("never counts backwards", () => {
    expect(clockText(-5)).toBe("0s");
  });

  it("is absent on a run with no start time to count from", () => {
    const plan = ticketCardPlan(input({
      ticket: ticket({ run: run({ started_at: null }) }),
    }));
    expect(plan.run?.clock).toBeNull();
  });
});

describe("the run's state word", () => {
  it("drops the underscore, because an underscore reads poorly in prose", () => {
    expect(runStatusLabel("awaiting_input")).toBe("awaiting input");
    expect(runStatusLabel("done")).toBe("done");
    expect(runStatusLabel(undefined)).toBe("unknown");
  });
});
