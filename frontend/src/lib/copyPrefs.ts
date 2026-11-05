// What happens to a copy made in the Workshop. `save`: whether the Workshop
// tells the apps subscribed to copies about it (a clipboard manager keeps it).
// `destination`: whether it also goes onto the system clipboard, where every
// other app can paste it, or stays in the Workshop, where Workshop fields paste
// it and an app can still put a chosen item on the system clipboard
// (`SYSTEM_COPY_EVENT`). Kept on the server per user, so every device follows
// one choice (services/copy_prefs.py); the last answer is kept on the device
// too, so a copy made before the server has answered is already held.

import { useSyncExternalStore } from "react";
import type { components } from "../api/schema.gen";
import { jsonOrFail } from "./http";
import { userStorage } from "./storage";

export type CopyPrefs = components["schemas"]["CopyPrefs"];
export type CopyDestination = CopyPrefs["destination"];

export const DEFAULT_COPY_PREFS: CopyPrefs = { save: true, destination: "system" };

/** Dispatched on its own window by an app just before it writes the
 *  clipboard, to put that one write on the system clipboard even when copies
 *  stay in the Workshop (a clipboard manager's own Copy). */
export const SYSTEM_COPY_EVENT = "workshop:copy-to-system";

const KEY = "copies.prefs";
const URL = "/api/copies/prefs";

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

function keep(next: CopyPrefs): void {
  current = next;
  userStorage.local.setItem(KEY, JSON.stringify(next));
  listeners.forEach((fn) => fn());
}

export const copyPrefs = {
  get(): CopyPrefs {
    if (!current) current = parseCopyPrefs(userStorage.local.getItem(KEY));
    return current;
  },
  /** Read the server's answer. False when it could not be read. */
  async load(): Promise<boolean> {
    try {
      keep(await fetch(URL).then((r) => jsonOrFail<CopyPrefs>(r)));
      return true;
    } catch {
      return false;
    }
  },
  /** Change it for every device. Applies here at once; false (and put back)
   *  when the server did not take it. */
  async set(patch: Partial<CopyPrefs>): Promise<boolean> {
    const before = copyPrefs.get();
    keep({ ...before, ...patch });
    try {
      keep(await fetch(URL, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }).then((r) => jsonOrFail<CopyPrefs>(r)));
      return true;
    } catch {
      keep(before);
      return false;
    }
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
