import { Hand, Map, PencilLine, ShieldOff, Zap } from "lucide-react";

// How much a chat asks before it acts, as the UI says it.
//
// THE LABELS ARE THE VENDOR'S. "Manual" is Claude Code's own name for the mode
// whose stored value is `default`, and it is what the CLI, the VS Code extension
// and the desktop app all print. Inventing a better word here would mean the
// operator reads one name in this composer and a different one in the terminal
// for the same setting.
//
// The OPTIONS come from the backend (`Provider.modes.options`), not from this
// file: `auto` is account-gated and can be turned off by managed settings, so
// which modes exist is a runtime fact. What lives here is only how to say one.
//
// Note this is NOT the chat's `mode` field — that is Direct/Tickets, about where
// work goes, and it presents as "Tickets" in the header. This is about how much
// the turn asks before touching anything.

export type PermissionModeDef = {
  id: string;
  label: string;
  // One line, shown in the menu row. It says what runs WITHOUT asking, because
  // that is the actual difference between these modes.
  hint: string;
  Icon: typeof Hand;
  // Renders in the danger tone: nothing is checked in this mode.
  danger?: boolean;
};

const DEFS: Record<string, PermissionModeDef> = {
  default: {
    id: "default",
    label: "Manual",
    hint: "Reads freely. Asks before every edit and command.",
    Icon: Hand,
  },
  plan: {
    id: "plan",
    label: "Plan",
    hint: "Researches and proposes. Makes no changes until you approve.",
    Icon: Map,
  },
  acceptEdits: {
    id: "acceptEdits",
    label: "Edit automatically",
    hint: "Edits files without asking. Still asks for anything riskier.",
    Icon: PencilLine,
  },
  auto: {
    id: "auto",
    label: "Auto",
    hint: "Acts without asking, with background safety checks.",
    Icon: Zap,
  },
  bypassPermissions: {
    id: "bypassPermissions",
    label: "Bypass",
    hint: "Acts without asking and without checks.",
    Icon: ShieldOff,
    danger: true,
  },
};

// An unknown id renders under its own name rather than vanishing, so a mode the
// backend adds is usable before this table knows it.
export function permissionModeDef(id: string | undefined): PermissionModeDef {
  if (id && DEFS[id]) return DEFS[id];
  return { id: id || "bypassPermissions", label: id || "Bypass", hint: "", Icon: ShieldOff };
}

/** Plan is a ONE-TURN posture: approving the plan leaves it, which is what the
 *  CLI's own setMode suggestion says to do. Callers use this to avoid treating a
 *  plan-mode chat as permanently in plan mode. */
export const ONE_TURN_MODES = ["plan"];
