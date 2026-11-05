// The Read aloud control for a document's bar: reads what is selected inside
// the document, else the whole of it, and stops a reading it started. Hidden
// when there is no Workshop reader to ask (readAloud.ts). Given `within`, the
// reader knows where the text is on the page: it can mark the sentence being
// read, and a tap in the document reads from the sentence tapped.
import { useEffect, useRef, type RefObject } from "react";
import { Square, Volume2 } from "lucide-react";
import { Button } from "./controls";
import { useReadAloud } from "./readAloud";

export interface ReadAloudButtonProps {
  /** Who is asking, so the button knows the reading is its own. */
  source: string;
  /** The whole text, read when nothing is selected. Markdown is fine. */
  text: () => string;
  /** The document's element: a selection counts only inside it. */
  within?: RefObject<HTMLElement | null>;
  /** The selection when the page draws its own (an editor), else null. */
  selected?: () => string | null;
}

export function ReadAloudButton({ source, text, within, selected }: ReadAloudButtonProps) {
  const speech = useReadAloud(source);
  const picked = useRef<{ text: string; place: Element | Range | null } | null>(null);
  const whole = useRef(text);
  whole.current = text;
  const { place } = speech;
  useEffect(() => {
    const el = within?.current;
    return el && place ? place(el, source, () => whole.current()) : undefined;
  }, [within, place, source]);
  if (!speech.available) return null;

  // Taken at pointer-down: on a phone the tap clears the selection before the
  // click arrives.
  const take = () => {
    const root = within?.current ?? null;
    if (selected) {
      const t = selected();
      picked.current = t ? { text: t, place: root } : null;
      return;
    }
    const sel = window.getSelection();
    const range = sel && !sel.isCollapsed && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
    const t = range && (!root || root.contains(range.commonAncestorContainer)) ? sel!.toString().trim() : "";
    picked.current = t && range ? { text: t, place: range.cloneRange() } : null;
  };
  const label = speech.reading ? "Stop reading" : "Read aloud";
  return (
    <Button variant="ghost" icon aria-pressed={speech.reading} aria-label={label} title={label}
      onPointerDown={take} onMouseDown={(e) => e.preventDefault()}
      onClick={() => {
        if (speech.reading) { speech.stop(); return; }
        const p = picked.current;
        speech.read(p?.text || text(), p ? p.place : within?.current ?? null);
        picked.current = null;
      }}>
      {speech.reading ? <Square aria-hidden size={16} /> : <Volume2 aria-hidden size={16} />}
    </Button>
  );
}
