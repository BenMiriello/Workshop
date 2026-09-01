import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
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
// Two kinds of page, and the title scale is the difference between them.
//
//   variant "page"   — the title is a NAME the app chose: Tickets, Agents,
//                      Settings. Short, and it is the largest thing on screen.
//   variant "entity" — the title is CONTENT: one ticket's sentence, one store's
//                      id. It can run to three lines, so it is set at reading
//                      size and wraps. A 24px sentence is a banner, not a title.
//
// `back` and `meta` exist because every detail page was hand-rolling both into
// the `title` slot: a router Link wrapping a bare lucide arrow, which inherits
// the h1's font-size (24px) and the link color (the MINE hue), so the loudest
// thing on the page was the way out of it. Back is navigation chrome, so it is
// muted at rest like every other control (see "A control is not content").
export function PageHeader({
  title,
  eyebrow,
  sub,
  meta,
  back,
  actions,
  variant = "page",
  className,
}: {
  title: ReactNode;
  /** WHOSE page this is, above the title, uppercase and muted.
   *
   *  For a page whose title names the CONTENT ("Tickets", "Memory") while the
   *  thing that owns it is elsewhere. The Tickets page hand-rolled exactly this
   *  as its own `.ws-tickets__eyebrow` to print the scope over the title; the
   *  per-app tabs then needed the same thing to say which app you are in, and a
   *  second private copy is how an idiom becomes two idioms. Not `back`: an
   *  eyebrow is a label, not a way out. */
  eyebrow?: ReactNode;
  sub?: ReactNode;
  /** The facts you read once: status, scope, when it moved. One muted line
   *  under the title, so they are visible before any scrolling happens. */
  meta?: ReactNode;
  /** A router link to the list this record came from. Renders above the
   *  title, small and muted. */
  back?: ReactNode;
  actions?: ReactNode;
  variant?: "page" | "entity";
  className?: string;
}) {
  // `false` as well as null: every call site writes `meta={record && (...)}`,
  // and a bare `!= null` check on that renders an empty slot whose margin is
  // still there while the record is loading.
  const has = (slot: ReactNode) => slot != null && slot !== false;
  return (
    <header className={cx("ws-page-header", variant === "entity" && "ws-page-header--entity", className)}>
      <div className="ws-page-header__main">
        {has(back) && <div className="ws-page-header__back">{back}</div>}
        {has(eyebrow) && <div className="ws-page-header__eyebrow">{eyebrow}</div>}
        <h1 className="ws-page-header__title">{title}</h1>
        {has(sub) && <p className="ws-page-header__sub">{sub}</p>}
        {has(meta) && <div className="ws-page-header__meta">{meta}</div>}
      </div>
      {has(actions) && <div className="ws-page-header__actions">{actions}</div>}
    </header>
  );
}

// ---- Detail layout ---------------------------------------------------------
// The standard record page: the work in a main column, its properties in a
// rail. The rail is FIRST in the DOM, which is what makes the narrow layout
// right: on a phone there is one column and the properties sit directly under
// the header instead of below the comments, the runs and the activity feed.
// Wide, the grid puts it back on the right. One DOM, no duplicated markup.
export function DetailLayout({
  main,
  rail,
  className,
}: {
  main: ReactNode;
  rail?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("ws-detail", className)}>
      {rail != null && <div className="ws-detail__rail">{rail}</div>}
      <div className="ws-detail__main">{main}</div>
    </div>
  );
}

// ---- Props: read-only record fields ----------------------------------------
// A description list, NOT a stack of `Field`s. `Field` is form furniture: its
// label is 13px at `--fg` and medium weight, which is correct beside an input
// the operator is about to type in and wrong beside a value that is only being
// read. Every detail page reached for it anyway, because it was the only
// label-plus-value shape the kit had, and the rails all came out looking like
// disabled forms.
//
// The columns auto-fill, so the same markup is a single column in a 260px rail
// and two or three across a phone. Never a media query at the call site.
export function Props({ children, className }: { children: ReactNode; className?: string }) {
  return <dl className={cx("ws-props", className)}>{children}</dl>;
}

export function Prop({
  label,
  children,
  className,
}: {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("ws-props__row", className)}>
      <dt className="ws-props__label">{label}</dt>
      <dd className="ws-props__value">{children}</dd>
    </div>
  );
}

// ---- Clamp: long body text, cut to a readable height -----------------------
// A description is read at the top of a page, on the way to something else. At
// full height a 60-line ticket body pushes its own comments, runs and activity
// off the screen, so the page opens on the one thing the reader has already
// decided about.
//
// The toggle appears ONLY when the content actually overflows, which is why
// this measures instead of counting lines: markdown carries code blocks, lists
// and images, and a line count says nothing about how tall any of them are.
// Measurement is skipped while open (the body is unclamped then, so it would
// measure as fitting and the control would vanish under the reader's hand).
export function Clamp({
  max = 260,
  moreLabel = "Show more",
  lessLabel = "Show less",
  children,
  className,
}: {
  /** Collapsed height in px. */
  max?: number;
  moreLabel?: string;
  lessLabel?: string;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const check = () => setOver(el.scrollHeight - el.clientHeight > 4);
    check();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, children]);

  return (
    <div className={cx("ws-clamp", className)}>
      <div
        ref={ref}
        className={cx("ws-clamp__body", !open && "is-clamped", !open && over && "is-faded")}
        style={open ? undefined : { maxHeight: max }}
      >
        {children}
      </div>
      {over && (
        <button type="button" className="ws-clamp__toggle" onClick={() => setOpen((v) => !v)}>
          {open ? lessLabel : moreLabel}
        </button>
      )}
    </div>
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
