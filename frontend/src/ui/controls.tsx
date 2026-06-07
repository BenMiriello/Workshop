import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { cx } from "./cx";
import { catClass, statusClass, type CatHue, type StatusKind } from "./status";

// ---- Button ----------------------------------------------------------------
type ButtonVariant = "default" | "primary" | "ghost" | "danger" | "danger-ghost";

export function Button({
  variant = "default",
  size,
  icon,
  block,
  loading,
  className,
  children,
  ...rest
}: {
  variant?: ButtonVariant;
  size?: "sm" | "lg";
  icon?: boolean;
  block?: boolean;
  loading?: boolean;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={cx(
        "ws-btn",
        variant === "primary" && "ws-btn--primary",
        variant === "ghost" && "ws-btn--ghost",
        variant === "danger" && "ws-btn--danger",
        variant === "danger-ghost" && "ws-btn--danger-ghost",
        size === "sm" && "ws-btn--sm",
        size === "lg" && "ws-btn--lg",
        icon && "ws-btn--icon",
        block && "ws-btn--block",
        loading && "ws-btn--loading",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

// ---- Field + controls ------------------------------------------------------
// A Field is the column wrapper: label, control, optional help / error. Use it
// for everything that writes; the bare Input/Select/Textarea exist for inline
// composition (e.g. inside a Row).
export function Field({
  label,
  htmlFor,
  required,
  optional,
  help,
  error,
  inline,
  className,
  children,
}: {
  label?: ReactNode;
  htmlFor?: string;
  required?: boolean;
  optional?: boolean;
  help?: ReactNode;
  error?: ReactNode;
  inline?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cx("ws-field", inline && "ws-field--inline", error && "ws-field--error", className)}>
      {label != null &&
        (htmlFor ? (
          <label className="ws-field__label" htmlFor={htmlFor}>
            {label}
            {required && <span className="ws-req">*</span>}
            {optional && <span className="ws-field__opt">optional</span>}
          </label>
        ) : (
          <span className="ws-field__label">
            {label}
            {required && <span className="ws-req">*</span>}
            {optional && <span className="ws-field__opt">optional</span>}
          </span>
        ))}
      {children}
      {help != null && <span className="ws-field__help">{help}</span>}
      {error != null && <span className="ws-field__error">{error}</span>}
    </div>
  );
}

export function Input({ className, ...rest }: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx("ws-input", className)} {...rest} />;
}

export function Select({ className, children, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx("ws-select", className)} {...rest}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx("ws-textarea", className)} {...rest} />;
}

// Read-only display value — the Edit-mode "view" state of a field.
export function ReadOnly({ value, className }: { value?: ReactNode; className?: string }) {
  const empty = value == null || value === "";
  return (
    <span className={cx("ws-readonly", empty && "ws-readonly--empty", className)}>{empty ? "—" : value}</span>
  );
}

// ---- Toggle ----------------------------------------------------------------
export function Toggle({
  checked,
  onChange,
  disabled,
  label,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx("ws-toggle", className)}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="ws-toggle__track">
        <span className="ws-toggle__thumb" />
      </span>
      {label != null && <span>{label}</span>}
    </label>
  );
}

// ---- Checkbox --------------------------------------------------------------
export function Checkbox({
  checked,
  onChange,
  disabled,
  label,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label?: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx("ws-check", className)}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="ws-check__box">
        <Check aria-hidden />
      </span>
      {label != null && <span>{label}</span>}
    </label>
  );
}

// ---- Badge / StatusPill / StatusDot / Tag ----------------------------------
export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("ws-badge", className)}>{children}</span>;
}

// Soft-tinted pill with a dot — standalone status (on cards, headers).
export function StatusPill({ status, children, className }: { status: StatusKind; children: ReactNode; className?: string }) {
  return (
    <span className={cx("ws-pill", statusClass(status), className)}>
      <span className="ws-pill__dot" />
      {children}
    </span>
  );
}

// Bare dot + text — for dense table rows.
export function StatusDot({ status, children, className }: { status: StatusKind; children: ReactNode; className?: string }) {
  return <span className={cx("ws-dot", statusClass(status), className)}>{children}</span>;
}

// Categorical chip — differentiation, not status.
export function Tag({ hue, children, className }: { hue: CatHue; children: ReactNode; className?: string }) {
  return (
    <span className={cx("ws-tag", catClass(hue), className)}>
      <span className="ws-tag__dot" />
      {children}
    </span>
  );
}

// ---- Tabs ------------------------------------------------------------------
export interface TabItem<T extends string> {
  value: T;
  label: ReactNode;
}

export function Tabs<T extends string>({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem<T>[];
  value: T;
  onChange: (next: T) => void;
  className?: string;
}) {
  return (
    <div className={cx("ws-tabs", className)} role="tablist">
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          aria-selected={it.value === value}
          className="ws-tab"
          onClick={() => onChange(it.value)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
