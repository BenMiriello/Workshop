import { describe, expect, it } from "vitest";
import type { Schedule } from "../../lib/chatSchedules";
import { againFormOf, formBody } from "./scheduleFields";
import { actLabel, actsOf, groupOf, ruleSentence, sendText } from "./schedules";

const now = new Date("2026-10-31T12:00:00");
const iso = (hoursFromNow: number) => new Date(now.getTime() + hoursFromNow * 3600_000).toISOString();

const base: Schedule = {
  id: "sch_1", text: "Carry on.", label: null, repeat: "after_reply", every_min: null,
  start_at: null, until_at: null, max_sends: null, status: "active", why: null, sent: 0,
  last_sent_at: null, next_at: null, driver: "workshop", set_by: { kind: "you" },
  created_at: "2026-10-25T07:00:00+00:00",
};
const ended: Schedule = { ...base, status: "done", why: "sent 5 of 5", sent: 5, max_sends: 5 };
const app: Schedule = { ...base, driver: "app", set_by: { kind: "app", name: "Runner", href: null } };

describe("what can be done to a schedule", () => {
  it("groups by state as Active, Paused and Ended", () => {
    expect(groupOf(base)).toBe("active");
    expect(groupOf({ ...base, status: "paused" })).toBe("paused");
    expect(groupOf(ended)).toBe("ended");
  });
  it("offers an active one its everyday acts, and Delete last", () => {
    expect(actsOf(base)).toEqual(["send", "toggle", "edit", "duplicate", "end", "delete"]);
    expect(actsOf({ ...base, status: "paused" })).toEqual(["toggle", "edit", "duplicate", "end", "delete"]);
    expect(actLabel("toggle", { ...base, status: "paused" })).toBe("Resume");
  });
  it("starts an ended one over or sends it again, and never edits it in place", () => {
    expect(actsOf(ended)).toEqual(["rearm", "again", "duplicate", "delete"]);
    expect(actLabel("rearm", ended)).toBe("Run again");
    expect(actLabel("again", ended)).toBe("Send again");
  });
  it("never copies or hand-sends one an app writes", () => {
    expect(actsOf(app)).toEqual(["toggle", "edit", "end", "delete"]);
    expect(actsOf({ ...app, status: "done" })).toEqual(["delete"]);
  });
});

describe("a schedule's rule as one sentence", () => {
  it("says how often and within what", () => {
    expect(ruleSentence(base, now)).toBe("After each reply, until you end it.");
    expect(ruleSentence({ ...base, repeat: "every", every_min: 30, max_sends: 5 }, now))
      .toBe("Every 30 min, up to 5 sends.");
  });
  it("says once and when, with nothing about an end", () => {
    expect(ruleSentence({ ...base, repeat: "once", start_at: "2026-10-31T15:00:00" }, now))
      .toBe("Once at 3:00 PM.");
  });
  it("names the app that sends it", () => {
    expect(ruleSentence(app, now)).toBe("After each reply, when Runner sends it, until you end it.");
  });
});

describe("running a schedule again", () => {
  it("keeps the message and rule, and moves a past time forward", () => {
    const once: Schedule = { ...ended, repeat: "once", max_sends: null, start_at: iso(-24) };
    const f = againFormOf(once, now);
    expect(f.text).toBe("Carry on.");
    expect(f.repeat).toBe("once");
    expect(formBody(f, now).body?.start_at).toBe(iso(1));
  });
  it("starts a repeating one on its own clock when its start has passed", () => {
    const every: Schedule = { ...ended, repeat: "every", every_min: 60, start_at: iso(-2), until_at: iso(-1) };
    const f = againFormOf(every, now);
    expect(f.start).toBe("now");
    expect(f.end).toBe("at");
    expect(formBody(f, now).body).toMatchObject({ start_at: null, until_at: iso(24), every_min: 60 });
  });
  it("keeps times still to come", () => {
    const f = againFormOf({ ...base, repeat: "every", every_min: 30, start_at: iso(3) }, now);
    expect(f.start).toBe("at");
    expect(formBody(f, now).body?.start_at).toBe(iso(3));
  });
});

describe("a schedule's history", () => {
  it("shows when each went out, and the one still waiting", () => {
    expect(sendText({ at: "2026-10-31T09:30:00", state: "sent" }, now)).toBe("9:30 AM");
    expect(sendText({ at: "2026-10-31T11:59:00", state: "waiting" }, now)).toBe("Waiting to send");
  });
});
