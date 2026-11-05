// Opening and closing the chat beside the page: one toggle for the Chat button
// and for the keyboard shortcut.

import { useCallback, useEffect, useRef } from "react";
import { isMacLike } from "../../../lib/speech/shortcuts";
import { useAppFrames } from "../frames/AppFrames";
import { beside, useBeside } from "./besideStore";
import { lastBeside } from "./lastBeside";
import { usePageScope } from "./usePageScope";

export function useBesideToggle(): () => void {
  const b = useBeside();
  const frames = useAppFrames();
  const app = frames.frames.find((f) => f.key === frames.active)?.slug ?? null;
  const pageScope = usePageScope(app);
  return useCallback(() => {
    if (b.open) {
      beside.close();
      return;
    }
    // Back to the conversation last had beside this place, on this device;
    // else the place's own chat, where its own last conversation is picked up.
    const last = b.place ? lastBeside(b.place) : null;
    if (last) beside.open({ scope: last.scope, threadId: last.threadId });
    else if (b.scope === pageScope) beside.open();
    else beside.open({ scope: pageScope });
  }, [b.open, b.place, b.scope, pageScope]);
}

/** Ctrl+` everywhere, the key that opens the terminal in VS Code, and Ctrl+I
 *  on a Mac (it is the bookmarks sidebar in Firefox on Windows and Linux). */
export function isBesideKey(e: KeyboardEvent, mac: boolean): boolean {
  if (!e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return false;
  return e.code === "Backquote" || (mac && e.code === "KeyI");
}

export function useBesideShortcut(toggle: () => void, enabled: boolean): void {
  const ref = useRef({ toggle, enabled });
  ref.current = { toggle, enabled };
  useEffect(() => {
    const mac = isMacLike();
    let last = 0;
    const fire = () => {
      const now = Date.now();
      if (!ref.current.enabled || now - last < 250) return;
      last = now;
      ref.current.toggle();
    };
    const onKey = (e: KeyboardEvent) => {
      if (!isBesideKey(e, mac)) return;
      e.preventDefault();
      fire();
    };
    window.addEventListener("keydown", onKey, true);
    // The Mac app takes Ctrl+I in its menu, so a page view that has focus
    // still reaches it, and sends this instead of the key.
    window.addEventListener("workshop:beside", fire);
    // An app frame is its own document, and keys typed in it never reach this one.
    const relay = (frame: HTMLIFrameElement) => {
      try {
        frame.contentWindow?.removeEventListener("keydown", onKey, true);
        frame.contentWindow?.addEventListener("keydown", onKey, true);
      } catch {
        // Not ours to listen on.
      }
    };
    const onLoad = (e: Event) => {
      if (e.target instanceof HTMLIFrameElement) relay(e.target);
    };
    document.querySelectorAll("iframe").forEach(relay);
    document.addEventListener("load", onLoad, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("workshop:beside", fire);
      document.removeEventListener("load", onLoad, true);
    };
  }, []);
}
