import { describe, expect, it } from "vitest";
import type { ChatMsg } from "../../lib/chat";
import type { Schedule } from "../../lib/chatSchedules";
import { formBody, formOf } from "./scheduleFields";
import {
  everyText, fromOf, inGroup, limitText, nextText, repeatText, rowMeta, setByText, shownAs,
  stateText, upcomingOf,
} from "./schedules";

const base: Schedule = {
  id: "sch_1", text: "Carry on.", label: null, repeat: "after_reply", every_min: null,
  start_at: null, until_at: null, max_sends: null, status: "active", why: null, sent: 0,
  last_sent_at: null, next_at: null, driver: "workshop", set_by: { kind: "you" },
  created_at: "2026-10-25T07:00:00+00:00",
};
const app: Schedule = {
  ...base, id: "sch_2", driver: "app", label: "Run #9",
  set_by: { kind: "app", name: "Another app", href: "/apps/x/runs/9" },
};

describe("a schedule in words", () => {
  it("says how often and within what", () => {
    expect(everyText(90)).toBe("1 h 30 min");
    expect(repeatText({ ...base, repeat: "every", every_min: 30 })).toBe("Every 30 min");
    expect(repeatText(base)).toBe("After each reply");
    expect(limitText(base)).toBeNull();
    expect(limitText({ ...base, max_sends: 1 })).toBe("up to 1 send");
  });
  it("says where it stands", () => {
    expect(stateText({ ...base, sent: 3, max_sends: 5 })).toBe("Sent 3 of 5");
    expect(stateText({ ...base, status: "paused", why: "paused in the chat" }))
      .toBe("Paused: paused in the chat");
  });
});

describe("whose a message is", () => {
  const msg = (sender?: string): ChatMsg => ({
    id: "m", role: "user", content: "Carry on.", ts: "2026-10-25T07:00:00Z",
    meta: sender ? { sender } : {},
  } as ChatMsg);

  it("leaves what the operator typed as his", () => {
    expect(shownAs(msg(), [base])).toEqual(msg());
  });
  it("shows an app's schedule message as the app's, with its link", () => {
    const shown = shownAs(msg("schedule:sch_2"), [base, app]);
    expect(shown.role).toBe("assistant");
    expect(shown.meta?.sender_name).toBe("Run #9");
    expect(shown.meta?.sender_href).toBe("/apps/x/runs/9");
  });
  it("keeps his own schedule's message his, marked scheduled", () => {
    const shown = shownAs(msg("schedule:sch_1"), [base]);
    expect(shown.role).toBe("user");
    expect(shown.meta?.scheduled).toBe("Carry on.");
  });
  it("shows a schedule the chat set for itself as the chat's own, never the operator's", () => {
    const own: Schedule = { ...base, id: "sch_3", repeat: "every", every_min: 60,
      label: "Review run 9", set_by: { kind: "chat" } };
    const shown = shownAs(msg("schedule:sch_3"), [own]);
    expect(shown.role).toBe("assistant");
    expect(shown.meta?.sender_name).toBe("Review run 9");
    expect(setByText(own)).toBe("This chat");
  });
  it("names a sender written as free text by that text", () => {
    expect(fromOf("some app run #9", [])?.name).toBe("some app run #9");
    expect(fromOf("schedule:gone", [])?.mine).toBe(false);
  });
});

describe("the schedule form", () => {
  const now = new Date("2026-10-25T07:00:00Z");
  const form = {
    text: "Carry on.", label: "", repeat: "every" as const, every: 2, unit: "h" as const,
    onceAt: "", start: "now" as const, startAt: "", end: "count" as const, untilAt: "",
    maxSends: 3,
  };
  it("turns hours into minutes and a count into max sends", () => {
    const got = formBody(form, now).body;
    expect(got?.every_min).toBe(120);
    expect(got?.max_sends).toBe(3);
    expect(got?.until_at).toBeNull();
  });
  it("says what is missing", () => {
    expect(formBody({ ...form, text: " " }, now).error).toBe("Write the message it sends.");
    expect(formBody({ ...form, repeat: "once", onceAt: "" }, now).error).toBe("Pick a time in the future.");
    expect(formBody({ ...form, start: "at", startAt: "" }, now).error).toBe("Pick when it starts.");
  });
  it("starts on its own clock unless a start is chosen", () => {
    expect(formBody(form, now).body?.start_at).toBeNull();
    expect(formBody({ ...form, start: "at", startAt: "2026-10-25T09:00" }, now).body?.start_at).not.toBeNull();
  });
  it("ignores the end fields of a one-off", () => {
    const got = formBody({ ...form, repeat: "once", onceAt: "2026-10-26T09:00" }, now).body;
    expect(got?.max_sends).toBeNull();
    expect(got?.until_at).toBeNull();
  });
  it("opens an existing schedule with its own start", () => {
    const f = formOf({ ...base, repeat: "every", every_min: 120, start_at: "2026-10-25T09:00:00Z" }, "", now);
    expect(f.start).toBe("at");
    expect(f.unit).toBe("h");
    expect(formOf(base, "", now).start).toBe("now");
  });
});

describe("the list", () => {
  const now = new Date("2026-10-25T07:00:00Z");
  const at = (h: number) => new Date(now.getTime() + h * 3600_000).toISOString();
  const soon: Schedule = { ...base, id: "a", repeat: "every", every_min: 30, next_at: at(1) };
  const later: Schedule = { ...base, id: "b", repeat: "once", start_at: at(5), next_at: at(5) };
  const paused: Schedule = { ...base, id: "c", status: "paused", why: "you stopped the chat's turn" };
  const done: Schedule = { ...base, id: "d", status: "done", why: "sent 4 of 4", sent: 4 };
  const wake = { id: "w", due_at: at(3), reason: "Check the run" };

  it("lists what is coming soonest first, wake-ups among it, untimed last", () => {
    const got = upcomingOf([later, base, paused, soon, done], [wake]);
    expect(got.map((u) => (u.kind === "wake" ? u.w.id : u.s.id))).toEqual(["a", "w", "b", "sch_1"]);
  });
  it("keeps paused and ended apart", () => {
    expect(inGroup([soon, paused, done], "paused").map((s) => s.id)).toEqual(["c"]);
    expect(inGroup([soon, paused, done], "ended").map((s) => s.id)).toEqual(["d"]);
  });
  it("says when each sends next, or why it stopped", () => {
    expect(nextText(base, now)).toBe("After the next reply");
    expect(nextText(app, now)).toBe("When the app sends it");
    expect(rowMeta({ ...soon, max_sends: 5, sent: 2 }, now)).toMatch(/^Every 30 min · next .+ · 2 of 5 sent$/);
    expect(rowMeta(later, now)).toMatch(/^Once at [^·]+$/);
    expect(rowMeta(paused, now)).toBe("After each reply · you stopped the chat's turn");
    expect(rowMeta(done, now)).toBe("After each reply · sent 4 of 4");
    expect(rowMeta({ ...base, set_by: { kind: "chat" } }, now)).toBe("After each reply · set by this chat");
  });
});
