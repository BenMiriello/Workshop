import type { ReactNode } from "react";
import { cx } from "./cx";

// Airy table: one hairline under the header, hover rows, no row rules. Always
// wrap in TableScroll so it scrolls horizontally on narrow screens instead of
// widening the page. Keep the most identifying column (time / name) first.
export function TableScroll({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("ws-table-scroll", className)}>{children}</div>;
}

export function Table({ children, className, ...rest }: { children: ReactNode; className?: string } & React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <table className={cx("ws-table", className)} {...rest}>
      {children}
    </table>
  );
}

// Numeric cell — right-aligned tabular mono. `time` cell — muted mono.
export function NumCell({ children, className, ...rest }: { children?: ReactNode; className?: string } & React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cx("ws-td-num", className)} {...rest}>
      {children}
    </td>
  );
}

export function TimeCell({ children, className, ...rest }: { children?: ReactNode; className?: string } & React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cx("ws-td-time", className)} {...rest}>
      {children}
    </td>
  );
}
