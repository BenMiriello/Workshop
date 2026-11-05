// The ring around the field words are going into, while dictating: drawn by
// the speech layer over the field, so the field itself never changes, in the
// field's own layer of the page (stackLayer.ts). It is how the destination
// stays plain when the caret is somewhere else.

import { useEffect, useState } from "react";
import { visiblePart } from "../../../lib/speech/anchor";
import type { Box } from "../../../lib/speech/placement";
import { fixedOrigin, sameBox, viewBox } from "./fixedSpace";
import { anchorOf, visibleBox } from "./frames";
import { layerOf } from "./stackLayer";

// The layer changes only when a dialog or menu opens around the field.
const LAYER_EVERY_MS = 500;

function ringBox(el: HTMLElement): Box | null {
  if (!el.isConnected) return null;
  const at = anchorOf(el);
  const view = viewBox();
  const shown = visibleBox(at.el, at.box);
  const seen = shown && visiblePart(shown, {
    left: view.left, top: view.top, right: view.left + view.width, bottom: view.top + view.height,
  });
  if (!seen) return null;
  const o = fixedOrigin();
  return { left: seen.left - o.left, top: seen.top - o.top, right: seen.right - o.left, bottom: seen.bottom - o.top };
}

export function DestinationRing({ el }: { el: HTMLElement }) {
  const [box, setBox] = useState<Box | null>(null);
  const [z, setZ] = useState(0);
  useEffect(() => {
    let raf = 0;
    let layerAt = 0;
    const tick = () => {
      const next = ringBox(el);
      setBox((prev) => (next && sameBox(next, prev) ? prev : next));
      if (next && performance.now() - layerAt > LAYER_EVERY_MS) {
        layerAt = performance.now();
        setZ(layerOf(anchorOf(el).el));
      }
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [el]);
  if (!box) return null;
  return (
    <div
      className="ws-speech-ring"
      aria-hidden
      style={{ left: box.left, top: box.top, width: box.right - box.left, height: box.bottom - box.top, zIndex: z }}
    />
  );
}
