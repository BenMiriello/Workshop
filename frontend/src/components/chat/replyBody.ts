import type { ChatMsg } from "../../lib/chat";

// What, if anything, an assistant reply puts in a bubble.
//
// Two rules, both of which exist because a turn can END WITHOUT SAYING
// ANYTHING, and there are several different ways that happens.

/** Why a turn produced no text, said in one plain sentence.
 *
 *  A bubble with nothing in it is the worst of the three possible renderings: it
 *  says something happened without saying what, and reads as a rendering bug
 *  rather than as a turn that ended early. `timeout` already carries a tag above
 *  the body, so it only needs to answer the question that tag leaves open. The
 *  last case is the genuinely unexplained one, and it says so.
 *
 *  A STOPPED TURN SAYS NOTHING HERE. It is already reported on the quiet line
 *  under the reply, and it used to be said three times over: a yellow border, a
 *  tag above the body, and a sentence inside it. */
export function noReplyText(m: ChatMsg): string {
  if (m.meta?.timeout) return "No reply was written before the time limit.";
  if (m.meta?.stopped) return "";
  return "No response. The turn ended without sending a message.";
}

/** Does this message have a bubble at all?
 *
 *  Stopping a turn before it wrote anything leaves a reply with no text, no
 *  segments, and (by the rule above) no explanatory sentence either. Rendering
 *  the bubble anyway put an EMPTY BOX above the "user interrupted" line, which
 *  reads as a rendering fault rather than as a turn the operator ended. The line
 *  alone is the whole treatment.
 *
 *  Everything else keeps its bubble, including an errored turn with no text —
 *  an error is still something that happened, and it has its own wording. */
export function hasBubble(m: ChatMsg): boolean {
  if (m.role !== "assistant") return true;
  if (m.content.trim()) return true;
  if (m.meta?.segments?.length) return true;
  if (m.meta?.error || m.meta?.timeout) return true;
  return noReplyText(m) !== "";
}
