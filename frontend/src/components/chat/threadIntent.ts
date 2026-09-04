// "OPEN THIS CONVERSATION" IS AN EVENT, NOT A VALUE.
//
// Picking a chat in the toolbar's recent list navigates to
// `/chat/<context_key>?thread=<id>`, and everything downstream of that used to
// key off the VALUE the URL carries: `initialThreadId` was a dependency of the
// thread resolver and of the ref that arms it. That works exactly as long as
// the router's remembered `?thread=` is the conversation actually on screen,
// and there are four ordinary ways for it not to be. `newChat`, `adoptThread`,
// `adoptThreadId` and the resolver's own fallback selection all move the open
// thread WITHOUT routing -- deliberately, because none of them is a link the
// operator clicked -- and the address bar is put back in step afterwards with a
// raw `history.replaceState`, which the router never sees.
//
// So the router can be sitting on `?thread=B` while the chat is on something
// else. Picking B out of the list then navigates to a URL whose `thread` value
// is the string the router already holds: React re-renders, `initialThreadId`
// is `"B" === "B"`, no dependency changed, the resolver never runs, and the
// click does NOTHING. Measured, not theorised: 78 of this installation's 131
// conversations share one context key, so the overwhelmingly common pick is a
// same-path, search-only navigation, which is precisely the case a value
// comparison cannot see.
//
// The fix is to stop asking "is this a different thread?" and ask "is this a
// different NAVIGATION?". React Router mints a fresh `location.key` on every
// push, including a push to a byte-identical URL (`createLocation` ->
// `createKey()`), so the key IS the event. This module is the one place that
// rule is written down, and its test is what stops the next refactor from
// quietly reverting to a value comparison.

/**
 * The dependency that means "the operator asked to open a conversation".
 *
 * Pass BOTH parts. `navKey` alone would miss a thread change that arrives
 * without a navigation (a redirect that rewrites the URL in place); the thread
 * id alone is the bug described above. Together they change whenever either
 * reason to re-resolve is present, and only then.
 *
 * `navKey` is `useLocation().key`. Callers that have no router location (the
 * chat dock, mounted inside another page) pass `undefined`, which degrades to
 * exactly the value comparison this replaces -- correct there, because nothing
 * navigates underneath them.
 */
export function linkIntentKey(
  navKey: string | undefined,
  threadId: string | undefined,
): string {
  return `${navKey ?? ""}|${threadId ?? ""}`;
}
