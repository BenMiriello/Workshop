// What each page has said about itself, and the calls waiting on a page's answer.
// One window listener for the whole shell: messages arrive from any same-origin
// frame (an app's screen), keyed by the window that sent them, so a page that
// goes away takes its declaration with it.

import { useSyncExternalStore } from "react";
import {
  callMessage, isTakeover, parseAttach, parsePage, parseResult, parseShowChat, type AttachRequest, type PageDecl,
} from "./protocol";

const CALL_TIMEOUT_MS = 45000;

let decls = new Map<Window, PageDecl>();
const listeners = new Set<() => void>();
const waiting = new Map<string, (r: { ok: boolean; result?: unknown; error?: string }) => void>();
let onAttach: ((req: AttachRequest, from: Window) => void) | null = null;
let onDeclared: ((from: Window) => void) | null = null;
let onTakeover: ((from: Window) => void) | null = null;
let onShowChat: ((thread: string, from: Window) => void) | null = null;
let started = false;

function notify() {
  listeners.forEach((l) => l());
}

function onMessage(e: MessageEvent) {
  if (e.origin !== window.location.origin || !e.source) return;
  const from = e.source as Window;
  const page = parsePage(e.data);
  if (page) {
    decls = new Map(decls);
    for (const w of decls.keys()) if (w.closed) decls.delete(w);
    decls.set(from, page);
    notify();
    onDeclared?.(from);
    return;
  }
  const attach = parseAttach(e.data);
  if (attach) {
    onAttach?.(attach, from);
    return;
  }
  if (isTakeover(e.data)) {
    onTakeover?.(from);
    return;
  }
  const thread = parseShowChat(e.data);
  if (thread) {
    onShowChat?.(thread, from);
    return;
  }
  const result = parseResult(e.data);
  if (result) {
    const done = waiting.get(result.id);
    if (done) {
      waiting.delete(result.id);
      done(result);
    }
  }
}

export function startFramePages(handlers: {
  attach: (req: AttachRequest, from: Window) => void;
  declared: (from: Window) => void;
  takeover: (from: Window) => void;
  showChat: (thread: string, from: Window) => void;
}): () => void {
  onAttach = handlers.attach;
  onDeclared = handlers.declared;
  onTakeover = handlers.takeover;
  onShowChat = handlers.showChat;
  if (!started) {
    window.addEventListener("message", onMessage);
    started = true;
  }
  return () => {
    onAttach = null;
    onDeclared = null;
    onTakeover = null;
    onShowChat = null;
  };
}

export function declOf(w: Window | null): PageDecl | null {
  return w ? decls.get(w) ?? null : null;
}

export function useDeclOf(w: Window | null): PageDecl | null {
  return useSyncExternalStore(
    (fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    () => declOf(w),
    () => null,
  );
}

let seq = 0;

/** Ask the page in `w` to run one of its own tools. */
export function callFrame(w: Window, tool: string, args: unknown, access: string[] | null = null): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  seq += 1;
  const id = `call-${Date.now().toString(36)}-${seq}`;
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      waiting.delete(id);
      resolve({ ok: false, error: "The page did not answer in time." });
    }, CALL_TIMEOUT_MS);
    waiting.set(id, (r) => {
      window.clearTimeout(timer);
      resolve(r);
    });
    try {
      w.postMessage(callMessage(id, tool, args, access), window.location.origin);
    } catch {
      window.clearTimeout(timer);
      waiting.delete(id);
      resolve({ ok: false, error: "The page could not be reached." });
    }
  });
}
