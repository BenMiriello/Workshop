// One dictation at a time across this browser's Workshop windows and tabs.
//
// The window dictating says so on a BroadcastChannel every couple of seconds
// and on every change; every other window shows it on a bar of its own (where
// the words are going, Stop, Move here), so an old tab still listening is never
// a secret. Starting dictation in one window stops it in the others. The
// server ends an older live session of the same user too (other devices).
//
// Each tab has an id kept in its session storage, so a reload is the same tab:
// that is how "Keep dictation on" starts again only in the tab that had it.

import type { RecentPlace } from "../../../lib/speech/recentPlaces";
import type { Destination } from "./destination";

type Kind = Destination["kind"];
export type Elsewhere = { tab: string; name: string; kind: Kind; place: RecentPlace | null; words: number; at: number };
type Message =
  | ({ t: "on" } & Elsewhere)
  | { t: "off"; tab: string }
  | { t: "stop"; tab: string | null; from: string };

const BEAT_MS = 2000;
const GONE_MS = 5000;
const TAB_KEY = "speech.tab";

function tabId(): string {
  try {
    const was = sessionStorage.getItem(TAB_KEY);
    if (was) return was;
    const id = Math.random().toString(36).slice(2, 10);
    sessionStorage.setItem(TAB_KEY, id);
    return id;
  } catch {
    return Math.random().toString(36).slice(2, 10);
  }
}

export const TAB = tabId();
const channel: BroadcastChannel | null =
  typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("workshop.dictation") : null;

let other: Elsewhere | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((fn) => fn());
let stopHere: () => void = () => {};

function heard(m: Message): void {
  if (m.t === "on" && m.tab !== TAB) { other = m; changed(); }
  else if (m.t === "off" && other?.tab === m.tab) { other = null; changed(); }
  else if (m.t === "stop" && m.from !== TAB && (m.tab === null || m.tab === TAB)) stopHere();
}

channel?.addEventListener("message", (e: MessageEvent<Message>) => heard(e.data));
setInterval(() => {
  if (other && Date.now() - other.at > GONE_MS) { other = null; changed(); }
}, BEAT_MS);

export const elsewhere = {
  subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; },
  get: (): Elsewhere | null => other,
  /** What stops this tab's dictation when another window asks. */
  onStop(fn: () => void) { stopHere = fn; },
  /** This tab is starting to dictate: every other window stops. */
  claim(): void { channel?.postMessage({ t: "stop", tab: null, from: TAB } satisfies Message); },
  /** Ask the window dictating to stop. */
  stop(tab: string): void { channel?.postMessage({ t: "stop", tab, from: TAB } satisfies Message); },
  /** This tab is dictating into `d`; said again every beat while it is. */
  on(d: Destination, place: RecentPlace | null, words: number): void {
    channel?.postMessage({ t: "on", tab: TAB, name: d.name, kind: d.kind, place, words, at: Date.now() } satisfies Message);
  },
  off(): void { channel?.postMessage({ t: "off", tab: TAB } satisfies Message); },
};

export { BEAT_MS };
