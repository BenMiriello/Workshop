import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cx } from "./cx";
import { Button } from "./controls";

// Modal / Sheet. Reimplements kitchen.js openOverlay: scrim click + Esc close,
// focus trap, restore focus on close. On mobile (<560px) the CSS turns it into
// a bottom sheet. Render nothing when not open.
export function Modal({
  open,
  onClose,
  title,
  danger,
  children,
  actions,
  labelledBy = "ws-modal-title",
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  danger?: boolean;
  children?: ReactNode;
  actions?: ReactNode;
  labelledBy?: string;
}) {
  const modalRef = useRef<HTMLDivElement>(null);
  const lastFocus = useRef<HTMLElement | null>(null);

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
        onClose();
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
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="ws-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={modalRef}
        className={cx("ws-modal", danger && "ws-modal--danger")}
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
    document.body,
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
