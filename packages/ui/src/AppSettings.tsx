import { useState, type ComponentType, type ReactNode } from "react";
import { SlidersHorizontal, ChevronDown, type LucideProps } from "lucide-react";
import { Card, SectionLabel } from "./layout";

// The standard app-settings slot. An app page renders <AppSettings> near the
// bottom of its single page to host its own user-facing config, without adding
// a second header (frontend/CLAUDE.md: one header per app). It is a collapsible
// disclosure so an app's config stays out of the way until asked for. Promoted
// from Workshop's `components/AppSettings.tsx` 2026-08-20 (Stage 4b): it was
// already built entirely from kit primitives and had no platform dependency,
// so the only change here is where the file lives.
//
// The two shells below (SettingsSection / SettingsRow) are the same label+card
// and label-beside-value row the platform Settings page uses, extracted so a
// moved app-settings section renders identically inside its app or in Settings.

export function AppSettings({
  title = "Settings",
  defaultOpen = false,
  children,
}: {
  title?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      <button
        type="button"
        className="ws-section-label"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          font: "inherit",
          color: "inherit",
          cursor: "pointer",
          display: "inline-flex",
          alignItems: "center",
          gap: "var(--space-2)",
        }}
      >
        <SlidersHorizontal size={14} strokeWidth={1.8} aria-hidden />
        {title}
        <ChevronDown
          size={14}
          strokeWidth={1.8}
          aria-hidden
          style={{
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform var(--dur-fast) var(--ease-out)",
          }}
        />
      </button>
      {open && <div style={{ marginTop: "var(--space-3)" }}>{children}</div>}
    </section>
  );
}

// Reusable section shell: design-system SectionLabel + Card.
//
// `title` is optional. Omit it when the section is the ONLY one on its screen
// and the page header already names it, which is every tab of Settings that
// holds a single section: the header said "Account" and the label said
// "ACCOUNT" directly under it, one word twice, plus the lit row in the page
// column saying it a third time. A label earns its place by telling groups
// apart, so one group means no label.
export function SettingsSection({
  icon: Icon,
  title,
  children,
}: {
  icon: ComponentType<LucideProps>;
  title?: string;
  children: ReactNode;
}) {
  return (
    <section style={{ marginBottom: "var(--space-6)" }}>
      {title && <SectionLabel icon={<Icon aria-hidden />}>{title}</SectionLabel>}
      <Card>{children}</Card>
    </section>
  );
}

// Label-beside-value row. Uses the inline-field grid; a hairline separates rows.
// `hint` is a caption under the control, for a setting whose current value
// needs a word of explanation (e.g. what a color scheme is meant to feel like).
export function SettingsRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className="ws-field ws-field--inline"
      style={{ alignItems: "baseline", padding: "var(--space-2) 0", borderBottom: "1px solid var(--border)" }}
    >
      <span className="ws-field__label ws-muted">{label}</span>
      <span>
        {children}
        {hint && <span className="ws-field__help" style={{ display: "block", marginTop: "var(--space-2)" }}>{hint}</span>}
      </span>
    </div>
  );
}
