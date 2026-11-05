import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { MessageSquarePlus, Volume2 } from "lucide-react";
import { Button } from "@workshop/ui";
import { belowSpot, type Box } from "../../../lib/speech/placement";
import { keepFocus } from "./press";

/** "Read aloud" and "Add to chat" for selected text, as one small toolbar.
 *  With a mouse it floats just below the selection (above it when there is no
 *  room), never over it. On touch it sits in the speech dock instead (`docked`):
 *  the phone draws its own Cut/Copy/Paste menu beside the selection, and
 *  anything floated there lands on top of it or under it. Either action is
 *  left out when null. */
export function SelectionRead({ box, docked, onRead, onAddToChat }: {
  box: Box; docked: boolean; onRead: (() => void) | null; onAddToChat: (() => void) | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 112, height: 28 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || docked) return;
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    if (width !== size.width || height !== size.height) setSize({ width, height });
  }, [size, docked]);
  let style: CSSProperties | undefined;
  if (!docked) {
    const vv = window.visualViewport;
    const view = vv
      ? { left: vv.offsetLeft, top: vv.offsetTop, width: vv.width, height: vv.height }
      : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
    const spot = belowSpot(box, size.width, size.height, view);
    style = { left: spot.left, top: spot.top };
  }
  return (
    <div
      ref={ref}
      className={"ws-speech-select" + (docked ? " ws-speech-select--docked" : "")}
      style={style}
      role="toolbar"
      aria-label="Selected text"
      data-dictation="off"
      onPointerDown={keepFocus}
      onMouseDown={keepFocus}
    >
      {onRead && (
        <Button size="sm" variant="ghost" onClick={onRead}>
          <Volume2 aria-hidden /> Read aloud
        </Button>
      )}
      {onAddToChat && (
        <Button size="sm" variant="ghost" onClick={onAddToChat}>
          <MessageSquarePlus aria-hidden /> Add to chat
        </Button>
      )}
    </div>
  );
}
