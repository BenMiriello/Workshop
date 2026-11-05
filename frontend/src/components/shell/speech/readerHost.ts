// The shell's reader, offered to pages and framed apps through the kit's
// read-aloud host contract. A page sees what is being read and for whom, and
// nothing of the player's own controls. It can say where its text is on the
// page, to be highlighted and tapped into (readPlaces.ts).

import type { ReadAloudHost, ReadAloudPhase, ReadAloudState } from "@workshop/ui";
import { placeRead } from "./readPlaces";

type ReaderLike = {
  read(text: string, source: string, place?: Element | Range | null): void;
  stop(): void;
  getState(): { phase: ReadAloudPhase; source: string | null };
  subscribe(fn: () => void): () => void;
};

/** The host view of `r`. `getState` returns the same object until the phase
 *  or source changes, as `useSyncExternalStore` requires. */
export function readerHost(r: ReaderLike): ReadAloudHost {
  let view: ReadAloudState = { phase: "idle", source: null };
  return {
    read: (text, source, place) => r.read(text, source, place),
    place: placeRead,
    stop: () => r.stop(),
    subscribe: (fn) => r.subscribe(fn),
    getState: () => {
      const { phase, source } = r.getState();
      if (phase !== view.phase || source !== view.source) view = { phase, source };
      return view;
    },
  };
}
