// Who, if anyone, will actually run a ticket filed to a scope.
//
// The backend answers this in `scope_pickup` (routes/tasks_all.py), served on
// GET /api/tasks/scopes as a `pickup` map and on the chat's ticket-scope
// endpoint. It separates two questions the UI kept conflating:
//
//   runnable   a worker CAN be launched here by hand ("Run now", "File and run")
//   automatic  something picks the ticket up WITHOUT the operator pressing anything
//
// `automatic` is false far more often than it looks: one resident queue runner
// covers the workshop root and nothing else, and today zero registered projects
// carry a `loop` trigger. A ticket filed to `project:<slug>` therefore sits
// until someone presses Run. The operator has to learn that BEFORE filing,
// which is the whole reason the predicate exists, and is exactly what the chat
// card path was skipping.

export type Pickup = {
  scope: string;
  runnable: boolean;
  automatic: boolean;
  why: string;
};

export type PickupMap = Record<string, Pickup>;

// Mirrors backend scope_is_runnable: launch_task_run accepts these three scope
// shapes and 400s on everything else (a general chat resolves to `general`, so
// "file and run" there could only ever file).
export function scopeIsRunnable(scope: string | null | undefined): boolean {
  if (!scope) return false;
  return (
    scope === "workshop"
    || scope.startsWith("project:")
    || scope.startsWith("agent:")
  );
}

// The pickup answer for a scope, from the server map when it has one.
//
// A scope with no task store yet is absent from the map. That is not "unknown":
// nothing is polling a store that does not exist, so it answers `automatic:
// false` with the reason, rather than defaulting to the optimistic case.
export function pickupFor(map: PickupMap | null | undefined, scope: string): Pickup {
  const hit = map?.[scope];
  if (hit) return hit;
  const runnable = scopeIsRunnable(scope);
  return {
    scope,
    runnable,
    automatic: false,
    why: runnable
      ? "no ticket store exists for this scope yet, so nothing is polling it"
      : "this scope cannot run tickets; file it somewhere runnable",
  };
}

// The one sentence to put in front of the operator, or null when the honest
// answer is "something will pick this up" and there is nothing to warn about.
export function pickupNote(p: Pickup): string | null {
  if (!p.runnable) {
    return `${p.why.charAt(0).toUpperCase()}${p.why.slice(1)}. Filing works here; running does not.`;
  }
  if (p.automatic) return null;
  const because = p.why ? `: ${p.why}` : "";
  return `Nothing will pick this up on its own${because}. Run it yourself when you want it done.`;
}
