// WHAT ADVANCES A THREAD WITHOUT A PERSON, in one place.
//
// The keystone of `docs/platform-redesign/threads-branches-and-pages-2026-08-29.md`
// section 1: there is one entity, the Thread, and two INDEPENDENT fields on it.
// `armed` decides whether it works unasked; `role` names the definition it runs
// under. Neither implies the other, so they are two controls, never one
// checkbox. "Agent" is a description of a thread that is armed, not a second
// kind of object, which is why there is no roster page and no conversion
// gesture.
//
// The list lives here rather than in the panel that edits it because three
// surfaces read it: the chat's own settings (which sets it), the chats list
// (which filters on it), and the row (which shows it). `chatModes.ts` is the
// same shape for the same reason.
//
// The backend clamps an unknown value to `off` (`services/chat_store.ARMED_MODES`);
// `armedOf` does the same on the way in, so a thread from before the field
// existed reads as "nothing advances it" rather than as a blank.

import { Clock, ListChecks, MessageSquare, Minus, type LucideIcon } from "lucide-react";
import type { ThreadSummary } from "../../lib/chat";

export type ArmedId = "off" | "on-message" | "schedule" | "on-ready-ticket";

export const ARMED_IDS: ArmedId[] = ["off", "on-message", "schedule", "on-ready-ticket"];

export interface ArmedOption {
  id: ArmedId;
  /** The control's label. What the operator picks. */
  name: string;
  /** The second line: what picking it actually does. */
  gloss: string;
  /** Names the thing beside it, per the icon rule. */
  Icon: LucideIcon;
}

export const ARMED_OPTIONS: ArmedOption[] = [
  {
    id: "off",
    name: "Only when I write",
    gloss: "Nothing advances this thread on its own. Every thread starts here.",
    Icon: Minus,
  },
  {
    id: "on-message",
    name: "On a message",
    gloss: "Any message on this thread advances it, whoever sent it.",
    Icon: MessageSquare,
  },
  {
    id: "schedule",
    name: "On a schedule",
    gloss: "A clock advances it, whether or not anyone is here.",
    Icon: Clock,
  },
  {
    id: "on-ready-ticket",
    name: "On a ready ticket",
    gloss: "A ticket going ready in this thread's scope advances it.",
    Icon: ListChecks,
  },
];

const BY_ID = new Map(ARMED_OPTIONS.map((o) => [o.id, o]));

/** This thread's armed setting, clamped. Unknown and absent both read `off`. */
export function armedOf(thread: Pick<ThreadSummary, "armed"> | null | undefined): ArmedId {
  const raw = thread?.armed;
  return BY_ID.has(raw as ArmedId) ? (raw as ArmedId) : "off";
}

/** Does anything at all advance this thread without a person. */
export function isArmed(thread: Pick<ThreadSummary, "armed"> | null | undefined): boolean {
  return armedOf(thread) !== "off";
}

export function armedOption(id: ArmedId): ArmedOption {
  return BY_ID.get(id) ?? ARMED_OPTIONS[0];
}

/** The row label: the short name, or empty for a thread nothing advances. A
 *  row does not spend width saying that the ordinary case is ordinary. */
export function armedLabel(thread: Pick<ThreadSummary, "armed"> | null | undefined): string {
  const id = armedOf(thread);
  return id === "off" ? "" : armedOption(id).name;
}

/** A role name, trimmed, or "". Free text by design: the human-facing name
 *  (Planner, Builder, Researcher) is a different question from the closed
 *  write-authority set the backend keeps in `agent_authority`. */
export function roleOf(thread: Pick<ThreadSummary, "role"> | null | undefined): string {
  return (thread?.role ?? "").trim();
}
