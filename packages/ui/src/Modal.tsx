import { useCallback, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "./cx";
import { Button } from "./controls";
import { useDialogFocus } from "./dialogFocus";
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
// A centered dialog is the default for everything, pickers included; a
// bottom sheet is only for a surface the operator asked to have one.
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
  tall = false,
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
  // The bottom-sheet presentation, only where the operator asked for one.
  sheet?: boolean;
  // A form that is mostly fields: on a phone it may grow to the whole region's
  // height before its body scrolls, instead of the short cap a dialog has there.
  tall?: boolean;
}) {
  const modalRef = useRef<HTMLDivElement>(null);
  // The shell's content region, when there is one. A dialog is scoped to the
  // app it was opened in — it never covers the Workshop top nav. See
  // OverlayHost; <body> is the fallback for entries with no shell.
  const host = useOverlayHost();
  useDialogFocus(open, modalRef, onClose);

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
        className={cx("ws-modal", size === "lg" && "ws-modal--lg", tall && "ws-modal--tall", danger && "ws-modal--danger")}
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
