import { useCallback, useState, type ReactNode } from "react";
import { AlertTriangle, Check, Copy, Eye, EyeOff, Inbox, Info } from "lucide-react";
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

// ---- Notice: the other half of rule 9 --------------------------------------
// A REGION (`StateBlock`) stands in for content that is not there. A LINE
// (`Notice`) sits beside content that is. Reach for this whenever the thing
// that failed did not empty the screen: a save that did not go through, a form
// submit that was rejected, a connection that dropped under a full transcript.
//
// Rendering those as a `StateBlock` is how a one-sentence problem became a
// tall, centred, dashed panel in the middle of a working page. It is the most
// common design mistake in this repo and it has exactly one cause: the region
// was the only error shape the kit offered.
export function Notice({
  tone = "info",
  icon,
  children,
  detail,
  where,
  action,
  className,
}: {
  /** `error` and `warn` tint the icon, which is the only hue on the line. */
  tone?: "info" | "warn" | "error";
  icon?: ReactNode;
  /** The sentence. What happened, in the reader's terms. */
  children: ReactNode;
  /** The cause, if it adds something the sentence cannot: a server message, an
   *  exception. Muted, and part of the same line. */
  detail?: ReactNode;
  /** The endpoint that failed. Pass it where it is actionable (a page-level
   *  failure someone will debug), leave it off where it is noise (a dropped
   *  poll the reader can only retry). */
  where?: ReactNode;
  /** A control, when retrying is a real action. */
  action?: ReactNode;
  className?: string;
}) {
  const fallback = tone === "info" ? <Info aria-hidden /> : <AlertTriangle aria-hidden />;
  return (
    <div
      className={cx("ws-notice", tone !== "info" && `ws-notice--${tone}`, className)}
      role={tone === "error" ? "alert" : undefined}
    >
      <span className="ws-notice__icon">{icon ?? fallback}</span>
      {/* A div, not a span: the sentence and its cause are inline (one thought,
          one line where it fits), but a caller may hang a disclosure under
          them, and the chat's blocked-network notice does. */}
      <div className="ws-notice__body">
        <span>{children}</span>
        {detail != null && <> <span className="ws-notice__detail">{detail}</span></>}
        {where != null && <> <span className="ws-notice__where">{where}</span></>}
      </div>
      {action != null && <span className="ws-notice__action">{action}</span>}
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
