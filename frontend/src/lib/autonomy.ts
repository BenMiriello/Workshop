import type { StatusKind } from "../ui";

// The ONE rule for turning the autonomy pair into something the operator reads.
//
// Two facts are independent and must never be collapsed into one word:
//   running — a worker process is resident (the API probes its flock)
//   enabled — the off-switch is clear, i.e. work is PERMITTED
//
// Collapsing them is a bug this codebase has now shipped twice. QueueRunner was
// written specifically to kill it, and then AutonomyControls' summary row did it
// again: `allStopped = cronDisabled` said "all autonomy: running" purely because
// the override flag was clear, directly above a row correctly reporting that
// nothing was running. Same card, same render, contradicting.
//
// So the rule lives here, once, and both surfaces import it. There is no second
// place to get it wrong.

export type AutonomyPair = {
  running: boolean;
  enabled: boolean;
};

export type AutonomyView = {
  kind: StatusKind;
  label: string;
  hint: string;
};

export function describeAutonomy({ running, enabled }: AutonomyPair): AutonomyView {
  if (running && enabled) {
    return {
      kind: "running",
      label: "Running",
      hint: "Tickets move from queued to running to done on their own.",
    };
  }
  if (running && !enabled) {
    return {
      kind: "warn",
      label: "Paused",
      hint: "Autonomy is switched off, so no ticket will be picked up.",
    };
  }
  if (!running && enabled) {
    return {
      kind: "warn",
      label: "Nothing running",
      hint: "Work is permitted, but no worker is resident. Filed tickets will queue.",
    };
  }
  return {
    kind: "stopped",
    label: "Stopped",
    hint: "Nothing is running, so filed tickets sit in the queue.",
  };
}

// Work only happens when BOTH hold. This is what the Stop control keys off, so a
// "Stop all" is never offered against a state where nothing is running.
export function autonomyIsLive(s: AutonomyPair): boolean {
  return s.running && s.enabled;
}
