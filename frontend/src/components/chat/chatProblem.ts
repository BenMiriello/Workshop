// WHERE A CHAT ERROR IS SAID, decided once.
//
// The chat used to report failures three different ways: a bare red line above
// the composer, an egress `StateBlock`, and a per-message note in the log.
// Three idioms for one thing, so the same failure looked like a different class
// of problem depending on which code path produced it. There are two
// presentations now, and this module owns the choice between them:
//
//   "in-log"     the failure belongs to ONE message the operator sent, so it is
//                said under that message, next to a Retry that re-sends exactly
//                that message (UnsentNote). See the log-placement rule in
//                frontend/CLAUDE.md.
//   "chat-block" the failure is about the chat as a whole — a poll that stopped
//                landing, a thread that would not load, a blocked environment —
//                so it is a `StateBlock variant="error"` naming the endpoint
//                that failed. Nothing in the log is wrong; the chat is.
//
// The one interesting case is the ENVIRONMENT failure that arrives attached to
// a message. A turn that died because external network is blocked is recorded
// on that turn's reply, but it is not a fact about that reply: the next message
// will fail the same way, and the remedy (run it in your own terminal, or
// approve the host) has nothing to do with the text that was sent. It escalates
// to chat scope, which is what the build chat's egress banner has always done —
// that behaviour is the rule, written down and executable rather than inlined
// as a condition in the component.

import { isEgressFailure } from "./chatScope";

/** A failure that belongs to the CHAT rather than to a message.
 *
 *  `endpoint` is the API path that failed. It is a required part of the error
 *  state (rule 9 of frontend/CLAUDE.md) and every writer that made a request
 *  knows it; `null` is for the failures that made no request at all.
 *
 *  `retry` is present only where retrying is a real action — re-polling the
 *  log, re-reading the thread list, re-fetching the inbox. A "Retry" under
 *  "Couldn't rename this conversation" would be a button for something the
 *  operator does by pressing Rename again, so those carry their advice in the
 *  text instead of a control that re-runs a write behind their back. */
export type ChatProblem = {
  text: string;
  endpoint?: string | null;
  retry?: (() => void) | null;
  /** How loud to be. Defaults to `error` (an alert-triangle, role=alert): the
   *  vast majority of these ARE failures. `info` is for a message that is not a
   *  failure at all but has nowhere else to land, e.g. answering a retired slash
   *  command with where its job went. Rendered as a neutral Info line, no alarm. */
  tone?: "info" | "warn" | "error";
};

export type ProblemPlacement = "in-log" | "chat-block";

/** THE ROUTING RULE.
 *
 *  `messageId` identifies the message this failure is attached to — the client
 *  id of a send that did not land, or the key of the errored reply already
 *  sitting in the transcript. A failure that names a message is that message's,
 *  and is said under it. A failure that names none is the chat's.
 *
 *  The exception is the environment: a turn that died because external network
 *  is blocked did not fail because of the message that started it, and the next
 *  message fails the same way. That escalates to chat scope no matter which
 *  message it came in on. */
export function placementFor(f: {
  messageId?: string | null;
  text?: string | null;
}): ProblemPlacement {
  if (isEgressFailure(f.text)) return "chat-block";
  return f.messageId ? "in-log" : "chat-block";
}

/** Longest lead sentence we will put on the `StateBlock` title line. Past this
 *  the title stops being a title and becomes the body in bold. */
const TITLE_MAX = 90;
/** The bound on the detail line. A pathological body — a gateway's HTML error
 *  page — must not take over the panel. Every writer passes a sentence, so this
 *  should not fire; when it does, the ellipsis says it cut. */
const DETAIL_MAX = 400;

function clamp(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** Split a writer's sentence into the block's title and its detail.
 *
 *  Every writer of this channel says "Couldn't <do the thing>: <cause>. <what
 *  that means>", or a bare sentence pair. The first clause is what the operator
 *  needs at a glance and the rest is why — which is exactly the title/hint split
 *  `StateBlock` already has, so the text is cut where it was already written to
 *  be cut rather than being dumped whole into one line. */
export function problemBlock(p: { text?: string | null }): {
  title: string;
  detail: string | null;
} {
  const text = String(p.text ?? "").trim();
  if (!text) return { title: "Something went wrong in this chat.", detail: null };

  const colon = text.indexOf(": ");
  if (colon > 0 && colon <= TITLE_MAX) {
    return {
      title: `${text.slice(0, colon)}.`,
      detail: clamp(text.slice(colon + 2).trim(), DETAIL_MAX) || null,
    };
  }
  const stop = text.indexOf(". ");
  if (stop > 0 && stop <= TITLE_MAX) {
    return {
      title: text.slice(0, stop + 1),
      detail: clamp(text.slice(stop + 2).trim(), DETAIL_MAX) || null,
    };
  }
  if (text.length <= TITLE_MAX) return { title: text, detail: null };
  return { title: "Something went wrong in this chat.", detail: clamp(text, DETAIL_MAX) };
}

/** A failure, as one CLAUSE of a longer sentence.
 *
 *  Every one of these lines is built the same way: a stem, the failure, and
 *  what it means for the chat. "Couldn't compact: ${e}. The chat is unchanged."
 *  The failure already ends in a period whenever it came from `shortHttpError`
 *  ("Server unavailable (502). Retrying."), so the caller's own period landed
 *  beside it and the operator read "Retrying.. The chat is unchanged." on
 *  2026-09-04. Trimming the punctuation the sentence is about to supply is the
 *  whole of it; the words are untouched.
 *
 *  Trailing whitespace goes too, so a message with a stray newline does not put
 *  a line break in the middle of a sentence. */
export function problemClause(e: unknown): string {
  return String(e).trim().replace(/[.\s]+$/, "");
}
