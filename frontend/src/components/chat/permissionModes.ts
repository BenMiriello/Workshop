import { FastForward, Map, Pause, ShieldOff, Zap } from "lucide-react";

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
  // One line, shown in the menu row. It says what runs without asking AND what
  // still stops to ask, because the difference between these modes is where
  // that line falls, not whether there is one.
  hint: string;
  Icon: typeof Map;
  // Renders in the danger tone: nothing is checked in this mode.
  danger?: boolean;
};

// The hints are short clauses of plain fact. They were two sentences of
// explanation; the operator asked for plain compact facts, not a description of
// each mode's character. What a hint may never do is promise the absence of a
// prompt this deployment will still show: `bypassPermissions` is the only mode
// launched without an approval channel, so it is the only one that may claim to
// never ask.
const DEFS: Record<string, PermissionModeDef> = {
  default: {
    id: "default",
    // Pause, not Hand: it is Claude Code's own glyph for this mode, and it says
    // the right thing — the turn stops at each edit and waits for you.
    label: "Manual",
    hint: "Asks before every edit and command.",
    Icon: Pause,
  },
  plan: {
    id: "plan",
    label: "Plan",
    hint: "Researches and proposes. Changes nothing.",
    Icon: Map,
  },
  acceptEdits: {
    id: "acceptEdits",
    // FastForward, not a pencil: a pencil says "this mode edits", which is true
    // of every mode here. Fast-forward says the one thing that is actually
    // different about it — it skips PAST the ask, for edits only — and reads
    // apart from Auto's Zap at a glance instead of both looking like "writes".
    label: "Edit automatically",
    hint: "Edits without asking. Asks for anything riskier.",
    Icon: FastForward,
  },
  auto: {
    id: "auto",
    label: "Auto",
    // NOT "acts without asking", which is what this said until 2026-09-17 and
    // is false here: `model_caps.NEEDS_APPROVAL_CHANNEL` covers every mode but
    // bypass, so an auto turn is launched WITH an approval channel and does
    // stop when the safety check gates a call. The operator reported exactly
    // that promise being broken ("it should not be asking me for permissions
    // when it is in auto mode"), and the honest fix for a hint is to describe
    // the mode that exists rather than to offer the one that never asks.
    hint: "Acts on its own. Stops for a call the safety check gates.",
    Icon: Zap,
  },
  bypassPermissions: {
    id: "bypassPermissions",
    label: "Bypass",
    hint: "Acts without asking. No checks.",
    Icon: ShieldOff,
    danger: true,
  },
};

// Every mode this table can say, for tests and for anything that has to walk
// the set. NOT what the picker offers: that is `Provider.modes.options`, a
// runtime fact from the backend (see the note at the top of this file).
export const PERMISSION_MODE_IDS: readonly string[] = Object.keys(DEFS);

// An unknown id renders under its own name rather than vanishing, so a mode the
// backend adds is usable before this table knows it.
//
// Bypass keeps its entry above even though the picker no longer OFFERS it: a
// chat that chose it before it was withdrawn still has to render its own setting
// under the right name rather than as a blank or as somebody else's mode.
export function permissionModeDef(id: string | undefined): PermissionModeDef {
  if (id && DEFS[id]) return DEFS[id];
  return { id: id || "auto", label: id || "Auto", hint: "", Icon: Zap };
}
