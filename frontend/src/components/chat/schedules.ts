import type { ChatMsg } from "../../lib/chat";
import type { Schedule, WakeUp } from "../../lib/chatSchedules";
import { whenText } from "./sendRules";

// A chat's schedules in words, and whose a message is. Pure, so the rules run
// in tests: the dialog and the transcript only render what these return.

export const SCHEDULE_SENDER = "schedule:";

/** "30 min", "2 h", "1 h 30 min". */
export function everyText(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

/** What it is called: its label, else the start of what it sends. */
export function nameOf(s: Schedule): string {
  if (s.label) return s.label;
  const t = s.text.replace(/\s+/g, " ").trim();
  return t.length > 48 ? `${t.slice(0, 47)}…` : t;
}

/** "After each reply", "Every 30 min", "Once at 3:00 PM". */
export function repeatText(s: Schedule, now?: Date): string {
  if (s.repeat === "every") return `Every ${everyText(s.every_min ?? 0)}`;
  if (s.repeat === "once") return s.start_at ? `Once at ${whenText(s.start_at, now)}` : "Once";
  return "After each reply";
}

/** Its bounds, or null when it has none: "from 9:00 AM, until 5:00 PM, up to 5 sends". */
export function limitText(s: Schedule, now?: Date): string | null {
  const parts: string[] = [];
  if (s.start_at && s.repeat !== "once") parts.push(`from ${whenText(s.start_at, now)}`);
  if (s.until_at) parts.push(`until ${whenText(s.until_at, now)}`);
  if (s.max_sends) parts.push(`up to ${s.max_sends} ${s.max_sends === 1 ? "send" : "sends"}`);
  return parts.length ? parts.join(", ") : null;
}

/** Where it stands: "Sent 3 of 5. Next 3:30 PM", "Paused: you stopped the chat's turn". */
export function stateText(s: Schedule, now?: Date): string {
  if (s.status === "paused") return s.why ? `Paused: ${s.why}` : "Paused";
  if (s.status === "done") return s.why ? `Done: ${s.why}` : "Done";
  const sent = s.max_sends ? `Sent ${s.sent} of ${s.max_sends}` : `Sent ${s.sent}`;
  return s.next_at ? `${sent}. Next ${whenText(s.next_at, now)}` : sent;
}

/** Its whole rule as one sentence: "Every 30 min, from 9:00 AM, up to 5 sends." */
export function ruleSentence(s: Schedule, now?: Date): string {
  const parts = [repeatText(s, now)];
  if (s.driver === "app") parts.push(`when ${setByText(s)} sends it`);
  if (s.repeat !== "once") parts.push(limitText(s, now) ?? "until you end it");
  return `${parts.join(", ")}.`;
}

/** "You", the app that set it, or this chat (one it set for itself). */
export function setByText(s: Schedule): string {
  if (s.set_by.kind === "chat") return "This chat";
  return s.set_by.kind === "app" ? s.set_by.name || "An app" : "You";
}

export type From = { name: string; href?: string; mine: boolean; label?: string };

/** Who put a message there, from its `meta.sender`; null when the operator typed it. */
export function fromOf(sender: string | undefined, schedules: readonly Schedule[]): From | null {
  if (!sender) return null;
  if (sender.startsWith(SCHEDULE_SENDER)) {
    const s = schedules.find((x) => x.id === sender.slice(SCHEDULE_SENDER.length));
    if (!s) return { name: "A schedule", mine: false };
    if (s.set_by.kind === "you") return { name: "Scheduled", mine: true, label: nameOf(s) };
    if (s.set_by.kind === "chat") return { name: s.label || "This chat's schedule", mine: false };
    return { name: s.label || s.set_by.name || "An app", href: s.set_by.href ?? undefined, mine: false };
  }
  if (sender.startsWith("thread:")) return { name: "Another chat", mine: false };
  if (sender.startsWith("run:")) return { name: "Run", mine: false };
  if (sender.startsWith("agent:")) return { name: sender.slice(6), mine: false };
  if (sender === "platform" || sender === "person") return { name: "Workshop", mine: false };
  return { name: sender, mine: false };
}

/** A message as the transcript shows it. One someone else put there reads as
 *  theirs (the incoming side, with their name), never as the operator's own;
 *  one from the operator's own schedule stays his, marked scheduled. */
export function shownAs(msg: ChatMsg, schedules: readonly Schedule[]): ChatMsg {
  if (msg.role !== "user") return msg;
  const from = fromOf(msg.meta?.sender, schedules);
  if (!from) return msg;
  if (from.mine) return { ...msg, meta: { ...msg.meta, scheduled: from.label ?? "" } };
  return { ...msg, role: "assistant",
    meta: { ...msg.meta, sender_name: from.name, sender_href: from.href } };
}

export type Group = "active" | "paused" | "ended";

/** Active, paused, or ended (done: it ran out or was ended). */
export function groupOf(s: Schedule): Group {
  if (s.status === "active") return "active";
  return s.status === "paused" ? "paused" : "ended";
}

export type Upcoming =
  | { kind: "schedule"; s: Schedule }
  | { kind: "wake"; w: WakeUp };

const ms = (v?: string | null) => (v ? Date.parse(v) : NaN);

/** Active schedules and the chat's wake-ups, soonest first; a schedule with no
 *  time of its own (after each reply, or one an app sends) after the timed. */
export function upcomingOf(schedules: readonly Schedule[], wakeups: readonly WakeUp[]): Upcoming[] {
  const at = (u: Upcoming) => {
    const v = u.kind === "wake" ? ms(u.w.due_at) : ms(u.s.next_at);
    return Number.isNaN(v) ? Number.MAX_SAFE_INTEGER : v;
  };
  return [
    ...schedules.filter((s) => groupOf(s) === "active").map((s) => ({ kind: "schedule" as const, s })),
    ...wakeups.map((w) => ({ kind: "wake" as const, w })),
  ].sort((a, b) => at(a) - at(b));
}

/** Paused or ended schedules, the most recently changed first. */
export function inGroup(schedules: readonly Schedule[], group: Exclude<Group, "active">): Schedule[] {
  const recent = (s: Schedule) => ms(s.updated_at ?? s.last_sent_at ?? s.created_at) || 0;
  return schedules.filter((s) => groupOf(s) === group).sort((a, b) => recent(b) - recent(a));
}

/** When it sends next: "3:30 PM", "After the next reply", "When the app sends it". */
export function nextText(s: Schedule, now?: Date): string {
  if (s.next_at) return whenText(s.next_at, now);
  if (s.driver === "app") return "When the app sends it";
  return s.repeat === "after_reply" ? "After the next reply" : "Not set";
}

/** The line under a schedule's name in the list, by where it stands. */
export function rowMeta(s: Schedule, now?: Date): string {
  const sent = s.max_sends ? `${s.sent} of ${s.max_sends} sent` : "";
  const by = s.set_by.kind === "chat" ? "set by this chat"
    : s.set_by.kind === "app" ? `set by ${setByText(s)}` : "";
  const group = groupOf(s);
  const parts = group === "active"
    ? [repeatText(s, now), s.repeat !== "once" && s.next_at ? `next ${whenText(s.next_at, now)}` : "", sent]
    : group === "paused"
      ? [repeatText(s, now), s.why ?? "", sent]
      : [repeatText(s, now), s.why ?? `sent ${s.sent}`,
        s.last_sent_at ? `last ${whenText(s.last_sent_at, now)}` : ""];
  return [...parts, by].filter(Boolean).join(" · ");
}

export type Act = "send" | "toggle" | "edit" | "duplicate" | "end" | "rearm" | "again" | "delete";

/** What can be done to a schedule where it stands. An ended one is started
 *  over (Run again) or sent once more, never edited in place; one an app
 *  writes is never copied or sent by hand. */
export function actsOf(s: Schedule): Act[] {
  const ours = s.driver === "workshop";
  const acts: (Act | false)[] = groupOf(s) === "ended"
    ? [ours && "rearm", ours && "again", ours && "duplicate"]
    : [s.status === "active" && ours && "send", "toggle", "edit", ours && "duplicate", "end"];
  return [...acts.filter((a): a is Act => !!a), "delete"];
}

/** The words for an act on `s`. */
export function actLabel(a: Act, s: Schedule): string {
  if (a === "toggle") return s.status === "active" ? "Pause" : "Resume";
  return {
    send: "Send now", edit: "Edit", duplicate: "Duplicate", end: "End",
    rearm: "Run again", again: "Send again", delete: "Delete",
  }[a];
}

/** A send in its history: "Tue 3:00 PM", or that it is waiting to go out. */
export function sendText(send: { at: string; state: "sent" | "waiting" }, now?: Date): string {
  return send.state === "waiting" ? "Waiting to send" : whenText(send.at, now);
}
