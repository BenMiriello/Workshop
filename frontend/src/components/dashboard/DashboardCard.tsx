import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cx, StateBlock, Skeleton } from "@workshop/ui";

// THE CARD EVERY DASHBOARD CARD IS. One head layout, one clip rule, one set of
// states, so "how a card behaves" is answered once rather than four times.
//
// FIXED HEIGHT, AND IT CLIPS. That is the layout rule the whole page is built
// on: the grid sets the row height and a card shows a whole number of rows
// inside it. A card never grows to fit its content and never half-draws a row
// at the clip edge; what does not fit is named on the head's link. Cards that
// size themselves are four boxes of different lengths, which is not a grid.
//
// `tone="alert"` is the ONE alarm on this page, and only "Needs you" uses it:
// the card's border and label go warn when something is actually waiting. If a
// second card ever wants it, that is a conversation, not a prop change.

export function DashboardCard({
  label, count, tone = "plain", link, children, className,
}: {
  label: string;
  /** Beside the label. A number the head states, not a badge. */
  count?: ReactNode;
  tone?: "plain" | "alert";
  /** The way out of this card, right-aligned in the head. */
  link?: { to: string; label: string };
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("ws-dashcard", tone === "alert" && "ws-dashcard--alert", className)}>
      <header className="ws-dashcard__head">
        <span className="ws-dashcard__label">{label}</span>
        {count != null && <span className="ws-dashcard__count">{count}</span>}
        {link && (
          <Link className="ws-dashcard__link" to={link.to}>{link.label}</Link>
        )}
      </header>
      <div className="ws-dashcard__body">{children}</div>
    </section>
  );
}

/** The card's own footer, above the clip. Only "Running now" has one. */
export function DashboardCardFoot({ children }: { children: ReactNode }) {
  return <footer className="ws-dashcard__foot">{children}</footer>;
}

/** Loading, empty and error, in the one place, so four cards cannot answer the
 *  same three questions three different ways.
 *
 *  ERROR NAMES THE ENDPOINT. A card that says "couldn't load" and nothing else
 *  is a card nobody can act on; the operator of this instance can read a path.
 */
export function DashboardState({
  loading, error, empty, emptyText, endpoint, rows = 3,
}: {
  loading: boolean;
  error: string | null;
  empty: boolean;
  emptyText: string;
  endpoint: string;
  rows?: number;
}) {
  if (error) {
    return (
      <StateBlock
        variant="error"
        className="ws-dashcard__state"
        title="Couldn't load"
        hint={<><code>{endpoint}</code> ({error})</>}
      />
    );
  }
  if (loading) {
    return (
      <div className="ws-dashcard__skeletons">
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} width={i % 2 ? "62%" : "84%"} />
        ))}
      </div>
    );
  }
  if (empty) return <p className="ws-dashcard__empty">{emptyText}</p>;
  return null;
}
