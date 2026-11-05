// KEEPING A HIDDEN APP FRAME QUIET.
//
// A kept frame that is not on screen stays loaded but must not be heard or
// seen playing. Holding a frame pauses every playing `<video>` and `<audio>`
// in its document and in every same-origin frame inside it, and pauses any
// that starts while the hold lasts (an autoplay, the next in a playlist, a
// timer). Releasing it lets media play again and restarts nothing, so what
// was playing stays paused until it is played, the same as a native page
// view (`pages.hide`). Players and audio contexts a script made outside the
// page are held too (./frameAudio.ts). A frame we cannot reach into keeps its
// own media; native page views are held by the shell (`shell.foreground`).

import { useEffect, useRef, useState } from "react";
import { holdFrameAudio } from "./frameAudio";
import type { KeptFrame } from "./keptFrames";

function reachable(win: Window): Document | null {
  try {
    return win.document;
  } catch {
    return null;
  }
}

/** `win`'s document and those of every same-origin frame inside it. */
function documents(win: Window): Document[] {
  const doc = reachable(win);
  if (!doc) return [];
  const out = [doc];
  for (let i = 0; i < win.frames.length; i += 1) out.push(...documents(win.frames[i]));
  return out;
}

const isMedia = (el: unknown): el is HTMLMediaElement =>
  !!el && typeof (el as HTMLMediaElement).pause === "function" && "paused" in (el as object);

/** Holds `win`'s media; call the result to release it. */
export function holdMedia(win: Window): () => void {
  const pause = (m: HTMLMediaElement) => { if (!m.paused) m.pause(); };
  const onPlay = (e: Event) => { if (isMedia(e.target)) pause(e.target); };
  const docs = documents(win);
  for (const doc of docs) {
    doc.querySelectorAll("video, audio").forEach((m) => pause(m as HTMLMediaElement));
    doc.addEventListener("play", onPlay, true);
  }
  const audio = docs.map((doc) => (doc.defaultView ? holdFrameAudio(doc.defaultView) : () => {}));
  return () => {
    for (const doc of docs) doc.removeEventListener("play", onPlay, true);
    audio.forEach((release) => release());
  };
}

function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const on = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}

/** Holds the media of every kept frame that is not on screen, and lets a
 *  frame's media go when it is again. */
export function useFrameMediaHolds(frames: readonly KeptFrame[], active: string | null,
  elements: Map<string, HTMLIFrameElement>) {
  const holds = useRef(new Map<string, { win: Window; release: () => void }>()).current;
  const visible = useDocumentVisible();
  useEffect(() => {
    const kept = new Set(frames.map((f) => f.key));
    for (const key of kept) {
      const win = elements.get(key)?.contentWindow;
      const held = holds.get(key);
      const onScreen = visible && key === active;
      if (held && (onScreen || held.win !== win)) {
        held.release();
        holds.delete(key);
      }
      if (!onScreen && win && !holds.has(key)) holds.set(key, { win, release: holdMedia(win) });
    }
    for (const [key, held] of holds) {
      if (!kept.has(key)) { held.release(); holds.delete(key); }
    }
  }, [frames, active, elements, holds, visible]);
}
