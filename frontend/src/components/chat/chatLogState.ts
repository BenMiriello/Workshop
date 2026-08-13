// Which placeholder, if any, the transcript shows in place of blocks.
//
// THE RULE THAT MATTERS: the empty state is a CLAIM ("this conversation has
// nothing in it"), and a claim may not be made while the first page of the log
// is still on the wire. Opening a thread used to do exactly that — the thread
// loader's own flag went false the moment the thread was selected, and the
// transcript's first fetch (`events?after=0`, the whole history, the largest
// payload in the chat) only STARTED after that commit and had no flag of its
// own. For one full round trip plus the parse, an open chat rendered "Ask
// anything, or tell me to change the plan." That is the "my chat opens blank"
// report, and it is why `loading` now covers hydration too.
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
  if (!input.empty) return null;
  if (input.loading) return "loading";
  if (input.error) return "error";
  if (input.inFlight) return null;
  return "empty";
}
