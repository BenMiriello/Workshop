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
  compact,
  className,
  children,
  ...rest
}: {
  interactive?: boolean;
  flat?: boolean;
  raised?: boolean;
  pad0?: boolean;
  compact?: boolean;
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
        compact && "ws-card--compact",
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

// ---- ProgressBar -----------------------------------------------------------
// A horizontal fill showing value/max. `status` maps the fill to one of the
// five status kinds (ok/running/stopped/warn/danger). Use for a bounded
// quantity that fills toward a cap (usage vs budget, disk, quota) — not for a
// pass/fail state (that is a StatusPill).
// Stacked composition bar: one track, N categorical segments, largest first.
// Use when a total is made of parts (usage by app) rather than a single value
// against a limit — that is ProgressBar. `max` is optional: pass it when the
// window has a cap, so the unfilled remainder shows as bare track; omit it and
// the segments fill the width, reading as share-of-total (the correct shape for
// a lookback window that has no cap).
export function StackedBar({
  segments,
  max,
  label,
  className,
}: {
  segments: { key: string; value: number; color: string }[];
  max?: number;
  label?: ReactNode;
  className?: string;
}) {
  const total = segments.reduce((a, s) => a + Math.max(0, s.value), 0);
  const denom = max != null && max > 0 ? Math.max(max, total) : total;
  return (
    <div className={cx("ws-stackbar", className)}>
      {label != null && <div className="ws-stackbar__label">{label}</div>}
      <div className="ws-stackbar__track">
        {denom > 0 &&
          segments
            .filter((s) => s.value > 0)
            .map((s) => (
              <div
                key={s.key}
                className="ws-stackbar__seg"
                style={{ width: `${(s.value / denom) * 100}%`, background: s.color }}
                title={`${s.key}: ${Math.round((s.value / (total || 1)) * 100)}%`}
              />
            ))}
      </div>
    </div>
  );
}

export function ProgressBar({
  value,
  max,
  status = "ok",
  label,
  className,
}: {
  value: number;
  max: number;
  status?: "ok" | "running" | "stopped" | "warn" | "danger";
  label?: ReactNode;
  className?: string;
}) {
  const frac = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const pct = Math.round(frac * 100);
  return (
    <div className={cx("ws-progress", `ws-status-${status}`, className)}>
      {label != null && <div className="ws-progress__label">{label}</div>}
      <div
        className="ws-progress__track"
        role="progressbar"
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={max}
      >
        <div className="ws-progress__fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
