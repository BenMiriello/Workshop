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
import { Check } from "lucide-react";
import { cx } from "./cx";

// Menu — a button that opens a small popover list of actions or choices.
//
// Two jobs, one primitive:
//   • overflow ("kebab") menus, so low-frequency actions stop competing with
//     the ones you use every turn;
//   • compact single-choice pickers, where a native <select> is the wrong
//     shape — it can't carry icons, a checkmark, or a description, and its
//     stock double-arrow is the loudest thing in a dense control row.
//
// For a full-width form field, use `Select` instead: this is for toolbars and
// action rows. Closes on Escape, outside pointerdown, and item activation;
// arrow keys / Home / End move between items; focus returns to the trigger.

export function Menu({
  trigger,
  label,
  align = "start",
  side = "bottom",
  className,
  children,
}: {
  // The clickable element. Gets `aria-haspopup`/`aria-expanded`/`onClick`
  // merged in, so callers style their own trigger without re-wiring behaviour.
  trigger: ReactElement<Record<string, unknown>>;
  label: string;
  // Which edge of the trigger the popover lines up with, and which way it opens.
  align?: "start" | "end";
  side?: "bottom" | "top";
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const id = useId();

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) {
      wrapRef.current?.querySelector<HTMLElement>("[data-ws-menu-trigger]")?.focus();
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
        return;
      }
      if (e.key === "Tab") {
        setOpen(false);
        return;
      }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
      const items = Array.from(
        listRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [],
      );
      if (!items.length) return;
      e.preventDefault();
      const at = items.indexOf(document.activeElement as HTMLElement);
      const next =
        e.key === "Home" ? 0
        : e.key === "End" ? items.length - 1
        : e.key === "ArrowDown" ? (at + 1) % items.length
        : (at - 1 + items.length) % items.length;
      items[next]?.focus();
    };
    // pointerdown, not click: a menu that survives until mouseup can be
    // re-triggered by the same press that was meant to dismiss it.
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey, true);
    listRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open, close]);

  const triggerEl = isValidElement(trigger)
    ? cloneElement(trigger, {
        "data-ws-menu-trigger": "",
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": open ? id : undefined,
        onClick: (e: React.MouseEvent) => {
          (trigger.props.onClick as ((e: React.MouseEvent) => void) | undefined)?.(e);
          setOpen((v) => !v);
        },
      })
    : trigger;

  return (
    <div className={cx("ws-menu", className)} ref={wrapRef}>
      {triggerEl}
      {open && (
        <div
          id={id}
          ref={listRef}
          role="menu"
          aria-label={label}
          className={cx("ws-menu__list", `ws-menu__list--${align}`, `ws-menu__list--${side}`)}
          // Activating an item closes the menu; individual items don't each
          // have to remember to.
          onClick={() => close()}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  children,
  onSelect,
  icon,
  checked,
  danger,
  hint,
}: {
  children: ReactNode;
  onSelect: () => void;
  icon?: ReactNode;
  // Present (true or false) marks this menu a single-choice picker: every item
  // reserves the check column so the labels stay aligned.
  checked?: boolean;
  danger?: boolean;
  hint?: ReactNode;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={cx("ws-menu__item", danger && "ws-menu__item--danger")}
      onClick={onSelect}
    >
      {checked !== undefined && (
        <span className="ws-menu__check" aria-hidden>
          {checked && <Check size={14} />}
        </span>
      )}
      {icon && <span className="ws-menu__icon" aria-hidden>{icon}</span>}
      <span className="ws-menu__label">{children}</span>
      {hint && <span className="ws-menu__hint">{hint}</span>}
    </button>
  );
}

export function MenuSeparator() {
  return <div className="ws-menu__sep" role="separator" />;
}
