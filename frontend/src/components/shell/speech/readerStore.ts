// The reader's state as an external store for `useSyncExternalStore`, and the
// reading speed, which is kept per user across reads and visits.

import { userStorage } from "../../../lib/storage";

export type ReaderPhase = "idle" | "loading" | "playing" | "paused" | "error";

export type ReaderState = {
  phase: ReaderPhase;
  /** Who asked: a Read aloud button's own id, or "selection". */
  source: string | null;
  speed: number;
  error: string | null;
  /** The sentence being read, from zero, and how many the read has. */
  index: number;
  count: number;
  /** The read has a place on the page (passage.ts), so it can be highlighted
   *  and followed. */
  placed: boolean;
};

/** Nothing being read: the part of the state a stop resets. */
export const IDLE: Pick<ReaderState, "phase" | "source" | "index" | "count" | "placed"> = {
  phase: "idle", source: null, index: 0, count: 0, placed: false,
};

export class Store<S> {
  private listeners = new Set<() => void>();

  constructor(protected state: S) {}

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  getState = () => this.state;

  protected set(patch: Partial<S>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }
}

export const SPEEDS = [0.75, 1, 1.25, 1.5, 1.75, 2];
const SPEED_KEY = "speech.read-speed";

export function storedSpeed(): number {
  const v = Number(userStorage.local.getItem(SPEED_KEY));
  return SPEEDS.includes(v) ? v : 1;
}

export function storeSpeed(speed: number): void {
  userStorage.local.setItem(SPEED_KEY, String(speed));
}
