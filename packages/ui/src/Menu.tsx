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
import { useAnchoredPlacement } from "./useAnchoredPlacement";

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
//
// For a surface that is a PANEL rather than a list of choices — chips, rows, a
// text field, a submit button — use `Popover`. Every behaviour on this list is
// wrong for one: it would hijack the arrow keys from the text field and close
// itself on the first click inside.

export function Menu({
  trigger,
  label,
  side = "bottom",
  className,
  children,
}: {
  // The clickable element. Gets `aria-haspopup`/`aria-expanded`/`onClick`
  // merged in, so callers style their own trigger without re-wiring behaviour.
  trigger: ReactElement<Record<string, unknown>>;
  label: string;
  // Which way it PREFERS to open. Not a guarantee: like `align`, it is measured
  // after opening and flipped if the preference would be clipped and the other
  // way fits. A caller knows which side reads better, not how much room the
  // page will have at that scroll position on that viewport.
  side?: "bottom" | "top";
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const id = useId();
  // Which edge it pins to and which side it opens on, measured before paint and
  // re-measured on resize and ancestor scroll. Shared with Popover.
  const { align, side: placed } = useAnchoredPlacement(open, side, wrapRef, listRef);
  // Whether this menu is being driven by the keyboard. Focus is only sent back
  // to the trigger when it is: a programmatic .focus() after a mouse/touch pick
  // re-triggers the global :focus-visible ring, which looks like a border the
  // app stuck on the control for no reason the operator can see.
  const viaKeyboard = useRef(false);

  const close = useCallback((restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus && viaKeyboard.current) {
      wrapRef.current?.querySelector<HTMLElement>("[data-ws-menu-trigger]")?.focus();
    }
    viaKeyboard.current = false;
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
      viaKeyboard.current = true;
      // Both item roles: a checkable row is still a row you arrow onto. It is
      // only the CLOSE-on-activation gate below that distinguishes them.
      const items = Array.from(
        listRef.current?.querySelectorAll<HTMLElement>(
          '[role="menuitem"], [role="menuitemcheckbox"]') ?? [],
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
    // Focus the LIST, not its first item: focusing an item drew a focus ring
    // around a row you hadn't chosen, which reads as a border on a random entry.
    // ArrowDown from here still lands on the first item (`at` is -1).
    listRef.current?.focus();
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
          // detail === 0 means the click came from Enter/Space, not a pointer.
          viaKeyboard.current = e.detail === 0;
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
          tabIndex={-1}
          aria-label={label}
          className={cx("ws-menu__list", `ws-menu__list--${align}`, `ws-menu__list--${placed}`)}
          // Activating an ITEM closes the menu; individual items don't each have
          // to remember to. Gated to role="menuitem" rather than "any click in
          // the list", because a menu can also hold a persistent control (a
          // Toggle for an on/off setting, not a one-shot action) — that click
          // was bubbling here too, so tapping the switch closed the menu on the
          // same tap that flipped it. The operator saw "closes and does
          // nothing": the state change did happen, but there was nothing left
          // open to show it having happened.
          onClick={(e) => {
            if ((e.target as HTMLElement).closest('[role="menuitem"]')) close();
          }}
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
  active,
  danger,
  disabled,
  title,
}: {
  children: ReactNode;
  onSelect: () => void;
  icon?: ReactNode;
  // Marks the chosen entry in a single-choice menu. Only the checked row shows
  // (and takes space for) the mark — an unchosen row is not indented to reserve
  // room for something it doesn't have.
  checked?: boolean;
  // "You are here" — for a menu used as navigation. Distinct from `checked`,
  // which is a choice you made; this is where the app currently is.
  active?: boolean;
  danger?: boolean;
  // Present but not choosable right now (e.g. a scope you can't switch to
  // mid-turn). A native disabled button neither fires onSelect nor bubbles a
  // click the menu could treat as "close on activation", so the row simply
  // does nothing until it isn't disabled any more — `title` is how it says why.
  disabled?: boolean;
  // Hover/long-press explanation. A menu row is a NAME you are choosing; the
  // gloss on it belongs behind hover, the way every other app does it. This
  // replaces an inline `hint` span that sat in the same flex row as the label
  // and could not shrink, so a narrow menu clipped the name away to nothing and
  // left the description at full width — backwards, since the name is the part
  // you cannot guess.
  title?: string;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      aria-current={active ? "page" : undefined}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      className={cx("ws-menu__item", active && "is-active", danger && "ws-menu__item--danger")}
      title={title}
      onClick={onSelect}
    >
      {checked && (
        <span className="ws-menu__check" aria-hidden>
          <Check size={14} />
        </span>
      )}
      {icon && <span className="ws-menu__icon" aria-hidden>{icon}</span>}
      <span className="ws-menu__label">{children}</span>
    </button>
  );
}

// A row you TOGGLE rather than choose: one value of a multi-value filter.
//
// `role="menuitemcheckbox"`, which is not just the correct ARIA — it is what
// keeps the menu OPEN. The list closes itself when a click lands inside a
// `[role="menuitem"]`, because choosing one of a set of actions is the end of
// that interaction. Ticking one of six statuses is not: closing after each tick
// would make picking three filters cost three round trips to the trigger.
// Arrow keys still reach these rows (see the keyboard handler above).
export function MenuCheckItem({
  children,
  checked,
  onToggle,
  count,
  disabled,
  title,
}: {
  children: ReactNode;
  checked: boolean;
  onToggle: () => void;
  /** A trailing count, for "how many tickets carry this tag". */
  count?: number;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      role="menuitemcheckbox"
      aria-checked={checked}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      className={cx("ws-menu__item", "ws-menu__item--check", checked && "is-checked")}
      title={title}
      onClick={onToggle}
    >
      <span className="ws-menu__check" aria-hidden>
        {checked && <Check size={14} />}
      </span>
      <span className="ws-menu__label">{children}</span>
      {count != null && <span className="ws-menu__count">{count}</span>}
    </button>
  );
}

export function MenuSeparator() {
  return <div className="ws-menu__sep" role="separator" />;
}

// A heading for a group of related items, in the same uppercase motif as
// SectionLabel. Grouping a long menu by kind is hierarchy done with position
// and weight rather than with rules between rows (design rule 6), so this is
// what a grouped menu uses instead of stacking separators.
//
// `role="presentation"` because the heading is not selectable and must not be
// announced as a menu item; the visual grouping is the affordance.
export function MenuLabel({ children }: { children: ReactNode }) {
  return <div className="ws-menu__group" role="presentation">{children}</div>;
}
