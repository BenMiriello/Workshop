import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { cx } from "./cx";
import { useAnchoredPlacement } from "./useAnchoredPlacement";

// Popover — a PANEL anchored to a trigger. Not a menu, not a modal.
//
// The distinction is the whole reason this exists, so it is stated once here
// rather than rediscovered by the next person holding two primitives that look
// like they overlap:
//
//   Menu is a list of CHOICES. It owns arrow-key roving focus over
//   [role="menuitem"] and it closes itself the moment a click lands inside one,
//   because picking one of a set of actions ends that interaction. Both of
//   those are wrong for a panel: a panel holds filter chips, rows, a text field
//   and a submit button, so the arrow keys belong to the field the caret is in,
//   and a click inside is work being done rather than a choice being made. It
//   is also not role="menu", and calling it one lies to a screen reader about
//   what the arrow keys will do.
//
//   Modal is a surface you cannot leave: it traps focus, it dims the page, and
//   it sits in the middle of the viewport. A popover is attached to the control
//   that opened it, has to flip and re-align against every clipping ancestor,
//   and leaves the page usable behind it.
//
// So: role="dialog", placement shared with Menu through `useAnchoredPlacement`,
// closes on Escape and on an outside pointerdown, and on nothing else. No focus
// trap and no autofocus: opening a panel must not raise a phone keyboard or
// take the caret off whatever the operator was already typing in.
export function Popover({
  trigger,
  children,
  side = "bottom",
  label,
  open: openProp,
  onOpenChange,
  className,
}: {
  // The clickable element. Gets `aria-haspopup`/`aria-expanded`/`onClick`
  // merged in, so callers style their own trigger without re-wiring behaviour.
  trigger: ReactElement<Record<string, unknown>>;
  children: ReactNode;
  // Which way it PREFERS to open. Not a guarantee: it is measured after opening
  // and flipped if the preference would be clipped and the other way fits.
  side?: "bottom" | "top";
  /** Names the dialog for a screen reader. */
  label?: string;
  // CONTROLLED when `open` is supplied, uncontrolled otherwise, and it has to
  // be both. The chat's Work panel is a popover on a wide screen and a top
  // sheet on a phone, which is not a popover at all: one piece of open-state
  // drives both, so the caller has to own it. Everything else that just wants a
  // panel on a button should not have to hold a useState to get one.
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const controlled = openProp !== undefined;
  const open = controlled ? openProp : uncontrolledOpen;
  const wrapRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const id = useId();

  const setOpen = useCallback((next: boolean) => {
    // An uncontrolled Popover keeps its own state; a controlled one is told
    // what to be and only reports. Writing internal state in the controlled
    // case would give it a second opinion about whether it is open.
    if (!controlled) setUncontrolledOpen(next);
    onOpenChange?.(next);
  }, [controlled, onOpenChange]);

  const { align, side: placed } = useAnchoredPlacement(open, side, wrapRef, surfaceRef);

  useEffect(() => {
    if (!open) return;
    // pointerdown, not click: a panel that survives until mouseup can be
    // re-triggered by the same press that was meant to dismiss it. Anything
    // INSIDE the wrapper is left alone, which is the difference from Menu: a
    // click in here is the operator using the panel, not choosing and leaving.
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Stop it reaching an outer surface: Escape dismisses the innermost thing
      // that is open, not everything at once.
      e.stopPropagation();
      setOpen(false);
      // Focus goes back to the trigger, and ONLY on this path. Menu's note
      // applies and is honoured by omission: a programmatic .focus() after a
      // mouse pick re-triggers the global :focus-visible ring, which reads as a
      // border the app stuck on the control for no reason. Escape is a key, so
      // the ring it leaves is correct; the outside-pointerdown close above
      // restores nothing.
      wrapRef.current?.querySelector<HTMLElement>("[data-ws-popover-trigger]")?.focus();
    };
    document.addEventListener("pointerdown", onDown);
    // Capture, so Escape from inside a text field in the panel still reaches
    // us. Tab is deliberately NOT handled: Menu closes on Tab because there is
    // nothing in a menu to tab between, and a panel is the opposite case.
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, setOpen]);

  const triggerEl = isValidElement(trigger)
    ? cloneElement(trigger, {
        "data-ws-popover-trigger": "",
        // "dialog", not "menu": what opens is a panel, and the arrow keys do
        // not walk it.
        "aria-haspopup": "dialog",
        "aria-expanded": open,
        "aria-controls": open ? id : undefined,
        onClick: (e: React.MouseEvent) => {
          (trigger.props.onClick as ((e: React.MouseEvent) => void) | undefined)?.(e);
          setOpen(!open);
        },
      })
    : trigger;

  return (
    <div className={cx("ws-popover", className)} ref={wrapRef}>
      {triggerEl}
      {open && (
        <div
          id={id}
          ref={surfaceRef}
          role="dialog"
          aria-label={label}
          className={cx(
            "ws-popover__surface",
            `ws-popover__surface--${align}`,
            `ws-popover__surface--${placed}`,
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}
