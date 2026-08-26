// The shapes the app runner reports, and the one question the Run panel asks of
// them repeatedly: is this app mid-transition?
//
// The predicate lives here rather than in the panel because it decides the
// panel's POLL CADENCE as well as its rendering, and those two readers must not
// be able to disagree. It is a pure function of one status payload so both can
// call it, and so it can be executed by a test (frontend/CLAUDE.md: the target
// is the pure rule behind a behaviour, extracted into its own module).

export type AppServiceState =
  | "pending" | "building" | "starting" | "running"
  | "failed" | "stopped" | "disabled" | "crashlooping";

export type Service = {
  name: string;
  status: AppServiceState;
  pid: number | null;
  port: number | null;
  url: string | null;
  expose: boolean;
  healthy: boolean | null;
  error: string | null;
  // Crash restarts only, counted by docker. An operator pressing Restart never
  // moves this, which is the whole point of it.
  restarts: number;
  oom_killed?: boolean;
  exit_code?: number | null;
  started_at?: string | null;
};

export type Dep = { name: string; ok: boolean; error?: string | null };

export type AppStatus = {
  slug: string;
  phase: string;
  desired: string;
  manifest_detected?: boolean;
  services: Service[];
  deps: Dep[];
};

/**
 * Is the app moving right now: coming up as a whole, or with any single service
 * still building or starting. Nothing has been read yet counts as NOT in
 * flight, the same as an app sitting still.
 */
export function appWorkInFlight(status: AppStatus | null | undefined): boolean {
  if (!status) return false;
  if (status.phase === "starting") return true;
  return (status.services || []).some(
    (s) => s.status === "building" || s.status === "starting",
  );
}
