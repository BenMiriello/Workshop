// One way to poll, for the whole app.
//
// Workshop is installed as a PWA and is never closed, so "poll every N seconds
// while mounted" is not the harmless thing it looks like: a backgrounded phone
// kept running every timer on every mounted page, waking the radio and the CPU
// on a device that was in someone's pocket. Roughly forty `setInterval` sites
// had each made this decision independently, and exactly two of them checked
// whether the app was visible.
//
// So the decision is made once, here, and it has a default: A POLL PAUSES WHILE
// THE APP IS HIDDEN, and runs immediately when it comes back. The catch-up run
// is the half that makes pausing safe to default. Without it a resumed tab
// shows stale data for up to a full interval, which is the "the browser does
// not reflect the backend" problem that made these polls fast in the first
// place, so pausing without it would just trade one bug for another.
//
// A caller that genuinely must keep ticking while hidden says so
// (`whenHidden: "continue"`) and, by saying so, documents why.
//
// What this does NOT replace: timers that are not polls. A bounded retry that
// gives up after N tries, an audio meter, a debounce, a one-shot delay. Those
// are their own logic and they stay where they are.
import { useEffect, useRef } from "react";

export type WhenHidden = "pause" | "continue";

/** Whether a poll should be ticking right now. Pure so it can be tested; the
 *  hook is only the wiring around it. */
export function pollShouldRun(
  { enabled, hidden, whenHidden }: { enabled: boolean; hidden: boolean; whenHidden: WhenHidden },
): boolean {
  if (!enabled) return false;
  return whenHidden === "continue" || !hidden;
}

/**
 * Call `fn` every `intervalMs` while mounted.
 *
 * @param opts.enabled     poll only while true. Default true. Flipping it to
 *                         false stops the timer; flipping it back runs `fn`
 *                         immediately, the same as returning from hidden.
 * @param opts.immediate   run `fn` once on mount. Default true, because almost
 *                         every caller was doing `load(); setInterval(load, n)`
 *                         and the ones that were not had simply forgotten to.
 * @param opts.whenHidden  "pause" (default) or "continue".
 */
export function usePoll(
  fn: () => void | Promise<void>,
  intervalMs: number,
  opts: { enabled?: boolean; immediate?: boolean; whenHidden?: WhenHidden } = {},
): void {
  const { enabled = true, immediate = true, whenHidden = "pause" } = opts;

  // Callers pass inline lambdas. Reading through a ref keeps a new closure from
  // tearing down and re-arming the timer on every render, which would reset the
  // interval continuously and, for a fast poll, mean it never fires at all.
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;

    let timer: number | undefined;
    const run = () => { void fnRef.current(); };

    const stop = () => { window.clearInterval(timer); timer = undefined; };
    const start = (runNow: boolean) => {
      if (runNow) run();
      if (timer === undefined) timer = window.setInterval(run, intervalMs);
    };

    const sync = (runNow: boolean) => {
      if (pollShouldRun({ enabled, hidden: document.hidden, whenHidden })) start(runNow);
      else stop();
    };

    // On mount the catch-up run is the caller's `immediate`; on every later
    // transition back to visible it is unconditional, because by then there is
    // real elapsed time to catch up on.
    sync(immediate);

    const onVisibility = () => sync(true);
    document.addEventListener("visibilitychange", onVisibility);
    // Focus too: a PWA restored from the app switcher does not always fire
    // visibilitychange, and a poll that silently never resumes is worse than
    // one that never paused.
    window.addEventListener("focus", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onVisibility);
    };
  }, [enabled, intervalMs, immediate, whenHidden]);
}
