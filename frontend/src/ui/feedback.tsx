import { useCallback, useState, type ReactNode } from "react";
import { AlertTriangle, Check, Copy, Eye, EyeOff, Inbox } from "lucide-react";
import { cx } from "./cx";
import { Button } from "./controls";

// ---- State blocks: loading / empty / error ---------------------------------
// Every list/page ships all four states. Empty ALWAYS carries an instructional
// hint (how to populate it); error names the failing endpoint + offers retry.
export function StateBlock({
  variant = "empty",
  icon,
  title,
  hint,
  action,
  className,
}: {
  variant?: "empty" | "error";
  icon?: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const fallbackIcon = variant === "error" ? <AlertTriangle aria-hidden /> : <Inbox aria-hidden />;
  return (
    <div className={cx("ws-state", variant === "error" && "ws-state--error", className)}>
      <span className="ws-state__icon">{icon ?? fallbackIcon}</span>
      <div className="ws-state__title">{title}</div>
      {hint != null && <div className="ws-state__hint">{hint}</div>}
      {action}
    </div>
  );
}

// Shimmer line for loading rows. Width is caller-controlled.
export function Skeleton({ width, className, style }: { width?: string; className?: string; style?: React.CSSProperties }) {
  return <span className={cx("ws-skeleton", className)} style={{ width, display: "block", ...style }} />;
}

// ---- Sticky action bar (Edit-mode Save / Cancel) ---------------------------
export function ActionBar({
  children,
  flash,
  className,
}: {
  children: ReactNode;
  flash?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("ws-action-bar", className)}>
      {children}
      <span className="ws-action-bar__spacer" />
      {flash != null && <span className="ws-action-bar__flash">{flash}</span>}
    </div>
  );
}

// ---- CopyButton ------------------------------------------------------------
// Mono value + a copy icon button. Commits on click (a view pref), flashes a
// check for ~1.1s. Use for URLs / endpoints / ids in Settings.
export function CopyButton({ value, className }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    const done = () => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1100);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(value).then(done, done);
    else done();
  }, [value]);
  return (
    <span className={cx("ws-copy", className)}>
      <span className="ws-copy__val">{value}</span>
      <Button size="sm" variant="ghost" icon onClick={copy} aria-label="Copy">
        {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
      </Button>
    </span>
  );
}

// ---- RevealField -----------------------------------------------------------
// Secret value hidden behind dots; an eye toggle reveals it (commits on change,
// a view pref). Use for keys/tokens in Settings.
export function RevealField({ value, className }: { value: string; className?: string }) {
  const [shown, setShown] = useState(false);
  return (
    <span className={cx("ws-copy", className)}>
      <span className={cx("ws-reveal__val", !shown && "ws-reveal__val--hidden")}>
        {shown ? value : "•".repeat(Math.min(value.length, 16) || 8)}
      </span>
      <Button size="sm" variant="ghost" icon onClick={() => setShown((s) => !s)} aria-label={shown ? "Hide" : "Reveal"}>
        {shown ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
      </Button>
    </span>
  );
}
