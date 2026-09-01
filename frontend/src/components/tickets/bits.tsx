// The small pieces every ticket view repeats: the key, the tag strip, the
// priority/effort meta, relative time. One definition each, so the board card,
// the table row and the phone list cannot drift into three ideas of what a
// ticket looks like.
import { Tag, catFor, timeAgo } from "@workshop/ui";
import { ticketKey, type Task } from "../../lib/tasks";
import { priorityLabel } from "../../lib/priority";

// Relative time, the unit every tracker's list column uses. An absolute
// timestamp in a list is precision nobody reads; the ticket page has the full
// one. Re-exported so the existing `ago` import at every call site keeps
// working; the formatting itself lives in lib/time (one definition, not
// sixteen).
export const ago = (iso?: string | null): string => timeAgo(iso, { suffix: false });

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

// Priority and effort as one mono fact, the way the design draws it
// ("high · deep").
//
// Both are frequently unset (priority on 45% of live tickets, effort on 70%),
// so this renders only what exists and nothing when neither does. A zero
// priority is "no priority": it is the store's default, carried by 79 tickets
// that never set one, so printing it would put a number nobody chose on most
// rows.
//
// THE BAND, NOT THE INTEGER. `lib/priority` exists because the stored number is
// the floor of a five-name band, so 80 and 99 are the same priority written two
// ways -- see its header for the day five agents filed carefully graded numbers
// and every distinction collapsed. The table converted at the edge and this did
// not, so the same ticket read "high" in one view of this page and "80" in the
// other. `priorityLabel` returns "" for none, which drops the bit entirely.
export function ticketMeta(task: Task): string {
  const bits: string[] = [];
  const band = priorityLabel(task.priority);
  if (band) bits.push(band);
  if (task.effort) bits.push(task.effort);
  return bits.join(" · ");
}

export function TicketMeta({ task }: { task: Task }) {
  const text = ticketMeta(task);
  return text ? <span className="ws-ticket-meta">{text}</span> : null;
}
