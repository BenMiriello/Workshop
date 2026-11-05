import type { ReactNode } from "react";
import { cx } from "./cx";

// A row that is not itself chosen: an entry with buttons of its own at its
// end, such as one action in a history with its Undo. `muted` greys the name,
// for an entry that no longer stands (undone).
export function MenuRow({
  children,
  icon,
  actions,
  muted,
  title,
}: {
  children: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  muted?: boolean;
  title?: string;
}) {
  return (
    <div className={cx("ws-menu__row", "ws-menu__row--entry", muted && "is-muted")} role="group" title={title}>
      {icon && <span className="ws-menu__icon" aria-hidden>{icon}</span>}
      <span className="ws-menu__label">{children}</span>
      {actions && <span className="ws-menu__acts">{actions}</span>}
    </div>
  );
}

// An icon button inside a MenuRow or a MenuLabel. Pressing it does NOT close
// the menu, so what it changed stays in view; the arrow keys reach it.
export function MenuAction({
  icon,
  label,
  onPress,
  disabled,
  title,
}: {
  icon: ReactNode;
  /** What it does, for screen readers and as the hover text. */
  label: string;
  onPress: () => void;
  disabled?: boolean;
  /** Hover text when it differs from `label` (why it is disabled). */
  title?: string;
}) {
  return (
    <button
      type="button"
      className="ws-menu__act"
      aria-label={label}
      title={title ?? label}
      disabled={disabled}
      onClick={onPress}
    >
      {icon}
    </button>
  );
}
