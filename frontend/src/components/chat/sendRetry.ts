import { TRANSIENT_BACKOFF_MS } from "@workshop/ui";
import { isRequestTimeout } from "../../lib/requestTimeout";
import { isTransientSendFailure } from "./sendFailure";

// POSTING A MESSAGE THROUGH A BLIP, and reading the log back after it.
//
// Every send carries a client id, and the server's log and queue are both
// idempotent on it (chat_log.append_message_sync, chat_queue.enqueue), so
// posting the same message again after a 502 or a dropped connection cannot
// record it twice. That covers the TRANSCRIPT. It does not cover a turn that is
// already running: `build_streaming.inject` writes the text to the turn's input
// whether or not the log already held it, so a second POST of a message the
// first one delivered would put it in front of the model twice.
//
// A 502 or a refused connection never reached the backend, so re-posting is
// safe. A missed deadline is the one ambiguous case: the backend may have taken
// the message and simply been slow to say so. So after a timeout the caller is
// asked whether the message landed, from the log, before anything is posted
// again.

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The answer when a timed-out send turned out to have landed after all. */
export const LANDED = Symbol("landed");

/** Post, and post again under the same client id while the failure is
 *  transient, on the kit's restart schedule. `landed` is asked after a timeout
 *  only; when it says yes, the server has the message and nothing is re-sent. */
export async function sendWithRetry<T>(
  attempt: () => Promise<T>,
  opts: {
    landed?: () => Promise<boolean>;
    backoffMs?: readonly number[];
    wait?: (ms: number) => Promise<void>;
  } = {},
): Promise<T | typeof LANDED> {
  const backoff = opts.backoffMs ?? TRANSIENT_BACKOFF_MS;
  const wait = opts.wait ?? sleep;
  for (let tries = 0; ; tries++) {
    try {
      return await attempt();
    } catch (e) {
      if (isRequestTimeout(e) && opts.landed && (await opts.landed().catch(() => false))) {
        return LANDED;
      }
      if (!isTransientSendFailure(e) || tries >= backoff.length) throw e;
      await wait(backoff[tries]);
    }
  }
}

/** Read the log until a read lands, on the kit's restart schedule. The read
 *  after a send is what turns the pending bubble into the committed message;
 *  left to the next poll, one failed read held it pending for a minute. */
export async function syncUntilRead(
  sync: () => Promise<boolean>,
  backoffMs: readonly number[] = TRANSIENT_BACKOFF_MS,
  wait: (ms: number) => Promise<void> = sleep,
): Promise<boolean> {
  for (let i = 0; ; i++) {
    if (await sync()) return true;
    if (i >= backoffMs.length) return false;
    await wait(backoffMs[i]);
  }
}
