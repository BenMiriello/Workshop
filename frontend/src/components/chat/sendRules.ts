import type { QueueWaiting, SendRule } from "../../lib/chat";

// The words for a queued message's send rule (backend services/send_rules), and
// the one conversion the composer needs: a local date-time field to a rule.

/** "3:00 PM" today, "Tue 3:00 PM" this week, else "Oct 31, 3:00 PM". */
export function whenText(iso: string, now: Date = new Date()): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const time = at.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  const sameDay = at.toDateString() === now.toDateString();
  if (sameDay) return time;
  const days = (at.getTime() - now.getTime()) / 86_400_000;
  if (days > 0 && days < 6) {
    return `${at.toLocaleDateString("en-US", { weekday: "short" })} ${time}`;
  }
  return `${at.toLocaleDateString("en-US", { month: "short", day: "numeric" })}, ${time}`;
}

/** "gpu" reads as "GPU"; a longer name stays as written. */
export function resourceText(names: readonly string[]): string {
  const words = names.map((n) => (n.length <= 3 ? n.toUpperCase() : n));
  return words.length ? words.join(" and ") : "the machine";
}

export function ruleText(when: SendRule, opts: {
  now?: Date;
  chatTitle?: (threadId: string) => string | undefined;
  resources?: readonly string[];
} = {}): string {
  switch (when.kind) {
    case "after_turn": return "Sends after this reply";
    case "at": return when.at ? `Sends at ${whenText(when.at, opts.now)}` : "Scheduled";
    case "usage_reset":
      return when.at
        ? `Sends when the usage limit resets, about ${whenText(when.at, opts.now)}`
        : "Sends when the usage limit resets";
    case "after_thread": {
      const title = when.thread_id ? opts.chatTitle?.(when.thread_id) : undefined;
      return title ? `Sends after "${title}" finishes` : "Sends after the other chat finishes";
    }
    case "resources_free":
      return `Sends when the ${resourceText(opts.resources ?? ["gpu"])} is free`;
    case "hold": return "Held until you send it";
    default: return "Waiting";
  }
}

export function waitingText(w: QueueWaiting, now?: Date): string {
  if (w.reason === "usage_limit") {
    return w.until
      ? `Waiting for the usage limit to reset at ${whenText(w.until, now)}.`
      : "Waiting for the usage limit to reset.";
  }
  return "Waiting.";
}

/** A `datetime-local` value (local wall time, no zone) as an `at` rule, or
 *  null when it is empty, unreadable, or already past. */
export function atRule(local: string, now: Date = new Date()): SendRule | null {
  if (!local) return null;
  const at = new Date(local);
  if (Number.isNaN(at.getTime()) || at.getTime() <= now.getTime()) return null;
  return { kind: "at", at: at.toISOString() };
}

/** The `datetime-local` value for a moment, in local wall time. */
export function localInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
