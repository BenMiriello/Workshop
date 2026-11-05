import type { Schedule } from "../../lib/chatSchedules";
import { localInputValue } from "./sendRules";

// A schedule as its form edits it, and back to a request. Pure, so the rules
// run in tests: ScheduleForm only renders these values.

export type ScheduleForm = {
  text: string;
  label: string;
  repeat: "after_reply" | "every" | "once";
  every: number;
  unit: "min" | "h";
  /** `datetime-local` values: local wall time, no zone. */
  onceAt: string;
  start: "now" | "at";
  startAt: string;
  end: "never" | "at" | "count";
  untilAt: string;
  maxSends: number;
};

const iso = (local: string): string | null => {
  const d = local ? new Date(local) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toISOString() : null;
};

/** The form as a request, or the sentence that says what is missing. */
export function formBody(f: ScheduleForm, now: Date = new Date()): {
  body?: {
    text: string; label: string | null; repeat: ScheduleForm["repeat"]; every_min: number | null;
    start_at: string | null; until_at: string | null; max_sends: number | null;
  };
  error?: string;
} {
  if (!f.text.trim()) return { error: "Write the message it sends." };
  const every = f.repeat === "every" ? Math.round(f.every * (f.unit === "h" ? 60 : 1)) : null;
  if (every !== null && !(every >= 1)) return { error: "Say how often, at least a minute." };
  const once = f.repeat === "once";
  const start = once ? iso(f.onceAt) : f.start === "at" ? iso(f.startAt) : null;
  if (once && (!start || new Date(start) <= now)) return { error: "Pick a time in the future." };
  if (!once && f.start === "at" && !start) return { error: "Pick when it starts." };
  const until = !once && f.end === "at" ? iso(f.untilAt) : null;
  if (!once && f.end === "at" && (!until || new Date(until) <= now)) {
    return { error: "Pick an end time in the future." };
  }
  const max = !once && f.end === "count" ? Math.round(f.maxSends) : null;
  if (max !== null && !(max >= 1)) return { error: "Send at least once." };
  return { body: {
    text: f.text.trim(), label: f.label.trim() || null, repeat: f.repeat, every_min: every,
    start_at: start, until_at: until, max_sends: max,
  } };
}

/** The form for a new schedule of `text`, or for editing `s`. */
export function formOf(s: Schedule | null, text = "", now: Date = new Date()): ScheduleForm {
  const local = (v?: string | null) => (v ? localInputValue(new Date(v)) : "");
  const later = (ms: number) => localInputValue(new Date(now.getTime() + ms));
  const every = s?.every_min ?? 30;
  const once = s?.repeat === "once";
  return {
    text: s?.text ?? text, label: s?.label ?? "", repeat: s?.repeat ?? "after_reply",
    every: every % 60 === 0 ? every / 60 : every, unit: every % 60 === 0 ? "h" : "min",
    onceAt: local(once ? s?.start_at : null) || later(3600_000),
    start: !once && s?.start_at ? "at" : "now",
    startAt: local(once ? null : s?.start_at) || later(3600_000),
    end: s?.until_at ? "at" : s?.max_sends ? "count" : "never",
    untilAt: local(s?.until_at) || later(86_400_000),
    maxSends: s?.max_sends ?? 10,
  };
}

/** The form for running `s` again or as a copy: its message and rule, with
 *  any time already past moved to the next sensible one. */
export function againFormOf(s: Schedule, now: Date = new Date()): ScheduleForm {
  const f = formOf(s, "", now);
  const blank = formOf(null, "", now);
  const past = (local: string) => !(new Date(local).getTime() > now.getTime());
  return {
    ...f,
    onceAt: past(f.onceAt) ? blank.onceAt : f.onceAt,
    start: f.start === "at" && past(f.startAt) ? "now" : f.start,
    startAt: past(f.startAt) ? blank.startAt : f.startAt,
    untilAt: past(f.untilAt) ? blank.untilAt : f.untilAt,
  };
}
