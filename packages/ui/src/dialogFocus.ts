import { useEffect, useRef, type RefObject } from "react";

// What every modal surface does with the keyboard, shared by Modal and Sheet:
// focus moves in when it opens (the `data-autofocus` element, else the first
// focusable), Tab and Shift+Tab stay inside it, Escape closes it, and focus goes
// back to where it was when it closes.
//
// FOCUS IS TAKEN ONCE, WHEN THE SURFACE OPENS, not again on every render. This
// used to depend on `onClose`, which callers pass as an inline arrow, so any
// parent re-render (a poll landing, a refresh finishing) tore the effect down
// and set it up again: focus jumped back to the dialog's first control and its
// focus ring reappeared on its own, seconds after the operator had clicked it
// away. The handler is read through a ref so the listener stays current without
// the effect re-running.
export function useDialogFocus(open: boolean, ref: RefObject<HTMLElement>,
                               onClose: () => void) {
  const lastFocus = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    lastFocus.current = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        ref.current?.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    const auto = ref.current?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0];
    auto?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeRef.current();
        return;
      }
      if (e.key === "Tab") {
        const f = focusables();
        if (f.length === 0) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      lastFocus.current?.focus?.();
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
}
