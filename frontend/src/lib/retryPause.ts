// WAITING TO ASK AGAIN.
//
// A read that keeps asking until the server answers (the boot session read,
// the app catalog) waits between asks, and two things change how long. A hidden
// page holds the wait until it is shown again: nobody is waiting on the answer,
// and a phone in a pocket should not poll. The network coming back, the window
// regaining focus, or the page being shown ends every wait at once, because each
// is the moment an answer is both most likely and most wanted.

/** 0.5s doubling, capped at 5s. */
export function cappedBackoff(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 5000);
}

const waiting = new Set<() => void>();
let listening = false;

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function wakeAll(): void {
  if (hidden()) return;
  for (const go of [...waiting]) go();
}

function listen(): void {
  if (listening || typeof window === "undefined" || typeof document === "undefined") return;
  listening = true;
  window.addEventListener("online", wakeAll);
  window.addEventListener("focus", wakeAll);
  document.addEventListener("visibilitychange", wakeAll);
}

/** Wait `ms`, or less if the page comes back into use, or longer if it is
 *  hidden when the time is up. */
export function pauseForRetry(ms: number): Promise<void> {
  listen();
  return new Promise((resolve) => {
    const go = () => {
      clearTimeout(timer);
      waiting.delete(go);
      resolve();
    };
    const timer = setTimeout(() => {
      if (!hidden()) go();
    }, ms);
    waiting.add(go);
  });
}
