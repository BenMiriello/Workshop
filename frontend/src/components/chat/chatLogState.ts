// Which placeholder, if any, the transcript shows in place of blocks.
//
// THE RULE THAT MATTERS: the empty state is a CLAIM ("this conversation has
// nothing in it"), and a claim may not be made while the first page of the log
// is still on the wire. Opening a thread used to do exactly that — the thread
// loader's own flag went false the moment the thread was selected, and the
// transcript's first fetch (`events?after=0`, the whole history, the largest
// payload in the chat) only STARTED after that commit and had no flag of its
// own. For one full round trip plus the parse, an open chat rendered "Ask
// anything." That is the "my chat opens blank" report, and it is why
// `loading` now covers hydration too.
//
// THE PLACEHOLDER IS A REGION THAT STANDS IN PLACE OF THE TRANSCRIPT, so it may
// only show when there is nothing real to render. It must NEVER stack ABOVE
// content. It used to (`if (loading) return "loading"` came first,
// unconditionally): on a NEW chat, the thread is created the instant the first
// message is sent, `hydrated` flips back to false while that new thread's own
// (empty) history is fetched, and a "Loading..." region then drew ABOVE the
// message already on screen — shoving it hundreds of pixels down the page and
// letting it snap back the moment hydration landed. That is the "my new
// message jumps down then back up" the operator reported at least seven times.
// Loading now wins only when there is nothing real underneath it.
//
// "NOTHING REAL" IS NOT "NO BLOCKS", because of the one case the 2026-08-14 fix
// was for: a pending send the outbox restored SYNCHRONOUSLY on thread select
// (a stale FAILED send from a previous session) made `blocks` non-empty before
// the log had said anything, so a single old failed message rendered alone,
// looking like the whole conversation. That block is not evidence the log
// loaded, so it does not count as content (see `noRealContent`): a thread
// holding only restored stubs is still "empty" for this purpose, and loading
// still wins over it. A committed message, an in-flight send, or a live turn is
// real content, and the region gives way to it.
//
// Pure so the rule can be executed instead of watched: chatLogState.test.ts.

import type { TranscriptBlock } from "./transcriptBlocks";

/** A block on screen only because the outbox restored it: a FAILED send from a
 *  previous session. It is not evidence the thread's log has loaded, so the
 *  loading region may still stand over a transcript that holds only these. An
 *  in-flight (`sending`) send is the operator's live action and DOES count as
 *  content. */
function isRestoredStub(b: TranscriptBlock): boolean {
  return b.kind === "pending" && b.status === "failed";
}

/** No block worth treating as loaded content: either nothing at all, or only
 *  restored stubs. `[].every` is true, so an empty transcript qualifies. */
export function noRealContent(blocks: TranscriptBlock[]): boolean {
  return blocks.every(isRestoredStub);
}

export type LogPlaceholder = "loading" | "error" | "empty" | null;

export function logPlaceholder(input: {
  /** No content worth showing: no blocks, or only restored stubs. See
   *  `noRealContent`; a plain `blocks.length === 0` is wrong here. */
  empty: boolean;
  /** The thread list or this thread's first page has not resolved. */
  loading: boolean;
  /** A load failed and the message is worth showing. */
  error: boolean;
  /** A run is in flight, so "nothing here" is false even with no blocks yet. */
  inFlight: boolean;
}): LogPlaceholder {
  // Real content on screen wins over every region: the log shows it and says
  // nothing over it. A failure beside it becomes the line above the composer,
  // not a region here (see the caller's `logShowsProblem`).
  if (!input.empty) return null;
  if (input.loading) return "loading";
  if (input.error) return "error";
  if (input.inFlight) return null;
  return "empty";
}
