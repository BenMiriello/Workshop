import type { ReactNode } from "react";
import { cx } from "./cx";

// ---- Stack / Row -----------------------------------------------------------
// Flex layout helpers. Compose everything with these instead of inline flex.
export function Stack({
  gap,
  className,
  children,
  ...rest
}: { gap?: "sm" | "lg"; className?: string; children?: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("ws-stack", gap === "sm" && "ws-stack--sm", gap === "lg" && "ws-stack--lg", className)} {...rest}>
      {children}
    </div>
  );
}

export function Row({
  wrap,
  between,
  end,
  className,
  children,
  ...rest
}: {
  wrap?: boolean;
  between?: boolean;
  end?: boolean;
  className?: string;
  children?: ReactNode;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx("ws-row", wrap && "ws-row--wrap", between && "ws-row--between", end && "ws-row--end", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function Spacer() {
  return <span className="ws-spacer" />;
}

// ---- Section label (the recurring uppercase motif) -------------------------
export function SectionLabel({
  icon,
  children,
  className,
}: {
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("ws-section-label", className)}>
      {icon}
      {children}
    </div>
  );
}

// ---- Page header -----------------------------------------------------------
export function PageHeader({
  title,
  sub,
  actions,
  className,
}: {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cx("ws-page-header", className)}>
      <div>
        <h1 className="ws-page-header__title">{title}</h1>
        {sub != null && <p className="ws-page-header__sub">{sub}</p>}
      </div>
      {actions != null && <div className="ws-page-header__actions">{actions}</div>}
    </header>
  );
}

export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("ws-toolbar", className)}>{children}</div>;
}

// ---- Card ------------------------------------------------------------------
export function Card({
  interactive,
  flat,
  raised,
  pad0,
  className,
  children,
  ...rest
}: {
  interactive?: boolean;
  flat?: boolean;
  raised?: boolean;
  pad0?: boolean;
  className?: string;
  children?: ReactNode;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx(
        "ws-card",
        interactive && "ws-card--interactive",
        flat && "ws-card--flat",
        raised && "ws-card--raised",
        pad0 && "ws-card--pad-0",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <ul className={cx("ws-card-grid", className)}>{children}</ul>;
}

// ---- Stat (big-number KPI tile) --------------------------------------------
export function Stat({
  label,
  value,
  sub,
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("ws-stat", className)}>
      <div className="ws-section-label" style={{ margin: 0 }}>
        {label}
      </div>
      <div className="ws-stat__num">{value}</div>
      {sub != null && <div className="ws-stat__sub">{sub}</div>}
    </div>
  );
}

export function Divider({ className }: { className?: string }) {
  return <hr className={cx("ws-divider", className)} />;
}
