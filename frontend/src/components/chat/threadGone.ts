// IS THIS CONVERSATION GONE, OR DID WE JUST FAIL TO ASK?
//
// Those are different answers and the chat used to treat them as the same one.
// The thread resolver read the open conversation's settings with
// `chatApi.getThread(target).catch(() => null)` and, on `null`, called
// `setActiveId(null)`: the chat dropped to a blank new chat, the transcript
// emptied, and when the thread was picked up again every message in it was
// rendered fresh. That is the reset the operator described on 2026-09-04 --
// "the chat will reset, and then all of the messages will reanimate and load in
// rapid succession, and it looks crazy."
//
// A 502 in a restart window produced exactly that `null`, and so did a fetch
// that never got a response at all. Neither of them is the server saying the
// thread is not there. The only failures that are:
//
//   404  the server looked and there is no such thread
//   403  the server looked and it is not this account's
//
// Everything else -- 502/503/504 while the backend comes back up, a 500, a
// TypeError from a fetch that never landed -- means nothing was learned, and
// the honest response to learning nothing is to change nothing. Say so on the
// error line and leave the conversation where it is.
//
// Deliberately NOT "is this transient": that question has an owner already
// (`isTransientNetworkError` in the kit, which decides what is worth retrying)
// and it answers `false` for a 500, which would drop the chat on a backend
// bug. This asks the other question, and only a positive answer from the
// server counts.

import { HttpError } from "@workshop/ui";

/** Statuses that are the server ANSWERING that this thread is not available to
 *  this reader. Exported because the set is the rule: widening it is how a
 *  transient failure would silently start blanking the chat again. */
export const THREAD_GONE_STATUSES: readonly number[] = [403, 404];

/** Did this failure prove the thread is not there (or not ours)? */
export function threadIsGone(e: unknown): boolean {
  return e instanceof HttpError && THREAD_GONE_STATUSES.includes(e.status);
}
