import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { Check } from "lucide-react";
import { cx } from "./cx";
import { placeMenu } from "./menuPlacement";

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

// The box a popover must stay inside: the viewport, narrowed by every ancestor
// that clips.
//
// THE VIEWPORT IS NOT THE ONLY EDGE, and assuming it was is what put the chat's
// branch menu half off screen. `.chat-log` is `overflow-x: hidden` and the chat
// column is capped at `--page-max` and centred, so on a desktop a menu opening
// from a bubble had hundreds of pixels of viewport to its right and was still
// sliced off at the log's own edge. Measured against the viewport it did not
// overflow, so it never flipped. Reported 2026-08-14: "it opens up and off to
// the side oddly and is clipped/hidden off the edge of its container".
//
// Each axis is asked separately, because `overflow-x: hidden` with a `visible`
// y is a real and common combination (that log is exactly it) and clipping one
// axis says nothing about the other.
function clipBox(el: HTMLElement) {
  let left = 0;
  let top = 0;
  let right = window.innerWidth;
  let bottom = window.innerHeight;
  for (let n = el.parentElement; n; n = n.parentElement) {
    const s = getComputedStyle(n);
    // CSS coerces a `visible` paired with a non-visible other axis to `auto`,
    // so a computed value of `visible` is the only one that does not clip.
    const clipsX = s.overflowX !== "visible";
    const clipsY = s.overflowY !== "visible";
    if (!clipsX && !clipsY) continue;
    const r = n.getBoundingClientRect();
    if (clipsX) { left = Math.max(left, r.left); right = Math.min(right, r.right); }
    if (clipsY) { top = Math.max(top, r.top); bottom = Math.min(bottom, r.bottom); }
  }
  return { left, top, right, bottom };
}

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
  // Horizontal alignment is NOT a caller choice — it used to be (`align`
  // start|end per instance), and every caller had to reason about where in its
  // row it sat and guess whether that made left or right the safe direction.
  // Two guesses were wrong on a narrow phone (Effort and Mode, both hardcoded
  // "end", ran off the LEFT edge). Left-anchored ("start") is the one default;
  // this measures itself after opening and flips to right-anchored ("end")
  // only if that default would run past the right edge of the box it must stay
  // inside. One rule, no per-caller guessing, correct at any position in any
  // row.
  const [align, setAlign] = useState<"start" | "end">("start");
  // Where it ACTUALLY opened, which is `side` unless that would be clipped.
  const [placed, setPlaced] = useState<"bottom" | "top">(side);
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const id = useId();
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

  // Measure BEFORE paint, so a flip is never visible as a jump. Re-measures on
  // resize and on any ancestor scroll while open: the trigger's position
  // relative to the clip edge can change without a close and reopen.
  useLayoutEffect(() => {
    if (!open) { setAlign("start"); setPlaced(side); return; }
    const measure = () => {
      const list = listRef.current;
      const wrap = wrapRef.current;
      if (!list || !wrap) return;
      // The arithmetic is in menuPlacement.ts, where a test can execute it.
      // This function's whole job is turning the DOM into its four arguments.
      const at = wrap.getBoundingClientRect();
      const p = placeMenu(
        { left: at.left, top: at.top, right: at.right, bottom: at.bottom },
        { width: list.offsetWidth, height: list.offsetHeight },
        clipBox(wrap),
        side,
        // The real gap the CSS leaves between trigger and list, read from the
        // token rather than restated as a number that can drift from it.
        parseFloat(getComputedStyle(list).getPropertyValue("--space-1")) || 4,
      );
      setAlign(p.align);
      setPlaced(p.side);
    };
    measure();
    window.addEventListener("resize", measure);
    // Scrolling any ancestor moves the trigger relative to the clip box without
    // closing the menu (only a pointerdown does that), so the side that fitted
    // when it opened may not fit a moment later. Capture, because a scroll on an
    // inner scroller does not bubble.
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, side]);

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
