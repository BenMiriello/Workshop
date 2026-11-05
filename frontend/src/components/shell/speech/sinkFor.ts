// Turning the destination into where words physically go right now (a sink,
// dictationWords.ts): the field itself when it is on a page, the same field
// found again when it came back as a new element, its chat's composer when that
// is on screen, the chat's draft when it is not, or the clipboard.

import { composerFor } from "../../../lib/speech/chatTargets";
import { CLIPBOARD, samePage, type Destination, type FieldPlace } from "./destination";
import type { Sink } from "./dictationWords";
import { findField, identityOf } from "./fieldFind";
import { documentShown } from "./frames";
import { captureTarget, type DictationTarget } from "./insertAtTarget";

export type Resolved = {
  sink: Sink;
  /** The destination, with a field found again swapped in. */
  into: Destination;
  waiting: boolean;
  /** The field left the page with nothing to know it by: words go to the
   *  clipboard from here on. */
  lost: boolean;
};

// A field in an app frame the shell keeps loaded but hidden is still
// connected, and still takes words.
const attached = (el: Element) => el.isConnected && !!el.ownerDocument.defaultView;

let composer: DictationTarget | null = null;

function composerTarget(el: HTMLElement): DictationTarget | null {
  if (composer?.el !== el) composer = captureTarget(el);
  return composer;
}

// The field's element is still the field: an editor kept while its page
// moves on to another note is that note's field now (destination.ts samePage).
const still = (d: FieldPlace, el: Element) => samePage(identityOf(el), d.identity);

function field(d: FieldPlace): Resolved {
  if (d.target && attached(d.target.el) && still(d, d.target.el)) {
    return { sink: { kind: "live", target: d.target, name: d.name }, into: d, waiting: false, lost: false };
  }
  const back = d.identity && findField(d.identity);
  const target = back && captureTarget(back);
  if (target) {
    const into = { ...d, target };
    return { sink: { kind: "live", target, name: d.name }, into, waiting: false, lost: false };
  }
  if (d.identity) return { sink: { kind: "wait", name: d.name }, into: d, waiting: true, lost: false };
  return { sink: { kind: "clip", name: CLIPBOARD.name }, into: CLIPBOARD, waiting: false, lost: true };
}

export function sinkFor(d: Destination): Resolved {
  if (d.kind === "field") return field(d);
  if (d.kind === "chat") {
    const el = composerFor(d.chat);
    const target = el && attached(el) && documentShown(el.ownerDocument) ? composerTarget(el) : null;
    const sink: Sink = target ? { kind: "live", target, name: d.name } : { kind: "draft", chat: d.chat, name: d.name };
    return { sink, into: d, waiting: false, lost: false };
  }
  return { sink: { kind: "clip", name: d.name }, into: d, waiting: false, lost: false };
}
