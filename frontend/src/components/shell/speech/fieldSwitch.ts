// Moving dictation to another field without the bar: a focused field that is
// not where words go offers "Dictate here" (the chip), and a second press in
// it moves the words there, once nothing has been said for a while, so a
// press made to fix a word while still talking does not.

import { focused } from "./insertAtTarget";
import { destinationFor, samePlace, type ChatPlace, type Destination, type FieldPlace } from "./destination";
import { sinkFor } from "./sinkFor";

const QUIET_MS = 5000;

/** The place a focused element offers, when words are not going there. */
export function hereOf(el: Element | null, into: Destination | null): FieldPlace | ChatPlace | null {
  const d = destinationFor(el);
  return d && !samePlace(d, into) ? d : null;
}

/** Whether a press at `target` moves dictation to `here`: it is in that
 *  field, the field already had focus, and nothing was said since `spokeAt`
 *  for a while. */
export function pressMoves(here: Destination | null, target: Node | null, spokeAt: number, now = Date.now()): boolean {
  const sink = here ? sinkFor(here).sink : null;
  const el = sink?.kind === "live" ? sink.target.el : null;
  return !!el && !!target && el.contains(target) && focused(el) && now - spokeAt >= QUIET_MS;
}
