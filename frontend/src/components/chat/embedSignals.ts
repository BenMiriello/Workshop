import { useEffect } from "react";

// WHAT AN EMBEDDED CHAT TELLS THE PAGE FRAMING IT.
//
// An app that frames its chat (`/embed/chat/<scope>`) cannot see into it: DOM
// events do not cross a document boundary, so input in the chat reads to the
// app as nobody being there, and nothing says a turn is running. On 2026-10-03
// an app reloaded itself for a new build of its own, "idle" by its own count
// while the operator was reading and typing in its chat, and the chat reloaded
// with it in the middle of a turn.
//
// So the frame posts namespaced, versioned messages to its parent, the same
// shape as the ones a framed app posts up to the shell (pages/apps/tabs/
// AppScreen.tsx). Addressed to this page's own origin only: a host on another
// origin receives nothing.
//
//   workshop/v1/chatTurn   { running }   on load and whenever it changes
//   workshop/v1/activity   {}            on input, at most every ACTIVITY_MS
//   workshop/v1/chatThread { threadId }  the conversation the chat is on,
//                                        whenever it becomes a different one
//   workshop/v1/open       { id, path, service, inner }
//                                        a link in the chat to one of the host
//                                        app's own screens was clicked
//
// OPEN asks the host to show one of its own screens in place, so a link to it
// does not take the window away from the app and its chat. It is posted only
// for a path under the app's screen route, `/apps/<slug>/ui/<service>/...`:
// `service` is that segment, and `inner` is everything after it, the address
// inside the app (`#/library?show=12` for a hash router, `notes/3?x=1` for a
// path one, "" for its root). `path` is the whole Workshop address. A host that
// shows it replies to the frame, at once:
//
//   workshop/v1/opened     { id }
//
// and the frame does nothing more. With no reply within OPEN_ACK_MS the frame
// opens `path` the way it opens any other Workshop page (`leaveFrame`), so a
// host that does not listen behaves as before.
//
// CHATTHREAD is how a host that shows the chat at its own address keeps that
// address true. A chat opened as new has no id until its first message creates
// the conversation, and the frame's own `?thread=` cannot be read across the
// frame, so without it the host's address keeps saying "new" and a reload
// starts another chat. Posted for an id only: a blank chat has none to name.

export const CHAT_TURN_MSG = "workshop/v1/chatTurn";
export const CHAT_THREAD_MSG = "workshop/v1/chatThread";
export const ACTIVITY_MSG = "workshop/v1/activity";
export const OPEN_MSG = "workshop/v1/open";
export const OPENED_MSG = "workshop/v1/opened";
export const ACTIVITY_MS = 2000;
export const OPEN_ACK_MS = 600;

const INPUT_EVENTS = ["pointerdown", "keydown", "touchstart", "scroll", "wheel"] as const;

export function chatTurnMessage(running: boolean) {
  return { v: 1, type: CHAT_TURN_MSG, running };
}

export function chatThreadMessage(threadId: string) {
  return { v: 1, type: CHAT_THREAD_MSG, threadId };
}

export function activityMessage() {
  return { v: 1, type: ACTIVITY_MSG };
}

export type OpenTarget = { service: string; inner: string };

/** The host app's own screen `path` names, or null when it names anything
 *  else (another app, a Workshop page, the app's overview). */
export function inAppTarget(path: string, appSlug: string): OpenTarget | null {
  if (!appSlug) return null;
  const prefix = `/apps/${encodeURIComponent(appSlug)}/ui/`;
  if (!path.startsWith(prefix)) return null;
  const rest = path.slice(prefix.length);
  const cut = rest.search(/[/?#]/);
  const service = cut < 0 ? rest : rest.slice(0, cut);
  if (!service) return null;
  const tail = cut < 0 ? "" : rest.slice(cut);
  return { service: decodeURIComponent(service), inner: tail.startsWith("/") ? tail.slice(1) : tail };
}

export function openMessage(id: string, path: string, target: OpenTarget) {
  return { v: 1, type: OPEN_MSG, id, path, service: target.service, inner: target.inner };
}

/** Whether `data` is the host's reply to the open request `id`. */
export function isOpenedReply(data: unknown, id: string): boolean {
  if (!data || typeof data !== "object") return false;
  const d = data as { v?: unknown; type?: unknown; id?: unknown };
  return d.v === 1 && d.type === OPENED_MSG && d.id === id;
}

/** Whether an input at `now` is worth reporting, given the last report. */
export function activityDue(last: number, now: number): boolean {
  return now - last >= ACTIVITY_MS;
}

function framed(): boolean {
  try {
    return typeof window !== "undefined" && window.parent !== window;
  } catch {
    return false;
  }
}

function post(msg: object) {
  try {
    window.parent.postMessage(msg, window.location.origin);
  } catch {
    // A parent we cannot address is one that was not listening.
  }
}

let openSeq = 0;

/** Ask the host to show its own screen at `path`; `unhandled` runs when no
 *  host replies within OPEN_ACK_MS, or when this page is not framed. */
export function openInHost(path: string, target: OpenTarget, unhandled: () => void): void {
  if (!framed()) {
    unhandled();
    return;
  }
  const id = `open-${Date.now()}-${++openSeq}`;
  const onReply = (e: MessageEvent) => {
    if (e.origin !== window.location.origin || e.source !== window.parent) return;
    if (!isOpenedReply(e.data, id)) return;
    window.clearTimeout(timer);
    window.removeEventListener("message", onReply);
  };
  const timer = window.setTimeout(() => {
    window.removeEventListener("message", onReply);
    unhandled();
  }, OPEN_ACK_MS);
  window.addEventListener("message", onReply);
  post(openMessage(id, path, target));
}

/** Post the frame's turn state, its conversation and the operator's presence
 *  to the host page. Does nothing unless `enabled` and actually inside a frame. */
export function useEmbedSignals(enabled: boolean, turnRunning: boolean, threadId: string | null): void {
  useEffect(() => {
    if (!enabled || !framed()) return;
    post(chatTurnMessage(turnRunning));
  }, [enabled, turnRunning]);

  useEffect(() => {
    if (!enabled || !threadId || !framed()) return;
    post(chatThreadMessage(threadId));
  }, [enabled, threadId]);

  useEffect(() => {
    if (!enabled || !framed()) return;
    let last = 0;
    const onInput = () => {
      const now = Date.now();
      if (!activityDue(last, now)) return;
      last = now;
      post(activityMessage());
    };
    const opts = { capture: true, passive: true } as const;
    INPUT_EVENTS.forEach((e) => window.addEventListener(e, onInput, opts));
    return () => INPUT_EVENTS.forEach((e) => window.removeEventListener(e, onInput, opts));
  }, [enabled]);
}
