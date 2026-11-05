// The system clipboard through the Mac and iPhone apps (shared/native-bridge.ts
// `clipboard`). A write here needs no user gesture, so it can land after the
// click that asked for it is gone. In a browser `available()` is false and the
// caller uses the page's own clipboard.
import type { ClipboardApi, ClipboardWatchMode, WorkshopNative } from "@shared/native-bridge";

export type { ClipboardWatchMode };

function shell(): WorkshopNative | null {
  if (typeof window === "undefined") return null;
  const n = (window as unknown as { WorkshopNative?: WorkshopNative }).WorkshopNative;
  try {
    return n?.has("clipboard") ? n : null;
  } catch {
    return null;
  }
}

function api(): ClipboardApi | null {
  return shell()?.clipboard ?? null;
}

/** The Mac and iPhone apps: they can save copies made in other apps. */
function watcher(): WorkshopNative | null {
  const n = shell();
  return n?.platform === "macos" || n?.platform === "ios" ? n : null;
}

export const nativeClipboard = {
  available(): boolean {
    return api() !== null;
  },

  /** True once `text` is on the system clipboard; false without the app or
   *  when the app could not write it. */
  async write(text: string): Promise<boolean> {
    const clip = api();
    if (!clip) return false;
    try {
      return (await clip.write(text)) === true;
    } catch {
      return false;
    }
  },

  /** Whether this device can choose to save copies made in other apps. */
  canWatch(): boolean {
    return watcher() !== null;
  },

  /** Null where there is no watch to read. */
  async getWatch(): Promise<ClipboardWatchMode | null> {
    const n = watcher();
    if (!n) return null;
    try {
      return await n.clipboard.watch.get();
    } catch {
      return null;
    }
  },

  /** Resolves to the mode now in force; throws when the app refused it. */
  async setWatch(mode: ClipboardWatchMode): Promise<ClipboardWatchMode> {
    const n = watcher();
    if (!n) throw new Error("This device cannot save copies from other apps.");
    return n.clipboard.watch.set(mode);
  },
};
