// SOUND FROM ANYTHING NOT ON SCREEN IS WRITTEN DOWN.
//
// The native shell holds every page view that is not on screen and checks
// that a held page went quiet, letting go of one that did not
// (`PagePool+Quiet.swift`). This document hears every page's state, so it
// records the two things that should never happen: a page audible while its
// app is not the one on screen (the shell was told the wrong thing), and a
// page the shell had to let go. Each lands in `.runtime/client-media.jsonl`
// (backend/routes/client_media.py), so a leak is found in the record.

import { useEffect, useRef } from "react";
import { native, type PageState } from "@workshop/ui";

export type MediaFault = "audible-off-screen" | "released";

const REPEAT_MS = 30_000;

/** What a page's state says went wrong, with `shownApp` the app whose frame
 *  is on screen and `visible` whether the shell's document is; null when
 *  nothing did. A shell that does not name a page's app or its hold is not
 *  judged. */
export function mediaFault(s: PageState, shownApp: string | null, visible: boolean): MediaFault | null {
  if (s.closed) return s.closedBy === "released" ? "released" : null;
  if (!s.app || s.held === undefined || !s.playingAudio || s.muted || s.held) return null;
  return !visible || s.app !== shownApp ? "audible-off-screen" : null;
}

export function usePageMediaLog(shownApp: string | null) {
  const shown = useRef(shownApp);
  shown.current = shownApp;
  useEffect(() => {
    const bridge = native();
    if (!bridge?.has("pages")) return;
    const last = new Map<string, number>();
    return bridge.on<PageState>("pages:state", (s) => {
      const fault = mediaFault(s, shown.current, document.visibilityState === "visible");
      if (!fault) return;
      const key = `${fault}|${s.app}|${s.id}`;
      const now = Date.now();
      if (now - (last.get(key) ?? 0) < REPEAT_MS) return;
      last.set(key, now);
      fetch("/api/client-media", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event: fault, app: s.app ?? "", page: s.id.slice(0, 200), url: s.url.slice(0, 2000),
          shown_app: shown.current ?? "", visible: document.visibilityState === "visible",
          held: s.held ?? null, muted: s.muted ?? null, user_agent: navigator.userAgent.slice(0, 500),
        }),
      }).catch(() => {});
    });
  }, []);
}
