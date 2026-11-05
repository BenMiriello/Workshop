// The app frames the shell keeps alive (rules in ./keptFrames.ts).
//
// An app screen does not own its iframe. It asks for one with `useAppFrames`,
// and `AppFrameHost`, mounted once inside <main>, draws every kept frame: the
// shown one in the page's flow, the rest pinned where they last sat and hidden.
// Hidden by visibility, not removed and not `display: none`, so a document
// keeps its layout, its scroll positions and its size while it is away.
//
// A frame being hidden or shown is told so, the same namespaced, versioned
// shape a framed app posts up to the shell (pages/apps/tabs/AppScreen.tsx):
//
//   workshop/v1/visibility   { visible }
//
// A hidden frame's document never sees `visibilitychange`, so an app that
// keeps working in the background listens for this. A frame plays media only
// while it is on screen: the shown one while the shell's own document is
// visible. Every other frame's media is held (./frameMedia.ts), and a native
// shell holds the page views of every app but the shown one
// (`shell.foreground`, called from AppScreen). A native shell draws page views
// under this document, so the shown frame goes see-through while its app
// shows one (./usePageSlots.ts).

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type CSSProperties, type ReactNode,
} from "react";
import { watchDocuments } from "../sameOriginFrames";
import { trackFrameAudio } from "./frameAudio";
import { useFrameMediaHolds } from "./frameMedia";
import { usePageMediaLog } from "./pageMediaLog";
import { dropFrame, keepFrame, type FrameRequest, type KeptFrame } from "./keptFrames";
import { usePageSlots } from "./usePageSlots";
import { useShellOverlays } from "./useShellOverlays";

export const FRAME_VISIBILITY_MSG = "workshop/v1/visibility";

type Rect = { left: number; top: number; width: number; height: number };

type AppFrames = {
  frames: readonly KeptFrame[];
  active: string | null;
  show: (req: FrameRequest) => void;
  hide: (key: string) => void;
  drop: (key: string) => void;
  windowOf: (key: string) => Window | null;
  elements: Map<string, HTMLIFrameElement>;
};

const Ctx = createContext<AppFrames | null>(null);

export function useAppFrames(): AppFrames {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppFrames needs AppFramesProvider above it");
  return ctx;
}

export function AppFramesProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ frames: readonly KeptFrame[]; active: string | null }>(
    { frames: [], active: null },
  );
  const clock = useRef(0);
  const elements = useRef(new Map<string, HTMLIFrameElement>()).current;

  const show = useCallback((req: FrameRequest) => {
    clock.current += 1;
    const now = clock.current;
    setState((s) => ({ frames: keepFrame(s.frames, req, now), active: req.key }));
  }, []);
  const hide = useCallback((key: string) => {
    setState((s) => (s.active === key ? { ...s, active: null } : s));
  }, []);
  const drop = useCallback((key: string) => {
    setState((s) => {
      const frames = dropFrame(s.frames, key);
      return frames === s.frames ? s : { frames, active: s.active === key ? null : s.active };
    });
  }, []);
  const windowOf = useCallback((key: string) => elements.get(key)?.contentWindow ?? null, [elements]);

  const value = useMemo(
    () => ({ ...state, show, hide, drop, windowOf, elements }),
    [state, show, hide, drop, windowOf, elements],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

const SHOWN: CSSProperties = {
  flex: "1 1 auto", width: "100%", minWidth: 0, border: 0, background: "var(--card)",
};

function pinned(rect: Rect | undefined): CSSProperties {
  const r = rect ?? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
  return {
    position: "fixed", left: r.left, top: r.top, width: r.width, height: r.height,
    border: 0, visibility: "hidden", pointerEvents: "none",
  };
}

function tell(el: HTMLIFrameElement, visible: boolean) {
  el.contentWindow?.postMessage({ v: 1, type: FRAME_VISIBILITY_MSG, visible }, window.location.origin);
}

/** Every kept frame. Mounted once, inside <main>, so the shown one fills the
 *  app's page and the hidden ones never take up room in any page. */
export function AppFrameHost() {
  const { frames, active, elements } = useAppFrames();
  const rects = useRef(new Map<string, Rect>()).current;
  const told = useRef<string | null>(null);
  useFrameMediaHolds(frames, active, elements);
  useEffect(() => watchDocuments((doc) => (doc === document ? () => {} : trackFrameAudio(doc))), []);
  usePageMediaLog(frames.find((f) => f.key === active)?.slug ?? null);
  useShellOverlays(active, elements);
  usePageSlots(active, elements);

  useEffect(() => {
    const el = active ? elements.get(active) : undefined;
    if (!active || !el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) rects.set(active, { left: r.left, top: r.top, width: r.width, height: r.height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [active, elements, rects]);

  useEffect(() => {
    const prev = told.current;
    told.current = active;
    if (prev === active) return;
    const was = prev ? elements.get(prev) : undefined;
    if (was) {
      tell(was, false);
      // A hidden frame must not keep the keyboard: focus left in it sends every
      // key to a page nobody can see, and the shell's own shortcuts go dead.
      if (document.activeElement === was) was.blur();
    }
    const now = active ? elements.get(active) : undefined;
    if (now) tell(now, true);
  }, [active, elements]);

  useEffect(() => {
    const kept = new Set(frames.map((f) => f.key));
    for (const key of rects.keys()) if (!kept.has(key)) rects.delete(key);
  }, [frames, rects]);

  return (
    <>
      {frames.map((f) => {
        const shown = f.key === active;
        return (
          <iframe key={f.key} title={f.title} src={f.src}
            ref={(el) => { if (el) elements.set(f.key, el); else elements.delete(f.key); }}
            aria-hidden={shown ? undefined : true} tabIndex={shown ? undefined : -1}
            style={shown ? SHOWN : pinned(rects.get(f.key))} />
        );
      })}
    </>
  );
}
