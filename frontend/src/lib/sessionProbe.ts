// WHO IS SIGNED IN, asked until the server actually answers.
//
// The boot read of `/api/auth/me` used to treat every failure as "not signed
// in": a 502 from the edge while the backend restarted, a dropped connection,
// a stall. So a phone that opened the app during a four-second restart landed
// on the Login screen with a perfectly good session cookie, and stayed there.
//
// Only the server SAYING there is no session is an answer, and it says it with
// a 401 (backend/deps_user.current_user). Nothing on this route answers 403;
// one that arrives came from an edge in front of the backend and says nothing
// about the session. Anything else, a 5xx, a timeout, a fetch that never got a
// response, is the server not answering, and the splash waits and asks again.

import { cappedBackoff } from "./retryPause";

/** What one read of `/api/auth/me` learned. */
export type MeResult<U> =
  | { kind: "authed"; user: U }
  | { kind: "anon" }
  | { kind: "unanswered" };

/** What an `/api/auth/me` status means. */
export function meOutcome(status: number): "authed" | "anon" | "unanswered" {
  if (status >= 200 && status < 300) return "authed";
  if (status === 401) return "anon";
  return "unanswered";
}

/** Ask until there is an answer. Resolves to the user, `null` for a real
 *  "not signed in", or `undefined` when `alive` turned false (the caller went
 *  away) before either. `wait` is where a caller pauses while the page is
 *  hidden; the probe never gives up on its own. */
export async function probeSession<U>(
  probe: () => Promise<MeResult<U>>,
  wait: (ms: number) => Promise<void>,
  alive: () => boolean,
): Promise<U | null | undefined> {
  for (let attempt = 0; alive(); attempt++) {
    const r = await probe();
    if (!alive()) return undefined;
    if (r.kind === "authed") return r.user;
    if (r.kind === "anon") return null;
    await wait(cappedBackoff(attempt));
  }
  return undefined;
}
