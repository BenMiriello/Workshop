// WHAT TO TELL THE OPERATOR WHEN A MESSAGE DID NOT SEND.
//
// This existed as `setErr(String(e))`, which put "TypeError: Failed to fetch"
// in a single slot above the composer — where it was truncated to 240
// characters, overwritten by the next error, and cleared by the next send. Three
// separate problems, and the smallest of them is the wording.
//
// The rule here fixes only the wording, and only for a failure that BELONGS TO A
// MESSAGE. Two things it must do:
//
//   1. Say what happened in a sentence about this chat, not in the vocabulary of
//      whatever threw. "TypeError: Failed to fetch" is the browser telling the
//      program something; it is not the app telling the operator anything.
//   2. Say what to do. Every failure routed through here has a Retry sitting
//      next to it, and the text names it.
//
// It never invents a diagnosis. An error it does not recognise is passed through
// with a sentence around it rather than being flattened into a generic line that
// would hide the one detail that mattered.

/** The bounded tail we are willing to show of an unrecognised error. Long enough
 *  for a real server message, short enough that an HTML error page cannot take
 *  over the transcript. */
const DETAIL_MAX = 160;

/** The words a 401/403 carries when it has nothing to add: HTTP's own reason
 *  phrases and FastAPI's stock detail. */
const BARE_AUTH_REASON =
  /^(unauthorized|forbidden|not authenticated|authentication required|access denied)\.?$/i;

function messageOf(e: unknown): string {
  if (typeof e === "string") return e.trim();
  if (e && typeof e === "object" && "message" in e) return String((e as Error).message).trim();
  return String(e ?? "").trim();
}

/** A leading HTTP status, which is the shape `shortHttpError` produces
 *  ("500 something", "Server unavailable (502). Retrying."). */
function statusOf(raw: string): number | null {
  const lead = raw.match(/^(\d{3})\b/);
  if (lead) return Number(lead[1]);
  const paren = raw.match(/\((\d{3})\)/);
  return paren ? Number(paren[1]) : null;
}

function trimDetail(raw: string, status: number | null): string {
  let d = raw;
  if (status !== null) d = d.replace(/^\d{3}\s*/, "").replace(/^Server unavailable \(\d{3}\)\.\s*Retrying\.?$/i, "");
  d = d.replace(/^(TypeError|Error):\s*/i, "").trim();
  if (!d) return "";
  return d.length > DETAIL_MAX ? `${d.slice(0, DETAIL_MAX - 1)}…` : d;
}

/** Is this failure one that goes away by itself?
 *
 *  YES for the two the deployment produces on its own, neither of which says
 *  anything about the message:
 *
 *    * the connection dropping (the Cloudflare tunnel re-dialling, a phone
 *      changing network);
 *    * 502/503/504, which is what the public edge returns while the backend is
 *      restarting — 32 restarts in the week to 2026-08-11, each a window about a
 *      second wide, and the edge's own pooled connection to the backend expires
 *      on the same 5s clock the backend closes it on.
 *
 *  A send that hits one of those is retried ONCE, silently, before the operator
 *  is told anything: it is the same message under the same client id, which the
 *  log dedupes, and the alternative is a dead bubble the operator has to press
 *  Retry on for a fault that had already cleared.
 *
 *  NO for anything the server answered with. A 400, a 403, a 500 out of the
 *  turn itself: those are about this message, and repeating it changes nothing. */
export function isTransientSendFailure(e: unknown): boolean {
  const raw = messageOf(e);
  if (/failed to fetch|networkerror|network request failed|load failed|err_internet/i.test(raw)) {
    return true;
  }
  const status = statusOf(raw);
  return status === 502 || status === 503 || status === 504;
}

/** The line shown under a message that never reached the server. */
export function sendFailureText(e: unknown): string {
  const raw = messageOf(e);
  // The browser's own wording for "the request never left, or never came back".
  // Firefox, Chrome and Safari each say it differently and none of them says it
  // to a person.
  if (/failed to fetch|networkerror|network request failed|load failed|err_internet/i.test(raw)) {
    return "Not sent: the server could not be reached. Check your connection, then press Retry.";
  }
  const status = statusOf(raw);
  const detail = trimDetail(raw, status);
  if (status !== null) {
    if (status === 502 || status === 503 || status === 504) {
      return "Not sent: the server is restarting or unreachable. Press Retry in a moment.";
    }
    if (status === 401 || status === 403) {
      // A REFUSAL THAT CAME WITH A REASON KEEPS ITS REASON. Not every 403 is a
      // session problem: the chat returns one, with a written sentence, when a
      // developer-mode scope is asked to run on an installation where the flag
      // is off. Five of those were reported to the operator as "this browser is
      // not signed in", which sent him to reload a page that was fine.
      //
      // The status's own reason phrase is not a reason. It is the word the
      // protocol already said, and showing it instead of the guidance below
      // trades a useful sentence for a worse one.
      if (detail && !BARE_AUTH_REASON.test(detail)) return `Not sent: ${detail}`;
      // A BARE REFUSAL STILL IS NOT A BARE SIGN-OUT. Keeping the reason fixed
      // the 403s that carry one, but the ones that carry nothing kept claiming
      // the session was gone, and that claim is only true for 401:
      //
      //   401 — there is no valid session. Reloading and signing in IS the fix.
      //   403 — the request was authenticated and refused anyway. Reloading
      //         cannot change the answer.
      //
      // A bare 403 is the shape an edge in front of the origin produces: the
      // Cloudflare tunnel fronting this deployment answers with an HTML page,
      // `shortHttpError` drops HTML bodies on purpose, and what reaches here is
      // a 403 with no detail at all. On 2026-08-11 one of those told the
      // operator he was not signed in; he reloaded, came straight back into a
      // working session, and reasonably concluded the app was lying about it.
      if (status === 403) {
        return "Not sent: the server refused this message. You are still signed in, so reloading will not help — this was refused upstream or by a rule, not by your session.";
      }
      return "Not sent: this browser is not signed in to the workshop. Reload the page, then press Retry.";
    }
    if (status >= 500) {
      return detail
        ? `Not sent: the server failed to start this turn (${status}: ${detail}). Press Retry.`
        : `Not sent: the server failed to start this turn (${status}). Press Retry.`;
    }
    return detail
      ? `Not sent: the server refused this message (${status}: ${detail}).`
      : `Not sent: the server refused this message (${status}).`;
  }
  return detail
    ? `Not sent: ${detail}. Press Retry.`
    : "Not sent: this message never reached the server. Press Retry.";
}
