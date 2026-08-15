// The small pieces every ticket view repeats: the key, the tag strip, the
// priority/effort meta, relative time. One definition each, so the board card,
// the table row and the phone list cannot drift into three ideas of what a
// ticket looks like.
import { Tag, catFor } from "@workshop/ui";
import { ticketKey, type Task } from "../../lib/tasks";

// Relative time, the unit every tracker's list column uses. An absolute
// timestamp in a list is precision nobody reads; the ticket page has the full
// one.
export function ago(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo`;
  return `${Math.floor(months / 12)}y`;
}

/** The short mono handle for a ticket. See `ticketKey` for why six characters. */
export function TicketKey({ id }: { id: string }) {
  return <span className="ws-ticket-key">{ticketKey(id)}</span>;
}

/** A ticket's tags as one non-wrapping, horizontally scrolling line.
 *  Renders nothing at all when there are none: 95% of the live store carries no
 *  label, so a reserved empty slot would be blank space on nineteen rows out of
 *  twenty. */
export function TagStrip({ labels }: { labels?: string[] | null }) {
  const tags = (labels ?? []).filter(Boolean);
  if (tags.length === 0) return null;
  return (
    <span className="ws-tagstrip">
      {tags.map((t) => (
        <Tag key={t} hue={catFor(t)} variant="neutral">{t}</Tag>
      ))}
    </span>
  );
}

// Priority and effort as one mono fact, the way the design draws it ("60 · deep").
//
// Both are frequently unset (priority on 45% of live tickets, effort on 70%),
// so this renders only what exists and nothing when neither does. A zero
// priority is "no priority": it is the store's default, carried by 79 tickets
// that never set one, so printing it would put a number nobody chose on most
// rows.
export function ticketMeta(task: Task): string {
  const bits: string[] = [];
  if (typeof task.priority === "number" && task.priority !== 0) bits.push(String(task.priority));
  if (task.effort) bits.push(task.effort);
  return bits.join(" · ");
}

export function TicketMeta({ task }: { task: Task }) {
  const text = ticketMeta(task);
  return text ? <span className="ws-ticket-meta">{text}</span> : null;
}
