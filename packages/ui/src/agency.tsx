import type { ReactNode } from "react";
import { cx } from "./cx";

// AGENCY = WHO DID THIS. The second axis of the color system, beside status.
//
//   you          your hand on it: typed, clicked, confirmed
//   system       machine work inside a conversation you are present for
//   autonomous   it moved with nobody watching (cron, sweep, watchdog, event)
//
// Two rules, enforced by the CSS in components.css and by these primitives:
//
//   1. AGENCY IS FILLED, STATUS IS OUTLINE. An agency chip has a soft fill and
//      no border; a status chip (StatusPill) has a transparent background and a
//      hairline. Never give a status element a fill or an agency element a bare
//      outline — that pairing is what keeps the two vocabularies apart, and it
//      is why they can share a hue in some scheme without confusion.
//   2. ONE AGENCY COLOR PER SURFACE. A card is never both system and
//      autonomous. If an autonomous agent calls a tool, the card stays
//      autonomous: provenance beats mechanism.
//
// `agency` on a container sets the tier for everything inside it, including
// which tone `running` resolves to — a row that knows a job came from cron
// says so once, and its dot, its running chip and its progress bar all agree.
export type AgencyTier = "you" | "system" | "autonomous";

// Props to spread on any element to scope it to a tier.
export function agencyProps(tier: AgencyTier): { "data-agency": AgencyTier } {
  return { "data-agency": tier };
}

// The vocabulary the platform already records for a bounded execution
// (services/jobs.py TRIGGERS, services/triggers.py) mapped onto the axis.
// `manual` is the only one you did; `discord` and `chat` are you talking to it,
// so the work happens with you present; the rest start themselves.
const TRIGGER_TIER: Record<string, AgencyTier> = {
  manual: "you",
  chat: "system",
  discord: "system",
  cron: "autonomous",
  event: "autonomous",
  loop: "autonomous",
};

// The tier for a job/run/agent trigger, or NULL when the record does not say.
//
// Null is not a formality: more than half the jobs on this box predate the
// trigger field, and neither answer is safe for them. Calling an unknown run
// `autonomous` raises a false alarm about something you may well have started
// yourself; calling it `system` asserts you were present for work that is by
// definition not a chat turn. So it makes NO CLAIM, and a caller renders no
// identity mark at all — an absent provenance mark is honest, an invented one
// is not. Callers must handle null rather than defaulting it.
export function agencyFor(trigger?: string | null): AgencyTier | null {
  if (!trigger) return null;
  return TRIGGER_TIER[trigger] ?? null;
}

export const AGENCY_LABEL: Record<AgencyTier, string> = {
  you: "You",
  system: "System",
  autonomous: "Autonomous",
};

// Filled chip naming the tier. Uppercase mono, no dot, no border — a different
// shape and typeface from both StatusPill (outlined) and Tag (categorical), so
// all three can sit in one row and stay legible as three different statements.
export function AgencyChip({
  tier, children, icon, className,
}: {
  tier: AgencyTier;
  /** Defaults to the tier's own name. */
  children?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <span className={cx("ws-agency", className)} data-agency={tier}>
      {icon}
      {children ?? AGENCY_LABEL[tier]}
    </span>
  );
}

// The identity dot on a row: who this belongs to. Pairs with a StatusPill on
// the other end of the row — the dot says WHO, the chip says HOW IT WENT.
// `active` switches to the tier's `-up` tone and pulses a ring; it means
// HAPPENING RIGHT NOW, never unread and never severity.
export function AgencyDot({
  tier, active, label, className,
}: {
  tier: AgencyTier;
  active?: boolean;
  /** Screen-reader name. Defaults to the tier's own name. */
  label?: string;
  className?: string;
}) {
  return (
    <span
      className={cx("ws-agency-dot", active && "is-active", className)}
      data-agency={tier}
      role="img"
      aria-label={label ?? AGENCY_LABEL[tier]}
    />
  );
}
