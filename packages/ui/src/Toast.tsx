import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef,
  useState, type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cx } from "./cx";
import { statusClass, type StatusKind } from "./status";

// Transient notifications. Deterministic structure, so a stack of them reads as
// one thing rather than as a pile of differently shaped cards:
//
//   [icon] Title                    [x]   row 1, one line, fixed height
//   [thumb] one line of context           row 2, thumbs 48px, text clamped
//   meta                                  row 3, optional, small
//
// Bottom right on desktop, bottom on mobile. Lingers, and the timer pauses on
// hover or focus, so a toast can never expire while it is being read. It PAUSES
// rather than restarts: giving back the full linger on every pointer pass meant
// a toast under a resting cursor never expired at all, which is what "the toasts
// stay and never go away" turned out to be.

export interface ToastSpec {
  id?: number;
  kind?: StatusKind;
  title: string;
  text?: string;
  meta?: string;
  /** Small square pictures, already resolved to URLs by the caller. */
  thumbs?: string[];
  icon?: ReactNode;
  /** Clicking the body does this. The close button never triggers it. */
  onOpen?: () => void;
  /** Milliseconds on screen. 0 keeps it until it is dismissed. */
  linger?: number;
}

interface Live extends ToastSpec { id: number }

const LINGER_MS = 6000;
//: Past this many, the rest collapse into one line. Three is what fits above
//: the fold on the narrow viewport without covering the app.
const STACK = 3;

interface ToastApi {
  toast: (spec: ToastSpec) => number;
  dismiss: (id: number) => void;
}

const Ctx = createContext<ToastApi | null>(null);

/** Show a toast. Outside a `ToastHost` this is a no-op rather than a crash:
 *  a notification is never the point of the thing that raised it. */
export function useToast(): ToastApi {
  return useContext(Ctx) ?? { toast: () => 0, dismiss: () => {} };
}

export function ToastHost({ children }: { children?: ReactNode }) {
  const [items, setItems] = useState<Live[]>([]);
  const [showAll, setShowAll] = useState(false);
  const next = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((list) => list.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((spec: ToastSpec) => {
    const id = spec.id ?? next.current++;
    setItems((list) => [...list.filter((t) => t.id !== id), { ...spec, id }]);
    return id;
  }, []);

  const api = useMemo(() => ({ toast, dismiss }), [toast, dismiss]);

  const newest = [...items].reverse();
  const shown = showAll ? newest : newest.slice(0, STACK);
  const hidden = newest.length - shown.length;

  return (
    <Ctx.Provider value={api}>
      {children}
      {typeof document !== "undefined" && items.length > 0 && createPortal(
        <div className="ws-toasts" role="region" aria-label="Notifications">
          {shown.map((t) => (
            <Toast key={t.id} spec={t} onClose={() => dismiss(t.id)} />
          ))}
          {hidden > 0 && (
            <button type="button" className="ws-toasts__more"
              onClick={() => setShowAll(true)}>
              {hidden} more
            </button>
          )}
        </div>,
        document.body)}
    </Ctx.Provider>
  );
}

function Toast({ spec, onClose }: { spec: Live; onClose: () => void }) {
  const [held, setHeld] = useState(false);
  const linger = spec.linger ?? LINGER_MS;
  const left = useRef(linger);
  const since = useRef(0);

  useEffect(() => {
    if (!linger || held) return;
    since.current = Date.now();
    const h = window.setTimeout(onClose, left.current);
    // Holding banks the time already served rather than giving it back. The
    // old version restarted from full on every leave, so a toast under a
    // resting pointer renewed itself forever and never went away.
    return () => {
      window.clearTimeout(h);
      left.current = Math.max(0, left.current - (Date.now() - since.current));
    };
  }, [linger, held, onClose]);

  const open = spec.onOpen;
  return (
    <div
      className={cx("ws-toast", statusClass(spec.kind ?? "ok"),
        open && "ws-toast--open")}
      role="status"
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
    >
      <div className="ws-toast__head">
        {spec.icon && <span className="ws-toast__icon">{spec.icon}</span>}
        <span className="ws-toast__title">{spec.title}</span>
        <button type="button" className="ws-toast__close" onClick={onClose}
          aria-label="Dismiss">
          <X size={14} />
        </button>
      </div>
      {(spec.thumbs?.length || spec.text) && (
        <div className={cx("ws-toast__body", open && "ws-toast__body--open")}
          onClick={open} role={open ? "button" : undefined}
          tabIndex={open ? 0 : undefined}
          onKeyDown={open ? (e) => { if (e.key === "Enter") open(); } : undefined}
        >
          {spec.thumbs?.length ? (
            <span className="ws-toast__thumbs">
              {spec.thumbs.map((src) => (
                <img key={src} className="ws-toast__thumb" src={src} alt="" />
              ))}
            </span>
          ) : null}
          {spec.text && <span className="ws-toast__text">{spec.text}</span>}
        </div>
      )}
      {spec.meta && <div className="ws-toast__meta">{spec.meta}</div>}
    </div>
  );
}
