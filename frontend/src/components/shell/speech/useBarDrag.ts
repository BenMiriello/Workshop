// Dragging the dictation bar by its grip. It follows the pointer anywhere on
// screen; near its own spot it is held there, leaning toward the pointer, and
// glides out to the pointer once pulled past that distance, and back in when
// dragged near again (lib/speech/barSpot.ts). Let go
// while pulled in, it is home; anywhere else it stays.
// Where it was left is kept per device and held on screen as the window
// changes size.

import { useCallback, useEffect, useRef, useState, type PointerEvent, type RefObject } from "react";
import { clampBar, dragBar, parsePoint, type Point, type Size } from "../../../lib/speech/barSpot";
import { userStorage } from "../../../lib/storage";

const KEY = "speech.barSpot";
/** How long the bar glides into or out of its spot (dictation.css). */
const GLIDE_MS = 180;

const view = () => ({ width: window.innerWidth, height: window.innerHeight });

function sizeOf(el: HTMLElement): Size {
  const r = el.getBoundingClientRect();
  return { width: r.width, height: r.height };
}

export type Home = Point & Size;

/** `slot`: the bar's place in the dock while it is elsewhere, which is its
 *  own spot. */
export function useBarDrag(ref: RefObject<HTMLElement | null>, slot: RefObject<HTMLElement | null>) {
  const [spot, setSpot] = useState<Point | null>(() => parsePoint(userStorage.local.getItem(KEY)));
  const [home, setHome] = useState<Home | null>(null);
  const [pulled, setPulled] = useState(false);
  const [gliding, setGliding] = useState(false);
  const glide = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!spot) return;
    const keep = () => {
      if (!ref.current) return;
      const next = clampBar(spot, sizeOf(ref.current), view());
      if (next.left !== spot.left || next.top !== spot.top) setSpot(next);
    };
    keep();
    window.addEventListener("resize", keep);
    return () => window.removeEventListener("resize", keep);
  }, [spot, ref]);

  useEffect(() => () => clearTimeout(glide.current), []);

  const onPointerDown = useCallback((e: PointerEvent<HTMLElement>) => {
    const el = ref.current;
    if (!el || e.button !== 0) return;
    e.preventDefault();
    const r = el.getBoundingClientRect();
    const size = sizeOf(el);
    const own = spot ? slot.current?.getBoundingClientRect() : r;
    const base: Point = own ? { left: own.left, top: own.top } : { left: r.left, top: r.top };
    const start = { x: e.clientX, y: e.clientY, left: r.left, top: r.top };
    let now = !spot;
    let at: Point = { left: r.left, top: r.top };
    setHome({ ...base, ...size });
    setPulled(now);
    // An app frame under the pointer would take the moves for itself.
    document.body.classList.add("ws-bar-dragging");
    const move = (ev: globalThis.PointerEvent) => {
      const raw = { left: start.left + ev.clientX - start.x, top: start.top + ev.clientY - start.y };
      const next = dragBar(raw, base, now, size, view());
      if (next.pulled !== now) {
        now = next.pulled;
        setPulled(now);
        setGliding(true);
        clearTimeout(glide.current);
        glide.current = setTimeout(() => setGliding(false), GLIDE_MS);
      }
      at = next.at;
      setSpot(at);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.body.classList.remove("ws-bar-dragging");
      setPulled(false);
      if (!now) {
        setHome(null);
        setSpot(at);
        userStorage.local.setItem(KEY, JSON.stringify(at));
        return;
      }
      // Held, it springs back into its spot, then rests in the dock.
      userStorage.local.removeItem(KEY);
      setSpot(base);
      setGliding(true);
      clearTimeout(glide.current);
      glide.current = setTimeout(() => { setHome(null); setSpot(null); setGliding(false); }, GLIDE_MS);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }, [ref, slot, spot]);

  return { spot, dragging: home !== null, home, pulled, gliding, onPointerDown };
}
