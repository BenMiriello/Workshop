import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "./cx";
import { Button } from "./controls";
import { useOverlayHost } from "./OverlayHost";

// Modal. Scrim click + Esc close, focus trap, restore focus on close. Renders
// nothing when not open.
//
// SCOPED TO THE SHELL'S CONTENT REGION when one exists (`OverlayHost`): the
// dialog sits under the Workshop top nav rather than over it. This file does
// not know what that chrome is or how tall it is, and must not learn.
//
// A CENTERED DIALOG AT EVERY WIDTH. This used to become a bottom sheet under
// 560px automatically, which made every dialog in the app a drawer on a phone.
// A sheet reads as a peripheral panel you flick away — right for a picker,
// wrong for a decision you are being asked to make — so it is now opt-in via
// `sheet`, and the caller chooses.
export function Modal({
  open,
  onClose,
  title,
  danger,
  children,
  actions,
  labelledBy = "ws-modal-title",
  size = "default",
  sheet = false,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  danger?: boolean;
  children?: ReactNode;
  actions?: ReactNode;
  labelledBy?: string;
  // "lg" widens the dialog for content-heavy forms (many fields/sliders).
  size?: "default" | "lg";
  // Opt in to the bottom-sheet presentation. For panels that belong to the
  // bottom edge (a picker, a quick action list), never for a decision.
  sheet?: boolean;
}) {
  const modalRef = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);
  // The shell's content region, when there is one. A dialog is scoped to the
  // app it was opened in — it never covers the Workshop top nav. See
  // OverlayHost; <body> is the fallback for entries with no shell.
  const host = useOverlayHost();

  // FOCUS IS TAKEN ONCE, WHEN THE DIALOG OPENS — not again on every render.
  // This effect used to depend on `onClose`, which callers pass as an inline
  // arrow, so any parent re-render (a poll landing, a refresh finishing) tore
  // the effect down and set it up again: focus jumped back to the dialog's
  // first control and its focus ring reappeared on its own, seconds after the
  // operator had clicked it away. The handler is read through a ref so the
  // listener stays current without the effect re-running.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    lastFocus.current = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        modalRef.current?.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    // Move focus into the dialog (autofocus target or first focusable).
    const auto = modalRef.current?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0];
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
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className={cx("ws-overlay", sheet && "ws-overlay--sheet")}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={modalRef}
        className={cx("ws-modal", size === "lg" && "ws-modal--lg", danger && "ws-modal--danger")}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title != null ? labelledBy : undefined}
      >
        {title != null && (
          <h3 id={labelledBy} className="ws-modal__title">
            {title}
          </h3>
        )}
        {children != null && <div className="ws-modal__body">{children}</div>}
        {actions != null && <div className="ws-modal__actions">{actions}</div>}
      </div>
    </div>,
    host ?? document.body,
  );
}

// Destructive-confirm dialog. Always danger treatment + names the consequence.
// `body` should state what happens and that it can't be undone.
export function ConfirmDialog({
  open,
  onCancel,
  onConfirm,
  title,
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  busy,
  danger = true,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  title: ReactNode;
  body: ReactNode;
  confirmLabel?: ReactNode;
  cancelLabel?: ReactNode;
  busy?: boolean;
  // Destructive by default (danger styling + "can't be undone" framing). Set
  // false for a non-destructive confirm (e.g. publish), which renders a primary
  // confirm button instead of a red one.
  danger?: boolean;
}) {
  const confirm = useCallback(() => {
    if (!busy) onConfirm();
  }, [busy, onConfirm]);

  return (
    <Modal
      open={open}
      onClose={onCancel}
      title={title}
      danger={danger}
      actions={
        <>
          <Button data-autofocus onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={confirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {body}
    </Modal>
  );
}
