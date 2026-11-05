import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button } from "./controls";
import { useDialogFocus } from "./dialogFocus";
import { useOverlayHost } from "./OverlayHost";

// Sheet. A panel of settings that belongs beside the work rather than over it:
// a side panel on the right edge on a wide screen, a bottom sheet on a narrow
// one, so what it changes stays in view on a desktop and it stays in reach of a
// thumb on a phone. The head carries the title and a close button, the body
// scrolls, and the actions sit outside the scroll so they are always visible.
//
// Same keyboard and dismissal as Modal (Esc, scrim, focus trap, focus restored)
// and the same overlay layer, with its own geometry (`.ws-overlay--side`). It is
// for panels you work in, where the operator asked for one. A dialog, a
// picker or a decision is a Modal.
export function Sheet({
  open,
  onClose,
  title,
  children,
  actions,
  labelledBy = "ws-sheet-title",
  closeLabel = "Close",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children?: ReactNode;
  /** Buttons pinned under the body, outside its scroll. */
  actions?: ReactNode;
  labelledBy?: string;
  /** The close button's accessible name. */
  closeLabel?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const host = useOverlayHost();
  useDialogFocus(open, ref, onClose);

  if (!open) return null;

  return createPortal(
    <div
      className="ws-overlay ws-overlay--side"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={ref} className="ws-sheet" role="dialog" aria-modal="true"
        aria-labelledby={labelledBy}>
        <div className="ws-sheet__head">
          <h3 id={labelledBy} className="ws-sheet__title">{title}</h3>
          <Button variant="ghost" size="sm" icon aria-label={closeLabel} onClick={onClose}>
            <X size={16} aria-hidden />
          </Button>
        </div>
        {children != null && <div className="ws-sheet__body">{children}</div>}
        {actions != null && <div className="ws-sheet__actions">{actions}</div>}
      </div>
    </div>,
    host ?? document.body,
  );
}
