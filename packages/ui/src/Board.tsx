import type { ReactNode } from "react";
import { cx } from "./cx";

// A kanban board: fixed-width columns in a horizontal scroller, one card per
// item. Generic on purpose — a column is a heading, a count and a stack of
// children; it does not know that the heading is a status.
//
// NOT A DRAG SURFACE, and that is a deliberate limit rather than an unfinished
// one. Reordering and dragging between columns are out of scope for this pass
// (design handoff 2026-08-14): a card is a link to its record, and the state it
// would be dragged into is changed there. Nothing here sets up for a drag, so
// nothing here has to be undone to add one.
export function Board({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("ws-board", className)}>{children}</div>;
}

export function BoardColumn({
  title,
  count,
  mark,
  empty,
  children,
  className,
}: {
  title: ReactNode;
  /** Shown right-aligned in the heading. Pass 0 rather than omitting it: an
   *  empty lane that says "0" reads as measured, one that says nothing reads
   *  as broken. */
  count?: number;
  /** A leading mark for the column: a `StatusDot` with no children, in
   *  practice. The kit does not decide that a lane is a status. */
  mark?: ReactNode;
  /** What to show when the column holds nothing. */
  empty?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const isEmpty = children == null
    || (Array.isArray(children) && children.length === 0);
  return (
    <section className={cx("ws-board__col", className)}>
      <header className="ws-board__head">
        {mark}
        <span className="ws-board__title">{title}</span>
        {count != null && <span className="ws-board__count">{count}</span>}
      </header>
      <div className="ws-board__stack">
        {isEmpty ? <div className="ws-board__empty">{empty}</div> : children}
      </div>
    </section>
  );
}

// One card. Renders as whatever `as` says so a card that navigates is a real
// link (middle-click, keyboard, native long-press) rather than a div with an
// onClick.
export function BoardCard({
  as: As = "div",
  children,
  className,
  ...rest
}: {
  as?: React.ElementType;
  children: ReactNode;
  className?: string;
} & Record<string, unknown>) {
  return (
    <As className={cx("ws-board__card", className)} {...rest}>
      {children}
    </As>
  );
}
