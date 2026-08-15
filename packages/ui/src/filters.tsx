import type { ReactNode } from "react";
import { X } from "lucide-react";
import { cx } from "./cx";

// The filter row: a search field that absorbs the slack, one removable chip per
// active filter, an "add a filter" affordance, and the actions pinned right.
//
// WHY THIS IS A LAYOUT PRIMITIVE AND NOT A FILTER ENGINE. The handoff asked for
// one generic component "driven by a field descriptor list". What is actually
// shared between the tickets list, Memory and the runs feed is the ROW: how it
// wraps, which child absorbs the leftover width, and what a chip looks like.
// What is NOT shared is where the options come from (a scope registry, a label
// index, a closed enum) and what picking one does. Owning that here would mean
// this file learning what a scope is, which is exactly the coupling the kit
// forbids. So the row is the primitive, the menus are the caller's, and a page
// adopting it writes its own descriptors on top.
//
// Every control in it is `--control-h` and `flex: none` except the search, so a
// narrow viewport shrinks the field rather than crushing the buttons, and the
// left group wraps its own chips instead of colliding with the actions.
export function FilterBar({
  search,
  chips,
  add,
  actions,
  className,
}: {
  /** The search field. The only child allowed to grow. */
  search?: ReactNode;
  /** `FilterChip`s for the filters that are currently on. */
  chips?: ReactNode;
  /** The "+ Filter" control (a `Menu` trigger, in practice). */
  add?: ReactNode;
  /** Sort, view options, the primary action. Pinned to the right. */
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("ws-filterbar", className)}>
      <div className="ws-filterbar__group">
        {search}
        {chips}
        {add}
      </div>
      {actions != null && <div className="ws-filterbar__actions">{actions}</div>}
    </div>
  );
}

// One active filter, removable. The key reads muted and the value reads in the
// accent, so a row of them scans as "Status · 2  Scope Development" rather than
// as a row of equally loud pills.
//
// `--radius-md`, not a pill: a pill in this system is a tag or a status, and a
// filter is neither. The clear button is a real button so it is reachable by
// keyboard and names what it clears.
export function FilterChip({
  label,
  value,
  onClear,
  onClick,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  /** Omit to render a chip that cannot be removed. */
  onClear?: () => void;
  /** Opens the editor for this filter. The chip is the control as well as the
   *  readout, which is what makes a second "edit filters" surface unnecessary. */
  onClick?: () => void;
  className?: string;
}) {
  const body = (
    <>
      <span className="ws-filterchip__key">{label}</span>
      <span className="ws-filterchip__val">{value}</span>
    </>
  );
  return (
    <span className={cx("ws-filterchip", className)}>
      {onClick ? (
        <button type="button" className="ws-filterchip__open" onClick={onClick}>
          {body}
        </button>
      ) : (
        <span className="ws-filterchip__open">{body}</span>
      )}
      {onClear && (
        <button
          type="button"
          className="ws-filterchip__clear"
          onClick={onClear}
          aria-label={typeof label === "string" ? `Clear ${label.toLowerCase()} filter` : "Clear filter"}
        >
          <X aria-hidden />
        </button>
      )}
    </span>
  );
}
