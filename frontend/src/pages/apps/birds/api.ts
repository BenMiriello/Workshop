// The bird pages' data layer: endpoint, fetch helpers, and the small
// formatters that describe a detection.
//
// SPLIT OUT OF ui.tsx ON PURPOSE, and the reason is bundle weight rather than
// tidiness. `ui.tsx` imports recharts, so anything that reached into it for
// `jget` pulled the whole charting library along behind a one-line fetch
// wrapper. `scope.tsx` did exactly that, and because the Birds scope provider
// is imported eagerly by the app registry, recharts landed in the MAIN chunk:
// every visitor downloaded it, including the ones who never open Birds.
//
// Nothing in this file may import a component, a chart, or `@workshop/ui`.
// That constraint is the whole point of it existing.

export const API = "/api/birds";
export const pct = (c: number) => `${Math.round(c * 100)}%`;
// "##% confidence" — the wording the species/detection rows use.
export const conf = (c: number) => `${Math.round(c * 100)}% confidence`;
// Hour-of-day label in 12-hour am/pm (the app uses 12-hour time everywhere).
export const hh = (h: number) => {
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12} ${ap}`;
};

// A device is "recording" if it posted very recently. The Pi yard bridge POSTs a
// block every ~3s while it's streaming, and the browser recorder refreshes
// last_seen while live, so a 60s window cleanly separates "recording right now"
// from "went quiet". Used to drive the live indicator on the header + device cards.
export const RECORDING_WINDOW_S = 60;
export function isRecording(lastSeen: string | null, withinSec = RECORDING_WINDOW_S): boolean {
  if (!lastSeen) return false;
  const t = new Date(lastSeen).getTime();
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) / 1000 < withinSec;
}

export async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

// Form-encoded POST for the trust-model actions (confirm/reject/mute/auto/review).
export async function apost<T = unknown>(path: string, body: Record<string, string | number | boolean>): Promise<T> {
  const fd = new FormData();
  for (const [k, v] of Object.entries(body)) fd.append(k, String(v));
  const r = await fetch(`${API}${path}`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}
