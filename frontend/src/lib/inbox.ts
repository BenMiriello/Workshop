// The inbox, as rules rather than as a component.
//
// ONE ENDPOINT, ONE HOOK. `/api/inbox` serves two surfaces: the chat's work
// drawer, filtered to the scope of the thread being read, and the global Inbox
// tab on /work, which is every scope at once. Those were two hand-rolled
// fetch/resolve/snooze implementations against the same endpoint, and they had
// already diverged (one had an "elsewhere" count, the other had a loading
// state). The state machine now lives once in `useInboxItems`
// (components/chat/InboxStrip); the arithmetic and the URL live here so they can
// be executed in a test.
//
// The three modes below are a real distinction, not a nullable chat id:
//
//   chat, with a thread   read that chat's filtered view
//   chat, with no thread  read NOTHING. Reading the unfiltered inbox here would
//                         count items belonging to conversations that are not
//                         on screen.
//   global                read every scope. This is a destination the operator
//                         opened on purpose, so nothing is out of view.

export type InboxMode = "chat" | "global";

export type InboxCounts = { notify: number; question: number; review: number };

export const INBOX_KINDS = ["question", "review", "notify"] as const;

export type InboxKind = (typeof INBOX_KINDS)[number];

/** Where to read, or null when there is nothing to read yet. */
export function inboxUrl(chatId: string | null, mode: InboxMode): string | null {
  if (mode === "global") return "/api/inbox";
  if (!chatId) return null;
  return `/api/inbox?chat_id=${encodeURIComponent(chatId)}`;
}

export function inboxTotal(counts: InboxCounts | null): number {
  if (!counts) return 0;
  return counts.question + counts.review + counts.notify;
}

const KIND_NOUN: Record<InboxKind, [string, string]> = {
  question: ["question", "questions"],
  review: ["review", "reviews"],
  notify: ["notification", "notifications"],
};

/** "1 question, 2 notifications". A badge that says WHAT is waiting, not just
 *  how much. Kinds with nothing in them are left out entirely. */
export function inboxSummary(counts: InboxCounts | null): string {
  if (!counts) return "";
  return INBOX_KINDS
    .filter((k) => (counts[k] ?? 0) > 0)
    .map((k) => `${counts[k]} ${KIND_NOUN[k][counts[k] === 1 ? 0 : 1]}`)
    .join(", ");
}

/** How many open items are outside the view being read. Zero in global mode by
 *  definition: the global inbox has no elsewhere.
 *
 *  Computed from the two COUNTS rather than from the rows on screen. The rows
 *  are capped by the server's page size and are removed optimistically the
 *  instant the operator answers one, either of which would otherwise be
 *  reported as items hiding in another scope. */
export function inboxElsewhere(
  counts: InboxCounts | null,
  globalCounts: InboxCounts | null,
  mode: InboxMode,
): number {
  if (mode === "global") return 0;
  return Math.max(0, inboxTotal(globalCounts) - inboxTotal(counts));
}

/** Items the server holds that this response did not carry, so a capped list
 *  can say so instead of looking complete. Never negative and never counts a
 *  row that is still on screen. */
export function inboxOverflow(counts: InboxCounts | null, shown: number): number {
  return Math.max(0, inboxTotal(counts) - shown);
}
