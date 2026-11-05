// What happens to a copy made in the Workshop, on this device. `save`: whether
// the Workshop tells the apps subscribed to copies about it (a clipboard
// manager keeps it). `destination`: whether it also goes onto the system
// clipboard, where every other app can paste it, or stays in the Workshop,
// where Workshop fields paste it and an app can still put a chosen item on
// the system clipboard (`SYSTEM_COPY_EVENT`). Kept per device, like the Mac
// app's choice to save copies made in other apps.

import { useSyncExternalStore } from "react";
import { userStorage } from "./storage";

export type CopyDestination = "system" | "workshop";

export type CopyPrefs = { save: boolean; destination: CopyDestination };

export const DEFAULT_COPY_PREFS: CopyPrefs = { save: true, destination: "system" };

/** Dispatched on its own window by an app just before it writes the
 *  clipboard, to put that one write on the system clipboard even when copies
 *  stay in the Workshop (a clipboard manager's own Copy). */
export const SYSTEM_COPY_EVENT = "workshop:copy-to-system";

const KEY = "copies.prefs";

export function parseCopyPrefs(raw: string | null): CopyPrefs {
  let v: Record<string, unknown> = {};
  try {
    const parsed = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === "object") v = parsed as Record<string, unknown>;
  } catch {
    v = {};
  }
  return {
    save: typeof v.save === "boolean" ? v.save : DEFAULT_COPY_PREFS.save,
    destination: v.destination === "workshop" || v.destination === "system"
      ? v.destination : DEFAULT_COPY_PREFS.destination,
  };
}

const listeners = new Set<() => void>();
let current: CopyPrefs | null = null;

export const copyPrefs = {
  get(): CopyPrefs {
    if (!current) current = parseCopyPrefs(userStorage.local.getItem(KEY));
    return current;
  },
  set(patch: Partial<CopyPrefs>): void {
    current = { ...copyPrefs.get(), ...patch };
    userStorage.local.setItem(KEY, JSON.stringify(current));
    listeners.forEach((fn) => fn());
  },
  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
  /** Copies made in the Workshop stay off the system clipboard. */
  held(): boolean {
    return copyPrefs.get().destination === "workshop";
  },
};

export function useCopyPrefs(): CopyPrefs {
  return useSyncExternalStore(copyPrefs.subscribe, copyPrefs.get, copyPrefs.get);
}
