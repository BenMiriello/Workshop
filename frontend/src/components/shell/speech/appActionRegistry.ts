// The actions pages and apps declare by voice (the kit's spokenActions.ts),
// kept with the document that declared each. An action applies only while its
// document is on screen: the shell keeps a left app's frame loaded but hidden
// (frames/AppFrames.tsx), and what it declared must not be heard meanwhile.
// Commands lists them under the name of the place they belong to, the app a
// frame serves or the page the shell is on, found from the shell's own list of
// places, so no app is named here.
//
// Each registration gets an id of its own (`app:<n>`), which is what the live
// socket hears it as (spokenWire.ts) and can never be a built-in's. What they
// do when heard is appActions.ts.

import type { SpokenAction, SpokenActionsHost } from "@workshop/ui";
import type { LiveAction } from "../../../lib/speech/liveWire";
import { openPlaces } from "../../../lib/speech/openPlaces";
import { pageFor, placeFor } from "../places";
import { wireOf } from "./appActionsCore";
import { documentShown } from "./frames";

export type AppEntry = { id: string; doc: Document; action: SpokenAction };

const entries = new Map<string, AppEntry>();
const listeners = new Set<() => void>();
let next = 0;
let applied: AppEntry[] = [];
let observer: MutationObserver | null = null;
let queued = false;

const live = (e: AppEntry) => !!e.doc.defaultView;

function check(declared = false): void {
  queued = false;
  for (const [id, e] of entries) if (!live(e)) entries.delete(id);
  const now = [...entries.values()].filter((e) => documentShown(e.doc));
  if (!declared && now.length === applied.length && now.every((e, i) => e === applied[i])) return;
  applied = now;
  listeners.forEach((fn) => fn());
}

function soon(): void {
  if (queued) return;
  queued = true;
  requestAnimationFrame(() => check());
}

// A frame is shown or hidden by its style (and the shell's own pages by their
// route, which unmounts them), so while anything is declared the shell's
// attributes are watched and the list checked once per frame drawn.
function watch(): void {
  if (entries.size && !observer) {
    observer = new MutationObserver(soon);
    observer.observe(document.body, {
      subtree: true, attributes: true, attributeFilter: ["style", "class", "hidden", "inert", "aria-hidden"],
    });
  } else if (!entries.size && observer) {
    observer.disconnect();
    observer = null;
  }
}

export const appActionsHost: SpokenActionsHost = {
  register(actions, doc) {
    const ids = actions.map((action) => {
      const id = `app:${++next}`;
      entries.set(id, { id, doc, action });
      return id;
    });
    doc.defaultView?.addEventListener("pagehide", () => requestAnimationFrame(() => check(true)), { once: true });
    watch();
    check(true);
    return () => {
      ids.forEach((id) => entries.delete(id));
      watch();
      check(true);
    };
  },
};

/** The declared actions that apply now, in the order they were declared. */
export function appActionsNow(): readonly AppEntry[] {
  return applied.filter((e) => live(e) && documentShown(e.doc));
}

export function appAction(id: string): AppEntry | null {
  const e = entries.get(id);
  return e && live(e) && documentShown(e.doc) ? e : null;
}

/** Call `fn` whenever the actions declared, or the ones that apply, may
 *  have changed. */
export function subscribeAppActions(fn: () => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** What the live socket is told: every action declared on a page still
 *  open, shown or not, so going back to one does not retrain what loose
 *  wordings are understood by. Which apply is said apart (spokenWire.ts). */
export function appActionsWire(): LiveAction[] {
  return [...entries.values()].filter(live).map((e) => wireOf(e.id, e.action));
}

/** The frame element in the shell's own document that `doc` sits in, or null
 *  for the shell's document. */
function shellFrame(doc: Document): Element | null {
  let frame: Element | null = null;
  for (let win: Window | null = doc.defaultView; win && win !== window; ) {
    try { frame = win.frameElement; } catch { return frame; }
    if (!frame) return null;
    win = frame.ownerDocument.defaultView;
  }
  return frame;
}

// An app's screen is framed from its proxy path, and its place in the shell
// starts with the same slug.
const SERVED = /\/base-apps\/run\/([^/]+)\//;

/** The name Commands lists `doc`'s actions under. */
export function placeLabel(doc: Document): string {
  const places = openPlaces.get();
  if (!places.length) return "This page";
  const frame = shellFrame(doc);
  const slug = frame ? SERVED.exec(frame.getAttribute("src") ?? "")?.[1] : null;
  const served = slug ? placeFor(places, `/apps/${slug}`) : null;
  if (served?.kind === "app") return served.name;
  const here = placeFor(places, location.pathname);
  if (frame || here.kind === "app") return here.name;
  return pageFor(here, location.pathname)?.label ?? here.name;
}
