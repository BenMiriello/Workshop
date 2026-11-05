// The pure rules behind the chat list's GRID view: which layout the list is
// in, the plain text a card previews, and the date it prints. Beside
// `chatList` for the same reason that module exists: there is no DOM harness,
// so a rule has to live where a test can execute it.

export type ChatListView = "list" | "grid";

/** The stored view, read defensively: anything but "grid" is the list, so a
 *  missing or mangled value lands on the layout every surface had before. */
export function parseListView(raw: string | null | undefined): ChatListView {
  return raw === "grid" ? "grid" : "list";
}

/**
 * A message's markdown, reduced to the words a reader would see.
 *
 * The card previews the last message's first lines, and a raw snippet starts
 * with `## Plan` or a fence's backticks as often as with a sentence. Line
 * breaks between blocks are kept, because "the first three lines" is what the
 * card shows; blank lines collapse so a heading's gap does not spend one.
 * The input is at most the 300 characters the list route sends, so an
 * unclosed fence or link at the cut is normal and must not leak syntax.
 */
export function plainSnippet(md: string | null | undefined): string {
  if (!md) return "";
  const lines = md.replace(/\r\n?/g, "\n").split("\n").map((line) => {
    let s = line;
    if (/^\s*(```|~~~)/.test(s)) return "";
    if (/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(s)) return "";
    if (/^\s*([-*_]\s*){3,}$/.test(s)) return "";
    s = s.replace(/^\s{0,3}#{1,6}\s+/, "");
    s = s.replace(/^\s*(>\s*)+/, "");
    s = s.replace(/^\s*([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?/, "");
    s = s.replace(/!\[([^\]]*)\]\([^)]*\)?/g, "$1");
    s = s.replace(/\[([^\]]*)\]\([^)]*\)?/g, "$1");
    s = s.replace(/<\/?[a-zA-Z][^>]*>/g, "");
    s = s.replace(/`+/g, "");
    s = s.replace(/(\*\*|__|~~)(?=\S)([^]*?\S)\1/g, "$2");
    s = s.replace(/(^|[^\w*])[*_](?=\S)([^*_]*?\S)[*_](?![\w*])/g, "$1$2");
    s = s.replace(/^\s*\|(.*)\|?\s*$/, (_m, cells: string) => cells.split("|").map((c) => c.trim()).filter(Boolean).join(" "));
    return s.replace(/\s+/g, " ").trim();
  });
  return lines.filter(Boolean).join("\n");
}

/**
 * The date a card prints, at the resolution the grid is scanned at.
 *
 * The card sits under the same date heading the list uses, so it names the
 * DAY, not an age: today reads as the time, this year as month and day, and an
 * older chat carries its year. Off `updated_at`, the field the list orders by.
 */
export function cardDate(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const opts: Intl.DateTimeFormatOptions = d.getFullYear() === now.getFullYear()
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" };
  return d.toLocaleDateString([], opts);
}

/** "12 messages", or nothing where the count is absent or zero, so a card
 *  never claims a chat is empty on the strength of a field it was not sent. */
export function messageCount(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return "";
  return `${n} ${n === 1 ? "message" : "messages"}`;
}
