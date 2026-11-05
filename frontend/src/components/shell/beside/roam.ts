// WHICH CHAT IS BESIDE THE PAGE YOU ARRIVE AT. The rule, not the store.
//
// A conversation filed under Development or the Workshop root is not about one
// place, so it travels: it stays open and stays on the same conversation as the
// operator moves between apps. A conversation filed under an app, project or
// agent belongs to the place it was opened beside, so moving away shows what
// the other place had open, or closes the drawer when it had nothing, and the
// way back finds it as it was left.
//
// Development outranks the root. Arriving somewhere that has its own open
// conversation at the same level or higher, that one is shown instead.
//
// A new blank chat has no conversation to belong to yet, so it travels from
// wherever it was started and outranks what the place has open (which is kept
// for when the blank chat is gone). Once its first message makes a thread, the
// rules above apply to it like any other.

import { DEVELOPMENT_SCOPE, WORKSHOP_SCOPE, scopeForContext } from "../../../lib/scope";

export type Drawer = {
  open: boolean;
  /** As the drawer holds it: null means whatever scope the page asks for. */
  scope: string | null;
  threadId: string | null;
  /** The conversation is a new blank chat, which has no thread to name. */
  blank?: boolean;
  /** The scope the conversation is actually in, which `scope` does not say when it is null. */
  filed: string;
};

/** 3 Development, 2 the Workshop root, 1 anything narrower. */
export function chatLevel(contextKey: string | null | undefined): number {
  const scope = scopeForContext(contextKey);
  if (scope === DEVELOPMENT_SCOPE) return 3;
  return scope === WORKSHOP_SCOPE ? 2 : 1;
}

export const ROAMS_FROM = 2;

/** Both drawers are on the same open conversation, so showing one in place of
 *  the other changes nothing the chat has to start over for. */
export function sameConversation(a: Drawer, b: Drawer): boolean {
  return a.open && b.open && a.filed === b.filed && !!a.blank === !!b.blank && a.threadId === b.threadId;
}

export type Arrival = { drawer: Drawer; roamed: boolean };

export function arrive(from: Drawer, target: Drawer | undefined): Arrival {
  if (from.open && from.blank) return { drawer: { ...from, scope: from.filed }, roamed: true };
  const level = from.open ? chatLevel(from.filed) : 0;
  const own = target?.open ? chatLevel(target.filed) : 0;
  if (level >= ROAMS_FROM && own < level) {
    return { drawer: { ...from, scope: from.filed }, roamed: true };
  }
  if (target) return { drawer: target, roamed: false };
  return { drawer: { open: false, scope: null, threadId: null, filed: "general" }, roamed: false };
}
