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
