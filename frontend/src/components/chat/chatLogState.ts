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
// LOADING IS CHECKED FIRST, BEFORE EMPTINESS. It used to be the other way
// round — "if there are blocks, show nothing, whatever `loading` says" — on
// the reasoning that content on screen speaks for itself. It does not when the
// only "content" is a pending send the outbox restored SYNCHRONOUSLY, on
// thread select, while the actual history is still loading asynchronously
// (useChatThread's `adoptSettings` → `outboxRef.current.bind`, versus
// useThreadLog's async fetch). `empty` was `blocks.length === 0`, and a
// restored pending block made that false before the log had said anything at
// all — so a single old failed message rendered ALONE, with no loading
// indicator, looking like the whole conversation rather than like a page still
// in flight. Reported 2026-08-14. `loading` already means "this thread has not
// hydrated yet" (see useThreadLog's `hydrated`); checking it first fixes this
// at the root instead of teaching this function to recognize one particular
// KIND of block. Once loading clears, a real empty conversation still reaches
// "empty" exactly as before — this only changes what happens while the answer
// is not settled yet.
//
// Pure so the rule can be executed instead of watched: chatLogState.test.ts.

export type LogPlaceholder = "loading" | "error" | "empty" | null;

export function logPlaceholder(input: {
  /** No transcript blocks to render. */
  empty: boolean;
  /** The thread list or this thread's first page has not resolved. */
  loading: boolean;
  /** A load failed and the message is worth showing. */
  error: boolean;
  /** A run is in flight, so "nothing here" is false even with no blocks yet. */
  inFlight: boolean;
}): LogPlaceholder {
  if (input.loading) return "loading";
  if (!input.empty) return null;
  if (input.error) return "error";
  if (input.inFlight) return null;
  return "empty";
}
