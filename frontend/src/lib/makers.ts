// The maker agent, as the roster and its page read it.
//
// THE ONE THING THIS FILE IS FOR: saying what the thing that works the tickets
// is doing, without inventing a state the backend cannot observe. The API's
// `activity` has exactly three values (`absent | present | working`) and
// `present` deliberately covers BOTH "resident, idling on its poll" and
// "resident, mid-sweep, has not claimed a ticket yet", because the presence
// channel carries no structural difference between them. So nothing here
// branches on which of the two it is, and nothing here prints a fourth word.
// `activity_detail` is the backend's own sentence about that ambiguity and the
// page renders it verbatim.
//
// Pure on purpose (frontend/CLAUDE.md: "the target of a test is always the pure
// rule behind a behaviour"). This repo has no DOM harness, so the rules that
// decide a card's colour and its first line live here and are executed by
// makers.test.ts.
import type { StatusKind } from "@workshop/ui";
import type { components } from "../api/schema.gen";
import { type AutonomyMode } from "./autonomy";

// Generated from the backend's own OpenAPI schema, never hand-written
// (frontend/CLAUDE.md: "API response types are GENERATED").
export type MakerSummary = components["schemas"]["MakerSummary"];
export type MakerState = components["schemas"]["MakerState"];
export type MakerSession = components["schemas"]["MakerSession"];
export type MakerActivity = MakerSummary["activity"];

// The arm level IS the scope's execution mode, which the app already has a
// vocabulary for. Importing it rather than redeclaring three strings is what
// stops the maker page and the chat's Work settings from drifting into two
// names for one setting.
export type ArmLevel = AutonomyMode;

export type MakerStatus = { kind: StatusKind; label: string };

/** The five status kinds, applied truthfully to a maker.
 *
 *  The case this exists for: ARMED WITH NO WORKER RESIDENT IS `warn`, never
 *  `ok`. That is precisely the disconnected state the operator could not see
 *  anywhere in the UI, and a green dot over it would hide it again. Likewise a
 *  scope that is disarmed while a worker is still on the tree is not simply
 *  "off": something is still there, so it warns until the tree is clear.
 */
export function makerStatus(m: MakerSummary): MakerStatus {
  // Liveness first. A session holding a ticket is working whatever the arm
  // level says, which matters while a stop is settling.
  if (m.activity === "working") return { kind: "running", label: "Working" };
  if (!m.armed) {
    return m.runner_resident || m.activity === "present"
      ? { kind: "warn", label: "Off, worker still resident" }
      : { kind: "stopped", label: "Off" };
  }
  // The global brake sits above every scope, so an armed maker under it is not
  // going to run and must not read as healthy.
  if (m.global_override_engaged) return { kind: "warn", label: "Brake on" };
  if (!m.runner_resident) return { kind: "warn", label: "No worker" };
  return { kind: "ok", label: "Ready" };
}

/** A card's first line: what this maker is doing, and the ticket if it holds
 *  one. The ticket is returned separately from the sentence because it is a
 *  LINK on screen, and this module does not build routes. */
export type MakerPrimary = {
  /** The sentence, or the words that lead into the ticket link. */
  text: string;
  /** Set only when a live session actually names a ticket. */
  ticketId: string | null;
  ticketLabel: string | null;
  ticketStatus: string | null;
};

export function makerPrimary(m: MakerSummary): MakerPrimary {
  const none = { ticketId: null, ticketLabel: null, ticketStatus: null };
  if (m.activity === "working") {
    if (m.ticket_id) {
      return {
        text: "Working on",
        ticketId: m.ticket_id,
        ticketLabel: m.ticket_title || m.ticket_id,
        ticketStatus: m.ticket_status ?? null,
      };
    }
    // Reported as working with no subject id. Say that rather than printing a
    // ticket link to nowhere.
    return { text: "Working. The live session did not name a ticket.", ...none };
  }
  if (!m.armed) {
    // The status word beside this line already says "Off", so the line says
    // what that means for the tickets instead of repeating it.
    return {
      text: m.runner_resident || m.activity === "present"
        ? "A worker is still on this tree."
        : "Filed tickets wait in the queue.",
      ...none,
    };
  }
  if (m.global_override_engaged) {
    return {
      text: m.activity === "present"
        ? "A worker is here, but the global brake is on."
        : "Armed, but the global brake is on.",
      ...none,
    };
  }
  if (m.activity === "present") {
    // NOT "idle". The channel cannot tell idling from sweeping, so the line
    // states only what it can see: somebody is here, holding no ticket.
    return { text: "A worker is here with no ticket claimed.", ...none };
  }
  return { text: "Armed, but no worker is on this tree.", ...none };
}

// The arm level, named the way the rest of the app names it (the chat's Work
// settings use exactly these three words for the same three modes).
const ARM_LEVEL_COPY: Record<ArmLevel, { name: string; gloss: string }> = {
  reactive: {
    name: "Off",
    gloss: "Filed tickets wait for you. This maker never picks one up on its own.",
  },
  supervised: {
    name: "Run to review",
    gloss: "It works ready tickets in the background and stops at review for your approval.",
  },
  autonomous: {
    name: "Run to done",
    gloss: "It works ready tickets in the background all the way to done.",
  },
};

export function armLevelName(level: ArmLevel): string {
  return ARM_LEVEL_COPY[level].name;
}

export function armLevelGloss(level: ArmLevel): string {
  return ARM_LEVEL_COPY[level].gloss;
}

/** How far unattended work may advance, in words. `ceiling` is null when the
 *  scope is reactive, which is not "no limit" but "nothing runs at all". */
export function ceilingLine(m: Pick<MakerSummary, "armed" | "ceiling">): string {
  if (!m.armed) return "Nothing runs unattended, so no stage is reached on its own.";
  if (m.ceiling === "in_review") return "Unattended work stops at review.";
  if (m.ceiling === "done") return "Unattended work goes through to done.";
  return "No stage ceiling is set for this scope.";
}

/** What Start and Stop actually do, as button help. Both change the ARM LEVEL,
 *  not just a process, and a reader who is not told that is surprised when
 *  stopping a worker also switches unattended work off. */
export const START_HELP =
  "Start arms this scope (at run to review, if it was off) and puts a worker on the tree.";
export const STOP_HELP =
  "Stop disarms this scope and ends the resident worker. A ticket half-worked goes back on the queue.";

/** "1 of 2 makers on this tree", or null when there is nothing to say.
 *
 *  SEVERAL MAKERS ON ONE SCOPE IS LEGAL: the registry exempts `maker` from its
 *  one-per-scope rule, because concurrent makers on disjoint `touches` is a
 *  real case. What is not legal is hiding it. On the live install two trees had
 *  two makers each, and which one the roster showed was decided by which
 *  definition FILENAME sorted first -- so `birds-builder-2` outranked
 *  `birds-builder` for no reason anyone chose. One maker says nothing here;
 *  more than one says how many, on every card that is one of them.
 */
export function peerNote(m: Pick<MakerSummary, "scope_makers">,
                         slug?: string | null): string | null {
  const peers = m.scope_makers || [];
  if (peers.length < 2) return null;
  const i = slug ? peers.indexOf(slug) : -1;
  const which = i >= 0 ? `${i + 1} of ${peers.length}` : `one of ${peers.length}`;
  return `${which} makers on this tree`;
}
