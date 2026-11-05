// Which apps can open over the page right now, and which one is open.
//
// One store the shell's host, its menu and Settings all read, so the list is
// fetched in one place and "open this one" is a call any of them can make.

import { useSyncExternalStore } from "react";
import type { components } from "../../api/schema.gen";

export type OverlayApp = components["schemas"]["OverlayApp"];
type OverlayList = components["schemas"]["OverlayList"];

type State = { apps: OverlayApp[]; open: string | null };

let state: State = { apps: [], open: null };
const listeners = new Set<() => void>();

function set(next: Partial<State>): void {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function sameApps(a: OverlayApp[], b: OverlayApp[]): boolean {
  return a.length === b.length && a.every((x, i) => JSON.stringify(x) === JSON.stringify(b[i]));
}

export const overlayApps = {
  get: () => state,
  subscribe(l: () => void) {
    listeners.add(l);
    return () => { listeners.delete(l); };
  },
  /** Read the running overlay apps again. A failed read keeps the last list. */
  async refresh(): Promise<void> {
    await overlayApps.load();
  },
  /** `refresh`, resolving to whether the read succeeded. */
  async load(): Promise<boolean> {
    try {
      const r = await fetch("/api/apps/overlays");
      if (!r.ok) return false;
      const body = await r.json() as OverlayList;
      const apps = body.overlays ?? [];
      if (!sameApps(apps, state.apps)) set({ apps });
      return true;
    } catch {
      return false;
    }
  },
  open(slug: string) {
    if (state.apps.some((a) => a.slug === slug)) set({ open: slug });
  },
  toggle(slug: string) {
    if (state.open === slug) set({ open: null });
    else overlayApps.open(slug);
  },
  close() {
    if (state.open !== null) set({ open: null });
  },
};

export function useOverlayApps(): State {
  return useSyncExternalStore(overlayApps.subscribe, overlayApps.get, overlayApps.get);
}
