// Shared Agent trigger vocabulary for the frontend. Mirrors the normalized
// trigger shape emitted by backend/services/triggers.py (kind manual|cron|event
// |loop). Used by the Agents roster and the Agent detail surface.

export type TriggerKind = "manual" | "cron" | "event" | "discord" | "loop";

export type Trigger = {
  kind: TriggerKind;
  cron_expression?: string;
  event_sources?: string[];
  channels?: string[];
  max_iterations?: number;
  budget?: { max_wall_s?: number };
  completion_gate?: string;
};

export type AgentRuntime = "builder" | "assistant";

// One unified Agent as returned by GET /api/agents/ and GET /api/agents/:id.
export type UnifiedAgent = {
  id: string;
  slug: string;
  runtime: AgentRuntime;
  name: string;
  purpose?: string;
  triggers?: Trigger[];
  limits?: { max_iterations?: number; budget?: { max_wall_s?: number } };
  capabilities?: string[];
  memory?: string | null;
  project_slug?: string | null;
  scope?: "user" | "project";
  state?: string | null;
  claude_session?: string | null;
  created_at?: string;
  last_active?: string;
  memory_blocks?: string[];
  source?: string;
};

export const EVENT_SOURCES = ["plaid", "gmail", "calendar", "github", "open-wearables", "bluebubbles", "custom"];

// A one-line human summary of an agent's triggers for the roster card.
export function triggerSummary(triggers?: Trigger[]): string {
  if (!triggers || triggers.length === 0) return "Manual, on demand";
  return triggers
    .map((t) => {
      if (t.kind === "cron") return `Cron ${t.cron_expression || "(unset)"}`;
      if (t.kind === "event") return `Event: ${(t.event_sources || []).join(", ") || "(none)"}`;
      if (t.kind === "discord") {
        const ch = t.channels || [];
        return `Discord: ${ch.length ? `${ch.length} channel${ch.length > 1 ? "s" : ""}` : "no channels (inert)"}`;
      }
      if (t.kind === "loop") return `Continuous loop (max ${t.max_iterations ?? "?"} iters)`;
      return "Manual";
    })
    .join(" · ");
}

// A short "next scheduled run" label. Full cron parsing is out of scope; the
// schedule expression is the truthful, useful read.
export function nextScheduled(triggers?: Trigger[]): string {
  const cron = triggers?.find((t) => t.kind === "cron");
  if (cron) return cron.cron_expression ? `schedule ${cron.cron_expression}` : "scheduled";
  if (triggers?.some((t) => t.kind === "loop")) return "continuous";
  if (triggers?.some((t) => t.kind === "event")) return "on event";
  return "on demand";
}

// WHICH AGENCY TIER AN AGENT BELONGS TO — the color axis, from the same facts.
//
// An agent that can START WORK ON ITS OWN is autonomous, and its card says so
// before you read a word of it. One that only ever runs when you ask is
// `system`: machine work inside a conversation you are present for. There is no
// `you` tier for an agent; an agent is never the operator.
//
// SELF-STARTING IS THE TEST, NOT "NOT MANUAL". `discord` is a trigger kind, and
// an agent you message on Discord is answering YOU, with you present — that is
// the system tier, however the work reaches it. Only a clock, an outside event
// and a continuous loop run with nobody watching.
const SELF_STARTING: TriggerKind[] = ["cron", "event", "loop"];

export function agentAgency(triggers?: Trigger[]): "system" | "autonomous" {
  const self = triggers?.some((t) => SELF_STARTING.includes(t.kind));
  return self ? "autonomous" : "system";
}
