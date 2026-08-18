// What a failed request is allowed to put on screen.
//
// A gateway/CDN 5xx (a Cloudflare 502 while the origin restarts, an nginx
// error page, a proxy timeout) answers with a full HTML page as the body.
// Concatenating that into an error string is how "Couldn't load apps" grew
// an unreadable, horizontally-overflowing dump of a webpage under it. FastAPI
// raises as `{"detail": "..."}`, so the sentence the backend actually wrote
// FOR the operator also needs unwrapping from around its own JSON.
//
// Since ADR 0023 (docs/decisions/0023-the-http-contract.md), the backend's
// own registered handler upgrades that string into an object --
// `{"detail": {"code", "message", "hint"}}` -- from ONE place, covering
// every `raise HTTPException` site in the app with no per-site edit. The
// edge shim (workshop_public_app.py) is a separate process that answers a
// handful of its own failures (backend unreachable, frontend not built) and
// still sends a bare string in `detail`, so both shapes reach the browser
// and both are unwrapped here.
//
// Rule: a body is only ever worth showing when it reads like a real,
// short API error message. HTML, non-JSON noise, or anything longer than
// ERROR_BODY_CAP is dropped in favour of the status and a short sentence.

/** Nothing built here ever puts more of a response body on screen than this. */
export const ERROR_BODY_CAP = 200;

/**
 * An HTTP failure that carries its status, so a caller can tell a transient
 * gateway hiccup from a real refusal without string-matching the message.
 * `message` stays the `shortHttpError` line, so `${e}` reads exactly as before.
 */
export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
}

/**
 * A failure a moment's wait can cure, rather than a real answer. A backend
 * restart is a pause, not a break: while it comes back up the edge has no
 * origin to reach and answers 502/503/504 for a second or two, and a fetch
 * that never got a response at all rejects as a TypeError. Everything else, a
 * 4xx or a 500, is a real answer the server chose to give and must not be
 * retried away.
 */
export function isTransientNetworkError(e: unknown): boolean {
  if (e instanceof HttpError) return e.status >= 502 && e.status <= 504;
  return e instanceof TypeError;
}

// Short, plain sentences for the statuses common enough to name. Anything
// else falls back to the response's own statusText, then to "request failed".
const STATUS_SENTENCES: Record<number, string> = {
  401: "Not signed in.",
  403: "Not allowed.",
  404: "Not found.",
  500: "The server errored.",
};

// FastAPI raises as `{"detail": "..."}`, so the sentence the backend wrote for
// the operator arrived wrapped in its own JSON and was shown that way. The
// refusal a chat gets for a developer-mode scope is a written sentence naming
// the flag; on screen it read `403 {"detail":"developer mode is off, ..."}`.
function unwrapDetail(body: string): string {
  if (!body.startsWith("{")) return body;
  try {
    const parsed = JSON.parse(body);
    const d = parsed?.detail;
    if (typeof d === "string" && d.trim()) return d.trim();
    // ADR 0023 shape: `detail` is `{code, message, hint}`. `message` is the
    // sentence the raise site wrote; show it the same way a plain string
    // detail always was.
    if (d && typeof d === "object" && typeof d.message === "string" && d.message.trim()) {
      return d.message.trim();
    }
  } catch {
    /* not JSON, or not ours: the raw body is still the best thing to show */
  }
  return body;
}

/**
 * Build a short, human error line from an HTTP failure.
 *
 * A 502/503/504 (the backend or the edge in front of it not answering)
 * always collapses to one retry sentence, no matter what the gateway's own
 * body says: quoting Cloudflare's HTML back at the operator names nothing he
 * can act on. Otherwise: a JSON `{"detail": "..."}` or short plain-text body
 * is shown after the status; an HTML body, a body over ERROR_BODY_CAP
 * characters, or an empty one falls back to a short sentence for the common
 * statuses, then the response's statusText, then "request failed".
 */
export function shortHttpError(status: number, statusText: string, body: string): string {
  const b = unwrapDetail((body || "").trim());
  const looksHtml = b.startsWith("<") || b.toLowerCase().includes("<!doctype");
  if (status >= 502 && status <= 504) return `Server unavailable (${status}). Retrying.`;
  const detail = !looksHtml && b && b.length <= ERROR_BODY_CAP ? b : "";
  const tail = detail || STATUS_SENTENCES[status] || statusText || "request failed";
  return `${status} ${tail}`.trim();
}
